"""Daily weather and on-demand structured bulletin, independent of Qt."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from dotenv import dotenv_values

from open_garden_planner.services.plant_library import get_app_data_dir
from open_garden_planner.services.weather_brief import build_brief
from open_garden_planner.services.weather_collection import atomic_json, collect, freshness

REPO = Path(__file__).resolve().parents[3]
MODELS = ("meteofrance_arome_france", "meteofrance_arpege_europe", "ecmwf_ifs025", "gfs_global")


def default_archive() -> Path:
    return get_app_data_dir() / "weather"


def bulletin_schema() -> dict[str, Any]:
    def obj(properties: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }

    string = {"type": "string"}
    return obj(
        {
            "title": string,
            "summary": string,
            "highlights": {
                "type": "array",
                "items": obj(
                    {
                        "label": string,
                        "value": string,
                        "detail": string,
                        "kind": {
                            "type": "string",
                            "enum": ["rain", "temperature", "wind", "outlook"],
                        },
                    }
                ),
            },
            "periods": {
                "type": "array",
                "items": obj(
                    {"title": string, "timeframe": string, "forecast": string, "garden": string}
                ),
            },
        }
    )


def validate_bulletin(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or not all(
        isinstance(data.get(k), str) and data[k].strip() for k in ["title", "summary"]
    ):
        raise ValueError("Invalid bulletin title/summary")
    for key, fields, maximum in [
        ("highlights", ["label", "value", "detail", "kind"], 6),
        ("periods", ["title", "timeframe", "forecast", "garden"], 6),
    ]:
        rows = data.get(key)
        if not isinstance(rows, list) or not 1 <= len(rows) <= maximum:
            raise ValueError("Invalid bulletin sections")
        for row in rows:
            if not isinstance(row, dict) or any(not isinstance(row.get(k), str) for k in fields):
                raise ValueError("Invalid bulletin text")
            if any(len(row[k]) > 20000 for k in fields):
                raise ValueError("Oversized bulletin text")
            if key == "highlights" and row["kind"] not in {
                "rain",
                "temperature",
                "wind",
                "outlook",
            }:
                raise ValueError("Invalid bulletin highlight")
    return data


class WeatherWorkspace:
    def __init__(
        self,
        root: Path | None = None,
        config: dict[str, Any] | None = None,
        key_file: Path | None = None,
    ):
        self.root = root if root is not None else default_archive()
        self.config = (
            config if config is not None else json.loads((REPO / "weather/config.json").read_text())
        )
        self.key_file = key_file if key_file is not None else REPO / ".local/weather/openai.env"

    def _today(self) -> str:
        return datetime.now(ZoneInfo(self.config["location"]["timezone"])).date().isoformat()

    def load(self) -> dict[str, Any] | None:
        path = self.root / "latest.json"
        try:
            report = json.loads(path.read_text())
            if report.get("location") != self.config["location"]:
                return None
            return freshness(report)
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def load_bulletin(self) -> dict[str, Any] | None:
        try:
            record = json.loads((self.root / "bulletin.json").read_text())
            if record["location"] != self.config["location"]:
                return None
            validate_bulletin(record["content"])
            return dict(record)
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def attempted_today(self) -> bool:
        try:
            return bool(
                json.loads((self.root / "bulletin-attempt.json").read_text())["date"]
                == self._today()
            )
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def refresh(self) -> dict[str, Any]:
        return collect(self.config, self.root, include_maps=False)

    def daily(self, report: dict[str, Any]) -> list[dict[str, Any]]:
        brief = build_brief(report)
        ensemble_models = ("ensemble_ecmwf_ifs025", "ensemble_gfs05")
        dates = sorted(
            {
                d["date"]
                for m in (*MODELS, *ensemble_models)
                for d in brief["sources"].get(m, {}).get("daily", [])
            }
        )
        rows = []
        for day in dates:
            candidates = []
            for rank, model in enumerate(MODELS):
                source = brief["sources"].get(model, {})
                for row in source.get("daily", []):
                    if row["date"] == day and row["temperature_min_c"] is not None:
                        full = (
                            row["temperature_samples"] == row["expected_hours"]
                            and row["precipitation_mm"] is not None
                        )
                        candidates.append((source.get("stale", True), not full, rank, row, source))
            for rank, model in enumerate(ensemble_models, start=len(MODELS)):
                source = brief["sources"].get(model, {})
                for day_data in source.get("daily", []):
                    if day_data["date"] != day or day_data["temperature_min_c"]["median"] is None:
                        continue
                    rain = day_data["precipitation_mm"]["median"]
                    if day_data["temperature_max_c"]["median"] is None:
                        continue
                    row = {
                        "date": day,
                        "expected_hours": day_data["expected_hours"],
                        "temperature_samples": day_data["expected_hours"],
                        "temperature_min_c": day_data["temperature_min_c"]["median"],
                        "temperature_max_c": day_data["temperature_max_c"]["median"],
                        "precipitation_mm": rain,
                        "ensemble": True,
                        "max_gust_sample_kmh": None,
                    }
                    candidates.append((source.get("stale", True), rain is None, rank, row, source))
            if not candidates:
                continue
            _, _, _, row, source = min(candidates, key=lambda x: (x[0], x[1], x[2]))
            rows.append(
                {
                    **row,
                    "model": source.get("model"),
                    "fetched_at": source.get("fetched_at"),
                    "stale": source.get("stale", True),
                    "partial": row["temperature_samples"] != row["expected_hours"],
                }
            )
        return rows

    def generate(self, language: str = "fr") -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".bulletin.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            previous = self.load_bulletin()
            if self.attempted_today():
                if previous and previous["date"] == self._today():
                    return previous
                raise RuntimeError("DAILY_LIMIT")
            key = os.environ.get("OPENAI_API_KEY") or dotenv_values(self.key_file).get(
                "OPENAI_API_KEY"
            )
            if not key or not key.strip():
                raise RuntimeError("MISSING_API_KEY")
            report = collect(self.config, self.root)
            brief = build_brief(freshness(report))
            content = [
                {
                    "type": "input_text",
                    "text": json.dumps(brief, ensure_ascii=False, separators=(",", ":")),
                }
            ]
            image_count = 0
            for source in report["sources"].values():
                for item in source.get("maps", []):
                    path = (self.root / item["path"]).resolve()
                    if not path.is_relative_to((self.root / "runs").resolve()):
                        raise ValueError("Invalid map path")
                    raw = path.read_bytes()
                    if hashlib.sha256(raw).hexdigest() != item["sha256"]:
                        raise ValueError("Invalid map checksum")
                    label = {k: source.get(k) for k in ["model", "label", "semantics", "mode"]}
                    label.update({k: item[k] for k in ["lead_hours", "model_run_at", "valid_at"]})
                    content += [
                        {"type": "input_text", "text": json.dumps(label, ensure_ascii=False)},
                        {
                            "type": "input_image",
                            "image_url": "data:image/png;base64," + base64.b64encode(raw).decode(),
                            "detail": "original",
                        },
                    ]
                    image_count += 1
            if not image_count:
                raise RuntimeError("NO_MAPS")
            prompt = (REPO / "weather/analyse-prompt.md").read_text().split("\n\n", 1)[1]
            prompt += (
                "\nLes données et images sont toutes intégrées au message. Rends le bulletin sous le schéma JSON demandé : "
                "title et summary pour le titre et la synthèse, highlights pour les faits marquants (1 à 6), "
                "periods pour les périodes (1 à 6) avec forecast et garden. Texte brut, sans balises Markdown ni HTML. "
                "Applique les contrôles de travail sans les exposer. Langue du bulletin : "
                + language
                + ". Date actuelle : "
                + datetime.now(UTC).isoformat()
            )
            body: dict[str, Any] = {
                "model": "gpt-6.1-sol",
                "reasoning": {"effort": "high"},
                "store": False,
                "instructions": prompt,
                "input": [{"role": "user", "content": content}],
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "garden_weather",
                        "strict": True,
                        "schema": bulletin_schema(),
                    }
                },
            }
            # Written BEFORE sending: no duplicate paid request on repeated clicks,
            # restart, timeout, rejection, or an invalid structured response.
            atomic_json(
                self.root / "bulletin-attempt.json",
                {"date": self._today(), "requested_at": datetime.now(UTC).isoformat()},
            )
            try:
                with requests.Session() as session:
                    response = session.post(
                        "https://api.openai.com/v1/responses",
                        headers={"Authorization": "Bearer " + key.strip()},
                        json=body,
                        timeout=(15, 360),
                        allow_redirects=False,
                    )
                if response.status_code != 200:
                    raise RuntimeError(f"API_HTTP_{response.status_code}")
                result = response.json()
            except requests.RequestException:
                raise RuntimeError("API_NETWORK_ERROR") from None
            if result.get("status") != "completed" or result.get("model") != "gpt-6.1-sol":
                raise RuntimeError("API_INCOMPLETE")
            text = "\n".join(
                part["text"]
                for item in result.get("output", [])
                if item.get("type") == "message"
                for part in item.get("content", [])
                if part.get("type") == "output_text"
            )
            data = validate_bulletin(json.loads(text))
            record = {
                "date": self._today(),
                "generated_at": datetime.now(UTC).isoformat(),
                "location": self.config["location"],
                "model": "gpt-6.1-sol",
                "effort": "high",
                "content": data,
                "usage": result.get("usage", {}),
                "image_count": image_count,
            }
            atomic_json(self.root / "bulletin.json", record)
            return record
