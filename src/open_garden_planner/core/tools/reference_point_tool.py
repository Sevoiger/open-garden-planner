"""One-click placement of a named reference point."""

from PyQt6.QtCore import Qt

from .base_tool import BaseTool, ToolType


class ReferencePointTool(BaseTool):
    tool_type = ToolType.REFERENCE_POINT
    display_name = "Reference point"
    shortcut = ""
    cursor = Qt.CursorShape.CrossCursor

    def mouse_press(self, event, scene_pos):
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        self.commit_typed_coordinate(scene_pos)
        return True

    def commit_typed_coordinate(self, point):
        from open_garden_planner.ui.reference_points import place_reference_point

        placed = place_reference_point(self.view, point)
        self.view.set_active_tool(ToolType.SELECT)
        return placed

    def mouse_move(self, _event, _scene_pos):
        return False

    def mouse_release(self, _event, _scene_pos):
        return False

    def key_press(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.view.set_active_tool(ToolType.SELECT)
            return True
        return False
