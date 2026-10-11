"""The host's lifecycle as a matrix of call orders (Phase 17 L1.2, fix round 1).

Render tier (``OGP_RENDER3D=1``). Each test starts ``quick3d_lifecycle_child.py``
with ONE order in a process of its own and judges what the process reported —
because the failure the matrix exists for is not an exception: a Qt Quick window
grabbed at the wrong moment hands back a perfectly good image, and the process
dies in the next pass of the event loop (``Quick3DHost``'s module docstring).

The rule every order is judged by — the host's whole contract in one sentence:

    a call returns a CORRECT answer or raises the DOCUMENTED error, and the
    process survives.

"Correct" for a frame: the red probe box covers the pixels its size predicts
(100 x 100 cm at 0.5 px/cm = 2,500 px, times the device pixel ratio squared).
Which of the two a state allows is ``EXPECTED_GRAB`` below, the table of
``host.py`` restated — so an order whose timing differs on another platform (is
the window exposed right after ``show()``?) is still judged, by the state the
host itself reported. What Windows was MEASURED to do is pinned on Windows.

Two orders are controls: they do what the guard forbids, on the raw view, and
must die. Without them a matrix that cannot see a death would pass as well.

Measured 2026-10-11, Qt 6.11, Direct3D 11 on an RTX 4070 and on WARP: every
order survives and answers as pinned; both controls die (3 of 3 each).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.integration import quick3d_lifecycle_child as child
from tests.integration.quick3d_render_support import QML_SOURCE, REPO, render_env, render_tier

pytestmark = render_tier

CHILD = Path(child.__file__).resolve()
WINDOWS = sys.platform == "win32"
#: The answer a grab must give in each state (``host.py``'s table). ``new`` is the one
#: offscreen frame of a standalone host; an embedded one has none (see the order).
EXPECTED_GRAB = {"live": "frame", "new": "frame", "starting": "refused", "hidden": "refused",
                 "spent": "refused", "closed": "refused"}
#: The three releases: legal on a closed host, where they do nothing.
RELEASES = ("destroy_part", "clear", "close")


def run_order(order: str, tmp_path: Path, qml_dir: Path | None = None
              ) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(CHILD), order]
    if qml_dir is not None:
        command.append(str(qml_dir))
    return subprocess.run(  # noqa: S603 - fixed argv, our own code
        command, env=render_env(tmp_path), capture_output=True, text=True, timeout=170, cwd=REPO)


def survived(proc: subprocess.CompletedProcess[str]) -> bool:
    lines = proc.stdout.strip().splitlines()
    return proc.returncode == 0 and bool(lines) and lines[-1] == "END"


def report(proc: subprocess.CompletedProcess[str]) -> str:
    return f"exit {proc.returncode}\n--- stdout ---\n{proc.stdout[-3000:]}\n--- stderr ---\n{proc.stderr[-3000:]}"


def observed(order: str, tmp_path: Path, qml_dir: Path | None = None) -> dict[str, Any]:
    """Run one order; the process must survive, and its observations come back."""
    proc = run_order(order, tmp_path, qml_dir)
    assert survived(proc), f"{order}: the process did not survive\n{report(proc)}"
    results = [line for line in proc.stdout.splitlines() if line.startswith("RESULT ")]
    assert len(results) == 1, report(proc)
    answer: dict[str, Any] = json.loads(results[0][len("RESULT "):])
    return answer


def is_frame(answer: Any) -> bool:
    return isinstance(answer, dict) and "red" in answer


def assert_scene(answer: Any, what: str) -> None:
    """The answer is a frame, and the frame is the scene: the red box, at its size."""
    assert is_frame(answer), f"{what}: expected a frame, got {answer!r}"
    expected = child.BOX_PIXELS * answer["dpr"] ** 2
    assert abs(answer["red"] - expected) <= 0.04 * expected, (what, answer, expected)


def assert_refused(answer: Any, what: str, *needles: str, error: str = "HostStateError") -> None:
    assert isinstance(answer, str) and answer.startswith(f"{error}: "), (
        f"{what}: expected {error}, got {answer!r}")
    for needle in needles:
        assert needle in answer, (what, needle, answer)


def assert_grab_fits_state(state: str, answer: Any, what: str) -> None:
    """THE rule: what the host says its state is decides what a grab must answer."""
    if EXPECTED_GRAB[state] == "frame":
        assert_scene(answer, f"{what} (state {state})")
    else:
        assert_refused(answer, f"{what} (state {state})")


def assert_hit(answer: Any, what: str) -> None:
    assert isinstance(answer, dict) and answer.get("hit") == "w", (what, answer)


# ── the orders ───────────────────────────────────────────────────────────────


def test_show_then_grab_at_once_is_refused_and_works_after_waiting(tmp_path: Path) -> None:
    """P1-1, the first killer: ``show()`` then ``grab()`` before the first expose killed
    the process (5 of 5). The host is ``STARTING`` there and refuses; ``wait_ready()``
    brings it to ``LIVE``, and then the frame is the scene."""
    out = observed("show_grab", tmp_path)
    assert out["state_after_show"] in ("starting", "live")
    assert_grab_fits_state(out["state_after_show"], out["grab_at_once"], "grab right after show()")
    if WINDOWS:  # measured: a shown window has no scene graph before the first event-loop pass
        assert out["state_after_show"] == "starting"
        assert_refused(out["grab_at_once"], "grab while starting", "wait_ready()")
        assert_refused(out["pick_at_once"], "pick while starting", "wait_ready()")
    assert out["ready"] is True and out["state_ready"] == "live"
    assert_scene(out["grab_ready"], "grab after wait_ready()")
    assert_hit(out["pick_ready"], "pick after wait_ready()")
    assert_scene(out["grab_later"], "a later grab")


def test_the_offscreen_frame_then_show_is_refused(tmp_path: Path) -> None:
    """P1-1, the second killer: one hidden grab, then ``show()`` — what the old error
    message RECOMMENDED — killed the process. A spent host refuses to be shown or
    embedded, and its message sends the caller to a new host."""
    out = observed("grab_show", tmp_path)
    assert out["state_new"] == "new"
    assert_scene(out["offscreen"], "the one offscreen frame")
    assert out["state_after"] == "spent" and out["state_end"] == "spent"
    assert_refused(out["show"], "show() on a spent host", "Build a new host")
    assert "show() it" not in out["show"], "the message must not send the caller to show()"
    assert_refused(out["container"], "container() on a spent host", "Build a new host")
    assert_refused(out["second_grab"], "a second offscreen grab", "Build a new host")
    assert out["wait_ready"] is False, "nothing is on its way: no waiting, no hang"
    assert out["hide"] is None  # hiding what is not shown is harmless


def test_show_wait_grab_is_the_normal_path(tmp_path: Path) -> None:
    out = observed("show_wait_grab", tmp_path)
    assert out["ready"] is True and out["state"] == "live"
    assert_scene(out["grab"], "grab")
    assert_hit(out["pick"], "pick")
    assert_scene(out["second_grab"], "second grab")
    assert out["renders_3d"] is True
    assert out["api"] in ("Direct3D11", "OpenGL", "Vulkan", "Metal", "Direct3D12")


def test_a_hidden_host_refuses_a_frame(tmp_path: Path) -> None:
    out = observed("hide_grab", tmp_path)
    assert out["ready"] is True
    assert_scene(out["live_grab"], "grab while live")
    assert out["state_hidden"] == "hidden"
    assert_refused(out["grab_hidden"], "grab while hidden", "not exposed")
    assert_refused(out["pick_hidden"], "pick while hidden", "not exposed")
    assert out["wait_hidden"] is False, "a hidden host is not on its way to live"


def test_show_hide_show_grab_brings_the_scene_back(tmp_path: Path) -> None:
    out = observed("show_hide_show_grab", tmp_path)
    assert out["ready"] is True
    assert_scene(out["first"], "first grab")
    assert out["state_reshown"] in ("hidden", "live")
    assert_grab_fits_state(out["state_reshown"], out["grab_at_once"], "grab right after re-show")
    if WINDOWS:  # measured: re-shown, the window is not exposed before the next event-loop pass
        assert out["state_reshown"] == "hidden"
    assert out["ready_again"] is True and out["state"] == "live"
    assert_scene(out["grab"], "grab after the re-show")
    assert_hit(out["pick"], "pick after the re-show")


def test_shown_and_hidden_before_any_event_is_still_new(tmp_path: Path) -> None:
    """The edge between the states: ``show()`` + ``hide()`` with no event in between
    never gave the window a scene graph, so the offscreen frame is still to be had —
    and, taken, the host is spent like any other."""
    out = observed("show_hide_before_expose_grab", tmp_path)
    assert_grab_fits_state(out["state"], out["offscreen"], "grab after show()+hide()")
    if WINDOWS:
        assert out["state"] == "new"
    if out["state"] == "new":
        assert out["state_after"] == "spent"
        assert_refused(out["show"], "show() after the offscreen frame", "Build a new host")


def test_a_never_shown_host_renders_exactly_one_frame(tmp_path: Path) -> None:
    """Measured: the second hidden grab is the bare background (mean abs difference 161
    on the probe frame) — so it is refused, and so is every one after it."""
    out = observed("grab_twice_hidden", tmp_path)
    assert_scene(out["first"], "the offscreen frame")
    assert_refused(out["second"], "the second hidden grab", "one offscreen frame")
    assert_refused(out["third"], "the third hidden grab", "one offscreen frame")


def _assert_closed_host_contract(calls: dict[str, Any], what: str) -> None:
    for name, answer in calls.items():
        if name in RELEASES:
            assert answer is None, f"{what}: {name}() must be a silent no-op, got {answer!r}"
        else:
            assert_refused(answer, f"{what}: {name}()", "closed")


def test_the_closed_host_table_covers_every_public_call() -> None:
    """The two tests below are only as strong as the table they loop over."""
    from open_garden_planner.ui.view3d.quick3d.host import Quick3DHost

    child._register_calls()  # noqa: SLF001 - the child's own table
    public = {name for name in dir(Quick3DHost)
              if not name.startswith("_") and callable(getattr(Quick3DHost, name))}
    # answered on a closed host, asserted separately: is_visible (False), engine_errors
    assert set(child.HOST_CALLS) == public - {"is_visible", "engine_errors"}


def test_close_then_anything(tmp_path: Path) -> None:
    """P1-3's rule from the host's side: after ``close()`` the three releases do nothing,
    the state answers, and EVERY other call raises ``HostStateError`` — at once, and
    still after the event loop has delivered the deferred delete."""
    out = observed("close_anything", tmp_path)
    assert out["ready"] is True
    assert (out["state"], out["is_closed"], out["is_visible"]) == ("closed", True, False)
    _assert_closed_host_contract(out["calls"], "right after close()")
    _assert_closed_host_contract(out["calls_after_events"], "after the deferred delete")


def test_parent_destroyed_then_anything(tmp_path: Path) -> None:
    """Qt destroys the view with the widget that parents its container (what closing the
    main window does). The host notices by asking Qt — no flag was set — and behaves
    exactly like a closed one. Before: ``TypeError`` / ``RuntimeError`` from sip."""
    out = observed("parent_destroyed_anything", tmp_path)
    assert out["ready"] is True
    assert (out["state"], out["is_closed"], out["is_visible"]) == ("closed", True, False)
    _assert_closed_host_contract(out["calls"], "after the parent was destroyed")


def test_embedded_in_a_splitter_the_parent_shows_and_hides(tmp_path: Path) -> None:
    """L1.3's first open: created with the app up, embedded beside a widget, shown, and
    asked for a frame at once (4 of 4 deaths before). Then the PARENT hides and shows
    the container and the window — ``host.hide()`` / ``show()`` are never called, so
    no Python flag could know; the state is Qt's."""
    out = observed("embedded_splitter", tmp_path)
    assert out["state_after_show"] in ("starting", "hidden", "live")
    assert_grab_fits_state(out["state_after_show"], out["grab_at_once"], "grab right after show()")
    if WINDOWS:
        assert out["state_after_show"] == "starting"
    assert out["ready"] is True and out["state"] == "live"
    assert_scene(out["grab"], "embedded grab")
    assert_hit(out["pick"], "embedded pick")
    assert out["state_parent_hid"] == "hidden"
    assert_refused(out["grab_parent_hid"], "grab while the parent hid the container")
    at_once = out["grab_parent_showed_at_once"]
    assert is_frame(at_once) or str(at_once).startswith("HostStateError: "), at_once
    if is_frame(at_once):
        assert_scene(at_once, "grab right after the parent showed the container")
    assert out["ready_again"] is True
    assert_scene(out["grab_again"], "grab after the parent showed the container")
    assert out["state_window_hidden"] == "hidden"
    assert_refused(out["grab_window_hidden"], "grab while the window is hidden")
    assert out["ready_window"] is True
    assert_scene(out["grab_window"], "grab after the window came back")


