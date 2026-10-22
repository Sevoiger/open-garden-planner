"""Two snapped picks define an infinite cutting axis; Shift constrains its angle."""

import contextlib
from copy import deepcopy
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from PyQt6.QtCore import QCoreApplication, QPointF, Qt
from PyQt6.QtGui import QColor, QKeyEvent, QMouseEvent, QPen
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsLineItem

from open_garden_planner.core.cad_geometry import snap_point_to_axis_step
from open_garden_planner.core.item_look import copy_visual_style
from open_garden_planner.core.shape_boolean import item_to_painter_path
from open_garden_planner.core.shape_split import can_split_shape, split_path
from open_garden_planner.core.tools.base_tool import BaseTool, ToolType
from open_garden_planner.core.tools.preview_z import PREVIEW_Z_LINE

if TYPE_CHECKING:
    from open_garden_planner.ui.canvas.canvas_view import CanvasView


class SplitTool(BaseTool):
    tool_type = ToolType.SPLIT
    display_name = QCoreApplication.translate('SplitTool', 'Split shape')
    shortcut = ''
    cursor = Qt.CursorShape.CrossCursor
    accepts_typed_coordinates = False
    # Keep BaseTool's shared anchor snap: both clicks and preview use it.

    def __init__(self, view: "CanvasView") -> None:
        super().__init__(view)
        self.source_id: UUID | None = None
        self._start: QPointF | None = None
        self._preview: QGraphicsLineItem | None = None
        self._scene = view._canvas_scene
        self._scene.document_replaced.connect(self.cancel)

    def mouse_press(self, event: QMouseEvent, scene_pos: QPointF) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        source = cast(Any, self._scene.find_item_by_id(self.source_id)) if self.source_id else None
        if source is None or not can_split_shape(source, self._scene):
            self._view.set_status_message(QCoreApplication.translate('SplitTool',
                'Choose an independent closed shape without constraints or linked data to split.'))
            self.cancel()
            self._view.set_active_tool(ToolType.SELECT)
            return True
        if self._start is None:
            self._start = QPointF(scene_pos)
            self._preview = QGraphicsLineItem()
            pen = QPen(QColor(25, 120, 220), 1.5, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            self._preview.setPen(pen)
            self._preview.setZValue(PREVIEW_Z_LINE)
            self._scene.addItem(self._preview)
            self._view.set_status_message(QCoreApplication.translate('SplitTool',
                'Click the second axis point; Shift constrains the angle. Esc cancels.'))
            return True
        end = self._end(scene_pos, event)
        path = item_to_painter_path(source)
        pieces = split_path(path, self._start, end) if path is not None else []
        if not pieces:
            self._view.set_status_message(QCoreApplication.translate('SplitTool',
                'The axis must cross the shape. Choose another second point.'))
            return True
        from open_garden_planner.core.commands import SplitShapeCommand
        from open_garden_planner.core.object_types import get_valid_types_for_shape
        from open_garden_planner.ui.canvas.items import PolygonItem

        results: list[QGraphicsItem] = []
        for polygon in pieces:
            # Work in the original local frame so the material keeps its orientation.
            item = PolygonItem.from_polygon(source.mapFromScene(polygon))
            item._apply_rotation(source.rotation())
            center = item.transformOriginPoint()
            item.setPos(source.mapToScene(center) - center)
            if source.object_type in get_valid_types_for_shape('polygon'):
                item.object_type = source.object_type
            item.name = source.name
            item.label_visible = source.label_visible
            item.layer_id = source.layer_id
            item.stack_order = source.stack_order
            item.metadata.update(deepcopy(source.metadata))
            copy_visual_style(source, item)
            item._setup_styling()
            results.append(item)
        self.cancel()
        self._view.command_manager.execute(SplitShapeCommand(self._scene, source, results))
        self._view.set_active_tool(ToolType.SELECT)
        return True

    def _end(self, point: QPointF, event: QMouseEvent) -> QPointF:
        if self._start is not None and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            return snap_point_to_axis_step(self._start, point, 45)
        return QPointF(point)

    def mouse_move(self, event: QMouseEvent, scene_pos: QPointF) -> bool:
        if self._start is None or self._preview is None:
            return True
        end = self._end(scene_pos, event)
        direction = end - self._start
        length = (direction.x() ** 2 + direction.y() ** 2) ** 0.5
        if length > 1e-6:
            extent = self._scene.sceneRect().width() + self._scene.sceneRect().height()
            direction *= extent / length
            a, b = self._start - direction, self._start + direction
            self._preview.setLine(a.x(), a.y(), b.x(), b.y())
        return True

    def mouse_release(self, _event: QMouseEvent, _scene_pos: QPointF) -> bool:
        return True

    def key_press(self, event: QKeyEvent) -> bool:
        if event.key() == Qt.Key.Key_Escape:
            self.cancel()
            self._view.set_active_tool(ToolType.SELECT)
            return True
        return False

    def cancel(self) -> None:
        if self._preview is not None:
            with contextlib.suppress(RuntimeError):
                if self._preview.scene() is not None:
                    self._scene.removeItem(self._preview)
        self._preview = None
        self._start = None
        self.source_id = None
