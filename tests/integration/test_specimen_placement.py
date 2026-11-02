"""One canonical plant identity survives placement, undo and .ogp reopen."""
from copy import deepcopy

from PyQt6.QtCore import QPointF

from open_garden_planner.core.commands import CommandManager
from open_garden_planner.core.garden_memory import GardenMemoryService
from open_garden_planner.core.plant_specimens import SpecimenEditCommand
from open_garden_planner.core.project import ProjectManager
from open_garden_planner.services.plant_specimens import add_specimen
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.specimen_placement import place_specimen


def test_place_preserves_identity_history_metadata_and_reopen(qtbot, tmp_path):
    project, scene, commands = ProjectManager(), CanvasScene(), CommandManager()
    species = {"common_name": "Rose", "scientific_name": "Rosa", "max_spread_cm": 75}
    candidate, identity = add_specimen(project.snapshot_dict(scene), "rose", "Rose gauche",
                                       species_data=species, cultivation_mode="pot")
    commands.execute(SpecimenEditCommand(project, candidate))
    service = GardenMemoryService(project, commands, scene)
    service.plant_measurement("add_plant_measurement", identity,
                              {"date": "2026-04-01", "height_cm": 42}, "once")
    memory = deepcopy(project.garden_memory)
    metadata = deepcopy(project.plant_specimens[identity]["metadata"])
    item = place_specimen(project, scene, commands, identity, QPointF(125, 250))
    assert str(item.item_id) == identity
    assert item.metadata == metadata
    assert identity not in project.plant_specimens
    assert project.garden_memory == memory
    commands.undo()
    assert item.scene() is None
    assert project.plant_specimens[identity]["metadata"] == metadata
    commands.redo()
    assert item.scene() is scene
    assert project.garden_memory == memory
    path = tmp_path / "placed.ogp"
    project.save(scene, path)
    reopened, new_scene = ProjectManager(), CanvasScene()
    reopened.load(new_scene, path)
    plants = [i for i in new_scene.items() if str(getattr(i, "item_id", "")) == identity]
    assert len(plants) == 1
    assert plants[0].metadata == metadata
    assert not reopened.plant_specimens
    assert reopened.garden_memory == memory
