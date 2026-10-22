"""Dragging one shape must not displace fixed or unrelated geometry."""

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
