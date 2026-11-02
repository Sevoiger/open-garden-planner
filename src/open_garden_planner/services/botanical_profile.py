"""Structured optional botanical traits inside the existing species record.

Source payloads stay immutable. Explicit manual values, including a deliberate
blank, override a source value without losing its provenance.
"""
from copy import deepcopy

from open_garden_planner.models.plant_data import PlantSpeciesData

SOURCE_FIELDS = {
    "cultivar": "Cultivar", "species": "Espèce", "common_names": "Autres noms communs",
    "habit": "Port", "foliage_persistence": "Persistance feuillage",
    "hardiness": "Rusticité", "soil_ph": "pH du sol", "spacing": "Densité de plantation",
    "uses": "Type d'utilisation", "pruning_advice": "Descriptif taille",
    "exposures": "Exposition", "soil_moisture": "Humidité du sol",
    "climate": "Climat de préférence", "pollinators": "Plante mellifère",
    "propagation": "Mode de semis", "overwintering": "Hivernage",
}


def source_trait(plant: PlantSpeciesData, key: str) -> str:
    label = SOURCE_FIELDS.get(key)
    if label is None:
        return ""
    raw = plant.raw_data
    values = []
    for container, field in (("local_sheet", "characteristics"), ("promesse_de_fleurs", "fields")):
        record = raw.get(container)
        fields = record.get(field) if isinstance(record, dict) else None
        value = fields.get(label) if isinstance(fields, dict) else None
        candidates = [value] if isinstance(value, str) else value if isinstance(value, list) else []
        for item in candidates:
            if isinstance(item, str) and item.strip() and item not in values:
                values.append(item)
    return " · ".join(values)


def trait(plant: PlantSpeciesData, key: str) -> str:
    profile = plant.raw_data.get("botanical_profile", {})
    if isinstance(profile, dict) and key in profile:
        value = profile[key]
        return value if isinstance(value, str) else ""
    return source_trait(plant, key)


def update_traits(plant: PlantSpeciesData, changes: dict[str, str]) -> PlantSpeciesData:
    """Patch only edited traits, preserving unknown fields and original sources."""
    result = deepcopy(plant)
    profile = result.raw_data.get("botanical_profile", {})
    if not isinstance(profile, dict):
        raise ValueError("Botanical profile uses an unrecognized format")
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in changes.items()):
        raise ValueError("Botanical traits must be text")
    for key in changes:
        if key in profile and not isinstance(profile[key], str):
            raise ValueError("Botanical trait uses an unrecognized format")
    result.raw_data["botanical_profile"] = {**profile, **changes}
    return result
