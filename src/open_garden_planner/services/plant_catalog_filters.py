"""Read-only catalogue filters over the existing library and project records."""
from copy import deepcopy

from open_garden_planner.models.botanical_calendar import CALENDAR_ACTIVITIES
from open_garden_planner.services.botanical_calendar import calendar_for_species
from open_garden_planner.services.botanical_profile import trait
from open_garden_planner.services.garden_queries import garden_objects

CATALOG_RELATIONS = ("study", "wanted", "ordered", "owned", "archived")


def _mapping(value):
    return value if isinstance(value, dict) else {}


def catalogue_relations(plant):
    values = plant.raw_data.get("catalog_relations", [])
    return {value for value in values if isinstance(value, str)} if isinstance(values, list) else set()


def update_catalogue_relations(plant, selected):
    """Change known flags only; future flags and unrelated source data survive."""
    original = plant.raw_data.get("catalog_relations", [])
    if not isinstance(original, list) or any(not isinstance(value, str) for value in original):
        raise ValueError("Catalogue relations use an unrecognized format")
    if not set(selected).issubset(CATALOG_RELATIONS):
        raise ValueError("Unknown catalogue relation")
    result = deepcopy(plant)
    future = [value for value in original if value not in CATALOG_RELATIONS]
    result.raw_data["catalog_relations"] = future + [value for value in CATALOG_RELATIONS if value in selected]
    return result


def specimen_summaries(entries, snapshot=None):
    """Join only explicit library ids; a matching name is never evidence."""
    records = garden_objects(snapshot or {})
    by_id = {record.get("item_id"): record for record in records}
    summaries = {}
    for identity, plant in entries:
        relations = catalogue_relations(plant)
        linked = [record for record in records
                  if _mapping(_mapping(record.get("metadata")).get("plant_instance")).get("library_plant_id") == identity]
        modes, locations = set(), []
        has_personal_photo = False
        for record in linked:
            metadata = _mapping(record.get("metadata"))
            instance = _mapping(metadata.get("plant_instance"))
            tracking = _mapping(metadata.get("garden_tracking"))
            relation = instance.get("relation")
            if relation in CATALOG_RELATIONS:
                relations.add(relation)
            if tracking.get("state") in ("in_place", "dormant", "uncertain"):
                relations.add("present")
            mode = instance.get("cultivation_mode", "unknown")
            if isinstance(mode, str):
                modes.add(mode)
            locations.extend(str(value) for value in (record.get("name", ""), instance.get("location", "")) if value)
            zone_id = tracking.get("zone_id") or record.get("parent_bed_id")
            if isinstance(zone_id, str) and zone_id:
                locations.append(str(by_id.get(zone_id, {}).get("name", zone_id)))
            media = instance.get("media_assets", [])
            if isinstance(media, list):
                has_personal_photo |= any(isinstance(asset, dict) and asset.get("kind") == "personal" for asset in media)
        calendar = calendar_for_species(plant)
        incomplete = (not plant.scientific_name.strip()
                      or plant.max_height_cm is None or plant.max_spread_cm is None
                      or (plant.sun_requirement.value == "unknown" and not trait(plant, "exposures"))
                      or not any(period.months is not None for period in
                                 (calendar.planting, calendar.flowering, calendar.pruning)))
        summaries[identity] = {"count": len(linked), "relations": relations, "modes": modes,
                               "location": " ".join(locations).casefold(), "incomplete": incomplete,
                               "has_personal_photo": has_personal_photo}
    return summaries


def filter_catalogue(entries, summaries, *, category="", relation="", mode="", location="",
                     month=None, activity="", incomplete=False, without_personal_photo=False, sort="name"):
    result = []
    for identity, plant in entries:
        summary = summaries[identity]
        if category and trait(plant, "category").casefold() != category.casefold():
            continue
        if relation and relation not in summary["relations"]:
            continue
        if mode and mode not in summary["modes"]:
            continue
        if location.strip().casefold() not in summary["location"]:
            continue
        if incomplete and not summary["incomplete"]:
            continue
        if without_personal_photo and summary["has_personal_photo"]:
            continue
        if month is not None:
            calendar = calendar_for_species(plant)
            periods = [getattr(calendar, activity)] if activity in CALENDAR_ACTIVITIES else [
                getattr(calendar, key) for key in CALENDAR_ACTIVITIES]
            if not any(month in (period.months or ()) for period in periods):
                continue
        result.append((identity, plant))
    def key(entry):
        identity, plant = entry
        name = (plant.common_name or plant.scientific_name).casefold()
        primary = (plant.scientific_name.casefold() if sort == "botanical" else
                   trait(plant, "category").casefold() if sort == "category" else
                   -summaries[identity]["count"] if sort == "count" else name)
        return primary, name, identity
    return sorted(result, key=key)
