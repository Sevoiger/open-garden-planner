"""The host's lifecycle as a matrix of call orders (Phase 17 L1.2, review rounds 1 and 2).

Render tier (``OGP_RENDER3D=1``). Each test starts ``quick3d_lifecycle_child.py``
with ONE order in a process of its own and judges what the process reported —
because the failures the matrix exists for are not exceptions: a Qt Quick window
grabbed at the wrong moment hands back a perfectly good image and the process
dies in the next pass of the event loop; a collapsed pane hands back a blank
image and a bogus pick; a Python Qt message handler beside a shown window
deadlocks the process.

THE JUDGE DOES NOT ASK THE HOST. Round 1 judged every frame by the state the host
reported for it — and was therefore blind to a host that reported the wrong state
(``live`` for a collapsed pane, ``new`` for a window xcb had already created).
``TRUTH`` below says, per order and per step, what a truthful host must answer:

- ``FRAME``   the caller did everything right (``wait_ready()`` said yes): the
              answer must be a frame of the window's size that contains the red
              probe box at the predicted pixels, a pick that hits it, and a
              projection onto the view's centre;
- ``REFUSED`` no truthful frame can be had there — nothing shows, or taking one is
              fatal: ``grab``, ``pick`` and ``project`` must each raise
              ``HostStateError``;
- ``EITHER``  it depends on how fast the platform delivers an event (is a window
              exposed right after ``show()``?): a correct frame or a refusal —
              NEVER a frame without the box. What Windows was measured to do is
              pinned on Windows (``ON_WINDOWS``).
- ``ANY``     the same without a pin: Qt delivers some resizes synchronously and
              some with the next pass of the event loop, per transition.

The pixels are predicted here: the box is 100 x 100 cm, the view is centred on it
at 0.5 px per cm, so it is a 50 px square in the middle of the frame, whatever the
frame's size (clipped by a frame narrower than that). And the SIZE a frame must
have is the window's, asked of Qt by the child before the grab — not the host's
idea of it (review round 3: ``wait_ready()`` said yes and the frame still had the
size the window had before it went full screen).

Controls do on the raw ``QQuickView`` what the host forbids and must fail the way
that was measured: die, hang, or return a frame the judge finds wrong. A control
is asserted on the platforms it was measured on and reported (``xfail``, not
strict) on any other.

Measured 2026-10-11, Qt 6.11: Direct3D 11 on an RTX 4070 and on WARP; xcb + OpenGL
on llvmpipe (Mesa 25.2, Ubuntu 24.04 under xvfb — the CI job's stack, in a
container). The four killing orders die 5 of 5 on xcb; three of them 3 of 3 on
Direct3D 11, where ``show(); hide(); grab()`` survives. The message-handler
deadlock is 5 of 5 on both.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.integration import quick3d_lifecycle_child as child
from tests.integration.quick3d_render_support import (
    QML_SOURCE,
    REPO,
    isolated_env,
    render_env,
    render_tier,
)

pytestmark = render_tier

CHILD = Path(child.__file__).resolve()
WINDOWS = sys.platform == "win32"
LINUX = sys.platform.startswith("linux")
FRAME, REFUSED, EITHER, ANY = "frame", "refused", "either", "any"
#: The three releases: legal on a closed host, where they do nothing.
RELEASES = ("destroy_part", "clear", "close")
#: Asked of a closed host they still answer (asserted where they are used).
ANSWERS_WHEN_CLOSED = {"is_visible", "health"}

# What a truthful host must answer — per order, per step (module docstring).
TRUTH: dict[str, dict[str, str]] = {
    "show_grab": {"at_once": EITHER, "after_wait": FRAME, "later": FRAME},
    "show_wait_grab": {"live": FRAME, "later": FRAME},
    "hide_grab": {"live": FRAME, "hidden_at_once": REFUSED, "hidden": REFUSED},
    "show_hide_show_grab": {"first": FRAME, "reshown_at_once": EITHER, "reshown": FRAME},
    # the xcb killer: no scene graph, not visible — and a platform window. No frame.
    "show_hide_before_expose_grab": {"hidden_at_once": REFUSED, "hidden": REFUSED, "shown": FRAME},
    "grab_twice_hidden": {"second": REFUSED, "third": REFUSED},
    "user_closes_window": {"live": FRAME, "closed_by_user_at_once": REFUSED,
                           "closed_by_user": REFUSED, "shown_again": FRAME},
    "embedded_splitter": {
        "at_once": EITHER, "live": FRAME, "parent_hid_at_once": REFUSED, "parent_hid": REFUSED,
        "parent_showed_at_once": EITHER, "parent_showed": FRAME,
        "window_hidden_at_once": REFUSED, "window_hidden": REFUSED,
        "window_shown_at_once": EITHER, "window_shown": FRAME},
    # a pane of no width shows nothing; one of ONE pixel renders no 3D (measured); after
    # every change of size the scene lags the window by one pass of the event loop
    "collapsed_pane": {
        "open": FRAME, "collapsed_at_once": REFUSED, "collapsed": REFUSED,
        "reopened_at_once": EITHER, "reopened": FRAME,
        "one_pixel_at_once": EITHER, "one_pixel": REFUSED,
        "narrow_at_once": EITHER, "narrow": FRAME, "narrow_later": FRAME},
    "minimised_window": {"open": FRAME, "minimised": REFUSED, "restored": FRAME,
                         "alone_minimised": REFUSED, "alone_restored": FRAME},
    "embedded_never_shown_grab": {"before_parent": REFUSED, "parent_shown_at_once": EITHER,
                                  "live": FRAME},
    "embed_after_show": {"standalone": FRAME, "embedded_at_once": EITHER, "embedded": FRAME},
    "health": {"shown": FRAME},
    "render_loop": {"live": FRAME},
}
#: Full screen, normal, maximised, full screen from maximised, normal (review round 3,
#: P1-1): at once anything truthful, and after ``wait_ready()`` a frame at the NEW size.
WINDOW_STATES = ("fullscreen", "normal", "maximised", "fullscreen_from_maximised", "normal_again")
for _order in ("window_states_standalone", "window_states_embedded"):
    TRUTH[_order] = {"open": FRAME}
    for _step in WINDOW_STATES:
        TRUTH[_order].update({f"{_step}_at_once": ANY, _step: FRAME, f"{_step}_later": FRAME})
#: What Windows was measured to do at the EITHER steps: no window is exposed, and no
#: scene has caught up, before the event loop ran.
ON_WINDOWS = REFUSED
#: ``wait_ready()`` answers per order: True where the host was on its way, False where
#: nothing was (at once — the seconds are bounded in the tests that measure them).
READY: dict[str, dict[str, bool]] = {
    "show_grab": {"ready": True},
    "show_wait_grab": {"ready": True},
    "hide_grab": {"ready": True, "wait_hidden": False},
    "show_hide_show_grab": {"ready": True, "ready_again": True},
    "show_hide_before_expose_grab": {"wait": False, "ready": True},
    "user_closes_window": {"ready": True, "ready_again": True},
    "embedded_splitter": {"ready": True, "ready_again": True, "ready_window": True},
    "collapsed_pane": {"ready": True, "wait_collapsed": False, "ready_reopened": True,
                       "ready_one_pixel": False, "ready_narrow": True},
    "minimised_window": {"ready": True, "wait_minimised": False, "ready_restored": True,
                         "alone_ready": True, "alone_ready_restored": True},
    "embedded_never_shown_grab": {"wait_before_parent": False, "ready": True},
    "embed_after_show": {"ready_standalone": True, "ready": True},
    "health": {"ready": True},
    "render_loop": {"ready": True},
    "window_states_standalone": {"ready": True, **{f"ready_{step}": True for step in WINDOW_STATES}},
    "window_states_embedded": {"ready": True, **{f"ready_{step}": True for step in WINDOW_STATES}},
}


def run_order(order: str, tmp_path: Path, *arguments: str, timeout: float = 170.0,
              **environment: str) -> subprocess.CompletedProcess[str]:
    """One order in a child with a real Qt platform and nothing of the developer's data.
    A child that is still running when ``timeout`` is up is killed, with its children;
    ``returncode`` is then None-like (``"timeout"`` in ``args``)."""
    command = [sys.executable, str(CHILD), order, *arguments]
    env = render_env(tmp_path, **environment)
    for name in ("QT_MESSAGE_PATTERN", "QT_LOGGING_RULES"):  # the matrix reads no Qt messages
        if name not in environment:
            env.pop(name, None)
    process = subprocess.Popen(  # noqa: S603 - fixed argv, our own code
        command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace", cwd=REPO)
    try:
        out, err = process.communicate(timeout=timeout)
        return subprocess.CompletedProcess(command, process.returncode, out, err)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        out, err = process.communicate(timeout=30)
        return subprocess.CompletedProcess([*command, "timeout"], -999, out, err)


def _kill_tree(process: subprocess.Popen[str]) -> None:
    """End a child that will not end by itself — and what it started (on Windows the
    venv's ``python.exe`` is a launcher whose child is the interpreter)."""
    if WINDOWS:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],  # noqa: S603, S607
                       capture_output=True, check=False)
    else:
        process.kill()


