"""The engine boundary, pinned (Phase 17 L1.2, #385 — gates G1 and G2; ADR-048).

One package talks to Qt Quick 3D: ``ui/view3d/quick3d/``. Everything else in the
product — the Qt-free scene core, the snapshot, the workspace, ``main.py`` — is
free of ``PyQt6.QtQuick3D`` / ``QtQuick`` / ``QtQml`` / ``QtQuickWidgets``, so
the engine can be replaced (or fail to load) without taking the 2D app with it,
and so a normal start never pays for it.

The scan is an AST scan over the WHOLE source tree, and it also reads module
names out of string literals: ``__import__("PyQt6.QtQuick3D")`` is an import.
The dormant L0 spike keeps its own, older rule (``test_spike_q3d_isolation.py``)
until it retires in L1.10.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
PRODUCT = SRC / "open_garden_planner"
PACKAGE = PRODUCT / "ui" / "view3d" / "quick3d"
ENGINE_MODULES = ("PyQt6.QtQuick3D", "PyQt6.QtQuick", "PyQt6.QtQml", "PyQt6.QtQuickWidgets")

#: The only files outside the engine package that may touch the engine: the L0 spike's
#: one engine module (dormant, ``--spike-q3d`` only; deleted with the spike in L1.10).
ALLOWED_OUTSIDE = {PRODUCT / "spike_q3d" / "quick.py"}
#: Inside the package: the modules that IMPORT the engine …
ENGINE_IMPORTERS = {"geometry.py", "host.py"}
#: … and the one that names its bindings as strings, to import them one by one and
#: report each (the self-test).
ENGINE_NAMERS = {"selftest.py"}


def _is_engine(name: str) -> bool:
    return any(name == module or name.startswith(module + ".") for module in ENGINE_MODULES)


def engine_imports(path: Path) -> set[str]:
    """Engine modules a file imports — by statement, in either spelling."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names if _is_engine(alias.name))
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if _is_engine(node.module):
                found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names
                         if _is_engine(f"{node.module}.{alias.name}"))
    return found


