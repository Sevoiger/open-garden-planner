"""Issue #277 hardening in ``main.py``: the frozen ``--selftest`` and the
uncaught-exception backstop that together stop a lazily-imported subsystem
failure from silently killing the process and discarding unsaved work.
"""

from __future__ import annotations

import sys

import pytest

from open_garden_planner.main import _install_excepthook, _run_selftest, _selftest_quick3d


class TestExceptHook:
    def test_installs_and_shows_dialog_without_reraising(
        self, qtbot, monkeypatch  # noqa: ARG002
    ) -> None:
        from PyQt6.QtWidgets import QMessageBox

        calls: list[tuple] = []
        monkeypatch.setattr(QMessageBox, "critical", lambda *a, **_k: calls.append(a))

        original = sys.excepthook
        try:
            _install_excepthook()
            assert sys.excepthook is not original
            # Simulate PyQt handing an uncaught slot exception to the hook.
            try:
                raise RuntimeError("boom")
            except RuntimeError:
                sys.excepthook(*sys.exc_info())
        finally:
            sys.excepthook = original

        assert calls, "excepthook should have shown a recoverable dialog"


def _stub_3d(monkeypatch, *, ok: bool = True, raises: bool = False) -> None:
    """Replace the engine package's self-test: it RENDERS, which this
    ``QT_QPA_PLATFORM=offscreen`` process cannot do (software scene graph — no 3D)."""
    from pathlib import Path

    from open_garden_planner.ui.view3d.quick3d import selftest

    def run_selftest() -> selftest.SelftestResult:
        if raises:
            raise RuntimeError("the engine package blew up")
        line = "  OK   Qt Quick 3D frame (stand-in)" if ok else "  FAIL Qt Quick 3D frame: stand-in"
        return selftest.SelftestResult(ok, (line,), Path("stand-in.log"))

    monkeypatch.setattr(selftest, "run_selftest", run_selftest)


@pytest.fixture()
def keep_the_qt_identity():
    """The self-test names the application when none exists yet (so its log lands in
    the app's log directory). Those names are process-global Qt statics: put them
    back, or every later test resolves ``QStandardPaths`` under them."""
    from PyQt6.QtCore import QCoreApplication

    saved = (QCoreApplication.organizationName(), QCoreApplication.applicationName())
    yield
    QCoreApplication.setOrganizationName(saved[0])
    QCoreApplication.setApplicationName(saved[1])


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="importing the Qt3D bindings needs the bundled Qt runtime "
    "(the Windows dev/CI-release environment)",
)
@pytest.mark.usefixtures("keep_the_qt_identity")
class TestSelfTest:
    """The Qt3D and Agent API blocks run for real here; the Qt Quick 3D block (L1.2)
    is a stand-in. The real 3D block runs in a subprocess with a real window system
    (``tests/integration/test_quick3d_selftest.py``, render tier) and in the frozen
    exe before every release."""

    def test_passes_on_a_consistent_qt_stack(self, monkeypatch) -> None:
        _stub_3d(monkeypatch)
        assert _run_selftest() == 0

    def test_returns_nonzero_on_version_mismatch(self, monkeypatch) -> None:
        """The load-bearing #277 detector: a Qt runtime that differs from the
        Qt3D wheel micro must fail (exit 1), even when the imports succeed."""
        import importlib.metadata as md

        _stub_3d(monkeypatch)
        real = md.version
        monkeypatch.setattr(
            md,
            "version",
            lambda name: "0.0.0-mismatch" if name == "PyQt6-3D-Qt6" else real(name),
        )
        assert _run_selftest() == 1

    def test_a_failed_3d_frame_fails_the_whole_selftest(self, monkeypatch, capsys) -> None:
        """Everything else healthy, the 3D engine not: exit 1."""
        _stub_3d(monkeypatch, ok=False)
        assert _run_selftest() == 1
        assert "SELFTEST: FAIL" in capsys.readouterr().out


@pytest.mark.usefixtures("keep_the_qt_identity")
class TestSelfTestQuick3d:
    """``main._selftest_quick3d`` — the wiring of the 3D block, on every platform."""

    def test_a_pass_prints_the_lines_and_the_log_path(self, monkeypatch, capsys) -> None:
        _stub_3d(monkeypatch)
        assert _selftest_quick3d() is True
        out = capsys.readouterr().out
        assert "  OK   Qt Quick 3D frame (stand-in)" in out
        assert "3D self-test log: stand-in.log" in out

    def test_a_failed_frame_is_a_failure_and_says_why(self, monkeypatch, capsys) -> None:
        _stub_3d(monkeypatch, ok=False)
        assert _selftest_quick3d() is False
        out = capsys.readouterr().out
        assert "FAIL Qt Quick 3D frame: stand-in" in out and "3D self-test log: stand-in.log" in out

    def test_a_crashing_3d_selftest_fails_instead_of_escaping(self, monkeypatch, capsys) -> None:
        _stub_3d(monkeypatch, raises=True)
        assert _selftest_quick3d() is False
        assert "FAIL Qt Quick 3D selftest" in capsys.readouterr().out

    def test_an_existing_application_keeps_its_identity(self, qapp, monkeypatch) -> None:  # noqa: ARG002
        """The log directory hangs on Qt's application identity, so the self-test names
        the application — but only when it is the one that will create it. An
        application that already exists (this test process's) keeps its own names.
        The other branch needs a process WITHOUT an application: it is pinned end to
        end, in a child, by ``test_quick3d_selftest.py::
        test_the_selftest_writes_its_log_beside_the_apps_logs_not_into_temp``."""
        from PyQt6.QtCore import QCoreApplication

        before = (QCoreApplication.organizationName(), QCoreApplication.applicationName())
        _stub_3d(monkeypatch)
        assert _selftest_quick3d() is True
        assert (QCoreApplication.organizationName(), QCoreApplication.applicationName()) == before

    def test_main_names_no_engine_module(self) -> None:
        """The import rule: ``main.py`` names no Qt Quick module — the engine imports of
        the self-test live in ``ui/view3d/quick3d/selftest.py``."""
        import ast
        from pathlib import Path

        import open_garden_planner.main as ogp_main

        source = Path(ogp_main.__file__).read_text(encoding="utf-8")
        strings = {node.value for node in ast.walk(ast.parse(source))
                   if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        assert not {s for s in strings if s.startswith(("PyQt6.QtQuick", "PyQt6.QtQml"))}
        assert "open_garden_planner.ui.view3d.quick3d.selftest" in source


class TestMainUtilities:
    def test_get_icon_path(self) -> None:
        from open_garden_planner.main import get_icon_path

        p = get_icon_path()
        assert p.name == "OGP_logo.png"
        assert p.exists()

    def test_write_crash_log(self, tmp_path, monkeypatch) -> None:
        from PyQt6.QtCore import QStandardPaths

        from open_garden_planner.main import _write_crash_log

        monkeypatch.setattr(QStandardPaths, "writableLocation", lambda _loc: str(tmp_path))
        _write_crash_log("test crash entry")
        crash_log = tmp_path / "crash.log"
        assert crash_log.exists()
        assert "test crash entry" in crash_log.read_text(encoding="utf-8")

    def test_main_selftest_dispatch(self, monkeypatch) -> None:
        import open_garden_planner.main as ogp_main

        monkeypatch.setattr(ogp_main, "_run_selftest", lambda: 42)
        monkeypatch.setattr(sys, "argv", ["ogp", "--selftest"])
        assert ogp_main.main() == 42
