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
    # Notes are still edited directly by this panel; undoing a date must not
    # silently discard a later note merely because it is in the same metadata.
    panel.notes_edit.setPlainText("Note ajoutée après la date")
    commands.undo()
    qtbot.waitUntil(lambda: panel.planting_date_edit.date() == QDate(2020, 1, 1))
    assert plant.metadata["plant_instance"]["notes"] == "Note ajoutée après la date"
    commands.redo()
    qtbot.waitUntil(lambda: panel.planting_date_edit.date() == QDate(2021, 3, 4))
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
