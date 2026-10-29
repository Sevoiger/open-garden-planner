"""Small local numerical briefing; map files remain available independently."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from open_garden_planner.services.weather_ensembles import quantile


def distribution(values: list[float], returned_members: int) -> dict[str, Any]:
    return {
        "complete_members": len(values),
        "returned_members": returned_members,
        "p10": quantile(values, 0.1) if values else None,
        "median": quantile(values, 0.5) if values else None,
        "p90": quantile(values, 0.9) if values else None,
    }


def ensemble_days(source: dict[str, Any], timezone: str) -> list[dict[str, Any]]:
    """Aggregate complete member-days before quantiles; never sum hourly medians."""
    times = [datetime.fromisoformat(t) for t in source["valid_at"]]
    zone = ZoneInfo(timezone)
    dates = sorted({t.astimezone(zone).date() for t in times})
    days = []
    for day in dates:
        local = datetime(day.year, day.month, day.day, tzinfo=zone)
        start, end = local.astimezone(UTC), (local + timedelta(days=1)).astimezone(UTC)
        expected = int((end - start).total_seconds() / 3600)
        instant = [i for i, t in enumerate(times) if start <= t < end]
        interval = [i for i, t in enumerate(times) if start < t <= end]
        minima, maxima, rain = [], [], []
        temp_members = source["members"].get("temperature_2m", {})
        rain_members = source["members"].get("precipitation", {})
        for values in temp_members.values():
            available = [values[i] for i in instant if values[i] is not None]
            if len(available) == expected:
                minima.append(min(available))
                maxima.append(max(available))
        for values in rain_members.values():
            available = [values[i] for i in interval if values[i] is not None]
            if len(available) == expected:
                rain.append(sum(available))
        days.append(
            {
                "date": day.isoformat(),
                "expected_hours": expected,
                "temperature_min_c": distribution(minima, len(temp_members)),
                "temperature_max_c": distribution(maxima, len(temp_members)),
                "precipitation_mm": distribution(rain, len(rain_members)),
                "fraction_daily_min_below_0c": sum(t < 0 for t in minima) / len(minima)
                if minima
                else None,
                "fraction_daily_precip_ge_1mm": sum(p >= 1 for p in rain) / len(rain)
                if rain
                else None,
            }
        )
    return days


def build_brief(report: dict[str, Any]) -> dict[str, Any]:
    sources = {}
    for key, source in report["sources"].items():
        # Include availability of every source, but no raw members, pixels or
        # image-derived findings in the numerical briefing.
        item = {
            k: source.get(k)
            for k in [
                "provider",
                "model",
                "status",
                "state",
                "fetched_at",
                "age_hours",
                "stale",
                "error",
                "grid",
                "model_run_at",
                "coverage_start",
                "coverage_end",
                "missing",
            ]
        }
        if "hours" in source:
            item["daily"] = []
            for day in source["daily"]:
                rows = [r for r in source["hours"] if r["valid_at"][:10] == day["date"]]
                gusts = [r["wind_gusts_10m"] for r in rows if r["wind_gusts_10m"] is not None]
                winds = [r["wind_speed_10m"] for r in rows if r["wind_speed_10m"] is not None]
                item["daily"].append(
                    {
                        **day,
                        "max_gust_sample_kmh": max(gusts) if gusts else None,
                        "mean_wind_sample_kmh": round(sum(winds) / len(winds), 2)
                        if winds
                        else None,
                        "wind_samples": len(winds),
                        "gust_samples": len(gusts),
                        "gust_note": "Maximum of samples timestamped on this day; provider gust intervals can cross midnight. Not a strict civil-day gust maximum.",
                    }
                )
        if source.get("status") == "forecast_ensemble":
            item["daily"] = ensemble_days(source, report["location"]["timezone"])
        if source.get("status") == "forecast_map":
            item.update(
                {
                    k: source.get(k)
                    for k in [
                        "family",
                        "mode",
                        "label",
                        "semantics",
                        "mixed_runs",
                        "missing_leads_hours",
                    ]
                }
            )
            item["image_count"] = len(source["maps"])
            item["map_interpretation"] = "Not performed by the local numerical briefing"
        sources[key] = item
    return {
        "schema_version": 1,
        "kind": "local_numerical_brief",
        "attempted_at": report["attempted_at"],
        "checked_at": report["checked_at"],
        "location": report["location"],
        "garden_context": report["garden_context"],
        "sources": sources,
        "notes": [
            "Maps remain available in the collection manifest; this briefing does not interpret their pixels.",
            "Ensemble day quantiles use complete members only, after per-member daily aggregation.",
            "Fractions are raw ensemble fractions, not calibrated local probabilities.",
            "Daily frost uses hourly temperature samples, not soil temperature.",
            "Null is not zero; forecasts are not observed rain; rain minus ET0 is not soil storage.",
        ],
    }
