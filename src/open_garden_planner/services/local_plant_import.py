"""Explicit, closed-app import of owner-supplied JSON plant sheets.

Both stores are validated and backed up before changes. Each replacement is
atomic; an interruption between stores is completed by replay. Existing species
and installed collections are never overwritten by replay.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from open_garden_planner.models.plant_data import (
    GrowthRate,
    PlantSpeciesData,
    SunRequirement,
    WaterNeeds,
    species_key,
)
from open_garden_planner.models.plant_lists import PlantList, PlantListEntry, PlantListStore
from open_garden_planner.services.classic_plant_lists import _validate_existing


def _dimension(value: object) -> float | None:
    if value in (None, ""):
        return None
    match = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(cm|m)\s*", str(value))
    if match is None:
        raise ValueError(f"Ambiguous plant dimension: {value!r}")
    number = float(match[1].replace(",", ".")) * (100 if match[2] == "m" else 1)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"Invalid plant dimension: {value!r}")
    return number


def read_sheets(directory: Path) -> list[PlantSpeciesData]:
    """Map explicit fields only; retain every supplied field and its provenance."""
    result = []
    identities: set[str] = set()
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise ValueError("No JSON plant sheets found")
    for path in paths:
        original = path.read_bytes()
        record = json.loads(original)
        if not isinstance(record, dict) or record.get("schema_version") != "1.0":
            raise ValueError(f"Unsupported plant sheet: {path.name}")
        for key in ("nom_botanique", "nom_commun", "url", "safe_name"):
            if not isinstance(record.get(key), str) or not record[key].strip():
                raise ValueError(f"Missing {key}: {path.name}")
        chars = record.get("characteristics")
        if not isinstance(chars, dict):
            raise ValueError(f"Missing characteristics: {path.name}")
        identity = "local-sheet:" + hashlib.sha256(record["url"].strip().encode()).hexdigest()[:32]
        if identity in identities:
            raise ValueError(f"Duplicate source URL: {path.name}")
        identities.add(identity)
        # Composite ranges are preserved verbatim rather than collapsed to one
        # supposedly definitive sun/water category. No USDA zone is inferred
        # from a Celsius hardiness threshold, nor frost-relative task dates
        # from calendar months. Marketing 'Double' is not sexual FlowerType.
        plant = PlantSpeciesData(
            scientific_name=html.unescape(record["nom_botanique"].strip()),
            common_name=html.unescape(record["nom_commun"].strip()),
            max_height_cm=_dimension(chars.get("Hauteur à maturité")),
            max_spread_cm=_dimension(chars.get("Largeur à maturité")),
            growth_rate={"lente": GrowthRate.SLOW, "normale": GrowthRate.MEDIUM,
                         "rapide": GrowthRate.FAST, "très rapide": GrowthRate.FAST}.get(
                             str(chars.get("Croissance", "")).strip(), GrowthRate.UNKNOWN),
            sun_requirement={"Soleil": SunRequirement.FULL_SUN,
                             "Mi-ombre": SunRequirement.PARTIAL_SHADE,
                             "Ombre": SunRequirement.FULL_SHADE}.get(
                                 str(chars.get("Exposition", "")).strip(), SunRequirement.UNKNOWN),
            water_needs={"sol sec": WaterNeeds.LOW, "sol frais": WaterNeeds.MEDIUM}.get(
                str(chars.get("Humidité du sol", "")).strip(), WaterNeeds.UNKNOWN),
            soil_type=str(chars.get("Type de sol", "")),
            flower_color=str(chars.get("Fleur de couleur", "")),
            foliage_color=str(chars.get("Feuillage de couleur", "")),
            description=html.unescape(str(record.get("description_text", ""))),
            data_source="custom", source_id=identity,
            raw_data={"local_sheet": record, "source_file": path.name,
                      "sha256": hashlib.sha256(original).hexdigest(),
                      "mapping_version": 1},
        )
        result.append(plant)
    return result


def _custom_store(path: Path) -> tuple[dict[str, Any], bytes | None]:
    if not path.exists():
        return {"version": "1.0", "plants": {}}, None
    original = path.read_bytes()
    value = json.loads(original)
    if (not isinstance(value, dict) or value.get("version") != "1.0"
            or not isinstance(value.get("plants"), dict)):
        raise ValueError("Unsupported custom plant library; left unchanged")
    for key, record in value["plants"].items():
        if not isinstance(key, str) or not isinstance(record, dict):
            raise ValueError("Malformed custom plant library; left unchanged")
        PlantSpeciesData.from_dict(record)
    return value, original


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".ogp-local-plants-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def install_collections(
    app_data: Path, collections: list[tuple[str, str, list[PlantSpeciesData]]],
) -> dict[str, Any]:
    """Merge into custom search and standard lists; caller must close the app.

    Collection keys are explicit installation markers. Reusing a key preserves
    the existing collection including user removals. Use a new key for a new batch.
    """
    library_path, lists_path = app_data / "custom_plants.json", app_data / "plant_lists.json"
    library, original_library = _custom_store(library_path)
    original_lists = _validate_existing(lists_path)
    store = PlantListStore(lists_path)
    markers = {item.description for item in store.all_lists()}
    pending = []
    added = 0
    seen_collections = set()
    for key, name, plants in collections:
        if not key.strip() or key in seen_collections:
            raise ValueError("Collection keys must be nonempty and unique")
        seen_collections.add(key)
        marker = f"Import local OGP v1/{key}"
        entries = []
        for plant in plants:
            record = plant.to_dict()
            # Check serialization before any file is touched.
            json.dumps(record, allow_nan=False)
            identity = species_key(record)
            if identity == "_unknown":
                raise ValueError("Plant identity is missing")
            if identity not in library["plants"]:
                library["plants"][identity] = record
                added += 1
            snapshot = PlantSpeciesData.from_dict(library["plants"][identity])
            entries.append(PlantListEntry(species=snapshot))
        if marker not in markers:
            pending.append(PlantList(name=name, description=marker, entries=entries))
    backups = []
    app_data.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    for path, original, changes in (
        (library_path, original_library, added), (lists_path, original_lists, len(pending)),
    ):
        if original is not None and changes:
            backup = path.with_name(path.stem + f".before-local-import-{stamp}.json")
            with backup.open("xb") as stream:
                stream.write(original)
            backups.append(str(backup))
    if added:
        _atomic_json(library_path, library)
    installed = []
    for item in pending:
        imported = store.import_list_from_json(json.dumps({"list": item.to_dict()}, ensure_ascii=False))
        installed.append({"name": imported.name, "count": len(imported.entries)})
    return {"added_to_search": added, "collections": installed,
            "skipped_collections": len(collections) - len(pending), "backups": backups}
