"""Qt-free perennial garden tracking contracts for the personal fork.

Partial periods retain their precision and original label. Interval boundaries
are conventional northern-hemisphere calendar boundaries, used only for search
and sorting. They are never written into the legacy exact planting_date field.
"""

from __future__ import annotations

import calendar
import math
import re
from copy import deepcopy
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

EVENT_TYPES = (
    "planting", "relocation", "amendment", "fertilisation", "mulching", "sowing",
    "pruning", "watering", "observation", "removal_loss",
)
PLANT_STATES = ("planned", "in_place", "dormant", "uncertain", "confirmed_dead", "removed_moved")
KINDS = ("individual", "group", "zone")
SEASONS = {"spring": 3, "summer": 6, "autumn": 9, "winter": 12}
_SEASON_ALIASES = {
    "printemps": "spring", "été": "summer", "ete": "summer",
    "automne": "autumn", "hiver": "winter",
    "frühling": "spring", "fruehling": "spring", "sommer": "summer",
    "herbst": "autumn", "fall": "autumn",
}
_SEASON_ALIASES.update({key: key for key in SEASONS})


def period(raw: Any = None) -> dict[str, str]:
    """Validate a partial date; unknown remains unknown. Never infer today's date."""
    label: Any
    if raw is None or raw == "":
        return {"precision": "unknown", "value": "", "label": ""}
    if isinstance(raw, str):
        label = raw.strip()
        if not label:
            return {"precision": "unknown", "value": "", "label": ""}
        value = label
        season = re.fullmatch(r"(\w+)\s+(\d{4})", label.lower())
        if season and season[1] in _SEASON_ALIASES:
            value = f"{season[2]}-{_SEASON_ALIASES[season[1]]}"
        precision = "season" if value.endswith(tuple(SEASONS)) else {
            4: "year", 7: "month", 10: "day",
        }.get(len(value), "invalid")
        raw = {"precision": precision, "value": value, "label": label}
    if not isinstance(raw, dict) or set(raw) - {"precision", "value", "label"}:
        raise ValueError("Invalid period object")
    precision, value = raw.get("precision", "unknown"), raw.get("value", "")
    label = raw.get("label", value)
    if not all(isinstance(v, str) for v in (precision, value, label)):
        raise ValueError("Period values must be text")
    if precision == "unknown":
        if value:
            raise ValueError("An unknown date cannot have a calendar value")
    elif precision == "day":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Exact dates require YYYY-MM-DD")
        date.fromisoformat(value)
    elif precision == "month":
        if not re.fullmatch(r"\d{4}-\d{2}", value):
            raise ValueError("Months require YYYY-MM")
        date.fromisoformat(value + "-01")
    elif precision == "year":
        if not re.fullmatch(r"\d{4}", value) or not 1 <= int(value) <= 9998:
            raise ValueError("Years require YYYY in 0001..9998")
    elif precision == "season":
        if not re.fullmatch(r"\d{4}-(spring|summer|autumn|winter)", value):
            raise ValueError("Seasons require YYYY-spring/summer/autumn/winter")
        if not 1 <= int(value[:4]) <= 9998:
            raise ValueError("Season year outside 0001..9998")
    else:
        raise ValueError("Unknown date precision")
    return {"precision": precision, "value": value, "label": label}


def period_bounds(raw: Any) -> tuple[str, str] | None:
    """Inclusive conventional search bounds, never a claim of exact occurrence."""
    p = period(raw)
    value, precision = p["value"], p["precision"]
    if precision == "unknown":
        return None
    if precision == "day":
        return value, value
    year = int(value[:4])
    if precision == "year":
        return f"{year:04d}-01-01", f"{year:04d}-12-31"
    if precision == "month":
        month = int(value[5:])
        return value + "-01", f"{value}-{calendar.monthrange(year, month)[1]:02d}"
    month = SEASONS[value[5:]]
    end_year, end_month = (year + 1, 2) if month == 12 else (year, month + 2)
    return date(year, month, 1).isoformat(), date(
        end_year, end_month, calendar.monthrange(end_year, end_month)[1]
    ).isoformat()


