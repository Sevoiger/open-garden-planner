"""THE STATE CONTRACT of the engine host (L1.2, fix rounds 2 and 3). Engine-free.

This docstring is the ONE place the contract is written down. ``host.py``, the
architecture docs, the ADR and the skills link here and do not restate it
(``tests/unit/test_quick3d_packaging.py`` pins that): the last time it stood in
nine places, three of them were wrong.

``Quick3DHost`` reads ten facts from Qt on every call (``WindowFacts``) and this
module turns them into one of six states. No Qt import: the whole truth table
runs in the GPU-less unit tests, and ``host.py`` contains no state logic.

The facts
---------
====  =====================================================================
S     a platform window was EVER created (Qt's ``PlatformSurface`` event —
      the fact behind ``QWindow.handle()``, which PyQt6 does not bind)
V     ``QWindow.isVisible()``
M     minimised (the view, or the top-level window that embeds it)
A     an area: width and height of at least ``MIN_SIDE_PX`` (2) logical px
G     ``QQuickWindow.isSceneGraphInitialized()``
E     ``QWindow.isExposed()``
R     the QML root item has the window's size
T     the render TARGET has the window's size: Qt delivered an expose to the
      exposed window after its last resize event — and no ``grab()`` since
      came back in another size
====  =====================================================================

The states — first match wins
-----------------------------
============  ==============================================================
``CLOSED``    ``close()`` ran to its end, or Qt destroyed the view
``SPENT``     else: the one offscreen frame was taken (its scene is gone)
``NEW``       else: not S
``HIDDEN``    else: not (V and not M and A) — hidden, minimised, or a pane
              collapsed to nothing
``LIVE``      else: G and E and R and T
``STARTING``  else — shown with an area, and not live yet
============  ==============================================================

What the states promise
-----------------------
- **``LIVE`` means: ``grab()`` returns the scene at the window's current size,
  or it raises ``HostStateError``.** Never a frame of another size, never a
  frame of another scene. ("The window" is the Qt Quick window itself. An
  embedded host whose PARENT was resized a moment ago still has its old size
  until the layout ran — and is truthfully live at that size until then.)
- **``wait_ready()`` returning True means: the host is ``LIVE`` after every
  pending event was delivered — the next ``grab()`` / ``pick()`` /
  ``project()`` answers**, at the size the window has then. It delivers what
  is pending ONCE for any visible host (also one that already reads live: a
  parent's resize may be waiting in the queue), then waits only while the host
  is ``STARTING``, looking again in every pass and blocking for events — never
  a busy loop. It answers False at once when nothing is on its way (new,
  hidden, minimised, collapsed, spent), when Qt reported that no graphics
  device will come, when the application is quitting (no event loop can run),
  when the host is hidden, closed or destroyed during the wait, and on timeout.
- **``STARTING`` means: do not ask for a frame.** Either the first expose is
  pending (no scene graph: a grab now KILLS the process), or the next one is
  (shown again), or the scene (R) or the render target (T) has not caught up
  with a resize: a frame taken now would be the old picture, or blank.
- **Not every resize passes through ``STARTING`` as seen from outside.** Qt
  delivers some resizes synchronously (``showFullScreen()``: R holds at once, T
  does not — measured) and some on the next pass of the event loop (a splitter
  move, ``showMaximized()``: at first nothing has changed at all). Do not
  reason about passes: after ANY change of size or visibility call
  ``wait_ready()`` and look at its answer.
- ``NEW`` has exactly ONE frame, offscreen, and only when the host is not
  embedded; it is ``SPENT`` after. ``SPENT`` refuses ``show()`` and
  ``container()`` (they would kill the process). ``HIDDEN`` and ``SPENT`` give
  no frame.

Which call is legal in which state
----------------------------------
==========================  =====  ========  =====  ======  =====  ======
call                        new    starting  live   hidden  spent  closed
==========================  =====  ========  =====  ======  =====  ======
``grab``                    once*  refuse    yes    refuse  refuse refuse
``pick``, ``project``       refuse refuse    yes    refuse  refuse refuse
``show``, ``container``     yes    yes       yes    yes     refuse refuse
``wait_ready``              False  waits     True   False+  False  refuse
``destroy_part``, ``clear``,
``close``                   yes    yes       yes    yes     yes    no-op
``state``, ``is_closed``,
``is_visible``, ``health``  yes    yes       yes    yes     yes    yes
every other call            yes    yes       yes    yes     yes    refuse
==========================  =====  ========  =====  ======  =====  ======

``refuse`` is a ``HostStateError`` whose text names the missing fact
(``why_no_frame``). ``once*``: refused when the host is embedded. ``False+``:
False at once for a host that is not visible. One that Qt still calls visible
(minimised, a pane collapsed to nothing) gets its pending events first; if it
is ``STARTING`` after them (the pane was reopened), it is waited for.

What raises what
----------------
``HostStateError``: the call cannot be answered truthfully in this state —
including a ``grab()`` on a live host whose image came back in another size
than the window's (the render target was stale after all: the host notes it,
reads ``STARTING`` until the next expose, and ``wait_ready()`` waits for that).
``EngineError``: the engine failed — the scene file did not load, a property or
a scene function is missing or threw, a Model was refused or not destroyed, no
image came back, or this backend renders no 3D at all.

Why these facts and no fewer — each was paid for with a dead process or a wrong
frame (``docs/11-risks-and-technical-debt`` §11.4.9; ADR-048, addendum L1.2):
"not visible and no scene graph" is not "never shown" (xcb segfaults); a
collapsed splitter pane is visible, exposed and 0 px wide; one pixel draws no
3D; the scene lags the window after an asynchronous resize; the render target
lags it after a synchronous one. ``isVisible()`` alone is neither necessary nor
sufficient, and neither is ``isExposed()``: right after ``hide()`` Qt still
calls the window exposed, and a minimised window stays "visible".

Not measured: platforms that un-expose an occluded window (macOS, Wayland) —
a visible window may read ``STARTING`` there for as long as it is covered, and
``wait_ready()`` would then use its whole timeout; and a change of the device
pixel ratio in mid-session (a window dragged to a display with another scale).
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

    #: S — a platform window was created at some point.
    surface_created: bool
    #: V — ``QWindow.isVisible()``.
    visible: bool
    #: M — the view itself, or the top-level window that embeds it, is minimised.
    minimised: bool
    #: ``QWindow.width()`` / ``height()``, logical pixels (A: both at least MIN_SIDE_PX).
    width: int
    height: int
    #: G — ``QQuickWindow.isSceneGraphInitialized()``.
    scene_graph: bool
    #: E — ``QWindow.isExposed()``.
    exposed: bool
    #: The QML root item's size (R: it equals the window's).
    root_width: float
    root_height: float
    #: T — an expose reached the exposed window after its last resize, and no grab
    #: since came back in another size: the render target has the window's size.
    target_synced: bool

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
        """A frame taken now is the current scene at the window's current size."""
        return (self.shows and self.scene_graph and self.exposed and self.root_caught_up
                and self.target_synced)


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


