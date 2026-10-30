"""Integration tests: selection and resize-handle behaviour.

Regression coverage for fix #121 / #122:
  - Mid-edge handles (MIDDLE_LEFT, MIDDLE_RIGHT) must constrain to X-axis only.
  - Mid-edge handles (TOP_CENTER, BOTTOM_CENTER) must constrain to Y-axis only.
  - Corner handles may change both axes freely.

Workflow per test:
  1. Draw rectangle via RectangleTool (tool → press → move → release).
  2. Select the resulting item and show its resize handles.
  3. Set up handle drag state (initial rect + drag start).
  4. Call _apply_resize with a diagonal delta.
  5. Assert that only the expected axis changed.
"""

# ruff: noqa: ARG002

from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtTest import QTest

from open_garden_planner.core.tools import ToolType
from open_garden_planner.ui.canvas.canvas_view import CanvasView
from open_garden_planner.ui.canvas.items import RectangleItem
from open_garden_planner.ui.canvas.items.resize_handle import (
    HandlePosition,
    ResizeHandle,
    RotationHandle,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _draw_rect(view: CanvasView, x1: float, y1: float, x2: float, y2: float) -> RectangleItem:
    """Draw a rectangle and return the resulting item."""
    event = MagicMock(spec=QMouseEvent)
    event.button.return_value = Qt.MouseButton.LeftButton
    event.modifiers.return_value = Qt.KeyboardModifier.NoModifier

    view.set_active_tool(ToolType.RECTANGLE)
    tool = view.tool_manager.active_tool
    tool.mouse_press(event, QPointF(x1, y1))
    tool.mouse_move(event, QPointF(x2, y2))
    tool.mouse_release(event, QPointF(x2, y2))

    items = [i for i in view.scene().items() if isinstance(i, RectangleItem)]
    assert items, "Rectangle should have been created"
    return items[0]


def _get_handle(item: RectangleItem, position: HandlePosition) -> object:
    """Return the ResizeHandle for the given position."""
    item.show_resize_handles()
    for handle in item._resize_handles:
        if handle._position == position:
            return handle
    raise AssertionError(f"Handle {position} not found on item")


def _setup_handle_drag(handle: object, item: RectangleItem) -> None:
    """Prime a handle for drag testing (sets internal drag state)."""

    handle._is_dragging = True
    handle._drag_start_pos = QPointF(0, 0)
    if hasattr(item, "rect") and callable(item.rect):
        handle._initial_rect = item.rect()
    else:
        handle._initial_rect = item.boundingRect()
    handle._initial_parent_pos = item.pos()


# ---------------------------------------------------------------------------
# Selection basics
# ---------------------------------------------------------------------------


class TestSelection:
    """Basic item selection behaviour."""

    def test_item_selected_programmatically(self, canvas: CanvasView, qtbot: object) -> None:
        """An item can be selected and reports isSelected() == True."""
        item = _draw_rect(canvas, 100, 100, 400, 300)
        item.setSelected(True)
        assert item.isSelected()

    def test_deselect(self, canvas: CanvasView, qtbot: object) -> None:
        """Clearing the scene selection deselects all items."""
        item = _draw_rect(canvas, 100, 100, 400, 300)
        item.setSelected(True)
        canvas.scene().clearSelection()
        assert not item.isSelected()

    def test_multiple_items_one_selected(self, canvas: CanvasView, qtbot: object) -> None:
        """Selecting one item does not auto-select others."""
        item_a = _draw_rect(canvas, 100, 100, 300, 200)
        item_b = _draw_rect(canvas, 500, 100, 700, 200)
        item_a.setSelected(True)
        assert item_a.isSelected()
        assert not item_b.isSelected()


# ---------------------------------------------------------------------------
# Resize handle axis constraints (Regression #121 / #122)
# ---------------------------------------------------------------------------


class TestResizeHandleConstraints:
    """Regression tests for mid-edge resize axis constraints.

    Fix #121/#122: MIDDLE_LEFT/RIGHT handles must not change item height;
    TOP_CENTER/BOTTOM_CENTER handles must not change item width.
    """

    def test_middle_left_only_changes_x(self, canvas: CanvasView, qtbot: object) -> None:
        """MIDDLE_LEFT handle: diagonal drag changes width but not height."""
        item = _draw_rect(canvas, 100, 100, 400, 300)
        initial_height = item.rect().height()

        handle = _get_handle(item, HandlePosition.MIDDLE_LEFT)
        _setup_handle_drag(handle, item)

        # Diagonal delta — both x and y are non-zero
        handle._apply_resize(QPointF(50.0, 50.0))

        assert item.rect().height() == pytest.approx(initial_height, abs=0.01), (
            "MIDDLE_LEFT should not affect height"
        )

    def test_middle_right_only_changes_x(self, canvas: CanvasView, qtbot: object) -> None:
        """MIDDLE_RIGHT handle: diagonal drag changes width but not height."""
        item = _draw_rect(canvas, 100, 100, 400, 300)
        initial_height = item.rect().height()

        handle = _get_handle(item, HandlePosition.MIDDLE_RIGHT)
        _setup_handle_drag(handle, item)

        handle._apply_resize(QPointF(50.0, 50.0))

        assert item.rect().height() == pytest.approx(initial_height, abs=0.01), (
            "MIDDLE_RIGHT should not affect height"
        )

    def test_top_center_only_changes_y(self, canvas: CanvasView, qtbot: object) -> None:
        """TOP_CENTER handle: diagonal drag changes height but not width."""
        item = _draw_rect(canvas, 100, 100, 400, 300)
        initial_width = item.rect().width()

        handle = _get_handle(item, HandlePosition.TOP_CENTER)
        _setup_handle_drag(handle, item)

        handle._apply_resize(QPointF(50.0, 50.0))

        assert item.rect().width() == pytest.approx(initial_width, abs=0.01), (
            "TOP_CENTER should not affect width"
        )

    def test_bottom_center_only_changes_y(self, canvas: CanvasView, qtbot: object) -> None:
        """BOTTOM_CENTER handle: diagonal drag changes height but not width."""
        item = _draw_rect(canvas, 100, 100, 400, 300)
        initial_width = item.rect().width()

        handle = _get_handle(item, HandlePosition.BOTTOM_CENTER)
        _setup_handle_drag(handle, item)

        handle._apply_resize(QPointF(50.0, 50.0))

        assert item.rect().width() == pytest.approx(initial_width, abs=0.01), (
            "BOTTOM_CENTER should not affect width"
        )

    def test_corner_handle_changes_both_axes(self, canvas: CanvasView, qtbot: object) -> None:
        """TOP_LEFT corner handle: diagonal drag changes both width and height."""
        item = _draw_rect(canvas, 100, 100, 400, 300)
        initial_width = item.rect().width()
        initial_height = item.rect().height()

        handle = _get_handle(item, HandlePosition.TOP_LEFT)
        _setup_handle_drag(handle, item)

        handle._apply_resize(QPointF(50.0, 50.0))

        assert item.rect().width() != pytest.approx(initial_width, abs=0.01), (
            "TOP_LEFT should change width"
        )
        assert item.rect().height() != pytest.approx(initial_height, abs=0.01), (
            "TOP_LEFT should change height"
        )

    def test_resize_enforces_minimum_size(self, canvas: CanvasView, qtbot: object) -> None:
        """Resizing below 1 cm is clamped to the minimum size (1 cm)."""
        item = _draw_rect(canvas, 100, 100, 110, 110)  # 10x10 cm

        handle = _get_handle(item, HandlePosition.MIDDLE_RIGHT)
        _setup_handle_drag(handle, item)

        # Delta moves right edge far to the left (would produce negative width)
        handle._apply_resize(QPointF(-500.0, 0.0))

        assert item.rect().width() >= 1.0, "Width must not drop below 1 cm minimum"


@pytest.mark.parametrize("angle", [0.0, 37.0, 143.0])
@pytest.mark.parametrize("handle_type", [ResizeHandle, RotationHandle])
def test_shift_drag_handle_does_not_select_the_item_behind(
    canvas: CanvasView, qtbot, angle: float, handle_type: type,
) -> None:
    """#522: see through locked objects, but deliver handle presses to Qt."""
    canvas.resize(1000, 800)
    canvas.set_active_tool(ToolType.SELECT)
    background = RectangleItem(600, 600, 1200, 1200)
    foreground = RectangleItem(1000, 1000, 400, 300)
    canvas.scene().addItem(background)
    canvas.scene().addItem(foreground)
    foreground._apply_rotation(angle)
    foreground.setSelected(True)
    canvas.show()
    qtbot.waitExposed(canvas)
    canvas.centerOn(foreground.mapToScene(foreground.rect().center()))
    handle = next(
        child for child in foreground.childItems()
        if isinstance(child, handle_type)
        and canvas.scene().itemAt(child.scenePos(), canvas.transform()) is child
    )
    start = canvas.mapFromScene(handle.scenePos())
    before_rect = QRectF(foreground.rect())
    before_pos = QPointF(foreground.pos())
    before_angle = foreground.rotation()
    undo_count = len(canvas.command_manager._undo_stack)
    modifiers = Qt.KeyboardModifier.ShiftModifier
    QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, modifiers, start)
    try:
        assert canvas.scene().mouseGrabberItem() is handle
        assert canvas._active_drag_handle is handle
        assert not background.isSelected()
        target = canvas.mapFromScene(handle.scenePos() + QPointF(80, 65))
        QTest.mouseMove(canvas.viewport(), target)
    finally:
        QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, modifiers, start)
    assert foreground.isSelected()
    assert not background.isSelected()
    if handle_type is RotationHandle:
        assert foreground.rotation() != pytest.approx(before_angle)
    else:
        assert foreground.rect() != before_rect
    assert len(canvas.command_manager._undo_stack) == undo_count + 1
    canvas.command_manager.undo()
    assert foreground.rect() == before_rect
    assert foreground.pos() == before_pos
    assert foreground.rotation() == pytest.approx(before_angle)


