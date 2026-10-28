"""Transactional operations for the personal botanical library.

This module reads and writes only the explicit path passed to
``LibraryOperations``. Preview tokens are process-local; committed receipts and
versioned undo data are stored beside the plant records in the same JSON file.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import tempfile
import threading
import time
import unicodedata
import uuid
from collections import OrderedDict
from copy import deepcopy
from dataclasses import fields
from pathlib import Path
from typing import Any

from open_garden_planner.core.file_lock import ProcessFileLock
from open_garden_planner.models.plant_data import (
    FlowerType,
    GrowthRate,
    PermacultureLayer,
    PlantCycle,
    PlantFunction,
    PlantSpeciesData,
    PollinationType,
    SunRequirement,
    WaterNeeds,
)

_MISSING_FINGERPRINT = hashlib.sha256(b"ogp:missing-library-file").hexdigest()
_OPERATIONS_KEY = "_ogp_library_operations"
_OPERATIONS_VERSION = 1
_MAX_BATCH_SIZE = 100
_MAX_PREPARATIONS = 128
_PREPARATION_TTL_SECONDS = 300
_MAX_REQUEST_BYTES = 1_000_000
_MAX_PREVIEW_BYTES = 2_000_000
_MAX_RECEIPTS = 10_000
_MAX_TRANSACTIONS = 10_000
_MAX_OPERATION_HISTORY_BYTES = 8_000_000

_SPECIES_FIELDS = frozenset(field.name for field in fields(PlantSpeciesData))
_SPECIES_FIELDS_WITH_ID_ALIAS = _SPECIES_FIELDS | {"id"}
_ENUM_FIELDS = {
    "cycle": PlantCycle,
    "growth_rate": GrowthRate,
    "permaculture_layer": PermacultureLayer,
    "flower_type": FlowerType,
    "pollination_type": PollinationType,
    "sun_requirement": SunRequirement,
    "water_needs": WaterNeeds,
}
_STRING_FIELDS = frozenset(
    {
        "scientific_name",
        "common_name",
        "family",
        "genus",
        "soil_type",
        "flower_color",
        "foliage_color",
        "foliage_texture",
        "image_url",
        "thumbnail_url",
        "data_source",
        "source_id",
        "slug",
        "description",
    }
)
_OPTIONAL_STRING_VALUES = {
    "frost_tolerance": frozenset({"hardy", "half-hardy", "tender"}),
    "nutrient_demand": frozenset({"heavy", "medium", "light", "fixer"}),
    "n_demand": frozenset({"high", "medium", "low", "fixer"}),
    "p_demand": frozenset({"high", "medium", "low", "fixer"}),
    "k_demand": frozenset({"high", "medium", "low", "fixer"}),
}
_NUMBER_FIELDS = frozenset(
    {
        "min_height_cm",
        "max_height_cm",
        "min_spread_cm",
        "max_spread_cm",
        "ph_min",
        "ph_max",
        "min_germination_temp_c",
        "seed_depth_cm",
    }
)
_INTEGER_FIELDS = frozenset(
    {
        "hardiness_zone_min",
        "hardiness_zone_max",
        "indoor_sow_start",
        "indoor_sow_end",
        "direct_sow_start",
        "direct_sow_end",
        "transplant_start",
        "transplant_end",
        "harvest_start",
        "harvest_end",
        "days_to_germination_min",
        "days_to_germination_max",
        "days_to_maturity_min",
        "days_to_maturity_max",
        "prick_out_after_days",
        "harden_off_days",
    }
)
_RANGE_PAIRS = (
    ("min_height_cm", "max_height_cm"),
    ("min_spread_cm", "max_spread_cm"),
    ("ph_min", "ph_max"),
    ("hardiness_zone_min", "hardiness_zone_max"),
    ("days_to_germination_min", "days_to_germination_max"),
    ("days_to_maturity_min", "days_to_maturity_max"),
)
_VALIDATED_SPECIES_FIELDS = (
    set(_ENUM_FIELDS)
    | _STRING_FIELDS
    | set(_OPTIONAL_STRING_VALUES)
    | _NUMBER_FIELDS
    | _INTEGER_FIELDS
    | {"edible", "flowering", "functions", "edible_parts", "raw_data"}
)
if _SPECIES_FIELDS != _VALIDATED_SPECIES_FIELDS:
    raise RuntimeError(
        "LibraryOperations botanical validation must cover every PlantSpeciesData field; "
        f"uncovered fields: {sorted(_SPECIES_FIELDS ^ _VALIDATED_SPECIES_FIELDS)}"
    )


class LibraryOperationError(RuntimeError):
    """Base error for safe personal-library operations."""


class LibraryFileError(LibraryOperationError):
    """The existing library file cannot be read or interpreted safely."""


class LibraryPersistenceError(LibraryOperationError):
    """A library write failed before a successful commit was confirmed."""


class LibraryConflictError(LibraryOperationError):
    """The library changed since preparation or an undo precondition is stale."""


class LibraryValidationError(LibraryOperationError):
    """The requested batch contains invalid or conflicting rows."""


class PreparationExpiredError(LibraryOperationError):
    """A preview token is unknown or has expired from this process's cache."""


