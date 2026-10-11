"""The engine package in the frozen exe and in CI, pinned as text (Phase 17 L1.2, #385).

Neither the PyInstaller build nor GitHub's runners run in the test suite, so the
things that silently drop the 3D engine from a release are pinned where they
are written down:

- ``installer/ogp.spec`` ships the QML and shader files and names the lazily
  imported package (a missing data file only fails when a Model uses the shader);
- ``release.yml`` still runs ``--selftest`` the one way that tests the shipping
  condition, and gives the 3D log a path it can print;
- ``ci.yml`` has the ``render3d`` job on both platforms, running files that
  exist and are opt-in, and the job is not a required check.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "src" / "open_garden_planner" / "ui" / "view3d" / "quick3d"
SPEC = (REPO / "installer" / "ogp.spec").read_text(encoding="utf-8")
CI = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
RELEASE = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
RENDER_TESTS = ("tests/integration/test_quick3d_render.py",
                "tests/integration/test_quick3d_engine.py",
                "tests/integration/test_quick3d_selftest.py")


def _code(text: str) -> str:
    """A file without its ``#`` comment lines (a rule named only in a comment is not a rule)."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


class TestSpec:
    def test_the_qml_and_shader_files_are_bundled(self) -> None:
        loop = re.search(
            r'for f in \(SRC / "open_garden_planner" / "ui" / "view3d" / "quick3d" / "qml"\)'
            r'\.rglob\("\*"\):\n\s+if f\.is_file\(\):\n\s+rel = f\.relative_to\(SRC\)\n'
            r"\s+datas\.append\(\(str\(f\), str\(rel\.parent\)\)\)", _code(SPEC))
        assert loop is not None, "ogp.spec no longer bundles ui/view3d/quick3d/qml/*"
        assert sorted(p.name for p in (PACKAGE / "qml").iterdir()) == [
            "Garden.qml", "foliage.frag", "foliage.vert", "grass.vert", "water.frag"]

    def test_every_module_of_the_package_is_a_hidden_import(self) -> None:
        """The package is imported lazily; the spec names each module, as it does for
        every other lazily loaded part of the app."""
        code = _code(SPEC)
        assert '"open_garden_planner.ui.view3d.quick3d",' in code
        for module in sorted(PACKAGE.glob("*.py")):
            if module.name != "__init__.py":
                name = f'"open_garden_planner.ui.view3d.quick3d.{module.stem}",'
                assert name in code, f"{module.name} is missing from ogp.spec's hiddenimports"
        for binding in ("PyQt6.QtQuick", "PyQt6.QtQml", "PyQt6.QtQuick3D"):
            assert f'"{binding}",' in code

    def test_the_dormant_spike_is_still_bundled_until_l1_10(self) -> None:
        """Spec trimming is L1.10's, together with the spike: its QML, its modules and
        ``PyQt6.QtQuickWidgets`` (which only the spike's ``quick.py`` imports)."""
        code = _code(SPEC)
        assert '"open_garden_planner" / "spike_q3d" / "qml"' in code
        for name in ("PyQt6.QtQuickWidgets", "open_garden_planner.spike_q3d.quick",
                     "open_garden_planner.spike_q3d.runner"):
            assert f'"{name}",' in code
        assert "L1.10" in SPEC


class TestReleaseWorkflow:
    def _step(self) -> str:
        start = RELEASE.index("- name: Verify frozen subsystems")
        return RELEASE[start:RELEASE.index("\n      - name:", start + 10)]

    def test_the_step_names_the_new_coverage(self) -> None:
        step = self._step()
        assert "Qt Quick 3D" in step.splitlines()[0]
        failure = next(line for line in step.splitlines() if "Write-Error \"Frozen self-test failed" in line)
        for covered in ("#277", "#291", "Qt Quick 3D", "WARP"):
            assert covered in failure, covered

    def test_the_verdict_is_still_the_exit_code_alone(self) -> None:
        """The 3D log is printed for a human; nothing but ``$p.ExitCode`` decides."""
        step = self._step()
        assert step.count("exit 1") == 2  # the missing exe, the failed self-test — as before
        assert re.search(r"\$p = Start-Process -FilePath \$exe -ArgumentList \"--selftest\" "
                         r"-Wait -PassThru", step)
        assert "if ($p.ExitCode -ne 0) {" in step
        decisions = [line.strip() for line in step.splitlines() if line.strip().startswith("if (")]
        assert decisions == [
            'if (-not (Test-Path $exe)) { Write-Error "Frozen exe not found: $exe"; exit 1 }',
            "if (Test-Path $env:OGP_SELFTEST_LOG) {",
            "if ($p.ExitCode -ne 0) {",
        ]

    def test_the_3d_log_gets_a_path_the_step_can_print(self) -> None:
        step = self._step()
        setting = step.index('$env:OGP_SELFTEST_LOG = Join-Path $env:RUNNER_TEMP "ogp-selftest-3d.log"')
        assert setting < step.index("$p = Start-Process"), "the child must inherit the variable"
        assert "Get-Content $env:OGP_SELFTEST_LOG | Write-Host" in step


