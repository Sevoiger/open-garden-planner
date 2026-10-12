"""``LookController`` against a recording host (Phase 17 L1.4, #385) — default tier.

The controller is engine-free: what it tells a host is plain data. Here a host
that only records stands in for ``Quick3DHost``, so every decision is checked
without a GPU — what look follows which sun, when the sky is rebuilt (gate G4's
decision), what the animation policy does (gate G7), that a preset step is one
``set_look`` and nothing else (gate G6), and that no data never moves the
automatic preset.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from open_garden_planner.core.scene3d import SunState
from open_garden_planner.core.scene3d.look import (
    AutoPolicy,
    LookSpec,
    SkySpec,
    fog_color,
    look_for,
)
from open_garden_planner.ui.view3d import look_controller
from open_garden_planner.ui.view3d.look_controller import (
    AUTO_SAMPLE_MS,
    SKY_SETTLE_MS,
    LookController,
)


class Host:
    """What a look controller may tell a host — and a record of what it was told."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.frames = 0
        self.fail: Exception | None = None

    def _record(self, name: str, value: Any = None) -> None:
        if self.fail is not None:
            raise self.fail
        self.calls.append((name, value))

    def set_look(self, look: LookSpec) -> None:
        self._record("look", look)

    def set_sky(self, sky: SkySpec | None) -> None:
        self._record("sky", sky)

    def set_wind_running(self, running: bool) -> None:
        self._record("wind", running)

    def rest_wind(self) -> None:
        self._record("rest")

    def health(self) -> SimpleNamespace:
        return SimpleNamespace(frames_presented=self.frames)

    # what a look controller must never call (ADR-065 decision 17)
    def grab(self, *_a: Any) -> None:  # pragma: no cover - the test fails if it runs
        raise AssertionError("the look took a frame from the live view")

    wait_ready = pick = project = grab

    def named(self, name: str) -> list[Any]:
        return [value for call, value in self.calls if call == name]

    def take(self) -> list[tuple[str, Any]]:
        calls, self.calls = self.calls, []
        return calls


def sun(elevation: float, azimuth: float = 180.0) -> SunState:
    return SunState(elevation_deg=elevation, azimuth_deg=azimuth)


def make(qtbot: Any, host: Host, **kwargs: Any) -> LookController:  # noqa: ARG001 - Qt must be up
    kwargs.setdefault("sky_light", True)
    kwargs.setdefault("quality", "high")
    return LookController(host, **kwargs)


# ── the look follows the sun the pipeline sent ───────────────────────────────


def test_nothing_is_sent_before_the_first_sun(qtbot) -> None:
    host = Host()
    look = make(qtbot, host)
    look.set_sun(None)
    look.set_quality("low")
    assert host.calls == [], "no look and no sky before the first sun is known"
    assert look.look is None and look.sky is None


def test_the_first_sun_gives_the_look_and_the_first_sky_at_once(qtbot) -> None:
    host = Host()
    look = make(qtbot, host)
    look.set_sun(sun(35.0, 225.0))
    expected = look_for(sun(35.0, 225.0), "high")
    assert host.calls == [("sky", expected.sky), ("look", expected)]
    assert look.look == expected and look.sky == expected.sky and look.sky_rebuilds == 1


def test_the_same_sun_again_sends_nothing(qtbot) -> None:
    """A pass that did not move the sun (an edit, a light-only pass with the same
    instant) costs the engine nothing."""
    host = Host()
    look = make(qtbot, host)
    look.set_sun(sun(35.0, 225.0))
    host.take()
    for _ in range(5):
        look.set_sun(sun(35.0, 225.0))
    assert host.calls == []


@pytest.mark.parametrize("elevation", [-12.0, 0.5, 5.89, 15.23, 60.9])
@pytest.mark.parametrize("quality", ["low", "medium", "high", "ultra"])
def test_the_look_handed_to_the_engine_is_look_for_of_that_sun(qtbot, elevation: float,
                                                               quality: str) -> None:
    host = Host()
    look = make(qtbot, host, quality=quality)
    look.set_sun(sun(elevation, 200.0))
    assert host.named("look") == [look_for(sun(elevation, 200.0), quality)]
    assert look.preset == quality == look.quality