def timed_out(proc: subprocess.CompletedProcess[str]) -> bool:
    return proc.args[-1] == "timeout"


def survived(proc: subprocess.CompletedProcess[str]) -> bool:
    lines = proc.stdout.strip().splitlines()
    return proc.returncode == 0 and bool(lines) and lines[-1] == "END"


def report(proc: subprocess.CompletedProcess[str]) -> str:
    return (f"exit {proc.returncode}{' (killed at the timeout)' if timed_out(proc) else ''}\n"
            f"--- stdout ---\n{proc.stdout[-3000:]}\n--- stderr ---\n{proc.stderr[-3000:]}")


def observed(order: str, tmp_path: Path, *arguments: str, **environment: str) -> dict[str, Any]:
    """Run one order; the process must survive, and its observations come back."""
    proc = run_order(order, tmp_path, *arguments, **environment)
    assert survived(proc), f"{order}: the process did not survive\n{report(proc)}"
    results = [line for line in proc.stdout.splitlines() if line.startswith("RESULT ")]
    assert len(results) == 1, report(proc)
    answer: dict[str, Any] = json.loads(results[0][len("RESULT "):])
    return answer


# ── the judge's own idea of a correct frame ──────────────────────────────────


def frame_problem(frame: Any, window: list[float] | None = None) -> str | None:
    """None when ``frame`` shows the probe where it must be — else what is wrong with it.

    ``window``: the logical size the frame must have — the window's, asked of Qt
    before the grab. Predicted here, from the probe's definition alone: a red
    square of ``BOX_SIDE_PX`` logical pixels centred in the frame, clipped by a
    frame that is narrower than the square.
    """
    if not (isinstance(frame, dict) and "red" in frame):
        return f"not a frame: {frame!r}"
    width, height, ratio = frame["w"], frame["h"], frame["dpr"]
    if window is not None and (abs(width - window[0] * ratio) > 2 or abs(height - window[1] * ratio) > 2):
        return f"a {width} x {height} px frame for a {window[0]:g} x {window[1]:g} window"
    if frame["box"] is None:
        return "no red pixel: the box is not in the frame"
    side = child.BOX_SIDE_PX * ratio
    predicted = [max(0.0, width / 2 - side / 2), max(0.0, height / 2 - side / 2),
                 min(float(width), width / 2 + side / 2), min(float(height), height / 2 + side / 2)]
    if any(abs(got - want) > 1.5 for got, want in zip(frame["box"], predicted, strict=True)):
        return f"the box is at {frame['box']}, predicted {predicted}"
    area = (predicted[2] - predicted[0]) * (predicted[3] - predicted[1])
    if abs(frame["red"] - area) > max(0.06 * area, 4.0 * ratio):
        return f"{frame['red']} red pixels, predicted {area:g}"
    return None


