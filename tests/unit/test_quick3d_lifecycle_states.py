"""The host's states as a function of Qt's facts — the whole truth table (L1.2, #385).

``ui/view3d/quick3d/lifecycle.py`` is engine-free: it turns nine facts Qt reports
about a window into one of six states, and ``Quick3DHost`` contains no state logic
of its own. So the table can be checked here, without a window, on every machine.

Two kinds of test:

- ``MEASURED`` — the rows somebody paid for: each is what Qt 6.11 was MEASURED to
  report in a situation (Direct3D 11 on an RTX 4070 and on WARP; the xcb row on
  GitHub's runner), and the state a truthful host must derive from it. Three of
  them were wrong in the first state machine (review round 2): they are marked.
- invariants over EVERY combination of the facts (3,072) — stated as what must
  never happen, not as a second copy of the function.

That Qt really reports these facts, and what a host does in each state, is the
render tier's (``tests/integration/test_quick3d_lifecycle.py``).
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from typing import Any

import pytest

from open_garden_planner.ui.view3d.quick3d.lifecycle import (
    MIN_SIDE_PX,
    HostState,
    WindowFacts,
    state_of,
    why_no_frame,
)

LIVE_FACTS: dict[str, Any] = {
    "surface_created": True, "visible": True, "minimised": False, "width": 447, "height": 500,
    "scene_graph": True, "exposed": True, "root_width": 447.0, "root_height": 500.0}


def facts(**changes: Any) -> WindowFacts:
    return WindowFacts(**{**LIVE_FACTS, **changes})


# (what was done, the facts Qt reported, the state)
MEASURED: list[tuple[str, dict[str, Any], str]] = [
    ("a new QQuickView",
     {"surface_created": False, "visible": False, "scene_graph": False, "exposed": False}, "new"),
    ("show(), no event yet", {"scene_graph": False, "exposed": False}, "starting"),
    ("show(), one pass of the event loop", {}, "live"),
    ("hide(), no event yet: Qt still says exposed", {"visible": False}, "hidden"),
    ("hide(), later", {"visible": False, "exposed": False}, "hidden"),
    ("shown again, no event yet", {"exposed": False}, "starting"),
    ("minimised: Qt still says visible", {"minimised": True, "exposed": False}, "hidden"),
    # review round 2, P1-A — read as "new", took the offscreen frame, segfault on xcb
    ("show(); hide() before any event",
     {"visible": False, "scene_graph": False, "exposed": False}, "hidden"),
    # review round 2, P1-B — read as "live": a blank frame of the old size, a bogus pick
    ("the splitter pane collapsed to width 0: visible AND exposed", {"width": 0}, "hidden"),
    ("the pane reopened, no event yet: the scene is still 0 wide", {"root_width": 0.0}, "starting"),
    ("the pane squeezed to one pixel: no 3D is drawn", {"width": 1, "root_width": 1.0}, "hidden"),
    ("one pixel high", {"height": 1, "root_height": 1.0}, "hidden"),
    ("two pixels wide: the scene is drawn", {"width": 2, "root_width": 2.0}, "live"),
    ("resized, no event yet: the scene has the old size", {"width": 200}, "starting"),
    # a window closed with its X button loses its platform window and keeps the scene graph
    ("closed by the user", {"visible": False, "exposed": False}, "hidden"),
    # no graphics device: Qt says visible and exposed and never gets a scene graph
    ("the graphics device could not be created", {"scene_graph": False}, "starting"),
]


@pytest.mark.parametrize(("_what", "changes", "expected"), MEASURED,
                         ids=[row[0] for row in MEASURED])
def test_what_qt_was_measured_to_report(_what: str, changes: dict[str, Any], expected: str) -> None:
    assert state_of(facts(**changes)).value == expected


def test_only_live_is_answerable() -> None:
    for _what, changes, expected in MEASURED:
        assert facts(**changes).answerable is (expected == "live"), _what


def every_combination() -> Iterator[WindowFacts]:
    for surface, visible, minimised, width, height, graph, exposed, root_w, root_h in itertools.product(
            (False, True), (False, True), (False, True), (0, 1, 2, 447), (0, 1, 2, 500),
            (False, True), (False, True), ("same", 0.0, 320.0), ("same", 240.0)):
        yield WindowFacts(surface, visible, minimised, width, height, graph, exposed,
                          float(width) if root_w == "same" else root_w,
                          float(height) if root_h == "same" else root_h)


ALL = list(every_combination())


def test_the_table_is_complete() -> None:
    assert len(ALL) == 3072
    assert {state_of(row) for row in ALL} == {
        HostState.NEW, HostState.STARTING, HostState.LIVE, HostState.HIDDEN}


def test_closed_and_spent_win_over_every_fact() -> None:
    assert state_of(None) is HostState.CLOSED and state_of(None, spent=True) is HostState.CLOSED
    for row in ALL:
        assert state_of(row, closed=True) is HostState.CLOSED
        assert state_of(row, closed=True, spent=True) is HostState.CLOSED
        assert state_of(row, spent=True) is HostState.SPENT


def test_new_means_no_platform_window_was_ever_created_and_nothing_else() -> None:
    """Only then is the one offscreen frame safe. "Not visible and no scene graph" is NOT
    new: that is also a window shown and hidden again (the xcb segfault)."""
    for row in ALL:
        assert (state_of(row) is HostState.NEW) is (not row.surface_created)


def test_live_needs_every_fact() -> None:
    """Each term was paid for: take any one away and the frame is fatal, blank or old."""
    for row in ALL:
        everything = (row.surface_created and row.visible and not row.minimised
                      and row.width >= 2 and row.height >= 2 and row.scene_graph and row.exposed
                      and round(row.root_width) == row.width and round(row.root_height) == row.height)
        assert (state_of(row) is HostState.LIVE) is everything, row


def test_without_a_scene_graph_no_state_gives_a_frame_but_new() -> None:
    """THE killer: a grab of a window that has a platform window and no scene graph."""
    for row in ALL:
        if row.surface_created and not row.scene_graph:
            assert state_of(row) in (HostState.STARTING, HostState.HIDDEN), row


def test_starting_means_on_its_way() -> None:
    """``wait_ready()`` waits while the host is starting — so nothing that will not come
    by itself may read as starting: not hidden, not minimised, not collapsed."""
    for row in ALL:
        if state_of(row) is HostState.STARTING:
            assert row.visible and not row.minimised and row.has_area, row
        if row.surface_created and (not row.visible or row.minimised or not row.has_area):
            assert state_of(row) is HostState.HIDDEN, row


def test_the_smallest_window_that_shows_3d_is_two_pixels() -> None:
    """Measured (Direct3D 11, GPU and WARP): at 1 px the frame is the bare clear colour,
    in either dimension; at 2 px it is the scene."""
    assert MIN_SIDE_PX == 2
    assert not facts(width=1, root_width=1.0).has_area and facts(width=2, root_width=2.0).has_area
    assert not facts(height=1, root_height=1.0).has_area


def test_the_scene_has_caught_up_when_it_rounds_to_the_windows_size() -> None:
    """The root item's size is a real, the window's an int (a fractional scale factor)."""
    assert facts(root_width=446.6).root_caught_up and facts(root_width=447.4).root_caught_up
    assert not facts(root_width=446.4).root_caught_up
    assert not facts(root_height=501.0).root_caught_up


REMEDY = {
    HostState.STARTING: "wait_ready()", HostState.HIDDEN: "wait_ready()",
    HostState.NEW: "wait_ready()", HostState.SPENT: "Build a new host",
    HostState.CLOSED: "closed",
}


def test_every_refusal_says_why_and_what_to_do() -> None:
    for row in ALL:
        state = state_of(row)
        if state is HostState.LIVE:
            continue
        for embedded in (False, True):
            text = why_no_frame(state, row, embedded=embedded)
            assert REMEDY[state] in text, (state, text)
    assert REMEDY[HostState.SPENT] in why_no_frame(HostState.SPENT, facts(), embedded=False)
    assert REMEDY[HostState.CLOSED] in why_no_frame(HostState.CLOSED, None, embedded=False)


@pytest.mark.parametrize(("changes", "embedded", "words"), [
    ({"scene_graph": False, "exposed": False}, False, "would kill the process"),
    ({"exposed": False}, False, "shown again but not exposed yet"),
    ({"root_width": 0.0}, False, "window 447 x 500, scene 0 x 500"),
    ({"visible": False}, False, "not visible"),
    ({"minimised": True}, False, "minimised"),
    ({"width": 0}, False, "no usable area (0 x 500 px"),
    ({"surface_created": False, "visible": False}, False, "never shown: show() it"),
    ({"surface_created": False, "visible": False}, True, "embedded host has no offscreen frame"),
])
def test_the_refusal_names_the_fact_that_is_missing(changes: dict[str, Any], embedded: bool,
                                                    words: str) -> None:
    row = facts(**changes)
    assert words in why_no_frame(state_of(row), row, embedded=embedded)


def test_a_spent_host_is_never_told_to_show_itself() -> None:
    """The first error message recommended ``show()`` — which killed the process."""
    text = why_no_frame(HostState.SPENT, facts(), embedded=False)
    assert "show() it" not in text and "Build a new host" in text