def optional_number(value: Any, name: str) -> float | None:
    """Missing is distinct from zero. Reject booleans, negatives and non-finites."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite nonnegative number")
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        if "," in value and "." not in value:
            value = value.replace(",", ".")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be a finite nonnegative number")
    return result


def validate_card(raw: Any) -> dict[str, Any]:
    """Business fields on an existing shape; geometry remains owned by OGP."""
    if not isinstance(raw, dict):
        raise ValueError("Tracking card must be an object")
    allowed = {"kind", "state", "period", "quantity", "zone_id", "notes", "botanical_name",
               "cultivar", "provenance", "depth_cm", "media"}
    if set(raw) - allowed:
        raise ValueError(f"Unknown card fields: {sorted(set(raw) - allowed)}")
    out = deepcopy(raw)
    if out.get("kind", "individual") not in KINDS:
        raise ValueError("Unknown tracking kind")
    if out.get("state", "uncertain") not in PLANT_STATES:
        raise ValueError("Unknown plant state")
    out["period"] = period(out.get("period"))
    for key in ("quantity", "depth_cm"):
        if key in out:
            out[key] = optional_number(out[key], key)
    for key in ("zone_id", "notes", "botanical_name", "cultivar", "provenance"):
        if key in out and not isinstance(out[key], str):
            raise ValueError(f"{key} must be text")
    if "media" in out:
        out["media"] = validate_media(out["media"])
    return out


def validate_media(raw: Any) -> list[str]:
    """Portable project-relative media references, no parent traversal."""
    if not isinstance(raw, list) or any(
        not isinstance(p, str) or not p or p.startswith(("/", "\\"))
        or ".." in p.replace("\\", "/").split("/") or ":" in p for p in raw
    ):
        raise ValueError("Media paths must be relative to the project directory")
    return list(raw)


def validate_event(raw: Any) -> dict[str, Any]:
    """Validate an intervention, with stable historical target names and identity."""
    if not isinstance(raw, dict):
        raise ValueError("Intervention must be an object")
    allowed = {"id", "created_at", "period", "type", "status", "description", "targets",
               "material", "quantity", "unit", "application", "area_m2", "depth_cm", "media"}
    if set(raw) - allowed:
        raise ValueError(f"Unknown intervention fields: {sorted(set(raw) - allowed)}")
    out = deepcopy(raw)
    out.setdefault("id", str(uuid4()))
    out.setdefault("created_at", datetime.now(UTC).isoformat())
    if not isinstance(out["id"], str) or not out["id"]:
        raise ValueError("Intervention id is required")
    timestamp = datetime.fromisoformat(out["created_at"])
    if timestamp.tzinfo is None:
        raise ValueError("Creation timestamp requires a time zone")
    if out.get("type") not in EVENT_TYPES or out.get("status") not in ("planned", "done"):
        raise ValueError("Unknown intervention type or status")
    if not isinstance(out.get("description"), str) or not out["description"].strip():
        raise ValueError("An intervention description is required")
    out["period"] = period(out.get("period"))
    targets = out.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError("At least one target is required")
    ids = set()
    for target in targets:
        if (not isinstance(target, dict) or set(target) != {"id", "name"}
                or not all(isinstance(v, str) and v for v in target.values())
                or target["id"] in ids):
            raise ValueError("Targets require distinct ids and readable historical names")
        ids.add(target["id"])
    for key in ("quantity", "area_m2", "depth_cm"):
        if key in out:
            out[key] = optional_number(out[key], key)
    for key in ("material", "unit", "application"):
        if key in out and not isinstance(out[key], str):
            raise ValueError(f"{key} must be text")
    out["media"] = validate_media(out.get("media", []))
    return out


def validate_memory(raw: Any) -> dict[str, Any]:
    """Validate the versioned, single project-level intervention store."""
    if raw == {}:
        return {}
    if not isinstance(raw, dict) or set(raw) - {"version", "events", "requests"}:
        raise ValueError("Invalid garden memory")
    if type(raw.get("version")) is not int or raw["version"] != 1:
        raise ValueError("Unsupported garden memory version")
    events, requests = raw.get("events", {}), raw.get("requests", {})
    if not isinstance(events, dict) or not isinstance(requests, dict):
        raise ValueError("Events and requests must be objects")
    for key, value in events.items():
        validated = validate_event(value)
        if key != validated["id"] or value != validated:
            raise ValueError("Stored interventions must be complete and keyed by their own id")
    for key, value in requests.items():
        if (not isinstance(key, str) or not key or not isinstance(value, dict)
                or set(value) != {"fingerprint", "event_id"}
                or not all(isinstance(v, str) and v for v in value.values())):
            raise ValueError("Invalid idempotence receipt")
    return deepcopy(raw)
