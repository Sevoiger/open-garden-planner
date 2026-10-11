"""The frozen exe's 3D self-test: what it renders and how it judges the frame (L1.2).

``--selftest`` runs in the built exe on a GPU-less release runner; its exit code
is the only channel. So the JUDGE must be right on its own: it is a pure
function of the frame's pixels and is tested here on synthetic frames — a good
one, a blank one, a mirrored one, one without a shadow — with no engine at all.
That it passes on a real render (GPU and WARP, source and frozen) is the render
tier's and the build's job.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

from open_garden_planner.core.scene3d import MATERIAL_KINDS, Record, SceneSync
from open_garden_planner.core.shadow_geometry import compute_scene_shadows
from open_garden_planner.ui.view3d.quick3d import selftest
from open_garden_planner.ui.view3d.quick3d.adapter import Quick3DSink
from open_garden_planner.ui.view3d.quick3d.shots import top_down_pixel
from tests.unit.test_quick3d_adapter import FakeScene

GROUND = (20, 78, 12)
SHADOW = (8, 26, 6)
BOX = (255, 6, 4)
SWATCH = (150, 12, 6)
GLASS = (60, 60, 14)


def _paint(frame: np.ndarray, region: selftest.Region, colour: tuple[int, int, int],
           scale: float, grow_cm: float = 0.0) -> None:
    """Fill the scene-frame rectangle of ``region`` (plus a margin) with ``colour``."""
    height, width = frame.shape[:2]
    half_w, half_h = region.width_cm / 2 + grow_cm, region.depth_cm / 2 + grow_cm
    left, top = top_down_pixel(region.east_cm - half_w, region.north_cm + half_h, selftest.SHOT,
                               (width, height), scale)
    right, bottom = top_down_pixel(region.east_cm + half_w, region.north_cm - half_h, selftest.SHOT,
                                   (width, height), scale)
    frame[round(top):round(bottom), round(left):round(right), :3] = colour


def _good_frame(scale: float = 1.0) -> np.ndarray:
    """What the self-test scene looks like from above, painted by hand."""
    width, height = round(selftest.SIZE[0] * scale), round(selftest.SIZE[1] * scale)
    frame = np.zeros((height, width, 4), np.uint8)
    frame[..., :3] = GROUND
    frame[..., 3] = 255
    _paint(frame, selftest.REGIONS["shadow"], SHADOW, scale, grow_cm=6.0)
    for kind in selftest.SWATCH_KINDS:
        _paint(frame, selftest.REGIONS[kind], GLASS if kind == "glass" else SWATCH, scale, grow_cm=4.0)
    _paint(frame, selftest.REGIONS["box"], BOX, scale, grow_cm=10.0)
    return frame


class TestJudge:
    @pytest.mark.parametrize("scale", [1.0, 1.5, 2.0])
    def test_a_good_frame_passes(self, scale: float) -> None:
        assert selftest.judge_frame(_good_frame(scale), device_pixel_ratio=scale) == []

    def test_a_blank_frame_fails_every_content_check(self) -> None:
        frame = _good_frame()
        frame[..., :3] = (207, 226, 242)  # the bare sky colour: nothing was drawn
        problems = selftest.judge_frame(frame)
        assert len(problems) == 3 + len(selftest.SWATCH_KINDS)  # ground, box, shadow, each swatch
        assert problems[0].startswith("the ground is not lit meadow")
        assert problems[1].startswith("the box is not there")

    def test_a_frame_of_bare_ground_fails(self) -> None:
        frame = _good_frame()
        frame[..., :3] = GROUND  # the ground rendered, no item did
        problems = selftest.judge_frame(frame)
        assert problems[0].startswith("the box is not there")       # the ground itself is fine
        assert problems[1].startswith("the shadow is missing")
        assert len(problems) == 2 + len(selftest.SWATCH_KINDS)

    def test_a_missing_shadow_fails(self) -> None:
        """The sun is switched off, or the shadow map is broken: lit box, no shadow."""
        frame = _good_frame()
        _paint(frame, selftest.REGIONS["shadow"], GROUND, 1.0, grow_cm=6.0)
        (problem,) = selftest.judge_frame(frame)
        assert problem.startswith("the shadow is missing: the ground north-east of the box")

    @pytest.mark.parametrize("flip", ["east-west", "north-south"])
    def test_a_mirrored_frame_fails(self, flip: str) -> None:
        """The shadow lies north-east of the box and the swatches south of it: a
        mirrored frame cannot have both where they belong."""
        frame = _good_frame()
        mirrored = frame[:, ::-1] if flip == "east-west" else frame[::-1]
        assert selftest.judge_frame(np.ascontiguousarray(mirrored)) != []

    @pytest.mark.parametrize("kind", selftest.SWATCH_KINDS)
    def test_a_material_that_did_not_draw_fails(self, kind: str) -> None:
        frame = _good_frame()
        _paint(frame, selftest.REGIONS[kind], GROUND, 1.0, grow_cm=4.0)
        (problem,) = selftest.judge_frame(frame)
        assert kind in problem

    def test_a_frame_of_the_wrong_size_fails_before_any_pixel_is_read(self) -> None:
        (problem,) = selftest.judge_frame(np.zeros((30, 40, 4), np.uint8))
        assert "40x30" in problem and "160x120" in problem


def _footprint(record: Record) -> list[tuple[float, float]]:
    """A self-test item's footprint in scene coordinates (none of them is turned)."""
    return [(record.transform.east_cm + x, record.transform.north_cm + y)
            for x, y in record.footprints[0]]


