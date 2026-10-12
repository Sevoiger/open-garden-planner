"""Walk mode of the 3D view — the walker's state, its keys and its clock (L1.8, ADR-065).

Engine-free, and thin: every rule of walking — what blocks, how a wall is slid
along, why the path does not depend on the frame rate — is Qt-free in
``core/scene3d/walk.py``. This module holds ONE ``WalkState`` and feeds that core:

- **Input.** ``WalkMode`` is an event filter the workspace installs on the engine's
  window AFTER the orbit filter, so it sees every event FIRST. While walking it
  takes the keys and the drags; when not walking it passes everything on, and the
  orbit camera works as before. It never touches the orbit camera: leaving walk
  mode shows the pose the user had, exactly.

  ==========================  ================================================
  W / Up, S / Down            walk ahead / back
  A / Left, D / Right         step to the left / right
  Shift                       twice the pace
  a drag (any button)         look around: right is right, down is down
  Escape                      leave walk mode (``left``)
  wheel, Page Up/Down, Home   nothing (they would move the orbit camera)
  press and release           passed on: a click still picks (``OrbitInput``)
  every other key             passed on: the app's shortcuts keep working
  ==========================  ================================================

- **Time.** A timer runs only while a walking key is held; each tick hands the
  core the time that passed. A key change is applied AFTER the time before it was
  walked, so the pace does not depend on when the timer happens to fire.
- **Focus.** A window that loses the focus gets no key release: all keys are let go.

No pointer capture, no head bob, no smoothing — reduced motion has nothing to stop.
"""

from __future__ import annotations

import time
from typing import Final

from PyQt6.QtCore import QEvent, QObject, QPointF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QMouseEvent, QWheelEvent

from open_garden_planner.core.scene3d.walk import (
    EYE_HEIGHTS_CM,
    WALK_FOV_DEG,
    WalkCommand,
    WalkState,
    WalkWorld,
    advance,
    eye_scene,
    is_free,
    look_target_scene,
    nearest_free,
    start_state,
    turned,
)

#: The walker is stepped at this interval while a walking key is held.
WALK_STEP_MS: Final = 16
#: Degrees of turn per pixel of a drag (the orbit drag's, so the two feel alike).
LOOK_DEG_PER_PX: Final = 0.3

_AHEAD: Final = frozenset({Qt.Key.Key_W, Qt.Key.Key_Up})
_BACK: Final = frozenset({Qt.Key.Key_S, Qt.Key.Key_Down})
_LEFT: Final = frozenset({Qt.Key.Key_A, Qt.Key.Key_Left})
_RIGHT: Final = frozenset({Qt.Key.Key_D, Qt.Key.Key_Right})
_WALK_KEYS: Final = _AHEAD | _BACK | _LEFT | _RIGHT
#: Keys of the orbit camera that must not reach it while walking.
_SWALLOWED: Final = frozenset({Qt.Key.Key_PageUp, Qt.Key.Key_PageDown, Qt.Key.Key_Home})

Camera = tuple[tuple[float, float, float], tuple[float, float, float], float]


