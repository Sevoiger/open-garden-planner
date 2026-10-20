"""Qt-free illustration registry and conservative catalog presentation groups.

Artwork choices never modify recorded botanical traits. Unknown plants remain
available in the complete catalog; height alone is not evidence of growth form.
"""

import json
import types
from copy import deepcopy
from dataclasses import fields
from functools import lru_cache
from pathlib import Path
from typing import Any, get_args, get_origin

from open_garden_planner.core.plan_io import validate_plan_data
from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services.permaculture import matches_traits


@lru_cache(maxsize=1)
def illustrations() -> tuple[dict[str, Any], ...]:
    path = Path(__file__).parents[1] / "resources/plants/illustrations.json"
    return tuple(json.loads(path.read_text(encoding="utf-8"))["illustrations"])


def normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _field_matches(value: Any, annotation: Any) -> bool:
    if annotation is Any:
        return True
    origin, arguments = get_origin(annotation), get_args(annotation)
    if origin is types.UnionType:
        return any(_field_matches(value, option) for option in arguments)
    if origin is list:
        return isinstance(value, list) and all(_field_matches(v, arguments[0]) for v in value)
    if origin is dict:
        return isinstance(value, dict)
    if annotation is float:
        return type(value) in (int, float)
    if annotation in (int, bool, str):
        return type(value) is annotation
    return isinstance(value, annotation)


def validate_catalog_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    """Enforce model field types and the existing save-file finite-number gate."""
    plant = PlantSpeciesData.from_dict(data)
    for field in fields(plant):
        if not _field_matches(getattr(plant, field.name), field.type):
            raise ValueError(f"Invalid plant field: {field.name}")
    snapshot = {**deepcopy(data), **plant.to_dict()}
    validate_plan_data({"plant_species": snapshot})
    return snapshot


def catalog_name_rank(record: dict[str, Any], text: str) -> int:
    needle = normalized(text)
    raw = record.get("raw_data") or {}
    if not isinstance(raw, dict):
        raw = {}
    aliases = record.get("aliases") or []
    raw_aliases = raw.get("aliases") or []
    names = [record.get("common_name"), record.get("scientific_name"), raw.get("name_en"),
             *(aliases if isinstance(aliases, list) else []),
             *(raw_aliases if isinstance(raw_aliases, list) else [])]
    names = [normalized(n) for n in names if isinstance(n, str)]
    return 0 if needle in names else 1 if any(n.startswith(needle) for n in names) else 2


@lru_cache(maxsize=1)
def _art_indexes() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    exact: dict[str, dict[str, Any]] = {}
    genera: dict[str, dict[str, Any]] = {}
    for row in illustrations():
        for alias in (row["id"].replace("_", " "), *row["aliases"]):
            exact.setdefault(normalized(alias), row)
        # New entries' English labels are botanical names. Genus fallback is
        # only enabled for explicit genus illustrations, never for species.
        latin = row["name_en"]
        if "recipe" in row and " " not in latin:
            genera[normalized(latin)] = row
    return exact, genera


def illustration_for(record: dict[str, Any]) -> dict[str, Any] | None:
    exact, genera = _art_indexes()
    raw = record.get("raw_data") or {}
    if not isinstance(raw, dict):
        raw = {}
    chosen = raw.get("plan_illustration_id")
    if isinstance(chosen, str):
        match = next((row for row in illustrations() if row["id"] == chosen), None)
        if match is not None:
            return match
    aliases = record.get("aliases")
    raw_aliases = raw.get("aliases")
    names = [record.get("scientific_name", ""), record.get("common_name", ""),
             raw.get("name_en", ""), raw.get("label_fr", ""),
             *(aliases if isinstance(aliases, list) else []),
             *(raw_aliases if isinstance(raw_aliases, list) else [])]
    for name in names:
        if isinstance(name, str) and normalized(name) in exact:
            return exact[normalized(name)]
    scientific = normalized(record.get("scientific_name") or "")
    words = scientific.split()
    # A named cultivar can share its species/genus drawing without acquiring
    # that drawing's identity or traits. Prefer the longest botanical prefix.
    for end in range(len(words) - 1, 0, -1):
        parent = exact.get(" ".join(words[:end]))
        if parent is not None:
            return parent
    return genera.get(scientific.split(" ")[0]) if scientific else None


def palette_group(record: dict[str, Any]) -> str | None:
    """Use a recorded layer or known illustration; never infer from height."""
    layer = record.get("permaculture_layer")
    if layer in ("canopy", "sub_canopy"):
        return "tree"
    if layer == "shrub":
        return "shrub"
    art = illustration_for(record)
    if art is not None:
        return str(art["group"])
    if layer in ("herbaceous", "ground_cover", "vine"):
        return "flower"
    if layer == "root":
        return "vegetable"
    return None


def catalog_matches(record: dict[str, Any], text: str = "", *, group: str | None = None,
                    layers: set[str] | None = None, functions: set[str] | None = None) -> bool:
    return matches_traits(
        record, text, layers=layers, functions=functions,
    ) and (group is None or palette_group(record) == group)
