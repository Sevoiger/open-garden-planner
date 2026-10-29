"""Daily budget and structured output boundaries, using disposable credentials."""

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
import requests

from open_garden_planner.services import weather_workspace as ws
from open_garden_planner.services.weather_collection import atomic_json
from tests.unit.test_weather_collection import config, raw


def content():
    return {
        "title": "Briollay",
        "summary": "Sec puis plus frais.",
        "highlights": [{"label": "Pluie", "value": "0 mm", "detail": "Demain", "kind": "rain"}],
        "periods": [
            {
                "title": "Temps sec",
                "timeframe": "Demain",
                "forecast": "15 °C.",
                "garden": "Surface plus sèche.",
            }
        ],
    }


def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    key = tmp_path / "key.env"
    key.write_text("OPENAI_API_KEY=disposable-test-key\n")
    return ws.WeatherWorkspace(tmp_path / "archive", config(), key)


def setup_api(service, monkeypatch, status=200, data=None):
    root = service.root
    root.mkdir()
    image = root / "runs" / "test" / "map.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"test-image")
    import hashlib

    report = {
        "attempted_at": datetime.now(UTC).isoformat(),
        "checked_at": datetime.now(UTC).isoformat(),
        "location": service.config["location"],
        "garden_context": "sand",
        "refresh_hours": 24,
        "sources": {
            "map": {
                "maps": [
                    {
                        "path": str(image.relative_to(root)),
                        "sha256": hashlib.sha256(b"test-image").hexdigest(),
                        "model_run_at": datetime.now(UTC).isoformat(),
                        "valid_at": datetime.now(UTC).isoformat(),
                        "lead_hours": 12,
                    }
                ]
            }
        },
    }
    collect = MagicMock(return_value=report)
    monkeypatch.setattr(ws, "collect", collect)
    response = MagicMock(status_code=status)
    response.json.return_value = (
        data
        if data is not None
        else {
            "status": "completed",
            "model": "gpt-6.1-sol",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(content())}],
                }
            ],
            "usage": {"total_tokens": 100},
        }
    )
    post = MagicMock(return_value=response)
    monkeypatch.setattr(ws.requests.Session, "post", post)
    return post, collect


def test_single_daily_request_survives_restart_and_language_change(tmp_path, monkeypatch):
    service = workspace(tmp_path, monkeypatch)
    post, collect = setup_api(service, monkeypatch)
    first = service.generate("fr")
    restarted = ws.WeatherWorkspace(service.root, service.config, service.key_file)
    assert restarted.generate("de") == first
    assert post.call_count == 1 and collect.call_count == 1
    payload = post.call_args.kwargs["json"]
    assert payload["model"] == "gpt-6.1-sol"
    assert payload["reasoning"] == {"effort": "high"}
    assert "max_output_tokens" not in payload
    assert payload["text"]["format"]["strict"] is True
    assert payload["input"][0]["content"][-1]["detail"] == "original"
    assert restarted.load_bulletin()["content"] == content()


@pytest.mark.parametrize("failure", ["http", "timeout", "invalid", "incomplete"])
def test_failed_request_is_not_replayed_or_overwrites_previous(tmp_path, monkeypatch, failure):
    service = workspace(tmp_path, monkeypatch)
    data = {"status": "incomplete", "model": "gpt-6.1-sol"} if failure == "incomplete" else None
    if failure == "invalid":
        data = {"status": "completed", "model": "gpt-6.1-sol", "output": []}
    post, _ = setup_api(service, monkeypatch, status=401 if failure == "http" else 200, data=data)
    if failure == "timeout":
        post.side_effect = requests.Timeout("network")
    old = {"date": "2000-01-01", "location": service.config["location"], "content": content()}
    atomic_json(service.root / "bulletin.json", old)
    with pytest.raises((RuntimeError, ValueError)):
        service.generate()
    with pytest.raises(RuntimeError, match="DAILY_LIMIT"):
        service.generate()
    assert post.call_count == 1
    assert service.load_bulletin() == old


def test_missing_key_does_not_collect_or_use_daily_attempt(tmp_path, monkeypatch):
    service = workspace(tmp_path, monkeypatch)
    service.key_file.unlink()
    collect = MagicMock()
    monkeypatch.setattr(ws, "collect", collect)
    with pytest.raises(RuntimeError, match="MISSING_API_KEY"):
        service.generate()
    assert not service.attempted_today() and not collect.called