def is_refusal(answer: Any) -> bool:
    return isinstance(answer, str) and answer.startswith("HostStateError: ")


def judge_step(order: str, step: str, look: dict[str, Any], expected: str) -> None:
    """One step of one order against ``TRUTH`` — by the answers, not by the state, and
    against the size QT gave for the window, not the host's."""
    where = f"{order} / {step}"
    grab, pick, project, window = look["grab"], look["pick"], look["project"], look["window"]
    if expected == EITHER and WINDOWS:
        expected = ON_WINDOWS
    if expected in (EITHER, ANY):  # whichever it is, it must be a truthful one
        expected = REFUSED if is_refusal(grab) else FRAME
    if expected == FRAME:
        assert isinstance(window, list), f"{where}: Qt gave no size for the window"
        problem = frame_problem(grab, window)
        assert problem is None, f"{where}: must be a frame of the scene — {problem}"
        assert isinstance(pick, dict) and pick.get("hit") == "w", f"{where}: pick {pick!r}"
        assert pick["point"] == pytest.approx([0.0, 0.0, 20.0], abs=0.6), f"{where}: {pick!r}"
        assert isinstance(project, list), f"{where}: project {project!r}"
        assert project == pytest.approx([window[0] / 2, window[1] / 2], abs=0.6), f"{where}: {project!r}"
        assert look["state"] == "live", f"{where}: a host that answers must say it is live"
    else:
        for name, answer in (("grab", grab), ("pick", pick), ("project", project)):
            assert is_refusal(answer), (
                f"{where}: {name}() must raise HostStateError, got {str(answer)[:160]!r}")
        assert look["state"] != "live", f"{where}: a host that refuses must not say it is live"


def judge(order: str, out: dict[str, Any]) -> None:
    for step, expected in TRUTH[order].items():
        judge_step(order, step, out[step], expected)
    for key, expected_answer in READY.get(order, {}).items():
        assert out[key] is expected_answer, f"{order}: wait_ready() at {key!r} said {out[key]!r}"


# ── the table itself ─────────────────────────────────────────────────────────


def test_the_truth_table_covers_every_guarded_order() -> None:
    """An order the table does not know would run unjudged (``special``: the orders with
    a test of their own below)."""
    special = {"grab_show", "close_anything", "parent_destroyed_anything", "wait_while_it_goes",
               "sink_clear_orders", "random_walk"}
    guarded = {name for name in child.ORDERS
               if not name.startswith(("unguarded_", "deadlock_"))}
    assert guarded == set(TRUTH) | special
    assert set(READY) <= set(TRUTH)


def test_the_judge_knows_a_wrong_frame_when_it_sees_one() -> None:
    """The judge's own controls, no engine involved: a blank frame, a frame of the old
    size, a box in the wrong place, a box of the wrong size — each must be named."""
    good = {"w": 447, "h": 500, "dpr": 1.0, "red": 2550, "box": [198, 225, 249, 275]}
    assert frame_problem(good, [447.0, 500.0]) is None
    assert frame_problem({**good, "w": 670, "h": 750, "dpr": 1.5, "red": 5625,
                          "box": [297, 337, 373, 413]}, [447.0, 500.0]) is None
    assert "no red pixel" in str(frame_problem({**good, "red": 0, "box": None}))
    assert "window" in str(frame_problem(good, [0.0, 500.0]))            # the collapsed pane's lie
    assert "predicted" in str(frame_problem({**good, "box": [100, 225, 151, 275]}))
    assert "red pixels" in str(frame_problem({**good, "red": 900}))
    assert "not a frame" in str(frame_problem("HostStateError: no"))
    # the frame the F11 path handed out (review round 3): right picture, the OLD size
    assert "for a 2557 x 1440 window" in str(frame_problem(good, [2557, 1440]))
    # a pane narrower than the box: the box fills the frame's width, and is clipped by it
    narrow = {"w": 24, "h": 500, "dpr": 1.0, "red": 1200, "box": [0, 225, 24, 275]}
    assert frame_problem(narrow, [24, 500]) is None
    assert "predicted" in str(frame_problem({**narrow, "box": [3, 225, 24, 275]}, [24, 500]))
    assert frame_problem({"w": 2, "h": 500, "dpr": 1.0, "red": 100, "box": [0, 225, 2, 275]}, [2, 500]) is None


# ── the orders ───────────────────────────────────────────────────────────────

PORTABLE = ["show_grab", "show_wait_grab", "hide_grab", "show_hide_show_grab",
            "show_hide_before_expose_grab", "grab_twice_hidden", "embedded_splitter",
            "collapsed_pane", "embedded_never_shown_grab", "embed_after_show"]


@pytest.mark.parametrize("order", PORTABLE)
def test_a_call_order_is_answered_truthfully(tmp_path: Path, order: str) -> None:
    """Every step of the order against ``TRUTH`` — and the process survives.

    What the orders are, in the words of the defects behind them:
    ``show_grab`` — the first killer (5 of 5 before the guard); ``grab_twice_hidden`` —
    the second offscreen frame is the bare background; ``show_hide_before_expose_grab``
    — the xcb killer, the red CI job of review round 2; ``embedded_splitter`` — L1.3's
    first open, with the PARENT hiding and showing the container and the window;
    ``collapsed_pane`` — the pane dragged shut, reopened, squeezed to one pixel and
    opened again (review round 2, P1-B); ``embedded_never_shown_grab`` — an embedded
    host has no offscreen frame; ``embed_after_show`` — only a spent host cannot be
    embedded.
    """
    out = observed(order, tmp_path)
    judge(order, out)
    if order == "grab_twice_hidden":
        assert frame_problem(out["first"], [float(child.SIZE[0]), float(child.SIZE[1])]) is None


