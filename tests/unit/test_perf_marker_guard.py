"""Every wall-clock assertion in the suite must sit on the quiet runner (#509).

`addopts` carries `-m 'not perf'`, so the `perf` marker is what decides whether a
test runs in the default parallel run or on a quiet runner of its own. That makes
the marker a **correctness contract for the gate**, not a label: an unmarked
wall-clock assertion runs beside up to `-n auto` workers, and two such tests were
found failing intermittently that way during this change's own senior review — one
of which (a map-picker close bound under 0.5 s) the first audit had walked straight
past, while two more sat in classes literally named `TestPerf`.

So the invariant is pinned here as a test rather than in prose: **no test may assert
against a measured duration without the marker.** Prose decays — §8.10.4 already
claimed the audit was complete when it was not — so the check has to be mechanical.

Two deliberate design choices, both learned by being wrong first:

- **An ordering operator is required.** A budget is an inequality
  (`elapsed < 0.5`); `assert manager._timer.interval() == expected_interval_ms` is a
  *configured* value compared for equality and is not a measurement. Without this
  distinction the first run flagged `test_timer_interval_from_settings`.
- **One traversal, one naming scheme.** A first version walked the tree twice
  (candidates, then marked) and the two passes spelled node ids differently, so every
  class-level marker failed to match its own tests and the guard reported two correctly
  marked classes as offenders.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]

# Names a measured duration typically arrives as.
DURATION_NAMES = frozenset({
    "elapsed", "avg_ms", "build_median_ms", "query_median_ms", "best_median",
    "duration_ms", "took_ms", "wall_ms", "roundtrip_ms",
})
DURATION_SUFFIXES = ("_ms", "_seconds", "_secs")

ORDERING_OPS = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def _names_in(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
    return names


def _is_duration(name: str) -> bool:
    return name in DURATION_NAMES or name.endswith(DURATION_SUFFIXES)


def _has_marker(node: ast.AST) -> bool:
    for decorator in getattr(node, "decorator_list", []):
        text = ast.dump(decorator)
        if "perf" in text and "mark" in text:
            return True
    return False


def _asserts_duration(func: ast.AST) -> bool:
    """True when the body compares a measured duration against a bound."""
    for stmt in ast.walk(func):
        if not isinstance(stmt, ast.Assert) or not isinstance(stmt.test, ast.Compare):
            continue
        if not any(isinstance(op, ORDERING_OPS) for op in stmt.test.ops):
            continue
        if any(_is_duration(name) for name in _names_in(stmt)):
            return True
    return False


def collect_duration_asserts(tree: ast.Module) -> list[tuple[str, bool]]:
    """`(node_id, marked)` for every test that compares a duration against a bound.

    A single traversal, so the id a test is recorded under is the id it is looked up
    by. The marker is inherited: a marked class marks its test methods.
    """
    found: list[tuple[str, bool]] = []

    def visit(node: ast.AST, marked: bool, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, marked or _has_marker(child), f"{prefix}{child.name}::")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                marked_here = marked or _has_marker(child)
                if child.name.startswith("test_") and _asserts_duration(child):
                    found.append((f"{prefix}{child.name}", marked_here))
                visit(child, marked_here, prefix)
            else:
                visit(child, marked, prefix)

    visit(tree, False, "")
    return sorted(found)


def _python_files() -> list[Path]:
    return [p for p in TESTS_ROOT.rglob("test_*.py") if "__pycache__" not in p.parts]


def test_no_wall_clock_assertion_is_unmarked() -> None:
    """The guard: any duration assertion without `perf` can flake under workers."""
    offenders: list[str] = []
    scanned = 0
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node_id, marked in collect_duration_asserts(tree):
            scanned += 1
            if not marked:
                rel = path.relative_to(TESTS_ROOT.parent).as_posix()
                offenders.append(f"{rel}::{node_id}")
    assert scanned >= 7, (
        f"the guard only matched {scanned} duration assertion(s); it has probably "
        "stopped recognising how the suite spells them"
    )
    assert not offenders, (
        "wall-clock assertions without @pytest.mark.perf — they run beside up to "
        "`-n auto` workers in the default run and fail intermittently there:\n  "
        + "\n  ".join(offenders)
        + "\nAdd the marker, or rewrite the assertion to be robust (§11.4 #354)."
    )


def test_the_guard_detects_an_unmarked_assertion() -> None:
    """A guard nobody checked is a guard nobody can trust.

    Deliberately NOT marked `perf`: it asserts no duration, it IS the check that
    durations get marked. Marking it would deselect the gate from the default run.
    """

    def ids(source: str) -> list[tuple[str, bool]]:
        return collect_duration_asserts(ast.parse(source))

    # An unmarked method-level budget is reported unmarked.
    assert ids("def test_x():\n    elapsed = 1.0\n    assert elapsed < 0.5\n") == [
        ("test_x", False)
    ]
    # The same budget marked at method level.
    assert ids(
        "import pytest\n@pytest.mark.perf\ndef test_x():\n"
        "    elapsed = 1.0\n    assert elapsed < 0.5\n"
    ) == [("test_x", True)]
    # The same budget marked at CLASS level, reported under the qualified id.
    assert ids(
        "import pytest\n@pytest.mark.perf\nclass TestPerf:\n"
        "    def test_x(self):\n        elapsed = 1.0\n        assert elapsed < 0.5\n"
    ) == [("TestPerf::test_x", True)]
    # A configured interval compared for EQUALITY is not a measurement.
    assert ids(
        "def test_y():\n    expected_ms = 500\n    assert timer == expected_ms\n"
    ) == []
    # And a non-temporal assertion must not be swept up by the suffix heuristics.
    assert ids("def test_z():\n    assert species_key == 'x'\n") == []
