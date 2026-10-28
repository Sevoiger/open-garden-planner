"""Discoverable JSON contracts for prepared document and library operations.

The validators remain authoritative for business rules. These schemas describe
their accepted inputs for MCP clients, and the document batch validator reuses
the field names below where it owns the corresponding allowlist.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import fields as dataclass_fields
from enum import Enum
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

_UUID = "00000000-0000-4000-8000-000000000001"
_BED_UUID = "00000000-0000-4000-8000-000000000002"
_EVENT_UUID = "00000000-0000-4000-8000-000000000003"
_RECORD_UUID = "00000000-0000-4000-8000-000000000004"
_NOTE_UUID = "00000000-0000-4000-8000-000000000005"


def _field(
    type_name: str | list[str],
    example: Any,
    *,
    description: str | None = None,
    enum: list[str] | None = None,
    items: dict[str, Any] | None = None,
    properties: dict[str, Any] | None = None,
    additional_properties: bool | None = None,
    minimum: int | float | None = None,
    maximum: int | float | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"type": type_name, "example": example}
    if description:
        result["description"] = description
    if enum is not None:
        result["enum"] = enum
    if items is not None:
        result["items"] = items
    if properties is not None:
        result["properties"] = properties
    if additional_properties is not None:
        result["additionalProperties"] = additional_properties
    if minimum is not None:
        result["minimum"] = minimum
    if maximum is not None:
        result["maximum"] = maximum
    return result


_DATE = _field(
    ["string", "null"], "2025-04-12",
    description="ISO YYYY-MM-DD; empty string or null means unknown when accepted.",
)
_UUID_STRING = _field("string", _UUID, description="Stable object or record UUID.")
_PERIOD = _field(
    ["object", "string", "null"], "2025-04",
    description="Partial date string or {precision, value, label}; null/empty means unknown.",
    additional_properties=False,
    properties={
        "precision": _field("string", "month", enum=["day", "month", "season", "year", "unknown"]),
        "value": _field("string", "2025-04", description="Date matching precision; empty for unknown."),
        "label": _field("string", "printemps 2025", description="Optional original label."),
    },
)


def _op(
    name: str,
    *,
    fields: dict[str, dict[str, Any]] | None = None,
    required_fields: tuple[str, ...] = (),
    target_required: bool = False,
    fields_required: bool = False,
    field_additional_properties: bool = False,
    example: dict[str, Any],
    notes: str | None = None,
    any_of_fields: tuple[str, ...] = (),
    forbidden_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    field_properties = deepcopy(fields or {})
    field_schema: dict[str, Any] = {
        "type": "object",
        "properties": field_properties,
        "additionalProperties": field_additional_properties,
    }
    if required_fields:
        field_schema["required"] = list(required_fields)
    if any_of_fields:
        field_schema["anyOf"] = [{"required": [key]} for key in any_of_fields]
    if forbidden_fields:
        field_schema["not"] = {"anyOf": [{"required": [key]} for key in forbidden_fields]}

    properties: dict[str, Any] = {
        "operation": {"type": "string", "enum": [name]},
        "target_id": _field(
            "string" if target_required else ["string", "null"],
            _UUID if target_required else None,
            description=("Required for this operation." if target_required
                         else "Omit when this operation does not need an explicit target id."),
        ),
        "fields": field_schema,
    }
    required = ["operation"]
    if target_required:
        required.append("target_id")
    if fields_required:
        required.append("fields")

    result: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
        "example": deepcopy(example),
    }
    if notes:
        result["description"] = notes
    return result


_TASK_FIELDS = {
    "title": _field("string", "Planter les laitues", description="Nonempty task title."),
    "date": _DATE,
    "notes": _field("string", "Semis sous abri."),
    "bed_id": _field(["string", "null"], _BED_UUID, description="Optional existing bed UUID."),
}
_HARVEST_FIELDS = {
    "id": _UUID_STRING,
    "date": _DATE,
    "quantity": _field("number", 2.4, description="Measured quantity; zero is valid, unknown is refused.", minimum=0),
    "unit": _field("string", "kg", description="Explicit unit label."),
    "quality": _field("string", "good"),
    "notes": _field("string", "Récolte mûre."),
    "photo_path": _field(["string", "null"], "media/recolte.jpg"),
    "journal_note_id": _UUID_STRING,
    "species_key": _field("string", "malus domestica", description="Optional journal context."),
    "species_name": _field("string", "Pommier"),
    "journal_note_text": _field("string", "Récolte du jour."),
    "record_id": _UUID_STRING,
}
_PEST_FIELDS = {
    "id": _UUID_STRING,
    "date": _DATE,
    "entry_type": _field("string", "pest", enum=["pest", "disease"]),
    "name": _field("string", "Pucerons"),
    "severity": _field("string", "low", enum=["low", "medium", "high"]),
    "treatment": _field("string", "Pulvérisation d'eau."),
    "notes": _field("string", "Observation sur les jeunes pousses."),
    "photo_path": _field(["string", "null"], "media/feuille.jpg"),
    "resolved": _field("boolean", False),
    "record_id": _UUID_STRING,
}
_NOTE_FIELDS = {
    "id": _UUID_STRING,
    "date": _DATE,
    "text": _field("string", "Vérifier la reprise."),
    "photo_path": _field(["string", "null"], "media/note.jpg"),
    "tags": _field("array", ["suivi"], items={"type": "string"}),
}


DOCUMENT_OPERATION_CONTRACTS: dict[str, dict[str, Any]] = {
    "add_manual_task": _op(
        "add_manual_task", fields=_TASK_FIELDS, required_fields=("title",), fields_required=True,
        example={"operation": "add_manual_task", "fields": {"title": "Planter les laitues", "date": "2025-04-12"}},
    ),
    "edit_manual_task": _op(
        "edit_manual_task", fields=_TASK_FIELDS, required_fields=("title",), target_required=True,
        fields_required=True,
        example={"operation": "edit_manual_task", "target_id": _UUID, "fields": {"title": "Planter les laitues", "notes": "Planche nord."}},
    ),
    "delete_manual_task": _op(
        "delete_manual_task", fields=_TASK_FIELDS, target_required=True,
        example={"operation": "delete_manual_task", "target_id": _UUID},
    ),
    "set_garden_card": _op(
        "set_garden_card", target_required=True,
        fields={
            "name": _field("string", "Pommier", description="Required when the existing shape has no label."),
            "kind": _field("string", "individual", enum=["individual", "group", "zone"]),
            "state": _field("string", "in_place", enum=["planned", "in_place", "dormant", "uncertain", "confirmed_dead", "removed_moved"]),
            "period": _PERIOD,
            "quantity": _field(["number", "null"], 1, minimum=0),
            "zone_id": _field(["string", "null"], _BED_UUID),
            "notes": _field("string", "Planté dans le jardin."),
            "botanical_name": _field("string", "Malus domestica"),
            "cultivar": _field("string", "Reine des Reinettes"),
            "provenance": _field("string", "Pépinière locale"),
            "depth_cm": _field(["number", "null"], 30, minimum=0),
            "media": _field("array", [], items={"type": "string"}),
        },
        example={"operation": "set_garden_card", "target_id": _UUID, "fields": {"name": "Pommier", "kind": "individual", "state": "in_place", "period": {"precision": "year", "value": "2022"}}},
        notes="Fields are partial; an existing object name can supply the label when name is omitted.",
    ),
    "add_plant_measurement": _op("add_plant_measurement", target_required=True, fields_required=True,
        fields={"date": _DATE, "height_cm": _field(["number", "null"], 120, minimum=0),
                "spread_cm": _field(["number", "null"], 80, minimum=0), "note": _field("string", "Measured")}, required_fields=('date',),
        example={'operation': 'add_plant_measurement', 'target_id': '00000000-0000-4000-8000-000000000001', 'fields': {'date': '2026-10-28', 'height_cm': 120}}),
    "edit_plant_measurement": _op("edit_plant_measurement", target_required=True, fields_required=True,
        fields={"date": _DATE, "height_cm": _field(["number", "null"], 120, minimum=0),
                "spread_cm": _field(["number", "null"], 80, minimum=0), "note": _field("string", "Measured"), "measurement_id": _UUID_STRING}, required_fields=('date', 'measurement_id'),
        example={'operation': 'edit_plant_measurement', 'target_id': '00000000-0000-4000-8000-000000000001', 'fields': {'date': '2026-10-28', 'height_cm': 120, 'measurement_id': '00000000-0000-4000-8000-000000000004'}}),
    "delete_plant_measurement": _op("delete_plant_measurement", target_required=True, fields_required=True,
        fields={"measurement_id": _UUID_STRING}, required_fields=('measurement_id',),
        example={'operation': 'delete_plant_measurement', 'target_id': '00000000-0000-4000-8000-000000000001', 'fields': {'measurement_id': '00000000-0000-4000-8000-000000000004'}}),
    "set_planting_date": _op(
        "set_planting_date", target_required=True, fields_required=True,
        fields={"planting_date": _PERIOD}, required_fields=("planting_date",),
        example={"operation": "set_planting_date", "target_id": _UUID, "fields": {"planting_date": "2025-04-12"}},
        notes="A null or empty planting_date preserves an unknown date; the target must be a plant.",
    ),
    "add_garden_intervention": _op(
        "add_garden_intervention", fields_required=True,
        fields={
            "period": _PERIOD,
            "type": _field("string", "observation", enum=["planting", "relocation", "amendment", "fertilisation", "mulching", "sowing", "pruning", "watering", "observation", "removal_loss"]),
            "status": _field("string", "done", enum=["planned", "done"]),
            "description": _field("string", "Feuilles jaunies observées.", description="Nonempty report."),
            "targets": _field("array", [{"id": _UUID, "name": "Pommier"}], items={"type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"}}, "required": ["id", "name"], "additionalProperties": False}),
            "material": _field("string", "Compost"),
            "quantity": _field(["number", "null"], 0, description="Null/absent means unknown; zero is measured zero.", minimum=0),
            "unit": _field("string", "kg"),
            "application": _field("string", "En surface"),
            "area_m2": _field(["number", "null"], 2.5, minimum=0),
            "depth_cm": _field(["number", "null"], 3, minimum=0),
            "media": _field("array", [], items={"type": "string"}),
        },
        required_fields=("type", "status", "description", "targets"),
        example={"operation": "add_garden_intervention", "fields": {"type": "observation", "status": "done", "description": "Feuilles jaunies observées.", "period": "2025-04", "targets": [{"id": _UUID, "name": "Pommier"}]}},
        notes="The batch-level idempotence request_key is supplied to apply_document_operations, not here.",
    ),
    "edit_garden_intervention": _op(
        "edit_garden_intervention", target_required=True,
        fields={
            "period": _PERIOD,
            "type": _field("string", "watering", enum=["planting", "relocation", "amendment", "fertilisation", "mulching", "sowing", "pruning", "watering", "observation", "removal_loss"]),
            "status": _field("string", "done", enum=["planned", "done"]),
            "description": _field("string", "Arrosage effectué."),
            "targets": _field("array", [{"id": _UUID, "name": "Pommier"}], items={"type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"}}, "required": ["id", "name"], "additionalProperties": False}),
            "material": _field("string", "Eau"), "quantity": _field(["number", "null"], 4, minimum=0),
            "unit": _field("string", "L"), "application": _field("string", "Au pied"),
            "area_m2": _field(["number", "null"], 1, minimum=0), "depth_cm": _field(["number", "null"], 0, minimum=0),
            "media": _field("array", [], items={"type": "string"}),
        },
        example={"operation": "edit_garden_intervention", "target_id": _EVENT_UUID, "fields": {"status": "done", "description": "Arrosage effectué."}},
        notes="target_id is the intervention id. Identity and creation timestamp are immutable.",
    ),
    "delete_garden_intervention": _op(
        "delete_garden_intervention", target_required=True, field_additional_properties=True,
        example={"operation": "delete_garden_intervention", "target_id": _EVENT_UUID},
        notes="target_id is the intervention id; fields, if present, are ignored.",
    ),
    "record_soil_test": _op(
        "record_soil_test", fields_required=True,
        fields={
            "ph": _field(["number", "null"], 6.5, minimum=0, maximum=14),
            **{
                f"{name}_level": _field(["integer", "null"], example, minimum=minimum, maximum=maximum)
                for name, example, minimum, maximum in (
                    ("n", 2, 0, 4), ("p", 2, 0, 4), ("k", 2, 1, 4),
                    ("ca", 1, 0, 2), ("mg", 1, 0, 2), ("s", 1, 0, 2),
                )
            },
            "soil_texture": _field(["string", "null"], "loamy", enum=["sandy", "loamy", "clayey", "compacted"]),
            "test_date": _field("string", "2025-04-12", description="Explicit, nonempty ISO date; a reading is required too."),
            "notes": _field("string", "Test après amendement."),
        },
        required_fields=("test_date",),
        any_of_fields=("ph", "n_level", "p_level", "k_level", "ca_level", "mg_level", "s_level"),
        example={"operation": "record_soil_test", "target_id": _BED_UUID, "fields": {"ph": 6.5, "n_level": 2, "test_date": "2025-04-12"}},
        notes="target_id is optional (defaults to global). Provide test_date and at least one measured ph/nutrient value; kit levels and ppm are separate.",
    ),
    "add_harvest_record": _op(
        "add_harvest_record", target_required=True, fields_required=True,
        fields=_HARVEST_FIELDS, required_fields=("quantity",), field_additional_properties=True,
        example={"operation": "add_harvest_record", "target_id": _UUID, "fields": {"date": "2025-09-18", "quantity": 2.4, "unit": "kg", "quality": "good"}},
        notes="Unknown record extension fields are preserved. Quantity must be measured; zero is valid, null is not.",
    ),
    "edit_harvest_record": _op(
        "edit_harvest_record", target_required=True, fields_required=True,
        fields=_HARVEST_FIELDS, required_fields=("id",), field_additional_properties=True,
        example={"operation": "edit_harvest_record", "target_id": _UUID, "fields": {"id": _RECORD_UUID, "quantity": 3.1}},
        notes="Unknown record extension fields are preserved; journal_note_id is managed by the planner.",
    ),
    "delete_harvest_record": _op(
        "delete_harvest_record", target_required=True, fields_required=True,
        fields={"record_id": _HARVEST_FIELDS["record_id"]}, required_fields=("record_id",),
        field_additional_properties=True,
        example={"operation": "delete_harvest_record", "target_id": _UUID, "fields": {"record_id": _RECORD_UUID}},
        notes="Only record_id is used; other extension fields are ignored on deletion.",
    ),
    "add_pest_log": _op(
        "add_pest_log", target_required=True, fields=_PEST_FIELDS, field_additional_properties=True,
        example={"operation": "add_pest_log", "target_id": _UUID, "fields": {"entry_type": "pest", "name": "Pucerons", "severity": "low", "date": "2025-05-20"}},
        notes="Unknown record extension fields are preserved; omitted fields use the model defaults.",
    ),
    "edit_pest_log": _op(
        "edit_pest_log", target_required=True, fields_required=True,
        fields=_PEST_FIELDS, required_fields=("id",), field_additional_properties=True,
        example={"operation": "edit_pest_log", "target_id": _UUID, "fields": {"id": _RECORD_UUID, "resolved": True}},
        notes="Unknown record extension fields are preserved.",
    ),
    "delete_pest_log": _op(
        "delete_pest_log", target_required=True, fields_required=True,
        fields={"record_id": _PEST_FIELDS["record_id"]}, required_fields=("record_id",),
        field_additional_properties=True,
        example={"operation": "delete_pest_log", "target_id": _UUID, "fields": {"record_id": _RECORD_UUID}},
        notes="Only record_id is used; other extension fields are ignored on deletion.",
    ),
    "add_journal_note": _op(
        "add_journal_note", fields=_NOTE_FIELDS, field_additional_properties=True,
        forbidden_fields=("scene_x", "scene_y", "position", "layer_id"),
        example={"operation": "add_journal_note", "fields": {"date": "2025-05-20", "text": "Reprise observée.", "tags": ["suivi"]}},
        notes="Unknown note extension fields are preserved. Qt-owned position fields are not accepted here.",
    ),
    "edit_journal_note": _op(
        "edit_journal_note", target_required=True, fields=_NOTE_FIELDS, field_additional_properties=True,
        forbidden_fields=("scene_x", "scene_y", "position", "layer_id"),
        example={"operation": "edit_journal_note", "target_id": _NOTE_UUID, "fields": {"text": "Reprise confirmée."}},
        notes="Unknown note extension fields are preserved. Qt-owned position fields are not accepted here.",
    ),
    "delete_journal_note": _op(
        "delete_journal_note", target_required=True, field_additional_properties=True,
        example={"operation": "delete_journal_note", "target_id": _NOTE_UUID},
        notes="fields, if present, are ignored on deletion.",
    ),
    "set_manual_task_status": _op(
        "set_manual_task_status", target_required=True, fields_required=True,
        fields={
            "status": _field("string", "done", enum=["open", "done", "dismissed"]),
            "done_date": _DATE,
            "snooze_until": _DATE,
        },
        required_fields=("status",),
        example={"operation": "set_manual_task_status", "target_id": _UUID, "fields": {"status": "done", "done_date": "2025-04-12"}},
        notes="Only existing manual tasks can receive stored status; generated tasks are derived.",
    ),
    "set_succession_plan": _op(
        "set_succession_plan", target_required=True, fields_required=True,
        fields={
            "year": _field("integer", 2025),
            "entries": _field("array", [{"species_key": "allium sativum", "start_date": "2025-03-01", "end_date": "2025-04-30"}], items={
                "type": "object", "properties": {
                    "id": _UUID_STRING,
                    "species_key": _field("string", "allium sativum", description="Canonical lowercased species name or other plan-known key."),
                    "common_name": _field("string", "Ail"),
                    "scientific_name": _field("string", "Allium sativum"),
                    "start_date": _field("string", "2025-03-01"),
                    "end_date": _field("string", "2025-04-30"),
                    "notes": _field("string", "Rotation après les haricots."),
                }, "required": ["species_key", "start_date", "end_date"], "additionalProperties": True,
            }),
        },
        required_fields=("entries", "year"),
        example={"operation": "set_succession_plan", "target_id": _BED_UUID, "fields": {"year": 2025, "entries": [{"species_key": "allium sativum", "start_date": "2025-03-01", "end_date": "2025-04-30"}]}},
        notes="Entries may be empty to remove the plan. Overlapping dates are refused.",
    ),
}


HARVEST_RECORD_FIELDS = frozenset({
    "id", "date", "quantity", "unit", "quality", "notes", "photo_path", "journal_note_id",
})
PEST_LOG_FIELDS = frozenset({
    "id", "date", "entry_type", "name", "severity", "treatment", "notes", "photo_path", "resolved",
})
JOURNAL_NOTE_FIELDS = frozenset({
    "id", "date", "text", "photo_path", "scene_x", "scene_y", "tags",
})


def document_operation_schemas(operations: tuple[str, ...] | None = None) -> dict[str, Any]:
    """Return detached per-operation schemas in the service's supported order."""
    names = tuple(DOCUMENT_OPERATION_CONTRACTS) if operations is None else operations
    return {name: deepcopy(DOCUMENT_OPERATION_CONTRACTS[name]) for name in names}


