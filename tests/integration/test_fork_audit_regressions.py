"""Data links and Qt lifetimes exercised by the personal fork audit."""

from copy import deepcopy

import pytest
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QDialog, QMessageBox

from open_garden_planner.core.garden_memory import GardenMemoryService
from open_garden_planner.core.object_types import ObjectType
from open_garden_planner.core.project import ProjectManager
from open_garden_planner.core.shape_split import can_split_shape
from open_garden_planner.services.soil_service import SoilService
from open_garden_planner.ui.canvas.items import CircleItem, RectangleItem


@pytest.mark.parametrize("operation", ["union", "intersect", "subtract"])
@pytest.mark.parametrize("link", ["intervention", "soil", "card", "zone_member"])
def test_boolean_replacement_keeps_linked_shapes(canvas, monkeypatch, operation, link):
    scene = canvas.scene()
    pm = ProjectManager()
    canvas.set_soil_service(SoilService(pm))
    service = GardenMemoryService(pm, canvas.command_manager, scene)
    bed = RectangleItem(100, 100, 300, 200, object_type=ObjectType.GARDEN_BED)
    other = RectangleItem(200, 100, 300, 200)
    scene.addItem(bed)
    scene.addItem(other)
    if link == "intervention":
        service.add_event({"type": "amendment", "status": "done", "description": "Compost fictif",
                           "targets": [{"id": str(bed.item_id), "name": "Massif"}]}, "once")
    elif link == "soil":
        pm._soil_tests = {str(bed.item_id): {"records": []}}
    elif link == "card":
        service.set_card(str(bed.item_id), {"name": "Massif", "kind": "zone"})
    else:
        plant = CircleItem(180, 150, 20, object_type=ObjectType.PERENNIAL)
        scene.addItem(plant)
        service.set_card(str(plant.item_id), {"name": "Plantation", "zone_id": str(bed.item_id)})
    bed.setSelected(True)
    other.setSelected(True)
    before = deepcopy(pm.snapshot_dict(scene))
    depth = canvas.command_manager.undo_depth
    confirmations = []

    def confirm(*_args):
        confirmations.append(True)
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "question", confirm)
    canvas.boolean_operation(operation)
    assert bed.scene() is scene and other.scene() is scene
    assert canvas.command_manager.undo_depth == depth
    assert not confirmations
    after = pm.snapshot_dict(scene)
    before.pop("metadata")
    after.pop("metadata")
    assert after == before


def test_split_keeps_zone_referenced_only_by_plant_metadata(canvas):
    scene = canvas.scene()
    bed = RectangleItem(100, 100, 300, 200, object_type=ObjectType.GARDEN_BED)
    plant = CircleItem(180, 150, 20, object_type=ObjectType.PERENNIAL)
    scene.addItem(bed)
    scene.addItem(plant)
    service = GardenMemoryService(ProjectManager(), canvas.command_manager, scene)
    service.set_card(str(plant.item_id), {"name": "Plantation", "zone_id": str(bed.item_id)})
    assert not can_split_shape(bed, scene)


def test_closed_records_dialogs_release_project_subscriptions(qtbot, monkeypatch):
    from open_garden_planner.app.application import GardenPlannerApp
    from open_garden_planner.ui.dialogs.garden_memory_dialog import GardenMemoryDialog

    monkeypatch.setattr(GardenPlannerApp, "_startup_sequence", lambda *_: None)
    monkeypatch.setattr(GardenMemoryDialog, "exec", lambda *_: QDialog.DialogCode.Rejected)
    app = GardenPlannerApp()
    qtbot.addWidget(app)
    baseline = app._project_manager.receivers(app._project_manager.garden_memory_changed)
    for _ in range(3):
        app._on_garden_records()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not app.findChildren(GardenMemoryDialog)
    assert app._project_manager.receivers(app._project_manager.garden_memory_changed) == baseline


