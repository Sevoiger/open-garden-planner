"""Dated, per-plant measurements; pure validation and current-size projection."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from math import isfinite
from typing import Any
from uuid import uuid4

DIMENSIONS = {"height_cm": "current_height_cm", "spread_cm": "current_spread_cm"}


def validate_measurement(fields: dict[str, Any]) -> dict[str, Any]:
    if set(fields) - {"date", "height_cm", "spread_cm", "note"}:
        raise ValueError("Unknown measurement field")
    day = fields.get("date")
    if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
        raise ValueError("Measurement date requires YYYY-MM-DD")
    result: dict[str, Any] = {"date": day, "note": fields.get("note", "")}
    if not isinstance(result["note"], str):
        raise ValueError("Measurement note must be text")
    for key in DIMENSIONS:
        value = fields.get(key)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0:
                raise ValueError("Measured dimensions must be finite positive centimetres")
            result[key] = float(value)
    if not any(key in result for key in DIMENSIONS):
        raise ValueError("At least one measured dimension is required")
    return result


def stored_measurements(instance: dict[str, Any], *, strict: bool = False) -> list[dict[str, Any]]:
    """Tolerant render reads; writes refuse malformed history without replacing it."""
    records = instance.get("size_measurements", [])
    normalized = []
    try:
        if not isinstance(records, list):
            raise ValueError("Measurement history must be a list")
        ids = set()
        days: set[tuple[str, str]] = set()
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not record["id"]:
                raise ValueError("Measurement identity is missing")
            known = validate_measurement({k: record[k] for k in ("date", "height_cm", "spread_cm", "note") if k in record})
            record = {**{k: v for k, v in record.items() if k not in DIMENSIONS}, **known}
            normalized.append(record)
            if record["id"] in ids:
                raise ValueError("Duplicate measurement identity")
            ids.add(record["id"])
            for key in DIMENSIONS:
                if key in record:
                    if (record["date"], key) in days:
                        raise ValueError("Duplicate measured dimension on one day")
                    days.add((record["date"], key))
    except (ValueError, TypeError, OverflowError) as exc:
        if strict:
            raise ValueError("Stored plant measurements are malformed; repair them before editing") from exc
        return []
    return deepcopy(normalized)


def measurement_view(metadata: dict[str, Any]) -> dict[str, Any]:
    instance = metadata.get("plant_instance", {})
    instance = instance if isinstance(instance, dict) else {}
    records = stored_measurements(instance)
    records.sort(key=lambda r: (r["date"], r["id"]))
    current = {}
    for key, current_key in DIMENSIONS.items():
        matching = [r for r in records if key in r]
        if matching:
            last = matching[-1]
            current[key] = {"value": last[key], "date": last["date"], "measurement_id": last["id"]}
        else:
            value = instance.get(current_key)
            if value is None and key == "spread_cm":
                value = instance.get("current_diameter_cm")
            current[key] = {"value": value, "date": None, "measurement_id": None}
    baseline = instance.get("size_measurement_baseline", {})
    return {"measurements": records, "current_size": current,
            "undated_size": deepcopy(baseline) if isinstance(baseline, dict) else {}}


def update_measurements(metadata: dict[str, Any], fields: dict[str, Any] | None,
                        measurement_id: str | None = None) -> tuple[dict[str, Any], str]:
    updated = deepcopy(metadata)
    instance = updated.setdefault("plant_instance", {})
    if not isinstance(instance, dict) or not isinstance(instance.get("size_measurement_baseline", {}), dict):
        raise ValueError("Stored plant instance is malformed")
    records = stored_measurements(instance, strict=True)
    if "size_measurement_baseline" not in instance or (
        not instance["size_measurement_baseline"] and not instance.get("size_measurements")
    ):
        instance["size_measurement_baseline"] = {
            key: deepcopy(instance[key]) for key in
            ("current_height_cm", "current_spread_cm", "current_diameter_cm") if key in instance}
    instance["size_measurements"] = records
    if measurement_id is None:
        if fields is None:
            raise ValueError("Measurement fields are required")
        record = {**validate_measurement(fields), "id": str(uuid4())}
        # Two observations of the same dimension on one day are ambiguous.
        measurement_id = record["id"]
    else:
        old = next((r for r in records if r["id"] == measurement_id), None)
        if old is None:
            raise ValueError("Unknown plant measurement")
        record = {**{k: v for k, v in old.items() if k not in {"date", "height_cm", "spread_cm", "note"}},
                  **validate_measurement(fields), "id": measurement_id} if fields is not None else None
        records.remove(old)
    if record is not None:
        for other in records:
            if other["date"] == record["date"] and any(k in other and k in record for k in DIMENSIONS):
                raise ValueError("A measurement already exists for this date and dimension; edit it")
        records.append(record)
    records.sort(key=lambda r: (r["date"], r["id"]))
    for key, current_key in DIMENSIONS.items():
        matching = [r for r in records if key in r]
        if matching:
            instance[current_key] = matching[-1][key]
        elif current_key in instance["size_measurement_baseline"]:
            instance[current_key] = instance["size_measurement_baseline"][current_key]
        else:
            instance.pop(current_key, None)
    return updated, measurement_id
