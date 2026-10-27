"""One-click placement of a named reference point."""

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent

from .base_tool import BaseTool, ToolType


class ReferencePointTool(BaseTool):
    tool_type = ToolType.REFERENCE_POINT
    display_name = "Reference point"
    shortcut = ""
    cursor = Qt.CursorShape.CrossCursor

    def mouse_press(self, event: QMouseEvent, scene_pos: QPointF) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        self.commit_typed_coordinate(scene_pos)
        return True

    def commit_typed_coordinate(self, point: QPointF) -> bool:
        from open_garden_planner.ui.reference_points import place_reference_point

        placed = place_reference_point(self.view, point)
        self.view.set_active_tool(ToolType.SELECT)
        return placed

    def mouse_move(self, _event: QMouseEvent, _scene_pos: QPointF) -> bool:
        return False

    def mouse_release(self, _event: QMouseEvent, _scene_pos: QPointF) -> bool:
        return False

    def key_press(self, event: QKeyEvent) -> bool:
        if event.key() == Qt.Key.Key_Escape:
            self.view.set_active_tool(ToolType.SELECT)
            return True
        return False
