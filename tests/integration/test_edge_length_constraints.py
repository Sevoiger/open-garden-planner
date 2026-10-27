"""Integration tests for direct edge-length constraints (issue #140)."""

# ruff: noqa: ARG002

from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QLineF, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtTest import QTest

from open_garden_planner.core.constraints import ConstraintType
from open_garden_planner.core.tools import ToolType
from open_garden_planner.ui.canvas.canvas_view import CanvasView
from open_garden_planner.ui.canvas.items import PolygonItem


@pytest.mark.parametrize("angle", [0, 31])
@pytest.mark.parametrize("edge_index", [0, 3])
@pytest.mark.parametrize("with_reference", [False, True])
def test_side_length_can_be_applied_away_from_midpoint(canvas, qtbot, monkeypatch,
                                                    angle, edge_index, with_reference):
    from open_garden_planner.ui.canvas.items import CircleItem

    points = [QPointF(200, 200), QPointF(1200, 200), QPointF(1200, 1200), QPointF(200, 1200)]
    polygon = PolygonItem(points)
    canvas.scene().addItem(polygon)
    polygon._apply_rotation(angle)
    pick_local = points[edge_index] * 0.75 + points[(edge_index + 1) % 4] * 0.25
    pick = polygon.mapToScene(pick_local)
    if with_reference:
        point = CircleItem(pick.x(), pick.y(), 8, metadata={"reference_point": True,
                                                         "reference_point_on_contour": True})
        point.parent_bed_id = polygon.item_id
        polygon.add_child_id(point.item_id)
        canvas.scene().addItem(point)
    canvas.resize(850, 600)
    canvas._zoom_factor = 0.25
    canvas._apply_transform()
    canvas.centerOn(pick)
    canvas.show()
    qtbot.wait(20)
    opened = []

    class AcceptedDialog:
        def __init__(self, current_length, _parent):
            opened.append(current_length)

        def exec(self):
            return 1

        def distance_cm(self):
            return 1100.0

    monkeypatch.setattr("open_garden_planner.core.tools.constraint_tool.DistanceInputDialog", AcceptedDialog)
    canvas.set_active_tool(ToolType.CONSTRAINT_EDGE_LENGTH)
    QTest.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=canvas.mapFromScene(pick))
    assert opened == pytest.approx([1000])
    constraints = list(canvas.scene().constraint_graph.constraints.values())
    assert len(constraints) == 1
    assert constraints[0].constraint_type == ConstraintType.EDGE_LENGTH
    assert constraints[0].anchor_a.item_id == polygon.item_id
    assert constraints[0].anchor_b.item_id == polygon.item_id
    after = polygon.polygon()
    assert QLineF(polygon.mapToScene(after.at(edge_index)),
                  polygon.mapToScene(after.at((edge_index + 1) % 4))).length() == pytest.approx(1100, abs=0.02)
    canvas.command_manager.undo()
    assert list(polygon.polygon()) == points


@pytest.mark.parametrize("protection", ["locked", "hidden"])
def test_length_picker_does_not_change_protected_or_hidden_polygon(canvas, qtbot, monkeypatch, protection):
    from open_garden_planner.models.layer import Layer

    scene = canvas.scene()
    points = [QPointF(200, 200), QPointF(1200, 200), QPointF(1200, 1200), QPointF(200, 1200)]
    visible = PolygonItem(points, layer_id=scene.active_layer.id)
    scene.addItem(visible)
    layer = Layer(name="Protected", z_order=10)
    scene.add_layer(layer)
    protected = PolygonItem(points, layer_id=layer.id)
    scene.addItem(protected)
    if protection == "locked":
        scene.update_layer_lock(layer.id, True)
    else:
        scene.update_layer_visibility(layer.id, False)

    class AcceptedDialog:
        def __init__(self, *_args):
            pass

        def exec(self):
            return 1

        def distance_cm(self):
            return 1100.0

    monkeypatch.setattr("open_garden_planner.core.tools.constraint_tool.DistanceInputDialog", AcceptedDialog)
    canvas.resize(850, 600)
    canvas._zoom_factor = 0.25
    canvas._apply_transform()
    canvas.centerOn(450, 200)
    canvas.show()
    qtbot.wait(20)
    canvas.set_active_tool(ToolType.CONSTRAINT_EDGE_LENGTH)
    QTest.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=canvas.mapFromScene(QPointF(450, 200)))
    constraints = list(scene.constraint_graph.constraints.values())
    assert len(constraints) == 1
    assert constraints[0].anchor_a.item_id == visible.item_id
    assert list(protected.polygon()) == points


def _left_click_event() -> MagicMock:
    event = MagicMock(spec=QMouseEvent)
    event.button.return_value = Qt.MouseButton.LeftButton
    event.buttons.return_value = Qt.MouseButton.LeftButton
    event.modifiers.return_value = Qt.KeyboardModifier.NoModifier
    return event


