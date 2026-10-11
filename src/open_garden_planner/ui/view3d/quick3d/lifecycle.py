"""The host's states as a pure function of what Qt reports (L1.2, fix round 2). Engine-free.

``Quick3DHost`` reads nine facts from Qt (``WindowFacts``) and this module turns
them into one of six states. No Qt import: the whole truth table runs in the
GPU-less unit tests, and ``host.py`` contains no state logic of its own.

Why these facts and no fewer — each term was paid for with a dead process or a
wrong frame (ADR-048 addendum L1.2, review rounds 1 and 2):

===============  ============================================================
``NEW``          no platform window was EVER created. Only then is the one
                 offscreen frame safe: on xcb a window that was shown and
                 hidden again — still without a scene graph — took that frame
                 and the process segfaulted in the next event-loop pass.
``HIDDEN``       it has (or had) a platform window and shows nothing: not
                 visible, minimised, or no usable area — a ``QSplitter`` pane
                 collapsed to width 0 stays "visible" and "exposed" for Qt and
                 renders a blank frame, and a window ONE pixel wide or high
                 renders no 3D at all (the bare sky colour; from 2 px on the
                 frame is the scene — measured on Direct3D 11).
``LIVE``         shown, with an area, scene graph up, exposed, AND the QML
                 root item has the window's size. After every resize the root
                 lags the window by one pass of the event loop; a frame taken
                 in between is the old picture (or blank, coming from 0 px).
``STARTING``     shown, with an area, not live yet: the first expose is
                 pending (no scene graph — a grab now KILLS the process), or
                 the next one is, or the root item is catching up.
                 ``wait_ready()`` gets it to ``LIVE``.
``SPENT``        took its one offscreen frame; its scene is gone.
``CLOSED``       ``close()`` ran, or Qt destroyed the view.
===============  ============================================================

``isVisible()`` alone is neither necessary nor sufficient for a frame; neither
is ``isExposed()``: right after ``hide()`` Qt still calls the window exposed
until the event loop ran, and a minimised window stays "visible".
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Final

#: The smallest window side, in logical pixels, at which Qt Quick 3D draws the scene.
#: Measured (Direct3D 11, GPU and WARP, 100 % scale): at 1 px the frame is the bare
#: clear colour, in either dimension; at 2 px it is the scene.
MIN_SIDE_PX: Final = 2


class HostState(enum.Enum):
    """Where a host is in its life (module docstring)."""

    NEW = "new"
    STARTING = "starting"
    LIVE = "live"
    HIDDEN = "hidden"
    SPENT = "spent"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class WindowFacts:
    """What Qt says about the view at one moment (``Quick3DHost`` reads it per call)."""

    #: A platform window was created at some point — Qt's ``PlatformSurface`` event
    #: (``QWindow.handle()`` is the same fact, and PyQt6 does not bind it).
    surface_created: bool
    #: ``QWindow.isVisible()``.
    visible: bool
    #: The view itself, or the top-level window that embeds it, is minimised.
    minimised: bool
    #: ``QWindow.width()`` / ``height()``, logical pixels.
    width: int
    height: int
    #: ``QQuickWindow.isSceneGraphInitialized()``.
    scene_graph: bool
    #: ``QWindow.isExposed()``.
    exposed: bool
    #: The QML root item's size (``SizeRootObjectToView`` makes it follow the window).
    root_width: float
    root_height: float

    @property
    def has_area(self) -> bool:
        """Room for a 3D frame: at least ``MIN_SIDE_PX`` in both dimensions."""
        return self.width >= MIN_SIDE_PX and self.height >= MIN_SIDE_PX

    @property
    def root_caught_up(self) -> bool:
        return round(self.root_width) == self.width and round(self.root_height) == self.height

    @property
    def shows(self) -> bool:
        """Something of the view can be on screen: visible, not minimised, with an area."""
        return self.visible and not self.minimised and self.has_area

    @property
    def answerable(self) -> bool:
        """A frame taken now is the current scene at the current size."""
        return self.shows and self.scene_graph and self.exposed and self.root_caught_up


def state_of(facts: WindowFacts | None, *, closed: bool = False, spent: bool = False) -> HostState:
    """The state for ``facts`` (``None``: Qt destroyed the view) — a pure function."""
    if closed or facts is None:
        return HostState.CLOSED
    if spent:
        return HostState.SPENT
    if not facts.surface_created:
        return HostState.NEW
    if not facts.shows:
        return HostState.HIDDEN
    return HostState.LIVE if facts.answerable else HostState.STARTING


def why_no_frame(state: HostState, facts: WindowFacts | None, *, embedded: bool) -> str:
    """Why a host in ``state`` cannot give a frame, and what to do — for the error text."""
    if state is HostState.CLOSED or facts is None:
        return "the host is closed (or Qt destroyed its view)"
    if state is HostState.SPENT:
        return ("this host took its one offscreen frame and its scene is gone. "
                "Build a new host (offscreen.render_records does, per frame)")
    if state is HostState.NEW:
        if embedded:
            return ("an embedded host has no offscreen frame (its parent may show it at any "
                    "time): show its container and call wait_ready()")
        return "the host was never shown: show() it and call wait_ready()"
    if state is HostState.HIDDEN:
        if not facts.visible:
            return "the host is not visible: make it visible and call wait_ready()"
        if facts.minimised:
            return "the window is minimised: restore it and call wait_ready()"
        return (f"the view has no usable area ({facts.width} x {facts.height} px — a collapsed "
                f"pane? Below {MIN_SIDE_PX} px nothing 3D is drawn): give it room and call "
                "wait_ready()")
    if state is HostState.STARTING:
        if not facts.scene_graph:
            return ("the window is shown but not exposed yet — it has no scene graph, and a "
                    "frame taken now would kill the process. Call wait_ready() first")
        if not facts.exposed:
            return "the window is shown again but not exposed yet. Call wait_ready() first"
        return (f"the scene has not caught up with the window's size yet (window "
                f"{facts.width} x {facts.height}, scene {facts.root_width:g} x "
                f"{facts.root_height:g}): a frame taken now would be the old picture. "
                "Call wait_ready() first")
    return f"the host is {state.value}"
