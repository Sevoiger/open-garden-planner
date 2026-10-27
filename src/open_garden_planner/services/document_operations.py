"""Prepared, versioned document operations shared by live and file hosts.

This module owns no Qt object or storage path. Hosts serialize calls and supply
the snapshot, revision and command execution boundary.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from open_garden_planner.core.command_history import Command
from open_garden_planner.services.document_operation_contracts import (
    DOCUMENT_OPERATION_CONTRACTS,
    document_operation_field_names,
)

RECEIPTS_KEY = "operation_receipts"
MAX_OPERATIONS = 100
MAX_REQUEST_BYTES = 1_000_000
SUPPORTED_OPERATIONS = tuple(DOCUMENT_OPERATION_CONTRACTS)


def fingerprint(value: Any) -> str:
    """Hash strict JSON; NaN and implicit object encoders are never accepted."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def document_fingerprint(document: dict[str, Any]) -> str:
    value = deepcopy(document)
    value.pop("agent_meta", None)
    value.pop(RECEIPTS_KEY, None)
    if isinstance(value.get("metadata"), dict):
        value["metadata"].pop("modified", None)
    return fingerprint(value)


def _values_at(document: dict[str, Any], paths: list[list[str | int]]) -> list[dict[str, Any]]:
    values = []
    for path in paths:
        value: Any = document
        exists = True
        for part in path:
            try:
                value = value[part]
            except (KeyError, IndexError, TypeError):
                exists = False
                value = None
                break
        values.append({"exists": exists, "value": value})
    return values


def validate_receipt_store(document: dict[str, Any]) -> dict[str, Any]:
    receipts = document.get(RECEIPTS_KEY, {})
    if not isinstance(receipts, dict) or len(receipts) > 10_000:
        raise ValueError("Malformed or oversized document operation receipts")
    for key, receipt in receipts.items():
        if (not isinstance(key, str) or not isinstance(receipt, dict)
                or not isinstance(receipt.get("fingerprint"), str)
                or not isinstance(receipt.get("results"), list)
                or receipt.get("status") not in {"applied", "unchanged"}):
            raise ValueError("Malformed document operation receipt")
    if len(json.dumps(receipts, ensure_ascii=False, allow_nan=False).encode()) > 10_000_000:
        raise ValueError("Document receipt storage limit reached")
    return receipts


@dataclass(frozen=True)
class Patch:
    """One detached replacement, including the distinction absent/null."""

    path: tuple[str | int, ...]
    before_exists: bool
    before: Any
    after_exists: bool
    after: Any

    def inverse(self) -> Patch:
        return Patch(self.path, self.after_exists, self.after, self.before_exists, self.before)


def changes_between(before: dict[str, Any], after: dict[str, Any],
                    path: tuple[str | int, ...] = ()) -> list[Patch]:
    result: list[Patch] = []
    for key in sorted(before.keys() | after.keys()):
        if key not in before or key not in after:
            result.append(Patch((*path, key), key in before, deepcopy(before.get(key)),
                                key in after, deepcopy(after.get(key))))
        elif isinstance(before[key], dict) and isinstance(after[key], dict):
            result.extend(changes_between(before[key], after[key], (*path, key)))
        elif (isinstance(before[key], list) and isinstance(after[key], list)
              and len(before[key]) == len(after[key])
              and all(isinstance(a, dict) and isinstance(b, dict) and a.get("item_id")
                      and a.get("item_id") == b.get("item_id")
                      for a, b in zip(before[key], after[key], strict=True))):
            # Graphical records can contain large embedded images. A card edit
            # must not retain/copy the whole scene twice in every preparation.
            for index, (a, b) in enumerate(zip(before[key], after[key], strict=True)):
                children = changes_between(a, b, (*path, key, index))
                if children:
                    # Guard the indexed identity as well as the field values:
                    # a later reorder must never redirect undo to another item.
                    result.append(Patch((*path, key, index, "item_id"), True, a["item_id"], True, a["item_id"]))
                    result.extend(children)
        elif before[key] != after[key]:
            result.append(Patch((*path, key), True, deepcopy(before[key]), True, deepcopy(after[key])))
    return result


def apply_patches(document: dict[str, Any], patches: list[Patch]) -> dict[str, Any]:
    """Validate every before-value on a copy, then return the candidate."""
    candidate = deepcopy(document)
    for patch in patches:
        parent: Any = candidate
        for key in patch.path[:-1]:
            if not isinstance(parent, (dict, list)):
                raise ValueError("Operation conflict: a parent record changed")
            try:
                parent = parent[key]
            except (KeyError, IndexError, TypeError) as exc:
                raise ValueError("Operation conflict: a parent record disappeared") from exc
        key = patch.path[-1]
        exists = key in parent if isinstance(parent, dict) else isinstance(key, int) and key < len(parent)
        if exists != patch.before_exists or exists and parent[key] != patch.before:
            raise ValueError("Operation conflict: a value changed since preparation")
        if patch.after_exists:
            parent[key] = deepcopy(patch.after)
        elif isinstance(parent, dict):
            parent.pop(key, None)
        else:
            raise ValueError("List element deletion is not a supported document operation")
    return candidate