def document_operation_field_names(name: str) -> set[str]:
    """Return the field keys described by one operation contract."""
    return set(DOCUMENT_OPERATION_CONTRACTS[name]["properties"]["fields"]["properties"])


def _json_schema_for_type(annotation: Any) -> dict[str, Any]:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (Union, UnionType):
        branches = [_json_schema_for_type(arg) for arg in args]
        simple_types: list[str] = []
        enum_values: list[Any] = []
        can_flatten = True
        for branch in branches:
            branch_type = branch.get("type")
            if isinstance(branch_type, list):
                simple_types.extend(branch_type)
            elif isinstance(branch_type, str):
                simple_types.append(branch_type)
            else:
                can_flatten = False
            enum_values.extend(branch.get("enum", []))
            if set(branch) - {"type", "enum"}:
                can_flatten = False
        if can_flatten:
            result: dict[str, Any] = {"type": list(dict.fromkeys(simple_types))}
            if enum_values:
                if "null" in simple_types:
                    enum_values.append(None)
                result["enum"] = list(dict.fromkeys(enum_values))
            return result
        return {"anyOf": branches}
    if origin is list:
        return {"type": "array", "items": _json_schema_for_type(args[0]) if args else {}}
    if origin is dict:
        return {"type": "object", "additionalProperties": True}
    if annotation is type(None):
        return {"type": "null"}
    if annotation is Any:
        return {}
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return {"type": "string", "enum": [member.value for member in annotation]}
    if annotation is str:
        return {"type": "string"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    return {}


def plant_library_import_schema() -> dict[str, Any]:
    """Describe the persisted plant-record rows accepted by a library import."""
    from open_garden_planner.models.plant_data import PlantSpeciesData
    from open_garden_planner.services.library_operations import (
        _ENUM_FIELDS,
        _INTEGER_FIELDS,
        _NUMBER_FIELDS,
        _OPTIONAL_STRING_VALUES,
        _RANGE_PAIRS,
    )

    hints = get_type_hints(PlantSpeciesData)
    properties = {
        field.name: _json_schema_for_type(hints[field.name])
        for field in dataclass_fields(PlantSpeciesData)
    }
    for name, enum_type in _ENUM_FIELDS.items():
        properties[name] = _json_schema_for_type(enum_type)
    for name, allowed_values in _OPTIONAL_STRING_VALUES.items():
        properties[name] = {
            "type": ["string", "null"],
            "enum": [*sorted(allowed_values), None],
        }
    for name in _NUMBER_FIELDS:
        if name.startswith(("min_height_", "max_height_", "min_spread_", "max_spread_", "seed_depth_")):
            properties[name]["minimum"] = 0
        elif name in {"ph_min", "ph_max"}:
            properties[name].update(minimum=0, maximum=14)
        elif name == "min_germination_temp_c":
            properties[name]["minimum"] = -273.15
    for name in _INTEGER_FIELDS:
        if name.startswith("hardiness_zone_"):
            properties[name].update(minimum=0, maximum=13)
        elif name.startswith(("days_to_", "prick_out_", "harden_off_")):
            properties[name]["minimum"] = 0
    for lower, upper in _RANGE_PAIRS:
        existing_description = properties[lower].get("description", "")
        properties[lower]["description"] = (
            f"{existing_description} " if existing_description else ""
        ) + f"Must not exceed {upper} when both bounds are supplied."
    properties["source_id"] = {
        "type": "string", "minLength": 1,
        "description": "Stable nonempty source record id; required unless id alias is supplied.",
    }
    properties["id"] = {"type": "string", "minLength": 1, "description": "Alias for source_id."}
    example = {
        "source_id": "perenual:12345",
        "data_source": "perenual",
        "scientific_name": "Malus domestica",
        "common_name": "Pommier",
        "cycle": "perennial",
        "sun_requirement": "full_sun",
        "water_needs": "medium",
    }
    return {
        "type": "object",
        "properties": properties,
        "required": ["scientific_name", "common_name"],
        "anyOf": [{"required": ["source_id"]}, {"required": ["id"]}],
        "additionalProperties": False,
        "example": example,
        "description": (
            "A PlantSpeciesData record. Successful rows need an explicit nonempty source_id "
            "or id alias; scientific_name and common_name must be strings. New or changed "
            "top-level fields are closed to this canonical schema; use raw_data for provider extensions."
        ),
    }
