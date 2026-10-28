"""Seed inventory data model for tracking seed packets.

Implements US-9.1: SeedPacket dataclass with viability calculation and
SeedInventoryStore for persisting the global (cross-project) inventory.
"""
from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any

from open_garden_planner.core.personal_store import (
    PersonalStoreConflictError,
    PersonalStoreLoadError,
    PersonalStoreLoadState,
    PersonalStorePersistenceError,
    read_json,
    write_json,
)

_QT_SPINBOX_INT_MIN = -(2**31)
_QT_SPINBOX_INT_MAX = 2**31 - 1
_MAX_CALENDAR_DURATION_DAYS = (date.max - date.min).days
_SEED_PACKET_INTEGER_FIELDS = {
    "purchase_year", "germination_days_min", "germination_days_max",
    "stratification_days", "viability_shelf_life_override",
}
_SEED_PACKET_DATE_DURATION_FIELDS = {
    "germination_days_min", "germination_days_max",
}


class ViabilityStatus(Enum):
    """Calculated viability status for a seed packet."""

    GOOD = "good"               # Within normal shelf life
    REDUCED = "reduced"         # Past peak viability but still usable
    EXPIRED = "expired"         # Beyond expected shelf life
    UNKNOWN = "unknown"         # No shelf-life data available


@dataclass
class SeedPacket:
    """A single seed packet in the user's inventory.

    All string fields default to "" so that freshly-created packets with
    partial data are still fully serializable.
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # ── Species link ──────────────────────────────────────────────────────────
    species_id: str | None = None        # scientific_name from PlantSpeciesData
    species_name: str = ""               # display name / fallback
    variety: str = ""

    # ── Provenance ────────────────────────────────────────────────────────────
    purchase_year: int = 2024
    manufacturer: str = ""
    batch_number: str = ""

    # ── Quantity ──────────────────────────────────────────────────────────────
    quantity: float = 0.0
    quantity_unit: str = "seeds"         # "seeds" | "grams"

    # ── Germination conditions ────────────────────────────────────────────────
    germination_temp_min_c: float | None = None
    germination_temp_opt_c: float | None = None
    germination_temp_max_c: float | None = None
    germination_days_min: int | None = None
    germination_days_max: int | None = None

    # True = light germinator, False = dark, None = indifferent
    light_germinator: bool | None = None

    # ── Pre-treatment ─────────────────────────────────────────────────────────
    cold_stratification: bool = False
    stratification_days: int | None = None
    pre_treatment: str = ""              # scarification, soaking, etc.

    # ── Extra ─────────────────────────────────────────────────────────────────
    notes: str = ""
    photo_path: str = ""                 # absolute or project-relative path
    created_date: str = ""              # ISO date string (YYYY-MM-DD)

    # ── Viability override (US-9.2) ───────────────────────────────────────────
    # When set, this shelf life (years) is used instead of the DB lookup,
    # allowing the user to record observed germination data for their specific
    # seed lot.
    viability_shelf_life_override: int | None = None

    # ──────────────────────────────────────────────────────────────────────────

    def viability_status(self, current_year: int, db: SeedViabilityDB) -> ViabilityStatus:
        """Return the calculated viability status for this packet.

        Uses ``viability_shelf_life_override`` when set; otherwise looks up the
        species in ``db``.  Falls back to the DB default (3 years) when the
        species is not found.

        Args:
            current_year: The year to calculate age against (usually today's year).
            db: Loaded SeedViabilityDB instance.

        Returns:
            ViabilityStatus enum value.
        """
        age = current_year - self.purchase_year
        if age < 0:
            return ViabilityStatus.GOOD

        if self.viability_shelf_life_override is not None:
            shelf_life = self.viability_shelf_life_override
            reduced_after = max(1, shelf_life - 1)
        else:
            entry = db.lookup(self.species_name) or db.default_entry
            shelf_life = entry.get("shelf_life_years", 3)
            reduced_after = entry.get("reduced_after_years", shelf_life)

        if age >= shelf_life:
            return ViabilityStatus.EXPIRED
        if age >= reduced_after:
            return ViabilityStatus.REDUCED
        return ViabilityStatus.GOOD

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        d: dict[str, Any] = {
            "id": self.id,
            "species_name": self.species_name,
            "purchase_year": self.purchase_year,
            "quantity": self.quantity,
            "quantity_unit": self.quantity_unit,
            "cold_stratification": self.cold_stratification,
        }
        # Omit falsy optionals to keep files compact
        if self.species_id:
            d["species_id"] = self.species_id
        if self.variety:
            d["variety"] = self.variety
        if self.manufacturer:
            d["manufacturer"] = self.manufacturer
        if self.batch_number:
            d["batch_number"] = self.batch_number
        if self.germination_temp_min_c is not None:
            d["germination_temp_min_c"] = self.germination_temp_min_c
        if self.germination_temp_opt_c is not None:
            d["germination_temp_opt_c"] = self.germination_temp_opt_c
        if self.germination_temp_max_c is not None:
            d["germination_temp_max_c"] = self.germination_temp_max_c
        if self.germination_days_min is not None:
            d["germination_days_min"] = self.germination_days_min
        if self.germination_days_max is not None:
            d["germination_days_max"] = self.germination_days_max
        if self.light_germinator is not None:
            d["light_germinator"] = self.light_germinator
        if self.stratification_days is not None:
            d["stratification_days"] = self.stratification_days
        if self.pre_treatment:
            d["pre_treatment"] = self.pre_treatment
        if self.notes:
            d["notes"] = self.notes
        if self.photo_path:
            d["photo_path"] = self.photo_path
        if self.created_date:
            d["created_date"] = self.created_date
        if self.viability_shelf_life_override is not None:
            d["viability_shelf_life_override"] = self.viability_shelf_life_override
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SeedPacket:
        """Deserialise from dictionary."""
        for field_name in _SEED_PACKET_INTEGER_FIELDS:
            value = data.get(field_name, 2024 if field_name == "purchase_year" else None)
            if value is None and field_name != "purchase_year":
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"seed packet has an invalid {field_name}")
            if field_name in _SEED_PACKET_DATE_DURATION_FIELDS:
                in_range = 0 <= value <= _MAX_CALENDAR_DURATION_DAYS
                supported_range = f"0..{_MAX_CALENDAR_DURATION_DAYS} days"
            else:
                in_range = _QT_SPINBOX_INT_MIN <= value <= _QT_SPINBOX_INT_MAX
                supported_range = "the supported Qt integer range"
            if not in_range:
                raise ValueError(
                    f"seed packet {field_name} is outside {supported_range}"
                )
        purchase_year = data.get("purchase_year", 2024)
        cold_stratification = data.get("cold_stratification", False)
        if not isinstance(cold_stratification, bool):
            raise ValueError("seed packet has an invalid cold_stratification")
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            species_id=data.get("species_id"),
            species_name=data.get("species_name", ""),
            variety=data.get("variety", ""),
            purchase_year=purchase_year,
            manufacturer=data.get("manufacturer", ""),
            batch_number=data.get("batch_number", ""),
            quantity=data.get("quantity", 0.0),
            quantity_unit=data.get("quantity_unit", "seeds"),
            germination_temp_min_c=data.get("germination_temp_min_c"),
            germination_temp_opt_c=data.get("germination_temp_opt_c"),
            germination_temp_max_c=data.get("germination_temp_max_c"),
            germination_days_min=data.get("germination_days_min"),
            germination_days_max=data.get("germination_days_max"),
            light_germinator=data.get("light_germinator"),
            cold_stratification=cold_stratification,
            stratification_days=data.get("stratification_days"),
            pre_treatment=data.get("pre_treatment", ""),
            notes=data.get("notes", ""),
            photo_path=data.get("photo_path", ""),
            created_date=data.get("created_date", ""),
            viability_shelf_life_override=data.get("viability_shelf_life_override"),
        )


# ── Viability database ─────────────────────────────────────────────────────────

class SeedViabilityDB:
    """Bundled database mapping species/families to seed shelf life.

    Loaded from ``resources/data/seed_viability.json``.  Lookups first try
    an exact match on common name (lowercase), then fall back to the plant
    family name, then return None.
    """

    _DEFAULT_ENTRY: dict[str, int] = {"shelf_life_years": 3, "reduced_after_years": 2}

    def __init__(self, data: dict[str, Any]) -> None:
        self._by_species: dict[str, dict[str, int]] = data.get("by_species", {})
        self._by_family: dict[str, dict[str, int]] = data.get("by_family", {})
        # Use the _default entry from the JSON if present, otherwise use the class default
        raw_default = data.get("_default", {})
        self._default_entry: dict[str, int] = {
            "shelf_life_years": raw_default.get("shelf_life_years", 3),
            "reduced_after_years": raw_default.get("reduced_after_years", 2),
        }

    @property
    def default_entry(self) -> dict[str, int]:
        """Default shelf-life entry applied when no species/family match is found."""
        return self._default_entry

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: Path) -> SeedViabilityDB:
        """Load the database from a JSON file."""
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SeedViabilityDB:
        """Create directly from a dict (useful for tests)."""
        return cls(data)

    # ── Lookup ────────────────────────────────────────────────────────────────

    def lookup(
        self,
        species_name: str,
        family: str | None = None,
    ) -> dict[str, int] | None:
        """Return shelf-life entry or None if not found.

        Tries (in order):
        1. ``species_name`` lowercased against ``by_species`` keys (exact)
        2. Any ``by_species`` key that appears as a substring of ``species_name``
        3. ``family`` (botanical family name) against ``by_family`` keys
        """
        key = species_name.lower().strip()

        # Exact species match
        if key in self._by_species:
            return self._by_species[key]

        # Substring match (e.g. "Cherry Tomato" → "tomato" key)
        for species_key, entry in self._by_species.items():
            if species_key in key or key in species_key:
                return entry

        # Family fallback
        if family:
            entry = self._by_family.get(family)
            if entry is not None:
                return entry

        return None

    # ── Introspection ─────────────────────────────────────────────────────────

    @property
    def species_count(self) -> int:
        """Number of species entries in the database."""
        return len(self._by_species)

    @property
    def family_count(self) -> int:
        """Number of family entries in the database."""
        return len(self._by_family)


# ── Global inventory store ─────────────────────────────────────────────────────

class SeedInventoryStore:
    """Manages the user's global seed inventory (cross-project).

    Packets are stored as a list in ``get_app_data_dir()/seed_inventory.json``.
    The store is not a singleton — callers obtain a fresh instance via
    ``get_seed_inventory()`` which caches it at module level.
    """

    FILENAME = "seed_inventory.json"

    def __init__(self, storage_path: Path) -> None:
        self._path = storage_path
        self._packets: dict[str, SeedPacket] = {}
        self._extra_fields: dict[str, dict[str, Any]] = {}
        self._disk_fingerprint: str | None = None
        self._load_state = PersonalStoreLoadState.ABSENT
        self._load_error: PersonalStoreLoadError | None = None
        self._dirty = False
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    @property
    def path(self) -> Path:
        """Return the JSON file path for this store."""
        return self._path

    @property
    def load_state(self) -> PersonalStoreLoadState:
        """Return the last observed disk state."""
        return self._load_state

    @property
    def load_error(self) -> PersonalStoreLoadError | None:
        """Return the active load/validation failure, if any."""
        return self._load_error

    @property
    def is_dirty(self) -> bool:
        """Whether current in-memory data has not been durably saved."""
        return self._dirty

    @staticmethod
    def _validate_records(raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            raise ValueError("seed inventory must be a JSON array")

        string_fields = {
            "species_name", "variety", "manufacturer", "batch_number",
            "quantity_unit", "pre_treatment", "notes", "photo_path", "created_date",
        }
        nullable_string_fields = {"species_id"}
        integer_fields = _SEED_PACKET_INTEGER_FIELDS
        required_integer_fields = {"purchase_year"}
        numeric_fields = {
            "quantity", "germination_temp_min_c", "germination_temp_opt_c",
            "germination_temp_max_c",
        }
        boolean_fields = {"cold_stratification", "light_germinator"}
        packet_ids: set[str] = set()
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                raise ValueError(f"seed packet at index {index} must be an object")
            packet_id = item.get("id")
            if not isinstance(packet_id, str) or not packet_id.strip():
                raise ValueError(f"seed packet at index {index} has no valid id")
            if packet_id in packet_ids:
                raise ValueError(f"duplicate seed packet id: {packet_id!r}")
            packet_ids.add(packet_id)
            for field_name in string_fields:
                if field_name in item and not isinstance(item[field_name], str):
                    raise ValueError(
                        f"seed packet {packet_id!r} has an invalid {field_name}"
                    )
            for field_name in nullable_string_fields:
                value = item.get(field_name)
                if value is not None and not isinstance(value, str):
                    raise ValueError(
                        f"seed packet {packet_id!r} has an invalid {field_name}"
                    )
            for field_name in integer_fields:
                value = item.get(field_name)
                if field_name in item and field_name in required_integer_fields and (
                    isinstance(value, bool) or not isinstance(value, int)
                ):
                    raise ValueError(
                        f"seed packet {packet_id!r} has an invalid {field_name}"
                    )
                if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                    raise ValueError(
                        f"seed packet {packet_id!r} has an invalid {field_name}"
                    )
                if value is not None:
                    if field_name in _SEED_PACKET_DATE_DURATION_FIELDS:
                        in_range = 0 <= value <= _MAX_CALENDAR_DURATION_DAYS
                        supported_range = f"0..{_MAX_CALENDAR_DURATION_DAYS} days"
                    else:
                        in_range = (
                            _QT_SPINBOX_INT_MIN <= value <= _QT_SPINBOX_INT_MAX
                        )
                        supported_range = "the supported Qt integer range"
                    if not in_range:
                        raise ValueError(
                            f"seed packet {packet_id!r} has a {field_name} outside "
                            f"{supported_range}"
                        )
            for field_name in numeric_fields:
                value = item.get(field_name)
                if value is None:
                    continue
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(
                        f"seed packet {packet_id!r} has an invalid {field_name}"
                    )
                try:
                    finite = math.isfinite(value)
                except OverflowError:
                    finite = False
                if not finite:
                    raise ValueError(
                        f"seed packet {packet_id!r} has a non-finite {field_name}"
                    )
            if item.get("quantity", 0.0) < 0:
                raise ValueError(f"seed packet {packet_id!r} has a negative quantity")
            for field_name in boolean_fields:
                value = item.get(field_name)
                if (
                    field_name in item
                    and field_name == "cold_stratification"
                    and not isinstance(value, bool)
                ):
                    raise ValueError(
                        f"seed packet {packet_id!r} has an invalid {field_name}"
                    )
                if value is not None and not isinstance(value, bool):
                    raise ValueError(
                        f"seed packet {packet_id!r} has an invalid {field_name}"
                    )
        return raw

    def _load(self) -> None:
        self._packets = {}
        self._extra_fields = {}
        self._disk_fingerprint = None
        self._load_error = None
        self._dirty = False
        try:
            loaded = read_json(self._path)
            if loaded.state == PersonalStoreLoadState.ABSENT:
                self._load_state = PersonalStoreLoadState.ABSENT
                return
            records = self._validate_records(loaded.value)
            for item in records:
                packet = SeedPacket.from_dict(item)
                self._packets[packet.id] = packet
                known_fields = SeedPacket.__dataclass_fields__
                self._extra_fields[packet.id] = {
                    key: value for key, value in item.items() if key not in known_fields
                }
            self._disk_fingerprint = loaded.fingerprint
            self._load_state = (
                PersonalStoreLoadState.LOADED if records
                else PersonalStoreLoadState.EMPTY
            )
        except (
            PersonalStoreLoadError, ValueError, TypeError, KeyError, RecursionError
        ) as exc:
            self._packets = {}
            self._extra_fields = {}
            self._disk_fingerprint = None
            self._load_state = PersonalStoreLoadState.FAILED
            self._load_error = (
                exc if isinstance(exc, PersonalStoreLoadError)
                else PersonalStoreLoadError(f"Invalid seed inventory {self._path}: {exc}")
            )

    def reload(self) -> None:
        """Explicitly discard current memory and try loading the file again."""
        self._load()

    def _ensure_readable(self) -> None:
        if self._load_state == PersonalStoreLoadState.FAILED:
            raise self._load_error or PersonalStoreLoadError(
                f"Seed inventory is unreadable: {self._path}"
            )

    def _ensure_writable(self) -> None:
        self._ensure_readable()

    def save(self) -> None:
        """Persist memory state, retaining it explicitly when disk save fails."""
        self._ensure_writable()
        packets_list = sorted(
            self._packets.values(), key=lambda p: (p.species_name, p.id)
        )
        records = []
        for packet in packets_list:
            record = dict(self._extra_fields.get(packet.id, {}))
            record.update(packet.to_dict())
            records.append(record)

        try:
            # A packet can be edited in memory by callers. Refuse to publish a
            # shape that would fail the next load, even when json.dumps accepts it.
            self._validate_records(records)
            self._disk_fingerprint = write_json(
                self._path,
                records,
                expected_fingerprint=self._disk_fingerprint,
            )
        except PersonalStoreConflictError:
            self._dirty = True
            raise
        except Exception as exc:  # noqa: BLE001
            self._dirty = True
            raise PersonalStorePersistenceError(
                f"Changes remain in memory but could not be saved to {self._path}: {exc}"
            ) from exc
        self._load_state = (
            PersonalStoreLoadState.LOADED if records
            else PersonalStoreLoadState.EMPTY
        )
        self._dirty = False

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def add(self, packet: SeedPacket) -> None:
        """Add or replace a packet (keyed by packet.id)."""
        self._ensure_writable()
        self._packets[packet.id] = packet
        self._dirty = True

    def remove(self, packet_id: str) -> bool:
        """Remove a packet by ID.  Returns True if it existed."""
        self._ensure_writable()
        removed = self._packets.pop(packet_id, None) is not None
        if removed:
            self._extra_fields.pop(packet_id, None)
            self._dirty = True
        return removed

    def get(self, packet_id: str) -> SeedPacket | None:
        """Return packet by ID, or None."""
        self._ensure_readable()
        return self._packets.get(packet_id)

    def all(self) -> list[SeedPacket]:
        """Return all packets sorted by species name then ID."""
        self._ensure_readable()
        return sorted(self._packets.values(), key=lambda p: (p.species_name, p.id))

    def __len__(self) -> int:
        self._ensure_readable()
        return len(self._packets)


# ── Module-level accessor ──────────────────────────────────────────────────────

_store: SeedInventoryStore | None = None


def get_seed_inventory() -> SeedInventoryStore:
    """Return the application-wide seed inventory (loaded lazily)."""
    global _store
    if _store is None:
        from open_garden_planner.services.plant_library import get_app_data_dir

        _store = SeedInventoryStore(get_app_data_dir() / SeedInventoryStore.FILENAME)
    return _store


def get_viability_db() -> SeedViabilityDB:
    """Load and return the bundled seed viability database."""
    data_path = (
        Path(__file__).parent.parent
        / "resources"
        / "data"
        / "seed_viability.json"
    )
    return SeedViabilityDB.load(data_path)
