"""Coincidence and point-on-edge must agree in solving, drawing and diagnostics."""


import pytest
from PyQt6.QtCore import QPointF

from open_garden_planner.core.commands import AddConstraintCommand
from open_garden_planner.core.constraints import AnchorRef, ConstraintType
from open_garden_planner.core.measure_snapper import AnchorType, get_anchor_points
from open_garden_planner.ui.canvas.items import PolygonItem, RectangleItem
from open_garden_planner.ui.panels.constraints_panel import ConstraintsPanel


def point(item, kind, index=0):
    return next(a.point for a in get_anchor_points(item) if a.anchor_type == kind and a.anchor_index == index)


def pin(graph, item):
    ref = AnchorRef(item.item_id, AnchorType.CENTER)
    graph.add_constraint(ref, ref, 0, constraint_type=ConstraintType.FIXED,
                         target_x=item.pos().x(), target_y=item.pos().y())


def add_side(canvas, moving, reference, first, second):
    a = AnchorRef(moving.item_id, AnchorType.CORNER, 3)
    b = AnchorRef(reference.item_id, AnchorType.CORNER, first)
    c = AnchorRef(reference.item_id, AnchorType.CORNER, second)
    assert canvas.is_constraint_feasible(a, b, 0, ConstraintType.POINT_ON_EDGE, anchor_c=c)
    command = AddConstraintCommand(canvas.scene().constraint_graph, a, b, 0,
                                    ConstraintType.POINT_ON_EDGE, anchor_c=c)
    canvas._execute_constraint_with_solve(command)
    return canvas.scene().constraint_graph.constraints[command._constraint_id]


def test_two_compatible_sides_reach_corner_and_show_satisfied(canvas, qtbot, tmp_path):
    reference = RectangleItem(800, 400, 300, 300)
    moving = RectangleItem(710, 560, 50, 50)
    canvas.scene().addItem(reference)
    canvas.scene().addItem(moving)
    pin(canvas.scene().constraint_graph, reference)
    vertical = add_side(canvas, moving, reference, 0, 2)
    horizontal = add_side(canvas, moving, reference, 2, 3)
    assert point(moving, AnchorType.CORNER, 3) == QPointF(800, 700)
    panel = ConstraintsPanel()
    qtbot.addWidget(panel)
    panel.set_scene(canvas.scene())
    assert panel._is_satisfied(vertical)
    assert panel._is_satisfied(horizontal)
    canvas.command_manager.undo()
    assert point(moving, AnchorType.CORNER, 3).x() == pytest.approx(800)
    canvas.command_manager.redo()
    assert point(moving, AnchorType.CORNER, 3) == QPointF(800, 700)
    from open_garden_planner.core.project import ProjectManager

    identity = moving.item_id
    path = tmp_path / 'corner.ogp'
    ProjectManager().save(canvas.scene(), path)
    ProjectManager().load(canvas.scene(), path)
    restored = canvas.scene().find_item_by_id(identity)
    assert point(restored, AnchorType.CORNER, 3) == QPointF(800, 700)
    assert all(panel._is_satisfied(c) for c in canvas.scene().constraint_graph.constraints.values())


@pytest.mark.parametrize('kind', [AnchorType.EDGE_TOP, AnchorType.EDGE_BOTTOM,
                                 AnchorType.EDGE_LEFT, AnchorType.EDGE_RIGHT])
def test_rectangle_constraint_annotation_resolves_the_requested_side(canvas, kind):
    item = RectangleItem(100, 100, 300, 200)
    canvas.scene().addItem(item)
    actual = canvas.scene().dimension_line_manager._resolve_anchor_position(item.item_id, kind, 0)
    assert actual == point(item, kind)