def test_boolean_preserves_rotated_material_frame_and_hidden_label(canvas, monkeypatch, tmp_path):
    from PyQt6.QtCore import QPointF

    from open_garden_planner.core.fill_patterns import FillPattern
    from open_garden_planner.core.shape_boolean import item_to_painter_path
    from open_garden_planner.ui.canvas.items import PolygonItem

    scene = canvas.scene()
    source = RectangleItem(100, 100, 300, 200)
    source._apply_rotation(37)
    source.label_visible = False
    source.fill_pattern = FillPattern.GRASS
    source.metadata["fill_transform"] = {"angle_deg": 23, "scale": 1.5, "offset_x_cm": 12}
    source._setup_styling()
    other = RectangleItem(150, 100, 300, 200)
    for item in (source, other):
        scene.addItem(item)
        item.setSelected(True)
    expected_path = item_to_painter_path(source).united(item_to_painter_path(other))
    material_frame = source.brush().transform() * source.sceneTransform()
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Ok)
    canvas.boolean_operation("union")
    result = next(item for item in scene.items() if isinstance(item, PolygonItem))
    def check_appearance(item):
        result_frame = item.brush().transform() * item.sceneTransform()
        for point in (QPointF(), QPointF(10, 0), QPointF(0, 10)):
            assert result_frame.map(point).x() == pytest.approx(material_frame.map(point).x())
            assert result_frame.map(point).y() == pytest.approx(material_frame.map(point).y())
        assert not item.label_visible
        assert expected_path.subtracted(item_to_painter_path(item)).isEmpty()

    check_appearance(result)
    canvas.command_manager.undo()
    assert source.scene() is scene and other.scene() is scene
    canvas.command_manager.redo()
    check_appearance(result)
    path = tmp_path / "merged.ogp"
    pm = ProjectManager()
    pm.save(scene, path)
    restored = type(scene)()
    pm.load(restored, path)
    check_appearance(next(item for item in restored.items() if isinstance(item, PolygonItem)))


def test_boolean_rechecks_links_added_during_confirmation(canvas, monkeypatch):
    scene = canvas.scene()
    pm = ProjectManager()
    canvas.set_soil_service(SoilService(pm))
    items = [RectangleItem(100, 100, 200, 100), RectangleItem(200, 100, 200, 100)]
    for item in items:
        scene.addItem(item)
        item.setSelected(True)

    def confirm(*_args):
        pm._soil_tests = {str(items[0].item_id): {"records": []}}
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "question", confirm)
    canvas.boolean_operation("union")
    assert all(item.scene() is scene for item in items)
    assert canvas.command_manager.undo_depth == 0


def test_empty_canvas_menus_are_released_after_dismissal(canvas, monkeypatch):
    from PyQt6.QtCore import QPoint
    from PyQt6.QtGui import QContextMenuEvent
    from PyQt6.QtWidgets import QMenu

    monkeypatch.setattr(QMenu, "exec", lambda *_: None)
    canvas.show()
    baseline = len(canvas.findChildren(QMenu))
    for _ in range(3):
        position = QPoint(40, 40)
        canvas.contextMenuEvent(QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse, position, canvas.mapToGlobal(position),
        ))
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert len(canvas.findChildren(QMenu)) == baseline


