"""A reference name is also a screen-sized drag target for its point."""

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsSceneContextMenuEvent,
    QGraphicsSceneMouseEvent,
    QGraphicsSimpleTextItem,
    QStyleOptionGraphicsItem,
    QWidget,
)


class ReferencePointLabel(QGraphicsSimpleTextItem):
    def __init__(self, text: str, parent: QGraphicsItem) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._origins: dict[QGraphicsItem, QPointF] = {}
        self._press_scene = QPointF()

    def paint(self, painter: QPainter | None, option: QStyleOptionGraphicsItem | None,
              widget: QWidget | None = None) -> None:
        if getattr(self.scene(), "_rendering_minimap", False):
            return
        super().paint(painter, option, widget)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent | None) -> None:
        parent, scene = self.parentItem(), self.scene()
        if (event is None or parent is None or scene is None
                or event.button() != Qt.MouseButton.LeftButton
                or not parent.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable):
            super().mousePressEvent(event)
            return
        if event.modifiers() & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier):
            parent.setSelected(not parent.isSelected())
            if not parent.isSelected():
                self._origins.clear()
                event.accept()
                return
        elif not parent.isSelected():
            scene.clearSelection()
        parent.setSelected(True)
        self._press_scene = event.scenePos()
        self._origins = {item: QPointF(item.pos()) for item in scene.selectedItems()}
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent | None) -> None:
        if event is None or not self._origins:
            super().mouseMoveEvent(event)
            return
        from .resize_handle import _is_item_fixed

        for item, origin in self._origins.items():
            if not item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable or _is_item_fixed(item):
                continue
            parent = item.parentItem()
            delta = (parent.mapFromScene(event.scenePos()) - parent.mapFromScene(self._press_scene)
                     if parent else event.scenePos() - self._press_scene)
            item.setPos(origin + delta)
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent | None) -> None:
        self._origins.clear()
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if event is not None:
            event.accept()

    def contextMenuEvent(self, event: QGraphicsSceneContextMenuEvent | None) -> None:
        parent = self.parentItem()
        if parent is not None and event is not None:
            parent.contextMenuEvent(event)
            event.accept()
            return
        super().contextMenuEvent(event)

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent | None) -> None:
        parent = self.parentItem()
        if (event is not None and event.button() == Qt.MouseButton.LeftButton and parent is not None
                and parent.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable):
            # The point owns editing and the existing dialog/command path.
            parent.mouseDoubleClickEvent(event)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
