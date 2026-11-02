"""Export selection is explicit and independent of the canvas selection."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFileDialog

from open_garden_planner.core.commands import CommandManager
from open_garden_planner.core.plant_specimens import SpecimenEditCommand
from open_garden_planner.core.project import ProjectManager
from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services.plant_specimens import add_specimen
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.dialogs.plant_catalog_export_dialog import PlantCatalogExportDialog


def test_export_dialog_writes_checked_species_without_a_canvas(qtbot, tmp_path, monkeypatch):
    entries = [("rose", PlantSpeciesData("Rosa", "Rose")), ("manual", PlantSpeciesData("", "Nom provisoire"))]
    dialog = PlantCatalogExportDialog(entries, selected_ids=["rose"])
    qtbot.addWidget(dialog)
    assert not dialog.personal.isEnabled()
    assert dialog.species_list.item(0).checkState() == Qt.CheckState.Checked
    assert dialog.species_list.item(1).checkState() == Qt.CheckState.Unchecked
    dialog.species_list.item(1).setCheckState(Qt.CheckState.Checked)
    destination = tmp_path / "sheets.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_kw: (str(destination), ""))
    dialog._export()
    assert dialog.exported == destination
    assert destination.read_bytes().startswith(b"%PDF-")


def test_photo_checkboxes_distinguish_same_media_on_two_specimens(qtbot):
    project, scene, commands = ProjectManager(), CanvasScene(), CommandManager()
    document = project.snapshot_dict(scene)
    identities = []
    for name in ("Rose gauche", "Rose droite"):
        document, identity = add_specimen(document, "rose", name)
        identities.append(identity)
        document["plant_specimens"][identity]["metadata"]["plant_instance"]["media_assets"] = [
            {"id": "a" * 64, "photo_path": "photo.jpg", "kind": "personal", "caption": name},
        ]
    commands.execute(SpecimenEditCommand(project, document))
    dialog = PlantCatalogExportDialog([("rose", PlantSpeciesData("Rosa", "Rose"))], selected_ids=["rose"],
                                      project=project, scene=scene)
    qtbot.addWidget(dialog)
    assert dialog.photos.count() == 2
    first = dialog.photos.item(0)
    second = dialog.photos.item(1)
    assert first.data(Qt.ItemDataRole.UserRole) != second.data(Qt.ItemDataRole.UserRole)
    first.setCheckState(Qt.CheckState.Checked)
    dialog._refresh_photos()
    assert dialog.photos.item(0).checkState() == Qt.CheckState.Checked
    assert dialog.photos.item(1).checkState() == Qt.CheckState.Unchecked
