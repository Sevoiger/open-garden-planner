"""The engine package in the frozen exe and in CI, pinned as text (Phase 17 L1.2, #385).

Neither the PyInstaller build nor GitHub's runners run in the test suite, so the
things that silently drop the 3D engine from a release are pinned where they
are written down:

- ``installer/ogp.spec`` ships the QML and shader files and names the lazily
  imported package (a missing data file only fails when a Model uses the shader);
- ``release.yml`` still runs ``--selftest`` the one way that tests the shipping
  condition, decides by the exit code alone, cannot hang, and prints the log in a
  step of its own that always runs;
- ``frozen-selftest.yml`` runs the same gate on pull requests, with the release
  recipe and the same step scripts, whenever shipped code changes;
- ``ci.yml`` has the ``render3d`` job on both platforms, running files that
  exist and are opt-in, and the job is not a required check.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "src" / "open_garden_planner" / "ui" / "view3d" / "quick3d"
SPEC = (REPO / "installer" / "ogp.spec").read_text(encoding="utf-8")
CI = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
RELEASE = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
FROZEN = (REPO / ".github" / "workflows" / "frozen-selftest.yml").read_text(encoding="utf-8")
RENDER_TESTS = ("tests/integration/test_quick3d_render.py",
                "tests/integration/test_quick3d_engine.py",
                "tests/integration/test_quick3d_selftest.py",
                "tests/integration/test_quick3d_lifecycle.py")


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


def _step(workflow: str, name: str) -> str:
    """One step of a workflow — from its ``- name:`` up to the next step (or the comment
    block that introduces the next step)."""
    start = workflow.index(f"- name: {name}")
    end = workflow.find("\n      - name:", start + 10)
    step = workflow[start:] if end < 0 else workflow[start:end]
    lines = step.rstrip().splitlines()
    while lines and (not lines[-1].strip() or lines[-1].lstrip().startswith("#")):
        lines.pop()  # the next step's comment block is not part of this one
    return "\n".join(lines) + "\n"


def _selftest_step(workflow: str) -> str:
    return _step(workflow, "Verify frozen subsystems")


def _log_step(workflow: str) -> str:
    return _step(workflow, "Print the self-test log")


def _script(step: str) -> str:
    """The step's ``run: |`` block."""
    return step[step.index("        run: |\n") + len("        run: |\n"):]


