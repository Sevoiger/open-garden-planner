"""French common names and deliberately unspecified starter plant cards."""

import hashlib
import json
from pathlib import Path

from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services.bundled_species_db import get_species_db

_MANIFEST = Path(__file__).parents[1] / "resources/data/personal_catalog_fr.json"


def french_collections() -> list[tuple[str, str, list[PlantSpeciesData]]]:
    """Keep existing botanical data; generic labels assert no species or dimensions."""
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    groups: dict[str, list[PlantSpeciesData]] = {}
    for record in get_species_db().values():
        naming = manifest["translations"][record["common_name"]]
        plant = PlantSpeciesData.from_dict({**record, "common_name": naming["name_fr"],
                                            "data_source": "custom"})
        plant.raw_data["aliases"] = record.get("aliases", [])
        plant.raw_data["name_en"] = record["common_name"]
        plant.raw_data["origin"] = "catalogue embarqué OGP ; nom usuel traduit en français"
        groups.setdefault(naming["group"], []).append(plant)
    result = [(f"catalogue-fr-v1/{key}", f"Catalogue FR — {key}", rows)
              for key, rows in groups.items()]
    for key, names in manifest["generic_groups"].items():
        plants = [PlantSpeciesData(
            scientific_name="", common_name=f"{name} — variété à préciser",
            source_id="generic-fr:" + hashlib.sha256(name.casefold().encode()).hexdigest()[:24],
            data_source="custom", description="Fiche de départ sans espèce ni variété identifiée. "
            "Renseigner le nom botanique et les caractéristiques une fois la plante identifiée.",
            raw_data={"identification": "unspecified", "label_fr": name},
        ) for name in names]
        result.append((f"generiques-fr-v1/{key}", f"À préciser — {key}", plants))
    return result


def localize_classic_lists(app_data: Path) -> int:
    """Translate only still-original English names in our earlier classic lists."""
    from datetime import UTC, datetime

    from open_garden_planner.models.plant_lists import PlantListStore
    from open_garden_planner.services.classic_plant_lists import _validate_existing

    path = app_data / "plant_lists.json"
    original = _validate_existing(path)
    if original is None:
        return 0
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    store = PlantListStore(path)
    changed = 0
    for collection in store.all_lists():
        if not collection.description.startswith("Sélection de départ OGP — classiques v1/"):
            continue
        for entry in collection.entries:
            naming = manifest["translations"].get(entry.species.common_name)
            if naming and naming["name_fr"] != entry.species.common_name:
                entry.species.common_name = naming["name_fr"]
                changed += 1
    if changed:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        backup = path.with_name(path.stem + f".before-french-names-{stamp}.json")
        with backup.open("xb") as stream:
            stream.write(original)
        store.save()
    return changed
