"""Read-only document calculations; results never become recorded work."""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import asdict
from typing import Any

from open_garden_planner.models.soil_test import SoilTestHistory
from open_garden_planner.services.soil_assessment import (
    ALL_PARAMS,
    calculate_amendments,
    health_level,
)


def assess_document_soil(document: dict[str, Any], target_id: str = "global", *,
                         area_m2: float = 0) -> dict[str, Any]:
    """Use local measurements or the global fallback and the shared calculator.

    Area is explicit in square metres. A missing area yields no dosage rather
    than silently deriving it from a bounding box or inventing an intervention.
    """
    from open_garden_planner.services.document_operations import _bed_ids

    if isinstance(area_m2, bool) or not isinstance(area_m2, (int, float)) or not math.isfinite(area_m2) or area_m2 < 0:
        raise ValueError("area_m2 must be a finite nonnegative number")
    if target_id != "global" and target_id not in _bed_ids(document):
        raise ValueError("Soil assessment requires an existing soil-capable bed")
    histories = document.get("soil_tests", {})
    raw = histories.get(target_id, {"target_id": target_id, "records": []})
    record = SoilTestHistory.from_dict(raw).latest
    source = target_id
    if record is None and target_id != "global":
        record = SoilTestHistory.from_dict(histories.get("global", {"target_id": "global", "records": []})).latest
        source = "global"
    enabled = document.get("enabled_amendments")
    recommendations = calculate_amendments(record, bed_area_m2=area_m2,
                                           enabled_ids=set(enabled) if enabled is not None else None,
                                           prefer_organic=document.get("prefer_organic", True))
    return {"kind": "recommendation", "target_id": target_id, "measurement_source": source if record else None,
            "record": deepcopy(record.to_dict()) if record else None,
            "health": {parameter: health_level(record, parameter).value for parameter in ALL_PARAMS},
            "area_m2": area_m2, "recommendations": [asdict(value) for value in recommendations],
            "warnings": ["An explicit area_m2 is required for dosage"] if not area_m2 else [],
            "recorded_intervention": False, "persisted": False}
