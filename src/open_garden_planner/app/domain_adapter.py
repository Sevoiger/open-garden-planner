"""Qt host for shared document operations; all methods run on the Qt thread."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from PyQt6.QtCore import QCoreApplication

from open_garden_planner.core.plan_io import validate_plan_data
from open_garden_planner.services.document_operations import (
    RECEIPTS_KEY,
    OperationSession,
    document_fingerprint,
)


class LiveDocumentOperations:
    """A port over the existing project/scene, never an authoritative copy."""

    def __init__(self, window: Any) -> None:
        self.window = window
        self.project = window._project_manager
        self.epoch = str(uuid4())
        self.project.project_changed.connect(self._new_epoch)
        self.session = OperationSession(
            self.snapshot, self.write, window.canvas_view.command_manager.execute,
            self.revision,
            description=lambda: QCoreApplication.translate("Commands", "Garden records"),
            media_check=self.check_media,
        )

    def _new_epoch(self, _path: Any) -> None:
        self.epoch = str(uuid4())

    def snapshot(self) -> dict[str, Any]:
        return deepcopy(self.project.snapshot_dict(self.window.canvas_scene))

    def revision(self) -> str:
        return f"{self.epoch}:{getattr(self.project, '_domain_revision', 0)}:{document_fingerprint(self.snapshot())}"

    def dispatch(self, action: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if action in {"read", "media"}:
            from open_garden_planner.agent_api.read_projection import (
                project_document_state,
                read_document_media,
            )

            snapshot = self.snapshot()
            revision = (
                f"{self.epoch}:{getattr(self.project, '_domain_revision', 0)}:"
                f"{document_fingerprint(snapshot)}"
            )
            if action == "read":
                return project_document_state(
                    snapshot,
                    revision=revision,
                    mode="live",
                    **arguments,
                )
            return read_document_media(
                snapshot,
                media_id=arguments["media_id"],
                revision=revision,
                expected_revision=arguments["expected_revision"],
                offset=arguments.get("offset", 0),
                max_bytes=arguments.get("max_bytes", 65536),
            )
        if action == "snapshot":
            return {"revision": self.revision(), "document": self.snapshot(), "mode": "live"}
        if action == "prepare":
            return self.session.prepare(arguments["operations"], arguments.get("expected_revision"))
        if action == "apply":
            return self.session.apply(arguments["preparation_id"], arguments["request_key"])
        if action == "result":
            return self.session.result(arguments["request_key"])
        raise ValueError(f"Unknown document operation: {action}")

    def check_media(self, reference: str) -> None:
        if self.project.current_file is None:
            raise ValueError("Save the project before attaching media")
        root = self.project.current_file.parent.resolve()
        path = (root / reference).resolve()
        path.relative_to(root)
        if not path.is_file():
            raise ValueError(f"Missing project attachment: {reference}")

    def write(self, candidate: dict[str, Any]) -> None:
        """Publish validated store replacements before emitting any refresh signal.

        No Qt objects are constructed or destroyed. The set of supported stores
        is intentionally explicit rather than a generic project mutation API.
        """
        validate_plan_data(candidate)
        pm = self.project
        tasks = deepcopy(candidate.get("manual_tasks", {}))
        receipts = deepcopy(candidate.get(RECEIPTS_KEY, {}))
        changed_tasks = pm.manual_tasks != tasks
        memory = deepcopy(candidate.get("garden_memory", {}))
        changed_memory = pm.garden_memory != memory
        soil = deepcopy(candidate.get("soil_tests", {}))
        changed_soil = pm.soil_tests != soil
        stores = [
            ("harvest_logs", "_harvest_logs", "harvest_logs_changed"),
            ("pest_disease_logs", "_pest_logs", "pest_logs_changed"),
            ("garden_journal_notes", "_garden_journal_notes", "garden_journal_notes_changed"),
            ("succession_plans", "_succession_plans", "succession_plans_changed"),
        ]
        replacements = [(attribute, signal, deepcopy(candidate.get(key, {})))
                        for key, attribute, signal in stores
                        if getattr(pm, attribute) != candidate.get(key, {})]
        states = deepcopy(candidate.get("task_states", {}))
        changed_states = pm.task_states != states
        # Only metadata/name changes are supported here. Resolve and validate
        # all live targets before the first Qt mutation, with no scene rebuild.
        live = {str(item.item_id): item for item in self.window.canvas_scene.items()
                if getattr(item, "item_id", None) is not None}
        old_records = self.snapshot().get("objects", [])
        new_records = candidate.get("objects", [])
        updates = []
        if len(old_records) != len(new_records):
            raise ValueError("Document operations cannot create or delete graphical items")
        for before, after in zip(old_records, new_records, strict=True):
            stable_before = {k: v for k, v in before.items() if k not in {"name", "metadata"}}
            stable_after = {k: v for k, v in after.items() if k not in {"name", "metadata"}}
            if stable_before != stable_after:
                raise ValueError("Document operations cannot change geometry or group membership")
            if before.get("metadata") != after.get("metadata") or before.get("name") != after.get("name"):
                identity = str(after.get("item_id", after.get("id")))
                item = live.get(identity)
                if item is None:
                    raise ValueError("Document target disappeared")
                updates.append((item, deepcopy(before.get("metadata", {})), before.get("name", ""),
                                deepcopy(after.get("metadata", {})), after.get("name", "")))
        try:
            for item, _old_metadata, _old_name, metadata, name in updates:
                item.prepareGeometryChange()
                item.metadata.clear()
                item.metadata.update(metadata)
                item.name = name
        except BaseException:
            for item, old_metadata, old_name, _metadata, _name in updates:
                item.metadata.clear()
                item.metadata.update(old_metadata)
                item.name = old_name
            raise
        # These assignments cannot run user callbacks. Publish the entire batch
        # before observers see it; preparation/validation has already completed.
        pm._manual_tasks = tasks
        pm._garden_memory = memory
        pm._soil_tests = soil
        for attribute, _signal, values in replacements:
            setattr(pm, attribute, values)
        if changed_states:
            pm.restore_task_statuses(states, notify=False)
        pm._extra_fields[RECEIPTS_KEY] = receipts
        pm.mark_dirty()
        if changed_tasks:
            pm.manual_tasks_changed.emit(deepcopy(tasks))
        if changed_memory:
            pm.garden_memory_changed.emit(deepcopy(memory))
        if changed_soil:
            pm.soil_tests_changed.emit(deepcopy(soil))
        for _attribute, signal, values in replacements:
            getattr(pm, signal).emit(deepcopy(values))
        if changed_states:
            pm.task_states_changed.emit(deepcopy(states))
            pm.task_completions_changed.emit(set(pm._task_completions))
        for item, *_rest in updates:
            item.update()
