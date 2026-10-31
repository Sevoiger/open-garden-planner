"""Plant detail dates share the document's undo and MCP conflict boundary."""

import pytest
from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import QMessageBox

from open_garden_planner.app.application import GardenPlannerApp
from open_garden_planner.core.object_types import ObjectType
from open_garden_planner.ui.canvas.items import CircleItem


@pytest.fixture
def plant_window(qtbot, monkeypatch):
    monkeypatch.setattr(GardenPlannerApp, "_startup_sequence", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_start_update_check", lambda _self: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Discard)
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    plant = CircleItem(100, 100, 30, object_type=ObjectType.TREE)
    plant.metadata.update({
        "plant_species": {"scientific_name": "Malus domestica", "common_name": "Apple"},
        "plant_instance": {"planting_date": "2020-01-01", "notes": "Conserver"},
    })
    window.canvas_scene.addItem(plant)
    plant.setSelected(True)
    yield window, plant
    window.close()


def test_date_edit_is_undoable_and_invalidates_prepared_changes(plant_window, qtbot, tmp_path):
    window, plant = plant_window
    panel = window.plant_database_panel
    path = tmp_path / "garden.ogp"
    window._project_manager.save(window.canvas_scene, path)
    commands = window.canvas_view.command_manager
    before = commands.undo_depth
    dispatch = window._build_agent_providers().document_operation
    preview = dispatch("prepare", {"operations": [{
        "operation": "set_planting_date", "target_id": str(plant.item_id),
        "fields": {"planting_date": "2022-02-02"},
    }]})

    panel.planting_date_edit.setDate(QDate(2021, 3, 4))
    assert plant.metadata["plant_instance"]["planting_date"] == "2021-03-04"
    assert commands.undo_depth == before + 1
    assert window._project_manager.is_dirty

    with pytest.raises(ValueError, match="changed after preparation"):
        dispatch("apply", {"preparation_id": preview["preparation_id"], "request_key": "date-conflict"})
    # A pending note becomes its own undo step before the date is undone.
    panel.notes_edit.setPlainText("Note ajoutée après la date")
    commands.undo()
    assert plant.metadata["plant_instance"]["notes"] == "Conserver"
    commands.undo()
    qtbot.waitUntil(lambda: panel.planting_date_edit.date() == QDate(2020, 1, 1))
    assert plant.metadata["plant_instance"]["notes"] == "Conserver"
    commands.redo()
    qtbot.waitUntil(lambda: panel.planting_date_edit.date() == QDate(2021, 3, 4))
    commands.redo()
    window._project_manager.save(window.canvas_scene, path)
    from open_garden_planner.core.project import ProjectManager
    from open_garden_planner.ui.canvas.canvas_scene import CanvasScene

    reopened = CanvasScene()
    manager = ProjectManager()
    window._project_manager.release_document_ownership()
    manager.load(reopened, path)
    found = next(item for item in reopened.items() if getattr(item, "item_id", None) == plant.item_id)
    assert found.metadata["plant_instance"]["planting_date"] == "2021-03-04"
    assert found.metadata["plant_instance"]["notes"] == "Note ajoutée après la date"
    manager.release_document_ownership()


def test_clear_date_and_partial_period_are_visible_and_undoable(plant_window, qtbot):
    window, plant = plant_window
    panel = window.plant_database_panel
    commands = window.canvas_view.command_manager
    assert panel.clear_planting_date_button.isEnabled()
    assert panel.clear_planting_date_button.accessibleName()
    qtbot.keyClick(panel.clear_planting_date_button, Qt.Key.Key_Space)
    assert "planting_date" not in plant.metadata["plant_instance"]
    assert plant.metadata["garden_tracking"]["period"]["precision"] == "unknown"
    assert not panel.clear_planting_date_button.isEnabled()
    commands.undo()
    qtbot.waitUntil(lambda: panel.planting_date_edit.date() == QDate(2020, 1, 1))
    commands.redo()
    qtbot.waitUntil(lambda: panel.planting_date_edit.date() == panel.planting_date_edit.minimumDate())
    panel.measurement_service.set_planting_date(str(plant.item_id), "Automne 2023")
    qtbot.waitUntil(lambda: not panel.planting_date_edit.isEnabled())
    assert not panel.clear_planting_date_button.isEnabled()
    assert plant.metadata["garden_tracking"]["period"]["precision"] == "season"


def test_date_validation_error_restores_visible_value(plant_window, monkeypatch):
    window, plant = plant_window
    panel = window.plant_database_panel
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, title, message: messages.append((title, message)))

    def reject(*_args):
        raise ValueError("synthetic failure")

    monkeypatch.setattr(panel.measurement_service, "set_planting_date", reject)
    panel.planting_date_edit.setDate(QDate(2021, 3, 4))
    assert panel.planting_date_edit.date() == QDate(2020, 1, 1)
    assert plant.metadata["plant_instance"]["planting_date"] == "2020-01-01"
    assert "synthetic failure" in messages[0][1]