def test_the_offscreen_frame_then_show_is_refused(tmp_path: Path) -> None:
    """The second killer: one hidden grab, then ``show()`` — what the first error
    message RECOMMENDED — killed the process. A spent host refuses to be shown or
    embedded, and its message sends the caller to a new host."""
    out = observed("grab_show", tmp_path)
    assert out["state_new"] == "new"
    assert frame_problem(out["offscreen"], [float(child.SIZE[0]), float(child.SIZE[1])]) is None
    assert out["state_after"] == "spent" and out["state_end"] == "spent"
    for call in ("show", "container"):
        assert is_refusal(out[call]) and "Build a new host" in out[call], out[call]
    assert "show() it" not in out["show"], "the message must not send the caller to show()"
    judge_step("grab_show", "second", out["second"], REFUSED)
    assert out["wait_ready"] is False, "nothing is on its way: no waiting, no hang"
    assert out["hide"] is None  # hiding what is not shown is harmless


def test_a_window_the_user_closed_is_hidden_not_new(tmp_path: Path) -> None:
    """The X button of a standalone window destroys its platform window and keeps the
    scene graph. Raw, a grab of it is an access violation (the control below); a host
    that read "no platform window" as "new" would take the offscreen frame."""
    out = observed("user_closes_window", tmp_path)
    judge("user_closes_window", out)
    assert out["closed_by_user"]["state"] == "hidden"
    assert out["show"] is None


@pytest.mark.skipif(not WINDOWS, reason="minimising needs a window manager; measured on Windows")
def test_a_minimised_window_refuses_and_does_not_keep_the_caller_waiting(tmp_path: Path) -> None:
    """Minimised — the main window around an embedded host, and a standalone host: Qt
    still calls a minimised window visible. No frame, and ``wait_ready()`` says no AT
    ONCE (it used to pump to its timeout)."""
    out = observed("minimised_window", tmp_path)
    if not (out["platform_minimised"] and out["platform_minimised_alone"]):
        # asked of Qt, not of the host — a session without a window manager (a CI runner)
        pytest.skip("the window system did not minimise the window")
    judge("minimised_window", out)
    assert out["wait_minimised_s"] < 1.0, "wait_ready() must not wait for a minimised window"


@pytest.mark.parametrize("order", ["window_states_standalone", "window_states_embedded"])
def test_after_wait_ready_the_frame_has_the_windows_new_size(tmp_path: Path, order: str) -> None:
    """Review round 3, P1-1 — the app's own F11 path (``application.py`` calls
    ``showFullScreen()`` on the very window the workspace embeds into).

    Qt delivers THAT resize synchronously: window and scene agree at once, and
    ``wait_ready()`` said True in 0 ms — while a grab still came back in the size the
    window had before, because Qt resizes the render target with the next expose
    (20 of 20; the host then raised ``EngineError``). Now: at once the host answers
    truthfully or refuses (``ANY``), and after ``wait_ready()`` said yes the frame
    has the size QT gives for the window — full screen, normal, maximised, full
    screen from maximised and back — with the probe where it belongs.

    Under xvfb there is no window manager and the window keeps its size; the same
    table holds (a frame at the window's size), it just proves less."""
    out = observed(order, tmp_path)
    judge(order, out)
    assert out["stale_frames"] == 0, "the host handed Qt a grab that came back in another size"
    for step in WINDOW_STATES:
        assert out[f"wait_{step}_s"] < 6.0, (step, out[f"wait_{step}_s"])
    if WINDOWS:
        small, full = out["open"]["window"], out["fullscreen"]["window"]
        assert full[0] > small[0] and full[1] > small[1], "the window did not go full screen"
        assert out["maximised"]["window"][0] > small[0]
        assert out["normal"]["window"] == small and out["normal_again"]["window"] == small
        # the very moment of the finding, pinned: Qt has resized window AND scene, the
        # render target is the old one — a truthful host refuses, and says which fact
        for step in ("fullscreen_at_once", "fullscreen_from_maximised_at_once"):
            at_once = out[step]
            assert at_once["window"] == full and at_once["view"] == [float(full[0]), float(full[1])]
            assert is_refusal(at_once["grab"]) and "render target has not caught up" in at_once["grab"]


def _walk_problems(out: dict[str, Any]) -> tuple[list[str], dict[str, int]]:
    """Judge a random walk's records — every answer against what QT said about the window
    at that moment, never against the state the host reported."""
    problems: list[str] = []
    counts = {"frames": 0, "refusals": 0, "ready_true": 0, "ready_false": 0}
    for index, record in enumerate(out["records"]):
        said, look, mode = record["qt"], record["look"], record["mode"]
        where = f"[{index}] {record['what']} / {mode} window={said['window']} state={look['state']}"
        grab, pick, project = look["grab"], look["pick"], look["project"]
        if isinstance(grab, dict):
            counts["frames"] += 1
            problem = frame_problem(grab, said["window"])
            if problem is not None:
                problems.append(f"{where}: {problem}")
            if not said["visible"] or said["minimised"]:
                problems.append(f"{where}: a frame from a window Qt calls invisible or minimised")
        elif is_refusal(grab):
            counts["refusals"] += 1
        else:
            problems.append(f"{where}: grab answered {str(grab)[:140]!r}")
        if isinstance(pick, dict):
            if pick.get("hit") != "w" or abs(pick["point"][0]) > 1.0 or abs(pick["point"][1]) > 1.0:
                problems.append(f"{where}: pick {pick}")
        elif not is_refusal(pick):
            problems.append(f"{where}: pick answered {str(pick)[:140]!r}")
        if isinstance(project, list):
            centre = [said["window"][0] / 2, said["window"][1] / 2]
            if abs(project[0] - centre[0]) > 0.8 or abs(project[1] - centre[1]) > 0.8:
                problems.append(f"{where}: project {project} for a {said['window']} window")
        elif not is_refusal(project):
            problems.append(f"{where}: project answered {str(project)[:140]!r}")
        if "ready" in record:
            counts["ready_true" if record["ready"] else "ready_false"] += 1
            if record["ready"] and not isinstance(grab, dict):
                problems.append(f"{where}: wait_ready() said True and grab said {str(grab)[:120]!r}")
            if record["took"] > 4.5:
                problems.append(f"{where}: wait_ready() took {record['took']} s")
        if record.get("settles") is False:
            problems.append(f"{where}: STUCK — Qt shows the window and the host does not come up: "
                            f"{record.get('stuck')}")
    return problems, counts


