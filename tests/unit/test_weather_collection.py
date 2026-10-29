"""Collector boundaries: partial data, local DST days, pinning and safe fallback."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from open_garden_planner.services import weather_collection as wc

ROOT = Path(__file__).resolve().parents[2]


def config():
    import json

    result = json.loads((ROOT / "weather/config.json").read_text())
    result["models"] = {"meteofrance_arome_france": 48}
    result["meteociel"]["enabled"] = False
    return result


def raw(times=None):
    times = times or [1793232000, 1793235600, 1793239200]
    return {
        "latitude": 47.575,
        "longitude": -0.5,
        "elevation": 22,
        "hourly_units": {"time": "unixtime", **wc.VARIABLES},
        "hourly": {"time": times, **{k: [None, 0, 2] for k in wc.VARIABLES}},
    }


def test_missing_is_not_zero_and_unknown_run_stays_unknown():
    result = wc.normalize(raw(), "Europe/Paris")
    assert result["hours"][0]["precipitation"] is None
    assert result["hours"][1]["precipitation"] == 0
    assert result["model_run_at"] is None
    assert result["daily"][0]["precipitation_mm"] is None
    assert "precipitation" in result["missing"]


@pytest.mark.parametrize("mutation", ["duplicate", "unit", "length", "negative", "nan", "all_null"])
def test_invalid_response_is_rejected(mutation):
    data = raw()
    if mutation == "duplicate":
        data["hourly"]["time"][1] = data["hourly"]["time"][0]
    elif mutation == "unit":
        data["hourly_units"]["precipitation"] = "inch"
    elif mutation == "length":
        data["hourly"]["precipitation"].pop()
    elif mutation == "negative":
        data["hourly"]["precipitation"][1] = -1
    elif mutation == "nan":
        data["hourly"]["temperature_2m"][0] = float("nan")
    else:
        for key in wc.VARIABLES:
            data["hourly"][key] = [None] * 3
    with pytest.raises(ValueError):
        wc.normalize(data, "Europe/Paris")


def test_missing_hours_are_reported():
    result = wc.normalize(raw([1793232000, 1793239200, 1793242800]), "Europe/Paris")
    assert result["missing_hours"] == 1


@pytest.mark.parametrize(("day", "hours"), [("2026-03-29", 23), ("2026-10-25", 25)])
def test_daily_intervals_follow_real_dst_day(day, hours):
    from zoneinfo import ZoneInfo

    start = datetime.fromisoformat(day).replace(tzinfo=ZoneInfo("Europe/Paris")).astimezone(UTC)
    rows = []
    for i in range(hours + 1):
        rows.append(
            {
                "valid_at": (start + timedelta(hours=i)).isoformat(),
                "temperature_2m": -1,
                "precipitation": 1,
                "et0_fao_evapotranspiration": 0.1,
            }
        )
    summary = next(d for d in wc.daily_summary(rows, "Europe/Paris") if d["date"] == day)
    assert summary["expected_hours"] == hours
    assert summary["precipitation_mm"] == hours
    assert summary["rain_minus_et0_mm"] == round(hours * 0.9, 3)
    assert summary["frost_samples"] == hours
    # Midnight's amount belongs to previous day, and missing cannot become zero.
    rows[-1]["precipitation"] = None
    assert wc.daily_summary(rows, "Europe/Paris")[0]["precipitation_mm"] is None


def test_only_published_pinned_maps_are_used():
    page = """imgArrayRun=['https://modeles7.meteociel.fr/modeles/arome/runs/2026102900/aromehd-1-3-1.png?29-02'];"""
    urls = wc.pinned_maps(page, [3], 1)
    assert "/runs/2026102900/" in urls[3]
    with pytest.raises(ValueError, match="unavailable"):
        wc.pinned_maps(page, [48], 1)
    with pytest.raises(ValueError):
        wc.pinned_maps(page.replace("/runs/2026102900/", "/"), [3], 1)


def test_cache_failure_and_configuration_change(tmp_path, monkeypatch):
    import json

    calls = []

    def get(self, url):
        calls.append(url)
        return json.dumps(raw()).encode()

    monkeypatch.setattr(wc.CurlClient, "get", get)
    first = wc.collect(config(), tmp_path)
    source = first["sources"]["meteofrance_arome_france"]
    assert source["state"] == "ok"
    assert len(calls) == 1
    assert wc.collect(config(), tmp_path)["sources"]["meteofrance_arome_france"]["state"] == "ok"
    assert len(calls) == 1
    archived = tmp_path / source["raw"]["path"]
    assert archived.exists()

    def fail(self, url):
        raise RuntimeError("HTTP 503")

    monkeypatch.setattr(wc.CurlClient, "get", fail)
    fallback = wc.collect(config(), tmp_path, force=True)["sources"]["meteofrance_arome_france"]
    assert fallback["state"] == "fallback"
    assert fallback["stale"] is True
    assert fallback["fetched_at"] == source["fetched_at"]
    assert fallback["raw"] == source["raw"]
    assert archived.exists()
    changed = config()
    changed["location"]["latitude"] += 1
    missing = wc.collect(changed, tmp_path)["sources"]["meteofrance_arome_france"]
    assert missing["state"] == "unavailable"
    assert "raw" not in missing


def test_overlapping_collections_do_not_overwrite(tmp_path, monkeypatch):
    import fcntl

    tmp_path.mkdir(exist_ok=True)
    with (tmp_path / ".collect.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            wc.collect(config(), tmp_path)
    assert not (tmp_path / "latest.json").exists()


def test_forbidden_url_is_rejected_before_network():
    with pytest.raises(ValueError, match="Unexpected"):
        wc.CurlClient(0).get("https://example.com/map.png")


def test_horizon_not_extended():
    c = config()
    c["models"]["meteofrance_arome_france"] = 168
    with pytest.raises(ValueError, match="horizon"):
        wc.validate_config(c)


@pytest.mark.parametrize(
    "last_attempt",
    [
        "2026-10-28T07:15:30+00:00",  # Yesterday's completion was after cron's time.
        "2026-03-28T07:15:00+00:00",  # Spring DST: only 23 hours since yesterday.
    ],
)
def test_daily_cron_runs_next_local_day(tmp_path, monkeypatch, last_attempt):
    import json

    calls = []

    def get(self, url):
        calls.append(url)
        return json.dumps(raw()).encode()

    monkeypatch.setattr(wc.CurlClient, "get", get)
    report = wc.collect(config(), tmp_path)
    report["attempted_at"] = last_attempt
    wc.atomic_json(tmp_path / "latest.json", report)
    today = "2026-10-29T07:15:00+00:00" if "10-28" in last_attempt else "2026-03-29T06:15:00+00:00"

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(today).astimezone(tz)

    monkeypatch.setattr(wc, "datetime", Clock)
    wc.collect(config(), tmp_path)
    assert len(calls) == 2
    wc.collect(config(), tmp_path)
    assert len(calls) == 2  # Same day's repeated call uses cache.