def test_existing_edge_binding_survives_polygon_edge_direction_change(canvas, qtbot):
    reference = RectangleItem(800, 400, 300, 300)
    moving = PolygonItem([QPointF(750, 450), QPointF(750, 550),
                          QPointF(700, 550), QPointF(700, 450)])
    canvas.scene().addItem(reference)
    canvas.scene().addItem(moving)
    pin(canvas.scene().constraint_graph, reference)
    # Edge 0 starts vertical (EDGE_RIGHT) then becomes horizontal (EDGE_BOTTOM).
    a = AnchorRef(moving.item_id, AnchorType.EDGE_RIGHT, 0)
    b = AnchorRef(reference.item_id, AnchorType.CORNER, 0)
    c = AnchorRef(reference.item_id, AnchorType.CORNER, 2)
    command = AddConstraintCommand(canvas.scene().constraint_graph, a, b, 0,
                                  ConstraintType.POINT_ON_EDGE, anchor_c=c)
    canvas._execute_constraint_with_solve(command)
    moving.setPolygon(type(moving.polygon())([QPointF(700, 450), QPointF(800, 450),
                                            QPointF(800, 550), QPointF(700, 550)]))
    moving.setSelected(True)
    canvas._drag_start_positions = {moving: moving.pos()}
    moving.moveBy(30, 20)
    canvas._enforce_point_on_edge_positions()
    canvas._propagate_constraints_during_drag()
    polygon = moving.polygon()
    mid = moving.mapToScene((polygon.at(0)+polygon.at(1))/2)
    assert mid.x() == pytest.approx(800, abs=.1)


def test_unrelated_violated_binding_does_not_reject_a_compatible_corner(canvas, qtbot):
    reference = RectangleItem(800, 400, 300, 300)
    moving = RectangleItem(710, 560, 50, 50)
    orphan_a = RectangleItem(100, 100, 40, 40)
    orphan_b = RectangleItem(300, 300, 40, 40)
    scene = canvas.scene()
    for item in (reference, moving, orphan_a, orphan_b):
        scene.addItem(item)
    graph = scene.constraint_graph
    pin(graph, reference)
    pin(graph, orphan_a)
    pin(graph, orphan_b)
    graph.add_constraint(AnchorRef(orphan_a.item_id, AnchorType.CENTER),
                         AnchorRef(orphan_b.item_id, AnchorType.CENTER), 0,
                         constraint_type=ConstraintType.COINCIDENT)
    add_side(canvas, moving, reference, 0, 2)
    add_side(canvas, moving, reference, 2, 3)
    assert point(moving, AnchorType.CORNER, 3) == QPointF(800, 700)


def test_feasibility_respects_construction_reference_and_matches_application(canvas):
    from open_garden_planner.ui.canvas.items.construction_item import ConstructionLineItem

    line = ConstructionLineItem(QPointF(800, 400), QPointF(800, 700))
    moving = RectangleItem(710, 560, 50, 50)
    scene = canvas.scene()
    scene.addItem(line)
    scene.addItem(moving)
    pin(scene.constraint_graph, moving)
    a = AnchorRef(moving.item_id, AnchorType.CORNER, 3)
    b = AnchorRef(line.item_id, AnchorType.ENDPOINT, 0)
    c = AnchorRef(line.item_id, AnchorType.ENDPOINT, 1)
    assert not canvas.is_constraint_feasible(a, b, 0, ConstraintType.POINT_ON_EDGE, anchor_c=c)



def test_compatible_second_coincidence_does_not_report_old_polygon_anchor(canvas):
    from uuid import uuid4

    from open_garden_planner.core.constraints import Constraint

    scene = canvas.scene()
    moving = PolygonItem([QPointF(100, 100), QPointF(150, 100), QPointF(150, 150), QPointF(100, 150)])
    partner = RectangleItem(100, 100, 50, 50)
    reference = RectangleItem(500, 500, 50, 50)
    for item in (moving, partner, reference):
        scene.addItem(item)
    graph = scene.constraint_graph
    pin(graph, reference)
    a = AnchorRef(moving.item_id, AnchorType.CORNER, 0)
    graph.add_constraint(a, AnchorRef(partner.item_id, AnchorType.CORNER, 0), 0,
                         constraint_type=ConstraintType.COINCIDENT)
    trial = Constraint(uuid4(), a, AnchorRef(reference.item_id, AnchorType.CORNER, 0), 0,
                       constraint_type=ConstraintType.COINCIDENT)
    positions = {item.item_id: (item.pos().x(), item.pos().y()) for item in (moving, partner, reference)}
    offsets = {(item.item_id, anchor.anchor_type, anchor.anchor_index):
               (anchor.point.x()-item.pos().x(), anchor.point.y()-item.pos().y())
               for item in (moving, partner, reference) for anchor in get_anchor_points(item)}
    deformable, vertices = canvas._gather_deformable_info({i.item_id: i for i in (moving, partner, reference)})
    assert graph.validate_constraint(a, trial.anchor_b, 0, ConstraintType.COINCIDENT, positions, offsets,
                                      deformable_items=deformable, deformable_vertices=vertices)
    assert graph.find_conflicting_constraints(trial, positions, offsets, deformable, vertices) == []