def test_an_embedded_host_has_no_offscreen_frame(tmp_path: Path) -> None:
    """Embedded but not shown yet: an offscreen frame now would kill the process the
    moment the parent shows the container (2 of 2). The host has none to give — and
    is alive and correct once the parent did show it."""
    out = observed("embedded_never_shown_grab", tmp_path)
    assert out["state"] == "new"
    assert_refused(out["grab_before_parent"], "offscreen grab of an embedded host", "embedded")
    assert out["wait_before_parent"] is False
    assert out["state_parent_shown"] in ("starting", "hidden", "live")
    assert_grab_fits_state(out["state_parent_shown"], out["grab_at_once"],
                           "grab right after the parent showed the window")
    assert out["ready"] is True
    assert_scene(out["grab"], "grab once live")
    assert_hit(out["pick"], "pick once live")


def test_a_shown_host_can_be_embedded_afterwards(tmp_path: Path) -> None:
    """Only a SPENT host cannot be embedded (measured: embedding a view that is live,
    starting or hidden is fine) — so ``container()`` is not limited to new hosts."""
    out = observed("embed_after_show", tmp_path)
    assert out["ready_standalone"] is True
    assert_scene(out["standalone"], "standalone grab")
    assert out["ready"] is True
    assert_scene(out["grab"], "grab after embedding")
    assert_hit(out["pick"], "pick after embedding")