def test_a_host_that_refuses_is_a_log_line_not_a_crash(qtbot, caplog) -> None:
    host = Host()
    look = make(qtbot, host)
    host.fail = RuntimeError("the engine\nis gone")
    with caplog.at_level("ERROR"):
        look.set_sun(sun(30.0))
        look.set_showing(True)
    assert look.look is None
    assert any("refused the look" in record.getMessage() for record in caplog.records)
    assert all("\n" not in record.getMessage() for record in caplog.records)
    host.fail = None
    look.set_sun(sun(31.5))          # the next pass tries again, with everything
    assert [name for name, _value in host.calls] == ["sky", "look"]


# ── gate G4, the decision: one sky per noticeable move ───────────────────────


def test_a_move_below_the_threshold_builds_no_sky(qtbot) -> None:
    host = Host()
    look = make(qtbot, host)
    look.set_showing(True)
    look.set_sun(sun(35.0, 225.0))
    host.take()
    for azimuth in (225.2, 225.5, 225.9, 225.0):
        look.set_sun(sun(35.0, azimuth))
    qtbot.wait(SKY_SETTLE_MS + 120)
    assert host.named("sky") == [] and look.sky_rebuilds == 1
    assert not look._sky_timer.isActive()


def test_a_noticeable_move_builds_one_sky_once_the_sun_rests(qtbot) -> None:
    host = Host()
    look = make(qtbot, host)
    look.set_showing(True)
    look.set_sun(sun(35.0, 225.0))
    host.take()
    look.set_sun(sun(37.0, 228.0))
    assert host.named("sky") == [], "not inside the pass: the sun may still be moving"
    assert host.named("look"), "the light, the exposure and the fog do not wait"
    qtbot.waitUntil(lambda: look.sky_rebuilds == 2, timeout=2000)
    assert host.named("sky") == [look_for(sun(37.0, 228.0), "high").sky]
    assert not look._sky_timer.isActive(), "single shot: nothing ticks afterwards"


def test_a_dragged_sun_costs_one_sky_not_one_per_step(qtbot) -> None:
    """Forty noticeable moves in a row — a time slider being dragged across two hours —
    are ONE rebuild, for where the sun came to rest."""
    host = Host()
    look = make(qtbot, host)
    look.set_showing(True)
    look.set_sun(sun(20.0, 100.0))
    host.take()
    for step in range(1, 41):
        look.set_sun(sun(20.0 + 0.5 * step, 100.0 + 1.5 * step))
    assert host.named("sky") == [] and len(host.named("look")) == 40
    qtbot.waitUntil(lambda: look.sky_rebuilds == 2, timeout=2000)
    qtbot.wait(SKY_SETTLE_MS + 100)
    assert host.named("sky") == [look_for(sun(40.0, 160.0), "high").sky]


def test_a_sun_that_comes_back_owes_no_sky(qtbot) -> None:
    host = Host()
    look = make(qtbot, host)
    look.set_showing(True)
    look.set_sun(sun(35.0, 225.0))
    look.set_sun(sun(39.0, 232.0))
    look.set_sun(sun(35.0, 225.3))      # back within a degree of the sky on screen
    qtbot.wait(SKY_SETTLE_MS + 120)
    assert look.sky_rebuilds == 1


def test_dusk_and_dawn_rebuild_at_once(qtbot) -> None:
    host = Host()
    look = make(qtbot, host)
    look.set_showing(True)
    look.set_sun(sun(0.6, 300.0))
    host.take()
    look.set_sun(sun(0.4, 300.1))       # a move of 0.2 degrees — and night
    night = look_for(sun(0.4, 300.1), "high")
    assert host.calls == [("sky", night.sky), ("look", night)]
    look.set_sun(sun(-30.0, 10.0))      # the night goes on: the sky draws no sun
    look.set_sun(sun(-12.0, 60.0))
    qtbot.wait(SKY_SETTLE_MS + 120)
    assert look.sky_rebuilds == 2
    host.take()
    look.set_sun(sun(0.7, 50.0))        # dawn
    assert [name for name, _value in host.calls] == ["sky", "look"]


def test_while_the_sky_lags_the_fog_meets_the_sky_on_screen(qtbot) -> None:
    """Gate G5's premise: the fog is the horizon of the sky that is SHOWN x the live
    probe exposure — not of a sky that is not built yet."""
    host = Host()
    look = make(qtbot, host)
    look.set_showing(True)
    look.set_sun(sun(4.0, 290.0))
    shown = look_for(sun(4.0, 290.0), "high").sky
    host.take()
    look.set_sun(sun(9.0, 285.0))       # 5 degrees up the ramp: another horizon colour
    wanted = look_for(sun(9.0, 285.0), "high")
    sent = host.named("look")[-1]
    assert wanted.sky.horizon != shown.horizon
    assert sent.fog_color == fog_color(shown.horizon, wanted.mood.probe_exposure)
    assert sent.fog_color != wanted.fog_color
    assert sent.mood == wanted.mood and sent.sky == wanted.sky, "only the fog is tied to the sky"
    qtbot.waitUntil(lambda: look.sky_rebuilds == 2, timeout=2000)
    assert host.named("look")[-1] == wanted, "with the sky rebuilt the fog is the look's own"


