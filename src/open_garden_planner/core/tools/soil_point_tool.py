"""Place one soil observation at a user-selected plan coordinate."""
from PyQt6.QtCore import QT_TR_NOOP, QPointF, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent

from .base_tool import BaseTool, ToolType


class SoilPointTool(BaseTool):
    tool_type = ToolType.SOIL_POINT
    display_name = QT_TR_NOOP("Soil sampling point")
    shortcut = ""
    cursor = Qt.CursorShape.CrossCursor
    skip_anchor_snap = True

    def mouse_press(self, event: QMouseEvent, scene_pos: QPointF) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        return self.commit_typed_coordinate(scene_pos)

    def commit_typed_coordinate(self, point: QPointF) -> bool:
        self._view.set_active_tool(ToolType.SELECT)
        self._view.soil_point_requested.emit(point.x(), point.y())
        return True

    def mouse_move(self, _event: QMouseEvent, _scene_pos: QPointF) -> bool:
        return False

    def mouse_release(self, _event: QMouseEvent, _scene_pos: QPointF) -> bool:
        return False

    def key_press(self, event: QKeyEvent) -> bool:
        if event.key() == Qt.Key.Key_Escape:
            self._view.set_active_tool(ToolType.SELECT)
            return True
        return False
