"""Detached soil sampling points and faithful laboratory-report data.

Points are observations at plan coordinates, never an implicit soil value for
the entire garden or a bed. Laboratory results remain source strings with
their reported units, methods and interpretations; no kit levels are inferred.
"""
from __future__ import annotations

import datetime
import json
import math
from copy import deepcopy
from typing import Any
from uuid import UUID, uuid4

from open_garden_planner.models.soil_test import SoilTestHistory, SoilTestRecord
from open_garden_planner.services.soil_assessment import prepare_soil_test_change

POINT_PREFIX = "sample:"
MAX_REPORT_TEXT = 200_000
MAX_READINGS = 300
_REPORT_KEYS = {"laboratory", "reference", "sample_depth", "source_text", "readings"}
_READING_KEYS = {"parameter", "parameter_code", "value", "unit", "method", "reference", "interpretation"}
STANDARD_PARAMETER_CODES = frozenset({"nitrogen_total", "phosphorus", "potassium", "sand", "silt", "clay",
    "organic_matter", "organic_carbon", "limestone_total", "limestone_active", "cec", "calcium", "magnesium", "sodium"})


def _text(value: Any, label: str, limit: int = 2_000) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{label} must be text of at most {limit} characters")
    return value


def validate_report(report: Any, previous: Any = None) -> dict[str, Any] | None:
    """Validate without parsing inequalities, changing units or inventing facts."""
    if report is None:
        return None
    if not isinstance(report, dict):
        raise ValueError("Laboratory report must be an object")
    previous = previous if isinstance(previous, dict) else {}
    extra = {key: deepcopy(value) for key, value in previous.items() if key not in _REPORT_KEYS}
    for key in set(report) - _REPORT_KEYS - {"removed_reading_indices"}:
        if key not in extra or extra[key] != report[key]:
            raise ValueError("Laboratory report contains unknown fields")
    result: dict[str, Any] = extra
    for key in _REPORT_KEYS - {"readings"}:
        if key in report:
            result[key] = _text(report[key], key, MAX_REPORT_TEXT if key == "source_text" else 2_000)
    rows = report.get("readings", [])
    if not isinstance(rows, list) or len(rows) > MAX_READINGS:
        raise ValueError(f"Laboratory report supports at most {MAX_READINGS} readings")
    old_rows = previous.get("readings", [])
    old_rows = old_rows if isinstance(old_rows, list) else []
    removed = report.get("removed_reading_indices", [])
    if (not isinstance(removed, list) or any(isinstance(i, bool) or not isinstance(i, int)
            or i < 0 or i >= len(old_rows) for i in removed) or len(set(removed)) != len(removed)):
        raise ValueError("Invalid removed reading indices")
    used = set(removed)
    result["readings"] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Laboratory reading must be an object")
        index = row.get("previous_index")
        row_extra = {}
        if index is not None:
            if (isinstance(index, bool) or not isinstance(index, int) or index < 0
                    or index >= len(old_rows) or index in used or not isinstance(old_rows[index], dict)):
                raise ValueError("Invalid previous reading index")
            used.add(index)
            old_code = old_rows[index].get("parameter_code")
            if (isinstance(old_code, str) and old_code in STANDARD_PARAMETER_CODES
                    and row.get("parameter") != old_rows[index].get("parameter")):
                raise ValueError("Cannot rename a standard laboratory parameter")
            row_extra = {key: deepcopy(value) for key, value in old_rows[index].items()
                         if key not in _READING_KEYS or key == "parameter_code"}
        for key in set(row) - _READING_KEYS - {"previous_index"}:
            if key not in row_extra or row_extra[key] != row[key]:
                raise ValueError("Laboratory reading contains unknown fields")
        clean = deepcopy(row_extra)
        for key, value in row.items():
            if key not in _READING_KEYS:
                continue
            if key == "parameter_code":
                if key in row_extra and value != row_extra[key]:
                    raise ValueError("Cannot change an existing parameter code")
                if not isinstance(value, str):
                    if key not in row_extra:
                        raise ValueError("Invalid parameter code")
                    clean[key] = deepcopy(value)
                else:
                    clean[key] = _text(value, key)
            else:
                clean[key] = _text(value, key)
        if not clean.get("parameter", "").strip() or not clean.get("value", "").strip():
            raise ValueError("Each laboratory reading requires a parameter and reported value")
        result["readings"].append(clean)
    for index, old in enumerate(old_rows):
        if index not in used and isinstance(old, dict) and (
                set(old) - _READING_KEYS or
                "parameter_code" in old):
            raise ValueError("Identify or explicitly remove each prior reading with extensions")
    if len(json.dumps(result).encode("utf-8")) > 200_000:
        raise ValueError("Laboratory report exceeds 200,000 bytes")
    return result


def _finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _position(value: Any, document: dict[str, Any]) -> dict[str, float]:
    if not isinstance(value, dict) or set(value) != {"x_cm", "y_cm"}:
        raise ValueError("A point requires x_cm and y_cm in plan coordinates")
    result = {}
    for key in ("x_cm", "y_cm"):
        number = value[key]
        if not _finite_number(number):
            raise ValueError(f"{key} must be a finite number")
        result[key] = float(number)
    canvas = document.get("canvas", {})
    width, height = canvas.get("width", 5000), canvas.get("height", 3000)
    if not (0 <= result["x_cm"] <= width and 0 <= result["y_cm"] <= height):
        raise ValueError("The sampling point must lie inside the plan")
    return result