def test_a_hidden_view_builds_no_sky_until_it_is_seen_again(qtbot) -> None:
    host = Host()
    look = make(qtbot, host)
    look.set_showing(True)
    look.set_sun(sun(35.0, 225.0))
    look.set_sun(sun(45.0, 240.0))
    look.set_showing(False)             # hidden before the sun rested
    qtbot.wait(SKY_SETTLE_MS + 150)
    assert look.sky_rebuilds == 1 and not look._sky_timer.isActive()
    look.set_showing(True)
    qtbot.waitUntil(lambda: look.sky_rebuilds == 2, timeout=2000)
    assert host.named("sky")[-1] == look_for(sun(45.0, 240.0), "high").sky


def test_without_sky_light_no_sky_is_ever_built(qtbot) -> None:
    """Gate G8's decision: the software fallback gets the look — plain background, flat
    fill — and never a light probe, whatever the sun does."""
    host = Host()
    look = make(qtbot, host, sky_light=False)
    look.set_showing(True)
    for elevation, azimuth in ((35.0, 225.0), (50.0, 180.0), (0.4, 300.0), (-20.0, 20.0), (10.0, 80.0)):
        look.set_sun(sun(elevation, azimuth))
    qtbot.wait(SKY_SETTLE_MS + 120)
    assert host.named("sky") == [] and look.sky_rebuilds == 0 and look.sky is None
    assert not look.sky_light
    assert host.named("look") == [look_for(sun(e, a), "high") for e, a in (
        (35.0, 225.0), (50.0, 180.0), (0.4, 300.0), (-20.0, 20.0), (10.0, 80.0))]
    last = host.named("look")[-1]
    assert last.plain_sky == last.fog_color, "the plain background is the fog's colour"


def test_every_sky_built_is_announced(qtbot) -> None:
    host = Host()
    look = make(qtbot, host)
    built: list[int] = []
    look.sky_rebuilt.connect(lambda: built.append(look.sky_rebuilds))
    look.set_showing(True)
    look.set_sun(sun(35.0, 225.0))
    look.set_sun(sun(-5.0, 300.0))
    assert built == [1, 2]


# ── gate G7: the animation policy ────────────────────────────────────────────


def test_wind_runs_only_while_the_view_is_shown(qtbot) -> None:
    host = Host()
    look = make(qtbot, host)
    assert host.calls == [] and not look.animating
    look.set_showing(True)
    assert host.take() == [("wind", True)] and look.animating
    look.set_showing(True)
    assert host.calls == [], "told once"
    look.set_showing(False)
    assert host.take() == [("wind", False)] and not look.animating
    look.set_showing(False)
    assert host.calls == []


def test_reduced_motion_never_animates_and_rests_the_clock(qtbot) -> None:
    host = Host()
    look = make(qtbot, host, reduced_motion=True)
    look.set_showing(True)
    assert host.take() == [("rest", None)], "at rest: the clock IS 0, not merely stopped"
    look.set_showing(False)
    look.set_showing(True)
    assert host.calls == [] and not look.animating
    assert ("wind", True) not in host.calls


def test_switching_reduced_motion_on_rests_a_running_wind_and_off_starts_it(qtbot) -> None:
    host = Host()
    look = make(qtbot, host)
    look.set_showing(True)
    host.take()
    look.set_reduced_motion(True)
    assert host.take() == [("rest", None)] and not look.animating and look.reduced_motion
    look.set_reduced_motion(True)
    assert host.calls == []
    look.set_reduced_motion(False)
    assert host.take() == [("wind", True)] and look.animating
    look.set_showing(False)
    host.take()
    look.set_reduced_motion(True)       # hidden: still put to rest, for when it is seen
    assert host.take() == [("rest", None)]
    look.set_showing(True)
    assert host.calls == []