class TestReleaseWorkflow:
    def _step(self) -> str:
        return _selftest_step(RELEASE)

    def test_the_step_names_the_new_coverage(self) -> None:
        step = self._step()
        assert "Qt Quick 3D" in step.splitlines()[0]
        failure = next(line for line in step.splitlines() if "Frozen self-test failed" in line)
        assert failure.strip().startswith('Write-Host "::error::Frozen self-test failed (exit ')
        for covered in ("#277", "#291", "Qt Quick 3D", "WARP", "watchdog", "The next step prints"):
            assert covered in failure, covered

    def test_the_verdict_is_still_the_exit_code_alone(self) -> None:
        """Nothing but ``$p.ExitCode`` decides — and the step does nothing else."""
        step = self._step()
        assert step.count("exit 1") == 2  # the missing exe, the failed self-test — as before
        assert re.search(r"\$p = Start-Process -FilePath \$exe -ArgumentList \"--selftest\" "
                         r"-Wait -PassThru", step)
        decisions = [line.strip() for line in step.splitlines() if line.strip().startswith("if (")]
        assert decisions == [
            'if (-not (Test-Path $exe)) { Write-Host "::error::Frozen exe not found: $exe"; exit 1 }',
            "if ($p.ExitCode -ne 0) {",
        ]
        script = _script(step)
        order = [script.index(marker) for marker in (
            "$p = Start-Process", "if ($p.ExitCode -ne 0) {", "Frozen self-test failed",
            "            exit 1\n", "Frozen self-test passed")]
        assert order == sorted(order), order
        assert "Write-Error" not in script, "Write-Error ends the step at once"
        assert "Get-Content" not in script, "the log is the next step's business"

    def test_the_exe_writes_its_log_where_the_next_step_reads_it(self) -> None:
        """The exe has no stdout; ``OGP_SELFTEST_LOG`` is the only way to its log. Both
        steps get the SAME path, from the step's own ``env`` (the child inherits it)."""
        path = "          OGP_SELFTEST_LOG: ${{ runner.temp }}/ogp-selftest-3d.log\n"
        for step in (self._step(), _log_step(RELEASE)):
            assert step.count(path) == 1 and step.index("        env:\n") < step.index(path) < step.index(
                "        run: |\n")
        assert "Get-Content -LiteralPath $env:OGP_SELFTEST_LOG -ErrorAction Stop" in _log_step(RELEASE)

    def test_the_log_is_printed_by_a_step_that_always_runs_and_cannot_fail(self) -> None:
        """Review round 2, P2-6. The self-test step used to print the log itself, after
        the verdict: a step cut off by its time limit prints nothing after the cut —
        and that is the run whose log says where it hung. The log step comes right
        after, runs ``always()`` (in a release run: when a release is being built at
        all), reads inside try/catch and ends with ``exit 0``."""
        after = RELEASE[RELEASE.index("      - name: Verify frozen subsystems"):]
        names = re.findall(r"^      - name: (.+)$", after, re.MULTILINE)
        assert names[:2] == ["Verify frozen subsystems (Qt3D #277, Agent API #291, Qt Quick 3D render)",
                             "Print the self-test log"]
        step = _log_step(RELEASE)
        assert "        if: always() && steps.check_tag.outputs.exists == 'false'\n" in step
        script = _script(step)
        order = [script.index(marker) for marker in (
            "try {", "Get-Content", "} catch {", "The verdict of the self-test step stands",
            "          exit 0\n")]
        assert order == sorted(order), order
        assert "exit 1" not in script and "Write-Error" not in script
        assert script.rstrip().endswith("exit 0")

    def test_neither_the_step_nor_the_job_can_run_for_six_hours(self) -> None:
        """Review P1-4 (1): the step and the job are bounded — and so is the exe itself
        (``main.SELFTEST_WATCHDOG_S``), well inside the step's limit."""
        # read, not imported: main.py imports QtWebEngine, which refuses to load once a
        # QApplication exists — an import here would pass or fail by test order
        source = (REPO / "src" / "open_garden_planner" / "main.py").read_text(encoding="utf-8")
        watchdog = re.search(r"^SELFTEST_WATCHDOG_S = (\d+(?:\.\d+)?)$", source, re.MULTILINE)
        assert watchdog is not None, "main.py no longer declares SELFTEST_WATCHDOG_S"
        step = self._step()
        assert "        timeout-minutes: 10\n" in step
        assert float(watchdog.group(1)) <= 0.5 * 10 * 60, "the in-exe watchdog must fire first"
        job = RELEASE[RELEASE.index("\n  release:\n"):RELEASE.index("\n    steps:\n")]
        assert re.search(r"\n    timeout-minutes: \d+\n", job), "the release job has no time limit"


