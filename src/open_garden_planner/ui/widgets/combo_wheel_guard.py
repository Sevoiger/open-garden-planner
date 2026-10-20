"""Keep wheel gestures from editing values or navigating popup menus."""

from PyQt6.QtCore import QEvent, QObject, QPointF
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QMenu,
    QScrollBar,
    QWidget,
)


class ComboWheelGuard(QObject):
    """Block field/menu wheel changes; preserve scrolling of enclosing forms."""

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:  # noqa: N802
        if not isinstance(event, QWheelEvent) or not isinstance(watched, QWidget):
            return super().eventFilter(watched, event)
        ancestor: QWidget | None = watched
        while ancestor is not None:
            if isinstance(ancestor, QMenu):
                event.accept()
                return True
            if isinstance(ancestor, QComboBox) and ancestor.view().isVisible():
                event.accept()
                return True
            ancestor = ancestor.parentWidget()
        if isinstance(watched, (QComboBox, QAbstractSpinBox)) or (
            isinstance(watched, QAbstractSlider) and not isinstance(watched, QScrollBar)
        ):
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
