"""Qt-free tracking projections from the canonical .ogp snapshot."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any

from open_garden_planner.models.garden_memory import period, period_bounds

BED_TYPES = {"GARDEN_BED", "RAISED_BED", "CONTAINER", "CONTAINER_ROUND", "WALL_PLANTER"}


def garden_objects(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Include nested shapes; exclude positional pins, backgrounds and text."""
    result = []

    def walk(records: list[dict[str, Any]]) -> None:
        for record in records:
            if record.get("item_id") and record.get("object_type"):
                result.append(record)
            walk(record.get("children", []))

    walk(snapshot.get("objects", []))
    return result


def card_for(snapshot: dict[str, Any], item_id: str) -> dict[str, Any]:
    obj = next((o for o in garden_objects(snapshot) if o["item_id"] == item_id), None)
    if obj is None:
        raise ValueError(f"Unknown garden object: {item_id}")
    return _card_for_object(obj)


def _card_for_object(obj: dict[str, Any]) -> dict[str, Any]:
    """Project one resolved object without traversing the plan again."""
    metadata = obj.get("metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    card: dict[str, Any] = deepcopy(metadata.get("garden_tracking", {}))
    instance = metadata.get("plant_instance", {})
    instance = instance if isinstance(instance, dict) else {}
    card.setdefault("kind", "zone" if obj.get("object_type") in BED_TYPES else "individual")
    card.setdefault("state", "uncertain")
    card.setdefault("period", period(instance.get("planting_date")))
    card["name"] = obj.get("name", "")
    card["notes"] = instance.get("notes", "")
    card["cultivar"] = instance.get("variety_cultivar", "")
    card["item_id"] = obj["item_id"]
    return card


def garden_cards(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Project every card in one traversal, including nested objects."""
    return [_card_for_object(obj) for obj in garden_objects(snapshot)]


def search_events(snapshot: dict[str, Any], target_id: str | None = None,
                  event_type: str | None = None, since: str | None = None,
                  until: str | None = None, text: str = "") -> list[dict[str, Any]]:
    """Filter occurrence periods by overlap; unknown periods match only unbounded searches."""
    if since:
        date.fromisoformat(since)
    if until:
        date.fromisoformat(until)
    if since and until and since > until:
        raise ValueError("Date range is reversed")
    result = []
    for event in snapshot.get("garden_memory", {}).get("events", {}).values():
        if target_id and target_id not in {t["id"] for t in event["targets"]}:
            continue
        if event_type and event["type"] != event_type:
            continue
        bounds = period_bounds(event["period"])
        if (since or until) and (bounds is None or since and bounds[1] < since
                                or until and bounds[0] > until):
            continue
        haystack = " ".join([event["description"], event.get("material", ""),
                             *(t["name"] for t in event["targets"])])
        if text.casefold() not in haystack.casefold():
            continue
        result.append(deepcopy(event))
    return sorted(result, key=lambda e: ((period_bounds(e["period"]) or ("9999", "9999"))[0], e["id"]))


def garden_record(snapshot: dict[str, Any], item_id: str) -> dict[str, Any]:
    """Direct and zone events are disjoint views of one source of truth."""
    objects = garden_objects(snapshot)
    obj = next((obj for obj in objects if obj["item_id"] == item_id), None)
    if obj is None:
        raise ValueError(f"Unknown garden object: {item_id}")
    card = _card_for_object(obj)
    direct = search_events(snapshot, item_id)
    own_ids = {event["id"] for event in direct}
    zone_events = search_events(snapshot, card["zone_id"]) if card.get("zone_id") else []
    children = [_card_for_object(o) for o in objects
                if o.get("metadata", {}).get("garden_tracking", {}).get("zone_id") == item_id]
    return {"card": card, "direct_events": direct,
            "zone_events": [e for e in zone_events if e["id"] not in own_ids], "plantings": children}