@pytest.mark.skipif(not WINDOWS, reason="minimising needs a window manager; measured on Windows")
def test_a_minimised_host_refuses_a_frame(tmp_path: Path) -> None:
    """Minimised: Qt still calls the window visible, and it is not exposed. A guard on
    ``isVisible()`` would grab; the state machine reads ``isExposed()``."""
    out = observed("minimised_grab", tmp_path)
    assert out["ready"] is True
    assert_scene(out["live"], "grab while live")
    assert out["state_minimised"] == "hidden" and out["is_visible"] is True
    assert_refused(out["grab_minimised"], "grab while minimised", "not exposed")
    assert_refused(out["pick_minimised"], "pick while minimised", "not exposed")
    assert out["ready_again"] is True
    assert_scene(out["grab_restored"], "grab after restoring")


# ── P1-3: the sink's teardown in every order ─────────────────────────────────


@pytest.mark.parametrize("order", ["parent_destroyed_then_clear",
                                   "parent_destroyed_close_then_clear", "close_then_clear"])
def test_clear_is_clean_whenever_the_host_died(clear_orders: dict[str, Any], order: str) -> None:
    """Before: ``TypeError``, ``ValueError``, and ``EngineError`` with the books left
    dirty — one per order. Now ``clear()`` returns in each, empties the books, is
    idempotent, and ``SceneSync.reset()`` works after it."""
    row = clear_orders[order]
    assert row["first_clear"] is None, row
    assert row["items"] == [] and row["in_transaction"] is False
    assert row["second_clear"] is None and row["third_clear"] is None, "clear() is idempotent"
    assert row["host_close"] is None and row["reset"] is None
    assert row["state"] == "closed"