def test_hidden_or_still_nothing_of_the_look_ticks(qtbot) -> None:
    """No idle wake-ups: the two timers of the look run only while there is something
    to wait for — a stale sky, a wind whose frames are being counted."""
    host = Host()
    look = make(qtbot, host, quality="auto")
    timers = (look._sky_timer, look._auto_timer)
    assert not any(timer.isActive() for timer in timers)
    look.set_sun(sun(35.0, 225.0))
    assert not any(timer.isActive() for timer in timers), "hidden: nothing"
    look.set_showing(True)
    assert look._auto_timer.isActive() and look._auto_timer.interval() == AUTO_SAMPLE_MS
    look.set_reduced_motion(True)
    assert not any(timer.isActive() for timer in timers), "still: no frames to count"
    look.set_reduced_motion(False)
    look.set_quality("high")
    assert not look._auto_timer.isActive(), "a fixed preset measures nothing"
    look.set_quality("auto")
    assert look._auto_timer.isActive()
    look.set_showing(False)
    assert not any(timer.isActive() for timer in timers)
    look.set_showing(True)
    look.shutdown()
    assert not any(timer.isActive() for timer in timers)


# ── gate G6: the preset, fixed and automatic ─────────────────────────────────


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _run(look: LookController, host: Host, clock: Clock, seconds: float, fps: float | None) -> None:
    """``seconds`` of a view presenting ``fps`` frames a second (None: none at all),
    sampled as the controller's own timer would."""
    step = AUTO_SAMPLE_MS / 1000.0
    owed = 0.0
    for _ in range(int(round(seconds / step))):
        clock.now += step
        if fps is not None:
            owed += fps * step
            host.frames += int(owed)
            owed -= int(owed)
        look._sample_frames()


def test_a_fixed_preset_is_used_as_it_is_and_measures_nothing(qtbot) -> None:
    host, clock = Host(), Clock()
    look = make(qtbot, host, quality="ultra", clock=clock)
    look.set_showing(True)
    look.set_sun(sun(40.0))
    _run(look, host, clock, 60.0, 5.0)
    assert look.preset == "ultra" and host.named("look") == [look_for(sun(40.0), "ultra")]


def test_auto_starts_where_it_was_left_and_steps_down_when_slow(qtbot) -> None:
    host, clock = Host(), Clock()
    kept: list[str] = []
    look = make(qtbot, host, quality="auto", auto_level="high", on_auto_level=kept.append,
                clock=clock)
    changed: list[str] = []
    look.preset_changed.connect(changed.append)
    look.set_showing(True)
    look.set_sun(sun(40.0, 200.0))
    assert look.preset == "high" and look.quality == "auto"
    host.take()
    _run(look, host, clock, 6.0, 12.0)        # 12 frames a second
    assert look.preset == "medium" and changed == ["medium"] and kept == ["medium"]
    assert host.calls == [("look", look_for(sun(40.0, 200.0), "medium"))], \
        "a preset step is ONE set_look: no sky, no wind call, nothing else"
    _run(look, host, clock, 6.0, 12.0)
    assert look.preset == "low" and kept == ["medium", "low"]


def test_auto_steps_up_only_after_the_hold_time(qtbot) -> None:
    host, clock = Host(), Clock()
    look = make(qtbot, host, quality="auto", auto_level="low", clock=clock)
    look.set_showing(True)
    look.set_sun(sun(40.0))
    started = clock.now
    _run(look, host, clock, 5.0, 60.0)
    assert look.preset == "medium"
    stepped_at = look.auto.steps[0][0]
    assert stepped_at - started <= 5.0
    hold = AutoPolicy().hold_s
    _run(look, host, clock, hold - (clock.now - stepped_at) - 0.5, 60.0)
    assert look.preset == "medium", "inside the hold time, however fast the view is"
    _run(look, host, clock, 2.0, 60.0)
    assert look.preset == "high"
    assert look.auto.steps[1][0] - stepped_at >= hold


def test_a_view_that_presents_nothing_holds_its_preset(qtbot) -> None:
    """The display asleep: no frame is presented. No data is not "slow"."""
    for level in ("low", "medium", "high"):
        host, clock = Host(), Clock()
        look = make(qtbot, host, quality="auto", auto_level=level, clock=clock)
        look.set_showing(True)
        look.set_sun(sun(40.0))
        _run(look, host, clock, 300.0, None)
        assert look.preset == level and look.auto.steps == []