def test_instance_edits_are_undoable_and_save_flushes_typing(plant_window, qtbot, tmp_path):
    window, plant = plant_window
    panel = window.plant_database_panel
    commands = window.canvas_view.command_manager
    before = commands.undo_depth
    panel.notes_edit.setPlainText("Une")
    panel.notes_edit.setPlainText("Une note")
    assert commands.undo_depth == before + 1
    assert plant.metadata["plant_instance"]["notes"] == "Une note"
    path = tmp_path / "notes.ogp"
    window._project_manager.save(window.canvas_scene, path)
    commands.undo()
    qtbot.waitUntil(lambda: panel.notes_edit.toPlainText() == "Conserver")
    assert plant.metadata["plant_instance"]["notes"] == "Conserver"
    commands.redo()
    qtbot.waitUntil(lambda: panel.notes_edit.toPlainText() == "Une note")
    panel.variety_edit.setText("Variété fictive")
    panel.variety_edit.editingFinished.emit()
    assert plant.metadata["plant_instance"]["variety_cultivar"] == "Variété fictive"
    commands.undo()
    qtbot.waitUntil(lambda: panel.variety_edit.text() == "")
    assert "variety_cultivar" not in plant.metadata["plant_instance"]
    commands.redo()
    qtbot.waitUntil(lambda: panel.variety_edit.text() == "Variété fictive")
    panel._on_add_custom_field()
    row = panel.custom_fields_layout.itemAt(0).widget()
    row.key_edit.setText("Origine")
    row.value_edit.setText("Pépinière")
    row.value_edit.editingFinished.emit()
    assert plant.metadata["plant_instance"]["custom_fields"] == {"Origine": "Pépinière"}
    commands.undo()
    assert "custom_fields" not in plant.metadata["plant_instance"]
    commands.redo()
    assert plant.metadata["plant_instance"]["custom_fields"] == {"Origine": "Pépinière"}


def test_pending_note_is_recorded_before_selection_changes(plant_window, qtbot):
    window, plant = plant_window
    panel = window.plant_database_panel
    commands = window.canvas_view.command_manager
    panel.notes_edit.setPlainText("Note de la première plante")
    other = CircleItem(300, 100, 30, object_type=ObjectType.TREE)
    other.metadata["plant_species"] = {"scientific_name": "Pyrus communis", "common_name": "Poirier"}
    window.canvas_scene.addItem(other)
    plant.setSelected(False)
    other.setSelected(True)
    commands.undo()
    assert plant.metadata["plant_instance"]["notes"] == "Conserver"
    assert not other.metadata.get("plant_instance", {}).get("notes")
    plant.setSelected(True)
    other.setSelected(False)
    qtbot.waitUntil(lambda: panel.notes_edit.toPlainText() == "Conserver")


