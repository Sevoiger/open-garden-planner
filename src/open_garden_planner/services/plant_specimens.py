"""Plant records without geometry, owned by the project and its single history."""
from copy import deepcopy
from uuid import UUID, uuid4

from open_garden_planner.models.garden_memory import PLANT_STATES, optional_number, period

CULTIVATION_MODES = ("unknown", "ground", "pot", "bottomless", "other")
RELATIONS = ("study", "wanted", "ordered", "owned", "archived")


def specimen_record(document, identity):
    """Resolve exactly one plant, including nested plan objects, without guessing."""
    matches = []
    def walk(objects):
        for record in objects:
            if record.get("item_id", record.get("id")) == identity:
                matches.append(record)
            walk(record.get("children", []))

    walk(document.get("objects", []))
    record = document.get("plant_specimens", {}).get(identity)
    if record is not None:
        matches.append(record)
    if len(matches) != 1:
        raise ValueError("Specimen identity is absent or ambiguous")
    return matches[0]


def prepare_personal_specimen_fields(document, identity, fields):
    """Patch personal facts, reusing the single planting-period preparation.

    Only submitted fields are owned by this edit. Botanical snapshots, media,
    measurements and future extensions remain unchanged.
    """
    from open_garden_planner.services.garden_operations import (
        _editable,
        _records,
        _resolve,
        prepare_garden_operation,
    )

    if not isinstance(fields, dict):
        raise ValueError("Personal fields must be an object")
    allowed = {"name", "state", "planting_period", "relation", "cultivation_mode", "location",
               "acquisition_period", "acquisition_price", "acquisition_currency", "acquisition_supplier",
               "acquisition_receipt_media_id", "container", "substrate", "notes", "old_number", "alias", "nfc_tag"}
    if set(fields) - allowed:
        raise ValueError("Unknown personal specimen field")
    candidate = deepcopy(document)
    target, top_level, _canonical_id = _resolve(_records(candidate), identity)
    _editable(target, top_level, candidate)
    if "planting_period" in fields:
        candidate, _ = prepare_garden_operation(candidate, "set_planting_date",
                                                {"planting_date": fields["planting_period"]}, identity)
    record = specimen_record(candidate, identity)
    metadata = record.setdefault("metadata", {})
    instance = metadata.setdefault("plant_instance", {})
    if not isinstance(instance, dict):
        raise ValueError("Unrecognized specimen metadata")
    for key, value in fields.items():
        if key == "planting_period":
            continue
        if key == "name":
            if not isinstance(value, str) or not value.strip():
                raise ValueError("A specimen name is required")
            record["name"] = value.strip()
        elif key == "state":
            if value not in PLANT_STATES:
                raise ValueError("Unknown specimen state")
            metadata.setdefault("garden_tracking", {})["state"] = value
        elif key == "acquisition_period":
            period(instance.get("acquisition_period", instance.get("acquisition_date")))
            occurrence = period(value)
            instance[key] = occurrence
            if occurrence["precision"] == "day":
                instance["acquisition_date"] = occurrence["value"]
            else:
                instance.pop("acquisition_date", None)
        elif key == "acquisition_price":
            optional_number(instance.get(key), key)
            instance[key] = optional_number(value, key)
        elif key == "relation":
            if value not in RELATIONS:
                raise ValueError("Unknown catalogue relation")
            instance[key] = value
        elif key == "cultivation_mode":
            if value not in CULTIVATION_MODES:
                raise ValueError("Unknown cultivation mode")
            instance[key] = value
        elif key == "acquisition_receipt_media_id":
            if value is not None and not any(
                asset.get("id") == value and asset.get("kind") == "document"
                for asset in instance.get("media_assets", []) if isinstance(asset, dict)
            ):
                raise ValueError("Receipt must reference an attached document")
            instance[key] = value
        else:
            if key in instance and not isinstance(instance[key], str):
                raise ValueError("Stored personal field uses an unrecognized format")
            if not isinstance(value, str):
                raise ValueError("Personal specimen descriptions must be text")
            instance[key] = value
    return candidate


def validate_specimens(records):
    if not isinstance(records, dict):
        raise ValueError("Plant specimens must be an object")
    for identity, record in records.items():
        if str(UUID(identity)) != identity or not isinstance(record, dict):
            raise ValueError("Invalid specimen identity")
        if record.get("item_id") != identity or record.get("object_type") not in {"TREE", "SHRUB", "PERENNIAL"}:
            raise ValueError("Invalid specimen record")
        if not isinstance(record.get("name"), str) or not record["name"].strip():
            raise ValueError("A specimen name is required")
        metadata = record.get("metadata")
        if not isinstance(metadata, dict) or not isinstance(metadata.get("plant_instance"), dict):
            raise ValueError("Invalid specimen metadata")
        instance = metadata["plant_instance"]
        if instance.get("cultivation_mode", "unknown") not in CULTIVATION_MODES:
            raise ValueError("Invalid cultivation mode")
        if instance.get("relation", "owned") not in RELATIONS:
            raise ValueError("Invalid catalogue relation")
        tracking = metadata.get("garden_tracking", {})
        if not isinstance(tracking, dict) or tracking.get("state", "uncertain") not in PLANT_STATES:
            raise ValueError("Invalid specimen state")
    return deepcopy(records)


def add_specimen(document, library_id, name, *, cultivation_mode="unknown", identity=None, species_data=None):
    """Create a plant without an object, retaining all project extension fields."""
    if not isinstance(library_id, str) or not library_id:
        raise ValueError("An exact botanical library id is required")
    result = deepcopy(document)
    records = validate_specimens(result.get("plant_specimens", {}))
    identity = identity or str(uuid4())
    if identity in records:
        raise ValueError("Specimen already exists")
    def check_objects(objects):
        for record in objects:
            if record.get("item_id", record.get("id")) == identity:
                raise ValueError("Specimen identity already belongs to a plan object")
            check_objects(record.get("children", []))

    check_objects(document.get("objects", []))
    records[identity] = {
        "item_id": identity, "name": name, "object_type": "PERENNIAL",
        "metadata": {
            "plant_instance": {"library_plant_id": library_id, "cultivation_mode": cultivation_mode,
                               "relation": "owned"},
            "garden_tracking": {"state": "planned", "kind": "individual", "period": period()},
        },
    }
    if species_data is not None:
        if not isinstance(species_data, dict):
            raise ValueError("Species snapshot must be an object")
        records[identity]["metadata"]["plant_species"] = deepcopy(species_data)
    result["plant_specimens"] = validate_specimens(records)
    return result, identity
