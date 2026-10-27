"""Placement and editing of named surveying references using existing commands."""

from typing import TYPE_CHECKING, cast
from uuid import UUID

from PyQt6.QtCore import QCoreApplication, QPointF
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGraphicsItem,
    QGraphicsScene,
    QLineEdit,
)

from open_garden_planner.core.commands import (
    ChangePropertyCommand,
    CreateItemCommand,
    SetParentBedCommand,
)
from open_garden_planner.core.reference_points import is_reference_point
from open_garden_planner.ui.canvas.items import CircleItem, GardenItemMixin

if TYPE_CHECKING:
    from open_garden_planner.ui.canvas.canvas_view import CanvasView


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


def point_dialog(view: "CanvasView", item: CircleItem | None = None) -> tuple[str, UUID | None] | None:
    """Choose a name and an explicit parent; never infer ownership by overlap."""
    dialog = QDialog(view)
    dialog.setWindowTitle(QCoreApplication.translate("ReferencePoints", "Reference point"))
    form = QFormLayout(dialog)
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
    selected = set(view._canvas_scene.selectedItems())
    for obj in sorted(candidates, key=lambda obj: (obj.name, str(obj.item_id))):
        parents.addItem(obj.name or str(obj.item_id)[:8], obj.item_id)
    parent_id = item.parent_bed_id if item else None
    if item is None:
        selected_parents = [obj for obj in candidates if obj in selected]
        if len(selected_parents) == 1:
            parent_id = selected_parents[0].item_id
    index = parents.findData(parent_id)
    parents.setCurrentIndex(max(0, index))
    form.addRow(QCoreApplication.translate("ReferencePoints", "Attached to"), parents)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        parent=dialog,
    )
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    form.addRow(buttons)
    name.setFocus()
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return name.text().strip(), cast(UUID | None, parents.currentData())


def place_reference_point(view: "CanvasView", position: QPointF) -> bool:
    result = point_dialog(view)
    if result is None:
        return False
    name, parent_id = result
    scene = view._canvas_scene
    parent = scene.find_item_by_id(parent_id) if parent_id else None
    layer_id = parent.layer_id if isinstance(parent, GardenItemMixin) else (scene.active_layer.id if scene.active_layer else None)
    item = CircleItem(
        position.x(), position.y(), 8.0, name=name,
        metadata={"reference_point": True}, layer_id=layer_id,
    )
    view.command_manager.execute(CreateReferencePointCommand(scene, item, parent_id))
    scene.clearSelection()
    item.setSelected(True)
    return True


def edit_reference_point(item: CircleItem) -> None:
    scene = item.scene()
    if scene is None or not scene.views():
        return
    view = cast("CanvasView", scene.views()[0])
    result = point_dialog(view, item)
    if result is None:
        return
    name, parent_id = result
    old = (item.name, item.parent_bed_id)
    new = (name, parent_id)
    if old == new:
        return

    def apply(point: CircleItem, value: tuple[str, UUID | None]) -> None:
        point.name = value[0]
        SetParentBedCommand(scene, point, point.parent_bed_id, value[1]).execute()

    command = ChangePropertyCommand(item, "Reference point", old, new, apply)
    view.command_manager.execute(command)