def _bed_ids(document: dict[str, Any]) -> set[str]:
    # The snapshot uses stable object type names, independent of Qt's enum class.
    soil_types = {"GARDEN_BED", "RAISED_BED", "CONTAINER", "CONTAINER_ROUND", "WALL_PLANTER"}
    return {str(obj.get("item_id", obj.get("id"))) for obj in document.get("objects", [])
            if obj.get("object_type") in soil_types and obj.get("item_id", obj.get("id"))}


def prepare_operations(document: dict[str, Any], operations: list[dict[str, Any]], *,
                       media_check: Callable[[str], None] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Prepare a bounded homogeneous-storage batch without any external effect."""
    from open_garden_planner.services.task_operations import prepare_manual_task_change

    if not isinstance(operations, list) or not 1 <= len(operations) <= MAX_OPERATIONS:
        raise ValueError(f"A batch requires 1..{MAX_OPERATIONS} operations")
    encoded = json.dumps(operations, allow_nan=False, ensure_ascii=False).encode()
    if len(encoded) > MAX_REQUEST_BYTES:
        raise ValueError("Operation payload is too large")
    candidate = deepcopy(document)
    results = []
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict) or set(operation) - {"operation", "fields", "target_id"}:
            raise ValueError(f"Invalid operation at row {index + 1}")
        name = operation.get("operation")
        if not isinstance(name, str) or name not in SUPPORTED_OPERATIONS:
            raise ValueError(f"Unsupported document operation at row {index + 1}")
        target_id = operation.get("target_id")
        if target_id is not None and (not isinstance(target_id, str) or not target_id.strip()):
            raise ValueError("target_id must be a nonempty string or omitted")
        fields = operation.get("fields", {})
        if not isinstance(fields, dict):
            raise ValueError(f"Invalid fields at row {index + 1}")
        if name in {"add_manual_task", "edit_manual_task", "delete_manual_task"}:
            if set(fields) - document_operation_field_names(name):
                raise ValueError("Unknown manual task field")
            action = str(name).split("_", 1)[0]
            tasks = candidate.setdefault("manual_tasks", {})
            change = prepare_manual_task_change(tasks, operation=action,
                                                task_id=operation.get("target_id"),
                                                valid_bed_ids=_bed_ids(candidate), **fields)
            if change.after is None:
                tasks.pop(change.task_id, None)
            else:
                tasks[change.task_id] = deepcopy(change.after)
            results.append({"row": index + 1, "operation": name, "target_id": change.task_id,
                            "status": "unchanged" if change.is_noop else "prepared"})
        elif name in {"set_garden_card", "set_planting_date", "add_garden_intervention",
                      "edit_garden_intervention", "delete_garden_intervention"}:
            from open_garden_planner.services.garden_operations import prepare_garden_operation

            garden_names = {"set_garden_card": "set_card", "set_planting_date": "set_planting_date",
                            "add_garden_intervention": "add_event", "edit_garden_intervention": "edit_event",
                            "delete_garden_intervention": "edit_event"}
            # The outer operation receipt owns idempotence for this batch. The
            # legacy event key is only a private planner input, never exposed.
            key = str(uuid4()) if name == "add_garden_intervention" else None
            candidate, result = prepare_garden_operation(
                candidate, garden_names[name], None if name == "delete_garden_intervention" else fields,
                operation.get("target_id"), key, media_check=media_check)
            if key is not None:
                candidate["garden_memory"]["requests"].pop(key, None)
            results.append({"row": index + 1, "operation": name, **result})
        elif name == "record_soil_test":
            from open_garden_planner.services.soil_assessment import (
                build_soil_record_for_agent,
                prepare_soil_test_change,
            )

            target = "global" if target_id is None else target_id
            if target != "global" and target not in _bed_ids(candidate):
                raise ValueError("A soil test requires an existing soil-capable bed")
            if set(fields) - document_operation_field_names(name):
                raise ValueError("Unknown soil-test field; kit levels and ppm are separate units")
            if not isinstance(fields.get("test_date"), str) or not fields["test_date"]:
                raise ValueError("An explicit measured test_date is required; an unknown date must not become today")
            record = build_soil_record_for_agent(**fields)
            histories = candidate.setdefault("soil_tests", {})
            change = prepare_soil_test_change(histories, operation="add", target_id=target, record=record)
            histories[target] = change.after
            results.append({"row": index + 1, "operation": name, "target_id": target,
                            "record_id": record.id, "status": "prepared"})
        elif name in {"add_harvest_record", "edit_harvest_record", "delete_harvest_record",
                      "add_pest_log", "edit_pest_log", "delete_pest_log",
                      "add_journal_note", "edit_journal_note", "delete_journal_note"}:
            from open_garden_planner.services.record_operations import prepare_record_operation

            candidate, result = prepare_record_operation(candidate, name, fields,
                                                          operation.get("target_id"), media_check=media_check)
            results.append({"row": index + 1, **result})
        elif name == "set_manual_task_status":
            from open_garden_planner.services.task_status import prepare_task_status

            task_id = operation.get("target_id")
            if task_id not in candidate.get("manual_tasks", {}):
                raise ValueError("Status operations require an existing manual task; generated tasks remain derived")
            if set(fields) - document_operation_field_names(name) or "status" not in fields:
                raise ValueError("Provide status and optional done_date/snooze_until")
            states = candidate.setdefault("task_states", {})
            change = prepare_task_status(states, task_id, **fields)
            if change.after is None:
                states.pop(task_id, None)
            else:
                states[task_id] = change.after
            completions = set(candidate.get("task_completions", []))
            if change.after and change.after.get("status") == "done":
                completions.add(task_id)
            else:
                completions.discard(task_id)
            if completions:
                candidate["task_completions"] = sorted(completions)
            else:
                candidate.pop("task_completions", None)
            if not states:
                candidate.pop("task_states", None)
            results.append({"row": index + 1, "operation": name, "target_id": task_id,
                            "status": "unchanged" if change.is_noop else "prepared"})
        elif name == "set_succession_plan":
            from open_garden_planner.services.succession_operations import (
                build_succession_plan_for_agent,
                prepare_succession_change,
            )

            bed_id = operation.get("target_id")
            if bed_id not in _bed_ids(candidate):
                raise ValueError("Succession requires an existing soil-capable bed")
            if (set(fields) - document_operation_field_names(name) or not isinstance(fields.get("entries"), list)
                    or not isinstance(fields.get("year"), int) or isinstance(fields.get("year"), bool)):
                raise ValueError("Provide entries and an explicit integer year")
            if not 1 <= fields["year"] <= 9998:
                raise ValueError("Succession year outside supported range")
            plan = build_succession_plan_for_agent(fields.get("entries"), bed_id, fields["year"])
            plans = candidate.setdefault("succession_plans", {})
            change = prepare_succession_change(plans, bed_id=bed_id, new_plan=plan, allow_overlap=False)
            if change.after is None:
                plans.pop(bed_id, None)
            else:
                plans[bed_id] = change.after
            if not plans:
                candidate.pop("succession_plans", None)
            results.append({"row": index + 1, "operation": name, "target_id": bed_id,
                            "status": "unchanged" if change.is_noop else "prepared"})
        else:
            raise ValueError(f"Unsupported document operation: {name}")
    # An absent empty store is not a modification.
    if not candidate.get("manual_tasks") and "manual_tasks" not in document:
        candidate.pop("manual_tasks", None)
    return candidate, results


class DocumentChangeCommand(Command):
    """One prepared change/inverse; protocol receipts deliberately survive undo."""

    def __init__(self, read: Callable[[], dict[str, Any]], write: Callable[[dict[str, Any]], None],
                 patches: list[Patch], *, request_key: str, receipt: dict[str, Any],
                 description: str = "Garden records") -> None:
        self._read, self._write, self._patches = read, write, deepcopy(patches)
        self._request_key, self._receipt = request_key, deepcopy(receipt)
        self._description = description

    @property
    def description(self) -> str:
        return self._description

    def _apply(self, patches: list[Patch]) -> None:
        candidate = apply_patches(self._read(), patches)
        candidate.setdefault(RECEIPTS_KEY, {})[self._request_key] = deepcopy(self._receipt)
        validate_receipt_store(candidate)
        self._write(candidate)

    def execute(self) -> None:
        self._apply(self._patches)

    def undo(self) -> None:
        self._apply([p.inverse() for p in reversed(self._patches)])


@dataclass
class _Preparation:
    revision: str
    intention: str
    expires: float
    patches: list[Patch]
    results: list[dict[str, Any]]
    media: list[str]


class OperationSession:
    """Preparation cache belonging to one host epoch, never a second document."""

    def __init__(self, read: Callable[[], dict[str, Any]], write: Callable[[dict[str, Any]], None],
                 execute: Callable[[Command], None], revision: Callable[[], str], *,
                 persisted: bool = False, ttl: float = 300, capacity: int = 128,
                 clock: Callable[[], float] = time.monotonic,
                 description: Callable[[], str] = lambda: "Garden records",
                 media_check: Callable[[str], None] | None = None) -> None:
        self._read, self._write, self._execute, self._revision = read, write, execute, revision
        self._persisted, self._ttl, self._capacity, self._clock = persisted, ttl, capacity, clock
        self._description = description
        self._media_check = media_check
        self._plans: OrderedDict[str, _Preparation] = OrderedDict()

    def _receipts(self) -> dict[str, Any]:
        return validate_receipt_store(self._read())

    def _receipt_result(self, request_key: str, receipt: dict[str, Any]) -> dict[str, Any]:
        effect = "unknown"
        if receipt.get("status") == "unchanged":
            effect = "unchanged"
        elif "paths" in receipt:
            current = fingerprint(_values_at(self._read(), receipt["paths"]))
            effect = ("present" if current == receipt.get("after_fingerprint") else
                      "reverted" if current == receipt.get("before_fingerprint") else "changed")
        return {**deepcopy(receipt), "request_key": request_key, "effect": effect,
                "persisted": self._persisted}

    def prepare(self, operations: list[dict[str, Any]], expected_revision: str | None = None) -> dict[str, Any]:
        revision = self._revision()
        if expected_revision is not None and revision != expected_revision:
            raise ValueError("Operation conflict: document revision changed")
        before = self._read()
        self._receipts()
        media: set[str] = set()

        def check_media(reference: str) -> None:
            media.add(reference)
            if self._media_check is not None:
                self._media_check(reference)

        after, results = prepare_operations(before, operations, media_check=check_media)
        patches = changes_between(before, after)
        preview_bytes = json.dumps([{"path": p.path, "before": p.before, "after": p.after}
                                    for p in patches], allow_nan=False, ensure_ascii=False).encode()
        if len(preview_bytes) > 2_000_000:
            raise ValueError("Prepared changes exceed the 2 MB preview limit")
        token = str(uuid4())
        intention = fingerprint({"contract": 1, "operations": operations})
        self._plans[token] = _Preparation(revision, intention, self._clock() + self._ttl, patches, results, sorted(media))
        while len(self._plans) > self._capacity:
            self._plans.popitem(last=False)
        return {"status": "prepared", "preparation_id": token, "revision": revision,
                "expires_in_seconds": self._ttl, "scope": "project", "results": deepcopy(results),
                "changes": [{"path": list(p.path), "before_exists": p.before_exists,
                             "before": deepcopy(p.before), "after_exists": p.after_exists,
                             "after": deepcopy(p.after)} for p in patches],
                "warnings": [], "side_effects": [], "persisted": False}

    def apply(self, preparation_id: str, request_key: str) -> dict[str, Any]:
        if not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 256:
            raise ValueError("A nonempty request_key of at most 256 characters is required")
        plan = self._plans.get(preparation_id)
        if plan is None:
            raise ValueError("Preparation expired or belongs to another session; inspect request result, then prepare again")
        receipts = self._receipts()
        previous = receipts.get(request_key)
        if previous is not None:
            if previous["fingerprint"] != plan.intention:
                raise ValueError("Idempotence conflict: request_key already used for different content")
            return {**self._receipt_result(request_key, previous), "replayed": True}
        if self._clock() >= plan.expires:
            raise ValueError("Preparation expired; prepare again")
        if self._revision() != plan.revision:
            raise ValueError("Operation conflict: document changed after preparation")
        if len(receipts) >= 10_000:
            raise ValueError("Document operation receipt limit reached; no receipt is silently expired")
        if self._media_check is not None:
            for reference in plan.media:
                self._media_check(reference)
        receipt = {"fingerprint": plan.intention, "results": deepcopy(plan.results),
                   "status": "applied" if plan.patches else "unchanged"}
        if plan.patches:
            paths = [list(p.path) for p in plan.patches]
            before = self._read()
            after = apply_patches(before, plan.patches)
            receipt.update(paths=paths, before_fingerprint=fingerprint(_values_at(before, paths)),
                           after_fingerprint=fingerprint(_values_at(after, paths)))
        if plan.patches:
            self._execute(DocumentChangeCommand(self._read, self._write, plan.patches,
                                                request_key=request_key, receipt=receipt,
                                                description=self._description()))
        else:
            candidate = deepcopy(self._read())
            candidate.setdefault(RECEIPTS_KEY, {})[request_key] = receipt
            validate_receipt_store(candidate)
            self._write(candidate)
        return {**self._receipt_result(request_key, receipt), "replayed": False}

    def result(self, request_key: str) -> dict[str, Any]:
        receipt = self._receipts().get(request_key)
        return ({"request_key": request_key, "status": "unknown", "persisted": False} if receipt is None
                else self._receipt_result(request_key, receipt))
