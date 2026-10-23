"""Saved position locks must constrain real viewport drags after reopening."""

import json

import pytest
from PyQt6.QtCore import QPointF

from open_garden_planner.core.constraints import AnchorRef, ConstraintType
from open_garden_planner.core.measure_snapper import AnchorType
from open_garden_planner.core.project import ProjectManager
from open_garden_planner.ui.canvas.items import RectangleItem
from open_garden_planner.ui.canvas.items.resize_handle import _is_item_fixed
from tests.integration.test_drag_fixed_reference import drag, pin


@pytest.mark.parametrize('legacy', [True, False])
def test_reopened_fixed_rectangle_cannot_move_or_break_edge_link(canvas, qtbot, tmp_path, legacy):
    scene = canvas.scene()
    reference = RectangleItem(800, 400, 300, 300, layer_id=scene.active_layer.id)
    fixed = RectangleItem(700, 450, 100, 150, layer_id=scene.active_layer.id)
    scene.addItem(reference)
    scene.addItem(fixed)
    # A pre-lock drag is folded into rectangle geometry by the file serializer.
    reference.moveBy(40, 25)
    fixed.moveBy(40, 25)
    graph = scene.constraint_graph
    pin(graph, reference)
    pin(graph, fixed)
    graph.add_constraint(AnchorRef(fixed.item_id, AnchorType.EDGE_RIGHT),
                         AnchorRef(reference.item_id, AnchorType.CORNER, 0), 0,
                         constraint_type=ConstraintType.POINT_ON_EDGE,
                         anchor_c=AnchorRef(reference.item_id, AnchorType.CORNER, 2))
    ids = fixed.item_id, reference.item_id
    before = fixed.sceneBoundingRect(), reference.sceneBoundingRect()
    path = tmp_path / 'locked.ogp'
    pm = ProjectManager()
    pm.save(scene, path)
    if legacy:
        data = json.loads(path.read_text())
        for constraint in data['constraints']:
            if constraint['constraint_type'] == 'FIXED':
                constraint.pop('target_x', None)
                constraint.pop('target_y', None)
        path.write_text(json.dumps(data))
    pm.load(scene, path)
    fixed, reference = (scene.find_item_by_id(identity) for identity in ids)
    assert _is_item_fixed(fixed) and _is_item_fixed(reference)
    assert (fixed.sceneBoundingRect(), reference.sceneBoundingRect()) == before
    depth = canvas.command_manager.undo_depth
    click = fixed.mapToScene(fixed.rect().center())
    drag(qtbot, canvas, click, QPointF(70, 30))
    assert (fixed.sceneBoundingRect(), reference.sceneBoundingRect()) == before
    assert canvas.command_manager.undo_depth == depth
    # Saving a formerly incomplete lock writes usable coordinates, with no drift
    # on a second reopen in the serializer's new item coordinate frame.
    repaired = tmp_path / 'resaved.ogp'
    pm.save(scene, repaired)
    saved = json.loads(repaired.read_text())
    assert all(c.get('target_x') is not None and c.get('target_y') is not None
               for c in saved['constraints'] if c['constraint_type'] == 'FIXED')
    pm.load(scene, repaired)
    fixed, reference = (scene.find_item_by_id(identity) for identity in ids)
    drag(qtbot, canvas, fixed.mapToScene(fixed.rect().center()), QPointF(-40, -20))
    assert (fixed.sceneBoundingRect(), reference.sceneBoundingRect()) == before
    # Explicit unlock still allows sliding along the fixed reference; undo puts
    # both the geometry and the position lock back in one action per edit.
    from PyQt6.QtWidgets import QMenu

    menu = QMenu()
    fixed._build_position_lock_action(menu)
    next(a for a in menu.actions() if a.objectName() == 'toggle_position_lock').trigger()
    assert not _is_item_fixed(fixed)
    canvas.set_object_snap_enabled(False)
    drag(qtbot, canvas, fixed.mapToScene(fixed.rect().center()), QPointF(30, 40))
    assert fixed.sceneBoundingRect().left() == pytest.approx(before[0].left())
    assert fixed.sceneBoundingRect().top() == pytest.approx(before[0].top() + 40)
    assert reference.sceneBoundingRect() == before[1]
    canvas.command_manager.undo()
    canvas.command_manager.undo()
    assert _is_item_fixed(fixed)
    assert (fixed.sceneBoundingRect(), reference.sceneBoundingRect()) == before


def test_graph_roundtrip_keeps_fixed_coordinates(qtbot):
    from uuid import uuid4

    from open_garden_planner.core.constraints import ConstraintGraph

    graph = ConstraintGraph()
    ref = AnchorRef(uuid4(), AnchorType.CENTER)
    graph.add_constraint(ref, ref, 0, constraint_type=ConstraintType.FIXED, target_x=40, target_y=25)
    assert ConstraintGraph.from_list(graph.to_list()).to_list() == graph.to_list()


def test_fixed_without_coordinates_is_still_immobile(canvas, qtbot):
    item = RectangleItem(500, 500, 150, 100)
    canvas.scene().addItem(item)
    ref = AnchorRef(item.item_id, AnchorType.CENTER)
    canvas.scene().constraint_graph.add_constraint(ref, ref, 0, constraint_type=ConstraintType.FIXED)
    before = item.sceneBoundingRect()
    drag(qtbot, canvas, QPointF(540, 535), QPointF(70, 30))
    assert item.sceneBoundingRect() == before
    assert canvas.command_manager.undo_depth == 0



def test_solver_pins_missing_coordinate_lock_at_supplied_position(qtbot):
    from uuid import uuid4

    from open_garden_planner.core.constraints import ConstraintGraph

    graph = ConstraintGraph()
    fixed_id, moving_id = uuid4(), uuid4()
    a, b = AnchorRef(fixed_id, AnchorType.CENTER), AnchorRef(moving_id, AnchorType.CENTER)
    graph.add_constraint(a, a, 0, constraint_type=ConstraintType.FIXED)
    graph.add_constraint(a, b, 100, constraint_type=ConstraintType.DISTANCE)
    result = graph.solve_anchored({fixed_id: (300, 200), moving_id: (500, 200)}, {}, tolerance=.05)
    assert fixed_id not in result.item_deltas
    assert result.item_deltas[moving_id] == pytest.approx((-100, 0))
