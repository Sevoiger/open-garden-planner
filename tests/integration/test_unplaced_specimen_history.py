"""Unplaced plants use the same Qt undo and garden operations as plan plants."""
from PyQt6.QtWidgets import QGraphicsScene

from open_garden_planner.core.commands import CommandManager
from open_garden_planner.core.garden_memory import GardenMemoryService
from open_garden_planner.core.plant_specimens import SpecimenEditCommand
from open_garden_planner.core.project import ProjectManager
from open_garden_planner.services.plant_specimens import add_specimen


def test_unplaced_create_measure_undo_redo(qtbot, tmp_path):
    project = ProjectManager()
    scene = QGraphicsScene()
    commands = CommandManager()
    candidate, identity = add_specimen(project.snapshot_dict(scene), "rose", "Rose pot")
    commands.execute(SpecimenEditCommand(project, candidate))
    service = GardenMemoryService(project, commands, scene)
    service.plant_measurement("add_plant_measurement", identity,
                              {"date": "2026-04-01", "height_cm": 42}, "once")
    assert project.plant_specimens[identity]["metadata"]["plant_instance"]["current_height_cm"] == 42
    commands.undo()
    assert "current_height_cm" not in project.plant_specimens[identity]["metadata"]["plant_instance"]
    commands.redo()
    assert project.plant_specimens[identity]["metadata"]["plant_instance"]["current_height_cm"] == 42
    assert not scene.items()
    path = tmp_path / "garden.ogp"
    project.save(scene, path)
    reopened = ProjectManager()
    reopened.load(QGraphicsScene(), path)
    assert reopened.plant_specimens == project.plant_specimens
    assert reopened.garden_memory == project.garden_memory
