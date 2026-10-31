"""Soil points through the real canvas, command history, persistence and MCP."""
from copy import deepcopy

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtWidgets import QDialog, QMessageBox

from open_garden_planner.app.application import GardenPlannerApp
from open_garden_planner.core.plan_io import read_plan
from open_garden_planner.ui.dialogs.soil_sample_dialog import SoilSampleDialog


def test_canvas_point_and_mcp_round_trip(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(GardenPlannerApp, "_startup_sequence", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_start_update_check", lambda _self: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(SoilSampleDialog, "exec", lambda dialog: dialog.ph.setText("7") or QDialog.DialogCode.Accepted)
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    window.canvas_view.fit_in_view()
    controller = window._soil_points
    controller.start(False)
    click = window.canvas_view.mapFromScene(QPointF(1500, 1200))
    qtbot.mouseClick(window.canvas_view.viewport(), Qt.MouseButton.LeftButton, pos=click)
    records = window._project_manager.soil_tests
    assert len(records) == 1
    target, history = next(iter(records.items()))
    record = history["records"][0]
    assert record["ph"] == 7 and record["date"]
    assert abs(record["sample_position"]["x_cm"] - 1500) < 20
    assert abs(record["sample_position"]["y_cm"] - 1200) < 20
    assert len(controller._markers) == 1
    path = tmp_path / "sample.ogp"
    window._project_manager.save(window.canvas_scene, path)
    assert read_plan(path)["soil_tests"][target] == history
    window._agent_undo()
    assert not window._project_manager.soil_tests and not controller._markers
    window._agent_redo()
    assert len(controller._markers) == 1
    window._project_manager.load(window.canvas_scene, path)
    assert len(controller._markers) == 1
    opened = []
    monkeypatch.setattr(SoilSampleDialog, "exec", lambda _self: opened.append(True) or QDialog.DialogCode.Rejected)
    marker_pos = window.canvas_view.mapFromScene(QPointF(record["sample_position"]["x_cm"],
                                                        record["sample_position"]["y_cm"]))
    qtbot.mouseDClick(window.canvas_view.viewport(), Qt.MouseButton.LeftButton, pos=marker_pos)
    qtbot.waitUntil(lambda: bool(opened), timeout=1000)
    dispatch = window._build_agent_providers().document_operation
    before = deepcopy(window._project_manager.soil_tests)
    preview = dispatch("prepare", {"operations": [{"operation": "edit_soil_sample", "target_id": target,
        "fields": {"record_id": record["id"], "report": {"source_text": "Texte fictif", "readings": [
            {"parameter": "P₂O₅", "value": "< 0.01", "unit": "g/kg"}]}}}]})
    assert window._project_manager.soil_tests == before
    dispatch("apply", {"preparation_id": preview["preparation_id"], "request_key": "soil-sample"})
    assert window._project_manager.soil_tests[target]["records"][0]["laboratory_report"]["source_text"] == "Texte fictif"
    assert len(controller._markers) == 1
    window.close()


def test_open_form_conflict_and_cancel_do_not_overwrite(qtbot, monkeypatch):
    from open_garden_planner.services.soil_samples import prepare_sample

    monkeypatch.setattr(GardenPlannerApp, "_startup_sequence", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_start_update_check", lambda _self: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Discard)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_a, **_kw: warnings.append(True))
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    controller = window._soil_points
    monkeypatch.setattr(SoilSampleDialog, "exec", lambda _self: QDialog.DialogCode.Rejected)
    controller._place(100, 100)
    assert not window._project_manager.soil_tests
    document, result = prepare_sample(controller._document(), "record_soil_sample", None, {
        "test_date": "2025-01-01", "ph": 6, "position": {"x_cm": 100, "y_cm": 100}})
    target, record_id = result["target_id"], result["record_id"]
    window._project_manager.restore_soil_test_history(target, document["soil_tests"][target])

    def concurrent_change(_dialog):
        changed = deepcopy(window._project_manager.soil_tests[target])
        changed["records"][0]["ph"] = 8
        window._project_manager.restore_soil_test_history(target, changed)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SoilSampleDialog, "exec", concurrent_change)
    controller.edit(target, record_id)
    assert warnings and window._project_manager.soil_tests[target]["records"][0]["ph"] == 8
    window.close()


def test_autonomous_sample_prepare_replay_conflict_and_reopen(tmp_path):
    import pytest

    from open_garden_planner.core.document_session import DocumentSession
    from open_garden_planner.core.plan_io import write_plan

    path = tmp_path / "isolated.ogp"
    write_plan(path, {"version": "1.4", "canvas": {"width": 500, "height": 500}, "objects": []})
    fields = {"test_date": "2025-01-01", "position": {"x_cm": 100, "y_cm": 250},
              "report": {"source_text": "Rapport fictif entier", "readings": [
                  {"parameter": "Sodium", "value": "< 0.01", "unit": "g/kg"}]}}
    with DocumentSession(path) as host:
        operation = {"operation": "record_soil_sample", "fields": fields}
        preview = host.dispatch("prepare", {"operations": [operation]})
        assert not read_plan(path).get("soil_tests")
        args = {"preparation_id": preview["preparation_id"], "request_key": "sample-create"}
        host.dispatch("apply", args)
        assert host.dispatch("apply", args)["replayed"]
        target = preview["results"][0]["target_id"]
        assert read_plan(path)["soil_tests"][target]["records"][0]["laboratory_report"] == fields["report"]
        stale = host.dispatch("prepare", {"operations": [operation]})
        host.dispatch("undo", {})
        assert not read_plan(path).get("soil_tests")
        host.dispatch("redo", {})
        with pytest.raises(ValueError, match="changed after preparation"):
            host.dispatch("apply", {"preparation_id": stale["preparation_id"], "request_key": "stale"})
    with DocumentSession(path) as reopened:
        assert reopened.snapshot()["soil_tests"][target]["records"][0]["sample_position"]["y_cm"] == 250


def test_dialog_keeps_unknown_report_fields_and_exact_ph(qtbot):
    record = {"date": "2025-01-01", "ph": 6.123456789, "laboratory_report": {
        "accreditation": {"issuer": "synthetic"}, "source_text": "Source fictive",
        "readings": [{"parameter": "P", "value": "< 0.01", "unit": "g/kg", "uncertainty": "± 1"}]}}
    dialog = SoilSampleDialog(full=True, record=record)
    qtbot.addWidget(dialog)
    dialog.notes.setText("Nouvelle note")
    fields = dialog.fields()
    assert fields["ph"] == record["ph"]
    assert fields["report"]["accreditation"] == record["laboratory_report"]["accreditation"]
    assert fields["report"]["readings"][0]["uncertainty"] == "± 1"
    fresh = SoilSampleDialog(full=False)
    qtbot.addWidget(fresh)
    assert fresh.fields()["ph"] is None


def test_same_point_history_is_accessible_and_keyboard_places(qtbot, monkeypatch):
    from PyQt6.QtWidgets import QInputDialog

    from open_garden_planner.services.soil_samples import prepare_sample

    monkeypatch.setattr(GardenPlannerApp, "_startup_sequence", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_start_update_check", lambda _self: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Discard)
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    controller = window._soil_points
    doc, first = prepare_sample(controller._document(), "record_soil_sample", None, {
        "test_date": "2024-01-01", "ph": 6, "position": {"x_cm": 100, "y_cm": 200}})
    doc, second = prepare_sample(doc, "record_soil_sample", first["target_id"], {
        "test_date": "2025-01-01", "ph": 7, "position": {"x_cm": 100, "y_cm": 200}})
    window._project_manager.restore_soil_test_history(first["target_id"], doc["soil_tests"][first["target_id"]])
    assert len(controller._markers) == 1
    opened = []
    monkeypatch.setattr(QInputDialog, "getItem", lambda _parent, _title, _label, items, *_a: (items[0], True))
    monkeypatch.setattr(controller, "edit", lambda target, record_id: opened.append((target, record_id)))
    controller.choose(first["target_id"])
    assert opened == [(first["target_id"], first["record_id"])]
    assert first["record_id"] != second["record_id"]
    coords = []
    window.canvas_view.soil_point_requested.disconnect(controller._place)
    window.canvas_view.soil_point_requested.connect(lambda x, y: coords.append((x, y)))
    controller.start(False)
    assert window.canvas_view.commit_typed_coordinate(QPointF(300, 400))
    assert coords == [(300, 400)]
    window.close()


def test_failed_save_keeps_sample_dirty_and_can_retry(qtbot, monkeypatch, tmp_path):
    import pytest

    import open_garden_planner.core.project as project_module
    from open_garden_planner.services.soil_samples import prepare_sample

    monkeypatch.setattr(GardenPlannerApp, "_startup_sequence", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_start_update_check", lambda _self: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Discard)
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    doc, result = prepare_sample(window._soil_points._document(), "record_soil_sample", None, {
        "test_date": "2025-01-01", "ph": 6.5, "position": {"x_cm": 100, "y_cm": 100}})
    target = result["target_id"]
    pm = window._project_manager
    pm.restore_soil_test_history(target, doc["soil_tests"][target])
    path = tmp_path / "failed-then-retry.ogp"
    write_plan = project_module.write_plan
    monkeypatch.setattr(project_module, "write_plan", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("synthetic")))
    with pytest.raises(OSError, match="synthetic"):
        pm.save(window.canvas_scene, path)
    assert pm.is_dirty and not path.exists() and len(window._soil_points._markers) == 1
    monkeypatch.setattr(project_module, "write_plan", write_plan)
    pm.save(window.canvas_scene, path)
    assert read_plan(path)["soil_tests"][target]["records"][0]["ph"] == 6.5
    assert not pm.is_dirty
    window.close()


def test_history_selector_refuses_concurrent_change(qtbot, monkeypatch):
    from PyQt6.QtWidgets import QInputDialog

    from open_garden_planner.services.soil_samples import prepare_sample

    monkeypatch.setattr(GardenPlannerApp, "_startup_sequence", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_start_update_check", lambda _self: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Discard)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_a, **_kw: warnings.append(True))
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    controller = window._soil_points
    doc, first = prepare_sample(controller._document(), "record_soil_sample", None, {
        "test_date": "2024-01-01", "ph": 6, "position": {"x_cm": 100, "y_cm": 200}})
    doc, _ = prepare_sample(doc, "record_soil_sample", first["target_id"], {
        "test_date": "2025-01-01", "ph": 7, "position": {"x_cm": 100, "y_cm": 200}})
    target = first["target_id"]
    window._project_manager.restore_soil_test_history(target, doc["soil_tests"][target])

    def change_during_selection(_parent, _title, _label, items, *_args):
        changed = deepcopy(window._project_manager.soil_tests[target])
        changed["records"][0]["ph"] = 8
        window._project_manager.restore_soil_test_history(target, changed)
        return items[0], True

    monkeypatch.setattr(QInputDialog, "getItem", change_during_selection)
    deleted = []
    monkeypatch.setattr(controller, "delete", lambda *_args: deleted.append(True))
    controller.choose(target, "delete")
    assert warnings and not deleted
    assert window._project_manager.soil_tests[target]["records"][0]["ph"] == 8
    window.close()


def test_soil_sample_over_authenticated_mcp_http(tmp_path):
    import asyncio
    import json
    import socket
    from concurrent.futures import ThreadPoolExecutor

    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    from open_garden_planner.agent_api.document_tools import DomainProviders
    from open_garden_planner.agent_api.server import AgentApiServer
    from open_garden_planner.core.document_session import DocumentSession
    from open_garden_planner.core.plan_io import write_plan
    from open_garden_planner.services.library_port import LibraryPort

    path = tmp_path / "http-soil.ogp"
    write_plan(path, {"version": "1.4", "canvas": {"width": 500, "height": 500}, "objects": []})
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with DocumentSession(path) as host:
        library = LibraryPort(tmp_path / "synthetic-library.json")
        server = AgentApiServer(DomainProviders(host.dispatch, library.dispatch), port=port,
                                writes_enabled=True, write_token="synthetic-soil-token")
        server.start()

        async def run():
            url = f"http://127.0.0.1:{port}/mcp"
            async with streamablehttp_client(url) as (reader, writer, _), ClientSession(reader, writer) as client:
                await client.initialize()
                denied = await client.call_tool("prepare_document_operations", {"operations": [{
                    "operation": "record_soil_sample", "fields": {"test_date": "2025-01-01",
                    "position": {"x_cm": 200, "y_cm": 300}, "ph": 6.4}}]})
                assert not denied.isError
                preparation_id = json.loads(denied.content[0].text)["preparation_id"]
                refused = await client.call_tool("apply_document_operations", {
                    "preparation_id": preparation_id, "request_key": "unauthorized-soil"})
                assert refused.isError and not read_plan(path).get("soil_tests")
            async with (streamablehttp_client(url, headers={"Authorization": "Bearer synthetic-soil-token"})
                        as (reader, writer, _), ClientSession(reader, writer) as client):
                await client.initialize()
                preview = await client.call_tool("prepare_document_operations", {"operations": [{
                    "operation": "record_soil_sample", "fields": {"test_date": "2025-01-01",
                    "positions": [{"x_cm": 200, "y_cm": 300}, {"x_cm": 250, "y_cm": 320}], "report": {
                        "source_text": "Rapport fictif", "readings": [
                            {"parameter": "P₂O₅", "value": "< 0.01", "unit": "g/kg"}]}}}]})
                assert not preview.isError, preview.content
                prepared = json.loads(preview.content[0].text)
                args = {"preparation_id": prepared["preparation_id"], "request_key": "soil-http"}
                applied = await client.call_tool("apply_document_operations", args)
                assert not applied.isError, applied.content
                retry = await client.call_tool("apply_document_operations", args)
                assert json.loads(retry.content[0].text)["replayed"]
                target = prepared["results"][0]["target_id"]
                record = read_plan(path)["soil_tests"][target]["records"][0]
                assert record["laboratory_report"]["readings"][0]["value"] == "< 0.01"
                assert len(record["sample_positions"]) == 2

        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(lambda: asyncio.run(run())).result(timeout=30)
        finally:
            server.stop()


def test_multi_point_analysis_placement_and_visibility(qtbot, monkeypatch):
    from PyQt6.QtGui import QKeyEvent

    from open_garden_planner.core.tools.base_tool import ToolType

    monkeypatch.setattr(GardenPlannerApp, "_startup_sequence", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_start_update_check", lambda _self: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Discard)

    def accept_report(dialog):
        dialog.ph.setText("6.5")
        dialog.ph_enabled.setChecked(True)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SoilSampleDialog, "exec", accept_report)
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    controller = window._soil_points
    assert not window.soil_points_visible_action.isChecked()
    controller.start(True)
    assert window.soil_points_visible_action.isChecked()
    tool = window.canvas_view._tool_manager.active_tool
    tool.commit_typed_coordinate(QPointF(100, 150))
    tool.commit_typed_coordinate(QPointF(200, 250))
    assert len(controller._pending_positions) == 2
    assert not window._project_manager.soil_tests
    tool.key_press(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
    record = next(iter(window._project_manager.soil_tests.values()))["records"][0]
    assert record["sample_positions"] == [{"x_cm": 100.0, "y_cm": 150.0}, {"x_cm": 200.0, "y_cm": 250.0}]
    assert len(controller._markers) == 2 and all(m.isVisible() for m in controller._markers)
    window.soil_points_visible_action.setChecked(False)
    assert all(not m.isVisible() for m in controller._markers)
    window._agent_undo()
    assert not window._project_manager.soil_tests
    window._agent_redo()
    assert len(controller._markers) == 2 and all(not m.isVisible() for m in controller._markers)
    controller.start(True)
    window.canvas_view.commit_typed_coordinate(QPointF(300, 300))
    window.canvas_view._tool_manager.active_tool.key_press(
        QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert not controller._pending_positions and window.canvas_view._tool_manager.active_tool.tool_type == ToolType.SELECT
    window.close()


def test_common_lab_parameters_remain_available_after_reopening(qtbot):
    record = {"date": "2025-01-01", "sample_position": {"x_cm": 10, "y_cm": 20},
              "laboratory_report": {"readings": [{"parameter": "Total nitrogen", "value": "0.2", "unit": "%"}]}}
    dialog = SoilSampleDialog(full=True, record=record)
    qtbot.addWidget(dialog)
    names = [dialog.table.item(row, 0).text() for row in range(dialog.table.rowCount())]
    assert names.count("Total nitrogen") == 1
    assert all(name in names for name in ("Phosphorus", "Potassium", "Sand", "Silt", "Clay"))
    values = dialog.fields()
    assert len(values["report"]["readings"]) == 1
    assert values["report"]["readings"][0]["value"] == "0.2"


def test_two_analysis_locations_via_viewport_clicks(qtbot, monkeypatch):
    monkeypatch.setattr(GardenPlannerApp, "_startup_sequence", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_start_update_check", lambda _self: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(SoilSampleDialog, "exec", lambda dialog: dialog.ph.setText("6.2")
                        or dialog.ph_enabled.setChecked(True) or QDialog.DialogCode.Accepted)
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    window.canvas_view.fit_in_view()
    window._soil_points.start(True)
    for point in (QPointF(1500, 1200), QPointF(2500, 1500)):
        qtbot.mouseClick(window.canvas_view.viewport(), Qt.MouseButton.LeftButton,
                         pos=window.canvas_view.mapFromScene(point))
    qtbot.keyClick(window.canvas_view, Qt.Key.Key_Return)
    record = next(iter(window._project_manager.soil_tests.values()))["records"][0]
    assert len(record["sample_positions"]) == 2
    assert abs(record["sample_positions"][0]["x_cm"] - 1500) < 20
    assert abs(record["sample_positions"][1]["y_cm"] - 1500) < 20
    window.close()


def test_standard_parameter_identity_cannot_be_renamed(qtbot):
    record = {"sample_position": {"x_cm": 10, "y_cm": 20}, "laboratory_report": {"readings": [
        {"parameter": "Azote total", "parameter_code": "nitrogen_total", "value": "0.1"}]}}
    dialog = SoilSampleDialog(full=True, record=record)
    qtbot.addWidget(dialog)
    assert not dialog.table.item(0, 0).flags() & Qt.ItemFlag.ItemIsEditable
    fields = dialog.fields()
    assert fields["report"]["readings"][0]["parameter_code"] == "nitrogen_total"
    labels = [dialog.table.item(i, 0).text() for i in range(dialog.table.rowCount())]
    assert "Total nitrogen" not in labels


def test_opaque_legacy_extensions_remain_readable_and_unchanged(qtbot, monkeypatch):
    from open_garden_planner.services.soil_samples import prepare_sample

    monkeypatch.setattr(GardenPlannerApp, "_startup_sequence", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_start_update_check", lambda _self: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Discard)
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    controller = window._soil_points
    doc, result = prepare_sample(controller._document(), "record_soil_sample", None, {
        "test_date": "2025-01-01", "position": {"x_cm": 100, "y_cm": 100},
        "report": {"readings": [{"parameter": "Custom", "value": "3"}]}})
    target = result["target_id"]
    history = doc["soil_tests"][target]
    record = history["records"][0]
    record["sample_positions"] = {"legacy": True}
    record["laboratory_report"]["readings"][0]["parameter_code"] = ["legacy", 1]
    window._project_manager.restore_soil_test_history(target, history)
    assert len(controller._markers) == 1
    dialog = SoilSampleDialog(full=True, record=record)
    qtbot.addWidget(dialog)
    fields = dialog.fields()
    assert fields["report"]["readings"][0]["parameter_code"] == ["legacy", 1]
    assert "positions" not in fields
    fields["record_id"] = record["id"]
    updated, _ = prepare_sample(controller._document(), "edit_soil_sample", target, fields)
    result_record = updated["soil_tests"][target]["records"][0]
    assert result_record["sample_positions"] == {"legacy": True}
    assert result_record["laboratory_report"]["readings"][0]["parameter_code"] == ["legacy", 1]
    window.close()
