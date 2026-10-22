"""Dragging one shape must not displace fixed or unrelated geometry."""

import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from open_garden_planner.core.constraints import AnchorRef, ConstraintType
from open_garden_planner.core.measure_snapper import AnchorType
from open_garden_planner.ui.canvas.items import RectangleItem


def pin(graph, item):
    ref = AnchorRef(item.item_id, AnchorType.CENTER)
    graph.add_constraint(ref, ref, 0, constraint_type=ConstraintType.FIXED,
                         target_x=item.pos().x(), target_y=item.pos().y())


def drag(qtbot, view, start, delta):
    view.resize(1000, 700)
    view._zoom_factor = 1
    view._apply_transform()
    view.show()
    view.centerOn(start)
    QApplication.processEvents()
    origin = view.mapFromScene(start)
    destination = view.mapFromScene(start + delta)
    qtbot.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=origin)
    for fraction in (0.3, 0.6, 1.0):
        point = QPointF(origin) + (QPointF(destination) - QPointF(origin)) * fraction
        event = QMouseEvent(QMouseEvent.Type.MouseMove, point,
                            QPointF(view.viewport().mapToGlobal(point.toPoint())),
                            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(view.viewport(), event)
    qtbot.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=destination)


def test_fixed_point_on_edge_does_not_move_or_pull_reference(canvas, qtbot):
    scene = canvas.scene()
    reference = RectangleItem(800, 600, 200, 200)
    fixed = RectangleItem(500, 500, 150, 100)
    scene.addItem(reference)
    scene.addItem(fixed)
    graph = scene.constraint_graph
    pin(graph, fixed)
    # An existing inconsistent relation must not override a position lock.
    graph.add_constraint(AnchorRef(fixed.item_id, AnchorType.EDGE_RIGHT),
                         AnchorRef(reference.item_id, AnchorType.CORNER, 0), 0,
                         constraint_type=ConstraintType.POINT_ON_EDGE,
                         anchor_c=AnchorRef(reference.item_id, AnchorType.CORNER, 2))
    before = (fixed.pos(), reference.pos())
    drag(qtbot, canvas, QPointF(540, 535), QPointF(70, 30))
    assert fixed.pos() == before[0]
    assert reference.pos() == before[1]
    assert canvas.command_manager.undo_depth == 0


def test_unrelated_fixed_item_is_not_repaired_by_another_drag(canvas, qtbot):
    scene = canvas.scene()
    fixed, moving = RectangleItem(100, 100, 150, 100), RectangleItem(500, 500, 150, 100)
    scene.addItem(fixed)
    scene.addItem(moving)
    pin(scene.constraint_graph, fixed)
    # A stored target can differ from the current position after an earlier edit.
    fixed.moveBy(12, 9)
    before = fixed.pos()
    drag(qtbot, canvas, QPointF(540, 535), QPointF(70, 30))
    assert fixed.pos() == before
    after = moving.pos()
    assert after != QPointF()
    canvas.command_manager.undo()
    assert moving.pos() == QPointF() and fixed.pos() == before
    canvas.command_manager.redo()
    assert moving.pos() == after and fixed.pos() == before


def test_movable_partner_slides_along_fixed_rectangle_in_mixed_selection(canvas, qtbot):
    scene = canvas.scene()
    reference = RectangleItem(800, 400, 300, 300)
    moving = RectangleItem(700, 450, 100, 150)
    scene.addItem(reference)
    scene.addItem(moving)
    graph = scene.constraint_graph
    pin(graph, reference)
    graph.add_constraint(AnchorRef(moving.item_id, AnchorType.EDGE_RIGHT),
                         AnchorRef(reference.item_id, AnchorType.CORNER, 0), 0,
                         constraint_type=ConstraintType.POINT_ON_EDGE,
                         anchor_c=AnchorRef(reference.item_id, AnchorType.CORNER, 2))
    reference.setSelected(True)
    moving.setSelected(True)
    canvas.set_object_snap_enabled(False)
    before_rect = moving.rect()
    drag(qtbot, canvas, QPointF(735, 495), QPointF(70, 30))
    assert reference.pos() == QPointF()
    assert abs(moving.pos().x()) < 0.001
    assert abs(moving.pos().y() - 30) < 0.001
    assert moving.rect() == before_rect
    assert canvas.command_manager.undo_depth == 1
    after = moving.pos()
    canvas.command_manager.undo()
    assert moving.pos() == QPointF() and reference.pos() == QPointF()
    canvas.command_manager.redo()
    assert moving.pos() == after and reference.pos() == QPointF()


@pytest.mark.parametrize('locked', ['none', 'edge', 'point'])
@pytest.mark.parametrize('driver', ['edge', 'point'])
@pytest.mark.parametrize('angle', [0, 37])
def test_point_on_edge_remains_satisfied_when_either_shape_moves(canvas, qtbot, locked, driver, angle):
    from open_garden_planner.core.measure_snapper import get_anchor_points

    scene = canvas.scene()
    edge = RectangleItem(800, 400, 300, 300)
    edge._apply_rotation(angle)
    point = RectangleItem(700, 450, 100, 150)
    scene.addItem(edge)
    scene.addItem(point)
    def anchor(item, kind, index=0):
        return next(a.point for a in get_anchor_points(item) if a.anchor_type == kind and a.anchor_index == index)
    b = anchor(edge, AnchorType.CORNER, 0)
    c = anchor(edge, AnchorType.CORNER, 2)
    point.setPos((b + c) / 2 - anchor(point, AnchorType.EDGE_RIGHT))
    graph = scene.constraint_graph
    graph.add_constraint(AnchorRef(point.item_id, AnchorType.EDGE_RIGHT),
                         AnchorRef(edge.item_id, AnchorType.CORNER, 0), 0,
                         constraint_type=ConstraintType.POINT_ON_EDGE,
                         anchor_c=AnchorRef(edge.item_id, AnchorType.CORNER, 2))
    if locked != 'none':
        pin(graph, edge if locked == 'edge' else point)
    canvas.set_object_snap_enabled(False)
    chosen = edge if driver == 'edge' else point
    click = chosen.mapToScene(chosen.rect().center() + (QPointF(80, 0) if driver == 'edge' else QPointF()))
    drag(qtbot, canvas, click, QPointF(70, 30))
    a = anchor(point, AnchorType.EDGE_RIGHT)
    b = anchor(edge, AnchorType.CORNER, 0)
    c = anchor(edge, AnchorType.CORNER, 2)
    v = c - b
    distance = abs(v.x() * (a.y()-b.y()) - v.y() * (a.x()-b.x())) / (v.x()**2 + v.y()**2)**.5
    assert distance < 1.01