def test_the_walk_judge_sees_what_it_is_for() -> None:
    """The control for the judge below, no engine involved: a frame of another size, a
    blank frame, ``wait_ready()`` True followed by a refusal, an engine error where a
    refusal belongs, a bogus pick, a stuck host — each must be named."""
    good = {"w": 447, "h": 500, "dpr": 1.0, "red": 2550, "box": [198, 225, 249, 275]}
    said = {"window": [447, 500], "visible": True, "exposed": True, "minimised": False}
    hit = {"hit": "w", "point": [0.0, 0.0, 20.0]}
    no = "HostStateError: grab(): not now"

    def record(**changes: Any) -> dict[str, Any]:
        look = {"state": "live", "window": [447, 500], "grab": good, "pick": hit, "project": [223.5, 250.0]}
        look.update(changes.pop("look", {}))
        return {"what": "x", "mode": "wait", "qt": {**said, **changes.pop("qt", {})}, "look": look,
                "ready": True, "took": 0.1, **changes}

    assert _walk_problems({"records": [record(), record(look={"grab": no, "pick": no, "project": no},
                                                        ready=False)]}) == (
        [], {"frames": 1, "refusals": 1, "ready_true": 1, "ready_false": 1})
    for wrong, word in (
            (record(qt={"window": [2557, 1440]}), "for a 2557 x 1440 window"),
            (record(look={"grab": {**good, "red": 0, "box": None}}), "no red pixel"),
            (record(look={"grab": no, "pick": no, "project": no}), "wait_ready() said True"),
            (record(look={"grab": "EngineError: grab() returned a 447 x 500 px image"}), "grab answered"),
            (record(look={"pick": {"hit": "w", "point": [40.0, 0.0, 20.0]}}), "pick"),
            (record(look={"pick": None}), "pick answered"),
            (record(look={"project": [0.0, 0.0]}), "project"),
            (record(qt={"minimised": True}), "invisible or minimised"),
            (record(took=5.0), "took"),
            (record(settles=False, stuck="starting"), "STUCK")):
        problems, _counts = _walk_problems({"records": [wrong]})
        assert len(problems) >= 1 and word in problems[0], (word, problems)


#: The seed and length of the random walk. Fixed: a failure must be the same failure twice.
WALK_SEED, WALK_STEPS = 20261011, 250


def test_a_seeded_random_order_never_yields_a_wrong_frame(tmp_path: Path) -> None:
    """Review round 3 — the invariant of the whole state machine, under an order nobody
    chose: 250 operations a parent can do to an embedded host (splitter to nothing, to
    one pixel, wide; resize; maximise; full screen; minimise; hide and show the pane,
    the host, the window), each followed by a look at once, after ``wait_ready()``, or
    after the event loop ran.

    - every ``grab()`` is the scene at the size QT gave for the window at that moment,
      with the probe at the predicted pixels — or a ``HostStateError``;
    - a pick that answers hits the probe, a projection that answers is the centre;
    - ``wait_ready()`` True is followed by a frame;
    - a window Qt shows, with an area, comes up once the event loop ran (never stuck).

    The same seed on Direct3D 11 (GPU and WARP) and on xcb + OpenGL."""
    proc = run_order("random_walk", tmp_path, str(WALK_SEED), str(WALK_STEPS), timeout=560.0)
    assert survived(proc), f"the random walk did not survive\n{report(proc)}"
    out = json.loads(next(line for line in proc.stdout.splitlines()
                          if line.startswith("RESULT "))[len("RESULT "):])
    assert out["ready"] is True and out["final_ready"] is True
    problems, counts = _walk_problems(out)
    assert not problems, f"{len(problems)} problem(s) in {len(out['records'])} looks:\n" + "\n".join(problems[:25])
    assert len(out["records"]) == WALK_STEPS + 2
    # not vacuous: the walk must have seen frames AND refusals, and waits that said yes and no
    assert counts["frames"] >= 40 and counts["refusals"] >= 40, counts
    assert counts["ready_true"] >= 20 and counts["ready_false"] >= 20, counts
    assert out["stale_frames"] == 0, (
        f"{out['stale_frames']} grab(s) came back in another size than the window's and were "
        "refused: the event stream said 'caught up' and was wrong")
    assert out["health"]["ok"] is True and out["health"]["slots_off_gui_thread"] == 0
    artifacts = os.environ.get("OGP_RENDER3D_ARTIFACTS")
    if artifacts:
        Path(artifacts).mkdir(parents=True, exist_ok=True)
        (Path(artifacts) / "random-walk.txt").write_text(
            f"{sys.platform}: seed {WALK_SEED}, {WALK_STEPS} steps, api {out['api']}, {counts}\n",
            encoding="utf-8")


