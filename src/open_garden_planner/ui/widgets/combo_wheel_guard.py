"""Scroll forms without accidentally editing their closed drop-down fields."""

from PyQt6.QtCore import QEvent, QObject, QPointF
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QAbstractScrollArea, QApplication, QComboBox, QWidget


class ComboWheelGuard(QObject):
    """Application event filter; open choice lists retain normal wheel scrolling."""

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:  # noqa: N802
        if isinstance(watched, QComboBox) and isinstance(event, QWheelEvent):
            view = watched.view()
            if view is not None and view.isVisible():
                return super().eventFilter(watched, event)
            # Ignoring the event alone does not reliably scroll its enclosing
            # form. Forward the original gesture (including trackpad pixels and
            # phase) to the nearest scroll viewport, never to a sibling editor.
            parent: QWidget | None = watched.parentWidget()
            while parent is not None and not isinstance(parent, QAbstractScrollArea):
                parent = parent.parentWidget()
            if isinstance(parent, QAbstractScrollArea):
                viewport = parent.viewport()
                if viewport is not None:
                    forwarded = QWheelEvent(
                        QPointF(viewport.mapFromGlobal(event.globalPosition().toPoint())),
                        event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                        event.buttons(), event.modifiers(), event.phase(), event.inverted(),
                        device=event.pointingDevice(),
                    )
                    QApplication.sendEvent(viewport, forwarded)
                    if not forwarded.isAccepted() and not event.pixelDelta().isNull():
                        # QScrollArea's scrollbar ignores pixel-only gestures
                        # on some backends. Pixels already carry their direction.
                        delta = event.pixelDelta()
                        for scrollbar, amount in (
                            (parent.horizontalScrollBar(), delta.x()),
                            (parent.verticalScrollBar(), delta.y()),
                        ):
                            if scrollbar is not None:
                                scrollbar.setValue(scrollbar.value() - amount)
            event.accept()
            return True
        return super().eventFilter(watched, event)