@pytest.mark.parametrize("shape", ["rectangle", "circle", "ellipse", "polygon", "grouped_rectangle"])
def test_moved_material_origin_survives_repeated_saves(qtbot, tmp_path, shape):
    from PyQt6.QtCore import QPointF

    from open_garden_planner.core.fill_patterns import FillPattern
    from open_garden_planner.core.fill_transform import fill_offset_values
    from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
    from open_garden_planner.ui.canvas.items import EllipseItem, PolygonItem
    from open_garden_planner.ui.canvas.items.group_item import GroupItem

    item = {
        "rectangle": lambda: RectangleItem(100, 100, 200, 100),
        "grouped_rectangle": lambda: RectangleItem(100, 100, 200, 100),
        "circle": lambda: CircleItem(200, 150, 70),
        "ellipse": lambda: EllipseItem(100, 100, 200, 100),
        "polygon": lambda: PolygonItem([QPointF(100, 100), QPointF(300, 100), QPointF(200, 200)]),
    }[shape]()
    scene = CanvasScene()
    scene.addItem(item)
    item.fill_pattern = FillPattern.WOOD
    if shape == "circle":
        from PyQt6.QtGui import QColor, QImage

        from open_garden_planner.core.png_asset import import_png

        png = tmp_path / "personal.png"
        image = QImage(8, 4, QImage.Format.Format_ARGB32)
        image.fill(QColor("blue"))
        assert image.save(str(png))
        item.metadata["personal_fill_pattern"] = {"png": import_png(png), "width_cm": 80}
    item.metadata["fill_transform"] = {"angle_deg": 23, "scale": 1.5, "offset_x_cm": 12}
    item._setup_styling()
    item._apply_rotation(37)
    item.setPos(141001 if shape == "rectangle" else 141, -29)
    identity = item.item_id
    if shape == "grouped_rectangle":
        group = GroupItem()
        scene.addItem(group)
        group.addToGroup(item)
        group.setPos(311, 89)
    path = tmp_path / "material.ogp"
    pm = ProjectManager()
    for iteration in range(3):
        if iteration == 2:
            item.moveBy(-17, 31)
        expected = item.brush().transform() * item.sceneTransform()
        expected_bounds = item.sceneBoundingRect()
        metadata = deepcopy(item.metadata)
        pm.save(scene, path)
        assert item.metadata == metadata
        scene = CanvasScene()
        pm.load(scene, path)
        item = scene.find_item_by_id(identity)
        actual = item.brush().transform() * item.sceneTransform()
        for point in (QPointF(), QPointF(10, 0), QPointF(0, 10)):
            assert actual.map(point).x() == pytest.approx(expected.map(point).x()), (shape, iteration)
            assert actual.map(point).y() == pytest.approx(expected.map(point).y())
        assert fill_offset_values(item.metadata["fill_transform"]) == (12, 0)
        assert item.sceneBoundingRect().getRect() == pytest.approx(expected_bounds.getRect())


def test_moved_ridged_roof_keeps_its_render_after_save(canvas, mouse_event, tmp_path):
    from PyQt6.QtCore import QPointF, QRectF
    from PyQt6.QtGui import QImage, QPainter

    from open_garden_planner.core.tools import ToolType
    from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
    from open_garden_planner.ui.canvas.items import PolygonItem

    canvas.set_active_tool(ToolType.HOUSE)
    tool = canvas.tool_manager.active_tool
    for point in (QPointF(100, 100), QPointF(500, 100), QPointF(500, 350), QPointF(100, 350)):
        tool.mouse_press(mouse_event, point)
    tool.mouse_double_click(mouse_event, QPointF(100, 350))
    house = next(item for item in canvas.scene().items() if isinstance(item, PolygonItem))
    house.metadata["fill_transform"] = {"angle_deg": 23, "scale": 1.5, "offset_x_cm": 12}
    house._setup_styling()
    house._apply_rotation(37)
    house.setPos(141, -29)

    def render(scene, item):
        scene.clearSelection()
        item.label_visible = False
        item._find_ridge().setVisible(False)
        image = QImage(600, 400, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        scene.render(painter, QRectF(image.rect()), item.sceneBoundingRect().adjusted(-5, -5, 5, 5))
        painter.end()
        return image

    before = render(canvas.scene(), house)
    path = tmp_path / "roof.ogp"
    ProjectManager().save(canvas.scene(), path)
    restored = CanvasScene()
    ProjectManager().load(restored, path)
    loaded = restored.find_item_by_id(house.item_id)
    assert render(restored, loaded) == before