def _draw_polygon(view: CanvasView) -> PolygonItem:
    event = _left_click_event()
    view.set_active_tool(ToolType.POLYGON)
    tool = view.tool_manager.active_tool
    tool.mouse_press(event, QPointF(100, 100))
    tool.mouse_press(event, QPointF(300, 100))
    tool.mouse_press(event, QPointF(300, 250))
    tool.mouse_press(event, QPointF(100, 250))
    tool.mouse_double_click(event, QPointF(100, 250))
    polygons = [i for i in view.scene().items() if isinstance(i, PolygonItem)]
    return polygons[0]


class TestEdgeLengthConstraintWorkflow:
    def test_click_edge_creates_edge_length_constraint(
        self, canvas: CanvasView, qtbot: object
    ) -> None:
        polygon = _draw_polygon(canvas)
        event = _left_click_event()
        canvas.set_active_tool(ToolType.CONSTRAINT_EDGE_LENGTH)
        tool = canvas.tool_manager.active_tool

        class _AcceptedDialog:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def exec(self) -> int:
                return 1

            def distance_cm(self) -> float:
                return 250.0

        with patch(
            "open_garden_planner.core.tools.constraint_tool.DistanceInputDialog",
            _AcceptedDialog,
        ):
            tool.mouse_press(event, QPointF(200, 100))

        constraints = list(canvas._canvas_scene.constraint_graph.constraints.values())
        assert len(constraints) == 1
        constraint = constraints[0]
        assert constraint.constraint_type == ConstraintType.EDGE_LENGTH
        assert constraint.anchor_a.item_id == polygon.item_id
        assert constraint.anchor_b.item_id == polygon.item_id
        assert constraint.anchor_a.anchor_index != constraint.anchor_b.anchor_index

    def test_click_edge_reshapes_polygon_to_target_length(
        self, canvas: CanvasView, qtbot: object
    ) -> None:
        polygon = _draw_polygon(canvas)
        event = _left_click_event()
        canvas.set_active_tool(ToolType.CONSTRAINT_EDGE_LENGTH)
        tool = canvas.tool_manager.active_tool

        class _AcceptedDialog:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def exec(self) -> int:
                return 1

            def distance_cm(self) -> float:
                return 250.0

        with patch(
            "open_garden_planner.core.tools.constraint_tool.DistanceInputDialog",
            _AcceptedDialog,
        ):
            tool.mouse_press(event, QPointF(200, 100))

        updated_polygon = polygon.polygon()
        top_left = polygon.mapToScene(updated_polygon.at(0))
        top_right = polygon.mapToScene(updated_polygon.at(1))
        assert abs(top_right.x() - top_left.x() - 250.0) < 1.0
        assert abs(top_right.y() - top_left.y()) < 0.1

    def test_edge_length_constraint_enforced_after_vertex_drag(
        self, canvas: CanvasView, qtbot: object
    ) -> None:
        """After a vertex drag violates an EDGE_LENGTH constraint, the enforcement
        path (_enforce_after_vertex_drag) must correct the vertex back to satisfy it."""
        polygon = _draw_polygon(canvas)
        event = _left_click_event()
        canvas.set_active_tool(ToolType.CONSTRAINT_EDGE_LENGTH)
        tool = canvas.tool_manager.active_tool

        class _AcceptedDialog:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def exec(self) -> int:
                return 1

            def distance_cm(self) -> float:
                return 250.0

        with patch(
            "open_garden_planner.core.tools.constraint_tool.DistanceInputDialog",
            _AcceptedDialog,
        ):
            tool.mouse_press(event, QPointF(200, 100))

        constraints = list(canvas._canvas_scene.constraint_graph.constraints.values())
        assert len(constraints) == 1
        constraint = constraints[0]
        anchor_a_idx = constraint.anchor_a.anchor_index
        anchor_b_idx = constraint.anchor_b.anchor_index

        # Record anchor_a's current (post-creation) local position.
        pg = polygon.polygon()
        old_local = QPointF(pg.at(anchor_a_idx))

        # Violate the constraint by moving anchor_a 80 cm in x.
        violation_local = QPointF(old_local.x() + 80, old_local.y())
        polygon._move_vertex_to(anchor_a_idx, violation_local)

        # Confirm the constraint is actually violated before enforcement.
        pg2 = polygon.polygon()
        p_a = polygon.mapToScene(pg2.at(anchor_a_idx))
        p_b = polygon.mapToScene(pg2.at(anchor_b_idx))
        assert abs(QLineF(p_a, p_b).length() - 250.0) > 10.0

        # Simulate the deferred-undo mechanism that mouseReleaseEvent sets up.
        canvas._deferred_vertex_move = (polygon, anchor_a_idx, old_local, violation_local)
        canvas._enforce_after_vertex_drag()

        # Assert the edge length is back within tolerance.
        pg3 = polygon.polygon()
        p_a2 = polygon.mapToScene(pg3.at(anchor_a_idx))
        p_b2 = polygon.mapToScene(pg3.at(anchor_b_idx))
        assert abs(QLineF(p_a2, p_b2).length() - 250.0) < 1.0