def positions_from_record(record: dict[str, Any]) -> list[dict[str, float]]:
    """Read recognized positions without consuming opaque legacy extensions."""
    def valid(point: Any) -> bool:
        return (isinstance(point, dict) and set(point) == {"x_cm", "y_cm"}
                and all(_finite_number(point[k]) for k in ("x_cm", "y_cm")))
    points = record.get("sample_positions")
    if isinstance(points, list) and 1 <= len(points) <= 15 and all(valid(point) for point in points):
        return deepcopy(points)
    point = record.get("sample_position")
    return [deepcopy(point)] if valid(point) else []

def prepare_sample(
    document: dict[str, Any], operation: str, target_id: str | None, fields: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Prepare one point observation, preserving other histories and extensions."""
    if operation not in {"record_soil_sample", "edit_soil_sample", "delete_soil_sample"}:
        raise ValueError("Unsupported soil sample operation")
    allowed = {"test_date", "ph", "position", "positions", "report", "notes"}
    if operation == "edit_soil_sample":
        allowed.add("record_id")
    elif operation == "delete_soil_sample":
        allowed = {"record_id"}
    if set(fields) - allowed:
        raise ValueError("Unknown soil sample field")
    if target_id is None and operation == "record_soil_sample":
        target_id = POINT_PREFIX + str(uuid4())
    try:
        if not isinstance(target_id, str) or not target_id.startswith(POINT_PREFIX):
            raise ValueError
        identity = UUID(target_id[len(POINT_PREFIX):])
        if target_id != POINT_PREFIX + str(identity):
            raise ValueError
    except (ValueError, AttributeError) as exc:
        raise ValueError("Soil sample target must be sample:<UUID>") from exc

    candidate = deepcopy(document)
    histories = candidate.setdefault("soil_tests", {})
    if not isinstance(histories, dict):
        raise ValueError("Invalid soil test histories")
    history = SoilTestHistory.from_dict(histories[target_id]) if target_id in histories else None
    record_id = fields.get("record_id")
    record = None
    if operation != "record_soil_sample":
        if not isinstance(record_id, str) or not record_id:
            raise ValueError("record_id is required")
        record = next((r for r in history.records if r.id == record_id), None) if history else None
        if record is None:
            raise ValueError("Soil sample record not found")
    elif record_id is not None:
        raise ValueError("record_id is assigned by the application")

    if operation == "delete_soil_sample":
        change = prepare_soil_test_change(histories, operation="delete", target_id=target_id, record_id=record_id)
    else:
        record = SoilTestRecord.from_dict(record.to_dict()) if record else SoilTestRecord()
        if "test_date" in fields:
            value = fields["test_date"]
            try:
                parsed = datetime.date.fromisoformat(value) if isinstance(value, str) else None
            except ValueError as exc:
                raise ValueError("test_date must be an ISO date") from exc
            if parsed is None or parsed.isoformat() != value or parsed > datetime.date.today():
                raise ValueError("test_date must be a known date not in the future")
            record.date = parsed.isoformat()
        elif not record.date:
            raise ValueError("An explicit test_date is required")
        if "ph" in fields:
            ph = fields["ph"]
            if ph is not None and (not _finite_number(ph) or not 0 <= ph <= 14):
                raise ValueError("pH must be a finite number between 0 and 14")
            record.ph = float(ph) if ph is not None else None
        if "position" in fields and "positions" in fields:
            raise ValueError("Use either position or positions")
        if "positions" in fields:
            if ("sample_positions" in record.extra_fields and
                    record.extra_fields["sample_positions"] != positions_from_record(record.extra_fields)):
                raise ValueError("Cannot replace an unrecognized sampling-position extension")
            positions = fields["positions"]
            if not isinstance(positions, list) or not 1 <= len(positions) <= 15:
                raise ValueError("An analysis requires 1 to 15 sampling positions")
            positions = [_position(pos, candidate) for pos in positions]
            record.extra_fields["sample_positions"] = positions
            record.extra_fields["sample_position"] = deepcopy(positions[0])
        elif "position" in fields:
            point = _position(fields["position"], candidate)
            record.extra_fields["sample_position"] = point
            if "sample_positions" in record.extra_fields:
                raw_positions = record.extra_fields["sample_positions"]
                positions = positions_from_record(record.extra_fields)
                if isinstance(raw_positions, list) and positions == raw_positions:
                    positions[0] = deepcopy(point)
                    record.extra_fields["sample_positions"] = positions
        elif "sample_position" not in record.extra_fields:
            raise ValueError("A sampling position is required")
        if "report" in fields:
            report = validate_report(fields["report"], record.extra_fields.get("laboratory_report"))
            if report is None:
                # Explicit null survives the soil history preparer's extension
                # merge; omission, in contrast, leaves the old report intact.
                record.extra_fields["laboratory_report"] = None
            else:
                record.extra_fields["laboratory_report"] = report
        if "notes" in fields:
            record.notes = _text(fields["notes"], "notes", 20_000)
        report = record.extra_fields.get("laboratory_report") or {}
        if record.ph is None and not report.get("source_text", "").strip() and not report.get("readings"):
            raise ValueError("Enter a pH, laboratory reading or source report")
        if len(positions_from_record(record.extra_fields)) > 1 and not report:
            raise ValueError("Multiple sampling positions require a laboratory analysis")
        record.mode = "lab" if report else "kit"
        change = prepare_soil_test_change(histories, operation="add" if operation == "record_soil_sample" else "edit",
                                          target_id=target_id, record=record)
        record_id = record.id
    if len(json.dumps(change.after).encode("utf-8")) > 240_000:
        raise ValueError("Soil point history exceeds 240,000 bytes")
    histories[target_id] = change.after
    return candidate, {"target_id": target_id, "record_id": record_id,
                       "status": "unchanged" if change.is_noop else "prepared"}
