"""A sheet opens offline without canvas selection and shares existing stores."""
from PyQt6.QtWidgets import QDialog, QLabel

from open_garden_planner.core.commands import CommandManager
from open_garden_planner.core.plant_specimens import SpecimenEditCommand
from open_garden_planner.core.project import ProjectManager
from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services.plant_library import PlantLibrary
from open_garden_planner.services.plant_specimens import add_specimen
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.dialogs.plant_sheet_dialog import PlantSheetDialog
from open_garden_planner.ui.views.plant_catalog_view import PlantSpeciesEditDialog


def test_complete_sheet_is_read_only_on_open_and_never_downloads(qtbot, tmp_path, monkeypatch):
    library = PlantLibrary(tmp_path / "library.json")
    identity = library.add_plant(PlantSpeciesData("Rosa canina", "Églantier", image_url="https://example.test/photo.jpg",
                                                raw_data={"local_sheet": {"characteristics": {"Rusticité": "-15°C"}}}))
    before = library.path.read_bytes()
    def no_network(*_a, **_kw):
        raise AssertionError("Opening a sheet must remain offline")
    monkeypatch.setattr("urllib.request.OpenerDirector.open", no_network)
    sheet = PlantSheetDialog(library, identity)
    qtbot.addWidget(sheet)
    sheet.show()
    assert [sheet.tabs.tabText(i) for i in range(sheet.tabs.count())] == [
        "Presentation", "Culture and calendar", "Quantity and personal details", "Photos and documents", "Sources",
    ]
    assert sheet.title.text() == "Églantier"
    assert any("-15°C" in label.text() for label in sheet.sources.findChildren(QLabel))
    assert library.path.read_bytes() == before


def test_sheet_embeds_same_specimens_and_updates_photo_choices(qtbot, tmp_path):
    project, scene, commands = ProjectManager(), CanvasScene(), CommandManager()
    library = PlantLibrary(tmp_path / "library.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose"))
    sheet = PlantSheetDialog(library, identity, project=project, scene=scene, commands=commands)
    qtbot.addWidget(sheet)
    assert sheet.specimens is not None
    document, specimen_id = add_specimen(project.snapshot_dict(scene), identity, "Rose en pot")
    commands.execute(SpecimenEditCommand(project, document))
    sheet.tabs.setCurrentIndex(3)
    assert sheet.photo_chooser.findData(specimen_id) >= 0
    assert not scene.items()


def test_sheet_edit_preserves_unknown_fields_and_updates_presentation(qtbot, tmp_path, monkeypatch):
    library = PlantLibrary(tmp_path / "library.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose", raw_data={"future": {"keep": True}}))
    sheet = PlantSheetDialog(library, identity)
    qtbot.addWidget(sheet)
    def accept_editor(editor):
        editor.common_name_edit.setText("Rose corrigée")
        editor._save()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(PlantSpeciesEditDialog, "exec", accept_editor)
    sheet._edit()
    assert sheet.title.text() == "Rose corrigée"
    assert library.get_plant(identity).raw_data["future"] == {"keep": True}


def test_botanical_editor_keeps_unknown_numeric_bounds_distinct_from_zero(qtbot):
    editor = PlantSpeciesEditDialog(PlantSpeciesData("Rosa", "Rose", ph_max=6.789123))
    qtbot.addWidget(editor)
    editor.numeric_fields["ph_min"][0].setText("0")
    editor.numeric_fields["min_height_cm"][0].setText("23,45678")
    editor._save()
    assert editor.plant.ph_min == 0
    assert editor.plant.ph_max == 6.789123
    assert editor.plant.min_height_cm == 23.45678
    assert editor.plant.hardiness_zone_min is None


def test_reference_refresh_does_not_erase_unsaved_personal_fields(qtbot, tmp_path):
    project, scene, commands = ProjectManager(), CanvasScene(), CommandManager()
    library = PlantLibrary(tmp_path / "library.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose"))
    document, _ = add_specimen(project.snapshot_dict(scene), identity, "Rose en pot")
    commands.execute(SpecimenEditCommand(project, document))
    sheet = PlantSheetDialog(library, identity, project=project, scene=scene, commands=commands)
    qtbot.addWidget(sheet)
    sheet.specimens.name.setText("Nom personnel non enregistré")
    sheet._reference_changed()
    assert sheet.specimens.name.text() == "Nom personnel non enregistré"
