"""A snapped cutting axis partitions independent shapes without losing their data."""

from copy import deepcopy

import pytest
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QPainterPath
from PyQt6.QtWidgets import QMenu

from open_garden_planner.core.constraints import AnchorRef, ConstraintType
from open_garden_planner.core.measure_snapper import AnchorType
from open_garden_planner.core.object_types import ObjectType
from open_garden_planner.core.project import ProjectManager
from open_garden_planner.core.shape_boolean import _polygon_area, item_to_painter_path
from open_garden_planner.core.shape_split import can_split_shape, split_path
from open_garden_planner.core.tools import ToolType
from open_garden_planner.ui.canvas.items import CircleItem, PolygonItem, RectangleItem


@pytest.mark.parametrize('angle', [0, 37, 90])
def test_axis_preserves_rotated_area_without_overlap(qtbot, angle):
    source = RectangleItem(100, 100, 400, 200)
    source.setRotation(angle)
    path = item_to_painter_path(source)
    center = path.boundingRect().center()
    parts = split_path(path, center - QPointF(0, 500), center + QPointF(0, 500))
    assert len(parts) == 2
    assert sum(_polygon_area(part) for part in parts) == pytest.approx(80000, abs=0.1)
    paths = []
    for part in parts:
        p = QPainterPath()
        p.addPolygon(part)
        p.closeSubpath()
        paths.append(p)
    assert paths[0].intersected(paths[1]).isEmpty()
    assert _polygon_area(path.subtracted(paths[0].united(paths[1])).toFillPolygon()) < 1e-6


def test_holes_and_concavity_are_retained(qtbot):
    path = QPainterPath()
    path.addRect(QRectF(0, 0, 400, 400))
    path.addRect(QRectF(100, 100, 200, 200))
    parts = split_path(path, QPointF(200, -20), QPointF(200, 420))
    assert sum(_polygon_area(p) for p in parts) == pytest.approx(120000)
    for poly in parts:
        p = QPainterPath()
        p.addPolygon(poly)
        assert not p.contains(QPointF(200, 200))
    assert split_path(path, QPointF(0, 0), QPointF(400, 0)) == []
    assert split_path(path, QPointF(), QPointF()) == []


def test_two_viewport_picks_snap_to_midpoints_and_undo_roundtrip(canvas, qtbot, tmp_path):
    scene = canvas.scene()
    source = RectangleItem(100, 100, 200, 100, object_type=ObjectType.TERRACE_PATIO)
    source.label_visible = False
    source.metadata.update({'note': {'opaque': True}, 'fill_transform': {'angle_deg': 37, 'scale': 2}})
    source._setup_styling()
    scene.addItem(source)
    source.setSelected(True)
    before = ProjectManager().snapshot_dict(scene)['objects']
    original_rank = source.stack_order
    canvas.resize(900, 650)
    canvas.show()
    canvas.set_zoom(2)
    canvas.centerOn(200, 150)
    canvas.set_object_snap_enabled(True)
    canvas.set_midpoint_snap_enabled(True)
    canvas.set_snap_enabled(False)
    qtbot.wait(20)
    menu = QMenu()
    source._build_arrange_menu(menu)
    action = next(a for a in menu.actions() if a.objectName() == 'split_shape')
    assert action.isEnabled()
    action.trigger()
    assert canvas.tool_manager.active_tool_type == ToolType.SPLIT
    qtbot.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton,
                     pos=canvas.mapFromScene(QPointF(102, 151)))
    assert canvas._split_tool._start == QPointF(100, 150)
    qtbot.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton,
                     pos=canvas.mapFromScene(QPointF(298, 149)))
    pieces = [i for i in scene.items() if isinstance(i, PolygonItem)]
    assert len(pieces) == 2 and source.scene() is None
    assert sum(_polygon_area(p.polygon()) for p in pieces) == pytest.approx(20000)
    assert all(p.metadata == source.metadata and p.stack_order == original_rank for p in pieces)
    assert all(not p.label_visible for p in pieces)
    assert all(p.layer_id == source.layer_id and p.object_type == source.object_type for p in pieces)
    assert canvas.command_manager.undo_depth == 1
    assert canvas.tool_manager.active_tool_type == ToolType.SELECT
    canvas.command_manager.undo()
    assert ProjectManager().snapshot_dict(scene)['objects'] == before
    canvas.command_manager.redo()
    after = ProjectManager().snapshot_dict(scene)['objects']
    path = tmp_path / 'split.ogp'
    ProjectManager().save(scene, path)
    ProjectManager().load(scene, path)
    loaded = ProjectManager().snapshot_dict(scene)['objects']
    # Loading intentionally normalizes sparse stack ranks; geometry and identity remain exact.
    for records in (after, loaded):
        for record in records:
            record.pop('stack_order', None)
    assert loaded == after