def test_unfinished_variety_and_custom_edits_survive_selection_and_save(plant_window, tmp_path):
    window, plant = plant_window
    panel = window.plant_database_panel
    panel.variety_edit.setText("Variété saisie")
    panel._on_add_custom_field()
    row = panel.custom_fields_layout.itemAt(0).widget()
    row.key_edit.setText("Référence")
    row.value_edit.setText("Lot 42")
    other = CircleItem(300, 100, 30, object_type=ObjectType.TREE)
    other.metadata["plant_species"] = {"scientific_name": "Pyrus communis", "common_name": "Poirier"}
    window.canvas_scene.addItem(other)
    plant.setSelected(False)
    other.setSelected(True)
    assert plant.metadata["plant_instance"]["variety_cultivar"] == "Variété saisie"
    assert plant.metadata["plant_instance"]["custom_fields"] == {"Référence": "Lot 42"}
    window._project_manager.save(window.canvas_scene, tmp_path / "garden.ogp")
    window.canvas_view.command_manager.undo()
    assert "custom_fields" not in plant.metadata["plant_instance"]
    window.canvas_view.command_manager.undo()
    assert "variety_cultivar" not in plant.metadata["plant_instance"]


def test_active_variety_survives_card_command_and_close_guard(plant_window, monkeypatch):
    window, plant = plant_window
    panel = window.plant_database_panel
    panel.variety_edit.setText("Variété saisie")
    assert window._project_manager.is_dirty
    panel.measurement_service.set_card(str(plant.item_id), {"name": "Pommier", "state": "in_place"})
    assert plant.metadata["plant_instance"]["variety_cultivar"] == "Variété saisie"
    window.canvas_view.command_manager.undo()
    assert plant.metadata["plant_instance"]["variety_cultivar"] == "Variété saisie"
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Cancel)
    assert not window.close()
    assert plant.metadata["plant_instance"]["variety_cultivar"] == "Variété saisie"
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Discard)


def test_coalesced_edit_invalidates_mcp_preparation_even_after_return_to_original(plant_window):
    window, plant = plant_window
    panel = window.plant_database_panel
    panel.notes_edit.setPlainText("A")
    dispatch = window._build_agent_providers().document_operation
    preview = dispatch("prepare", {"operations": [{
        "operation": "set_planting_date", "target_id": str(plant.item_id),
        "fields": {"planting_date": "2022-02-02"},
    }]})
    panel.notes_edit.setPlainText("B")
    panel.notes_edit.setPlainText("A")
    with pytest.raises(ValueError, match="changed after preparation"):
        dispatch("apply", {"preparation_id": preview["preparation_id"], "request_key": "aba-note"})
    assert plant.metadata["plant_instance"]["notes"] == "A"


def test_undo_refreshes_focused_variety(plant_window, qtbot):
    window, plant = plant_window
    panel = window.plant_database_panel
    window.show()
    qtbot.waitExposed(window)
    panel.variety_edit.setFocus()
    panel.variety_edit.setText("Nouvelle variété")
    window.canvas_view.command_manager.undo()
    qtbot.waitUntil(lambda: panel.variety_edit.text() == "")
    assert "variety_cultivar" not in plant.metadata["plant_instance"]


def test_edit_returning_to_original_does_not_leave_an_undo_step(plant_window):
    window, plant = plant_window
    panel = window.plant_database_panel
    commands = window.canvas_view.command_manager
    before = commands.undo_depth
    panel.notes_edit.setPlainText("Modification")
    panel.notes_edit.setPlainText("Conserver")
    commands.flush_pending()
    assert commands.undo_depth == before
    assert plant.metadata["plant_instance"]["notes"] == "Conserver"


def test_undo_updates_focused_custom_value(plant_window, qtbot):
    window, plant = plant_window
    panel = window.plant_database_panel
    window.show()
    qtbot.waitExposed(window)
    panel._on_add_custom_field()
    row = panel.custom_fields_layout.itemAt(0).widget()
    row.key_edit.setText("Origine")
    row.key_edit.editingFinished.emit()
    row.value_edit.setFocus()
    row.value_edit.setText("Pépinière")
    window.canvas_view.command_manager.undo()
    qtbot.waitUntil(lambda: row.value_edit.text() == "")
    assert plant.metadata["plant_instance"]["custom_fields"] == {"Origine": ""}