def _fingerprint(raw: bytes | None) -> str:
    if raw is None:
        return _MISSING_FINGERPRINT
    return hashlib.sha256(raw).hexdigest()


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise LibraryValidationError(f"Library data is not valid JSON: {exc}") from exc


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _read_path_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise LibraryFileError(f"Cannot read plant library {path}: {exc}") from exc


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def read_library_document(path: Path) -> tuple[dict[str, Any], bytes | None, str]:
    """Read a complete library document while retaining every unknown field.

    Missing files are represented by the existing version-1 empty shape. A
    present but unreadable, corrupt, or structurally unsafe file raises instead
    of being treated as an empty library.
    """
    path = Path(path)
    raw = _read_path_bytes(path)
    if raw is None:
        return {"version": "1.0", "plants": {}}, None, _MISSING_FINGERPRINT
    try:
        document = json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise LibraryFileError(f"Plant library is not valid UTF-8 JSON: {path}") from exc

    if not isinstance(document, dict):
        raise LibraryFileError("Plant library root must be a JSON object")
    plants = document.get("plants", {})
    if not isinstance(plants, dict):
        raise LibraryFileError("Plant library 'plants' value must be an object")
    # A missing plants key is accepted as an old empty document, but every
    # other top-level key remains byte-for-byte equivalent at the value level.
    # json.loads owns this entire tree. No caller or store can hold aliases to
    # it yet, so cloning the document and then its plants duplicates work.
    document["plants"] = plants
    _read_operations(document)
    return document, raw, _fingerprint(raw)


def _read_operations(document: dict[str, Any]) -> dict[str, Any]:
    """Return checked transaction metadata without changing the document."""
    if _OPERATIONS_KEY not in document:
        return {
            "version": _OPERATIONS_VERSION,
            "requests": {},
            "transactions": {},
            "record_versions": {},
        }
    operations = document[_OPERATIONS_KEY]
    if not isinstance(operations, dict):
        raise LibraryFileError(f"Plant library '{_OPERATIONS_KEY}' must be an object")
    version = operations.get("version", _OPERATIONS_VERSION)
    if isinstance(version, bool) or version != _OPERATIONS_VERSION:
        raise LibraryFileError("Unsupported plant-library operation history version")
    requests = operations.get("requests", {})
    transactions = operations.get("transactions", {})
    record_versions = operations.get("record_versions", {})
    if (
        not isinstance(requests, dict)
        or not isinstance(transactions, dict)
        or not isinstance(record_versions, dict)
    ):
        raise LibraryFileError(
            "Plant-library receipts, transactions, and record versions must be objects"
        )
    if len(requests) > _MAX_RECEIPTS or len(transactions) > _MAX_TRANSACTIONS:
        raise LibraryFileError("Plant-library operation history exceeds its row limit")
    try:
        history_size = len(_canonical_json(operations))
    except LibraryValidationError as exc:
        raise LibraryFileError("Plant-library operation history is not valid JSON") from exc
    if history_size > _MAX_OPERATION_HISTORY_BYTES:
        raise LibraryFileError("Plant-library operation history exceeds its size limit")
    normalized = deepcopy(operations)
    normalized.setdefault("version", _OPERATIONS_VERSION)
    normalized.setdefault("requests", {})
    normalized.setdefault("transactions", {})
    normalized.setdefault("record_versions", {})
    return normalized


def _mutable_operations(document: dict[str, Any]) -> dict[str, Any]:
    operations = _read_operations(document)
    operations.setdefault("version", _OPERATIONS_VERSION)
    operations.setdefault("requests", {})
    operations.setdefault("transactions", {})
    operations.setdefault("record_versions", {})
    document[_OPERATIONS_KEY] = operations
    return operations