def _inside(polygons: list[list[tuple[float, float]]], x: float, y: float) -> bool:
    """Even-odd point-in-polygon over a set of rings."""
    hit = False
    for polygon in polygons:
        count = len(polygon)
        for index in range(count):
            (x1, y1), (x2, y2) = polygon[index], polygon[(index + 1) % count]
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                hit = not hit
    return hit


def _samples(region: selftest.Region) -> list[tuple[float, float]]:
    """The corners, edge midpoints and centre of a region."""
    return [(region.east_cm + dx * region.width_cm / 2, region.north_cm + dy * region.depth_cm / 2)
            for dx in (-1, 0, 1) for dy in (-1, 0, 1)]


class TestScene:
    def test_the_scene_uses_every_material_kind(self) -> None:
        """One item per material kind: every shader the bundle ships compiles in the
        self-test, so a missing or broken shader file fails the release, not a user."""
        engine = FakeScene()
        sync = SceneSync(Quick3DSink(engine), selftest.registry(), validate=True)
        sync.apply(selftest.records())
        assert {part.spec.kind for part in engine.parts} == MATERIAL_KINDS
        assert sync.failures == {} and len(engine.parts) == len(MATERIAL_KINDS)

    def test_every_region_lies_inside_the_frame(self) -> None:
        width, height = selftest.SIZE
        for name, region in selftest.REGIONS.items():
            for x, y in _samples(region):
                px, py = top_down_pixel(x, y, selftest.SHOT, (width, height))
                assert 1 <= px <= width - 1 and 1 <= py <= height - 1, name

    def test_each_item_patch_is_well_inside_its_item(self) -> None:
        records = selftest.records()
        for name in ("box", *selftest.SWATCH_KINDS):
            ring = _footprint(records[name])
            low_x, high_x = min(x for x, _ in ring), max(x for x, _ in ring)
            low_y, high_y = min(y for _, y in ring), max(y for _, y in ring)
            for x, y in _samples(selftest.REGIONS[name]):
                assert low_x + 5 <= x <= high_x - 5 and low_y + 5 <= y <= high_y - 5, name

    def test_the_shadow_patch_is_inside_the_analytic_2d_shadow(self) -> None:
        """Where the judge looks for the shadow is where ``core/shadow_geometry`` — the
        2D overlay's code — puts the box's shadow for the self-test's sun, and it is
        outside the box. Not a guess."""
        box = selftest.records()["box"]
        ring = _footprint(box)
        shadow = compute_scene_shadows([(ring, box.height_cm)], selftest.SUN.elevation_deg,
                                       selftest.SUN.azimuth_deg)
        for x, y in _samples(selftest.REGIONS["shadow"]):
            assert _inside(shadow, x, y), (x, y)
            assert not _inside([ring], x, y), (x, y)

    def test_the_lit_ground_patch_is_in_no_shadow_and_on_no_item(self) -> None:
        records = selftest.records()
        rings = [_footprint(record) for record in records.values()]
        shadows = compute_scene_shadows(
            [(ring, record.height_cm) for ring, record in zip(rings, records.values(), strict=True)],
            selftest.SUN.elevation_deg, selftest.SUN.azimuth_deg)
        for x, y in _samples(selftest.REGIONS["lit ground"]):
            assert not _inside(shadows, x, y), (x, y)
            assert not any(_inside([ring], x, y) for ring in rings), (x, y)

    def test_everything_stands_on_the_plan_rectangle(self) -> None:
        for record in selftest.records().values():
            for x, y in _footprint(record):
                assert 0.0 < x < selftest.GROUND.width_cm and 0.0 < y < selftest.GROUND.depth_cm