def test_editing_custom_fields_preserves_unmodified_legacy_values(plant_window):
    window, plant = plant_window
    panel = window.plant_database_panel
    plant.metadata["plant_instance"]["custom_fields"] = {"Zéro": 0, "Autre": "avant"}
    plant.setSelected(False)
    plant.setSelected(True)
    rows = [panel.custom_fields_layout.itemAt(i).widget()
            for i in range(panel.custom_fields_layout.count() - 1)]
    zero = next(row for row in rows if row.key_edit.text() == "Zéro")
    other = next(row for row in rows if row.key_edit.text() == "Autre")
    assert zero.value_edit.text() == "0"
    other.value_edit.setText("après")
    assert plant.metadata["plant_instance"]["custom_fields"] == {"Zéro": 0, "Autre": "après"}
    window.canvas_view.command_manager.undo()
    assert plant.metadata["plant_instance"]["custom_fields"] == {"Zéro": 0, "Autre": "avant"}
    zero.key_edit.clear()
    zero.key_edit.setText("N")
    zero.key_edit.setText("Nouveau zéro")
    assert plant.metadata["plant_instance"]["custom_fields"] == {"Nouveau zéro": 0, "Autre": "avant"}
    window.canvas_view.command_manager.undo()
    assert plant.metadata["plant_instance"]["custom_fields"] == {"Zéro": 0, "Autre": "avant"}


def test_invalid_custom_name_preserves_data_and_blocks_save_and_selection(plant_window, tmp_path, monkeypatch):
    from open_garden_planner.agent_api.exports import save_plan_file

    window, plant = plant_window
    panel = window.plant_database_panel
    plant.metadata["plant_instance"]["custom_fields"] = {"A": 1, "AB": 2}
    plant.setSelected(False)
    plant.setSelected(True)
    manager = window._project_manager
    path = tmp_path / "fields.ogp"
    manager.save(window.canvas_scene, path)
    saved = path.read_bytes()
    rows = [panel.custom_fields_layout.itemAt(i).widget()
            for i in range(panel.custom_fields_layout.count() - 1)]
    row = next(row for row in rows if row.key_edit.text() == "AB")
    row.key_edit.clear()
    assert panel._custom_fields_invalid
    assert plant.metadata["plant_instance"]["custom_fields"] == {"A": 1, "AB": 2}
    assert manager.is_dirty
    with pytest.raises(ValueError, match="non-empty and unique"):
        manager.save(window.canvas_scene, path)
    with pytest.raises(ValueError, match="non-empty and unique"):
        save_plan_file(window.canvas_scene, manager, window._soil_service, None)
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *_args: errors.append(True))
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Save)
    assert not window.close()
    assert errors
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Discard)
    assert path.read_bytes() == saved
    row.key_edit.setText("A")
    assert panel._custom_fields_invalid
    assert plant.metadata["plant_instance"]["custom_fields"] == {"A": 1, "AB": 2}
    other = CircleItem(300, 100, 30, object_type=ObjectType.TREE)
    other.metadata["plant_species"] = {"scientific_name": "Pyrus communis", "common_name": "Poirier"}
    window.canvas_scene.addItem(other)
    other.setSelected(True)
    assert plant.isSelected() and not other.isSelected()
    assert panel._current_plant_item is plant
    row.key_edit.setText("AC")
    assert not panel._custom_fields_invalid
    assert plant.metadata["plant_instance"]["custom_fields"] == {"A": 1, "AC": 2}
    manager.save(window.canvas_scene, path)
    window.canvas_view.command_manager.undo()
    assert plant.metadata["plant_instance"]["custom_fields"] == {"A": 1, "AB": 2}


