"""Library media in species snapshots never become project-relative files."""
import pytest
from PIL import Image
from PyQt6.QtCore import QPointF

from open_garden_planner.core.commands import CommandManager
from open_garden_planner.core.plant_specimens import SpecimenEditCommand
from open_garden_planner.core.project import ProjectManager
from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services.botanical_media import (
    edit_reference_media,
    import_reference_media,
    resolve_reference_media,
)
from open_garden_planner.services.garden_archive import portable_backup, restore_backup
from open_garden_planner.services.plant_library import PlantLibrary
from open_garden_planner.services.plant_specimens import add_specimen
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.specimen_placement import place_specimen


@pytest.mark.parametrize("placed", [False, True])
@pytest.mark.parametrize("reference_removed", [False, True])
def test_illustrated_snapshot_survives_save_as_and_global_portable_backup(qtbot, tmp_path, placed, reference_removed):
    library = PlantLibrary(tmp_path / "app-data/custom_plants.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose"))
    source = tmp_path / "reference.png"
    Image.new("RGB", (48, 48), "white").save(source)
    asset = import_reference_media(source, library.path, identity)
    library.update_plant(identity, edit_reference_media(library.get_plant(identity), asset=asset))
    project, scene, commands = ProjectManager(), CanvasScene(), CommandManager()
    species = library.get_plant(identity).to_dict()
    document, specimen = add_specimen(project.snapshot_dict(scene), identity, "Rose en pot", species_data=species)
    commands.execute(SpecimenEditCommand(project, document))
    if placed:
        place_specimen(project, scene, commands, specimen, QPointF(100, 200))
    if reference_removed:
        library.update_plant(identity, edit_reference_media(library.get_plant(identity), remove_id=asset["id"]))
    original = tmp_path / "plans/garden.ogp"
    original.parent.mkdir()
    project.save(scene, original)
    relocated = tmp_path / "moved/garden.ogp"
    relocated.parent.mkdir()
    project.save(scene, relocated)
    archive = tmp_path / "backup.zip"
    portable_backup(project.snapshot_dict(scene), relocated, archive, app_data=library.path.parent)
    restored = restore_backup(archive, tmp_path / "restored")
    new_library = PlantLibrary(restored.parent / "app-data/custom_plants.json")
    assert resolve_reference_media(new_library.path, asset["photo_path"]).is_file()
    assert resolve_reference_media(new_library.path, asset["thumbnail_path"]).is_file()
    loaded, new_scene = ProjectManager(), CanvasScene()
    loaded.load(new_scene, restored)
    snapshot = loaded.snapshot_dict(new_scene)
    record = snapshot["objects"][0] if placed else snapshot["plant_specimens"][specimen]
    assert record["metadata"]["plant_species"] == species
