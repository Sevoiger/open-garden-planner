"""A script that imports the app keeps its hands off the user's own stores.

Phase 17 L1.1 (#385), section 8.10 "Test isolation". ``tests/conftest.py``
isolates the PYTEST process. A script under ``scripts/`` runs outside it, where
the app's settings are the user's own (on Windows a registry key that no
environment variable moves) and so are its app-data folder and every
``QStandardPaths`` location; ``ProjectManager.save()`` and ``.load()`` end with
``add_recent_file``. Scripts run that way replaced a developer's whole Recent
Files list with fixture paths (found 2026-10-11).

``scripts/script_isolation.py`` is what a script calls to prevent that. Here each
script that uses it is RUN, as a script, in a child process — and the child is
watched: every settings store it builds, where its profile variables point when
it ends, and whether anything appeared in the profile it was started with.

The child is never given the real names: a decoy pair stands where the
production names would be, so a script that failed to isolate itself would write
there (and this test would say so) — not into anybody's settings.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
import script_isolation  # noqa: E402  (scripts/ is not a package)

sys.path.remove(str(SCRIPTS))

#: Runs one script as ``__main__`` and reports what it did to the stores.
DRIVER = r'''
import json, os, runpy, sys

script, variables, *arguments = sys.argv[1:]
import open_garden_planner.app.settings as app_settings

decoy = ["cofade_test", f"Open Garden Planner Decoy {os.getpid()}"]
app_settings.ORGANIZATION_NAME, app_settings.APPLICATION_NAME = decoy
built = []
make = app_settings.create_qsettings


def watched():
    built.append([app_settings.ORGANIZATION_NAME, app_settings.APPLICATION_NAME])
    return make()


app_settings.create_qsettings = watched
sys.argv = [script, *arguments]
try:
    runpy.run_path(script, run_name="__main__")
    code = 0
except SystemExit as stop:
    code = stop.code if isinstance(stop.code, int) else (0 if stop.code is None else 1)
names = [app_settings.ORGANIZATION_NAME, app_settings.APPLICATION_NAME]
app_settings.ORGANIZATION_NAME, app_settings.APPLICATION_NAME = decoy
store = make()  # whatever a script that did NOT isolate itself wrote to the decoy: gone again
store.clear()
store.sync()
app_settings.ORGANIZATION_NAME, app_settings.APPLICATION_NAME = names
print("RESULT " + json.dumps({
    "exit": code, "decoy": decoy, "built": built, "names": names,
    "environment": {name: os.environ.get(name) for name in variables.split(",")},
}))
'''


def _under(path: str | None, root: Path) -> bool:
    return path is not None and Path(os.path.normcase(os.path.abspath(path))).is_relative_to(
        os.path.normcase(os.path.abspath(root)))


def _run_as_a_script(script: str, arguments: list[str], home: Path) -> dict:
    """Run ``scripts/<script>`` as ``__main__`` in a child whose whole profile is ``home``."""
    environment = dict(os.environ)
    for name in script_isolation.PROFILE_VARIABLES:
        folder = home / name.lower()
        folder.mkdir(parents=True)
        environment[name] = str(folder)
    environment["PYTHONPATH"] = str(REPO / "src")
    environment["QT_QPA_PLATFORM"] = "offscreen"
    done = subprocess.run(
        [sys.executable, "-c", DRIVER, str(SCRIPTS / script),
         ",".join(script_isolation.PROFILE_VARIABLES), *arguments],
        capture_output=True, text=True, env=environment, cwd=str(REPO), timeout=170, check=False)
    lines = [line for line in done.stdout.splitlines() if line.startswith("RESULT ")]
    assert lines, f"{script} did not finish:\n{done.stdout[-1500:]}\n{done.stderr[-1500:]}"
    result: dict = json.loads(lines[-1][len("RESULT "):])
    result["output"] = done.stdout
    return result


def _left_in(home: Path) -> list[str]:
    """Everything under ``home`` beyond the empty folders the child was started with."""
    started_with = {home / name.lower() for name in script_isolation.PROFILE_VARIABLES}
    return sorted(str(path.relative_to(home)) for path in home.rglob("*") if path not in started_with)


@pytest.mark.parametrize(
    ("script", "arguments", "builds_a_store"),
    [("make_scene3d_edge_plan.py", ["--check"], True),  # saves a plan: Recent Files is written
     ("measure_region_work.py", ["--help"], False)],
)
def test_a_script_run_as_a_script_reaches_none_of_the_users_stores(
    tmp_path: Path, script: str, arguments: list[str], builds_a_store: bool
) -> None:
    home = tmp_path / "home"
    result = _run_as_a_script(script, arguments, home)
    assert result["exit"] == 0, result["output"][-1500:]

    # the settings: every store the script built is a throwaway key of that process …
    organisation, application = result["names"]
    assert organisation == script_isolation.SCRIPT_ORGANIZATION
    assert application.startswith("Open Garden Planner Script ") and result["names"] != result["decoy"]
    assert all(pair == result["names"] for pair in result["built"]), result["built"]
    assert bool(result["built"]) is builds_a_store  # … and the generator did build one: it is the case that matters

    # the profile: every variable points away from the one the child was started with,
    # and nothing appeared in that one — not a file, not a folder
    for name in script_isolation.PROFILE_VARIABLES:
        assert not _under(result["environment"][name], home), name
    assert _left_in(home) == []


def test_the_generator_says_what_it_checked(tmp_path: Path) -> None:
    """The run above is a real run: the committed edge-case plan is current."""
    result = _run_as_a_script("make_scene3d_edge_plan.py", ["--check"], tmp_path / "home")
    assert "scene3d edge-case plan: OK" in result["output"]


def test_isolating_twice_is_isolating_once(tmp_path: Path) -> None:
    """Both calls are safe to repeat — a script calls ``isolate_profile()`` before
    its imports and ``isolate()`` in ``main()``. Shown in a child: this process's
    own redirection (``tests/conftest.py``) must not be moved."""
    program = (
        "import os, sys, json; sys.path.insert(0, sys.argv[1])\n"
        "import script_isolation as s\n"
        "first = s.isolate_profile(); again = s.isolate_profile(); s.isolate(); found = s.isolate_settings()\n"
        "import open_garden_planner.app.settings as a\n"
        "print('RESULT ' + json.dumps({'same': str(first) == str(again), 'root': str(first),\n"
        "    'exists': first.is_dir(), 'found': list(found), 'bound': [a.ORGANIZATION_NAME, a.APPLICATION_NAME],\n"
        "    'environment': {n: os.environ[n] for n in s.PROFILE_VARIABLES}}))\n"
    )
    environment = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    done = subprocess.run([sys.executable, "-c", program, str(SCRIPTS)], capture_output=True, text=True,
                          env=environment, cwd=str(tmp_path), timeout=120, check=False)
    (line,) = [line for line in done.stdout.splitlines() if line.startswith("RESULT ")]
    result = json.loads(line[len("RESULT "):])
    assert result["same"] and result["exists"]
    assert all(_under(value, Path(result["root"])) for value in result["environment"].values())
    assert result["bound"][0] == "cofade_test" and result["bound"][1].startswith("Open Garden Planner Script ")
    assert result["found"] != result["bound"]  # what it found is what a caller could put back
    assert not Path(result["root"]).exists()  # … and the folder is gone when the process is


SELF_ISOLATING = ("make_scene3d_edge_plan.py", "measure_region_work.py")
#: Scripts of the 3D pipeline that import nothing of the app: every child they start is pytest.
APP_FREE = ("mutation_check_scene3d.py", "script_isolation.py")


def _imports(tree: ast.Module, *, top_level_only: bool) -> set[str]:
    nodes = tree.body if top_level_only else list(ast.walk(tree))
    names: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("script", SELF_ISOLATING)
def test_the_isolation_comes_before_the_app_is_imported(script: str) -> None:
    """Read off the script's own text: the guarded ``isolate_profile()`` stands
    above the first import of the app (or of the generator that imports it), and
    ``isolate()`` is the first statement of ``main()``."""
    tree = ast.parse((SCRIPTS / script).read_text(encoding="utf-8"))
    guard = first_app_import = None
    for position, node in enumerate(tree.body):
        if (guard is None and isinstance(node, ast.If)
                and ast.unparse(node.test) == "__name__ == '__main__'"
                and "script_isolation.isolate_profile()" in ast.unparse(node)):
            guard = position
        imported = _imports(ast.Module(body=[node], type_ignores=[]), top_level_only=True)
        if first_app_import is None and imported & {"open_garden_planner", "make_bench_plans", "PyQt6"}:
            first_app_import = position
    assert guard is not None and first_app_import is not None and guard < first_app_import
    (main,) = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"]
    assert ast.unparse(main.body[0]) == "script_isolation.isolate()"


@pytest.mark.parametrize("script", APP_FREE)
def test_these_import_nothing_of_the_app_at_the_top(script: str) -> None:
    tree = ast.parse((SCRIPTS / script).read_text(encoding="utf-8"))
    assert not _imports(tree, top_level_only=True) & {"open_garden_planner", "PyQt6"}
    if script == "mutation_check_scene3d.py":  # nothing of the app anywhere: its children are pytest
        assert not _imports(tree, top_level_only=False) & {"open_garden_planner", "PyQt6"}
        assert '"-m", "pytest"' in (SCRIPTS / script).read_text(encoding="utf-8")