def test_frames_are_not_counted_while_hidden_or_still(qtbot) -> None:
    """A hidden view and a view at rest draw on demand: their frame count says nothing
    about speed. What was collected is dropped."""
    host, clock = Host(), Clock()
    look = make(qtbot, host, quality="auto", auto_level="high", clock=clock)
    look.set_showing(True)
    look.set_sun(sun(40.0))
    _run(look, host, clock, 2.0, 12.0)
    look.set_showing(False)
    _run(look, host, clock, 30.0, 1.0)        # samples while hidden: ignored
    look.set_showing(True)
    _run(look, host, clock, 2.0, 12.0)
    assert look.preset == "high", "two halves of a window, with a hidden gap, decide nothing"
    look.set_reduced_motion(True)
    _run(look, host, clock, 30.0, 1.0)
    assert look.preset == "high"


def test_a_preset_change_from_the_settings_is_one_look_call(qtbot) -> None:
    host = Host()
    look = make(qtbot, host, quality="high")
    look.set_showing(True)
    look.set_sun(sun(22.0, 140.0))
    host.take()
    look.set_quality("low")
    assert host.take() == [("look", look_for(sun(22.0, 140.0), "low"))]
    look.set_quality("low")
    look.set_quality("LOW")
    assert host.calls == []
    look.set_quality("nonsense")              # anything else is auto
    assert look.quality == "auto"


def test_a_store_that_cannot_be_written_does_not_stop_the_step(qtbot, caplog) -> None:
    host, clock = Host(), Clock()

    def broken(_level: str) -> None:
        raise OSError("read-only")

    look = make(qtbot, host, quality="auto", auto_level="high", on_auto_level=broken, clock=clock)
    look.set_showing(True)
    look.set_sun(sun(40.0))
    with caplog.at_level("WARNING"):
        _run(look, host, clock, 6.0, 12.0)
    assert look.preset == "medium"


# ── settings ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def settings(qtbot):  # noqa: ARG001 - Qt must be up
    from open_garden_planner.app.settings import get_settings

    store = get_settings()
    saved = (store.view3d_quality, store.view3d_reduced_motion, store.view3d_auto_level)
    yield store
    store.view3d_quality, store.view3d_reduced_motion, store.view3d_auto_level = saved


def test_from_settings_reads_the_three_values_and_keeps_where_auto_settles(qtbot, settings) -> None:
    settings.view3d_quality = "auto"
    settings.view3d_reduced_motion = True
    settings.view3d_auto_level = "low"
    host = Host()
    look = LookController.from_settings(host, sky_light=False)
    assert (look.quality, look.preset, look.reduced_motion, look.sky_light) == (
        "auto", "low", True, False)
    look._on_auto_level("high")               # what a step calls
    assert settings.view3d_auto_level == "high"


def test_reload_settings_follows_the_preferences_dialog(qtbot, settings) -> None:
    settings.view3d_quality = "high"
    settings.view3d_reduced_motion = False
    host = Host()
    look = LookController.from_settings(host, sky_light=True)
    look.set_showing(True)
    look.set_sun(sun(30.0))
    host.take()
    settings.view3d_quality = "medium"
    settings.view3d_reduced_motion = True
    look.reload_settings()
    assert host.take() == [("look", look_for(sun(30.0), "medium")), ("rest", None)]
    look.reload_settings()
    assert host.calls == []


def test_after_shutdown_nothing_reaches_the_host(qtbot) -> None:
    host = Host()
    look = make(qtbot, host, quality="auto")
    look.set_showing(True)
    look.set_sun(sun(30.0))
    look.shutdown()
    host.take()
    look.set_sun(sun(60.0))
    look.set_showing(False)
    look.set_showing(True)
    look.set_quality("low")
    look.set_reduced_motion(True)
    look._settle_sky()
    look._sample_frames()
    assert host.calls == []


# ── the memory rule (ADR-065 decision 17) ────────────────────────────────────


def test_the_look_controller_never_asks_a_host_for_a_frame() -> None:
    """A readback of the live view renders a frame that is never presented. The look
    is told things and reads ``health()``: none of the four calls appears in its source."""
    tree = ast.parse(Path(look_controller.__file__).read_text(encoding="utf-8"))
    called = {node.func.attr for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert called.isdisjoint({"grab", "wait_ready", "pick", "project"}), called
    assert "health" in called


def test_the_look_controller_imports_no_engine() -> None:
    tree = ast.parse(Path(look_controller.__file__).read_text(encoding="utf-8"))
    modules = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    modules |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                for alias in node.names}
    assert not any("QtQuick" in name or "QtQml" in name or "quick3d" in name for name in modules)
