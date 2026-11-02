"""Structured personal edits preserve canonical history on placed/unplaced plants."""
from copy import deepcopy

import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtWidgets import QDialog, QWidget

from open_garden_planner.core.commands import CommandManager
from open_garden_planner.core.plant_specimens import SpecimenEditCommand
from open_garden_planner.core.project import ProjectManager
from open_garden_planner.services.plant_specimens import add_specimen, specimen_record
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.dialogs.garden_memory_dialog import EventEditor
from open_garden_planner.ui.dialogs.plant_specimens_dialog import PlantSpecimensDialog
from open_garden_planner.ui.specimen_placement import place_specimen


@pytest.mark.parametrize("placed", [False, True])
def test_structured_personal_edit_is_one_undo_step_and_roundtrips(qtbot, tmp_path, placed):
    project, scene, commands = ProjectManager(), CanvasScene(), CommandManager()
    species = {"common_name": "Rose", "scientific_name": "Rosa"}
    document, identity = add_specimen(project.snapshot_dict(scene), "rose", "Rose gauche", species_data=species)
    metadata = document["plant_specimens"][identity]["metadata"]
    metadata["future"] = {"keep": True}
    metadata["plant_instance"]["media_assets"] = [{"id": "a" * 64, "kind": "document", "photo_path": "receipt.pdf"}]
    commands.execute(SpecimenEditCommand(project, document))
    if placed:
        place_specimen(project, scene, commands, identity, QPointF(120, 150))
    path = tmp_path / "garden.ogp"
    (tmp_path / "receipt.pdf").write_bytes(b"synthetic receipt")
    project.save(scene, path)
    before = deepcopy(specimen_record(project.snapshot_dict(scene), identity))
    history = deepcopy(project.garden_memory)
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = PlantSpecimensDialog("rose", project, scene, commands, parent, embedded=True)
    qtbot.addWidget(dialog)
    assert dialog.windowFlags() & Qt.WindowType.Window == 0
    dialog.acquisition_period.setText("spring 2024")
    dialog.price.setText("12,345678")
    dialog.currency.setText("EUR")
    dialog.supplier.setText("Pépinière fictive")
    dialog.receipt.setCurrentIndex(dialog.receipt.findData("a" * 64))
    dialog.planting_period.setText("autumn 2024")
    dialog.mode.setCurrentIndex(dialog.mode.findData("pot"))
    dialog.relation.setCurrentIndex(dialog.relation.findData("wanted"))
    dialog.state.setCurrentIndex(dialog.state.findData("dormant"))
    dialog.text_fields["container"].setText("Bac avec fond")
    dialog.text_fields["substrate"].setText("Terreau local")
    dialog.text_fields["old_number"].setText("ancien 17")
    dialog.text_fields["alias"].setText("Rosier gauche")
    dialog.text_fields["nfc_tag"].setText("nfc:test")
    dialog.notes.setPlainText("Observation personnelle")
    depth = commands._history.undo_depth
    dialog._save()
    assert commands._history.undo_depth == depth + 1
    saved = specimen_record(project.snapshot_dict(scene), identity)
    instance = saved["metadata"]["plant_instance"]
    assert instance["acquisition_period"]["precision"] == "season"
    assert "acquisition_date" not in instance
    assert instance["acquisition_price"] == 12.345678
    assert instance["acquisition_currency"] == "EUR"
    assert instance["acquisition_receipt_media_id"] == "a" * 64
    assert instance["relation"] == "wanted"
    assert instance["nfc_tag"] == "nfc:test"
    assert saved["metadata"]["garden_tracking"]["period"]["precision"] == "season"
    assert "planting_date" not in instance
    assert saved["metadata"]["future"] == {"keep": True}
    assert project.garden_memory == history or not history
    commands.undo()
    assert specimen_record(project.snapshot_dict(scene), identity) == before
    commands.redo()
    project.save(scene, path)
    reopened, new_scene = ProjectManager(), CanvasScene()
    reopened.load(new_scene, path)
    assert specimen_record(reopened.snapshot_dict(new_scene), identity) == saved


def test_unrelated_edit_keeps_precise_price_unknown_dates_and_future_identifiers(qtbot):
    project, scene, commands = ProjectManager(), CanvasScene(), CommandManager()
    document, identity = add_specimen(project.snapshot_dict(scene), "rose", "Rose")
    instance = document["plant_specimens"][identity]["metadata"]["plant_instance"]
    instance.update(acquisition_price=1.23456789, future_identifier={"id": "future"})
    commands.execute(SpecimenEditCommand(project, document))
    dialog = PlantSpecimensDialog("rose", project, scene, commands)
    qtbot.addWidget(dialog)
    dialog.notes.setPlainText("Unrelated note")
    dialog._save()
    saved = project.plant_specimens[identity]["metadata"]["plant_instance"]
    assert saved["acquisition_price"] == 1.23456789
    assert saved["future_identifier"] == {"id": "future"}
    assert "acquisition_period" not in saved and "acquisition_date" not in saved


def test_repotting_uses_existing_intervention_history_with_unknown_date(qtbot, monkeypatch):
    project, scene, commands = ProjectManager(), CanvasScene(), CommandManager()
    document, identity = add_specimen(project.snapshot_dict(scene), "rose", "Rose")
    commands.execute(SpecimenEditCommand(project, document))
    dialog = PlantSpecimensDialog("rose", project, scene, commands)
    qtbot.addWidget(dialog)
    def accept_event(editor):
        assert editor.date_edit.text() == ""
        assert editor.type_combo.currentData() == "observation"
        assert editor.description.toPlainText() == "Repotting"
        editor.accept()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(EventEditor, "exec", accept_event)
    dialog._intervention("observation", "Repotting")
    assert len(project.garden_memory["events"]) == 1
    event = next(iter(project.garden_memory["events"].values()))
    assert event["period"]["precision"] == "unknown"
    assert event["targets"][0]["id"] == identity
    assert "repotting_history" not in project.plant_specimens[identity]