def test_feasibility_does_not_move_reference_when_application_keeps_it_still(canvas):
    edge = RectangleItem(800, 400, 300, 300)
    moving = RectangleItem(750, 500, 50, 50)
    reference = RectangleItem(900, 600, 50, 50)
    scene = canvas.scene()
    for item in (edge, moving, reference):
        scene.addItem(item)
    graph = scene.constraint_graph
    pin(graph, edge)
    a = AnchorRef(moving.item_id, AnchorType.CORNER, 3)
    graph.add_constraint(a, AnchorRef(edge.item_id, AnchorType.CORNER, 0), 0,
                         constraint_type=ConstraintType.POINT_ON_EDGE,
                         anchor_c=AnchorRef(edge.item_id, AnchorType.CORNER, 2))
    b = AnchorRef(reference.item_id, AnchorType.CORNER, 0)
    assert not canvas.is_constraint_feasible(a, b, 0, ConstraintType.COINCIDENT)


def test_compatible_coincidences_are_applied_to_the_displayed_precision(canvas, qtbot):
    scene = canvas.scene()
    a, b, reference = (RectangleItem(x, 100, 50, 50) for x in (100, 100, 100.5))
    for item in (a, b, reference):
        scene.addItem(item)
    graph = scene.constraint_graph
    pin(graph, reference)
    ref_a = AnchorRef(a.item_id, AnchorType.CORNER, 0)
    old = graph.add_constraint(ref_a, AnchorRef(b.item_id, AnchorType.CORNER, 0), 0,
                               constraint_type=ConstraintType.COINCIDENT)
    command = AddConstraintCommand(graph, ref_a, AnchorRef(reference.item_id, AnchorType.CORNER, 0),
                                   0, ConstraintType.COINCIDENT)
    canvas._execute_constraint_with_solve(command)
    assert (point(a, AnchorType.CORNER) - point(b, AnchorType.CORNER)).manhattanLength() < .1
    panel = ConstraintsPanel()
    qtbot.addWidget(panel)
    panel.set_scene(scene)
    assert panel._is_satisfied(old)


@pytest.mark.parametrize('angle', [0, 37])
def test_corner_bindings_hold_during_real_drag_with_snapping(canvas, qtbot, angle):
    from tests.integration.test_drag_fixed_reference import drag

    reference = RectangleItem(800, 400, 300, 300)
    reference._apply_rotation(angle)
    moving = RectangleItem(710, 560, 50, 50)
    scene = canvas.scene()
    scene.addItem(reference)
    scene.addItem(moving)
    pin(scene.constraint_graph, reference)
    vertical = add_side(canvas, moving, reference, 0, 2)
    horizontal = add_side(canvas, moving, reference, 2, 3)
    expected = point(reference, AnchorType.CORNER, 2)
    before_geometry = moving.rect()
    canvas.set_object_snap_enabled(True)
    drag(qtbot, canvas, moving.mapToScene(moving.rect().center()), QPointF(90, 80))
    actual = point(moving, AnchorType.CORNER, 3)
    assert actual.x() == pytest.approx(expected.x(), abs=.05)
    assert actual.y() == pytest.approx(expected.y(), abs=.05)
    assert moving.rect() == before_geometry
    panel = ConstraintsPanel()
    qtbot.addWidget(panel)
    panel.set_scene(scene)
    assert panel._is_satisfied(vertical) and panel._is_satisfied(horizontal)


