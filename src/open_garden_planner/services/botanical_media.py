"""Reference media belong to the existing botanical-library record."""
from copy import deepcopy
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from open_garden_planner.models.garden_memory import validate_media
from open_garden_planner.services.plant_media import (
    edit_media_metadata,
    import_specimen_media,
    resolve_media,
)

REFERENCE_KINDS = {"reference", "illustration", "document"}


def _reference_paths(asset):
    paths = [asset.get("photo_path", "")]
    if "thumbnail_path" in asset:
        paths.append(asset["thumbnail_path"])
    validate_media(paths)
    for reference in paths:
        path = Path(reference)
        if ("\\" in reference or len(path.parts) < 3 or path.parts[0] != "botanical-media"):
            raise ValueError("Botanical references must stay in the dedicated media directory")
        try:
            UUID(path.parts[1])
        except ValueError as error:
            raise ValueError("Botanical media owner identity is invalid") from error
    return paths


def reference_assets(plant):
    if not isinstance(plant.raw_data, dict):
        raise ValueError("Unrecognized botanical source data")
    assets = plant.raw_data.get("reference_media", [])
    if not isinstance(assets, list) or any(
        not isinstance(asset, dict) or not isinstance(asset.get("id"), str)
        or not isinstance(asset.get("photo_path"), str) for asset in assets
    ):
        raise ValueError("Unrecognized botanical media collection")
    if len({asset["id"] for asset in assets}) != len(assets):
        raise ValueError("Botanical media identities are duplicated")
    for asset in assets:
        _reference_paths(asset)
    return deepcopy(assets)


def import_reference_media(source, library_path, library_id, *, kind="reference", **fields):
    """Copy one explicitly selected local file; no project or shape is created."""
    if kind not in REFERENCE_KINDS:
        raise ValueError("Personal photographs belong to specimens")
    if not isinstance(library_id, str) or not library_id:
        raise ValueError("An exact library identity is required")
    identity = str(uuid5(NAMESPACE_URL, "ogp:botanical-media:" + library_id))
    return import_specimen_media(source, Path(library_path), identity,
                                  folder="botanical-media", kind=kind, **fields)


def resolve_reference_media(library_path, reference):
    return resolve_media(Path(library_path), reference)


def edit_reference_media(plant, *, asset=None, remove_id=None, primary_id=None, update_id=None, fields=None):
    """Change associations in a copied species; originals remain on disk."""
    if asset is not None and (not isinstance(asset, dict) or asset.get("kind") not in REFERENCE_KINDS):
        raise ValueError("Personal photographs belong to specimens")
    if asset is not None:
        _reference_paths(asset)
    if fields is not None and not isinstance(fields, dict):
        raise ValueError("Reference media descriptions must be an object")
    if fields and "kind" in fields and fields["kind"] not in REFERENCE_KINDS:
        raise ValueError("Personal photographs belong to specimens")
    result = deepcopy(plant)
    # Reuse the shared descriptive media validator without introducing another
    # stored owner, catalogue or history. The temporary adapter is never saved.
    metadata = {"plant_instance": {"media_assets": reference_assets(plant)}}
    edited = edit_media_metadata(metadata, asset=asset, remove_id=remove_id,
                                 update_id=update_id, fields=fields)
    assets = edited["plant_instance"]["media_assets"]
    result.raw_data["reference_media"] = assets
    if result.raw_data.get("reference_primary_media_id") == remove_id:
        result.raw_data.pop("reference_primary_media_id", None)
    if primary_id is not None:
        if not any(record["id"] == primary_id and record.get("kind") == "reference" for record in assets):
            raise ValueError("The main botanical photo must be a reference photo")
        result.raw_data["reference_primary_media_id"] = primary_id
    selected = result.raw_data.get("reference_primary_media_id")
    if selected and update_id == selected and fields and "kind" in fields and fields["kind"] != "reference":
        result.raw_data.pop("reference_primary_media_id", None)
    return result


def primary_reference(plant):
    assets = reference_assets(plant)
    selected = plant.raw_data.get("reference_primary_media_id")
    return next((asset for asset in assets if asset["id"] == selected and asset.get("kind") == "reference"),
                next((asset for asset in assets if asset.get("kind") == "reference"), None))


def library_reference_paths(document):
    """Enumerate only declared botanical assets, never provider payloads or disk."""
    plants = document.get("plants", {}) if isinstance(document, dict) else None
    if not isinstance(plants, dict):
        raise ValueError("Invalid botanical library for media backup")
    seen = set()
    for record in plants.values():
        if not isinstance(record, dict) or not isinstance(record.get("raw_data", {}), dict):
            continue
        assets = record.get("raw_data", {}).get("reference_media", [])
        if not isinstance(assets, list):
            raise ValueError("Unrecognized botanical media collection")
        for asset in assets:
            if not isinstance(asset, dict) or not isinstance(asset.get("photo_path"), str):
                raise ValueError("Invalid botanical reference media")
            paths = _reference_paths(asset)
            for reference in paths:
                if reference not in seen:
                    seen.add(reference)
                    yield reference


def snapshot_reference_paths(snapshot):
    """Declared library-owned assets retained by botanical instance snapshots."""
    from open_garden_planner.services.garden_queries import garden_objects

    species = {}
    for index, record in enumerate(garden_objects(snapshot)):
        metadata = record.get("metadata", {})
        if not isinstance(metadata, dict):
            continue
        value = metadata.get("plant_species")
        if isinstance(value, dict):
            species[f"object-{index}"] = value
        instance = metadata.get("plant_instance", {})
        if isinstance(instance, dict) and isinstance(instance.get("species_data"), dict):
            species[f"instance-{index}"] = instance["species_data"]
    yield from library_reference_paths({"plants": species})
