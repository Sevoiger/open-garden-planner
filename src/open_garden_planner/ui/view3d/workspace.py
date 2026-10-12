"""The Plan / 3D / Split workspace of the main window (Phase 17 L1.3, ADR-057).

The area that held the canvas view holds a ``Workspace``: the canvas, and — once
the user first asks for it — a 3D pane beside it. Three modes:

- **Plan**: the canvas alone. Its size and its pixels are what they were without
  the workspace; the only addition on screen is a small floating mode switch.
- **3D**: the 3D pane alone.
- **Split**: canvas | 3D pane in a splitter. Neither side collapses: a collapsed
  3D pane would be a third way of saying "Plan" in which the engine keeps running.

Nothing of the 3D engine is imported or created before the first switch to 3D or
Split; after that ONE engine host is kept for the life of the window and only
hidden and shown. This module imports no Qt Quick binding at all: the engine is
built by ``engine_factory`` — by default ``ui/view3d/quick3d`` — which is also the
seam the tests put a recording sink into.

Layout rule (ADR-057): the 3D view is a NATIVE window inside a container, and no
sibling widget paints over it. So nothing here ever overlays it — every control,
notice and message of the 3D pane sits in a strip ABOVE the view, or replaces it.
L1.8's info card goes into the same pane, beside or below the view, not on top.

What moves the camera: user input (``OrbitInput``), "Reset 3D view", the first
open, and a document change — the four calls in ``_on_document_shown``,
``reset_view`` and the input filter. No edit does.

Timers: the pipeline's tick is ``Scene3DController``'s; this module adds two, and
both stop when the 3D pane is hidden — the camera step (runs only while the camera
is still moving) and the health poll (``Quick3DHost.health()``: first frame,
graphics failure, QML warnings — never a Qt message handler, which deadlocks
beside Qt's render thread).
"""

from __future__ import annotations

import enum
import logging
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Final, Protocol

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QHideEvent, QKeySequence, QShowEvent
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from open_garden_planner.core.scene3d.camera import FOV_DEG, OrbitCamera
from open_garden_planner.ui.plain_text import clip_text

from .init_guard import Init3DGuard, ModeReason, RenderMode, default_directory

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QGraphicsScene

    from open_garden_planner.core.scene3d import EngineSink
    from open_garden_planner.core.sim_clock import SimClock

    from .orbit_input import OrbitInput
    from .problems_dialog import View3DProblemsDialog
    from .sync_controller import Scene3DController, TickReport

_LOG: Final = logging.getLogger(__name__)

#: The 3D pane never gets narrower than this in Split mode (it does not collapse).
MIN_PANE_WIDTH: Final = 220
#: The camera is stepped at this interval while it moves.
CAMERA_STEP_MS: Final = 16
#: The health poll: fast until the first frame was presented, then slow.
HEALTH_FAST_MS: Final = 200
HEALTH_SLOW_MS: Final = 2000
#: Where the floating mode switch sits over the canvas: clear of the rulers' corner.
_FLOAT_OFFSET: Final = 28
_SPLITTER_KEY: Final = "workspace"


class WorkspaceMode(enum.Enum):
    PLAN = "plan"
    VIEW3D = "3d"
    SPLIT = "split"


class View3DState(enum.Enum):
    """Where the 3D pane is in its life."""

    NOT_STARTED = "not_started"   # 3D was never asked for (nothing was imported)
    STARTING = "starting"         # the engine is built; no frame was presented yet
    RUNNING = "running"
    UNAVAILABLE = "unavailable"   # Qt could not create the graphics device
    FAILED = "failed"             # building the engine raised (an import, the scene file)


class EngineHost(Protocol):
    """What the workspace needs from the engine's host (``Quick3DHost`` has it all)."""

    def container(self, parent: QWidget | None = None) -> QWidget: ...
    def install_event_filter(self, watcher: QObject) -> None: ...
    def set_camera(self, eye_scene: tuple[float, float, float],
                   target_scene: tuple[float, float, float], fov_deg: float = ...) -> None: ...
    def view_size(self) -> tuple[float, float]: ...
    def health(self) -> Any: ...
    def close(self) -> None: ...


