"""Local aggregates must precede ensemble statistics and preserve partial days."""

from datetime import UTC, datetime, timedelta

import pytest

from open_garden_planner.services.weather_brief import build_brief, ensemble_days


@pytest.mark.parametrize(
    ("start", "hours"), [("2026-03-29T00:00:00+01:00", 23), ("2026-10-25T00:00:00+02:00", 25)]
)
def test_ensemble_days_aggregate_each_complete_member_first(start, hours):
    base = datetime.fromisoformat(start).astimezone(UTC)
    dates = [(base + timedelta(hours=i)).isoformat() for i in range(hours + 1)]
    # Three members rain on different hours: median hourly rain is always zero,
    # while every member-day has a nonzero total. Daily median must be nonzero.
    members = {str(m): [1 if i == m + 1 else 0 for i in range(hours + 1)] for m in range(3)}
    source = {
        "valid_at": dates,
        "members": {"precipitation": members, "temperature_2m": {"control": [-1] * (hours + 1)}},
    }
    day = ensemble_days(source, "Europe/Paris")[0]
    assert day["expected_hours"] == hours
    assert day["precipitation_mm"]["median"] == 1
    assert day["precipitation_mm"]["complete_members"] == 3
    assert day["fraction_daily_precip_ge_1mm"] == 1
    assert day["fraction_daily_min_below_0c"] == 1
    members["0"][-1] = None
    day = ensemble_days(source, "Europe/Paris")[0]
    assert day["precipitation_mm"]["complete_members"] == 2
    assert day["precipitation_mm"]["returned_members"] == 3
    last = ensemble_days(source, "Europe/Paris")[-1]
    assert last["precipitation_mm"]["median"] is None


def test_brief_does_not_claim_to_read_maps_or_copy_raw_members():
    source = {
        "status": "forecast_map",
        "maps": [{"path": "x.png"}],
        "label": "Cumul pluie",
        "state": "ok",
    }
    report = {
        "attempted_at": "2026-10-29T08:00:00+00:00",
        "checked_at": "2026-10-29T08:00:00+00:00",
        "location": {"timezone": "Europe/Paris"},
        "garden_context": "sand",
        "sources": {"maps": source},
    }
    brief = build_brief(report)
    assert brief["sources"]["maps"]["image_count"] == 1
    assert "Not performed" in brief["sources"]["maps"]["map_interpretation"]
    assert "maps" not in brief["sources"]["maps"]


def test_gust_summary_explicitly_uses_timestamped_samples_not_civil_day_extreme():
    report = {
        "attempted_at": "2026-10-29T08:00:00+00:00",
        "checked_at": "2026-10-29T08:00:00+00:00",
        "location": {"timezone": "Europe/Paris"},
        "garden_context": "sand",
        "sources": {
            "forecast": {
                "daily": [{"date": "2026-10-29"}],
                "hours": [
                    {
                        "valid_at": "2026-10-29T00:00:00+01:00",
                        "wind_gusts_10m": 70,
                        "wind_speed_10m": 20,
                    }
                ],
            }
        },
    }
    day = build_brief(report)["sources"]["forecast"]["daily"][0]
    assert day["max_gust_sample_kmh"] == 70
    assert "max_gust_kmh" not in day
    assert "cross midnight" in day["gust_note"]
