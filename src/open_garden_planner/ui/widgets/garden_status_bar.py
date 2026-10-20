"""Keep status instruments within the available window width.

Coordinates, typed input and zoom remain permanent. Lower priority details are
revealed as space permits. No font scaling or global screen geometry assumptions.
"""

from PyQt6.QtCore import QEvent, QTimer, pyqtSignal
from PyQt6.QtGui import QResizeEvent
from PyQt6.QtWidgets import QLabel, QSizeGrip, QStatusBar, QWidget


class StatusLabel(QLabel):
    """Notify the status bar even while this label is explicitly hidden."""

    text_changed = pyqtSignal()

    def setText(self, text: str | None) -> None:  # noqa: N802
        super().setText(text)
        self.text_changed.emit()


class GardenStatusBar(QStatusBar):
    """Status bar whose optional icon/label pairs never crowd its input field."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._segments: list[tuple[QWidget, QWidget]] = []
        self._fitting = False
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self.fit_segments)

    def add_optional_segment(self, icon: QWidget, label: StatusLabel) -> None:
        """Register an existing permanent pair in descending priority order."""
        self._segments.append((icon, label))
        label.text_changed.connect(self.schedule_fit)
        self.schedule_fit()

    def schedule_fit(self) -> None:
        """Coalesce value changes, including changes to currently hidden labels."""
        self._fit_timer.start(0)

    def resizeEvent(self, event: QResizeEvent | None) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_timer.start(0)

    def event(self, event: QEvent | None) -> bool:
        result = super().event(event)
        if (event is not None and event.type() == QEvent.Type.LayoutRequest
                and hasattr(self, "_fit_timer")):
            self._fit_timer.start(0)
        return result

    def fit_segments(self) -> None:
        """Fit complete segments, reserving room for transient status messages."""
        if self._fitting:
            return
        self._fitting = True
        try:
            optional = {w for pair in self._segments for w in pair}
            # All permanent children share the status bar's internal layout.
            # Resolve children rather than duplicating the coordinate input's
            # sizing rules (it is inserted after this bar is constructed).
            permanent = [
                w for w in self.findChildren(QWidget)
                if w.parentWidget() is self and w not in optional
                and not w.isHidden() and not isinstance(w, QSizeGrip)
            ]
            used = 80 + sum(max(w.minimumWidth(), w.sizeHint().width()) + 6 for w in permanent)
            for icon, label in self._segments:
                needed = icon.sizeHint().width() + max(label.minimumWidth(), label.sizeHint().width()) + 12
                fits = used + needed <= self.width()
                icon.setVisible(fits)
                label.setVisible(fits)
                if fits:
                    used += needed
        finally:
            self._fitting = False