def _check_operation_capacity(document: dict[str, Any]) -> None:
    operations = document.get(_OPERATIONS_KEY)
    if not isinstance(operations, dict):
        raise LibraryFileError("Plant-library operation history is malformed")
    requests = operations.get("requests", {})
    transactions = operations.get("transactions", {})
    if not isinstance(requests, dict) or not isinstance(transactions, dict):
        raise LibraryFileError("Plant-library receipts or transactions are malformed")
    if len(requests) > _MAX_RECEIPTS:
        raise LibraryValidationError("Plant-library receipt capacity reached; no receipt was purged")
    if len(transactions) > _MAX_TRANSACTIONS:
        raise LibraryValidationError(
            "Plant-library transaction capacity reached; no transaction was purged"
        )
    if len(_canonical_json(operations)) > _MAX_OPERATION_HISTORY_BYTES:
        raise LibraryValidationError(
            "Plant-library operation history capacity reached; no history was purged"
        )


def changed_library_record_ids(before: dict[str, Any], after: dict[str, Any]) -> set[str]:
    """Find record IDs whose stored JSON values or presence changed."""
    changed: set[str] = set()
    for plant_id in before.keys() | after.keys():
        before_present = plant_id in before
        after_present = plant_id in after
        if before_present != after_present or (
            before_present
            and _canonical_json(before[plant_id]) != _canonical_json(after[plant_id])
        ):
            changed.add(plant_id)
    return changed


def bump_record_versions(document: dict[str, Any], plant_ids: set[str]) -> dict[str, int]:
    """Increment persistent per-record versions for one committed mutation."""
    operations = _mutable_operations(document)
    versions = operations["record_versions"]
    changed_versions: dict[str, int] = {}
    for plant_id in sorted(plant_ids):
        current = versions.get(plant_id, 0)
        if isinstance(current, bool) or not isinstance(current, int) or current < 0:
            raise LibraryFileError(f"Stored version for plant {plant_id!r} is invalid")
        current += 1
        versions[plant_id] = current
        changed_versions[plant_id] = current
    return changed_versions


def _atomic_replace(path: Path, payload: bytes) -> None:
    """Replace a path atomically with bytes already prepared in memory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temp_path.unlink(missing_ok=True)


def commit_library_document(
    path: Path,
    document: dict[str, Any],
    *,
    expected_fingerprint: str,
) -> str:
    """Atomically publish one document, preserving the previous bytes as backup.

    The caller must hold ``ProcessFileLock(path.with_name(path.name +
    '.writer.lock'))``. The expected disk fingerprint is checked immediately
    before publication, including after the backup has been written.
    """
    path = Path(path)
    try:
        payload = json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
        ).encode("utf-8") + b"\n"
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise LibraryValidationError(f"Plant library contains invalid JSON values: {exc}") from exc
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temporary = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())

        original = _read_path_bytes(path)
        if _fingerprint(original) != expected_fingerprint:
            raise LibraryConflictError("Plant library changed before commit")

        if original is not None:
            backup_path = path.with_name(path.name + ".bak")
            _atomic_replace(backup_path, original)

        # Recheck after backup creation. This catches any writer that does not
        # honor the lock during the longer temp/backup preparation interval.
        current = _read_path_bytes(path)
        if _fingerprint(current) != expected_fingerprint:
            raise LibraryConflictError("Plant library changed before publication")

        os.replace(temp_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except LibraryOperationError:
        raise
    except OSError as exc:
        raise LibraryPersistenceError(f"Could not save plant library {path}: {exc}") from exc
    finally:
        temp_path.unlink(missing_ok=True)
    return _fingerprint(payload)


def serialize_species_record(
    plant: PlantSpeciesData,
    *,
    existing: dict[str, Any] | None = None,
    supplied: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Serialize a plant over its previous raw dictionary without dropping extras."""
    record = deepcopy(existing) if isinstance(existing, dict) else {}
    if isinstance(supplied, dict):
        record.update(deepcopy(supplied))
    serialized = deepcopy(plant.to_dict())
    prior_raw = record.get("raw_data")
    incoming_raw = serialized.get("raw_data")
    if isinstance(prior_raw, dict) and isinstance(incoming_raw, dict):
        merged_raw = deepcopy(prior_raw)
        merged_raw.update(incoming_raw)
        serialized["raw_data"] = merged_raw
    record.update(serialized)
    return record


def _record_value(record: Any) -> Any:
    """Canonical, normalized view of known and unknown fields in a record."""
    if not isinstance(record, dict):
        return deepcopy(record)
    plant = _parse_species(record)
    normalized = deepcopy(record)
    normalized.update(deepcopy(plant.to_dict()))
    return normalized