class TestRun:
    def test_an_import_failure_is_reported_and_fails(self, monkeypatch: pytest.MonkeyPatch,
                                                     tmp_path) -> None:
        import importlib

        real = importlib.import_module

        def broken(name: str, *args: object, **kwargs: object) -> object:
            if name == "PyQt6.QtQuick3D":
                raise ImportError("DLL load failed while importing QtQuick3D")
            return real(name, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(importlib, "import_module", broken)
        result = selftest.run_selftest(tmp_path / "selftest.log")
        assert not result.ok
        assert any(line.startswith("  FAIL import PyQt6.QtQuick3D") for line in result.lines)
        assert not any("frame" in line and "OK" in line for line in result.lines)  # nothing rendered

    def test_a_render_error_is_reported_and_fails(self, monkeypatch: pytest.MonkeyPatch,
                                                  tmp_path) -> None:
        def explode(*_args: object, **_kwargs: object) -> object:
            raise RuntimeError("2 Qt error(s) during the render — QSpirvCompiler: Failed to parse shader")

        monkeypatch.setattr(selftest, "_render", explode)
        result = selftest.run_selftest(tmp_path / "selftest.log")
        assert not result.ok
        (failure,) = [line for line in result.lines if "FAIL" in line]
        assert "QSpirvCompiler" in failure and "RuntimeError" in failure
        assert "FAIL" in (tmp_path / "selftest.log").read_text(encoding="utf-8")

    def test_a_frame_the_judge_rejects_fails(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        blank = _good_frame()
        blank[..., :3] = (207, 226, 242)
        monkeypatch.setattr(selftest, "_render", lambda *_a, **_k: selftest.Rendered(
            blank, 1.0, "Direct3D11Rhi", "Microsoft Basic Render Driver", 321.0, {"error": 0}))
        result = selftest.run_selftest(tmp_path / "selftest.log")
        assert not result.ok and any("FAIL" in line and "box" in line for line in result.lines)

    def test_a_good_frame_passes_and_names_the_device(self, monkeypatch: pytest.MonkeyPatch,
                                                      tmp_path) -> None:
        monkeypatch.setattr(selftest, "_render", lambda *_a, **_k: selftest.Rendered(
            _good_frame(), 1.0, "Direct3D11Rhi", "Microsoft Basic Render Driver", 321.0, {"error": 0}))
        result = selftest.run_selftest(tmp_path / "selftest.log")
        assert result.ok, result.lines
        assert all(line.startswith("  OK  ") for line in result.lines)
        assert any("Microsoft Basic Render Driver" in line and "Direct3D11Rhi" in line
                   for line in result.lines)
        log = (tmp_path / "selftest.log").read_text(encoding="utf-8")
        assert "watchdog" in log and "PASS" in log

    def test_the_log_path_can_come_from_the_environment(self, monkeypatch: pytest.MonkeyPatch,
                                                        tmp_path) -> None:
        monkeypatch.setenv("OGP_SELFTEST_LOG", str(tmp_path / "from-env.log"))
        assert selftest.default_log_path() == tmp_path / "from-env.log"
        monkeypatch.delenv("OGP_SELFTEST_LOG")
        assert selftest.default_log_path().name == "OpenGardenPlanner-selftest-3d.log"


_HANG = textwrap.dedent("""
    import sys
    import time
    from pathlib import Path
    from open_garden_planner.ui.view3d.quick3d import selftest

    def stuck(*_args, **_kwargs):
        time.sleep(120)  # a render that never comes back

    selftest._render = stuck
    result = selftest.run_selftest(Path(sys.argv[1]), watchdog_s=1.5)
    print("returned", result.ok, flush=True)
""")


def test_a_hang_becomes_exit_code_1_with_the_stack_in_the_log(tmp_path: Path) -> None:
    """The release runner must never sit in a hung self-test: the watchdog dumps every
    thread's stack into the log and exits the process with code 1 — in a child here,
    because that exit is real."""
    log = tmp_path / "selftest.log"
    src = Path(__file__).resolve().parents[2] / "src"
    env = dict(os.environ, PYTHONPATH=str(src), QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run(  # noqa: S603 - fixed argv, our own code
        [sys.executable, "-c", _HANG, str(log)], env=env, capture_output=True, text=True,
        timeout=100)
    assert proc.returncode == 1, proc.stdout + proc.stderr[-2000:]
    assert "returned" not in proc.stdout, "the hang must end the process, not return a verdict"
    text = log.read_text(encoding="utf-8")
    assert "[watchdog] armed=True seconds=1.5" in text
    assert "Timeout (0:00:01" in text and "in stuck" in text and "run_selftest" in text
