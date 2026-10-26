"""Pure preparation for harvest, pest and journal record changes.

The planner edits detached ``.ogp`` snapshots only. Qt adapters may supply
media validation callbacks and remain responsible for the graphical journal
pin objects; this module never imports Qt or resolves file paths itself.
"""
from __future__ import annotations

import math
import uuid
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import date
from pathlib import PurePosixPath
from typing import Any

from open_garden_planner.models.harvest_log import HarvestHistory, HarvestRecord
from open_garden_planner.models.journal_note import JournalNote
from open_garden_planner.models.pest_log import PestLogHistory, PestLogRecord

_PLANT_TYPES = frozenset({"TREE", "SHRUB", "PERENNIAL"})
_BED_TYPES = frozenset({
    "GARDEN_BED", "RAISED_BED", "CONTAINER", "CONTAINER_ROUND", "WALL_PLANTER",
})
_HARVEST_FIELDS = frozenset({
    "id", "date", "quantity", "unit", "quality", "notes", "photo_path",
    "journal_note_id",
})
_PEST_FIELDS = frozenset({
    "id", "date", "entry_type", "name", "severity", "treatment", "notes",
    "photo_path", "resolved",
})
_NOTE_FIELDS = frozenset({
    "id", "date", "text", "photo_path", "scene_x", "scene_y", "tags",
})
_POSITION_FIELDS = frozenset({"scene_x", "scene_y", "position", "layer_id"})
_INTERNAL_RECORD_FIELDS = frozenset({
    "record_id", "species_key", "species_name", "journal_note_text",
})


def _uuid(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a UUID")
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{label} must be a UUID") from exc


def _record_id(value: Any, label: str, *, strict_uuid: bool) -> str:
    if strict_uuid:
        return _uuid(value, label)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty string")
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        return value


def _date(value: Any, label: str) -> str:
    """Accept an explicit empty date as unknown; never substitute today's date."""
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO YYYY-MM-DD date or null")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO YYYY-MM-DD date or null") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{label} must be an ISO YYYY-MM-DD date or null")
    return value


