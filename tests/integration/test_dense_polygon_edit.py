"""Generated curved outlines keep a bounded editing overlay, not less geometry."""

from PyQt6.QtCore import QPointF, Qt

from open_garden_planner.core.shape_boolean import boolean_union_many, item_to_painter_path
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.canvas.canvas_view import CanvasView
from open_garden_planner.ui.canvas.items import CircleItem, PolygonItem, RectangleItem
from open_garden_planner.ui.canvas.items.resize_handle import (
    MAX_DENSE_VERTEX_HANDLES,
    _format_coordinate,
)


def _capsule(qtbot):
    polygon = boolean_union_many([item_to_painter_path(item) for item in (
        RectangleItem(200, 200, 250, 170), CircleItem(200, 285, 85), CircleItem(450, 285, 85),
    )])
    item = PolygonItem.from_polygon(polygon)
    scene = CanvasScene()
    scene.addItem(item)
    view = CanvasView(scene)
    qtbot.addWidget(view)
    view.resize(1100, 650)
    view.show()
    view._zoom_factor = 1.5
    view._apply_transform()
    view.centerOn(item.sceneBoundingRect().center())
    qtbot.wait(30)
    return scene, view, item


def test_double_click_capsule_has_bounded_overlay_and_preserves_outline(qtbot):
    scene, view, item = _capsule(qtbot)
    original = item.polygon()
    assert original.count() > 200  # Reproduces the screenshot's tessellated arcs.
    item.setSelected(True)
    qtbot.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton,
                     pos=view.mapFromScene(original.boundingRect().center()))
    assert item.is_vertex_edit_mode
    assert not item._coord_labels and not item._edge_labels
    assert 0 < len(item._vertex_handles) <= MAX_DENSE_VERTEX_HANDLES
    assert len(item._midpoint_handles) < 10  # Only the genuinely long edges.
    assert len(item.childItems()) < 160
    assert item.polygon() == original
    assert all(handle.toolTip() for handle in item._vertex_handles)
    # Repeated zoom/pan must refresh rather than accumulate transient children.
    for zoom in (3.0, 0.8, 4.0):
        view._zoom_factor = zoom
        view._apply_transform()
        view.centerOn(QPointF(200, 285))
        assert len(item.childItems()) < 160
    assert item.polygon() == original
    item.setSelected(False)
    assert not item.is_vertex_edit_mode
    assert not item._vertex_handles and not item._midpoint_handles
    assert scene.get_command_manager().undo_depth == 0


def test_sampled_handle_uses_original_vertex_index_and_undo(qtbot):
    _scene, view, item = _capsule(qtbot)
    item.setSelected(True)
    item.enter_vertex_edit_mode()
    handle = next(handle for row, handle in enumerate(item._vertex_handles)
                  if handle.vertex_index != row)
    index = handle.vertex_index
    before = item.polygon()
    old = before.at(index)
    new = old + QPointF(10, 5)
    item._move_vertex_to(index, new)
    item._on_vertex_move_end(index, old, new)
    assert handle.pos() == new
    assert handle.toolTip() == _format_coordinate(new.x(), new.y())
    assert all(item.polygon().at(i) == before.at(i) for i in range(before.count()) if i != index)
    view.command_manager.undo()
    assert item.polygon() == before
    view.command_manager.redo()
    assert item.polygon().at(index) == new
    # Zooming into the arc exposes previously hidden original vertices.
    previous_indices = {handle.vertex_index for handle in item._vertex_handles}
    view._zoom_factor = 5.0
    view._apply_transform()
    view.centerOn(item.mapToScene(new))
    assert {handle.vertex_index for handle in item._vertex_handles} - previous_indices


def test_small_polygon_keeps_all_existing_annotations(qtbot):
    scene = CanvasScene()
    view = CanvasView(scene)
    qtbot.addWidget(view)
    item = PolygonItem([QPointF(0, 0), QPointF(100, 0), QPointF(50, 100)])
    scene.addItem(item)
    item.enter_vertex_edit_mode()
    assert len(item._vertex_handles) == len(item._midpoint_handles) == 3
    assert len(item._coord_labels) == len(item._edge_labels) == 3


def test_dense_handle_drag_through_viewport_survives_refresh(qtbot):
    _scene, view, item = _capsule(qtbot)
    item.setSelected(True)
    item.enter_vertex_edit_mode()
    handle = next(h for row, h in enumerate(item._vertex_handles) if h.vertex_index != row)
    index = handle.vertex_index
    original = item.polygon()
    start = view.mapFromScene(item.mapToScene(handle.pos()))
    qtbot.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    assert handle._is_dragging
    item.refresh_vertex_edit_overlay()
    assert handle in item._vertex_handles  # Never delete the current mouse grabber.
    finish = view.mapFromScene(item.mapToScene(original.at(index) + QPointF(15, 10)))
    qtbot.mouseMove(view.viewport(), finish)
    qtbot.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=finish)
    assert item.polygon().at(index) != original.at(index)
    assert all(item.polygon().at(i) == original.at(i) for i in range(original.count()) if i != index)
    assert view.command_manager.undo_depth == 1
    view.command_manager.undo()
    assert item.polygon() == original


def test_visible_midpoint_with_both_endpoints_offscreen(qtbot):
    _scene, view, item = _capsule(qtbot)
    item.setSelected(True)
    item.enter_vertex_edit_mode()
    view._zoom_factor = 10.0
    view._apply_transform()
    view.centerOn(QPointF(325, 370))
    assert not item._vertex_handles
    handle = next(h for h in item._midpoint_handles if h.pos() == QPointF(325, 370))
    original = item.polygon()
    index = handle.edge_index
    start = view.mapFromScene(item.mapToScene(original.at(index)))
    end = view.mapFromScene(item.mapToScene(original.at((index + 1) % original.count())))
    assert not view.viewport().rect().contains(start)
    assert not view.viewport().rect().contains(end)
    # The actual viewport click must insert at this original edge index.
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
                     pos=view.mapFromScene(item.mapToScene(handle.pos())))
    assert item.polygon().count() == original.count() + 1
    assert item.polygon().at(index + 1) == QPointF(325, 370)
    view.command_manager.undo()
    assert item.polygon() == original
