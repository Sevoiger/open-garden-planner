"""Placement and editing of named surveying references using existing commands."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast
from uuid import UUID

from PyQt6.QtCore import QCoreApplication, QPointF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGraphicsItem,
    QGraphicsScene,
    QLabel,
    QLineEdit,
    QMenu,
    QSpinBox,
)

from open_garden_planner.core.cad_geometry import point_to_segment_distance
from open_garden_planner.core.commands import (
    ChangePropertyCommand,
    CreateItemCommand,
    SetParentBedCommand,
)
from open_garden_planner.core.object_types import get_translated_display_name
from open_garden_planner.core.reference_points import is_reference_point
from open_garden_planner.core.snap.geometry import item_edges
from open_garden_planner.ui.canvas.items import CircleItem, GardenItemMixin, PolygonItem
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
    on_contour: bool = False

    @classmethod
    def from_item(cls, item: CircleItem) -> "PointProperties":
        return cls(item.name, item.parent_bed_id, item.radius * 2,
                   item.pen().color().name(QColor.NameFormat.HexArgb), item.pen().widthF(),
                   item.metadata.get("reference_point_on_contour") is True)


def nearest_contour_position(parent: QGraphicsItem, position: QPointF) -> tuple[QPointF, float] | None:
    """Project onto finite, transformed segments of the complete parent outline."""
    best: tuple[QPointF, float] | None = None
    for edge in item_edges(parent):
        distance, t = point_to_segment_distance(position, edge.p1(), edge.p2())
        if best is None or distance < best[1]:
            best = (edge.pointAt(t), distance)
    return best


def reference_contour_position(item: QGraphicsItem, proposed: QPointF) -> QPointF:
    """Return the permissible surveying coordinate for a contour-bound point."""
    if not is_reference_point(item) or not getattr(item, "metadata", {}).get("reference_point_on_contour"):
        return proposed
    scene = item.scene()
    parent_id = getattr(item, "parent_bed_id", None)
    if scene is None or parent_id is None:
        return proposed
    parent = next((obj for obj in scene.items() if isinstance(obj, GardenItemMixin)
                   and obj.item_id == parent_id), None)
    if parent is None:
        return proposed
    result = nearest_contour_position(parent, proposed)
    return result[0] if result is not None else proposed


def reference_contour_delta(item: QGraphicsItem, delta: QPointF) -> QPointF:
    if not is_reference_point(item) or not getattr(item, "metadata", {}).get("reference_point_on_contour"):
        return delta
    centre = item.mapToScene(QPointF())
    return reference_contour_position(item, centre + delta) - centre


def contour_translation_allowed(scene: QGraphicsScene, moves: list[tuple[QGraphicsItem, QPointF]]) -> bool:
    """Refuse a rigid contour move when existing geometric links cannot follow."""
    from open_garden_planner.agent_api.constraint_policy import translation_preserves_constraint
    from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
    if not isinstance(scene, CanvasScene):
        return True
    shifts = {str(it.item_id): (delta.x(), delta.y()) for it, delta in moves
              if isinstance(it, GardenItemMixin)}
    for it, delta in moves:
        if not getattr(it, "metadata", {}).get("reference_point_on_contour"):
            continue
        parent_id = getattr(it, "parent_bed_id", None)
        parent_delta = QPointF(*shifts.get(str(parent_id), (0.0, 0.0)))
        target = it.mapToScene(QPointF()) + delta
        permitted = reference_contour_position(it, target - parent_delta) + parent_delta
        if (target - permitted).manhattanLength() > 1e-6:
            return False
    affected = set()
    for it, _delta in moves:
        if isinstance(it, GardenItemMixin):
            affected.update(scene.constraint_graph.get_connected_component(it.item_id))
            affected.add(it.item_id)
    if not any(getattr(it, "metadata", {}).get("reference_point_on_contour")
               and getattr(it, "parent_bed_id", None)
               and getattr(it, "item_id", None) in affected for it in scene.items()):
        return True
    return all(translation_preserves_constraint(c.to_dict(), shifts)
               for c in scene.constraint_graph.constraints.values()
               if any(str(ref.item_id) in shifts for ref in (c.anchor_a, c.anchor_b, c.anchor_c)
                      if ref is not None))


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
    if item.metadata.get("reference_point_on_contour"):
        return QCoreApplication.translate("ReferencePoints", "On outline of: {name}").format(name=name)
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
    """A boundary click proposes that polygon, never an unrelated selection."""
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
                and obj.isVisible()
            )
        )
    ]
    for obj in sorted(candidates, key=lambda obj: (obj.name, str(obj.item_id))):
        parents.addItem(parent_name(obj), obj.item_id)
    parent_id = item.parent_bed_id if item else None
    auto_contour = False
    if item is None and position is not None:
        threshold = 8.0 / max(view._zoom_factor, 0.01)
        best_distance = threshold
        for candidate in candidates:
            if not isinstance(candidate, PolygonItem):
                continue
            projection = nearest_contour_position(candidate, position)
            if (projection is not None and projection[1] <= threshold
                    and (parent_id is None or projection[1] < best_distance - 1e-9)):
                parent_id = candidate.item_id
                best_distance = projection[1]
                auto_contour = True
    index = parents.findData(parent_id)
    parents.setCurrentIndex(max(0, index))
    form.addRow(QCoreApplication.translate("ReferencePoints", "Attached to"), parents)
    contour = QCheckBox(QCoreApplication.translate("ReferencePoints", "Keep on the parent outline"), dialog)
    contour.setObjectName("reference_point_on_contour")
    contour.setChecked(options.on_contour or auto_contour)
    form.addRow(contour)
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
        supported = parent is not None and next(iter(item_edges(parent)), None) is not None
        contour.setEnabled(supported)
        if not supported:
            contour.setChecked(False)
        preview_origin = origin
        if contour.isChecked() and parent is not None and origin is not None:
            projection = nearest_contour_position(parent, origin)
            if projection is not None:
                preview_origin = projection[0]
        if parent is not None:
            path.addPath(parent.mapToScene(parent.shape()))
            if preview_origin is not None:
                path.moveTo(preview_origin)
                path.lineTo(parent.sceneBoundingRect().center())
            status.setText(QCoreApplication.translate("ReferencePoints", "Attached to: {name}").format(
                name=parents.currentText()))
        else:
            status.setText(QCoreApplication.translate("ReferencePoints", "Independent point"))
        if preview_origin is not None:
            radius = 10 / max(view._zoom_factor, 0.01)
            path.moveTo(preview_origin + QPointF(-radius, 0))
            path.lineTo(preview_origin + QPointF(radius, 0))
            path.moveTo(preview_origin + QPointF(0, -radius))
            path.lineTo(preview_origin + QPointF(0, radius))
        preview.setPath(path)

    parents.currentIndexChanged.connect(update_preview)
    contour.toggled.connect(update_preview)
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
                               float(size.value()), color.color.name(QColor.NameFormat.HexArgb), width.value(),
                               contour.isChecked() and contour.isEnabled())
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
    layer = scene.get_layer_by_id(parent.layer_id) if isinstance(parent, GardenItemMixin) and parent.layer_id else scene.active_layer
    if layer is None or layer.locked or not layer.visible:
        parent_z = layer.z_order if layer is not None else float("-inf")
        layer = next((candidate for candidate in [scene.active_layer, *sorted(scene.layers, key=lambda it: -it.z_order)]
                      if candidate is not None and not candidate.locked and candidate.visible
                      and candidate.z_order >= parent_z), None)
    if layer is None:
        view.set_status_message(QCoreApplication.translate(
            "ReferencePoints", "Choose a visible, unlocked layer to place a reference point."))
        return False
    layer_id = layer.id
    if result.on_contour and parent is not None:
        projection = nearest_contour_position(parent, position)
        if projection is not None:
            position = projection[0]
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
    old_options = PointProperties.from_item(item)
    old = (old_options, QPointF(item.pos()))
    new_position = QPointF(item.pos())
    if result.on_contour and result.parent_id is not None:
        parent = next((obj for obj in scene.items() if isinstance(obj, GardenItemMixin)
                       and obj.item_id == result.parent_id), None)
        projection = nearest_contour_position(parent, item.mapToScene(QPointF())) if parent else None
        if projection is not None:
            qt_parent = item.parentItem()
            new_position = qt_parent.mapFromScene(projection[0]) if qt_parent else projection[0]
    new = (result, new_position)
    if new_position != old[1]:
        from open_garden_planner.agent_api.constraint_policy import translation_preserves_constraint
        from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
        shift = new_position - old[1]
        qt_parent = item.parentItem()
        parent_transform = qt_parent.sceneTransform() if qt_parent is not None else None
        if parent_transform is not None:
            shift = parent_transform.map(shift) - parent_transform.map(QPointF())
        if isinstance(scene, CanvasScene) and any(
            not translation_preserves_constraint(c.to_dict(), {str(item.item_id): (shift.x(), shift.y())})
            for c in scene.constraint_graph.get_item_constraints(item.item_id)
        ):
            view.set_status_message(QCoreApplication.translate(
                "CanvasView", "Movement blocked: existing constraints conflict with the parent outline."))
            return
    if old == new:
        return

    def apply(point: CircleItem, value: tuple[PointProperties, QPointF]) -> None:
        options, position = value
        apply_appearance(point, options)
        SetParentBedCommand(scene, point, point.parent_bed_id, options.parent_id).execute()
        point.setPos(position)

    command = ChangePropertyCommand(item, "Reference point", old, new, apply)
    view.command_manager.execute(command)


def apply_appearance(item: CircleItem, options: PointProperties) -> None:
    item.name = options.name
    item.metadata["reference_point_on_contour"] = options.on_contour
    item.set_radius_centered(options.size_px / 2)
    item.prepareGeometryChange()
    item.stroke_color = QColor(options.color)
    item.stroke_width = options.stroke_width
    pen = QPen(item.pen())
    pen.setColor(item.stroke_color)
    pen.setWidthF(options.stroke_width)
    item.setPen(pen)
    item.update()


def reference_context_menu(item: CircleItem) -> QMenu:
    """The usual object actions, routed through the existing view commands."""
    scene = item.scene()
    menu = QMenu()
    if scene is None or not scene.views():
        return menu
    view = cast("CanvasView", scene.views()[0])
    layer = view._canvas_scene.get_layer_by_id(item.layer_id) if item.layer_id else None
    editable = layer is not None and not layer.locked
    if editable and not item.isSelected():
        scene.clearSelection()
        item.setSelected(True)
    edit = menu.addAction(QCoreApplication.translate("ReferencePoints", "Edit reference point…"))
    assert edit is not None
    edit.setObjectName("reference_edit")
    edit.setEnabled(editable)
    edit.triggered.connect(lambda: edit_reference_point(item))
    layer_menu = item._build_move_to_layer_menu(menu)
    if layer_menu is not None:
        layer_menu.setEnabled(editable)
        layer_menu.triggered.connect(lambda action: item._dispatch_move_to_layer(action.data()))
    arrange_menu = item._build_arrange_menu(menu, supports_split=False)
    arrange_menu.setEnabled(editable)
    arrange_menu.triggered.connect(lambda action: item._dispatch_arrange(action.data()))
    menu.addSeparator()
    copy = menu.addAction(QCoreApplication.translate("GardenPlannerApp", "&Copy"))
    assert copy is not None
    copy.setObjectName("reference_copy")
    copy.setEnabled(editable)
    copy.triggered.connect(view.copy_selected)
    duplicate = menu.addAction(QCoreApplication.translate("CircleItem", "Duplicate"))
    assert duplicate is not None
    duplicate.setObjectName("reference_duplicate")
    duplicate.setEnabled(editable)
    duplicate.triggered.connect(view.duplicate_selected)
    delete = menu.addAction(QCoreApplication.translate("CircleItem", "Delete"))
    assert delete is not None
    delete.setObjectName("reference_delete")
    delete.setEnabled(editable)
    delete.triggered.connect(view._delete_selected_items)
    return menu
