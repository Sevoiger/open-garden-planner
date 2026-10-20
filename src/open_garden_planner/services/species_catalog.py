"""Complete offline catalog shared by search, filters and guild authoring."""

from typing import Any

from open_garden_planner.models.plant_data import PlantSpeciesData, species_key
from open_garden_planner.services import plant_library
from open_garden_planner.services.bundled_species_db import get_species_db
from open_garden_planner.services.permaculture import matches_traits


def offline_catalog() -> dict[str, dict[str, Any]]:
    """Return bundled and custom snapshots; custom keys remain provider identities."""
    records = dict(get_species_db())
    for _, species in plant_library.get_plant_library().get_all_plants():
        snapshot = species.to_dict()
        # A French display name must not hide the previous English search name.
        english_name = species.raw_data.get("name_en")
        if isinstance(english_name, str) and english_name:
            aliases = species.raw_data.get("aliases", [])
            snapshot["aliases"] = [english_name, *(aliases if isinstance(aliases, list) else [])]
        records[species_key(snapshot)] = snapshot
    return records


def search_offline(text: str = "", *, layers: set[str] | None = None,
                   functions: set[str] | None = None, limit: int | None = None) -> list[PlantSpeciesData]:
    """Filter the entire catalog before imposing a presentation limit."""
    records = [PlantSpeciesData.from_dict(r) for r in offline_catalog().values()
               if matches_traits(r, text, layers=layers, functions=functions)]
    return records if limit is None else records[:limit]
