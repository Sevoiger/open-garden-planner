"""MCP geometry edits must preserve constraints, rather than forbid all of them."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from PyQt6.QtCore import QPointF

from open_garden_planner.core.constraints import AnchorRef, ConstraintType
from open_garden_planner.core.measure_snapper import AnchorType
from open_garden_planner.ui.canvas.items import RectangleItem
from tests.integration.test_agent_api_writes import (
    _APP_BY_VIEW,
)
from tests.integration.test_agent_api_writes import (
    canvas as canvas,
)


@pytest.mark.parametrize("angle", [0, 37])
def test_rectangle_with_fixed_edge_size_can_translate_and_keep_undo(canvas: Any, angle: float) -> None:
    app = _APP_BY_VIEW[id(canvas)]
    scene = canvas.scene()
    item = RectangleItem(300, 300, 200, 100, layer_id=scene.active_layer.id)
    scene.addItem(item)
    item._apply_rotation(angle)
    corners = [item.mapToScene(item.rect().topLeft()), item.mapToScene(item.rect().topRight())]
    constraint = scene.constraint_graph.add_constraint(
        AnchorRef(item.item_id, AnchorType.CORNER, 0),
        AnchorRef(item.item_id, AnchorType.CORNER, 1),
        200, constraint_type=ConstraintType.EDGE_LENGTH,
    )
    baseline = deepcopy(app._project_manager._serialize_item(item))
    constraints = deepcopy(scene.constraint_graph.to_list())
    result = app._do_agent_move_object(str(item.item_id), 40, 25)
    assert result["action"] == "move"
    assert item.pos() == QPointF(40, 25)
    assert item.rect().width() == 200 and item.rect().height() == 100
    moved_corners = [item.mapToScene(item.rect().topLeft()), item.mapToScene(item.rect().topRight())]
    for old, new in zip(corners, moved_corners, strict=True):
        assert new.x() - old.x() == pytest.approx(40)
        assert new.y() - old.y() == pytest.approx(25)
    from PyQt6.QtCore import QLineF

    assert QLineF(*moved_corners).length() == pytest.approx(200)
    assert scene.constraint_graph.to_list() == constraints
    assert constraint.constraint_id in scene.constraint_graph.constraints
    canvas.command_manager.undo()
    assert app._project_manager._serialize_item(item) == baseline
    assert not canvas.command_manager.can_undo
    canvas.command_manager.redo()
    assert item.pos() == QPointF(40, 25)


@pytest.mark.parametrize(("kind", "allowed", "refused"), [
    (ConstraintType.HORIZONTAL, (40, 0), (0, 20)),
    (ConstraintType.VERTICAL_DISTANCE, (40, 0), (0, 20)),
    (ConstraintType.VERTICAL, (0, 40), (20, 0)),
    (ConstraintType.HORIZONTAL_DISTANCE, (0, 40), (20, 0)),
])
def test_axis_constraints_allow_the_free_axis_and_refuse_the_other(canvas, kind, allowed, refused):
    app = _APP_BY_VIEW[id(canvas)]
    scene = canvas.scene()
    item = RectangleItem(300, 300, 200, 100, layer_id=scene.active_layer.id)
    other_x, other_y = (300, 700) if kind in {ConstraintType.VERTICAL, ConstraintType.VERTICAL_DISTANCE} else (700, 300)
    other = RectangleItem(other_x, other_y, 200, 100, layer_id=scene.active_layer.id)
    scene.addItem(item)
    scene.addItem(other)
    constraint = scene.constraint_graph.add_constraint(
        AnchorRef(item.item_id, AnchorType.CENTER), AnchorRef(other.item_id, AnchorType.CENTER),
        400 if kind in {ConstraintType.HORIZONTAL_DISTANCE, ConstraintType.VERTICAL_DISTANCE} else 0,
        constraint_type=kind,
    )
    assert app.constraints_panel._is_satisfied(constraint)
    graph = deepcopy(scene.constraint_graph.to_list())
    app._do_agent_move_object(str(item.item_id), *allowed)
    assert item.pos() == QPointF(*allowed)
    assert app.constraints_panel._is_satisfied(constraint)
    before_refusal = app._project_manager._serialize_item(item)
    with pytest.raises(ValueError, match="would violate geometric constraint"):
        app._do_agent_move_object(str(item.item_id), *refused)
    assert app._project_manager._serialize_item(item) == before_refusal
    assert scene.constraint_graph.to_list() == graph
    canvas.command_manager.undo()
    assert item.pos() == QPointF(0, 0)
    assert not canvas.command_manager.can_undo


def test_group_internal_relation_can_move_but_a_fixed_child_cannot(canvas):
    from open_garden_planner.ui.canvas.items.group_item import GroupItem

    app = _APP_BY_VIEW[id(canvas)]
    scene = canvas.scene()
    group = GroupItem(layer_id=scene.active_layer.id)
    scene.addItem(group)
    items = [RectangleItem(300 + 300*i, 300, 200, 100, layer_id=scene.active_layer.id) for i in range(2)]
    for item in items:
        scene.addItem(item)
        group.addToGroup(item)
    scene.constraint_graph.add_constraint(
        AnchorRef(items[0].item_id, AnchorType.CENTER), AnchorRef(items[1].item_id, AnchorType.CENTER), 300,
    )
    initial = [i.scenePos() for i in items]
    app._do_agent_move_object(str(group.item_id), 40, 25)
    assert [i.scenePos() for i in items] == [pos + QPointF(40, 25) for pos in initial]
    canvas.command_manager.undo()
    assert [i.scenePos() for i in items] == initial
    fixed = scene.constraint_graph.add_constraint(
        AnchorRef(items[1].item_id, AnchorType.CENTER), AnchorRef(items[1].item_id, AnchorType.CENTER),
        0, constraint_type=ConstraintType.FIXED, target_x=initial[1].x(), target_y=initial[1].y(),
    )
    with pytest.raises(ValueError, match=str(fixed.constraint_id)):
        app._do_agent_move_object(str(group.item_id), 40, 25)
    assert [i.scenePos() for i in items] == initial
    assert not canvas.command_manager.can_undo


def test_carried_plant_relation_allows_common_motion_but_external_fixed_blocks(canvas):
    from open_garden_planner.core.object_types import ObjectType
    from open_garden_planner.ui.canvas.items import CircleItem

    app = _APP_BY_VIEW[id(canvas)]
    scene = canvas.scene()
    bed = RectangleItem(300, 300, 500, 300, object_type=ObjectType.GARDEN_BED, layer_id=scene.active_layer.id)
    plant = CircleItem(500, 450, 30, object_type=ObjectType.PERENNIAL, layer_id=scene.active_layer.id)
    scene.addItem(bed)
    scene.addItem(plant)
    bed.add_child_id(plant.item_id)
    plant.parent_bed_id = bed.item_id
    scene.constraint_graph.add_constraint(
        AnchorRef(bed.item_id, AnchorType.CENTER), AnchorRef(plant.item_id, AnchorType.CENTER), 50,
    )
    app._do_agent_move_object(str(bed.item_id), 20, 30)
    assert bed.pos() == plant.pos() == QPointF(20, 30)
    canvas.command_manager.undo()
    fixed = scene.constraint_graph.add_constraint(
        AnchorRef(plant.item_id, AnchorType.CENTER), AnchorRef(plant.item_id, AnchorType.CENTER),
        0, constraint_type=ConstraintType.FIXED, target_x=0, target_y=0,
    )
    with pytest.raises(ValueError, match=str(fixed.constraint_id)):
        app._do_agent_move_object(str(bed.item_id), 20, 30)
    assert bed.pos() == plant.pos() == QPointF(0, 0)
    assert not canvas.command_manager.can_undo


def test_size_constrained_rectangle_roundtrips_through_real_mcp(canvas, qtbot, tmp_path):
    from open_garden_planner.agent_api import AgentApiServer
    from tests.integration.test_agent_api_writes import TOKEN, _free_port, _providers, _run
    from tests.integration.test_garden_memory_mcp import unpack

    app = _APP_BY_VIEW[id(canvas)]
    scene = canvas.scene()
    item = RectangleItem(300, 300, 200, 100, layer_id=scene.active_layer.id)
    item.set_metadata("owner_extension", {"keep": True})
    scene.addItem(item)
    for first, second, length in ((0, 1, 200), (1, 2, 100)):
        scene.constraint_graph.add_constraint(
            AnchorRef(item.item_id, AnchorType.CORNER, first),
            AnchorRef(item.item_id, AnchorType.CORNER, second), length,
            constraint_type=ConstraintType.EDGE_LENGTH,
        )
    constraints = deepcopy(scene.constraint_graph.to_list())
    saved_id = item.item_id
    path = tmp_path / "constrained-translation.ogp"
    server = AgentApiServer(_providers(canvas), port=_free_port(), write_token=TOKEN, writes_enabled=True)
    server.start()

    async def body(context):
        http_client, ClientSession, url = context
        async with http_client(url) as (r, w, _), ClientSession(r, w) as session:
            await session.initialize()
            refused = await session.call_tool("move_object", {"item_id": str(saved_id), "dx": 40, "dy": 25})
            assert refused.isError
        async with (http_client(url, headers={"Authorization": f"Bearer {TOKEN}"}) as (r, w, _),
                    ClientSession(r, w) as session):
            await session.initialize()
            moved = unpack(await session.call_tool("move_object", {"item_id": str(saved_id), "dx": 40, "dy": 25}))
            assert (moved["x"], moved["y"]) == (440, 375)
            positioned = unpack(await session.call_tool("set_object_position", {"item_id": str(saved_id), "x": 800, "y": 700}))
            assert (positioned["x"], positioned["y"]) == (800, 700)
            assert (await session.call_tool("resize_object", {"item_id": str(saved_id), "width": 250})).isError
            geometry = unpack(await session.call_tool("get_geometry", {"item_id": str(saved_id)}))
            assert geometry["is_constrained"] and len(geometry["constraints"]) == 2
            assert (geometry["width_cm"], geometry["height_cm"]) == (200, 100)
            assert unpack(await session.call_tool("get_history", {}))["undo_depth"] == 2
            assert not (await session.call_tool("undo", {})).isError
            undone = unpack(await session.call_tool("get_geometry", {"item_id": str(saved_id)}))
            assert (undone["center_x_cm"], undone["center_y_cm"]) == (440, 375)
            assert not (await session.call_tool("redo", {})).isError
            assert not (await session.call_tool("save_plan", {"file_path": str(path)})).isError

    try:
        _run(server, body, qtbot)
    finally:
        server.stop()
    app._project_manager.load(scene, path)
    restored = scene.find_item_by_id(saved_id)
    assert app._agent_item_center(restored) == (800, 700)
    assert restored.rect().width() == 200 and restored.rect().height() == 100
    assert restored.metadata["owner_extension"] == {"keep": True}
    assert scene.constraint_graph.to_list() == constraints


def test_axis_compatibility_uses_the_actual_clamped_move(canvas):
    app = _APP_BY_VIEW[id(canvas)]
    scene = canvas.scene()
    item = RectangleItem(300, 300, 200, 100, layer_id=scene.active_layer.id)
    other = RectangleItem(700, 300, 200, 100, layer_id=scene.active_layer.id)
    scene.addItem(item)
    scene.addItem(other)
    edge_y = scene.canvas_rect.height() - item.sceneBoundingRect().bottom()
    item.setPos(0, edge_y)
    other.setPos(0, edge_y)
    scene.constraint_graph.add_constraint(
        AnchorRef(item.item_id, AnchorType.CENTER), AnchorRef(other.item_id, AnchorType.CENTER),
        0, constraint_type=ConstraintType.HORIZONTAL,
    )
    app._do_agent_move_object(str(item.item_id), 40, 50)
    assert item.pos() == QPointF(40, edge_y)
    assert app._agent_item_center(item)[1] == app._agent_item_center(other)[1]
    canvas.command_manager.undo()
    assert item.pos() == QPointF(0, edge_y)


def test_linked_roof_constraints_are_checked_before_moving_the_house(canvas):
    from open_garden_planner.core.object_types import ObjectType
    from open_garden_planner.ui.canvas.items import PolygonItem, PolylineItem

    app = _APP_BY_VIEW[id(canvas)]
    scene = canvas.scene()
    house = PolygonItem([QPointF(300, 300), QPointF(700, 300), QPointF(700, 600), QPointF(300, 600)],
                        object_type=ObjectType.HOUSE, layer_id=scene.active_layer.id)
    ridge = PolylineItem([QPointF(300, 450), QPointF(700, 450)],
                         object_type=ObjectType.ROOF_RIDGE, layer_id=scene.active_layer.id)
    scene.addItem(house)
    scene.addItem(ridge)
    house.set_metadata("ridge_item_id", str(ridge.item_id))
    scene.constraint_graph.add_constraint(
        AnchorRef(ridge.item_id, AnchorType.ENDPOINT, 0), AnchorRef(ridge.item_id, AnchorType.ENDPOINT, 1),
        400, constraint_type=ConstraintType.EDGE_LENGTH,
    )
    app._do_agent_move_object(str(house.item_id), 20, 30)
    assert ridge.points == [QPointF(320, 480), QPointF(720, 480)]
    canvas.command_manager.undo()
    baseline = deepcopy(app._project_manager._serialize_item(house)), deepcopy(app._project_manager._serialize_item(ridge))
    fixed = scene.constraint_graph.add_constraint(
        AnchorRef(ridge.item_id, AnchorType.CENTER), AnchorRef(ridge.item_id, AnchorType.CENTER),
        0, constraint_type=ConstraintType.FIXED, target_x=0, target_y=0,
    )
    with pytest.raises(ValueError, match=str(fixed.constraint_id)):
        app._do_agent_move_object(str(house.item_id), 20, 30)
    assert (app._project_manager._serialize_item(house), app._project_manager._serialize_item(ridge)) == baseline
    assert not canvas.command_manager.can_undo


def test_transformed_ridge_uses_its_actual_world_shift_not_the_house_delta(canvas):
    from open_garden_planner.core.object_types import ObjectType
    from open_garden_planner.ui.canvas.items import PolygonItem, PolylineItem

    app = _APP_BY_VIEW[id(canvas)]
    scene = canvas.scene()
    house = PolygonItem([QPointF(300, 300), QPointF(700, 300), QPointF(700, 600), QPointF(300, 600)],
                        object_type=ObjectType.HOUSE, layer_id=scene.active_layer.id)
    ridge = PolylineItem([QPointF(350, 450), QPointF(750, 450)],
                         object_type=ObjectType.ROOF_RIDGE, layer_id=scene.active_layer.id)
    scene.addItem(house)
    scene.addItem(ridge)
    house.set_metadata("ridge_item_id", str(ridge.item_id))
    app._do_agent_rotate_object(str(ridge.item_id), 90, False)
    canvas.command_manager.clear()
    constraint = scene.constraint_graph.add_constraint(
        AnchorRef(house.item_id, AnchorType.CENTER), AnchorRef(ridge.item_id, AnchorType.CENTER), 50,
    )
    baseline = deepcopy(app._project_manager._serialize_item(house)), deepcopy(app._project_manager._serialize_item(ridge))
    with pytest.raises(ValueError, match=str(constraint.constraint_id)):
        app._do_agent_move_object(str(house.item_id), 40, 0)
    assert (app._project_manager._serialize_item(house), app._project_manager._serialize_item(ridge)) == baseline
    assert not canvas.command_manager.can_undo
