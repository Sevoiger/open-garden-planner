"""Real live providers and the autonomous HTTP transport on synthetic data."""

import asyncio
import socket
from concurrent.futures import ThreadPoolExecutor

import pytest

from open_garden_planner.agent_api.document_tools import DomainProviders
from open_garden_planner.agent_api.server import AgentApiServer
from open_garden_planner.core.document_session import DocumentSession
from open_garden_planner.core.plan_io import read_plan, write_plan


def test_live_preview_apply_undo_and_gui_conflict(qtbot, monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QMessageBox

    from open_garden_planner.app.application import GardenPlannerApp
    from open_garden_planner.models.task import ManualTask

    monkeypatch.setattr(GardenPlannerApp, "_startup_sequence", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_start_update_check", lambda _self: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Discard)
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    dispatch = window._build_agent_providers().document_operation
    operation = {"operation": "add_manual_task", "fields": {"title": "Synthetic task"}}
    initial = window.canvas_view.command_manager.undo_depth
    preview = dispatch("prepare", {"operations": [operation]})
    assert not window._project_manager.manual_tasks
    assert not window._project_manager.is_dirty
    applied = dispatch("apply", {"preparation_id": preview["preparation_id"], "request_key": "live-1"})
    assert not applied["persisted"]
    assert window.canvas_view.command_manager.undo_depth == initial + 1
    window._agent_undo()
    assert not window._project_manager.manual_tasks
    window._agent_redo()
    task_id = next(iter(window._project_manager.manual_tasks))
    stale = dispatch("prepare", {"operations": [operation]})
    # The actual UI handler uses the same pure preparation and command.
    window.tasks_view._commit_manual(ManualTask(id=task_id, title="Changed in UI"), edit=True)
    with pytest.raises(ValueError, match="changed after preparation"):
        dispatch("apply", {"preparation_id": stale["preparation_id"], "request_key": "stale"})
    path = tmp_path / "live.ogp"
    window._project_manager.save(window.canvas_scene, path)
    assert read_plan(path)["manual_tasks"][task_id]["title"] == "Changed in UI"
    with pytest.raises(ValueError, match="already open"):
        DocumentSession(path)
    window._project_manager.release_document_ownership()
    with DocumentSession(path) as independent:
        assert independent.dispatch("result", {"request_key": "live-1"})["status"] == "applied"
    window.close()


def test_autonomous_http_auth_batch_retry_and_reopen(tmp_path):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client as streamable_http_client

    from open_garden_planner.services.library_port import LibraryPort

    path = tmp_path / "autonomous.ogp"
    write_plan(path, {"version": "1.4", "canvas": {"width": 500, "height": 500}, "objects": []})
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with DocumentSession(path) as host:
        library = LibraryPort(tmp_path / "library.json")
        server = AgentApiServer(DomainProviders(host.dispatch, library.dispatch), port=port,
                                writes_enabled=True, write_token="synthetic-domain-token")
        server.start()

        async def run():
            url = f"http://127.0.0.1:{port}/mcp"
            async with streamable_http_client(url) as (reader, writer, _), ClientSession(reader, writer) as client:
                await client.initialize()
                tools = (await client.list_tools()).tools
                assert all(tool.inputSchema.get("additionalProperties") is False for tool in tools)
                capabilities = await client.call_tool("get_capabilities", {})
                assert "render_canvas" not in str(capabilities)
                denied = await client.call_tool("apply_document_operations", {
                    "preparation_id": "untrusted", "request_key": "denied"})
                assert denied.isError
                assert (await client.call_tool("get_custom_plant_library", {})).isError
                assert not (tmp_path / "library.json").exists()
            async with (streamable_http_client(url, headers={"Authorization": "Bearer synthetic-domain-token"}) as (reader, writer, _),
                        ClientSession(reader, writer) as client):
                await client.initialize()
                preview = await client.call_tool("prepare_document_operations", {
                    "operations": [{"operation": "add_manual_task", "fields": {"title": f"Task {i}"}} for i in range(30)]})
                import json
                prepared = json.loads(preview.content[0].text)
                arguments = {"preparation_id": prepared["preparation_id"], "request_key": "http-30"}
                applied = await client.call_tool("apply_document_operations", arguments)
                assert not applied.isError
                assert len(read_plan(path)["manual_tasks"]) == 30
                retry = await client.call_tool("apply_document_operations", arguments)
                assert json.loads(retry.content[0].text)["replayed"]
                assert host.history.undo_depth == 1
                assert not (await client.call_tool("undo", {})).isError
                assert not read_plan(path).get("manual_tasks")
                assert not (await client.call_tool("redo", {})).isError
                library_preview = await client.call_tool("prepare_plant_library_import", {"records": [
                    {"source_id": f"synthetic:{i}", "scientific_name": f"Fictitia species{i}",
                     "common_name": "Homonym", "data_source": "custom"} for i in range(30)]})
                library_prepared = json.loads(library_preview.content[0].text)
                assert library_prepared["can_apply"]
                assert not (tmp_path / "library.json").exists()
                library_arguments = {"preparation_id": library_prepared["token"], "request_key": "library-30"}
                imported = await client.call_tool("apply_plant_library_import", library_arguments)
                assert not imported.isError, imported.content
                receipt = json.loads(imported.content[0].text)
                assert receipt["created"] == 30 and receipt["persisted"]
                repeated = await client.call_tool("apply_plant_library_import", library_arguments)
                assert json.loads(repeated.content[0].text)["replayed"]
                undone = await client.call_tool("undo_plant_library_import", {"transaction_id": receipt["transaction_id"]})
                assert not undone.isError, undone.content
                assert not library.dispatch("snapshot", {})["plants"]
                assert not (await client.call_tool("apply_plant_library_import", library_arguments)).isError
                assert not library.dispatch("snapshot", {})["plants"]

        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(lambda: asyncio.run(run())).result(timeout=30)
        finally:
            server.stop()
    with DocumentSession(path) as host:
        assert len(host.snapshot()["manual_tasks"]) == 30


def test_live_multidomain_batch_publishes_together_and_undoes(qtbot, monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QMessageBox

    from open_garden_planner.app.application import GardenPlannerApp
    from open_garden_planner.core.object_types import ObjectType
    from open_garden_planner.ui.canvas.items import CircleItem, RectangleItem

    monkeypatch.setattr(GardenPlannerApp, "_startup_sequence", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_start_update_check", lambda _self: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Discard)
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    plant = CircleItem(100, 100, 20, object_type=ObjectType.PERENNIAL)
    bed = RectangleItem(10, 10, 400, 400, object_type=ObjectType.GARDEN_BED)
    plant.name, bed.name = "Synthetic plant", "Synthetic bed"
    window.canvas_scene.addItem(bed)
    window.canvas_scene.addItem(plant)
    plant_id, bed_id = str(plant.item_id), str(bed.item_id)
    task_id = window._agent_add_manual_task("Synthetic task")["task_id"]
    before = window.canvas_view.command_manager.undo_depth
    operations = [
        {"operation": "set_manual_task_status", "target_id": task_id, "fields": {"status": "done"}},
        {"operation": "set_planting_date", "target_id": plant_id, "fields": {"planting_date": "2024"}},
        {"operation": "add_harvest_record", "target_id": plant_id,
         "fields": {"date": "", "quantity": 0, "unit": "kg"}},
        {"operation": "add_pest_log", "target_id": plant_id,
         "fields": {"date": "", "entry_type": "disease", "name": "Synthetic issue", "severity": "low"}},
        {"operation": "add_journal_note", "fields": {"date": "", "text": "Synthetic note"}},
        {"operation": "record_soil_test", "target_id": bed_id,
         "fields": {"ph": 6.5, "test_date": "2026-01-01"}},
        {"operation": "set_succession_plan", "target_id": bed_id, "fields": {
            "year": 2026, "entries": [{"species_key": "fictitia species", "start_date": "2026-03-01",
                                        "end_date": "2026-04-01"}]}},
    ]
    dispatch = window._build_agent_providers().document_operation
    prepared = dispatch("prepare", {"operations": operations})
    assert window.canvas_view.command_manager.undo_depth == before
    dispatch("apply", {"preparation_id": prepared["preparation_id"], "request_key": "multi"})
    pm = window._project_manager
    assert window.canvas_view.command_manager.undo_depth == before + 1
    assert pm.task_states[task_id]["status"] == "done"
    assert task_id in pm._task_completions
    assert pm.harvest_logs[plant_id]["records"][0]["quantity"] == 0
    assert pm.pest_logs[plant_id]["records"][0]["date"] == ""
    assert len(pm.garden_journal_notes) == 2  # Explicit note + the harvest's pinless note.
    assert bed_id in pm.soil_tests and bed_id in pm.succession_plans
    window._agent_undo()
    assert not pm.task_states and task_id not in pm._task_completions
    assert not pm.harvest_logs and not pm.pest_logs and not pm.garden_journal_notes
    assert not pm.soil_tests and not pm.succession_plans
    window._agent_redo()
    path = tmp_path / "multidomain.ogp"
    pm.save(window.canvas_scene, path)
    pm.release_document_ownership()
    with DocumentSession(path) as reopened:
        assert reopened.snapshot()["harvest_logs"][plant_id]["records"][0]["quantity"] == 0
        assert reopened.dispatch("result", {"request_key": "multi"})["effect"] == "present"
    window.close()