def test_wait_ready_comes_back_when_the_window_goes_during_the_wait(tmp_path: Path) -> None:
    """Review round 2, P2-5: the window is hidden, closed or destroyed while
    ``wait_ready()`` runs its event loop. It looked at visibility once and then spun
    to its timeout (3.0 of 3.0 s). Now: False, well inside a second, and no frame."""
    out = observed("wait_while_it_goes", tmp_path)
    assert set(out) == {"hide", "close", "delete"}
    for how, row in out.items():
        assert row["wait_ready"] is False, (how, row)
        assert row["seconds"] < 1.0, f"{how}: wait_ready() took {row['seconds']} s of its 5 s"
        assert is_refusal(row["grab"]), (how, row)
    assert (out["hide"]["state"], out["close"]["state"], out["delete"]["state"]) == (
        "hidden", "closed", "closed")


def _assert_closed_host_contract(calls: dict[str, Any], what: str) -> None:
    for name, answer in calls.items():
        if name in RELEASES:
            assert answer is None, f"{what}: {name}() must be a silent no-op, got {answer!r}"
        else:
            assert is_refusal(answer) and "closed" in answer, f"{what}: {name}() → {answer!r}"


def test_the_closed_host_table_covers_every_public_call() -> None:
    """The two tests below are only as strong as the table they loop over."""
    from open_garden_planner.ui.view3d.quick3d.host import Quick3DHost

    child._register_calls()  # noqa: SLF001 - the child's own table
    public = {name for name in dir(Quick3DHost)
              if not name.startswith("_") and callable(getattr(Quick3DHost, name))}
    assert set(child.HOST_CALLS) == public - ANSWERS_WHEN_CLOSED


def test_close_then_anything(tmp_path: Path) -> None:
    """After ``close()`` the three releases do nothing, the state and the health answer,
    and EVERY other call raises ``HostStateError`` — at once, and still after the event
    loop has delivered the deferred delete."""
    out = observed("close_anything", tmp_path)
    assert out["ready"] is True and out["health_ok"] is True
    assert (out["state"], out["is_closed"], out["is_visible"]) == ("closed", True, False)
    _assert_closed_host_contract(out["calls"], "right after close()")
    _assert_closed_host_contract(out["calls_after_events"], "after the deferred delete")


def test_parent_destroyed_then_anything(tmp_path: Path) -> None:
    """Qt destroys the view with the widget that parents its container (what closing the
    main window does). The host notices by asking Qt — nobody told it — and behaves
    exactly like a closed one."""
    out = observed("parent_destroyed_anything", tmp_path)
    assert out["ready"] is True
    assert (out["state"], out["is_closed"], out["is_visible"]) == ("closed", True, False)
    _assert_closed_host_contract(out["calls"], "after the parent was destroyed")


# ── the sink's teardown in every order ───────────────────────────────────────


