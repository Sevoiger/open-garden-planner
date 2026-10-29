"""Preserve ensemble members and compute explicitly uncalibrated distributions."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

ENSEMBLE_MODELS = {"ecmwf_ifs025": 15, "gfs05": 15}
ENSEMBLE_VARIABLES = {"temperature_2m": "°C", "precipitation": "mm"}


def quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * q
    lo = int(index)
    hi = min(lo + 1, len(ordered) - 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo), 4)


def normalize_ensemble(data: dict[str, Any], timezone: str) -> dict[str, Any]:
    import math

    hourly, units = data["hourly"], data["hourly_units"]
    times = hourly["time"]
    if (
        not times
        or units.get("time") != "unixtime"
        or any(not isinstance(t, (int, float)) or not math.isfinite(t) for t in times)
    ):
        raise ValueError("Invalid ensemble timestamps")
    if any(b <= a or (b - a) % 3600 for a, b in zip(times, times[1:], strict=False)):
        raise ValueError("Duplicate or unordered ensemble hours")
    members: dict[str, dict[str, list[float | None]]] = {}
    stats: dict[str, Any] = {}
    missing: dict[str, str] = {}
    for variable, unit in ENSEMBLE_VARIABLES.items():
        names = [k for k in hourly if re.fullmatch(re.escape(variable) + r"(?:_member\d+)?", k)]
        members[variable] = {}
        for name in names:
            values = hourly[name]
            if units.get(name) != unit or len(values) != len(times):
                raise ValueError(f"Invalid ensemble unit/length: {name}")
            if any(
                v is not None
                and (
                    not isinstance(v, (int, float))
                    or not math.isfinite(v)
                    or variable == "precipitation"
                    and v < 0
                )
                for v in values
            ):
                raise ValueError(f"Invalid ensemble value: {name}")
            # The un-suffixed series is the control member, not an ensemble mean.
            member = name.removeprefix(variable + "_") if name != variable else "control"
            members[variable][member] = values
        if not names:
            missing[variable] = "No members supplied by provider"
        if any(v is None for name in names for v in hourly[name]):
            missing[variable] = (
                "Null members/hours supplied; coverage may end before requested horizon"
            )
        series = []
        for i in range(len(times)):
            values = [hourly[name][i] for name in names if hourly[name][i] is not None]
            row = {
                "member_count": len(values),
                "expected_member_count": len(names),
                "p10": quantile(values, 0.1) if values else None,
                "median": quantile(values, 0.5) if values else None,
                "p90": quantile(values, 0.9) if values else None,
                "fraction_below_0c"
                if variable == "temperature_2m"
                else "fraction_precip_ge_0_1mm": sum(
                    v < 0 if variable == "temperature_2m" else v >= 0.1 for v in values
                )
                / len(values)
                if values
                else None,
            }
            series.append(row)
        stats[variable] = series
    usable = [
        i
        for i in range(len(times))
        if any(v[i] is not None for group in members.values() for v in group.values())
    ]
    if not usable:
        raise ValueError("No non-null ensemble values")
    dates = [
        datetime.fromtimestamp(t, UTC).astimezone(ZoneInfo(timezone)).isoformat() for t in times
    ]
    return {
        "status": "forecast_ensemble",
        "grid": {k: data.get(k) for k in ("latitude", "longitude", "elevation")},
        "model_run_at": None,
        "model_run_note": "Not exposed by forecast API",
        "units": ENSEMBLE_VARIABLES,
        "valid_at": dates,
        "members": members,
        "statistics": stats,
        "missing": missing,
        "coverage_start": dates[usable[0]],
        "coverage_end": dates[usable[-1]],
        "missing_hours": sum(
            int((b - a) / 3600) - 1 for a, b in zip(times, times[1:], strict=False)
        ),
        "statistics_note": "Linear interpolated empirical quantiles; fractions of available members, not calibrated local probabilities. Nulls excluded, counts explicit. Precipitation is previous-hour equivalent water; provider may interpolate native 3/6h fields.",
    }