def test_two_different_rectangle_side_anchors_fit_a_corner(canvas, qtbot):
    reference = RectangleItem(800, 400, 300, 300)
    moving = RectangleItem(710, 560, 50, 50)
    scene = canvas.scene()
    scene.addItem(reference)
    scene.addItem(moving)
    graph = scene.constraint_graph
    pin(graph, reference)
    for kind, first, second in ((AnchorType.EDGE_LEFT, 0, 2), (AnchorType.EDGE_BOTTOM, 2, 3)):
        a = AnchorRef(moving.item_id, kind)
        b = AnchorRef(reference.item_id, AnchorType.CORNER, first)
        c = AnchorRef(reference.item_id, AnchorType.CORNER, second)
        assert canvas.is_constraint_feasible(a, b, 0, ConstraintType.POINT_ON_EDGE, anchor_c=c)
        canvas._execute_constraint_with_solve(AddConstraintCommand(graph, a, b, 0,
                                    ConstraintType.POINT_ON_EDGE, anchor_c=c))
    assert point(moving, AnchorType.EDGE_LEFT).x() == pytest.approx(800, abs=.05)
    assert point(moving, AnchorType.EDGE_BOTTOM).y() == pytest.approx(700, abs=.05)
    panel = ConstraintsPanel()
    qtbot.addWidget(panel)
    panel.set_scene(scene)
    assert all(panel._is_satisfied(c) for c in graph.constraints.values())


def test_existing_point_follows_edge_when_its_second_endpoint_is_constrained(canvas, monkeypatch):
    from open_garden_planner.ui.dialogs.constraint_conflict_dialog import ConstraintConflictDialog

    monkeypatch.setattr(ConstraintConflictDialog, 'ask', lambda *_a, **_kw: pytest.fail('Compatible edge bindings reported as conflicting'))
    scene = canvas.scene()
    edge = PolygonItem([QPointF(800, 400), QPointF(1000, 400),
                        QPointF(1000, 600), QPointF(800, 600)])
    base = RectangleItem(800, 400, 50, 50)
    upper = RectangleItem(1000, 600, 50, 50)
    moving = RectangleItem(850, 350, 50, 50)
    for item in (edge, base, upper, moving):
        scene.addItem(item)
    graph = scene.constraint_graph
    pin(graph, base)
    pin(graph, upper)
    graph.add_constraint(AnchorRef(edge.item_id, AnchorType.CORNER, 0),
                         AnchorRef(base.item_id, AnchorType.CORNER, 0), 0,
                         constraint_type=ConstraintType.COINCIDENT)
    add_side(canvas, moving, edge, 0, 1)
    command = AddConstraintCommand(graph, AnchorRef(edge.item_id, AnchorType.CORNER, 1),
                                   AnchorRef(upper.item_id, AnchorType.CORNER, 0), 0,
                                   ConstraintType.COINCIDENT)
    canvas._execute_constraint_with_solve(command)
    a, b, c = point(moving, AnchorType.CORNER, 3), point(edge, AnchorType.CORNER, 0), point(edge, AnchorType.CORNER, 1)
    assert c == point(upper, AnchorType.CORNER, 0)
    from math import hypot

    distance = abs((a.x()-b.x())*(c.y()-b.y()) - (a.y()-b.y())*(c.x()-b.x())) / hypot(c.x()-b.x(), c.y()-b.y())
    assert distance < .1


def test_distinct_parallel_sides_still_reject_a_genuine_conflict(canvas):
    first = RectangleItem(800, 400, 100, 300)
    second = RectangleItem(1000, 400, 100, 300)
    moving = RectangleItem(710, 560, 50, 50)
    scene = canvas.scene()
    for item in (first, second, moving):
        scene.addItem(item)
    graph = scene.constraint_graph
    pin(graph, first)
    pin(graph, second)
    add_side(canvas, moving, first, 0, 2)
    before = graph.to_list(), moving.pos()
    assert not canvas.is_constraint_feasible(
        AnchorRef(moving.item_id, AnchorType.CORNER, 3),
        AnchorRef(second.item_id, AnchorType.CORNER, 0), 0, ConstraintType.POINT_ON_EDGE,
        anchor_c=AnchorRef(second.item_id, AnchorType.CORNER, 2),
    )
    assert (graph.to_list(), moving.pos()) == before
