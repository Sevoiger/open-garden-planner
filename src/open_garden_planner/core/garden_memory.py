"""Shared undoable garden writes for the GUI and main-thread MCP providers."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any
from uuid import UUID

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QGraphicsScene

from open_garden_planner.core.commands import Command, CommandManager
from open_garden_planner.models.garden_memory import validate_memory
from open_garden_planner.services.garden_operations import prepare_garden_operation

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
        return item

    def _check_media_reference(self, reference: str) -> None:
        project = self.project.current_file
        if project is None:
            raise ValueError("Save the project before attaching media")
        path = (project.parent / reference).resolve()
        path.relative_to(project.parent.resolve())
        if not path.is_file():
            raise ValueError(f"Missing project attachment: {reference}")

    def _memory(self) -> dict[str, Any]:
        return self.project.garden_memory or {"version": 1, "events": {}, "requests": {}}

    @staticmethod
    def _snapshot_object(document: dict[str, Any], item_id: str) -> dict[str, Any]:
        """Find one item in the exact candidate produced by the pure planner."""
        matches: list[dict[str, Any]] = []
        wanted = str(UUID(item_id))

        def walk(records: list[dict[str, Any]]) -> None:
            for record in records:
                raw_identity = record.get("item_id", record.get("id"))
                if raw_identity is not None:
                    try:
                        if str(UUID(raw_identity)) == wanted:
                            matches.append(record)
                    except (TypeError, ValueError):
                        pass
                children = record.get("children", [])
                if isinstance(children, list):
                    walk(children)

        walk(document.get("objects", []))
        if len(matches) != 1:
            raise ValueError(f"Garden target id is ambiguous or absent: {item_id}")
        return matches[0]

    def _prepare(self, operation: str, fields: dict[str, Any] | None,
                 target_id: str | None = None, request_key: str | None = None
                 ) -> tuple[dict[str, Any], dict[str, Any]]:
        document = self.project.snapshot_dict(self.scene)
        return prepare_garden_operation(
            document, operation, fields, target_id, request_key,
            media_check=self._check_media_reference,
        )

    def _apply_card(self, operation: str, item_id: str,
                    fields: dict[str, Any]) -> dict[str, Any]:
        candidate, result = self._prepare(operation, fields, item_id)
        target = self._item(item_id)
        record = self._snapshot_object(candidate, item_id)
        metadata = deepcopy(record.get("metadata", {}))
        name = record.get("name", "")
        if metadata != target.metadata or name != target.name:
            self.commands.execute(GardenEditCommand(
                self.project,
                candidate.get("garden_memory", self._memory()),
                target,
                metadata,
                name,
            ))
        return result

    def plant_measurement(self, operation: str, item_id: str, fields: dict[str, Any],
                          request_key: str | None = None) -> dict[str, Any]:
        candidate, result = self._prepare(operation, fields, item_id, request_key)
        if not result.get("replayed"):
            target = self._item(item_id)
            metadata = self._snapshot_object(candidate, item_id)["metadata"]
            if metadata != target.metadata:
                self.commands.execute(GardenEditCommand(
                    self.project, candidate["garden_memory"], target, metadata))
        return result

    def set_planting_date(self, item_id: str, value: str | None) -> dict[str, Any]:
        """Patch only a plant's date, preserving the other tracking fields."""
        return self._apply_card("set_planting_date", item_id, {"planting_date": value})

    def set_card(self, item_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Replace tracking fields, retaining the shape's existing identity/geometry."""
        return self._apply_card("set_card", item_id, fields)

    def add_event(self, fields: dict[str, Any], request_key: str) -> dict[str, Any]:
        """Add once per durable key; replay after deletion never recreates an event."""
        candidate, result = self._prepare("add_event", fields, request_key=request_key)
        if not result["replayed"]:
            self.commands.execute(GardenEditCommand(
                self.project, candidate["garden_memory"]
            ))
        return result

    def edit_event(self, event_id: str, fields: dict[str, Any] | None) -> dict[str, Any]:
        """Edit or delete; historical targets may be absent, with readable snapshots."""
        candidate, result = self._prepare("edit_event", fields, target_id=event_id)
        self.commands.execute(GardenEditCommand(
            self.project, candidate["garden_memory"]
        ))
        return result