def test_sliding_rectangle_keeps_dimensions_with_internal_lengths_and_snap(canvas, qtbot):
    scene = canvas.scene()
    reference = RectangleItem(800, 400, 300, 300)
    moving = RectangleItem(700, 450, 100, 150)
    scene.addItem(reference)
    scene.addItem(moving)
    graph = scene.constraint_graph
    pin(graph, reference)
    graph.add_constraint(AnchorRef(moving.item_id, AnchorType.EDGE_RIGHT),
                         AnchorRef(reference.item_id, AnchorType.CORNER, 0), 0,
                         constraint_type=ConstraintType.POINT_ON_EDGE,
                         anchor_c=AnchorRef(reference.item_id, AnchorType.CORNER, 2))
    for first, second, length in [(0, 1, 100), (0, 2, 150)]:
        graph.add_constraint(AnchorRef(moving.item_id, AnchorType.CORNER, first),
                             AnchorRef(moving.item_id, AnchorType.CORNER, second), length,
                             constraint_type=ConstraintType.EDGE_LENGTH)
    canvas.set_object_snap_enabled(True)
    before = moving.rect()
    drag(qtbot, canvas, QPointF(735, 495), QPointF(70, 30))
    assert abs(moving.pos().x()) < .01
    assert moving.rect() == before
    assert moving.pos().y() != 0  # Tangential motion, including optional snapping, remains free.
    after = moving.pos()
    canvas.command_manager.undo()
    assert moving.pos() == QPointF() and moving.rect() == before
    canvas.command_manager.redo()
    assert moving.pos() == after and moving.rect() == before



def test_internal_dimensions_do_not_disable_sliding_around_fixed_partner(canvas, qtbot):
    import math

    scene = canvas.scene()
    reference = RectangleItem(800, 400, 300, 300)
    moving = RectangleItem(1125, 525, 50, 50)
    scene.addItem(reference)
    scene.addItem(moving)
    graph = scene.constraint_graph
    pin(graph, reference)
    graph.add_constraint(AnchorRef(moving.item_id, AnchorType.CENTER),
                         AnchorRef(reference.item_id, AnchorType.CENTER), 200,
                         constraint_type=ConstraintType.DISTANCE)
    for first, second in [(0, 1), (0, 2)]:
        graph.add_constraint(AnchorRef(moving.item_id, AnchorType.CORNER, first),
                             AnchorRef(moving.item_id, AnchorType.CORNER, second), 50,
                             constraint_type=ConstraintType.EDGE_LENGTH)
    canvas.set_object_snap_enabled(False)
    before = moving.rect()
    drag(qtbot, canvas, QPointF(1150, 550), QPointF(70, 30))
    center = moving.mapToScene(moving.rect().center())
    fixed_center = reference.mapToScene(reference.rect().center())
    assert math.hypot(center.x()-fixed_center.x(), center.y()-fixed_center.y()) == pytest.approx(200, abs=1)
    assert moving.rect() == before and reference.pos() == QPointF()


def test_polygon_body_drag_stays_rigid_and_undo_restores_it(canvas, qtbot):
    import math

    from open_garden_planner.ui.canvas.items import PolygonItem

    scene = canvas.scene()
    reference = RectangleItem(800, 400, 300, 300)
    moving = PolygonItem([QPointF(1150, 550), QPointF(1200, 550),
                          QPointF(1200, 600), QPointF(1150, 600)])
    scene.addItem(reference)
    scene.addItem(moving)
    graph = scene.constraint_graph
    pin(graph, reference)
    graph.add_constraint(AnchorRef(moving.item_id, AnchorType.CORNER, 0),
                         AnchorRef(reference.item_id, AnchorType.CENTER), 200,
                         constraint_type=ConstraintType.DISTANCE)
    for second in (1, 3):
        graph.add_constraint(AnchorRef(moving.item_id, AnchorType.CORNER, 0),
                             AnchorRef(moving.item_id, AnchorType.CORNER, second), 50,
                             constraint_type=ConstraintType.EDGE_LENGTH)
    canvas.set_object_snap_enabled(False)
    before = moving.polygon()
    drag(qtbot, canvas, QPointF(1175, 575), QPointF(70, 30))
    assert moving.polygon() == before
    corner = moving.mapToScene(before.at(0))
    assert math.hypot(corner.x()-950, corner.y()-550) == pytest.approx(200, abs=1)
    after = moving.pos()
    canvas.command_manager.undo()
    assert moving.pos() == QPointF() and moving.polygon() == before
    canvas.command_manager.redo()
    assert moving.pos() == after and moving.polygon() == before