def _validate_species_input(
    record: dict[str, Any],
    *,
    existing: Any = None,
) -> None:
    """Validate the botanical schema accepted by a prepared library write.

    Historical top-level extensions remain readable and are preserved by the
    merge path. An update may pass one through unchanged, but new or changed
    extension keys belong in the explicit ``raw_data`` object.
    """
    for key, value in record.items():
        if key in _SPECIES_FIELDS_WITH_ID_ALIAS:
            continue
        if not isinstance(existing, dict) or key not in existing:
            raise ValueError(
                f"unrecognized field {key!r}; place provider extensions in raw_data"
            )
        if _canonical_json(value) != _canonical_json(existing[key]):
            raise ValueError(
                f"stored extension field {key!r} cannot be changed; use raw_data"
            )

    for field, enum_type in _ENUM_FIELDS.items():
        value = record.get(field)
        if field in record and (
            not isinstance(value, str) or value not in {member.value for member in enum_type}
        ):
            raise ValueError(f"{field} must be a supported {field} value")

    for field in _STRING_FIELDS:
        if field in record and not isinstance(record[field], str):
            raise ValueError(f"{field} must be a string")

    for field, allowed_values in _OPTIONAL_STRING_VALUES.items():
        value = record.get(field)
        if field in record and value is not None and (
            not isinstance(value, str) or value not in allowed_values
        ):
            allowed = ", ".join(sorted(allowed_values))
            raise ValueError(f"{field} must be one of {allowed} or null")

    for field in _NUMBER_FIELDS:
        value = record.get(field)
        if field not in record or value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{field} must be a finite number or null")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{field} must be a finite number or null")
        if field.startswith(("min_height_", "max_height_", "min_spread_", "max_spread_")):
            if value < 0:
                raise ValueError(f"{field} must be non-negative")
        elif field in {"ph_min", "ph_max"} and not 0 <= value <= 14:
            raise ValueError(f"{field} must be between 0 and 14")
        elif field == "min_germination_temp_c" and value < -273.15:
            raise ValueError(f"{field} cannot be below absolute zero")
        elif field == "seed_depth_cm" and value < 0:
            raise ValueError(f"{field} must be non-negative")

    for field in _INTEGER_FIELDS:
        value = record.get(field)
        if field not in record or value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{field} must be an integer or null")
        if field.startswith("hardiness_zone_"):
            if not 0 <= value <= 13:
                raise ValueError(f"{field} must be between 0 and 13")
        elif field.startswith(("days_to_", "prick_out_", "harden_off_")) and value < 0:
            raise ValueError(f"{field} must be non-negative")

    for field in ("edible", "flowering"):
        value = record.get(field)
        if field in record and value is not None and not isinstance(value, bool):
            raise ValueError(f"{field} must be a boolean or null")

    for field in ("functions", "edible_parts"):
        value = record.get(field)
        if field not in record:
            continue
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"{field} must be a list of strings")
        if field == "functions":
            allowed = {member.value for member in PlantFunction}
            if any(item not in allowed for item in value):
                raise ValueError("functions contains an unsupported plant function")

    if "raw_data" in record and not isinstance(record["raw_data"], dict):
        raise ValueError("raw_data must be an object")

    for start_field, end_field in _RANGE_PAIRS:
        start = record.get(start_field)
        end = record.get(end_field)
        if start is not None and end is not None and start > end:
            raise ValueError(f"{start_field} must not exceed {end_field}")


def _parse_species(record: dict[str, Any]) -> PlantSpeciesData:
    if not isinstance(record, dict):
        raise ValueError("plant record must be an object")
    for field in ("scientific_name", "common_name"):
        if not isinstance(record.get(field), str):
            raise ValueError(f"{field} must be a string")
    source_id = record.get("source_id", "")
    data_source = record.get("data_source", "")
    if not isinstance(source_id, str) or not isinstance(data_source, str):
        raise ValueError("source_id and data_source must be strings")
    if "raw_data" in record and not isinstance(record["raw_data"], dict):
        raise ValueError("raw_data must be an object")
    return PlantSpeciesData.from_dict(record)


def _source_id(record: Any) -> tuple[str | None, str | None]:
    if not isinstance(record, dict):
        return None, "row must be an object"
    source_id = record.get("source_id")
    alias_id = record.get("id")
    if source_id is not None and not isinstance(source_id, str):
        return None, "source_id must be a string"
    if alias_id is not None and not isinstance(alias_id, str):
        return None, "id must be a string"
    if source_id and alias_id and source_id != alias_id:
        return None, "id and source_id disagree"
    plant_id = source_id or alias_id
    if not isinstance(plant_id, str) or not plant_id.strip():
        return None, "an explicit non-empty source_id is required"
    return plant_id, None