def _photo(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a relative path or null")
    if not value:
        return ""
    if "\\" in value:
        raise ValueError(f"{label} must be a relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must stay inside the project directory")
    return value


def _check_media(
    new_reference: str | None,
    old_reference: str | None,
    media_check: Callable[[str], None] | None,
) -> None:
    if media_check is not None and new_reference and new_reference != old_reference:
        media_check(new_reference)


def _objects(document: Mapping[str, Any]) -> dict[str, list[tuple[dict[str, Any], bool]]]:
    records = document.get("objects", [])
    if not isinstance(records, list):
        raise ValueError("Garden document objects must be a list")
    indexed: dict[str, list[tuple[dict[str, Any], bool]]] = {}

    def walk(rows: list[Any], top_level: bool) -> None:
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Garden document contains a malformed object")
            raw_id = row.get("item_id", row.get("id"))
            if raw_id is not None:
                identity = _uuid(raw_id, "Garden object id")
                indexed.setdefault(identity, []).append((row, top_level))
            children = row.get("children", [])
            if not isinstance(children, list):
                raise ValueError("Garden group children must be a list")
            walk(children, False)

    walk(records, True)
    return indexed


def _validate_target(
    document: Mapping[str, Any], target_id: str, *, required: bool,
) -> dict[str, Any] | None:
    indexed = _objects(document)
    matches = indexed.get(target_id, [])
    if not matches:
        if required:
            raise ValueError(f"Unknown harvest/pest target: {target_id}")
        return None
    if len(matches) != 1:
        raise ValueError(f"Garden target id is ambiguous: {target_id}")
    item, top_level = matches[0]
    if not top_level:
        raise ValueError("Edit the top-level object or ungroup it first")
    if item.get("object_type") not in _PLANT_TYPES | _BED_TYPES:
        raise ValueError("Harvest and pest records require a plant or soil bed")
    metadata = item.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("Target metadata is malformed; repair it before editing")
    layer_id = item.get("layer_id")
    layers = document.get("layers", [])
    if not isinstance(layers, list):
        raise ValueError("Garden document layers must be a list")
    if layer_id is not None and any(
        isinstance(layer, dict)
        and str(layer.get("id")) == str(layer_id)
        and bool(layer.get("locked", False))
        for layer in layers
    ):
        raise ValueError("The target layer is locked")
    return item


def _store(document: dict[str, Any], key: str) -> dict[str, Any]:
    value = document.get(key)
    if value is None:
        value = {}
        document[key] = value
    if not isinstance(value, dict) or any(not isinstance(item, dict) for item in value.values()):
        raise ValueError(f"The {key} store is malformed")
    return value


def _existing_record_ids(records: list[Any]) -> list[str]:
    identities: list[str] = []
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("Record history contains a malformed record")
        identities.append(_record_id(row.get("id"), "Stored record id", strict_uuid=False))
    return identities


def _unknown_fields(fields: Mapping[str, Any], known: frozenset[str]) -> dict[str, Any]:
    return {key: deepcopy(value) for key, value in fields.items() if key not in known}


def _record_payload(
    kind: str,
    incoming: Mapping[str, Any],
    *,
    record_id: str,
    before: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate known fields with the persisted model and retain unknown keys."""
    allowed = _HARVEST_FIELDS if kind == "harvest" else _PEST_FIELDS
    if before is None:
        raw = deepcopy(dict(incoming))
    else:
        raw = deepcopy(before)
        for key, value in incoming.items():
            if key == "journal_note_id" and kind == "harvest":
                if value != before.get(key):
                    raise ValueError("The harvest journal link is managed by the planner")
            elif key not in {"record_id", "species_key", "species_name", "journal_note_text"}:
                raw[key] = deepcopy(value)
    raw["id"] = record_id
    raw["date"] = _date(raw.get("date", ""), "date")
    raw["photo_path"] = _photo(raw.get("photo_path"), "photo_path")

    if kind == "harvest":
        quantity_untouched_unknown = (
            before is not None
            and "quantity" not in incoming
            and raw.get("quantity") is None
        )
        if ("quantity" not in raw or raw["quantity"] is None) and not quantity_untouched_unknown:
            raise ValueError("quantity is unknown; provide a numeric value (0 is valid)")
        quantity = raw.get("quantity")
        if not quantity_untouched_unknown:
            if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
                raise ValueError("quantity must be a finite number greater than or equal to 0")
            if not math.isfinite(float(quantity)) or float(quantity) < 0:
                raise ValueError("quantity must be a finite number greater than or equal to 0")
            raw["quantity"] = float(quantity)
        if not isinstance(raw.get("unit", "kg"), str) or not raw.get("unit", "kg"):
            raise ValueError("unit must be a nonempty string")
        for key in ("quality", "notes"):
            if key in raw and not isinstance(raw[key], str):
                raise ValueError(f"{key} must be a string")
        raw.setdefault("unit", "kg")
        model_raw = {key: value for key, value in raw.items()
                     if key not in _INTERNAL_RECORD_FIELDS}
        if quantity_untouched_unknown:
            model_raw["quantity"] = 0.0
        normalized = HarvestRecord.from_dict(model_raw).to_dict()
        if quantity_untouched_unknown:
            if "quantity" not in raw:
                normalized.pop("quantity", None)
            else:
                normalized["quantity"] = deepcopy(raw["quantity"])
    else:
        if raw.get("entry_type", "pest") not in {"pest", "disease"}:
            raise ValueError("entry_type must be 'pest' or 'disease'")
        if raw.get("severity", "low") not in {"low", "medium", "high"}:
            raise ValueError("severity must be 'low', 'medium' or 'high'")
        for key in ("name", "treatment", "notes"):
            if key in raw and not isinstance(raw[key], str):
                raise ValueError(f"{key} must be a string")
        if "resolved" in raw and not isinstance(raw["resolved"], bool):
            raise ValueError("resolved must be a boolean")
        raw.setdefault("entry_type", "pest")
        raw.setdefault("name", "")
        raw.setdefault("severity", "low")
        raw.setdefault("resolved", False)
        model_raw = {key: value for key, value in raw.items()
                     if key not in _INTERNAL_RECORD_FIELDS}
        normalized = PestLogRecord.from_dict(model_raw).to_dict()

    # Models own the known persisted fields; explicit unknown keys are merged
    # back after serialisation so a newer schema survives edits by this client.
    normalized.update(_unknown_fields(raw, allowed | _INTERNAL_RECORD_FIELDS))
    return normalized


def _make_history(
    kind: str, target_id: str, raw: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if raw is None:
        history = (
            HarvestHistory(target_id=target_id).to_dict()
            if kind == "harvest" else PestLogHistory(target_id=target_id).to_dict()
        )
        return history, history["records"]
    history = deepcopy(raw)
    if history.get("target_id", target_id) != target_id:
        raise ValueError("History target_id does not match its storage key")
    history["target_id"] = target_id
    rows = history.get("records", [])
    if not isinstance(rows, list):
        raise ValueError("Record history records must be a list")
    history["records"] = deepcopy(rows)
    return history, history["records"]


def _default_harvest_note_text(record: dict[str, Any], species_name: str) -> str:
    quantity = f"{float(record['quantity']):g}"
    suffix = f" of {species_name}" if species_name else ""
    return f"Harvested {quantity} {record['unit']}{suffix}"


def _referenced_harvest_note(document: Mapping[str, Any], note_id: str) -> bool:
    store = document.get("harvest_logs") or {}
    if not isinstance(store, dict):
        raise ValueError("The harvest_logs store is malformed")
    for raw_history in store.values():
        if not isinstance(raw_history, dict):
            continue
        for record in raw_history.get("records", []):
            if isinstance(record, dict) and record.get("journal_note_id") == note_id:
                return True
    return False


def _apply_history_operation(
    candidate: dict[str, Any], operation: str, fields: dict[str, Any],
    target_id: str, *, media_check: Callable[[str], None] | None,
    note_text: str | None, strict_uuid: bool,
) -> dict[str, Any]:
    is_harvest = operation.endswith("harvest_record")
    kind = "harvest" if is_harvest else "pest"
    store_key = "harvest_logs" if is_harvest else "pest_disease_logs"
    target = _record_id(target_id, "target_id", strict_uuid=strict_uuid)
    store = _store(candidate, store_key)
    raw_history = store.get(target)
    if raw_history is None:
        raw_history = store.get(target_id)
        if raw_history is not None and target != target_id:
            store[target] = store.pop(target_id)
    if operation.startswith("add_") and raw_history is None:
        history, records = _make_history(kind, target, None)
    else:
        if raw_history is None:
            raise ValueError(f"No history exists for target {target!r}")
        history, records = _make_history(kind, target, raw_history)

    identities = _existing_record_ids(records)
    action = operation.split("_", 1)[0]
    record_key = "record_id" if action == "delete" else "id"
    if action == "add":
        incoming = deepcopy(fields)
        incoming.pop("record_id", None)
        supplied_id = incoming.get("id")
        resolved_id = (
            _record_id(supplied_id, "record id", strict_uuid=strict_uuid)
            if supplied_id is not None else str(uuid.uuid4())
        )
        if resolved_id in identities:
            raise ValueError(f"Record id {resolved_id!r} already exists")
        prior_photo = None
        record = _record_payload(kind, incoming, record_id=resolved_id)
        _check_media(record.get("photo_path"), prior_photo, media_check)
        records.append(record)
        if is_harvest:
            for cache_key in ("species_key", "species_name"):
                value = incoming.get(cache_key)
                if value is not None:
                    if not isinstance(value, str):
                        raise ValueError(f"{cache_key} must be a string")
                    if value:
                        history[cache_key] = value
            note_id_value = incoming.get("journal_note_id")
            note_id = (
                _uuid(note_id_value, "journal_note_id")
                if note_id_value is not None else str(uuid.uuid4())
            )
            notes = _store(candidate, "garden_journal_notes")
            if note_id in notes:
                raise ValueError(f"Journal note id {note_id!r} already exists")
            record["journal_note_id"] = note_id
            text = note_text if note_text is not None else _default_harvest_note_text(
                record, str(history.get("species_name", ""))
            )
            if not isinstance(text, str):
                raise ValueError("Harvest journal note text must be a string")
            note = JournalNote(
                id=note_id, date=record["date"], text=text, tags=["harvest"]
            )
            notes[note_id] = note.to_dict()
        history["records"] = records
        store[target] = history
        result = {
            "operation": operation, "target_id": target,
            "record_id": resolved_id, "status": "prepared",
        }
        if is_harvest:
            result["journal_note_id"] = note_id
        return result

    if action not in {"edit", "delete"}:
        raise ValueError(f"Unsupported record operation: {operation!r}")
    requested_id = fields.get(record_key)
    if not isinstance(requested_id, str) or not requested_id:
        raise ValueError(f"{record_key} is required for {operation}")
    resolved_id = _record_id(requested_id, "record id", strict_uuid=strict_uuid)
    matching = [index for index, identity in enumerate(identities) if identity == resolved_id]
    if not matching:
        raise ValueError(f"No record with id {resolved_id!r} exists")
    if len(matching) != 1:
        raise ValueError(f"Record id {resolved_id!r} is ambiguous")
    index = matching[0]
    before_record = deepcopy(records[index])
    if action == "edit":
        incoming = deepcopy(fields)
        incoming.pop("record_id", None)
        incoming["id"] = resolved_id
        before_photo = before_record.get("photo_path")
        before_linked_note = None
        if is_harvest and before_record.get("journal_note_id"):
            old_notes = candidate.get("garden_journal_notes", {})
            if isinstance(old_notes, dict):
                before_linked_note = deepcopy(
                    old_notes.get(before_record["journal_note_id"])
                )
        after_record = _record_payload(
            kind, incoming, record_id=resolved_id, before=before_record
        )
        _check_media(after_record.get("photo_path"), before_photo, media_check)
        records[index] = after_record
        if is_harvest:
            linked_id = before_record.get("journal_note_id")
            notes_value = candidate.get("garden_journal_notes", {})
            if not isinstance(notes_value, dict):
                raise ValueError("The garden_journal_notes store is malformed")
            notes = notes_value
            if linked_id and linked_id in notes:
                raw_note = notes[linked_id]
                if not isinstance(raw_note, dict):
                    raise ValueError("Linked harvest journal note is malformed")
                if "harvest" in raw_note.get("tags", []):
                    changed_note = deepcopy(raw_note)
                    changed_note["date"] = after_record["date"]
                    text = note_text if note_text is not None else _default_harvest_note_text(
                        after_record, str(history.get("species_name", ""))
                    )
                    changed_note["text"] = text
                    notes[linked_id] = changed_note
        history["records"] = records
        store[target] = history
        after_linked_note = None
        if is_harvest and before_record.get("journal_note_id"):
            after_linked_note = candidate.get("garden_journal_notes", {}).get(
                before_record["journal_note_id"]
            )
        status = (
            "unchanged"
            if before_record == after_record and before_linked_note == after_linked_note
            else "prepared"
        )
        return {"operation": operation, "target_id": target, "record_id": resolved_id,
                "status": status}

    linked_id = before_record.get("journal_note_id") if is_harvest else None
    del records[index]
    history["records"] = records
    store[target] = history
    if is_harvest and linked_id:
        notes_value = candidate.get("garden_journal_notes", {})
        if not isinstance(notes_value, dict):
            raise ValueError("The garden_journal_notes store is malformed")
        notes = notes_value
        linked_note = notes.get(linked_id)
        still_referenced = _referenced_harvest_note(candidate, linked_id)
        if (isinstance(linked_note, dict) and "harvest" in linked_note.get("tags", [])
                and not still_referenced):
            notes.pop(linked_id, None)
    if is_harvest and not records:
        known_history_keys = {"target_id", "records", "species_key", "species_name"}
        unknown_history_fields = set(history) - known_history_keys
        if not unknown_history_fields:
            store.pop(target, None)
        else:
            store[target] = history
    else:
        store[target] = history
    return {"operation": operation, "target_id": target, "record_id": resolved_id,
            "status": "prepared"}


def _note_payload(
    incoming: Mapping[str, Any], note_id: str, *, before: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if set(incoming) & _POSITION_FIELDS:
        raise ValueError("Journal pin position is managed by the Qt canvas adapter")
    raw = deepcopy(before or {})
    for key, value in incoming.items():
        if key != "id":
            raw[key] = deepcopy(value)
    raw["id"] = note_id
    raw["date"] = _date(raw.get("date", ""), "date")
    if not isinstance(raw.get("text", ""), str):
        raise ValueError("text must be a string")
    raw.setdefault("text", "")
    raw["photo_path"] = _photo(raw.get("photo_path"), "photo_path")
    raw.setdefault("scene_x", 0.0)
    raw.setdefault("scene_y", 0.0)
    for key in ("scene_x", "scene_y"):
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError(f"{key} must be a finite number")
        raw[key] = float(value)
    tags = raw.get("tags", [])
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError("tags must be a list of strings")
    raw["tags"] = list(tags)
    return JournalNote.from_dict(raw).to_dict() | _unknown_fields(raw, _NOTE_FIELDS)


def _has_graphical_pin(document: Mapping[str, Any], note_id: str) -> bool:
    rows = document.get("objects", [])
    if not isinstance(rows, list):
        raise ValueError("Garden document objects must be a list")

    def walk(items: list[Any]) -> bool:
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Garden document contains a malformed object")
            if item.get("note_id") == note_id:
                return True
            children = item.get("children", [])
            if not isinstance(children, list):
                raise ValueError("Garden group children must be a list")
            if walk(children):
                return True
        return False

    return walk(rows)


def _prepare(
    document: dict[str, Any], operation: str, fields: dict[str, Any],
    target_id: str | None, *, media_check: Callable[[str], None] | None,
    note_text: str | None, validate_target: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(document, dict):
        raise ValueError("Garden document must be an object")
    if not isinstance(fields, dict):
        raise ValueError("fields must be an object")
    candidate = deepcopy(document)
    if operation not in {
        "add_harvest_record", "edit_harvest_record", "delete_harvest_record",
        "add_pest_log", "edit_pest_log", "delete_pest_log",
        "add_journal_note", "edit_journal_note", "delete_journal_note",
    }:
        raise ValueError(f"Unsupported record operation: {operation!r}")

    if operation.endswith("harvest_record") or operation.endswith("pest_log"):
        if target_id is None:
            raise ValueError("target_id is required for harvest and pest operations")
        target = _record_id(target_id, "target_id", strict_uuid=validate_target)
        action = operation.split("_", 1)[0]
        if validate_target:
            _validate_target(candidate, target, required=(action == "add"))
        result = _apply_history_operation(
            candidate, operation, fields, target,
            media_check=media_check, note_text=note_text,
            strict_uuid=validate_target,
        )
        return candidate, result

    action = operation.split("_", 1)[0]
    notes = _store(candidate, "garden_journal_notes")
    if action == "add":
        if target_id is not None and fields.get("id", target_id) != target_id:
            raise ValueError("Journal note id does not match target_id")
        resolved_id = _record_id(
            fields.get("id", target_id or str(uuid.uuid4())),
            "journal note id", strict_uuid=validate_target,
        )
        if resolved_id in notes:
            raise ValueError(f"Journal note id {resolved_id!r} already exists")
        if validate_target and _has_graphical_pin(candidate, resolved_id):
            raise ValueError("The journal note id is already used by a canvas pin")
        note = _note_payload(fields, resolved_id)
        _check_media(note.get("photo_path"), None, media_check)
        notes[resolved_id] = note
        return candidate, {"operation": operation, "target_id": resolved_id,
                           "record_id": resolved_id, "status": "prepared"}

    if target_id is None:
        raise ValueError(f"target_id is required for {operation}")
    resolved_id = _record_id(target_id, "target_id", strict_uuid=validate_target)
    before = notes.get(resolved_id)
    if before is None:
        raise ValueError(f"No journal note with id {resolved_id!r} exists")
    if not isinstance(before, dict):
        raise ValueError("Journal note is malformed")
    if _referenced_harvest_note(candidate, resolved_id):
        raise ValueError("Harvest-linked journal notes are managed by harvest records")
    if action == "edit":
        if fields.get("id", resolved_id) != resolved_id:
            raise ValueError("Journal note id cannot be changed")
        old_photo = before.get("photo_path")
        after = _note_payload(fields, resolved_id, before=before)
        _check_media(after.get("photo_path"), old_photo, media_check)
        notes[resolved_id] = after
        return candidate, {"operation": operation, "target_id": resolved_id,
                           "record_id": resolved_id,
                           "status": "unchanged" if after == before else "prepared"}

    if action == "delete":
        if _has_graphical_pin(candidate, resolved_id):
            raise ValueError("Delete the journal pin through the Qt canvas command")
        notes.pop(resolved_id, None)
        return candidate, {"operation": operation, "target_id": resolved_id,
                           "record_id": resolved_id, "status": "prepared"}
    raise ValueError(f"Unsupported record operation: {operation!r}")


def prepare_record_operation(
    document: dict[str, Any], operation: str, fields: dict[str, Any],
    target_id: str | None = None, *,
    media_check: Callable[[str], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate one record change and return its detached candidate snapshot.

    ``target_id`` is the plant/bed id for harvest and pest operations and the
    note id for journal edit/delete. For journal add, ``fields.id`` is optional
    and may be supplied for a stable prepared plan. Empty dates remain unknown;
    harvest quantity must be present and numeric, so unknown cannot turn into 0.
    Unknown fields on both histories and records are retained.
    """
    return _prepare(
        document, operation, fields, target_id, media_check=media_check,
        note_text=None, validate_target=True,
    )


def prepare_command_record_change(
    project_manager: Any, operation: str, target_id: str,
    fields: dict[str, Any], *, note_text: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the same record transformation for legacy Qt command adapters.

    Existing commands already receive the selected object id from the UI, so
    target resolution remains in that adapter. Journal pin positions likewise
    stay with the canvas command and are copied back only after the pure note
    body has been prepared.
    """
    document = {
        "objects": [],
        "layers": [],
        "harvest_logs": deepcopy(project_manager.harvest_logs),
        "pest_disease_logs": deepcopy(project_manager.pest_logs),
        "garden_journal_notes": deepcopy(project_manager.garden_journal_notes),
    }
    return _prepare(
        document, operation, fields, target_id, media_check=None,
        note_text=note_text, validate_target=False,
    )


__all__ = ["prepare_record_operation"]