def engine_names(path: Path) -> set[str]:
    """Engine modules a file names in a string literal (``__import__``, ``importlib``)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and _is_engine(node.value)}


def _sources() -> list[Path]:
    return sorted(PRODUCT.rglob("*.py"))


def test_the_scan_covers_the_whole_tree() -> None:
    files = _sources()
    assert len(files) > 250, "the scan must walk the whole product, sub-packages included"
    assert PRODUCT / "main.py" in files and PACKAGE / "host.py" in files
    assert PRODUCT / "core" / "scene3d" / "interleave.py" in files


def test_only_the_engine_package_imports_the_engine() -> None:
    """G1. Any other module that imports (or names) a Qt Quick module is an offender."""
    offenders = sorted(
        str(path.relative_to(PRODUCT)) for path in _sources()
        if (engine_imports(path) or engine_names(path))
        and PACKAGE not in path.parents and path not in ALLOWED_OUTSIDE)
    assert offenders == []


def test_inside_the_package_only_the_engine_modules_import_it() -> None:
    """The package's own split: ``geometry`` and ``host`` are the engine; ``adapter``,
    ``materials``, ``look``, ``shots``, ``qt_messages``, ``runlog`` hold every decision
    that is not a Qt call — which is what lets the GPU-less run cover them."""
    importers = {path.name for path in PACKAGE.glob("*.py") if engine_imports(path)}
    namers = {path.name for path in PACKAGE.glob("*.py") if engine_names(path)}
    assert importers == ENGINE_IMPORTERS
    assert namers == ENGINE_NAMERS
    assert {path.name for path in PACKAGE.glob("*.py")} >= {
        "__init__.py", "adapter.py", "geometry.py", "host.py", "look.py", "materials.py",
        "offscreen.py", "qt_messages.py", "runlog.py", "selftest.py", "shots.py"}


def test_the_product_never_uses_a_qquickwidget() -> None:
    """ADR-048 criterion 3: a ``QQuickWidget`` moves the whole top-level window onto RHI
    composition (2D pan x1.4–x5.7). The engine package hosts a ``QQuickView``; it does
    not even import the widgets binding."""
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        used |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert "QQuickWidget" not in used, path.name  # docstrings may say why not
        assert not any("QtQuickWidgets" in name
                       for name in engine_imports(path) | engine_names(path)), path.name


@pytest.mark.parametrize(("source", "expected"), [
    ("from PyQt6 import QtQuick3D\n", {"PyQt6.QtQuick3D"}),
    ("import PyQt6.QtQml\n", {"PyQt6.QtQml"}),
    ("from PyQt6.QtQuick import QQuickView\n", {"PyQt6.QtQuick", "PyQt6.QtQuick.QQuickView"}),
    ("def f():\n    from PyQt6.QtQuickWidgets import QQuickWidget\n",
     {"PyQt6.QtQuickWidgets", "PyQt6.QtQuickWidgets.QQuickWidget"}),
    ("from PyQt6.QtCore import QObject\nfrom PyQt6 import QtGui, QtWidgets\n", set()),
    ("import PyQt6.QtQuickControls2\n", set()),  # not one of the four: a prefix is not a match
])
def test_the_scan_sees_every_import_spelling(tmp_path: Path, source: str, expected: set[str]) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(source, encoding="utf-8")
    assert engine_imports(probe) == expected


def test_the_scan_sees_imports_by_name(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text('import importlib\nmodule = importlib.import_module("PyQt6.QtQuick3D")\n'
                     '__import__("PyQt6.QtQml")\nlabel = "PyQt6.QtCore"\n', encoding="utf-8")
    assert engine_imports(probe) == set()
    assert engine_names(probe) == {"PyQt6.QtQuick3D", "PyQt6.QtQml"}


def _child(code: str) -> subprocess.CompletedProcess[str]:
    """A fresh interpreter importing THIS checkout's src: ``sys.modules`` of the shared
    test process proves nothing (other tests import the engine)."""
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(SRC), env.get("PYTHONPATH")) if p)
    return subprocess.run(  # noqa: S603 - fixed argv, our own code
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=170)


def test_app_start_loads_neither_the_engine_nor_its_package() -> None:
    """G2. Constructing the real main window imports no Qt Quick binding and nothing of
    ``ui/view3d/`` — the 3D engine costs a normal start nothing (measured on the dev
    box: the package 1 ms, its engine-free half 17 ms, the engine itself 17 ms, all
    of it paid when 3D is first opened)."""
    proc = _child(
        "import sys\n"
        "from PyQt6.QtWidgets import QApplication\n"
        "app = QApplication([])\n"
        "from open_garden_planner.app.application import GardenPlannerApp\n"
        "window = GardenPlannerApp()\n"
        "print(sorted(m for m in sys.modules if m.startswith(("
        "'PyQt6.QtQuick', 'PyQt6.QtQml', 'open_garden_planner.ui.view3d.quick3d'))))\n")
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip().splitlines()[-1] == "[]"


def test_importing_the_package_is_free() -> None:
    """Naming the package loads the package and nothing else — no Qt, no numpy core,
    no engine: ``__init__`` is a docstring."""
    proc = _child(
        "import sys\n"
        "import open_garden_planner.ui.view3d\n"
        "before = set(sys.modules)\n"
        "import open_garden_planner.ui.view3d.quick3d\n"
        "print(sorted(set(sys.modules) - before))\n")
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip().splitlines()[-1] == "['open_garden_planner.ui.view3d.quick3d']"
    tree = ast.parse((PACKAGE / "__init__.py").read_text(encoding="utf-8"))
    assert [type(node).__name__ for node in tree.body] == ["Expr"], "__init__ is a docstring only"


def test_the_engine_free_modules_load_without_the_engine() -> None:
    """Everything the GPU-less test tier imports — the sink, the look, the shots, the
    self-test's scene and judge — works in a process that never loads Qt Quick."""
    proc = _child(
        "import sys\n"
        "from open_garden_planner.ui.view3d.quick3d import (adapter, look, materials, qt_messages,\n"
        "                                                    runlog, selftest, shots)\n"
        "assert selftest.judge_frame is not None and adapter.Quick3DSink is not None\n"
        "print(sorted(m for m in sys.modules if m.startswith(('PyQt6.QtQuick', 'PyQt6.QtQml'))))\n")
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip().splitlines()[-1] == "[]"