@pytest.fixture(scope="module")
def clear_orders(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return observed("sink_clear_orders", tmp_path_factory.mktemp("clear_orders"))


@pytest.mark.parametrize("order", ["parent_destroyed_then_clear",
                                   "parent_destroyed_close_then_clear", "close_then_clear"])
def test_clear_is_clean_whenever_the_host_died(clear_orders: dict[str, Any], order: str) -> None:
    """Before round 1: ``TypeError``, ``ValueError``, and ``EngineError`` with the books
    left dirty — one per order. ``clear()`` returns in each, empties the books, is
    idempotent, and ``SceneSync.reset()`` works after it."""
    row = clear_orders[order]
    assert row["first_clear"] is None, row
    assert row["items"] == [] and row["in_transaction"] is False
    assert row["second_clear"] is None and row["third_clear"] is None, "clear() is idempotent"
    assert row["host_close"] is None and row["reset"] is None
    assert row["state"] == "closed"


def test_reset_alone_recovers_from_a_dead_engine_and_a_fresh_host_renders(
        clear_orders: dict[str, Any]) -> None:
    """The recovery as L1.3 will meet it. The main window is gone and took the engine
    with it; the next ``apply`` raises ``HostStateError`` and poisons the sync.
    ``SceneSync.reset()`` — which calls ``Quick3DSink.clear()`` and stays poisoned if
    that raises — succeeds on the dead engine. The SAME sink and sync, pointed at a
    fresh host (``Quick3DSink.attach``) and applied again: the scene is back, and
    the sink holds what a from-scratch apply would."""
    dead = clear_orders["reset_alone_on_a_dead_engine"]
    assert is_refusal(dead["apply_on_dead_engine"]) and "closed" in dead["apply_on_dead_engine"]
    assert dead["poisoned"] is True
    assert dead["reset"] is None, "reset() must not raise on an engine that is gone"
    assert dead["poisoned_after_reset"] is False
    assert dead["items"] == [] and dead["in_transaction"] is False and dead["state"] == "closed"
    fresh = clear_orders["fresh"]
    assert fresh["ready"] is True
    judge_step("sink_clear_orders", "fresh", fresh["look"], FRAME)
    assert fresh["parts"] == 1 and fresh["items"] == ["w"]
    assert fresh["state_equals_expected"] is True


# ── what a shown host learns about itself, on the GUI thread ─────────────────


def test_a_healthy_host_says_so_and_counts_its_frames_on_the_gui_thread(tmp_path: Path) -> None:
    """``health()`` with the shipped scene: nothing to report, at least one frame
    presented — and the slots that count them ran on the GUI thread although Qt renders
    (and emits ``frameSwapped``) on a thread of its own: the host's own slots (it
    counts every call that arrived elsewhere), and a second one this test connects the
    same way. No Python ever runs on Qt's render thread."""
    out = observed("health", tmp_path)
    judge("health", out)
    assert out["before_show"] == {"ok": True, "frames_presented": 0, "qml_warnings": [],
                                  "qml_warning_count": 0, "scene_graph_error": None,
                                  "slots_off_gui_thread": 0}
    assert out["health"]["ok"] is True and out["health"]["frames_presented"] >= 1
    assert out["health"]["slots_off_gui_thread"] == 0
    assert out["frame_slot_on_gui_thread"] is True
    assert out["health_after_close"] == out["health"], "health() still answers on a closed host"


def test_a_failing_qml_binding_reaches_health(tmp_path: Path) -> None:
    """A binding in the scene file that fails at run time: no exception anywhere, the
    frame still renders. The QML engine's ``warnings`` signal — delivered on the GUI
    thread — puts it into ``health()`` (and the log)."""
    qml = tmp_path / "qml"
    shutil.copytree(QML_SOURCE, qml)
    scene = qml / "Garden.qml"
    text = scene.read_text(encoding="utf-8")
    marker = "    property real windTime: 0\n"
    assert marker in text
    scene.write_text(text.replace(marker, marker + "    property real probe: nothingHere.value\n"),
                     encoding="utf-8")
    out = observed("health", tmp_path, str(qml))
    judge("health", out)  # the scene itself is intact: the frame is right
    assert out["health"]["ok"] is False and out["health"]["qml_warning_count"] >= 1
    assert any("nothingHere" in line for line in out["health"]["qml_warnings"]), out["health"]


@pytest.mark.skipif(not WINDOWS, reason="QT_D3D_ADAPTER_INDEX forces the failure on Direct3D only")
def test_a_graphics_device_that_cannot_be_created_is_reported_not_fatal(tmp_path: Path) -> None:
    """The window's ``sceneGraphError`` is CONNECTED (queued). With an adapter index
    that does not exist Qt cannot create the device: the host never becomes live,
    ``wait_ready()`` gives up as soon as Qt said so, ``health()`` carries the message,
    no frame was presented, nothing can be grabbed — and the process is alive. The
    control below is the same failure with nothing connected."""
    out = observed("health", tmp_path, QT_D3D_ADAPTER_INDEX="9")
    assert out["ready"] is False and out["wait_s"] < 3.0, "no waiting for a device that will not come"
    judge_step("health", "shown", out["shown"], REFUSED)
    assert "graphics backend" in str(out["health"]["scene_graph_error"]), out["health"]
    assert out["health"]["ok"] is False and out["health"]["frames_presented"] == 0
    assert out["health"]["slots_off_gui_thread"] == 0, "the error arrived on the GUI thread"


@pytest.mark.skipif(not WINDOWS, reason="QT_D3D_ADAPTER_INDEX forces the failure on Direct3D only")
def test_without_a_slot_on_scene_graph_error_qt_blocks_and_would_abort(tmp_path: Path) -> None:
    """CONTROL. The same failure on a raw ``QQuickView`` with NOTHING connected to
    ``sceneGraphError``: Qt opens a modal error box ("Failed to initialize graphics
    backend for D3D11.") and the process sits in it; dismissed, Qt aborts (measured on
    a desktop: blocked until killed, and exit ``0xC0000409`` once OK is clicked). In
    the app that would be the whole 2D application. Either way the order never
    reaches its end: blocked (killed here after 8 s) or dead."""
    proc = run_order("unguarded_device_failure", tmp_path, timeout=8.0, QT_D3D_ADAPTER_INDEX="9")
    assert "SHOWN" in proc.stdout, report(proc)
    assert not survived(proc) and "RESULT" not in proc.stdout, (
        f"Qt neither blocked nor aborted: the slot would be folklore\n{report(proc)}")


def test_which_thread_renders_a_shown_window(tmp_path: Path) -> None:
    """The fact the whole message-recorder rule rests on: a SHOWN window is rendered
    on a thread of its own. Qt names its render loop in ``qt.scenegraph.general``
    (read from the child's stderr — no Python ran there). Measured: ``threaded`` on
    Direct3D 11, GPU and WARP. On other platforms the answer is recorded with the
    run's artifacts; the rule holds either way (the basic loop has no such thread)."""
    proc = run_order("render_loop", tmp_path, QT_FORCE_STDERR_LOGGING="1",
                     QT_LOGGING_RULES="qt.scenegraph.general.debug=true")
    assert survived(proc), report(proc)
    loops = [name for name in ("threaded", "basic") if f"{name} render loop" in proc.stderr]
    assert len(loops) == 1, f"Qt did not name its render loop\n{report(proc)}"
    artifacts = os.environ.get("OGP_RENDER3D_ARTIFACTS")
    if artifacts:
        Path(artifacts).mkdir(parents=True, exist_ok=True)
        (Path(artifacts) / "render-loop.txt").write_text(
            f"{sys.platform}: {loops[0]} render loop\n", encoding="utf-8")
    if WINDOWS:
        assert loops == ["threaded"]


# ── controls: what the host forbids must really fail ─────────────────────────


def _dies(order: str, tmp_path: Path, attempts: int = 3) -> tuple[bool, str]:
    """Does the raw call order kill the process? Up to ``attempts`` runs: the claim is
    "it kills", and one lucky survival of an undefined-behaviour crash must not turn a
    control red. Also returns what the last run left behind."""
    last = ""
    for attempt in range(attempts):
        proc = run_order(order, tmp_path / f"attempt{attempt}")
        last = report(proc)
        if not survived(proc):
            return True, last
    return False, last


def _unmeasured_elsewhere(*platforms: str) -> pytest.MarkDecorator:
    """Asserted where it was measured; reported (xfail, not strict) everywhere else —
    the CI log of the other platform then says ``XFAIL`` or ``XPASS``, and that is
    the measurement."""
    measured = any(sys.platform.startswith(name) for name in platforms)
    return pytest.mark.xfail(not measured, strict=False,
                             reason=f"measured on {' and '.join(platforms)} only")


@_unmeasured_elsewhere("win32", "linux")
@pytest.mark.parametrize("order", ["unguarded_show_grab", "unguarded_grab_show",
                                   "unguarded_user_close_grab"])
def test_without_the_guard_the_process_dies(tmp_path: Path, order: str) -> None:
    """The killing call orders, on the raw ``QQuickView`` behind the host's back: shown
    and grabbed before the first expose; the offscreen frame and then shown; a window
    the user closed, grabbed. In the first two the grab RETURNS — a correct image, no
    exception — and the process is dead a moment later (abort, access violation, heap
    corruption; on xcb a segfault, a bus error or an abort). Direct3D 11: 3 of 3 each
    on the GPU and on WARP; xcb: 5 of 5 each. If the crash ever stopped happening the
    guard would be folklore, and this test says so."""
    died, last = _dies(order, tmp_path)
    assert died, f"{order}: the unguarded call order no longer kills the process\n{last}"
    if order != "unguarded_user_close_grab":
        assert "GRABBED " in last, f"the control never reached its grab\n{last}"


@_unmeasured_elsewhere("linux")
def test_the_xcb_killer_dies_without_the_guard(tmp_path: Path) -> None:
    """``show(); hide(); grab()`` on the raw view: the round-1 host read "no scene graph,
    not visible" as ``new``, took the offscreen frame, and on xcb the process
    segfaulted in the next event-loop pass (exit −11, the red CI job; 5 of 5 here).
    On Direct3D 11 the same order was measured to survive — which is why the matrix
    needs this control on Linux, and why it is only reported on Windows."""
    died, last = _dies("unguarded_show_hide_grab", tmp_path, attempts=5)
    assert died, f"the xcb killer no longer kills\n{last}"


@_unmeasured_elsewhere("win32", "linux")
def test_the_raw_view_lies_in_a_collapsed_pane_and_the_judge_sees_it(tmp_path: Path) -> None:
    """CONTROL for the judge (P1-B). In a pane collapsed to width 0 Qt calls the window
    visible and exposed; a raw grab returns a blank image of the OLD size, and right
    after the pane reopened it is blank still. The judge must reject both — by their
    pixels — and accept the frame taken one event-loop pass later."""
    out = observed("unguarded_collapsed_pane", tmp_path)
    collapsed, reopened = out["collapsed"], out["reopened_at_once"]
    assert (collapsed["visible"], collapsed["exposed"]) == (True, True), "what Qt says there"
    assert frame_problem(collapsed["grab"], collapsed["window"]) is not None
    assert frame_problem(reopened["grab"], reopened["window"]) is not None
    assert reopened["root"][0] != reopened["window"][0], "the scene lags the window"
    assert frame_problem(out["reopened_later"]["grab"], reopened["window"]) is None


@_unmeasured_elsewhere("win32", "linux")
def test_a_python_message_handler_beside_a_shown_window_deadlocks(tmp_path: Path) -> None:
    """CONTROL (review round 2, P1-C) — so nobody re-adds a message watch to the host.

    A Python Qt message handler, one message from Qt's render thread, and the GUI
    thread inside a Python-called ``hide()``: the render thread waits for the GIL, the
    GUI thread waits for the render thread. The order never comes back from
    ``hide()`` (every run: Direct3D 11 on the GPU and on WARP, and xcb + OpenGL). The
    twin without the handler returns in milliseconds.

    It also carries the measurement behind the supervised watchdog. The child arms
    ``faulthandler`` with ``exit=True`` for 4 s and the stack dump IS written. On
    Linux that ends the process (exit 1). On Windows the process is still alive at
    12 s: ``_exit`` is ``ExitProcess`` there, which waits in a DLL's detach code."""
    twin = observed("deadlock_without_handler", tmp_path)
    assert twin["ready"] is True and twin["returned_after_s"] < 2.0, twin
    proc = run_order("deadlock_with_handler", tmp_path, timeout=12.0)
    assert "HIDING " in proc.stdout, report(proc)
    assert "RESULT" not in proc.stdout, f"hide() came back: no deadlock\n{report(proc)}"
    assert "Timeout (0:00:04)" in proc.stderr, "faulthandler's dump must have been written"
    if WINDOWS:
        assert timed_out(proc), (
            f"the process ended by itself: faulthandler's exit works here after all\n{report(proc)}")
    if LINUX:
        assert not timed_out(proc) and proc.returncode == 1, (
            f"on Linux faulthandler's exit must end the deadlocked process\n{report(proc)}")


@pytest.mark.skipif(not WINDOWS, reason="the supervising process is Windows-only (runlog.py)")
def test_the_supervised_watchdog_ends_the_deadlock(tmp_path: Path) -> None:
    """Review round 2, P2-6. The same deadlock under ``runlog.watchdog(supervised=True)``:
    the in-process watchdog dumps the stacks and cannot end the process (``_exit`` is
    ``ExitProcess``, which waits in a DLL's detach code); the supervising process
    terminates it from outside — exit code 1, with the reason in the log."""
    log = tmp_path / "deadlock.log"
    proc = run_order("deadlock_supervised", tmp_path, str(log), timeout=40.0)
    assert not timed_out(proc), f"nothing ended the deadlocked process\n{report(proc)}"
    assert proc.returncode == 1, report(proc)
    assert "RESULT" not in proc.stdout
    text = log.read_text(encoding="utf-8")
    order = [text.index(marker) for marker in (
        "[watchdog] armed=True seconds=4.0 what=the deadlock control supervisor=process ",
        "Timeout (0:00:04)", "[supervisor] fired=True what=the deadlock control")]
    assert order == sorted(order), text
    assert "terminated from outside, exit code 1" in text


def test_a_child_of_the_matrix_cannot_see_the_developers_data(tmp_path: Path) -> None:
    """Review round 2, P2-9: every child gets ``APPDATA`` / ``XDG_DATA_HOME`` /
    ``XDG_CONFIG_HOME`` set explicitly to directories of the test's own, whatever the
    parent's environment says."""
    for build in (isolated_env, render_env):
        env = build(tmp_path)
        for name in ("APPDATA", "XDG_DATA_HOME", "XDG_CONFIG_HOME"):
            assert Path(env[name]).is_dir() and tmp_path in Path(env[name]).parents, (build, name)
            assert env[name] != os.environ.get(name), (build, name)
