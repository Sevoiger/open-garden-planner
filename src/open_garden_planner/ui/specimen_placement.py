"""Explicit specimen placement through the canonical plant constructor."""
from copy import deepcopy
from uuid import UUID

from open_garden_planner.core.plant_specimens import PlaceSpecimenCommand
from open_garden_planner.ui.plant_creation import build_species_plant


def place_specimen(project, scene, commands, identity, position, *, species=None):
    record = project.plant_specimens.get(identity)
    if record is None:
        raise ValueError("Unknown unplaced specimen")
    if set(record) - {"item_id", "name", "object_type", "metadata"}:
        raise ValueError("This specimen contains unknown record fields; placement would lose them")
    snapshot = record["metadata"].get("plant_species") or species
    if not isinstance(snapshot, dict):
        raise ValueError("A botanical snapshot is required for placement")
    item = build_species_plant(scene, position, snapshot)
    item._item_id = UUID(identity)
    item.name = record["name"]
    item.metadata.clear()
    item.metadata.update(deepcopy(record["metadata"]))
    commands.execute(PlaceSpecimenCommand(project, scene, item))
    return item