class TestRenderJob:
    def _job(self) -> str:
        start = CI.index("\n  render3d:\n")
        return CI[start:]

    def test_the_job_runs_on_both_platforms_with_the_tier_switched_on(self) -> None:
        job = _code(self._job())
        assert "os: [ubuntu-latest, windows-latest]" in job
        assert 'OGP_RENDER3D: "1"' in job
        assert "fail-fast: false" in job and "timeout-minutes:" in job
        assert "- uses: ./.github/actions/setup-locked-env" in job

    def test_linux_renders_under_xvfb_with_opengl_on_llvmpipe(self) -> None:
        job = _code(self._job())
        linux = next(line for line in job.splitlines() if "xvfb-run" in line and "pytest" in line)
        assert linux.strip().startswith('run: xvfb-run -a -s "-screen 0 1920x1080x24" python -m pytest')
        assert "QSG_RHI_BACKEND: opengl" in job and 'LIBGL_ALWAYS_SOFTWARE: "1"' in job
        packages = next(line for line in job.splitlines() if "apt-get install" in line)
        for package in ("xvfb", "libegl1", "libxcb-cursor0", "libgl1-mesa-dri"):
            assert f" {package}" in packages, package

    def test_both_platforms_run_the_same_existing_opt_in_files(self) -> None:
        job = _code(self._job())
        runs = [line for line in job.splitlines() if "python -m pytest" in line]
        assert len(runs) == 2
        for line in runs:
            assert all(name in line for name in RENDER_TESTS), line
        for name in RENDER_TESTS:
            source = (REPO / name).read_text(encoding="utf-8")
            assert "render_tier" in source, f"{name} is not marked as render tier"
        assert "QT_QPA_PLATFORM" not in job, (
            "the pytest process stays offscreen; the test helper gives the DRIVER a real "
            "platform (quick3d_render_support.render_env)")

    def test_the_job_is_not_a_required_check(self) -> None:
        protection = json.loads((REPO / "quality" / "master-protection.json").read_text(encoding="utf-8"))
        contexts = protection["required_status_checks"]["contexts"]
        assert not [name for name in contexts if "render" in name.lower() or "3d" in name.lower()]

    def test_frames_and_logs_are_uploaded_even_when_the_job_fails(self) -> None:
        job = _code(self._job())
        assert "OGP_RENDER3D_ARTIFACTS: ${{ github.workspace }}/render3d-artifacts" in job
        upload = job[job.index("- name: Upload frames, results and logs"):]
        assert "if: always()" in upload and "path: render3d-artifacts/" in upload


def test_the_render_helper_skips_cleanly_without_the_switch(monkeypatch) -> None:
    """On all three platforms: without ``OGP_RENDER3D=1`` the tier is skipped with a
    reason; on Linux it also needs a display; on Windows the switch is enough (the
    driver process gets the real platform — the test process is offscreen)."""
    from tests.integration import quick3d_render_support as support

    monkeypatch.delenv("OGP_RENDER3D", raising=False)
    assert "OGP_RENDER3D=1" in (support.render_tier_skip_reason() or "")
    monkeypatch.setenv("OGP_RENDER3D", "1")
    for platform, display, expected in (("win32", None, None), ("linux", None, "xvfb"),
                                        ("linux", ":99", None), ("darwin", None, None)):
        monkeypatch.setattr(support.sys, "platform", platform)
        if display is None:
            monkeypatch.delenv("DISPLAY", raising=False)
        else:
            monkeypatch.setenv("DISPLAY", display)
        reason = support.render_tier_skip_reason()
        assert (reason is None) if expected is None else (expected in (reason or "")), (platform, reason)


def test_the_driver_gets_a_real_platform_and_this_checkouts_code(monkeypatch, tmp_path: Path) -> None:
    from tests.integration import quick3d_render_support as support

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")  # what the test process runs under
    monkeypatch.setattr(support.sys, "platform", "win32")
    windows = support.render_env(tmp_path)
    assert "QT_QPA_PLATFORM" not in windows  # the default platform: Direct3D 11
    monkeypatch.setattr(support.sys, "platform", "linux")
    monkeypatch.delenv("QSG_RHI_BACKEND", raising=False)
    linux = support.render_env(tmp_path)
    assert (linux["QT_QPA_PLATFORM"], linux["QSG_RHI_BACKEND"]) == ("xcb", "opengl")
    assert linux["LIBGL_ALWAYS_SOFTWARE"] == "1" and linux["XDG_CONFIG_HOME"].startswith(str(tmp_path))
    for env in (windows, linux):
        first, second = env["PYTHONPATH"].split(support.os.pathsep)[:2]
        assert Path(first) == REPO / "src" and Path(second) == REPO
    forced = support.render_env(tmp_path, QT_QPA_PLATFORM="offscreen")
    assert forced["QT_QPA_PLATFORM"] == "offscreen"  # a test may ask for the no-3D platform