def test_forecast_then_full_collection_reuses_numeric_sources(tmp_path, monkeypatch):
    from open_garden_planner.services import weather_collection as wc

    c = config()
    calls = []
    monkeypatch.setattr(
        wc.CurlClient, "get", lambda _self, url: calls.append(url) or json.dumps(raw()).encode()
    )
    first = wc.collect(c, tmp_path, include_maps=False)
    assert first["collection_scope"] == "forecast"
    full = wc.collect(c, tmp_path, include_maps=True)
    assert full["collection_scope"] == "full"
    assert len(calls) == 1
    assert (
        full["sources"]["meteofrance_arome_france"]["fetched_at"]
        == first["sources"]["meteofrance_arome_france"]["fetched_at"]
    )


def test_daily_values_keep_unknown_rain_and_source_identity(tmp_path, monkeypatch):
    service = workspace(tmp_path, monkeypatch)
    from open_garden_planner.services.weather_collection import normalize

    source = {
        "model": "AROME",
        "fetched_at": datetime.now(UTC).isoformat(),
        "stale": False,
        **normalize(raw(), service.config["location"]["timezone"]),
    }
    report = {
        "attempted_at": datetime.now(UTC).isoformat(),
        "checked_at": datetime.now(UTC).isoformat(),
        "location": service.config["location"],
        "garden_context": "sand",
        "sources": {"meteofrance_arome_france": source},
    }
    days = service.daily(report)
    assert days[0]["precipitation_mm"] is None
    assert days[0]["partial"] and days[0]["model"] == "AROME"


def test_fresh_source_wins_over_stale_preferred_model(tmp_path, monkeypatch):
    service = workspace(tmp_path, monkeypatch)
    from open_garden_planner.services.weather_collection import normalize

    old = {
        "model": "AROME",
        "stale": True,
        **normalize(raw(), service.config["location"]["timezone"]),
    }
    fresh = {**old, "model": "ARPEGE", "stale": False}
    report = {
        "attempted_at": datetime.now(UTC).isoformat(),
        "checked_at": datetime.now(UTC).isoformat(),
        "location": service.config["location"],
        "garden_context": "sand",
        "sources": {"meteofrance_arome_france": old, "meteofrance_arpege_europe": fresh},
    }
    assert service.daily(report)[0]["model"] == "ARPEGE"


def test_failed_numeric_attempt_is_not_retried_during_same_day_promotion(tmp_path, monkeypatch):
    from open_garden_planner.services import weather_collection as wc

    c = config()
    calls = []

    def fail(_self, url):
        calls.append(url)
        raise RuntimeError("HTTP 503")

    monkeypatch.setattr(wc.CurlClient, "get", fail)
    wc.collect(c, tmp_path, include_maps=False)
    wc.collect(c, tmp_path, include_maps=True)
    assert len(calls) == 1


def test_ensembles_extend_daily_strip_without_ai(tmp_path, monkeypatch):
    from datetime import timedelta

    service = workspace(tmp_path, monkeypatch)
    start = datetime(2026, 11, 9, 23, tzinfo=UTC)
    source = {
        "status": "forecast_ensemble",
        "model": "ecmwf_ifs025",
        "stale": False,
        "valid_at": [(start + timedelta(hours=i)).isoformat() for i in range(25)],
        "members": {
            "temperature_2m": {"control": [4] * 25, "member01": [6] * 25},
            "precipitation": {"control": [1] * 25, "member01": [2] * 25},
        },
    }
    report = {
        "attempted_at": datetime.now(UTC).isoformat(),
        "checked_at": datetime.now(UTC).isoformat(),
        "location": service.config["location"],
        "garden_context": "sand",
        "sources": {"ensemble_ecmwf_ifs025": source},
    }
    days = service.daily(report)
    assert days[0]["date"] == "2026-11-10"
    assert days[0]["ensemble"] and days[0]["temperature_min_c"] == 5
    assert days[0]["precipitation_mm"] == 36
    assert days[0]["max_gust_sample_kmh"] is None