def test_cancel_and_invalid_axis_do_not_mutate(canvas, qtbot, mouse_event):
    item = RectangleItem(100, 100, 200, 100)
    canvas.scene().addItem(item)
    before = deepcopy(ProjectManager().snapshot_dict(canvas.scene())['objects'])
    canvas.start_split_shape(item)
    tool = canvas._split_tool
    tool.mouse_press(mouse_event, QPointF(0, 0))
    tool.mouse_press(mouse_event, QPointF(0, 500))
    assert canvas.command_manager.undo_depth == 0
    tool.cancel()
    assert ProjectManager().snapshot_dict(canvas.scene())['objects'] == before
    assert tool._preview is None


def test_linked_or_locked_shapes_cannot_be_split(canvas, qtbot):
    scene = canvas.scene()
    source = RectangleItem(100, 100, 200, 100)
    scene.addItem(source)
    ref = AnchorRef(source.item_id, AnchorType.CENTER)
    scene.constraint_graph.add_constraint(ref, ref, 0, constraint_type=ConstraintType.FIXED,
                                         target_x=0, target_y=0)
    assert not can_split_shape(source, scene)
    canvas.start_split_shape(source)
    assert canvas.tool_manager.active_tool_type != ToolType.SPLIT
    plant = CircleItem(100, 100, 40, object_type=ObjectType.TREE)
    scene.addItem(plant)
    assert not can_split_shape(plant, scene)


def test_rotated_split_keeps_geometry_and_material_direction(canvas, mouse_event, tmp_path):
    source = RectangleItem(100, 100, 400, 200)
    source._apply_rotation(37)
    source.metadata['fill_transform'] = {'angle_deg': 25, 'scale': 1.5}
    source._setup_styling()
    scene = canvas.scene()
    scene.addItem(source)
    original_path = item_to_painter_path(source)
    center = original_path.boundingRect().center()
    canvas.start_split_shape(source)
    canvas._split_tool.mouse_press(mouse_event, center - QPointF(0, 500))
    canvas._split_tool.mouse_press(mouse_event, center + QPointF(0, 500))
    pieces = [i for i in scene.items() if isinstance(i, PolygonItem)]
    assert len(pieces) == 2
    combined = QPainterPath()
    for item in pieces:
        combined = combined.united(item_to_painter_path(item))
        assert item.rotation() == source.rotation()
        assert item.brush().transform() == source.brush().transform()
    assert sum(_polygon_area(p) for p in combined.toFillPolygons()) == pytest.approx(80000, abs=.01)
    assert _polygon_area(original_path.subtracted(combined).toFillPolygon()) < 1e-6
    ProjectManager().save(scene, tmp_path / 'rotated.ogp')
    ProjectManager().load(scene, tmp_path / 'rotated.ogp')
    restored = QPainterPath()
    for item in scene.items():
        if isinstance(item, PolygonItem):
            restored = restored.united(item_to_painter_path(item))
    assert _polygon_area(combined.subtracted(restored).toFillPolygon()) < 1e-6


@pytest.mark.parametrize('finish', ['escape', 'switch', 'clear'])
def test_incomplete_axis_cleans_preview_on_exit(canvas, qtbot, mouse_event, finish):
    item = RectangleItem(100, 100, 200, 100)
    scene = canvas.scene()
    scene.addItem(item)
    canvas.start_split_shape(item)
    tool = canvas._split_tool
    tool.mouse_press(mouse_event, QPointF(100, 150))
    assert tool._preview is not None
    if finish == 'clear':
        scene.clear()
    elif finish == 'switch':
        canvas.set_active_tool(ToolType.RECTANGLE)
    else:
        qtbot.keyClick(canvas, Qt.Key.Key_Escape)
    assert tool._preview is None
    assert tool.source_id is None
    assert canvas.command_manager.undo_depth == 0


@pytest.mark.parametrize('collection, payload', [
    ('_soil_tests', lambda key: {key: {'records': []}}),
    ('_pest_logs', lambda key: {key: {'records': []}}),
    ('_crop_rotation', lambda key: {'records': [{'area_id': key}]}),
    ('_succession_plans', lambda key: {key: {}}),
    ('_harvest_logs', lambda key: {key: {}}),
    ('_garden_journal_notes', lambda key: {'note': {'target_id': key}}),
    ('_manual_tasks', lambda key: {'task': {'target_id': key}}),
    ('_task_states', lambda key: {'water:' + key + ':2026': {'status': 'done'}}),
    ('_extra_fields', lambda key: {'future': {'linked_shape': key}}),
])
def test_project_references_block_split_even_after_first_pick(canvas, mouse_event, collection, payload):
    from open_garden_planner.services.soil_service import SoilService

    scene = canvas.scene()
    source = RectangleItem(100, 100, 200, 100, object_type=ObjectType.GARDEN_BED)
    scene.addItem(source)
    pm = ProjectManager()
    canvas.set_soil_service(SoilService(pm))
    assert can_split_shape(source, scene)
    canvas.start_split_shape(source)
    canvas._split_tool.mouse_press(mouse_event, QPointF(200, 0))
    setattr(pm, collection, payload(str(source.item_id)))
    assert not can_split_shape(source, scene)
    canvas._split_tool.mouse_press(mouse_event, QPointF(200, 400))
    assert source.scene() is scene
    assert canvas.command_manager.undo_depth == 0
    assert getattr(pm, collection) == payload(str(source.item_id))