@pytest.mark.parametrize("angle", [0.0, 37.0])
def test_shift_click_through_locked_item_toggles(canvas, qtbot, angle):
    background = RectangleItem(1000, 1000, 400, 300)
    locked = RectangleItem(1000, 1000, 400, 300)
    other = RectangleItem(1500, 1000, 100, 100)
    for item in (background, locked, other):
        canvas.scene().addItem(item)
    background.rotation_angle = angle
    locked.rotation_angle = angle
    locked.setZValue(100)
    from PyQt6.QtWidgets import QGraphicsItem
    locked.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
    other.setSelected(True)
    canvas.set_active_tool(ToolType.SELECT)
    canvas.resize(1000, 800)
    canvas.show()
    qtbot.waitExposed(canvas)
    point = background.mapToScene(background.rect().center())
    canvas.centerOn(point)
    position = canvas.mapFromScene(point)
    for selected in (True, False):
        QTest.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.ShiftModifier, position)
        assert background.isSelected() is selected
        assert other.isSelected()
        assert not locked.isSelected()


@pytest.mark.parametrize("zoom", [0.4, 1.0, 2.0])
@pytest.mark.parametrize("target", ["label", "symbol"])
def test_shift_click_reference_keeps_its_selection_handler(
    canvas, qtbot, monkeypatch, zoom, target,
):
    from open_garden_planner.core.reference_points import is_reference_point
    from open_garden_planner.ui import reference_points
    from open_garden_planner.ui.canvas.items import PolygonItem

    terrain = PolygonItem([QPointF(0, 0), QPointF(2000, 0),
                           QPointF(2000, 1200), QPointF(0, 1200)])
    canvas.scene().addItem(terrain)
    monkeypatch.setattr(reference_points, "point_dialog",
                        lambda *_args: reference_points.PointProperties("Borne", fixed_size=False))
    assert reference_points.place_reference_point(canvas, QPointF(500, 400))
    point = next(item for item in canvas.scene().items() if is_reference_point(item))
    canvas.scene().clearSelection()
    canvas.set_active_tool(ToolType.SELECT)
    canvas.resize(850, 600)
    canvas.show()
    qtbot.waitExposed(canvas)
    canvas.set_zoom(zoom)
    canvas.centerOn(point.pos())
    label = point._label_item
    position = label.deviceTransform(canvas.viewportTransform()).map(
        label.boundingRect().center()).toPoint()
    if target == "symbol":
        position = canvas.mapFromScene(point.pos()) + QPoint(-8, 0)
    expected = point if target == "symbol" else label
    assert canvas.scene().itemAt(canvas.mapToScene(position), canvas.viewportTransform()) is expected
    for selected in (True, False):
        QTest.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.ShiftModifier, position)
        assert point.isSelected() is selected
        assert not terrain.isSelected()
