"""Shared undoable garden writes for the GUI and main-thread MCP providers."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import TYPE_CHECKING, Any
from uuid import UUID

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QGraphicsScene

from open_garden_planner.core.commands import Command, CommandManager
from open_garden_planner.core.object_types import is_bed_type, is_plant_type
from open_garden_planner.models.garden_memory import validate_card, validate_event, validate_memory

if TYPE_CHECKING:
    from open_garden_planner.core.project import ProjectManager


class GardenEditCommand(Command):
    """One gesture atomically owns the tracking snapshot and optional card metadata."""

    def __init__(self, project: ProjectManager, new: dict[str, Any],
                 item: Any = None, metadata: dict[str, Any] | None = None, name: str | None = None) -> None:
        self.project = project
        self.old = project.garden_memory
        self.new = validate_memory(new)
        self.item = item
        self.old_metadata = deepcopy(item.metadata) if item is not None else None
        self.new_metadata = deepcopy(metadata)
        self.old_name = item.name if item is not None else None
        self.new_name = name if name is not None else self.old_name

    @property
    def description(self) -> str:
        return QCoreApplication.translate("Commands", "Garden records")

    def _apply(self, memory: dict[str, Any], metadata: dict[str, Any] | None, name: str | None) -> None:
        candidate = deepcopy(memory)
        receipts = self.project.garden_memory.get("requests", {})
        if receipts:
            candidate = candidate or {"version": 1, "events": {}, "requests": {}}
            # Protocol receipts survive undo/deletion: a late network retry
            # must never resurrect an explicitly undone intervention.
            candidate.setdefault("requests", {}).update(receipts)
        validate_memory(candidate)
        if self.item is not None and metadata is not None:
            self.item.prepareGeometryChange()
            self.item.metadata.clear()
            self.item.metadata.update(deepcopy(metadata))
            if name is not None:
                self.item.name = name
            self.item.update()
        self.project.restore_garden_memory(candidate)

    def execute(self) -> None:
        self._apply(self.new, self.new_metadata, self.new_name)

    def undo(self) -> None:
        self._apply(self.old, self.old_metadata, self.old_name)


class GardenMemoryService:
    """Qt main-thread adapter. Never call this from the MCP server thread."""

    def __init__(self, project: ProjectManager, commands: CommandManager,
                 scene: QGraphicsScene) -> None:
        self.project, self.commands, self.scene = project, commands, scene

    def _item(self, item_id: str) -> Any:
        identity = UUID(item_id)
        item = next((i for i in self.scene.items() if getattr(i, "item_id", None) == identity), None)
        if item is None or not hasattr(item, "metadata"):
            raise ValueError(f"Unknown garden target: {item_id}")
        if not isinstance(item.metadata, dict) or not isinstance(item.metadata.get("plant_instance", {}), dict):
            raise ValueError("Target metadata is malformed; repair it before editing")
        if getattr(item, "note_id", None) is not None:
            raise ValueError("A positional journal pin is not a garden target")
        return item

    def _editable(self, item: Any) -> None:
        if item.parentItem() is not None:
            raise ValueError("Edit the top-level object or ungroup it first")
        layer = next((layer for layer in getattr(self.scene, "layers", [])
                      if layer.id == getattr(item, "layer_id", None)), None)
        if layer is not None and layer.locked:
            raise ValueError("The target layer is locked")

    def _check_media(self, media: list[str], previous: list[str] | None = None) -> None:
        for reference in set(media) - set(previous or []):
            project = self.project.current_file
            if project is None:
                raise ValueError("Save the project before attaching media")
            path = (project.parent / reference).resolve()
            path.relative_to(project.parent.resolve())
            if not path.is_file():
                raise ValueError(f"Missing project attachment: {reference}")

    def _memory(self) -> dict[str, Any]:
        return self.project.garden_memory or {"version": 1, "events": {}, "requests": {}}

    def set_planting_date(self, item_id: str, value: str | None) -> dict[str, Any]:
        """Patch only a plant's date, preserving the other tracking fields."""
        from open_garden_planner.models.garden_memory import period

        item = self._item(item_id)
        if not is_plant_type(getattr(item, "object_type", None)):
            raise ValueError("A planting date requires a plant (TREE, SHRUB or PERENNIAL)")
        fields = deepcopy(item.metadata.get("garden_tracking", {}))
        fields["period"] = period(value)
        return self._set_card(item_id, fields, require_name=False)

    def set_card(self, item_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Replace tracking fields, retaining the shape's existing identity/geometry."""
        return self._set_card(item_id, fields, require_name=True)

    def _set_card(self, item_id: str, fields: dict[str, Any], *, require_name: bool) -> dict[str, Any]:
        item = self._item(item_id)
        self._editable(item)
        fields = deepcopy(fields)
        name = fields.pop("name", item.name)
        if not isinstance(name, str) or require_name and not name.strip():
            raise ValueError("A provisional or botanical label is required")
        card = validate_card(fields)
        if card.get("kind") == "zone" and not is_bed_type(item.object_type):
            raise ValueError("A zone must use an existing bed shape")
        zone_id = card.get("zone_id")
        if zone_id:
            zone = self._item(zone_id)
            if zone is item or not is_bed_type(zone.object_type):
                raise ValueError("Zone must reference another existing bed")
        self._check_media(card.get("media", []), item.metadata.get("garden_tracking", {}).get("media", []))
        metadata = deepcopy(item.metadata)
        for key, legacy in (("notes", "notes"), ("cultivar", "variety_cultivar")):
            if key in card:
                metadata.setdefault("plant_instance", {})[legacy] = card.pop(key)
        metadata["garden_tracking"] = card
        if "period" in fields:
            instance = metadata.setdefault("plant_instance", {})
            if card["period"]["precision"] == "day":
                instance["planting_date"] = card["period"]["value"]
            else:
                instance.pop("planting_date", None)
        if metadata != item.metadata or name != item.name:
            self.commands.execute(GardenEditCommand(self.project, self._memory(), item, metadata, name))
        return {"item_id": item_id, "card": card, "persisted": False}

    def add_event(self, fields: dict[str, Any], request_key: str) -> dict[str, Any]:
        """Add once per durable key; replay after deletion never recreates an event."""
        if not isinstance(request_key, str) or not request_key.strip():
            raise ValueError("An idempotence request_key is required")
        if "id" in fields or "created_at" in fields:
            raise ValueError("Creation id and timestamp are assigned by the application")
        event = validate_event(fields)
        content = {k: v for k, v in event.items() if k not in {"id", "created_at"}}
        content["targets"] = sorted(t["id"] for t in event["targets"])
        fingerprint = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        memory = self._memory()
        receipt = memory["requests"].get(request_key)
        if receipt:
            if receipt["fingerprint"] != fingerprint:
                raise ValueError("Idempotence conflict: request_key was used with different content")
            identity = receipt["event_id"]
            return {"event_id": identity, "replayed": True,
                    "deleted": identity not in memory["events"], "persisted": False}
        self._check_media(event["media"])
        # Resolve targets only after receipt lookup: retries survive target deletion.
        for target in event["targets"]:
            item = self._item(target["id"])
            self._editable(item)
            target["name"] = item.name or target["name"]
        memory["events"][event["id"]] = event
        memory["requests"][request_key] = {"fingerprint": fingerprint, "event_id": event["id"]}
        self.commands.execute(GardenEditCommand(self.project, memory))
        return {"event_id": event["id"], "replayed": False, "persisted": False}

    def edit_event(self, event_id: str, fields: dict[str, Any] | None) -> dict[str, Any]:
        """Edit or delete; historical targets may be absent, with readable snapshots."""
        memory = self._memory()
        old = memory["events"].get(event_id)
        if old is None:
            raise ValueError(f"Unknown intervention: {event_id}")
        for target in old["targets"]:
            try:
                item = self._item(target["id"])
            except ValueError:
                continue  # Historical targets may have been removed.
            self._editable(item)
        if fields is None:
            del memory["events"][event_id]
        else:
            if "id" in fields or "created_at" in fields:
                raise ValueError("Intervention identity and creation time cannot change")
            candidate = validate_event({**old, **fields})
            self._check_media(candidate["media"], old["media"])
            old_ids = {t["id"] for t in old["targets"]}
            for target in candidate["targets"]:
                if target["id"] not in old_ids:
                    self._editable(self._item(target["id"]))
            memory["events"][event_id] = candidate
        self.commands.execute(GardenEditCommand(self.project, memory))
        return {"event_id": event_id, "deleted": fields is None, "persisted": False}