def why_no_frame(state: HostState, facts: WindowFacts | None, *, embedded: bool,
                 graphics_error: str | None = None) -> str:
    """Why a host in ``state`` cannot give a frame, and what to do — for the error text.

    ``graphics_error``: what Qt said when it could not create the graphics device
    (``HostHealth.scene_graph_error``); such a host is ``STARTING`` for ever, and
    telling its caller to wait would be a lie.
    """
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
        if not facts.scene_graph and graphics_error is not None:
            return (f"Qt could not create the graphics device for this window ({graphics_error}): "
                    "the host will never be live. See health().scene_graph_error; wait_ready() "
                    "answers False")
        if not facts.scene_graph:
            return ("the window is shown but not exposed yet — it has no scene graph, and a "
                    "frame taken now would kill the process. Call wait_ready() first")
        if not facts.exposed:
            return "the window is shown again but not exposed yet. Call wait_ready() first"
        if not facts.root_caught_up:
            return (f"the scene has not caught up with the window's size yet (window "
                    f"{facts.width} x {facts.height}, scene {facts.root_width:g} x "
                    f"{facts.root_height:g}): a frame taken now would be the old picture. "
                    "Call wait_ready() first")
        return (f"the render target has not caught up with the window's size yet (window "
                f"{facts.width} x {facts.height}; Qt resizes the target with the next expose): "
                "a frame taken now would have the old size. Call wait_ready() first")
    return f"the host is {state.value}"
