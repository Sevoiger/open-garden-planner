"""Placement and editing of named surveying references using existing commands."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast
from uuid import UUID

from PyQt6.QtCore import QCoreApplication, QPointF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGraphicsItem,
    QGraphicsScene,
    QLabel,
    QLineEdit,
    QSpinBox,
)

from open_garden_planner.core.commands import (
    ChangePropertyCommand,
    CreateItemCommand,
    SetParentBedCommand,
)
from open_garden_planner.core.object_types import get_translated_display_name
from open_garden_planner.core.reference_points import is_reference_point
from open_garden_planner.ui.canvas.items import CircleItem, GardenItemMixin
from open_garden_planner.ui.dialogs.properties_dialog import ColorButton

if TYPE_CHECKING:
    from open_garden_planner.ui.canvas.canvas_view import CanvasView


@dataclass(frozen=True)
class PointProperties:
    name: str
    parent_id: UUID | None = None
    size_px: float = 16.0
    color: str = "#168c9e"
    stroke_width: float = 1.8

    @classmethod
    def from_item(cls, item: CircleItem) -> "PointProperties":
        return cls(item.name, item.parent_bed_id, item.radius * 2,
                   item.pen().color().name(QColor.NameFormat.HexArgb), item.pen().widthF())


def parent_name(item: GardenItemMixin) -> str:
    label = item.name or (get_translated_display_name(item.object_type) if item.object_type else "")
    return f"{label} [{str(item.item_id)[:8]}]"


def reference_status(item: CircleItem) -> str:
    parent_id = item.parent_bed_id
    if parent_id is None:
        return QCoreApplication.translate("ReferencePoints", "Independent point")
    scene = item.scene()
    parent = next((obj for obj in scene.items() if isinstance(obj, GardenItemMixin)
                   and obj.item_id == parent_id), None) if scene else None
    name = parent_name(parent) if isinstance(parent, GardenItemMixin) else str(parent_id)[:8]
    return QCoreApplication.translate("ReferencePoints", "Attached to: {name}").format(name=name)


def reference_summary(item: CircleItem) -> str:
    return QCoreApplication.translate(
        "ReferencePoints", "{name}\n{status}\nSymbol: {size} px; stroke: {width} px"
    ).format(name=item.name, status=reference_status(item), size=f"{item.radius * 2:g}",
             width=f"{item.pen().widthF():g}")


def draw_reference_links(view: "CanvasView", painter: QPainter) -> None:
    """Selection-only feedback is a view overlay, never document geometry."""
    painter.save()
    for item in view._canvas_scene.selectedItems():
        if not isinstance(item, CircleItem) or not is_reference_point(item) or item.parent_bed_id is None:
            continue
        parent = view._canvas_scene.find_item_by_id(item.parent_bed_id)
        if parent is None or not parent.isVisible():
            continue
        pen = QPen(item.pen().color(), 1.5, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(parent.mapToScene(parent.shape()))
        painter.drawLine(item.mapToScene(item.rect().center()), parent.sceneBoundingRect().center())
    painter.restore()


class CreateReferencePointCommand(CreateItemCommand):
    """Keep the explicit parent across undo/redo of point placement."""

    def __init__(self, scene: QGraphicsScene, item: CircleItem, parent_id: UUID | None) -> None:
        super().__init__(scene, item)
        self._parent_id = parent_id

    @property
    def description(self) -> str:
        return QCoreApplication.translate("ReferencePoints", "Create reference point")

    def execute(self) -> None:
        super().execute()
        SetParentBedCommand(self._scene, self._item, None, self._parent_id).execute()


def point_dialog(view: "CanvasView", item: CircleItem | None = None,
                 position: QPointF | None = None) -> PointProperties | None:
    """Choose a name and an explicit parent; never infer ownership by overlap."""
    dialog = QDialog(view)
    dialog.setWindowTitle(QCoreApplication.translate("ReferencePoints", "Reference point"))
    form = QFormLayout(dialog)
    options = PointProperties.from_item(item) if item else PointProperties("")
    name = QLineEdit(item.name if item else "", dialog)
    form.addRow(QCoreApplication.translate("ReferencePoints", "Name"), name)
    parents = QComboBox(dialog)
    parents.addItem(QCoreApplication.translate("ReferencePoints", "Independent point"), None)
    candidates = [
        obj for obj in view._canvas_scene.items()
        if isinstance(obj, GardenItemMixin) and isinstance(obj, QGraphicsItem) and (item is None or obj.item_id != item.item_id)
        and (
            (item is not None and obj.item_id == item.parent_bed_id)
            or (
                not is_reference_point(obj) and obj.parentItem() is None
                and obj.isVisible() and obj.flags() & obj.GraphicsItemFlag.ItemIsSelectable
            )
        )
    ]
    for obj in sorted(candidates, key=lambda obj: (obj.name, str(obj.item_id))):
        parents.addItem(parent_name(obj), obj.item_id)
    parent_id = item.parent_bed_id if item else None
    index = parents.findData(parent_id)
    parents.setCurrentIndex(max(0, index))
    form.addRow(QCoreApplication.translate("ReferencePoints", "Attached to"), parents)
    status = QLabel(dialog)
    status.setWordWrap(True)
    form.addRow(status)
    size = QSpinBox(dialog)
    size.setObjectName("reference_point_size")
    size.setRange(8, 64)
    size.setSuffix(" px")
    size.setValue(round(options.size_px))
    form.addRow(QCoreApplication.translate("ReferencePoints", "Symbol size"), size)
    color = ColorButton(QColor(options.color), dialog)
    color.setObjectName("reference_point_color")
    form.addRow(QCoreApplication.translate("ReferencePoints", "Color"), color)
    width = QDoubleSpinBox(dialog)
    width.setObjectName("reference_point_width")
    width.setRange(0.5, 8.0)
    width.setSingleStep(0.5)
    width.setSuffix(" px")
    width.setValue(options.stroke_width)
    form.addRow(QCoreApplication.translate("ReferencePoints", "Stroke width"), width)
    hint = QLabel(QCoreApplication.translate(
        "ReferencePoints", "Drag the point to move it. Double-click to edit its appearance or attachment."
    ), dialog)
    hint.setWordWrap(True)
    form.addRow(hint)

    # Draw a transient preview at the clicked coordinate. Neither this item
    # nor its parent outline can be saved, selected or caught by snapping.
    from PyQt6.QtWidgets import QGraphicsPathItem

    from open_garden_planner.core.tools.preview_z import PREVIEW_Z_LINE
    preview = QGraphicsPathItem()
    preview.setZValue(PREVIEW_Z_LINE)
    preview.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
    preview_pen = QPen(QColor(options.color), 1.5, Qt.PenStyle.DashLine)
    preview_pen.setCosmetic(True)
    preview.setPen(preview_pen)
    view._canvas_scene.addItem(preview)
    origin = item.mapToScene(item.rect().center()) if item else position

    def update_preview() -> None:
        parent_id = parents.currentData()
        parent = view._canvas_scene.find_item_by_id(parent_id) if parent_id else None
        path = QPainterPath()
        if parent is not None:
            path.addPath(parent.mapToScene(parent.shape()))
            if origin is not None:
                path.moveTo(origin)
                path.lineTo(parent.sceneBoundingRect().center())
            status.setText(QCoreApplication.translate("ReferencePoints", "Attached to: {name}").format(
                name=parents.currentText()))
        else:
            status.setText(QCoreApplication.translate("ReferencePoints", "Independent point"))
        if origin is not None:
            radius = 10 / max(view._zoom_factor, 0.01)
            path.moveTo(origin + QPointF(-radius, 0))
            path.lineTo(origin + QPointF(radius, 0))
            path.moveTo(origin + QPointF(0, -radius))
            path.lineTo(origin + QPointF(0, radius))
        preview.setPath(path)

    parents.currentIndexChanged.connect(update_preview)
    update_preview()
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        parent=dialog,
    )
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    form.addRow(buttons)
    name.setFocus()
    try:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return PointProperties(name.text().strip(), cast(UUID | None, parents.currentData()),
                               float(size.value()), color.color.name(QColor.NameFormat.HexArgb), width.value())
    finally:
        if preview.scene() is view._canvas_scene:
            view._canvas_scene.removeItem(preview)


def place_reference_point(view: "CanvasView", position: QPointF) -> bool:
    result = point_dialog(view, None, position)
    if result is None:
        return False
    parent_id = result.parent_id
    scene = view._canvas_scene
    parent = scene.find_item_by_id(parent_id) if parent_id else None
    layer_id = parent.layer_id if isinstance(parent, GardenItemMixin) else (scene.active_layer.id if scene.active_layer else None)
    item = CircleItem(
        position.x(), position.y(), result.size_px / 2, name=result.name,
        metadata={"reference_point": True}, layer_id=layer_id,
    )
    apply_appearance(item, result)
    view.command_manager.execute(CreateReferencePointCommand(scene, item, parent_id))
    scene.clearSelection()
    item.setSelected(True)
    view.set_status_message(reference_status(item))
    return True


def edit_reference_point(item: CircleItem) -> None:
    scene = item.scene()
    if scene is None or not scene.views():
        return
    view = cast("CanvasView", scene.views()[0])
    result = point_dialog(view, item)
    if result is None:
        return
    old = PointProperties.from_item(item)
    new = result
    if old == new:
        return

    def apply(point: CircleItem, value: PointProperties) -> None:
        apply_appearance(point, value)
        SetParentBedCommand(scene, point, point.parent_bed_id, value.parent_id).execute()

    command = ChangePropertyCommand(item, "Reference point", old, new, apply)
    view.command_manager.execute(command)


def apply_appearance(item: CircleItem, options: PointProperties) -> None:
    item.name = options.name
    item.set_radius_centered(options.size_px / 2)
    item.prepareGeometryChange()
    item.stroke_color = QColor(options.color)
    item.stroke_width = options.stroke_width
    pen = QPen(item.pen())
    pen.setColor(item.stroke_color)
    pen.setWidthF(options.stroke_width)
    item.setPen(pen)
    item.update()
