"""Pure garden-card and intervention operations over a detached .ogp snapshot.

These operations own the validation and preparation rules shared by the Qt
adapter and document-operation hosts. They do not import Qt, inspect a scene,
read files, or mutate the input snapshot. Media existence checks are an
optional callback supplied by the host for newly referenced paths only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from copy import deepcopy
from typing import Any
from uuid import UUID

from open_garden_planner.models.garden_memory import (
    period,
    validate_card,
    validate_event,
    validate_memory,
)

_PLANT_TYPES = frozenset({"TREE", "SHRUB", "PERENNIAL"})
_BED_TYPES = frozenset({
    "GARDEN_BED", "RAISED_BED", "CONTAINER", "CONTAINER_ROUND", "WALL_PLANTER",
})
_JOURNAL_PIN_TYPE = "GARDEN_JOURNAL_PIN"
_DEFAULT_MEMORY = {"version": 1, "events": {}, "requests": {}}


class _MissingTarget(ValueError):
    """An absent historical target, which intervention edits may retain."""


def _records(document: dict[str, Any]) -> dict[str, list[tuple[dict[str, Any], bool]]]:
    """Index object records recursively while retaining duplicate identities."""
    objects = document.get("objects", [])
    if not isinstance(objects, list):
        raise ValueError("Garden document objects must be a list")
    indexed: dict[str, list[tuple[dict[str, Any], bool]]] = {}

    def walk(records: list[Any], *, top_level: bool) -> None:
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("Garden document contains a malformed object")
            raw_identity = record.get("item_id", record.get("id"))
            if raw_identity is not None:
                if not isinstance(raw_identity, str) or not raw_identity:
                    raise ValueError("Garden object ids must be nonempty strings")
                try:
                    identity = str(UUID(raw_identity))
                except (ValueError, AttributeError) as exc:
                    raise ValueError(f"Invalid garden object id: {raw_identity}") from exc
                indexed.setdefault(identity, []).append((record, top_level))
            children = record.get("children", [])
            if not isinstance(children, list):
                raise ValueError("Garden group children must be a list")
            walk(children, top_level=False)

    walk(objects, top_level=True)
    return indexed


def _resolve(
    indexed: dict[str, list[tuple[dict[str, Any], bool]]], item_id: str,
) -> tuple[dict[str, Any], bool, str]:
    if not isinstance(item_id, str) or not item_id:
        raise ValueError("A garden target id is required")
    try:
        identity = str(UUID(item_id))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Unknown garden target: {item_id}") from exc
    matches = indexed.get(identity, [])
    if not matches:
        raise _MissingTarget(f"Unknown garden target: {item_id}")
    if len(matches) != 1:
        raise ValueError(f"Garden target id is ambiguous: {item_id}")
    record, top_level = matches[0]
    metadata = record.get("metadata", {})
    if not isinstance(metadata, dict) or not isinstance(metadata.get("plant_instance", {}), dict):
        raise ValueError("Target metadata is malformed; repair it before editing")
    if record.get("note_id") is not None or record.get("object_type") == _JOURNAL_PIN_TYPE:
        raise ValueError("A positional journal pin is not a garden target")
    return record, top_level, identity


def _editable(
    record: dict[str, Any], top_level: bool, document: dict[str, Any],
) -> None:
    if not top_level:
        raise ValueError("Edit the top-level object or ungroup it first")
    layer_id = record.get("layer_id")
    layers = document.get("layers", [])
    if layer_id is None or not isinstance(layers, list):
        return
    locked = any(
        isinstance(layer, dict)
        and str(layer.get("id")) == str(layer_id)
        and bool(layer.get("locked", False))
        for layer in layers
    )
    if locked:
        raise ValueError("The target layer is locked")


def _validated_memory(document: dict[str, Any]) -> dict[str, Any]:
    memory = document.get("garden_memory", {})
    validate_memory(memory)
    return deepcopy(memory or _DEFAULT_MEMORY)


def _check_new_media(
    media: list[str], previous: list[str] | None,
    media_check: Callable[[str], None] | None,
) -> None:
    if media_check is None:
        return
    for reference in sorted(set(media) - set(previous or [])):
        media_check(reference)


def _prepare_card(
    document: dict[str, Any], operation: str, fields: dict[str, Any],
    target_id: str | None, media_check: Callable[[str], None] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if target_id is None:
        raise ValueError("A garden target id is required")
    candidate = deepcopy(document)
    indexed = _records(candidate)
    record, top_level, canonical_id = _resolve(indexed, target_id)
    _editable(record, top_level, candidate)

    incoming = deepcopy(fields)
    if operation == "set_planting_date":
        if set(incoming) != {"planting_date"}:
            raise ValueError("set_planting_date requires only planting_date")
        if record.get("object_type") not in _PLANT_TYPES:
            raise ValueError("A planting date requires a plant (TREE, SHRUB or PERENNIAL)")
        metadata = record.get("metadata", {})
        incoming = deepcopy(metadata.get("garden_tracking", {}))
        incoming["period"] = period(fields["planting_date"])
        require_name = False
    else:
        require_name = True

    metadata = deepcopy(record.get("metadata", {}))
    if not isinstance(record.get("object_type"), str) or not record["object_type"]:
        raise ValueError("Garden cards require an existing typed garden shape")
    name = incoming.pop("name", record.get("name", ""))
    if not isinstance(name, str) or require_name and not name.strip():
        raise ValueError("A provisional or botanical label is required")
    card = validate_card(incoming)
    object_type = record.get("object_type")
    if card.get("kind") == "zone" and object_type not in _BED_TYPES:
        raise ValueError("A zone must use an existing bed shape")

    zone_id = card.get("zone_id")
    if zone_id:
        zone, _zone_top_level, zone_identity = _resolve(indexed, zone_id)
        if zone_identity == canonical_id or zone.get("object_type") not in _BED_TYPES:
            raise ValueError("Zone must reference another existing bed")

    old_tracking = record.get("metadata", {}).get("garden_tracking", {})
    previous_media = old_tracking.get("media", []) if isinstance(old_tracking, dict) else []
    _check_new_media(card.get("media", []), previous_media, media_check)

    for key, legacy_key in (("notes", "notes"), ("cultivar", "variety_cultivar")):
        if key in card:
            metadata.setdefault("plant_instance", {})[legacy_key] = card.pop(key)
    metadata["garden_tracking"] = card
    if "period" in incoming:
        instance = metadata.setdefault("plant_instance", {})
        if card["period"]["precision"] == "day":
            instance["planting_date"] = card["period"]["value"]
        else:
            instance.pop("planting_date", None)

    result_card = deepcopy(card)
    old_name = record.get("name", "")
    changed = metadata != record.get("metadata", {}) or name != old_name
    if changed:
        record["metadata"] = metadata
        if name:
            record["name"] = name
        else:
            record.pop("name", None)
        candidate["garden_memory"] = _validated_memory(candidate)
    return candidate, {"item_id": target_id, "card": result_card, "persisted": False}


def _prepare_add_event(
    document: dict[str, Any], fields: dict[str, Any], request_key: str | None,
    media_check: Callable[[str], None] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(request_key, str) or not request_key.strip():
        raise ValueError("An idempotence request_key is required")
    if "id" in fields or "created_at" in fields:
        raise ValueError("Creation id and timestamp are assigned by the application")
    event = validate_event(fields)
    content = {key: value for key, value in event.items() if key not in {"id", "created_at"}}
    content["targets"] = sorted(target["id"] for target in event["targets"])
    fingerprint = hashlib.sha256(
        json.dumps(content, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    memory = _validated_memory(document)
    receipt = memory["requests"].get(request_key)
    if receipt is not None:
        if receipt["fingerprint"] != fingerprint:
            raise ValueError("Idempotence conflict: request_key was used with different content")
        event_id = receipt["event_id"]
        return deepcopy(document), {
            "event_id": event_id,
            "replayed": True,
            "deleted": event_id not in memory["events"],
            "persisted": False,
        }

    _check_new_media(event["media"], None, media_check)
    indexed = _records(document)
    for target in event["targets"]:
        record, top_level, _canonical_id = _resolve(indexed, target["id"])
        _editable(record, top_level, document)
        target["name"] = record.get("name") or target["name"]
    memory["events"][event["id"]] = event
    memory["requests"][request_key] = {"fingerprint": fingerprint, "event_id": event["id"]}
    candidate = deepcopy(document)
    candidate["garden_memory"] = memory
    return candidate, {"event_id": event["id"], "replayed": False, "persisted": False}


def _prepare_edit_event(
    document: dict[str, Any], event_id: str | None, fields: dict[str, Any] | None,
    media_check: Callable[[str], None] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("An intervention id is required")
    memory = _validated_memory(document)
    old = memory["events"].get(event_id)
    if old is None:
        raise ValueError(f"Unknown intervention: {event_id}")
    indexed = _records(document)
    for target in old["targets"]:
        try:
            record, top_level, _canonical_id = _resolve(indexed, target["id"])
        except _MissingTarget:
            continue  # Historical target was deleted; its snapshot remains readable.
        _editable(record, top_level, document)

    if fields is None:
        del memory["events"][event_id]
    else:
        if not isinstance(fields, dict):
            raise ValueError("Intervention fields must be an object or null")
        if "id" in fields or "created_at" in fields:
            raise ValueError("Intervention identity and creation time cannot change")
        candidate_event = validate_event({**old, **deepcopy(fields)})
        _check_new_media(candidate_event["media"], old["media"], media_check)
        old_ids = {target["id"] for target in old["targets"]}
        for target in candidate_event["targets"]:
            if target["id"] in old_ids:
                continue
            record, top_level, _canonical_id = _resolve(indexed, target["id"])
            _editable(record, top_level, document)
        memory["events"][event_id] = candidate_event

    candidate = deepcopy(document)
    candidate["garden_memory"] = memory
    return candidate, {"event_id": event_id, "deleted": fields is None, "persisted": False}


def prepare_garden_operation(
    document: dict[str, Any], operation: str, fields: dict[str, Any] | None,
    target_id: str | None = None, request_key: str | None = None, *,
    media_check: Callable[[str], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return an immutable candidate .ogp snapshot and compatible result shape.

    ``operation`` is one of ``set_card``, ``set_planting_date``, ``add_event``
    or ``edit_event``. Card/event validation follows the existing model
    validators; target and layer checks are performed against the supplied
    recursive object/layer snapshot. ``media_check`` receives only newly added
    portable media references and may refuse them without changing the input.
    """
    if not isinstance(document, dict):
        raise TypeError("Garden document must be an object")
    if not isinstance(operation, str) or operation not in {
        "set_card", "set_planting_date", "add_event", "edit_event",
    }:
        raise ValueError(f"Unsupported garden operation: {operation}")
    if operation == "edit_event":
        return _prepare_edit_event(document, target_id, fields, media_check)
    if not isinstance(fields, dict):
        raise ValueError("Garden operation fields must be an object")
    if operation == "add_event":
        return _prepare_add_event(document, fields, request_key, media_check)
    return _prepare_card(document, operation, fields, target_id, media_check)
