"""Undoable adapter for unplaced plants using the project's existing history."""
from copy import deepcopy

from PyQt6.QtCore import QCoreApplication

from open_garden_planner.core.commands import Command
from open_garden_planner.models.garden_memory import validate_memory
from open_garden_planner.services.plant_specimens import validate_specimens


class ProjectSpecimenHandle:
    """Read protocol used by the existing measurement dialog; no graphics item."""
    def __init__(self, project, identity):
        from uuid import UUID

        self.project, self.item_id = project, UUID(identity)

    @property
    def metadata(self):
        return self.project.plant_specimens[str(self.item_id)]["metadata"]


def replace_specimen_metadata(project, scene, commands, identity, metadata):
    """Publish a prepared metadata change through the existing project history."""
    from open_garden_planner.core.garden_memory import GardenEditCommand
    from open_garden_planner.services.plant_specimens import specimen_record

    candidate = project.snapshot_dict(scene)
    record = specimen_record(candidate, identity)
    record["metadata"] = deepcopy(metadata)
    if identity in project.plant_specimens:
        commands.execute(SpecimenEditCommand(project, candidate))
        return
    items = [item for item in scene.items() if str(getattr(item, "item_id", "")) == identity]
    if len(items) != 1:
        raise ValueError("Specimen identity is absent or ambiguous")
    item = items[0]
    layer = next((layer for layer in getattr(scene, "layers", [])
                  if layer.id == getattr(item, "layer_id", None)), None)
    if layer is not None and layer.locked:
        raise ValueError("The specimen layer is locked")
    commands.execute(GardenEditCommand(project, project.garden_memory, item, metadata))


class SpecimenEditCommand(Command):
    def __init__(self, project, candidate):
        self.project = project
        self.before = (project.plant_specimens, project.garden_memory)
        self.after = (
            validate_specimens(candidate.get("plant_specimens", {})),
            validate_memory(candidate.get("garden_memory", {})),
        )

    @property
    def description(self):
        return QCoreApplication.translate("Commands", "Garden records")

    def _apply(self, snapshot):
        specimens, memory = deepcopy(snapshot)
        # Receipts remain durable across undo just as in GardenEditCommand.
        receipts = self.project.garden_memory.get("requests", {})
        if receipts:
            memory = memory or {"version": 1, "events": {}, "requests": {}}
            memory.setdefault("requests", {}).update(receipts)
        validate_specimens(specimens)
        validate_memory(memory)
        self.project.restore_plant_specimens(specimens)
        self.project.restore_garden_memory(memory)

    def execute(self):
        self._apply(self.after)

    def undo(self):
        self._apply(self.before)


class PlaceSpecimenCommand(Command):
    """Transfer one canonical plant record to a shape without copying history."""

    def __init__(self, project, scene, item):
        from open_garden_planner.core.commands import CreateItemCommand

        self.project, self.scene, self.item = project, scene, item
        self.identity = str(item.item_id)
        self.before = project.plant_specimens
        if self.identity not in self.before:
            raise ValueError("Specimen is not available for placement")
        if any(getattr(other, "item_id", None) == item.item_id for other in scene.items()):
            raise ValueError("Specimen is already on the plan")
        record = self.before[self.identity]
        if item.metadata != record["metadata"] or item.name != record["name"]:
            raise ValueError("Placement must preserve the specimen metadata")
        self.after = deepcopy(self.before)
        del self.after[self.identity]
        self.creation = CreateItemCommand(scene, item)

    @property
    def description(self):
        return QCoreApplication.translate("Commands", "Garden records")

    def execute(self):
        self.creation.execute()
        try:
            self.project.restore_plant_specimens(self.after)
        except Exception:
            self.creation.undo()
            raise

    def undo(self):
        self.creation.undo()
        self.project.restore_plant_specimens(self.before)