def _merge_species_record(
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    merged = deepcopy(existing)
    incoming_raw = incoming.get("raw_data")
    existing_raw = merged.get("raw_data")
    merged.update(deepcopy(incoming))
    if isinstance(existing_raw, dict) and isinstance(incoming_raw, dict):
        raw_data = deepcopy(existing_raw)
        raw_data.update(deepcopy(incoming_raw))
        merged["raw_data"] = raw_data
    return merged


def _lock_path(path: Path) -> Path:
    return path.with_name(path.name + ".writer.lock")


def _normalize_request_key(request_key: str) -> str:
    if not isinstance(request_key, str):
        raise LibraryValidationError("request_key must be a string")
    key = unicodedata.normalize("NFC", request_key.strip())
    if not key:
        raise LibraryValidationError("request_key must not be empty")
    if len(key) > 200:
        raise LibraryValidationError("request_key must be at most 200 characters")
    return key


class LibraryOperations:
    """Prepare, apply, inspect, and undo batches in one explicit library file.

    Each input record must include ``source_id`` (or the documented ``id``
    alias). Matching is exact and case-sensitive; botanical names never select
    an update target. The object owns only a bounded cache of uncommitted
    previews; durable receipts and transactions live in the library document.
    """

    MAX_BATCH_SIZE = _MAX_BATCH_SIZE

    def __init__(self, path: Path) -> None:
        if path is None:
            raise TypeError("LibraryOperations requires an explicit path")
        try:
            self.path = Path(path).expanduser().resolve()
        except (OSError, RuntimeError) as exc:
            raise LibraryValidationError(f"Invalid plant library path: {exc}") from exc
        self._prepared: OrderedDict[str, dict[str, Any]] = OrderedDict()
        # Readers may prepare while a serialized writer applies another plan.
        # This mutex protects only cache bookkeeping, never disk I/O or Qt.
        self._preparation_mutex = threading.RLock()

    def snapshot(self) -> dict[str, Any]:
        """Return a detached mapping of stored plant IDs to raw records."""
        document, _, _ = read_library_document(self.path)
        return document["plants"]

    def prepare(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """Validate a batch and return a no-write preview with one opaque token."""
        if not isinstance(records, list):
            raise LibraryValidationError("records must be a list")
        if not records:
            raise LibraryValidationError("records must contain at least one row")
        if len(records) > self.MAX_BATCH_SIZE:
            raise LibraryValidationError(
                f"A library batch is limited to {self.MAX_BATCH_SIZE} records"
            )
        canonical_input = deepcopy(records)
        request_bytes = _canonical_json({"version": 1, "records": canonical_input})
        if len(request_bytes) > _MAX_REQUEST_BYTES:
            raise LibraryValidationError(
                f"A library request is limited to {_MAX_REQUEST_BYTES} JSON bytes"
            )
        request_hash = hashlib.sha256(request_bytes).hexdigest()
        document, _, expected_fingerprint = read_library_document(self.path)
        current_plants = document["plants"]

        extracted_ids: list[str | None] = []
        initial_errors: list[str | None] = []
        for raw in records:
            plant_id, error = _source_id(raw)
            extracted_ids.append(plant_id)
            initial_errors.append(error)
        frequencies: dict[str, int] = {}
        for plant_id in extracted_ids:
            if plant_id is not None:
                frequencies[plant_id] = frequencies.get(plant_id, 0) + 1

        prepared_rows: list[dict[str, Any]] = []
        public_rows: list[dict[str, Any]] = []
        for index, (raw, plant_id, identity_error) in enumerate(
            zip(records, extracted_ids, initial_errors, strict=True),
        ):
            public: dict[str, Any] = {"index": index, "id": plant_id}
            internal: dict[str, Any] = {"index": index, "id": plant_id}
            existing_for_preview = (
                current_plants.get(plant_id) if plant_id is not None else None
            )
            public["before"] = (
                deepcopy(existing_for_preview)
                if isinstance(existing_for_preview, dict)
                else None
            )
            public["after"] = deepcopy(raw)
            if identity_error:
                public.update(status="conflict", error=identity_error)
                internal.update(status="conflict", error=identity_error)
            elif frequencies[plant_id] > 1:
                message = "duplicate explicit ID in this batch"
                public.update(status="conflict", error=message)
                internal.update(status="conflict", error=message)
            else:
                try:
                    assert isinstance(raw, dict)
                    input_record = deepcopy(raw)
                    input_record["source_id"] = plant_id
                    if "data_source" not in input_record or input_record["data_source"] == "":
                        input_record["data_source"] = "custom"
                    existing = current_plants.get(plant_id)
                    _validate_species_input(input_record, existing=existing)
                    species = _parse_species(input_record)
                    normalized_input = serialize_species_record(species, supplied=input_record)
                    _canonical_json(normalized_input)

                    if existing is None:
                        public.update(status="create", before=None, after=deepcopy(normalized_input))
                        internal.update(status="create", record=normalized_input)
                    elif not isinstance(existing, dict):
                        raise ValueError("existing record is not a JSON object")
                    else:
                        current_species = _parse_species(existing)
                        current_id = current_species.source_id
                        if current_id and current_id != plant_id:
                            raise ValueError("stored source_id does not match its library key")
                        old_source = current_species.data_source
                        new_source = species.data_source
                        if old_source and new_source and old_source != new_source:
                            raise ValueError("explicit source differs from the stored record")
                        candidate = _merge_species_record(existing, normalized_input)
                        _canonical_json(candidate)
                        if _canonical_json(_record_value(existing)) == _canonical_json(
                            _record_value(candidate)
                        ):
                            public.update(
                                status="unchanged",
                                before=deepcopy(existing),
                                after=deepcopy(existing),
                            )
                            internal.update(status="unchanged")
                        else:
                            public.update(
                                status="update",
                                before=deepcopy(existing),
                                after=deepcopy(candidate),
                            )
                            internal.update(status="update", record=candidate)
                except Exception as exc:  # validation boundary for untrusted batch rows
                    message = str(exc) or type(exc).__name__
                    existing = current_plants.get(plant_id) if plant_id is not None else None
                    public["before"] = deepcopy(existing) if isinstance(existing, dict) else None
                    try:
                        public["after"] = deepcopy(raw)
                        _canonical_json(public["after"])
                    except LibraryValidationError:
                        public["after"] = None
                    public.update(status="conflict", error=message)
                    internal.update(status="conflict", error=message)

            public_rows.append(public)
            prepared_rows.append(internal)

        if len(_canonical_json(public_rows)) > _MAX_PREVIEW_BYTES:
            raise LibraryValidationError(
                f"A library preview is limited to {_MAX_PREVIEW_BYTES} JSON bytes"
            )

        token = secrets.token_urlsafe(24)
        preparation = {
            "request_hash": request_hash,
            "expected_fingerprint": expected_fingerprint,
            "rows": prepared_rows,
            "public_rows": public_rows,
            "created_at": time.monotonic(),
        }
        with self._preparation_mutex:
            self._prepared[token] = preparation
            self._prepared.move_to_end(token)
            while len(self._prepared) > _MAX_PREPARATIONS:
                self._prepared.popitem(last=False)
        return {
            "token": token,
            "can_apply": not any(row["status"] == "conflict" for row in public_rows),
            "entries": deepcopy(public_rows),
            "expected_revision": expected_fingerprint,
            "expires_in_seconds": _PREPARATION_TTL_SECONDS,
        }

    def apply(self, token: str, key: str) -> dict[str, Any]:
        """Atomically apply a prepared batch and persist its replay receipt."""
        request_key = _normalize_request_key(key)
        preparation = self._get_preparation(token)
        if preparation is not None and any(
            row["status"] == "conflict" for row in preparation["rows"]
        ):
            # A structurally invalid preview can never correspond to a prior
            # successful receipt, so refuse before creating the lock file.
            document, _, _ = read_library_document(self.path)
            receipt = _read_operations(document)["requests"].get(request_key)
            if isinstance(receipt, dict):
                if receipt.get("request_hash") != preparation["request_hash"]:
                    raise LibraryConflictError(
                        "request_key was already used for different library content"
                    )
                old_result = receipt.get("result")
                if isinstance(old_result, dict):
                    replay = deepcopy(old_result)
                    replay["replayed"] = True
                    replay["transaction_state"] = self._transaction_state(
                        document, replay.get("transaction_id")
                    )
                    return replay
            raise LibraryValidationError(
                "The prepared batch contains conflicting or invalid rows"
            )
        if preparation is None:
            # Replays can use the original committed token after a restart, but
            # an uncommitted expired token must not create a lock file.
            document, _, _ = read_library_document(self.path)
            receipt = _read_operations(document)["requests"].get(request_key)
            if (
                isinstance(receipt, dict)
                and receipt.get("preparation_token") == token
                and isinstance(receipt.get("result"), dict)
            ):
                replay = deepcopy(receipt["result"])
                replay["replayed"] = True
                replay["transaction_state"] = self._transaction_state(
                    document, replay.get("transaction_id")
                )
                return replay
            raise PreparationExpiredError("Unknown or expired library preview token")
        with ProcessFileLock(_lock_path(self.path)):
            document, _, current_fingerprint = read_library_document(self.path)
            operations = _read_operations(document)
            old_receipt = operations["requests"].get(request_key)
            if old_receipt is not None:
                if not isinstance(old_receipt, dict):
                    raise LibraryFileError("Stored operation receipt is malformed")
                old_hash = old_receipt.get("request_hash")
                if preparation is not None:
                    if old_hash != preparation["request_hash"]:
                        raise LibraryConflictError(
                            "request_key was already used for different library content"
                        )
                elif old_receipt.get("preparation_token") != token:
                    raise PreparationExpiredError(
                        "Preview token is not available; prepare the same batch again"
                    )
                result = deepcopy(old_receipt.get("result"))
                if not isinstance(result, dict):
                    raise LibraryFileError("Stored operation result is malformed")
                result["replayed"] = True
                result["transaction_state"] = self._transaction_state(
                    document, result.get("transaction_id")
                )
                return result

            if current_fingerprint != preparation["expected_fingerprint"]:
                raise LibraryConflictError(
                    "Plant library changed after preparation; prepare the batch again"
                )

            candidate = deepcopy(document)
            plants = candidate["plants"]
            changes: list[dict[str, Any]] = []
            for row in preparation["rows"]:
                if row["status"] not in {"create", "update"}:
                    continue
                plant_id = row["id"]
                before = deepcopy(plants.get(plant_id))
                after = deepcopy(row["record"])
                plants[plant_id] = after
                changes.append({
                    "id": plant_id,
                    "before": before,
                    "after": after,
                    "before_hash": _canonical_hash(before) if before is not None else None,
                    "after_hash": _canonical_hash(after),
                })

            transaction_id = str(uuid.uuid4())
            transaction = {
                "version": _OPERATIONS_VERSION,
                "transaction_id": transaction_id,
                "request_key": request_key,
                "request_hash": preparation["request_hash"],
                "state": "applied",
                "changes": changes,
            }
            operations = _mutable_operations(candidate)
            prior_versions = deepcopy(operations["record_versions"])
            after_versions = bump_record_versions(
                candidate,
                {change["id"] for change in changes},
            )
            operations = _mutable_operations(candidate)
            for change in changes:
                before_version = prior_versions.get(change["id"], 0)
                change["after_version"] = after_versions[change["id"]]
                change["before_version"] = before_version
            operations["transactions"][transaction_id] = transaction
            candidate[_OPERATIONS_KEY] = operations
            result = {
                "status": "applied",
                "request_key": request_key,
                "request_hash": preparation["request_hash"],
                "transaction_id": transaction_id,
                "entries": [
                    {key: row[key] for key in ("index", "id", "status") if key in row}
                    for row in preparation["public_rows"]
                ],
                "created": sum(row["status"] == "create" for row in preparation["rows"]),
                "updated": sum(row["status"] == "update" for row in preparation["rows"]),
                "unchanged": sum(
                    row["status"] == "unchanged" for row in preparation["rows"]
                ),
                "persisted": True,
                "replayed": False,
                "transaction_state": "applied",
            }
            operations["requests"][request_key] = {
                "version": _OPERATIONS_VERSION,
                "preparation_token": token,
                "request_hash": preparation["request_hash"],
                "transaction_id": transaction_id,
                "result": deepcopy(result),
            }
            candidate[_OPERATIONS_KEY] = operations
            _check_operation_capacity(candidate)

            try:
                commit_library_document(
                    self.path,
                    candidate,
                    expected_fingerprint=current_fingerprint,
                )
            except LibraryOperationError:
                raise
            except OSError as exc:
                raise LibraryPersistenceError(f"Could not save plant library: {exc}") from exc
            with self._preparation_mutex:
                self._prepared.pop(token, None)
            return result

    def _get_preparation(self, token: str) -> dict[str, Any] | None:
        if not isinstance(token, str):
            return None
        with self._preparation_mutex:
            preparation = self._prepared.get(token)
            if preparation is None:
                return None
            if time.monotonic() - preparation["created_at"] >= _PREPARATION_TTL_SECONDS:
                self._prepared.pop(token, None)
                return None
            self._prepared.move_to_end(token)
            return preparation

    def result(self, key: str) -> dict[str, Any] | None:
        """Read a durable idempotency receipt and its current transaction state."""
        request_key = _normalize_request_key(key)
        document, _, _ = read_library_document(self.path)
        operations = _read_operations(document)
        receipt = operations["requests"].get(request_key)
        if receipt is None:
            return None
        if not isinstance(receipt, dict) or not isinstance(receipt.get("result"), dict):
            raise LibraryFileError("Stored operation receipt is malformed")
        result = deepcopy(receipt["result"])
        result["transaction_state"] = self._transaction_state(
            document, result.get("transaction_id")
        )
        return result

    def undo(self, transaction_id: str) -> dict[str, Any]:
        """Undo one transaction only while every changed row still matches it."""
        if not isinstance(transaction_id, str) or not transaction_id.strip():
            raise LibraryValidationError("transaction_id must be a non-empty string")
        transaction_id = transaction_id.strip()
        with ProcessFileLock(_lock_path(self.path)):
            document, _, current_fingerprint = read_library_document(self.path)
            operations = _read_operations(document)
            transaction = operations["transactions"].get(transaction_id)
            if transaction is None:
                raise LibraryValidationError("Unknown library transaction")
            if not isinstance(transaction, dict):
                raise LibraryFileError("Stored library transaction is malformed")
            transaction_version = transaction.get("version")
            if (
                isinstance(transaction_version, bool)
                or transaction_version != _OPERATIONS_VERSION
            ):
                raise LibraryFileError("Unsupported library transaction version")
            state = transaction.get("state")
            if state == "undone":
                return {
                    "status": "undone",
                    "transaction_id": transaction_id,
                    "replayed": True,
                }
            if state != "applied":
                raise LibraryConflictError("Library transaction is not currently applied")

            changes = transaction.get("changes")
            if not isinstance(changes, list):
                raise LibraryFileError("Stored library transaction changes are malformed")
            current_plants = document["plants"]
            current_versions = operations["record_versions"]
            for change in changes:
                if not isinstance(change, dict) or not isinstance(change.get("id"), str):
                    raise LibraryFileError("Stored library transaction row is malformed")
                plant_id = change["id"]
                current = current_plants.get(plant_id)
                expected_hash = change.get("after_hash")
                if current is None or _canonical_hash(current) != expected_hash:
                    raise LibraryConflictError(
                        f"Plant {plant_id!r} changed after this transaction"
                    )
                expected_version = change.get("after_version")
                if current_versions.get(plant_id) != expected_version:
                    raise LibraryConflictError(
                        f"Plant {plant_id!r} changed after this transaction"
                    )

            candidate = deepcopy(document)
            plants = candidate["plants"]
            for change in changes:
                plant_id = change["id"]
                before = change.get("before")
                if before is None:
                    plants.pop(plant_id, None)
                else:
                    plants[plant_id] = deepcopy(before)
            mutable = _mutable_operations(candidate)
            bump_record_versions(candidate, {change["id"] for change in changes})
            mutable = _read_operations(candidate)
            candidate[_OPERATIONS_KEY] = mutable
            stored = mutable["transactions"][transaction_id]
            stored["state"] = "undone"
            stored["undo_version"] = 1
            candidate[_OPERATIONS_KEY] = mutable
            _check_operation_capacity(candidate)

            try:
                commit_library_document(
                    self.path,
                    candidate,
                    expected_fingerprint=current_fingerprint,
                )
            except LibraryOperationError:
                raise
            except OSError as exc:
                raise LibraryPersistenceError(f"Could not undo library transaction: {exc}") from exc
            return {
                "status": "undone",
                "transaction_id": transaction_id,
                "replayed": False,
            }

    @staticmethod
    def _transaction_state(document: dict[str, Any], transaction_id: Any) -> str | None:
        if not isinstance(transaction_id, str):
            return None
        transactions = _read_operations(document)["transactions"]
        transaction = transactions.get(transaction_id)
        if not isinstance(transaction, dict):
            return None
        state = transaction.get("state")
        if state != "applied":
            return state if isinstance(state, str) else None
        for change in transaction.get("changes", []):
            if not isinstance(change, dict):
                return "invalid"
            current = document["plants"].get(change.get("id"))
            current_version = _read_operations(document)["record_versions"].get(
                change.get("id")
            )
            if (
                current is None
                or _canonical_hash(current) != change.get("after_hash")
                or current_version != change.get("after_version")
            ):
                return "superseded"
        return "applied"


__all__ = [
    "LibraryConflictError",
    "LibraryFileError",
    "LibraryOperationError",
    "LibraryOperations",
    "LibraryPersistenceError",
    "LibraryValidationError",
    "PreparationExpiredError",
    "bump_record_versions",
    "changed_library_record_ids",
    "commit_library_document",
    "read_library_document",
    "serialize_species_record",
]