class TestFrozenSelftestWorkflow:
    """Review P1-4 (2): the frozen gate BEFORE a merge, not only after it."""

    def _triggers(self) -> str:
        return _code(FROZEN[FROZEN.index("\non:\n"):FROZEN.index("\npermissions:")])

    def test_it_runs_on_demand_and_on_every_pull_request_that_changes_shipped_code(self) -> None:
        """Review round 2, P2-11: the first filter named the 3D package and ``main.py`` —
        but ``--selftest`` also exercises ``core/scene3d`` and the Agent API, and the
        project's rule is "build and verify the exe before every merge". The whole
        package, then."""
        triggers = self._triggers()
        assert "  workflow_dispatch:\n" in triggers
        assert "  pull_request:\n    branches: [master]\n    paths:\n" in triggers
        assert "push:" not in triggers, "not on every push: the build takes minutes"
        paths = re.findall(r'^      - "([^"]+)"$', triggers, re.MULTILINE)
        assert paths == [
            "src/open_garden_planner/**",
            "installer/**",
            "pyproject.toml",
            "uv.lock",
            "pylock.toml",
            ".github/workflows/frozen-selftest.yml",
            ".github/workflows/release.yml",
            ".github/actions/setup-locked-env/**",
        ]

    def test_every_filtered_path_exists(self) -> None:
        """A path filter that matches nothing is a workflow that never runs."""
        for pattern in re.findall(r'^      - "([^"]+)"$', self._triggers(), re.MULTILINE):
            target = REPO / pattern.removesuffix("/**")
            assert target.exists(), pattern
        for shipped in ("ui/view3d/quick3d/selftest.py", "core/scene3d/sync.py",
                        "agent_api/__init__.py", "main.py"):
            assert (REPO / "src/open_garden_planner" / shipped).is_file(), shipped

    def test_it_can_only_read_the_repository(self) -> None:
        assert "\npermissions:\n  contents: read\n" in FROZEN
        assert FROZEN.count("permissions:") == 1, "no job widens the workflow's permissions"

    def test_it_uses_no_action_that_ci_does_not_use(self) -> None:
        used = set(re.findall(r"uses: (\S+)", _code(FROZEN)))
        assert used == {"actions/checkout@v4", "./.github/actions/setup-locked-env"}
        assert used <= set(re.findall(r"uses: (\S+)", _code(CI)))

    def test_it_builds_with_the_release_recipe_not_a_copy_of_it(self) -> None:
        code = _code(FROZEN)
        assert "runs-on: windows-latest" in code
        assert "        run: python installer/build_installer.py --skip-nsis\n" in code
        assert "PyInstaller" not in code and "ogp.spec" not in code, "no second build recipe"
        assert "run: python installer/build_installer.py --version" in _code(RELEASE)
        script = (REPO / "installer" / "build_installer.py").read_text(encoding="utf-8")
        assert '"--skip-nsis"' in script and '"--noconfirm"' in script and '"--clean"' in script

    def test_the_build_step_has_a_limit_of_its_own(self) -> None:
        """Review round 2, P2-10: only the job's 45 minutes bounded the build."""
        step = _step(FROZEN, "Build the bundle")
        assert "        timeout-minutes: 30\n" in step
        limits = [int(n) for n in re.findall(r"^        timeout-minutes: (\d+)$", _code(FROZEN), re.MULTILINE)]
        job = int(re.search(r"^    timeout-minutes: (\d+)$", FROZEN, re.MULTILINE).group(1))  # type: ignore[union-attr]
        assert limits == [30, 5, 10] and max(limits) < job, "each step ends before the job does"

    def test_the_comments_say_what_the_bundle_and_the_smoke_really_are(self) -> None:
        """Review round 2, P2-7 / P2-10. The workflow said "no ``_version.py`` is written"
        — the build script always writes one, with its default ``1.0.0`` when no version
        is passed, so the pull-request exe runs the update check as v1.0.0 during the
        smoke. And "survived 8 seconds" is also true of a process blocked in an error
        dialog. Pinned against the files the statements are about."""
        script = (REPO / "installer" / "build_installer.py").read_text(encoding="utf-8")
        assert 'APP_VERSION = "1.0.0"' in script
        assert re.search(r"if not args\.skip_pyinstaller:\n\s+_write_version_module\(APP_VERSION\)", script)
        application = (REPO / "src/open_garden_planner/app/application.py").read_text(encoding="utf-8")
        start = application.index("    def _start_update_check(self) -> None:")
        body = application[start:application.index("\n    def ", start + 10)]
        assert 'if not getattr(sys, "frozen", False):' in body
        assert "environ" not in body and "argv" not in body, (
            "the app has a switch for the update check now: use it for the smoke, and say so")
        checker = (REPO / "src/open_garden_planner/services/update_checker.py").read_text(encoding="utf-8")
        assert "except Exception:  # noqa: BLE001" in checker[checker.index("class UpdateChecker"):]
        comments = "\n".join(line for line in FROZEN.splitlines() if line.lstrip().startswith("#"))
        assert "no _version.py" not in comments.lower()
        for statement in ("writes _version.py with its default", "calls itself v1.0.0",
                          "blocked in an error dialog", "The self-test below is the real gate",
                          "the app has no switch to turn", "swallows every network error"):
            assert " ".join(statement.split()) in " ".join(comments.replace("#", " ").split()), statement

    def test_it_runs_the_same_two_steps_as_the_release(self) -> None:
        """One script each, two workflows: a fix to a release step that is not made here
        (or the other way round) would leave the pull-request gate testing something
        else."""
        assert _script(_selftest_step(FROZEN)) == _script(_selftest_step(RELEASE))
        assert _script(_log_step(FROZEN)) == _script(_log_step(RELEASE))
        assert "        shell: pwsh\n        timeout-minutes: 10\n" in _selftest_step(FROZEN)
        assert "        if: always()\n" in _log_step(FROZEN)
        after = FROZEN[FROZEN.index("      - name: Verify frozen subsystems"):]
        assert re.findall(r"^      - name: (.+)$", after, re.MULTILINE)[1:] == ["Print the self-test log"]

    def test_the_job_is_bounded_and_not_a_required_check(self) -> None:
        job = FROZEN[FROZEN.index("\njobs:\n"):FROZEN.index("    steps:\n")]
        assert re.search(r"\n    timeout-minutes: \d+\n", job)
        name = re.search(r"\n    name: (.+)\n", job)
        assert name is not None
        protection = json.loads((REPO / "quality" / "master-protection.json").read_text(encoding="utf-8"))
        contexts = protection["required_status_checks"]["contexts"]
        assert name.group(1) not in contexts and not [c for c in contexts if "frozen" in c.lower()]


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


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_no_child_of_an_engine_test_can_reach_the_developers_data_or_profile(
        monkeypatch, tmp_path: Path, platform: str) -> None:
    """Review round 2, P2-8 / P2-9: the environment HANDED TO THE CHILD, inspected.

    The render driver loads a plan through ``ProjectManager`` — which creates the app's
    data directory — and it inherited the developer's real ``APPDATA``. Every child a
    test of the engine package starts now gets, whatever the parent's environment
    says: its data directories under the test's own scratch directory, and a user
    profile of the test process's own (``QStandardPaths`` and the graphics driver's
    caches follow the profile, not ``APPDATA`` — measured)."""
    from tests.integration import quick3d_render_support as support

    data = [name for name, _folder in support.PRIVATE_DIRS]
    moved = ["USERPROFILE", "LOCALAPPDATA"] if platform == "win32" else ["XDG_CACHE_HOME"]
    assert data == ["APPDATA", "XDG_DATA_HOME", "XDG_CONFIG_HOME"]
    for name in (*data, "USERPROFILE", "LOCALAPPDATA", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / "the-developers-own" / name))
    monkeypatch.setattr(support.sys, "platform", platform)
    scratch = tmp_path / "scratch"
    profile = support.session_profile()
    for build in (support.isolated_env, support.render_env):
        env = build(scratch)
        for name, folder in support.PRIVATE_DIRS:
            assert Path(env[name]) == scratch / folder and Path(env[name]).is_dir(), (build, name)
        for name in moved:
            assert Path(env[name]).is_dir(), (build, name)
            assert profile == Path(env[name]) or profile in Path(env[name]).parents, (build, name)
        assert not [name for name in (*data, *moved) if "the-developers-own" in env[name]]
    assert profile.is_dir() and profile.name.startswith("ogp-quick3d-profile-")
    assert support.session_profile() == profile, "one profile per test process"
    assert tmp_path not in profile.parents and profile != Path.home()