class WalkMode(QObject):
    """Walk mode's state and input filter (module docstring). GUI thread only."""

    #: The walker moved, turned or changed its eye height: show the camera.
    changed = pyqtSignal()
    #: Escape was pressed while walking: the workspace leaves walk mode.
    left = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._state: WalkState | None = None
        self._world: WalkWorld | None = None
        self._eye_height = EYE_HEIGHTS_CM[0]
        self._held: set[int] = set()
        self._run = False
        self._look_from: QPointF | None = None
        self._stepped_at = 0.0
        self._paused = False
        #: The clock a step's length is measured on (the tests hold it).
        self.clock = time.perf_counter
        self._timer = QTimer(self)
        self._timer.setInterval(WALK_STEP_MS)
        self._timer.timeout.connect(self._step)

    # ── reads ────────────────────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        """True while the view is in walk mode."""
        return self._state is not None

    @property
    def state(self) -> WalkState | None:
        return self._state

    @property
    def world(self) -> WalkWorld | None:
        return self._world

    @property
    def eye_height_cm(self) -> float:
        return self._eye_height

    @property
    def moving(self) -> bool:
        """True while a walking key is held (the step timer runs)."""
        return self._timer.isActive()

    def camera(self) -> Camera:
        """``(eye, target, vertical field of view)`` for the host, scene frame."""
        state = self._state
        if state is None:
            raise RuntimeError("camera(): not in walk mode")
        return (eye_scene(state, self._eye_height), look_target_scene(state, self._eye_height),
                WALK_FOV_DEG)

    # ── control ──────────────────────────────────────────────────────────────

    def enter(self, world: WalkWorld, at: tuple[float, float], yaw_deg: float) -> bool:
        """Start walking at ``at`` (or the nearest free spot), facing ``yaw_deg``. False
        — and nothing changes — when the plan has no free ground to stand on."""
        state = start_state(world, at, yaw_deg)
        if state is None:
            return False
        self._world, self._state = world, state
        self._release_all()
        self._paused = False
        self.changed.emit()
        return True

    def leave(self) -> None:
        """Stop walking. The walker and its world are dropped; the eye height is kept
        for the next walk."""
        self._release_all()
        self._state = None
        self._world = None

    def set_world(self, world: WalkWorld) -> None:
        """The plan changed under the walker (an edit while walking): the same walker
        in the new world — moved to the nearest free spot if something was put where
        it stands; walk mode ends when there is no free ground left at all."""
        state = self._state
        if state is None:
            return
        self._settle()
        state = self._state
        assert state is not None
        self._world = world
        if not (is_free(world, state.east, state.north) and is_free(world, *state.shown)):
            spot = nearest_free(world, *state.shown)
            if spot is None:
                self.leave()
                self.left.emit()
                return
            self._state = WalkState(spot[0], spot[1], state.yaw_deg, state.pitch_deg, 0.0, spot)
            self.changed.emit()

    def set_eye_height(self, height_cm: float) -> None:
        """One of ``EYE_HEIGHTS_CM`` (anything else is refused)."""
        if height_cm not in EYE_HEIGHTS_CM:
            raise ValueError(f"an eye height is one of {EYE_HEIGHTS_CM}, got {height_cm!r}")
        if height_cm != self._eye_height:
            self._eye_height = height_cm
            if self.active:
                self.changed.emit()

    def pause(self) -> None:
        """The 3D pane was hidden: every key is let go and the clock stops. The walker
        stays where it is."""
        self._settle()
        self._release_all()
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    # ── the event filter ─────────────────────────────────────────────────────

    def eventFilter(self, _watched: QObject | None, event: QEvent | None) -> bool:  # noqa: N802 - Qt's name
        if event is None or self._state is None:
            return False
        kind = event.type()
        if isinstance(event, QKeyEvent):
            if kind == QEvent.Type.KeyPress:
                return self._key(event, pressed=True)
            if kind == QEvent.Type.KeyRelease:
                return self._key(event, pressed=False)
            return False
        if isinstance(event, QMouseEvent):
            if kind == QEvent.Type.MouseButtonPress:
                self._look_from = event.position()
                return False  # on to the orbit filter: a click still picks
            if kind == QEvent.Type.MouseButtonRelease:
                if event.buttons() == Qt.MouseButton.NoButton:
                    self._look_from = None
                return False
            if kind == QEvent.Type.MouseMove:
                return self._look(event)
            return False
        if isinstance(event, QWheelEvent):
            return True  # it would zoom the orbit camera under the walker
        if kind == QEvent.Type.FocusOut:
            self._settle()
            self._release_all()
        return False

    def _key(self, event: QKeyEvent, *, pressed: bool) -> bool:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            if pressed and not event.isAutoRepeat():
                self.left.emit()
            return True
        if key in _SWALLOWED:
            return True
        if key not in _WALK_KEYS and key != Qt.Key.Key_Shift:
            return False  # not ours: the app's shortcuts get it
        if event.isAutoRepeat():
            return True
        self._settle()  # the time until now was walked with the keys as they were
        if key == Qt.Key.Key_Shift:
            self._run = pressed
        elif pressed:
            self._held.add(key)
        else:
            self._held.discard(key)
        if self._command() != WalkCommand() and not self._paused:
            if not self._timer.isActive():
                self._stepped_at = self.clock()
                self._timer.start()
        else:
            self._timer.stop()
        return True

    def _look(self, event: QMouseEvent) -> bool:
        if event.buttons() == Qt.MouseButton.NoButton or self._look_from is None:
            return False  # a hover
        now = event.position()
        dx, dy = now.x() - self._look_from.x(), now.y() - self._look_from.y()
        self._look_from = now
        state = self._state
        if state is not None and (dx != 0.0 or dy != 0.0):
            self._settle()
            state = self._state
            assert state is not None
            self._state = turned(state, dx * LOOK_DEG_PER_PX, -dy * LOOK_DEG_PER_PX)
            self.changed.emit()
        return True

    # ── time ─────────────────────────────────────────────────────────────────

    def _command(self) -> WalkCommand:
        held = self._held
        forward = float(bool(held & _AHEAD)) - float(bool(held & _BACK))
        strafe = float(bool(held & _RIGHT)) - float(bool(held & _LEFT))
        return WalkCommand(forward, strafe, self._run and (forward != 0.0 or strafe != 0.0))

    def _settle(self) -> None:
        """Walk the time since the last step with the command as it is NOW (called
        before the command changes)."""
        if self._timer.isActive():
            self._step()

    def _step(self) -> None:
        state, world = self._state, self._world
        if state is None or world is None:
            self._timer.stop()
            return
        now = self.clock()
        after = advance(state, self._command(), now - self._stepped_at, world)
        self._stepped_at = now
        if after != state:
            self._state = after
            self.changed.emit()

    def _release_all(self) -> None:
        self._timer.stop()
        self._held.clear()
        self._run = False
        self._look_from = None
