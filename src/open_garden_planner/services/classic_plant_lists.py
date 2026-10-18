"""Install reusable starter lists from the existing offline species catalog.

This is an explicit one-shot operation for a closed application, not startup
repopulation. User changes and deleted lists therefore remain under their control.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.models.plant_lists import PlantList, PlantListEntry, PlantListStore
from open_garden_planner.services.bundled_species_db import lookup_species

_MANIFEST = Path(__file__).parents[1] / "resources" / "data" / "classic_plant_lists.json"


def classic_lists() -> list[PlantList]:
    """Build standard importable snapshots, without inferring any new plant facts."""
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    result = []
    for group in manifest["lists"]:
        entries = []
        for name in group["plants"]:
            record = lookup_species(name)
            if not record:
                raise ValueError(f"Missing bundled starter species: {name}")
            species = PlantSpeciesData.from_dict({**record, "data_source": "bundled"})
            entries.append(PlantListEntry(species=species,
                                          note="Source : catalogue embarqué OGP (plant_species.json)."))
        result.append(PlantList(
            id=str(uuid5(NAMESPACE_URL, f"ogp-personal/classics/v1/{group['key']}")),
            name=group["name"],
            description=f"Sélection de départ OGP — classiques v1/{group['key']}. À adapter au jardin.",
            entries=entries,
        ))
    return result


def _validate_existing(path: Path) -> bytes | None:
    """Never let the store's permissive loader silently reset an unreadable library."""
    if not path.exists():
        return None
    original = path.read_bytes()
    payload = json.loads(original)
    if not isinstance(payload, dict) or payload.get("version") != "1.0" or not isinstance(payload.get("lists"), list):
        raise ValueError("Unsupported or malformed plant-list store; left unchanged")
    identities = set()
    for row in payload["lists"]:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or not isinstance(row.get("name"), str) or not isinstance(row.get("entries"), list)
                or row["id"] in identities):
            raise ValueError("Malformed or duplicate plant list; left unchanged")
        identities.add(row["id"])
        for entry in row["entries"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("species"), dict):
                raise ValueError("Malformed plant-list entry; left unchanged")
            PlantSpeciesData.from_dict(entry["species"])
        PlantList.from_dict(row)
    return original


def install_classics(path: Path) -> dict[str, Any]:
    """Merge through the existing atomic list importer; replay skips installed lists.

    The caller must close the application to avoid competing with its in-memory
    store. Existing file bytes are retained before the first actual import.
    A failed write is raised; an interrupted batch can safely be retried.
    """
    prepared = classic_lists()
    original = _validate_existing(path)
    store = PlantListStore(path)
    descriptions = {item.description for item in store.all_lists()}
    pending = [item for item in prepared if item.description not in descriptions]
    backup = None
    if pending and original is not None:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        backup = path.with_name(path.stem + f".before-classics-{stamp}.json")
        with backup.open("xb") as stream:
            stream.write(original)
    installed = []
    for item in pending:
        imported = store.import_list_from_json(json.dumps({
            "version": "1.0", "type": "open_garden_planner_plant_list", "list": item.to_dict(),
        }, ensure_ascii=False))
        installed.append({"id": imported.id, "name": imported.name, "count": len(imported.entries)})
    return {"path": str(path), "backup": str(backup) if backup else None,
            "installed": installed, "skipped": len(prepared) - len(pending)}


def export_classics(directory: Path) -> list[Path]:
    """Produce portable files understood by Plant Lists > Import."""
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, item in enumerate(classic_lists(), 1):
        path = directory / f"classiques-{index}.json"
        with path.open("x", encoding="utf-8") as stream:
            json.dump({"version": "1.0", "type": "open_garden_planner_plant_list", "list": item.to_dict()},
                      stream, ensure_ascii=False, indent=2)
        paths.append(path)
    return paths
