"""Real MCP client → production Qt providers → undo → save → reopen."""

import json

from PyQt6.QtWidgets import QMessageBox

from open_garden_planner.agent_api import AgentApiServer
from open_garden_planner.app.application import GardenPlannerApp
from open_garden_planner.core.object_types import ObjectType
from open_garden_planner.core.project import ProjectManager
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.canvas.items import CircleItem, RectangleItem
from tests.integration.test_agent_api_writes import TOKEN, _free_port, _run


def unpack(call):
    assert not call.isError, call.content
    return json.loads(next(block.text for block in call.content if block.type == "text"))


def unpack_list(call):
    assert not call.isError, call.content
    values = [json.loads(block.text) for block in call.content if block.type == "text"]
    return values[0] if len(values) == 1 and isinstance(values[0], list) else values


def test_mcp_tracking_auth_idempotence_and_durable_roundtrip(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(GardenPlannerApp, "_startup_sequence", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_start_update_check", lambda _self: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Discard)
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    bed = RectangleItem(100, 100, 600, 300, object_type=ObjectType.GARDEN_BED)
    plant = CircleItem(300, 250, 30, object_type=ObjectType.PERENNIAL)
    bed.name, plant.name = "Zone fictive", "Bulbes fictifs"
    window.canvas_scene.addItem(bed)
    window.canvas_scene.addItem(plant)
    plant_id, zone_id = str(plant.item_id), str(bed.item_id)
    server = AgentApiServer(window._build_agent_providers(), port=_free_port(),
                            write_token=TOKEN, writes_enabled=True)
    server.start()
    results = {}
    path = tmp_path / "mcp-garden.ogp"

    async def body(context):
        http_client, ClientSession, url = context
        async with http_client(url) as (reader, writer, _), ClientSession(reader, writer) as session:
            await session.initialize()
            results["read"] = unpack(await session.call_tool("get_garden_record", {"item_id": plant_id}))
            denied = await session.call_tool("set_garden_card", {"item_id": plant_id, "fields": {"kind": "group"}})
            assert denied.isError
        async with (http_client(url, headers={"Authorization": f"Bearer {TOKEN}"}) as (reader, writer, _),
                    ClientSession(reader, writer) as session):
            await session.initialize()
            card = unpack(await session.call_tool("set_garden_card", {"item_id": plant_id,
                           "fields": {"kind": "group", "state": "dormant", "period": "Automne 2023", "zone_id": zone_id}}))
            assert card["persisted"] is False
            members = unpack_list(await session.call_tool("list_zone_plantings", {"zone_id": zone_id}))
            assert members[0]["item_id"] == plant_id
            fields = {"target_ids": [plant_id], "event_type": "observation", "description": "Observation fictive",
                      "request_key": "client-observation-1", "occurrence": None}
            first = unpack(await session.call_tool("add_garden_intervention", fields))
            retry = unpack(await session.call_tool("add_garden_intervention", fields))
            assert first["event_id"] == retry["event_id"] and retry["replayed"]
            conflict = await session.call_tool("add_garden_intervention", {**fields, "description": "Changed"})
            assert conflict.isError
            results["event_id"] = first["event_id"]
            history = await session.call_tool("get_history", {})
            before = history.structuredContent["undo_depth"]
            await session.call_tool("edit_garden_intervention", {"event_id": first["event_id"],
                                                               "fields": {"description": "Edited fictional observation"}})
            history = await session.call_tool("get_history", {})
            assert history.structuredContent["undo_depth"] == before + 1
            await session.call_tool("undo", {})
            events = unpack_list(await session.call_tool("search_garden_interventions", {"target_id": plant_id}))
            assert events[0]["description"] == fields["description"]
            saved = await session.call_tool("save_plan", {"file_path": str(path)})
            assert not saved.isError, saved.content

    try:
        _run(server, body, qtbot)
    finally:
        server.stop()
        window._project_manager.mark_clean()
        window.close()
    reopened, scene = ProjectManager(), CanvasScene()
    reopened.load(scene, path)
    event = reopened.garden_memory["events"][results["event_id"]]
    assert event["period"]["precision"] == "unknown"
    assert event["description"] == "Observation fictive"
    assert results["read"]["card"]["period"]["precision"] == "unknown"