def _make_invalid_custom_fields(window, plant):
    panel = window.plant_database_panel
    plant.metadata["plant_instance"]["custom_fields"] = {"A": 1, "AB": 2}
    plant.setSelected(False)
    plant.setSelected(True)
    rows = [panel.custom_fields_layout.itemAt(i).widget()
            for i in range(panel.custom_fields_layout.count() - 1)]
    next(row for row in rows if row.key_edit.text() == "AB").key_edit.setText("A")
    assert panel._custom_fields_invalid
    return panel


def test_discard_invalid_draft_on_new_plan_and_keep_guard(plant_window, tmp_path):
    window, plant = plant_window
    panel = _make_invalid_custom_fields(window, plant)
    with pytest.raises(ValueError, match="unsaved changes"):
        window._do_agent_new_plan(500, 500, False)
    assert panel._custom_fields_invalid
    window._do_agent_new_plan(500, 500, True)
    assert not panel._custom_fields_invalid
    window._project_manager.save(window.canvas_scene, tmp_path / "new.ogp")


def test_invalid_draft_survives_failed_open_but_clears_after_open(plant_window, tmp_path):
    from open_garden_planner.core.project import ProjectManager
    from open_garden_planner.ui.canvas.canvas_scene import CanvasScene

    window, plant = plant_window
    path = tmp_path / "other.ogp"
    other = ProjectManager()
    other.save(CanvasScene(), path)
    other.release_document_ownership()
    invalid = tmp_path / "invalid.ogp"
    invalid.write_text("not json", encoding="utf-8")
    panel = _make_invalid_custom_fields(window, plant)
    with pytest.raises(ValueError):
        window._do_agent_open_plan(str(invalid), True)
    assert panel._custom_fields_invalid
    assert plant.scene() is window.canvas_scene
    window._do_agent_open_plan(str(path), True)
    assert not panel._custom_fields_invalid
    window._project_manager.save(window.canvas_scene, path)


def test_invalid_draft_clears_when_plant_is_deleted_and_undo_keeps_data(plant_window, tmp_path):
    from open_garden_planner.core.commands import DeleteItemsCommand

    window, plant = plant_window
    panel = _make_invalid_custom_fields(window, plant)
    commands = window.canvas_view.command_manager
    commands.execute(DeleteItemsCommand(window.canvas_scene, [plant]))
    assert not panel._custom_fields_invalid
    window._project_manager.save(window.canvas_scene, tmp_path / "deleted.ogp")
    commands.undo()
    assert plant.metadata["plant_instance"]["custom_fields"] == {"A": 1, "AB": 2}


def test_other_command_restores_invalid_draft_without_losing_fields(plant_window):
    window, plant = plant_window
    panel = _make_invalid_custom_fields(window, plant)
    panel.measurement_service.set_card(str(plant.item_id), {"name": "Pommier", "state": "in_place"})
    assert not panel._custom_fields_invalid
    assert plant.metadata["plant_instance"]["custom_fields"] == {"A": 1, "AB": 2}
    assert "last valid names" in panel.custom_fields_status.text()
    assert not panel.custom_fields_status.isHidden()


def test_note_debounce_does_not_replace_invalid_custom_draft(plant_window, qtbot, tmp_path):
    window, plant = plant_window
    panel = window.plant_database_panel
    plant.metadata["plant_instance"]["custom_fields"] = {"A": 1, "AB": 2}
    plant.setSelected(False)
    plant.setSelected(True)
    panel.notes_edit.setPlainText("Note en cours")
    row = panel.custom_fields_layout.itemAt(1).widget()
    row.key_edit.setText("A")
    assert row.key_edit.text() == "A"
    qtbot.wait(750)
    assert panel._custom_fields_invalid
    assert row.key_edit.text() == "A"
    with pytest.raises(ValueError, match="non-empty and unique"):
        window._project_manager.save(window.canvas_scene, tmp_path / "draft.ogp")