def test_every_child_environment_of_the_engine_tests_is_built_by_the_helper() -> None:
    """…and nobody builds a child's environment by hand again: the tests of the engine
    package never copy ``os.environ`` themselves, and every ``env=`` they pass names a
    variable — which the two tests above show can only come from the helper."""
    files = [*sorted((REPO / "tests").glob("*/test_quick3d_*.py")),
             *sorted((REPO / "tests" / "integration").glob("quick3d_*.py")),
             REPO / "tests" / "unit" / "test_main_hardening.py"]
    assert len(files) >= 14
    children = 0
    for path in files:
        if path.name == "quick3d_render_support.py":
            continue  # the helper itself
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                call = ast.unparse(node)
                assert not call.startswith(("dict(os.environ", "os.environ.copy(")), (
                    f"{path.name}:{node.lineno} copies the parent's environment: {call[:60]}")
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("run", "Popen")
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"):
                continue
            keywords = {keyword.arg: keyword.value for keyword in node.keywords}
            if path.name == "quick3d_lifecycle_child.py" or "taskkill" in ast.unparse(node):
                continue  # not a child of the tests' own code (the tree kill)
            children += 1
            assert "env" in keywords, f"{path.name}:{node.lineno} starts a child with the parent's environment"
            value = ast.unparse(keywords["env"])
            assert value == "env" or value.startswith(("isolated_env(", "render_env(", "_child_env(")), (
                f"{path.name}:{node.lineno}: env={value}")
    assert children >= 10
