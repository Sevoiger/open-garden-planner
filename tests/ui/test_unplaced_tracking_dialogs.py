"""Existing tracking forms operate on an unplaced plant without a second history."""
from PyQt6.QtWidgets import QDialog

from open_garden_planner.core.commands import CommandManager
from open_garden_planner.core.garden_memory import GardenMemoryService
from open_garden_planner.core.plant_specimens import ProjectSpecimenHandle, SpecimenEditCommand
from open_garden_planner.core.project import ProjectManager
from open_garden_planner.services.plant_specimens import add_specimen
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.dialogs.garden_memory_dialog import EventEditor, GardenMemoryDialog
from open_garden_planner.ui.dialogs.plant_measurements_dialog import PlantMeasurementsDialog


def test_existing_dialogs_share_unplaced_plant_history(qtbot, monkeypatch):
    project, scene, commands = ProjectManager(), CanvasScene(), CommandManager()
    document, identity = add_specimen(project.snapshot_dict(scene), "rose", "Rose en pot")
    commands.execute(SpecimenEditCommand(project, document))
    service = GardenMemoryService(project, commands, scene)
    measurement = PlantMeasurementsDialog(service, ProjectSpecimenHandle(project, identity))
    qtbot.addWidget(measurement)
    measurement.day.setText("2026-05-01")
    measurement.height.setText("31")
    measurement._save()
    assert measurement.history.count() == 1
    assert project.plant_specimens[identity]["metadata"]["plant_instance"]["current_height_cm"] == 31

    records = GardenMemoryDialog(service, identity)
    qtbot.addWidget(records)
    assert records.panel.target_id == identity
    assert records.panel.name_edit.text() == "Rose en pot"
    def record_observation(dialog):
        dialog.date_edit.setText("")
        dialog.description.setPlainText("Première pousse observée")
        dialog.accept()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(EventEditor, "exec", record_observation)
    records.panel._add_event()
    assert len(project.garden_memory["events"]) == 1
    event = next(iter(project.garden_memory["events"].values()))
    assert event["targets"] == [{"id": identity, "name": "Rose en pot"}]
    assert event["period"]["precision"] == "unknown"
    assert not scene.items()