def test_a_fresh_host_renders_after_the_old_one_died(clear_orders: dict[str, Any]) -> None:
    """The recovery: the SAME sink and sync, cleared and reset on a dead host, pointed
    at a fresh host (``Quick3DSink.attach``) and applied again — the scene is back."""
    fresh = clear_orders["fresh"]
    assert fresh["ready"] is True
    assert_scene(fresh["grab"], "the fresh host's frame")
    assert_hit(fresh["pick"], "the fresh host's pick")
    assert fresh["parts"] == 1 and fresh["items"] == ["w"]


@pytest.fixture(scope="module")
def clear_orders(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return observed("sink_clear_orders", tmp_path_factory.mktemp("clear_orders"))


# ── P2-13: errors in a live view ─────────────────────────────────────────────


def test_a_live_view_with_the_shipped_shaders_reports_nothing(tmp_path: Path) -> None:
    out = observed("watch", tmp_path)
    assert_refused(out["errors_without_a_watch"], "engine_errors() before a watch", "watch_messages()")
    assert out["watch_is_idempotent"] is True
    assert out["ready"] is True
    assert_scene(out["grab"], "the watched frame")
    assert out["engine_errors"] == []
    assert out["counts"]["error"] == 0 and out["counts"]["warning"] == 0, out["counts"]
    assert [row for row in out["logged"] if row["level"] in ("ERROR", "WARNING")] == []
    assert out["errors_after_close"] == 0


def test_a_broken_shader_in_a_live_view_reaches_the_log_and_the_owner(tmp_path: Path) -> None:
    """Positive control. In a SHOWN view a shader that does not compile raises nothing:
    the frame renders (without the leaf) and Qt prints a warning to a stderr a frozen
    exe does not have. With ``watch_messages()`` it is an ERROR record in the app's
    log — written from Qt's render thread where the platform has one — and
    ``engine_errors()`` tells the owner on the GUI thread."""
    qml = tmp_path / "qml"
    shutil.copytree(QML_SOURCE, qml)
    shader = qml / "foliage.frag"
    text = shader.read_text(encoding="utf-8")
    assert "vColor.rgb * uTint" in text, "the control no longer finds its line in foliage.frag"
    shader.write_text(text.replace("vColor.rgb * uTint", "vColor.rgb * uTintMissing"),
                      encoding="utf-8")
    out = observed("watch", tmp_path, qml)
    assert out["ready"] is True
    assert out["engine_errors"], "the owner must learn of the broken shader"
    assert any("shader" in line.lower() for line in out["engine_errors"])
    errors = [row for row in out["logged"] if row["level"] == "ERROR"]
    assert errors and all(row["message"].startswith("Qt: ") for row in errors), out["logged"]
    assert out["errors_after_close"] == len(out["engine_errors"]), "readable after close()"
    assert is_frame(out["grab"]) and out["grab"]["red"] == 0, "the leaf is NOT drawn — silently"


# ── the controls: what the guard forbids must really kill ────────────────────


@pytest.mark.skipif(not WINDOWS, reason="measured on Windows (Direct3D 11, GPU and WARP) only")
@pytest.mark.parametrize("order", ["unguarded_show_grab", "unguarded_grab_show"])
def test_without_the_guard_the_process_dies(tmp_path: Path, order: str) -> None:
    """Positive control for the whole matrix. The same two call orders, on the raw
    ``QQuickView`` behind the host's back: the grab RETURNS — a correct image, no
    exception — and the process is dead a moment later (abort, access violation or
    heap corruption; 3 of 3 for each, 14 of 14 over both measuring sessions). If the
    crash ever stopped happening, the guard would be folklore and this test says so.

    Up to three attempts: the claim is "it kills", and a single lucky survival of an
    undefined-behaviour crash must not turn the control red.
    """
    deaths, attempts = 0, []
    for attempt in range(3):
        proc = run_order(order, tmp_path / f"attempt{attempt}")
        attempts.append(proc)
        grabbed = [line for line in proc.stdout.splitlines() if line.startswith("GRABBED ")]
        assert grabbed, f"the control never reached its grab\n{report(proc)}"
        frame = json.loads(grabbed[0][len("GRABBED "):])["grab"]
        assert frame["red"] > 0.9 * child.BOX_PIXELS, "the fatal grab returns a GOOD image"
        if not survived(proc):
            deaths += 1
            break
    assert deaths == 1, (f"{order}: the unguarded call order no longer kills the process "
                         f"({len(attempts)} attempts)\n{report(attempts[-1])}")
