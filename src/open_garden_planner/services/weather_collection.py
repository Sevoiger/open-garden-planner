"""Standalone, curl-based forecast archive; never touches the garden's storage."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import re
import subprocess
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

from open_garden_planner.services.weather_ensembles import (
    ENSEMBLE_MODELS,
    ENSEMBLE_VARIABLES,
    normalize_ensemble,
)
from open_garden_planner.services.weather_maps import fetch_product, validate_groups

VARIABLES = {
    "temperature_2m": "°C",
    "relative_humidity_2m": "%",
    "dew_point_2m": "°C",
    "precipitation": "mm",
    "wind_speed_10m": "km/h",
    "wind_direction_10m": "°",
    "wind_gusts_10m": "km/h",
    "cloud_cover": "%",
    "cloud_cover_low": "%",
    "pressure_msl": "hPa",
    "shortwave_radiation": "W/m²",
    "et0_fao_evapotranspiration": "mm",
}
MODEL_HORIZONS = {
    "meteofrance_arome_france": 48,
    "meteofrance_arpege_europe": 96,
    "ecmwf_ifs025": 168,
    "gfs_global": 168,
}
USER_AGENT = "OpenGardenPlanner-Weather/1.0 (personal non-commercial archive)"


def iso_now() -> str:
    return datetime.now(UTC).isoformat()


def atomic_json(path: Path, data: dict[str, Any]) -> None:
    """Publish a complete document; readers never see a partial write."""
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    temporary.replace(path)


class CurlClient:
    def __init__(self, spacing: float):
        self.spacing = spacing
        self.last_request = 0.0

    def get(self, url: str) -> bytes:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or not (
                host in {"api.open-meteo.com", "ensemble-api.open-meteo.com", "www.meteociel.fr"}
                or re.fullmatch(r"modeles\d+\.meteociel\.fr", host)
            )
            or parsed.port not in {None, 443}
            or parsed.username
        ):
            raise ValueError("Unexpected weather source URL")
        time.sleep(max(0, self.spacing - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="ogp-weather-http-") as directory:
            target = Path(directory) / "response"
            # No redirects, cookies, browser impersonation or protection bypass.
            result = subprocess.run(
                [
                    "curl",
                    "--silent",
                    "--show-error",
                    "--fail",
                    "--proto",
                    "=https",
                    "--connect-timeout",
                    "10",
                    "--max-time",
                    "40",
                    "--max-filesize",
                    "12582912",
                    "--retry",
                    "1",
                    "--retry-delay",
                    "3",
                    "--retry-max-time",
                    "85",
                    "--user-agent",
                    USER_AGENT,
                    "--output",
                    str(target),
                    "--write-out",
                    "%{http_code}",
                    url,
                ],
                capture_output=True,
                timeout=100,
                check=False,
            )
            if result.returncode or result.stdout != b"200":
                raise RuntimeError(
                    f"curl/HTTP failure: {result.returncode}, HTTP {result.stdout.decode(errors='replace')}"
                )
            return target.read_bytes()


def normalize(data: dict[str, Any], timezone: str) -> dict[str, Any]:
    """Validate hourly values and preserve missing hours without interpolation."""
    hourly, units = data["hourly"], data["hourly_units"]
    times = hourly["time"]
    if not times or units.get("time") != "unixtime":
        raise ValueError("No Unix hourly timestamps")
    if any(not isinstance(t, (int, float)) or not math.isfinite(t) for t in times):
        raise ValueError("Invalid hourly timestamps")
    if any(b <= a or (b - a) % 3600 for a, b in zip(times, times[1:], strict=False)):
        raise ValueError("Duplicate, unordered or non-hourly timestamps")
    values, missing = {}, {}
    for key, unit in VARIABLES.items():
        series = hourly.get(key)
        if series is None:
            missing[key] = "Not supplied by this model/provider"
            values[key] = [None] * len(times)
            continue
        if units.get(key) != unit or len(series) != len(times):
            raise ValueError(f"Unexpected unit or series length: {key}")
        if any(
            v is not None and (not isinstance(v, (int, float)) or not math.isfinite(v))
            for v in series
        ):
            raise ValueError(f"Invalid numeric value: {key}")
        if key in {"precipitation", "et0_fao_evapotranspiration"} and any(
            v is not None and v < 0 for v in series
        ):
            raise ValueError(f"Negative interval amount: {key}")
        if any(v is None for v in series):
            missing[key] = "Null values supplied by provider; outside coverage or unavailable"
        values[key] = series
    rows = [
        {
            "valid_at": datetime.fromtimestamp(t, UTC).astimezone(ZoneInfo(timezone)).isoformat(),
            **{key: series[i] for key, series in values.items()},
        }
        for i, t in enumerate(times)
    ]
    usable = [i for i in range(len(times)) if any(s[i] is not None for s in values.values())]
    if not usable:
        raise ValueError("Entire forecast is null")
    return {
        "grid": {k: data.get(k) for k in ("latitude", "longitude", "elevation")},
        "model_run_at": None,
        "model_run_note": "Not exposed by forecast API; retrieval time is not run time",
        "interpolation": "Open-Meteo hourly processing and terrain elevation correction; native cadence may differ",
        "grid_resolution_km": None,
        "status": "forecast",
        "units": VARIABLES,
        "missing": missing,
        "missing_hours": sum(
            int((b - a) / 3600) - 1 for a, b in zip(times, times[1:], strict=False)
        ),
        "coverage_start": rows[usable[0]]["valid_at"],
        "coverage_end": rows[usable[-1]]["valid_at"],
        "hours": rows,
        "daily": daily_summary(rows, timezone),
    }


def daily_summary(rows: list[dict[str, Any]], timezone: str) -> list[dict[str, Any]]:
    """Local calendar days; accumulations belong to the hour BEFORE valid_at."""
    zone = ZoneInfo(timezone)
    dates = sorted({datetime.fromisoformat(r["valid_at"]).astimezone(zone).date() for r in rows})
    result = []
    for day in dates:
        start = datetime(day.year, day.month, day.day, tzinfo=zone).astimezone(UTC)
        end = (datetime(day.year, day.month, day.day, tzinfo=zone) + timedelta(days=1)).astimezone(
            UTC
        )
        instant = [r for r in rows if start <= datetime.fromisoformat(r["valid_at"]) < end]
        intervals = [r for r in rows if start < datetime.fromisoformat(r["valid_at"]) <= end]
        expected = int((end - start).total_seconds() / 3600)
        temps = [r["temperature_2m"] for r in instant if r["temperature_2m"] is not None]
        rain = [r["precipitation"] for r in intervals if r["precipitation"] is not None]
        et0 = [
            r["et0_fao_evapotranspiration"]
            for r in intervals
            if r["et0_fao_evapotranspiration"] is not None
        ]
        complete = len(rain) == expected
        result.append(
            {
                "date": day.isoformat(),
                "expected_hours": expected,
                "temperature_samples": len(temps),
                "precipitation_samples": len(rain),
                "temperature_min_c": min(temps) if temps else None,
                "temperature_max_c": max(temps) if temps else None,
                "frost_samples": sum(t < 0 for t in temps) if temps else None,
                "precipitation_mm": round(sum(rain), 3) if complete else None,
                "et0_mm": round(sum(et0), 3) if len(et0) == expected else None,
                "rain_minus_et0_mm": round(sum(rain) - sum(et0), 3)
                if complete and len(et0) == expected
                else None,
            }
        )
    return result


def validate_config(config: dict[str, Any]) -> None:
    loc = config["location"]
    ZoneInfo(loc["timezone"])
    if not -90 <= loc["latitude"] <= 90 or not -180 <= loc["longitude"] <= 180:
        raise ValueError("Invalid coordinates")
    if not 1 <= config["refresh_hours"] <= 168 or not 1 <= config["request_spacing_seconds"] <= 60:
        raise ValueError("Invalid refresh/request spacing")
    for model, hours in config["models"].items():
        if (
            model not in MODEL_HORIZONS
            or not isinstance(hours, int)
            or not 1 <= hours <= MODEL_HORIZONS[model]
        ):
            raise ValueError(f"Unsupported model/horizon: {model}")
    mc = config["meteociel"]
    validate_groups(mc["groups"])
    for model, days in config.get("ensembles", {}).items():
        if (
            model not in ENSEMBLE_MODELS
            or not isinstance(days, int)
            or not 1 <= days <= ENSEMBLE_MODELS[model]
        ):
            raise ValueError(f"Unsupported ensemble model/horizon: {model}")
    url = urlsplit(mc["local_page"])
    if (
        url.scheme != "https"
        or url.netloc != "www.meteociel.fr"
        or not url.path.startswith("/previsions/")
    ):
        raise ValueError("Unexpected local page URL")


def freshness(report: dict[str, Any]) -> dict[str, Any]:
    """Recalculate at read time, including cached runs and failed-source fallback."""
    now = datetime.now(UTC)
    for source in report["sources"].values():
        fetched = source.get("fetched_at")
        source["age_hours"] = (
            round((now - datetime.fromisoformat(fetched)).total_seconds() / 3600, 2)
            if fetched
            else None
        )
        source["stale"] = (
            source["age_hours"] is None
            or source["age_hours"] >= report["refresh_hours"]
            or source["state"] != "ok"
        )
    report["checked_at"] = now.isoformat()
    return report


def collect(config: dict[str, Any], output: Path, force: bool = False) -> dict[str, Any]:
    validate_config(config)
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".collect.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _collect_locked(config, output, force)


def _collect_locked(config: dict[str, Any], output: Path, force: bool) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    latest = output / "latest.json"
    previous = json.loads(latest.read_text()) if latest.exists() else {}
    if previous.get("config_sha256") != fingerprint:
        previous = {}
    if previous and not force:
        last_attempt = datetime.fromisoformat(previous["attempted_at"])
        age = started_at - last_attempt
        zone = ZoneInfo(config["location"]["timezone"])
        # A daily cron must run on the next local day, even after a 23h DST day
        # or when the previous collection finished seconds after its start.
        next_daily_date = (
            config["refresh_hours"] == 24
            and started_at.astimezone(zone).date() > last_attempt.astimezone(zone).date()
        )
        if age.total_seconds() < config["refresh_hours"] * 3600 and not next_daily_date:
            return freshness(previous)
    run = output / "runs" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    run.mkdir(parents=True)
    client = CurlClient(config["request_spacing_seconds"])
    sources = {}

    def save(name: str, payload: bytes) -> dict[str, Any]:
        path = run / name
        path.write_bytes(payload)
        return {
            "path": str(path.relative_to(output)),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }

    def attempt(key: str, action: Callable[[], dict[str, Any]]) -> None:
        try:
            sources[key] = {**action(), "state": "ok", "fetched_at": iso_now()}
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            RuntimeError,
            subprocess.SubprocessError,
        ) as exc:
            old = previous.get("sources", {}).get(key, {})
            sources[key] = {
                **old,
                "state": "fallback" if old.get("fetched_at") else "unavailable",
                "error": str(exc),
            }

    for model, hours in config["models"].items():

        def fetch_model(model: str = model, hours: int = hours) -> dict[str, Any]:
            params = {
                "latitude": config["location"]["latitude"],
                "longitude": config["location"]["longitude"],
                "models": model,
                "forecast_hours": hours,
                "hourly": ",".join(VARIABLES),
                "timeformat": "unixtime",
                "timezone": "GMT",
                "wind_speed_unit": "kmh",
                "temperature_unit": "celsius",
                "precipitation_unit": "mm",
            }
            url = "https://api.open-meteo.com/v1/forecast?" + urlencode(params)
            raw = client.get(url)
            normalized = normalize(json.loads(raw), config["location"]["timezone"])
            return {
                "provider": "Open-Meteo",
                "model": model,
                "url": url,
                "attribution": "Open-Meteo (CC BY 4.0), Météo-France / ECMWF / NOAA according to model",
                "raw": save(f"{model}.json", raw),
                **normalized,
            }

        attempt(model, fetch_model)

    for model, days in config.get("ensembles", {}).items():

        def fetch_ensemble(model: str = model, days: int = days) -> dict[str, Any]:
            params = {
                "latitude": config["location"]["latitude"],
                "longitude": config["location"]["longitude"],
                "models": model,
                "forecast_days": days,
                "hourly": ",".join(ENSEMBLE_VARIABLES),
                "timeformat": "unixtime",
                "timezone": "GMT",
                "wind_speed_unit": "kmh",
                "temperature_unit": "celsius",
                "precipitation_unit": "mm",
            }
            url = "https://ensemble-api.open-meteo.com/v1/ensemble?" + urlencode(params)
            raw = client.get(url)
            return {
                "provider": "Open-Meteo",
                "model": model,
                "url": url,
                "requested_days": days,
                "attribution": "Open-Meteo CC BY 4.0; ECMWF / NOAA",
                "raw": save(f"ensemble-{model}.json", raw),
                **normalize_ensemble(json.loads(raw), config["location"]["timezone"]),
            }

        attempt(f"ensemble_{model}", fetch_ensemble)

    mc = config["meteociel"]
    if mc["enabled"]:

        def fetch_local() -> dict[str, Any]:
            raw = client.get(mc["local_page"])
            if b"Briollay" not in raw and config["location"]["name"].startswith("Briollay"):
                raise ValueError("Unexpected local page content")
            return {
                "provider": "Meteociel",
                "url": mc["local_page"],
                "status": "forecast_page",
                "raw": save("meteociel-local.html", raw),
            }

        attempt("meteociel_local", fetch_local)
        for family, group in mc["groups"].items():
            for product in group["products"]:
                key = f"meteociel_{family}_{product['mode']}"

                def fetch_maps(
                    family: str = family,
                    group: dict[str, Any] = group,
                    product: dict[str, Any] = product,
                ) -> dict[str, Any]:
                    return fetch_product(client, save, family, group, product)

                attempt(key, fetch_maps)
                print(f"{key}: {sources[key]['state']}", flush=True)
    report = freshness(
        {
            "schema_version": 2,
            "attempted_at": started_at.isoformat(),
            "config_sha256": fingerprint,
            "refresh_hours": config["refresh_hours"],
            "location": config["location"],
            "garden_context": config["garden_context"],
            "sources": sources,
            "requested_map_catalogue": config["meteociel"]["groups"],
            "limitations": [
                "No observations, observed radar or soil measurements",
                "No calibrated probability from deterministic model disagreement",
                "Rain minus ET0 is not garden soil water storage",
                "Archived forecasts are not observed rainfall",
            ],
        }
    )
    atomic_json(run / "manifest.json", report)
    atomic_json(latest, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--force", action="store_true", help="Ignore 24h cache")
    parser.add_argument("--status", action="store_true", help="Read archive only; recalculate age")
    args = parser.parse_args()
    try:
        if args.status:
            report = freshness(json.loads((args.output / "latest.json").read_text()))
        else:
            report = collect(json.loads(args.config.read_text()), args.output, args.force)
        for name, source in report["sources"].items():
            print(f"{name}: {source['state']}, age={source['age_hours']}h, stale={source['stale']}")
        print(args.output / "latest.json")
        return int(any(s["stale"] for s in report["sources"].values()) or not report["sources"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"Weather collection failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