#: ``factory(prefer_software) -> (host, sink)`` — builds the engine. THE TEST SEAM.
EngineFactory = Callable[[bool], "tuple[EngineHost, EngineSink]"]
LocationProvider = Callable[[], "Mapping[str, Any] | None"]


def quick3d_engine(prefer_software: bool) -> tuple[EngineHost, EngineSink]:
    """The production engine: a ``Quick3DHost`` and the ``Quick3DSink`` that drives
    it. Importing the two modules here is what loads Qt Quick 3D — on first use."""
    from .quick3d.adapter import Quick3DSink
    from .quick3d.host import Quick3DHost

    host = Quick3DHost(prefer_software=prefer_software)
    return host, Quick3DSink(host)


class ModeSwitch(QFrame):
    """The segmented control: three buttons that ARE the three mode actions."""

    def __init__(self, actions: list[QAction], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAutoFillBackground(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(0)
        self.buttons: list[QToolButton] = []
        for action in actions:
            button = QToolButton(self)
            button.setDefaultAction(action)
            button.setAutoRaise(True)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            layout.addWidget(button)
            self.buttons.append(button)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.adjustSize()


class _Pane3D(QWidget):
    """The 3D side: a strip of controls and notices above the view — or, when there
    is no view, a message in its place. Says when it is shown and hidden."""

    showing_changed = pyqtSignal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(MIN_PANE_WIDTH)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.strip = QWidget(self)
        self.strip_layout = QHBoxLayout(self.strip)
        self.strip_layout.setContentsMargins(4, 2, 4, 2)
        self.strip_layout.setSpacing(6)
        outer.addWidget(self.strip)
        self.body = QStackedWidget(self)
        outer.addWidget(self.body, 1)
        # page 0: a message instead of a view (nothing built yet, or it could not be)
        self.message_page = QWidget(self.body)
        message_layout = QVBoxLayout(self.message_page)
        message_layout.addStretch(1)
        self.message_label = QLabel("", self.message_page)
        # PLAIN text, said out loud: this label shows an exception's message, and a
        # QLabel left on AutoText would render markup found in it (§8.11)
        self.message_label.setTextFormat(Qt.TextFormat.PlainText)
        self.message_label.setWordWrap(True)
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        message_layout.addWidget(self.message_label)
        self.message_button = QPushButton("", self.message_page)
        self.message_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        message_layout.addWidget(self.message_button, 0, Qt.AlignmentFlag.AlignHCenter)
        message_layout.addStretch(1)
        self.body.addWidget(self.message_page)
        self.engine_widget: QWidget | None = None

    def show_engine(self, widget: QWidget) -> None:
        self.engine_widget = widget
        self.body.addWidget(widget)
        self.body.setCurrentWidget(widget)

    def show_message(self, text: str, button_text: str = "") -> None:
        self.message_label.setText(text)
        self.message_button.setText(button_text)
        self.message_button.setVisible(bool(button_text))
        self.message_button.setEnabled(True)
        self.body.setCurrentWidget(self.message_page)

    def drop_engine(self) -> None:
        widget, self.engine_widget = self.engine_widget, None
        if widget is not None:
            self.body.removeWidget(widget)

    def showEvent(self, event: QShowEvent | None) -> None:  # noqa: N802 - Qt's name
        super().showEvent(event)
        self.showing_changed.emit(True)

    def hideEvent(self, event: QHideEvent | None) -> None:  # noqa: N802 - Qt's name
        super().hideEvent(event)
        self.showing_changed.emit(False)


class Workspace(QWidget):
    """The workspace widget (module docstring). One per main window."""

    mode_changed = pyqtSignal(object)
    #: The 3D pane's state changed (``View3DState``).
    view3d_state_changed = pyqtSignal(object)

    def __init__(self, canvas_view: QWidget, *, engine_factory: EngineFactory | None = None,
                 guard: Init3DGuard | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._canvas = canvas_view
        #: THE TEST SEAM: what builds the engine on first use. Replace it BEFORE the
        #: first switch to 3D / Split (``(host, sink) = factory(prefer_software)``).
        self.engine_factory: EngineFactory = engine_factory or quick3d_engine
        self._guard = guard
        self._mode = WorkspaceMode.PLAN
        self._state = View3DState.NOT_STARTED
        self._chrome_visible = True
        self._scene: QGraphicsScene | None = None
        self._clock: SimClock | None = None
        self._location: LocationProvider = lambda: None
        self._command_manager: Any = None
        self._ui_state: Any = None
        self._pane: _Pane3D | None = None
        self._host: EngineHost | None = None
        self._controller: Scene3DController | None = None
        self._input: OrbitInput | None = None
        self._problems_dialog: View3DProblemsDialog | None = None
        #: The session's one camera: created with the workspace, so its pose survives
        #: hide / show, mode switches and an engine that had to be started again.
        self._camera = OrbitCamera()
        self._camera_stepped_at = 0.0
        self._first_frame_seen = False
        self._qml_warnings_seen = 0
        self._split_sizes: list[int] | None = None
        self._window_watched: QWidget | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.addWidget(canvas_view)
        self._splitter.splitterMoved.connect(self._remember_split)
        layout.addWidget(self._splitter)

        self._camera_timer = QTimer(self)
        self._camera_timer.setInterval(CAMERA_STEP_MS)
        self._camera_timer.timeout.connect(self._step_camera)
        self._health_timer = QTimer(self)
        self._health_timer.timeout.connect(self._poll_health)

        self._build_actions()
        # on the widget itself too: the shortcuts work whichever button row is shown
        self.addActions(self.mode_actions())
        self._floating = ModeSwitch(self.mode_actions(), self)
        self._place_floating()

    # ── wiring ───────────────────────────────────────────────────────────────

    def connect_plan(self, scene: QGraphicsScene, clock: SimClock, location: LocationProvider,
                     command_manager: Any, ui_state: Any = None) -> None:
        """What the 3D pipeline reads: the canvas scene, the one sim clock, the garden
        location, the undo stack (its ``stack_changed`` marks the plan dirty) and,
        optionally, the store the Split position is kept in."""
        self._scene, self._clock, self._location = scene, clock, location
        self._command_manager, self._ui_state = command_manager, ui_state

    def _build_actions(self) -> None:
        self._mode_group = QActionGroup(self)
        self._mode_group.setExclusive(True)
        specs = (
            (WorkspaceMode.PLAN, self.tr("&Plan"), "Alt+1",
             self.tr("Show the plan only")),
            (WorkspaceMode.VIEW3D, self.tr("&3D"), "Alt+2",
             self.tr("Show the garden in 3D")),
            (WorkspaceMode.SPLIT, self.tr("&Split"), "Alt+3",
             self.tr("Show the plan and the 3D view side by side")),
        )
        self._mode_actions: dict[WorkspaceMode, QAction] = {}
        for mode, text, shortcut, tip in specs:
            action = QAction(text, self)
            action.setCheckable(True)
            action.setShortcut(QKeySequence(shortcut))
            action.setStatusTip(tip)
            action.setToolTip(self.tr("{tip} ({shortcut})").format(tip=tip, shortcut=shortcut))
            action.triggered.connect(lambda _checked=False, m=mode: self.set_mode(m))
            self._mode_group.addAction(action)
            self._mode_actions[mode] = action
        self._mode_actions[WorkspaceMode.PLAN].setChecked(True)
        self._reset_action = QAction(self.tr("&Reset 3D View"), self)
        self._reset_action.setStatusTip(self.tr("Show the whole garden in the 3D view again"))
        self._reset_action.setToolTip(self.tr("Show the whole garden again (Home)"))
        self._reset_action.triggered.connect(lambda _checked=False: self.reset_view())
        self._reset_action.setEnabled(False)

    def mode_actions(self) -> list[QAction]:
        """Plan, 3D, Split — checkable and exclusive; the buttons and the menu share them."""
        return [self._mode_actions[mode] for mode in WorkspaceMode]

    @property
    def reset_view_action(self) -> QAction:
        return self._reset_action

    def create_menu(self, parent: QWidget | None = None) -> QMenu:
        """The View menu's entry: a submenu with the three modes and "Reset 3D View"."""
        menu = QMenu(self.tr("&Workspace"), parent)
        menu.addActions(self.mode_actions())
        menu.addSeparator()
        menu.addAction(self._reset_action)
        return menu

    # ── reads ────────────────────────────────────────────────────────────────

    @property
    def mode(self) -> WorkspaceMode:
        return self._mode

    @property
    def view3d_state(self) -> View3DState:
        return self._state

    @property
    def camera(self) -> OrbitCamera:
        return self._camera

    @property
    def controller(self) -> Scene3DController | None:
        """The sync controller — None until 3D was first opened (and after a failed start)."""
        return self._controller

    @property
    def host(self) -> EngineHost | None:
        return self._host

    @property
    def guard(self) -> Init3DGuard:
        if self._guard is None:
            self._guard = Init3DGuard(default_directory())
        return self._guard

    @property
    def splitter(self) -> QSplitter:
        return self._splitter

    @property
    def pane(self) -> QWidget | None:
        """The 3D pane (None until it was first asked for)."""
        return self._pane

    def status_text(self) -> str:
        return self._status_label.text() if self._pane is not None else ""

    def message_text(self) -> str:
        """The message shown INSTEAD of the 3D view ("" while a view is shown)."""
        pane = self._pane
        if pane is None or pane.body.currentWidget() is not pane.message_page:
            return ""
        return pane.message_label.text()

    def compat_text(self) -> str:
        if self._pane is None or self._compat_label.isHidden():
            return ""
        return self._compat_label.text()

    def problems_text(self) -> str:
        if self._pane is None or self._problems_button.isHidden():
            return ""
        return self._problems_button.text()

    # ── modes ────────────────────────────────────────────────────────────────

    def set_mode(self, mode: WorkspaceMode) -> None:
        self._mode_actions[mode].setChecked(True)
        if mode is self._mode:
            return
        if self._mode is WorkspaceMode.SPLIT:
            self._remember_split()
        self._mode = mode
        if mode is not WorkspaceMode.PLAN:
            self._ensure_pane()
        pane = self._pane
        # show before hide: the splitter is never without a visible side
        if mode is not WorkspaceMode.VIEW3D:
            self._canvas.setVisible(True)
        if pane is not None:
            pane.setVisible(mode is not WorkspaceMode.PLAN)
            self._pane_switch.setVisible(mode is WorkspaceMode.VIEW3D)
        if mode is WorkspaceMode.VIEW3D:
            self._canvas.setVisible(False)
        if mode is WorkspaceMode.SPLIT:
            self._apply_split()
        self._place_floating()
        self.mode_changed.emit(mode)

    def set_chrome_visible(self, visible: bool) -> None:
        """Fullscreen preview hides the workspace's own controls with the rest of the UI."""
        self._chrome_visible = visible
        if self._pane is not None:
            self._pane.strip.setVisible(visible)
        self._place_floating()

    def reset_view(self) -> None:
        """Frame the whole plan again (the camera glides there)."""
        controller = self._controller
        if controller is None:
            return
        self._camera.frame(controller.plan_bounds(), self._aspect())
        self._start_camera()

    def _place_floating(self) -> None:
        visible = self._chrome_visible and self._mode is not WorkspaceMode.VIEW3D
        self._floating.setVisible(visible)
        if visible:
            self._floating.move(_FLOAT_OFFSET, _FLOAT_OFFSET)
            self._floating.raise_()

    def _apply_split(self) -> None:
        sizes = self._split_sizes
        if sizes is None and self._ui_state is not None and self._ui_state.restore_splitter(
                _SPLITTER_KEY, self._splitter):
            sizes = self._splitter.sizes()
        if sizes is None or len(sizes) != 2 or min(sizes) <= 0:
            half = max(self._splitter.width() // 2, MIN_PANE_WIDTH)
            sizes = [half, half]
        self._splitter.setSizes(sizes)
        self._split_sizes = self._splitter.sizes()

    def _remember_split(self, *_arguments: object) -> None:
        if self._mode is WorkspaceMode.SPLIT and self._pane is not None:
            self._split_sizes = self._splitter.sizes()

    def save_state(self) -> None:
        """Keep the Split position for the next session (only a real Split is kept)."""
        if (self._ui_state is not None and self._pane is not None
                and self._mode is WorkspaceMode.SPLIT):
            self._ui_state.save_splitter(_SPLITTER_KEY, self._splitter)

    # ── the 3D pane, built on first use ──────────────────────────────────────

    def _ensure_pane(self) -> None:
        if self._pane is None:
            self._build_pane()
        if self._state is View3DState.NOT_STARTED:
            self._start_engine()

    def _build_pane(self) -> None:
        pane = _Pane3D(self._splitter)
        strip = pane.strip_layout
        self._pane_switch = ModeSwitch(self.mode_actions(), pane.strip)
        strip.addWidget(self._pane_switch)
        self._reset_button = QToolButton(pane.strip)
        self._reset_button.setDefaultAction(self._reset_action)
        self._reset_button.setAutoRaise(True)
        self._reset_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        strip.addWidget(self._reset_button)
        self._status_label = QLabel("", pane.strip)
        self._status_label.setTextFormat(Qt.TextFormat.PlainText)
        strip.addWidget(self._status_label)
        strip.addStretch(1)
        self._problems_button = QToolButton(pane.strip)
        self._problems_button.setAutoRaise(True)
        self._problems_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._problems_button.setToolTip(self.tr("Show which objects are missing from the 3D view"))
        self._problems_button.clicked.connect(self.show_problems)
        self._problems_button.hide()
        strip.addWidget(self._problems_button)
        self._compat_label = QLabel("", pane.strip)
        self._compat_label.setTextFormat(Qt.TextFormat.PlainText)
        self._compat_label.hide()
        strip.addWidget(self._compat_label)
        self._compat_button = QToolButton(pane.strip)
        self._compat_button.setAutoRaise(True)
        self._compat_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._compat_button.clicked.connect(self._on_compat_button)
        self._compat_button.hide()
        strip.addWidget(self._compat_button)
        pane.message_button.clicked.connect(self._on_message_button)
        pane.showing_changed.connect(self._on_pane_showing)
        pane.strip.setVisible(self._chrome_visible)
        pane.hide()
        self._splitter.addWidget(pane)
        self._splitter.setCollapsible(self._splitter.indexOf(pane), False)
        self._pane = pane

    def _start_engine(self) -> None:
        """Build the engine and the pipeline — the first time 3D is asked for, and
        again after "Try again". Opening 3D never takes the 2D app down: whatever
        this raises becomes a message in the pane."""
        pane, scene, clock = self._pane, self._scene, self._clock
        if pane is None:
            return
        if scene is None or clock is None:
            _LOG.error("The 3D workspace was asked for 3D before connect_plan() gave it a plan")
            return
        guard = self.guard
        software = guard.begin() is RenderMode.SOFTWARE
        host: EngineHost | None = None
        orbit: OrbitInput | None = None
        try:
            from .orbit_input import OrbitInput
            from .sync_controller import Scene3DController

            host, sink = self.engine_factory(software)
            container = host.container(pane)
            orbit = OrbitInput(self._camera, self._view_height, parent=self)
            host.install_event_filter(orbit)
            controller = Scene3DController(scene, clock, self._location, sink, parent=self)
        except Exception as error:  # noqa: BLE001 - an import, the scene file, anything
            guard.init_failed()
            _LOG.exception("The 3D view could not be started")
            if orbit is not None:
                orbit.deleteLater()
            if host is not None:
                self._close_host(host)
            self._set_state(View3DState.FAILED)
            pane.show_message(
                self.tr("The 3D view could not be started. The rest of the application "
                        "is not affected.\n\nDetails: {error}").format(error=clip_text(error)),
                self.tr("Try Again"))
            return
        self._host, self._controller, self._input = host, controller, orbit
        orbit.moved.connect(self._start_camera)
        orbit.reset_requested.connect(self.reset_view)
        if self._command_manager is not None:
            self._command_manager.stack_changed.connect(controller.mark_dirty)
        controller.applied.connect(self._on_applied)
        controller.problems_changed.connect(self._update_problems)
        controller.document_shown.connect(self._on_document_shown)
        controller.failed.connect(self._on_sync_failed)
        pane.show_engine(container)
        self._first_frame_seen = False
        self._qml_warnings_seen = 0
        self._reset_action.setEnabled(True)
        self._set_state(View3DState.STARTING)
        self._update_compat_notice()
        self._watch_window()
        self._on_pane_showing(pane.isVisible())

    # ── shown / hidden ───────────────────────────────────────────────────────

    def _pane_is_showing(self) -> bool:
        pane = self._pane
        if pane is None or not pane.isVisible():
            return False
        window = self.window()
        return not (window is not None and window.isMinimized())

    def _on_pane_showing(self, _shown: bool = False) -> None:
        showing = self._pane_is_showing()
        controller = self._controller
        if controller is None or self._host is None:
            self._health_timer.stop()
            self._camera_timer.stop()
            return
        if showing and controller.dirty and not controller.active:
            # the first build of a large plan can take a second: say so, paint, then build
            self._status_label.setText(self.tr("Building the 3D view…"))
        controller.set_active(showing)
        if showing:
            self._health_timer.start(HEALTH_SLOW_MS if self._first_frame_seen else HEALTH_FAST_MS)
            self._apply_camera()
            self._start_camera()
        else:
            self._health_timer.stop()
            self._camera_timer.stop()

    def _watch_window(self) -> None:
        window = self.window()
        if window is not None and window is not self._window_watched:
            if self._window_watched is not None:
                self._window_watched.removeEventFilter(self)
            window.installEventFilter(self)
            self._window_watched = window

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:  # noqa: N802
        # a minimised window still calls its children "visible": the pipeline pauses anyway
        if (event is not None and watched is self._window_watched
                and event.type() == QEvent.Type.WindowStateChange):
            self._on_pane_showing()
        return False

    # ── camera ───────────────────────────────────────────────────────────────

    def _view_height(self) -> float:
        host = self._host
        if host is None:
            return 0.0
        try:
            return float(host.view_size()[1])
        except Exception:  # noqa: BLE001 - a host that is gone has no size
            return 0.0

    def _aspect(self) -> float:
        host = self._host
        if host is not None:
            try:
                width, height = host.view_size()
                if width > 0.0 and height > 0.0:
                    return float(width) / float(height)
            except Exception:  # noqa: BLE001 - as above
                _LOG.debug("3D workspace: no view size for the aspect", exc_info=True)
        return 16.0 / 9.0

    def _apply_camera(self) -> None:
        host = self._host
        if host is None:
            return
        pose = self._camera.pose
        try:
            host.set_camera(pose.eye, pose.target_scene, FOV_DEG)
        except Exception:  # noqa: BLE001 - the health poll reports a host that died
            _LOG.debug("3D workspace: the camera could not be set", exc_info=True)

    def _start_camera(self) -> None:
        if self._camera.moving and self._pane_is_showing() and not self._camera_timer.isActive():
            self._camera_stepped_at = time.perf_counter()
            self._camera_timer.start()

    def _step_camera(self) -> None:
        now = time.perf_counter()
        moving = self._camera.step(now - self._camera_stepped_at)
        self._camera_stepped_at = now
        self._apply_camera()
        if not moving or not self._pane_is_showing():
            self._camera_timer.stop()  # at rest: no idle wake-ups

    def _on_document_shown(self) -> None:
        """The first pass of a document is in the engine: frame it, at once."""
        controller = self._controller
        if controller is None:
            return
        self._camera.frame(controller.plan_bounds(), self._aspect(), snap=True)
        self._apply_camera()

    # ── the pipeline's reports ───────────────────────────────────────────────

    def document_changed(self) -> None:
        """A new, opened or recovered plan replaced the scene (the main window says so)."""
        if self._controller is not None:
            self._controller.document_changed()

    def location_changed(self, *_arguments: object) -> None:
        if self._controller is not None:
            self._controller.refresh_sun()

    def _on_applied(self, _report: TickReport) -> None:
        self._status_label.setText("")

    def _on_sync_failed(self, _message: str) -> None:
        self._status_label.setText(self.tr(
            "The 3D view could not be updated. It will try again with the next change."))

    def _update_problems(self) -> None:
        controller = self._controller
        count = controller.problem_count if controller is not None else 0
        if count == 0:
            self._problems_button.hide()
        else:
            text = (self.tr("1 object is not shown in 3D") if count == 1
                    else self.tr("{count} objects are not shown in 3D").format(count=count))
            self._problems_button.setText(text)
            self._problems_button.show()
        dialog = self._problems_dialog
        if dialog is not None and dialog.isVisible():
            self._fill_problems(dialog)

    def show_problems(self) -> None:
        """Open (or raise) the list of what 3D could not show."""
        from .problems_dialog import View3DProblemsDialog

        dialog = self._problems_dialog
        if dialog is None:
            dialog = View3DProblemsDialog(self)
            dialog.item_chosen.connect(self.select_item)
            self._problems_dialog = dialog
        self._fill_problems(dialog)
        dialog.show()
        dialog.raise_()

    @property
    def problems_dialog(self) -> View3DProblemsDialog | None:
        return self._problems_dialog

    def _fill_problems(self, dialog: View3DProblemsDialog) -> None:
        from .problems_dialog import problems_of

        controller = self._controller
        if controller is None:
            dialog.set_problems((), self._item_name)
            return
        dialog.set_problems(problems_of(controller.skipped, controller.failures), self._item_name)

    def _find_item(self, item_id: str) -> Any:
        scene = self._scene
        if scene is None:
            return None
        for item in scene.items():
            if str(getattr(item, "item_id", "")) == item_id:
                return item
        return None

    def _item_name(self, item_id: str) -> str:
        return str(getattr(self._find_item(item_id), "name", "") or "")

    def select_item(self, item_id: str) -> None:
        """Select the item on the plan (a row of the problems list was activated)."""
        scene, item = self._scene, self._find_item(item_id)
        if scene is None or item is None:
            return
        scene.clearSelection()
        item.setSelected(True)
        center_on = getattr(self._canvas, "centerOn", None)
        if callable(center_on) and self._canvas.isVisible():
            center_on(item)

    # ── health: first frame, graphics failure, QML warnings ──────────────────

    def _poll_health(self) -> None:
        host = self._host
        if host is None:
            self._health_timer.stop()
            return
        health = host.health()
        error = getattr(health, "scene_graph_error", None)
        if error:
            self._graphics_failed(str(error))
            return
        if not self._first_frame_seen and int(getattr(health, "frames_presented", 0)) >= 1:
            self._first_frame_seen = True
            self.guard.confirm_first_frame()
            self._set_state(View3DState.RUNNING)
            self._health_timer.setInterval(HEALTH_SLOW_MS)
        warnings = int(getattr(health, "qml_warning_count", 0))
        if warnings > self._qml_warnings_seen:
            self._qml_warnings_seen = warnings
            self._status_label.setText(self.tr(
                "The 3D view reported a problem. Details are in the log file."))

    def _graphics_failed(self, error: str) -> None:
        """Qt could not create the graphics device: this host will never show a frame.
        Not a crash — the marker goes; the view is closed for the session, no retry."""
        guard, pane = self.guard, self._pane
        guard.graphics_failed()
        _LOG.error("3D is unavailable on this graphics device: %r", error)
        self._stop_engine()
        self._set_state(View3DState.UNAVAILABLE)
        if pane is None:
            return
        offer = guard.mode is RenderMode.HARDWARE and not guard.software_requested()
        pane.show_message(
            self.tr("3D is unavailable on this graphics device. The rest of the application "
                    "is not affected.\n\nDetails: {error}").format(error=clip_text(error)),
            self.tr("Use Compatibility Mode Next Time") if offer else "")
        self._hide_compat_notice()

    def _update_compat_notice(self) -> None:
        guard = self.guard
        if guard.mode is not RenderMode.SOFTWARE:
            self._hide_compat_notice()
            return
        if guard.reason is ModeReason.PREVIOUS_START_DIED:
            text = self.tr("3D is running in compatibility mode (software rendering) because "
                           "it did not start properly last time.")
        else:
            text = self.tr("3D is running in compatibility mode (software rendering).")
        self._compat_label.setText(text)
        self._compat_label.show()
        self._compat_button.setText(self.tr("Try Hardware Next Time"))
        self._compat_button.setVisible(guard.software_requested())

    def _hide_compat_notice(self) -> None:
        self._compat_label.hide()
        self._compat_button.hide()

    def _on_compat_button(self) -> None:
        self.guard.try_hardware_next_time()
        self._compat_button.hide()
        self._compat_label.setText(self.tr(
            "Hardware rendering will be tried the next time the application starts."))

    def _on_message_button(self) -> None:
        pane = self._pane
        if pane is None:
            return
        if self._state is View3DState.FAILED:
            self._set_state(View3DState.NOT_STARTED)
            self._start_engine()
        elif self._state is View3DState.UNAVAILABLE:
            self.guard.use_software_next_time()
            pane.message_button.setEnabled(False)
            pane.message_button.setText(self.tr(
                "Compatibility mode will be used the next time the application starts."))

    # ── teardown ─────────────────────────────────────────────────────────────

    def _close_host(self, host: EngineHost) -> None:
        try:
            host.close()
        except Exception:  # noqa: BLE001 - closing must never stop a shutdown
            _LOG.exception("3D workspace: the engine host did not close cleanly")

    def _stop_engine(self) -> None:
        self._camera_timer.stop()
        self._health_timer.stop()
        controller, self._controller = self._controller, None
        if controller is not None:
            controller.shutdown()
            if self._command_manager is not None:
                try:
                    self._command_manager.stack_changed.disconnect(controller.mark_dirty)
                except (RuntimeError, TypeError):
                    _LOG.debug("3D workspace: the undo stack was already disconnected")
            controller.deleteLater()
        host, self._host = self._host, None
        orbit, self._input = self._input, None
        if orbit is not None:
            orbit.deleteLater()
        if self._pane is not None:
            self._pane.drop_engine()
        if host is not None:
            self._close_host(host)
        self._reset_action.setEnabled(False)

    def shutdown(self) -> None:
        """The main window is closing. Order: the pipeline stops, the pending start
        marker goes (a clean close is not a crash), the host destroys its Models
        and its window — while the main window still exists."""
        self.save_state()
        if self._guard is not None:
            self._guard.abandon()
        dialog, self._problems_dialog = self._problems_dialog, None
        if dialog is not None:
            dialog.close()
        self._stop_engine()
        if self._window_watched is not None:
            self._window_watched.removeEventFilter(self)
            self._window_watched = None

    def _set_state(self, state: View3DState) -> None:
        if state is not self._state:
            self._state = state
            self.view3d_state_changed.emit(state)
