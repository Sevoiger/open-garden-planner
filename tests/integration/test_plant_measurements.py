"""Real MCP transport and GUI share dated sizes, undo and durable receipts."""
import os
from pathlib import Path

from PyQt6.QtWidgets import QMessageBox

from open_garden_planner.agent_api import AgentApiServer
from open_garden_planner.app.application import GardenPlannerApp
from open_garden_planner.core.garden_memory import GardenMemoryService
from open_garden_planner.core.object_types import ObjectType
from open_garden_planner.core.project import ProjectManager
from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services.garden_queries import garden_record
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.canvas.items import CircleItem
from open_garden_planner.ui.dialogs.plant_measurements_dialog import PlantMeasurementsDialog
from tests.integration.test_agent_api_writes import TOKEN, _free_port, _run
from tests.integration.test_garden_memory_mcp import unpack


def make_window(qtbot, monkeypatch):
    monkeypatch.setattr(GardenPlannerApp, "_startup_sequence", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_start_update_check", lambda _self: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Discard)
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    plant = CircleItem(300, 250, 30, object_type=ObjectType.SHRUB, name="Arbuste de test",
                       metadata={"plant_instance": {"current_height_cm": 30, "planting_date": "2020-01-01"},
                                 "plant_species": PlantSpeciesData(scientific_name="Species fictiva", common_name="Arbuste de test").to_dict()})
    window.canvas_scene.addItem(plant)
    return window, plant


def test_mcp_measurement_auth_replay_undo_and_roundtrip(qtbot, monkeypatch, tmp_path):
    window, plant = make_window(qtbot, monkeypatch)
    plant_id = str(plant.item_id)
    server = AgentApiServer(window._build_agent_providers(), port=_free_port(), write_token=TOKEN, writes_enabled=True)
    server.start()
    measured = {"item_id": plant_id, "date": "2026-10-28", "height_cm": 120, "request_key": "size-1"}
    path = tmp_path / "measurements.ogp"

    async def body(context):
        http_client, ClientSession, url = context
        async with http_client(url) as (reader, writer, _), ClientSession(reader, writer) as session:
            await session.initialize()
            for name, fields in [
                ("add_plant_measurement", measured),
                ("edit_plant_measurement", {"item_id": plant_id, "measurement_id": "fake", "date": "2026-10-28", "height_cm": 3}),
                ("delete_plant_measurement", {"item_id": plant_id, "measurement_id": "fake"}),
            ]:
                assert (await session.call_tool(name, fields)).isError
        async with (http_client(url, headers={"Authorization": f"Bearer {TOKEN}"}) as (reader, writer, _),
                    ClientSession(reader, writer) as session):
            await session.initialize()
            first = unpack(await session.call_tool("add_plant_measurement", measured))
            identity = first["measurement_id"]
            assert first["current_size"]["height_cm"]["date"] == "2026-10-28"
            history = (await session.call_tool("get_history", {})).structuredContent["undo_depth"]
            assert unpack(await session.call_tool("add_plant_measurement", measured))["replayed"]
            assert (await session.call_tool("get_history", {})).structuredContent["undo_depth"] == history
            assert (await session.call_tool("add_plant_measurement", {**measured, "height_cm": 130})).isError
            await session.call_tool("undo", {})
            assert unpack(await session.call_tool("add_plant_measurement", measured))["replayed"]
            assert not unpack(await session.call_tool("get_garden_record", {"item_id": plant_id}))["measurements"]
            await session.call_tool("redo", {})
            await session.call_tool("add_plant_measurement", {"item_id": plant_id, "date": "2025-01-01", "spread_cm": 60,
                                                                "height_cm": 80, "request_key": "size-2"})
            current = unpack(await session.call_tool("get_garden_record", {"item_id": plant_id}))
            assert current["current_size"]["height_cm"]["value"] == 120
            assert current["current_size"]["spread_cm"]["date"] == "2025-01-01"
            edited = unpack(await session.call_tool("edit_plant_measurement", {"item_id": plant_id,
                "measurement_id": identity, "date": "2026-10-28", "height_cm": 115, "note": "Correction"}))
            assert edited["current_size"]["height_cm"]["value"] == 115
            await session.call_tool("delete_plant_measurement", {"item_id": plant_id, "measurement_id": identity})
            assert unpack(await session.call_tool("add_plant_measurement", measured))["replayed"]
            await session.call_tool("undo", {})
            saved = await session.call_tool("save_plan", {"file_path": str(path)})
            assert not saved.isError

    try:
        _run(server, body, qtbot)
    finally:
        server.stop()
        window._project_manager.mark_clean()
        window.close()
    reopened, scene = ProjectManager(), CanvasScene()
    reopened.load(scene, path)
    restored = scene.find_item_by_id(plant.item_id)
    assert restored.radius == 30
    result = garden_record(reopened.snapshot_dict(scene), plant_id)
    assert len(result["measurements"]) == 2
    assert result["current_size"]["height_cm"]["value"] == 115
    assert result["undated_size"]["current_height_cm"] == 30
    service = GardenMemoryService(reopened, window.canvas_view.command_manager, scene)
    assert service.plant_measurement("add_plant_measurement", plant_id,
        {"date": "2026-10-28", "height_cm": 120, "spread_cm": None, "note": ""}, "size-1")["replayed"]


def test_measurement_dialog_native_journey(qtbot, monkeypatch, qapp):
    from open_garden_planner.core.i18n import load_translator

    if os.environ.get("OGP_MEASUREMENT_CAPTURE"):
        assert load_translator(qapp, "fr")
    window, plant = make_window(qtbot, monkeypatch)
    panel = window.plant_database_panel
    panel.set_selected_items([plant])
    assert panel.current_height_spin.isReadOnly()
    dialog = PlantMeasurementsDialog(panel.measurement_service, plant, window)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    dialog.day.setText("2026-10-28")
    dialog.height.setText("120")
    dialog.spread.setText("80")
    dialog.note.setText("Relevé après plantation")
    dialog._save()
    panel.set_selected_items([plant])
    assert dialog.history.count() == 1
    assert panel.current_height_spin.value() == 120
    assert "2026-10-28" in panel.current_height_spin.toolTip()
    dialog.history.setCurrentRow(0)
    dialog.height.setText("115")
    dialog._save()
    assert plant.metadata["plant_instance"]["current_height_cm"] == 115
    window.canvas_view.command_manager.undo()
    panel.set_selected_items([plant])
    assert panel.current_height_spin.value() == 120
    dialog._refresh()
    capture = os.environ.get("OGP_MEASUREMENT_CAPTURE")
    if capture:
        qtbot.wait(100)
        assert dialog.grab().save(str(Path(capture)))
    dialog.history.setCurrentRow(0)
    dialog._delete()
    assert plant.metadata["plant_instance"]["current_height_cm"] == 30
    assert not plant.metadata["plant_instance"]["size_measurements"]
    dialog.close()
    # No botanical identity is needed to record an observation.
    plant.metadata.pop("plant_species")
    panel.set_selected_items([plant])
    assert not panel.measurements_button.isHidden()
    window._project_manager.mark_clean()
    window.close()
    if os.environ.get("OGP_MEASUREMENT_CAPTURE"):
        load_translator(qapp, "en")


def test_note_correction_preserves_exact_dimensions_and_early_date(qtbot, monkeypatch):
    window, plant = make_window(qtbot, monkeypatch)
    service = window.plant_database_panel.measurement_service
    original = {"date": "1600-01-01", "height_cm": 0.005, "spread_cm": 1234567.89, "note": "Original"}
    service.plant_measurement("add_plant_measurement", str(plant.item_id), original, "precise-1")
    dialog = PlantMeasurementsDialog(service, plant, window)
    qtbot.addWidget(dialog)
    dialog.history.setCurrentRow(0)
    dialog.note.setText("Note corrected")
    dialog._save()
    record = plant.metadata["plant_instance"]["size_measurements"][0]
    assert record == {**original, "note": "Note corrected", "id": record["id"]}
    dialog.close()
    window._project_manager.mark_clean()
    window.close()


def test_unidentified_plant_views_use_observations_at_requested_date(qtbot):
    from datetime import date

    from open_garden_planner.core.object_height import effective_height_cm
    from open_garden_planner.services.plant_measurements import update_measurements
    from open_garden_planner.ui.canvas.sun_shadow_controller import _plant_canopy_radius_cm

    metadata, _ = update_measurements({}, {"date": "2025-01-01", "height_cm": 80, "spread_cm": 60})
    metadata, _ = update_measurements(metadata, {"date": "2026-01-01", "height_cm": 120, "spread_cm": 100})
    plant = CircleItem(0, 0, 10, object_type=ObjectType.SHRUB, metadata=metadata)
    assert effective_height_cm("SHRUB", metadata, date(2025, 1, 1)) == 80
    assert _plant_canopy_radius_cm(plant, date(2025, 1, 1)) == 30
    assert _plant_canopy_radius_cm(plant, date(2026, 1, 1)) == 50
