"""Data models and storage for plant lists and favourites (US-G4, issue #320).

Provides:
  * PlantListEntry: A single species entry within a list, holding a
    PlantSpeciesData snapshot so custom/imported plants survive even if the
    source is removed or unreachable.
  * PlantList: A named collection of species entries (e.g. Favorites,
    a bed palette, or a companion grouping).
  * PlantListStore: Cross-project persistent store backed by
    <app-data>/plant_lists.json using atomic file replacement.
"""

from __future__ import annotations

import json
import math
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal

from open_garden_planner.core.personal_store import (
    PersonalStoreConflictError,
    PersonalStoreLoadError,
    PersonalStoreLoadState,
    PersonalStorePersistenceError,
    loads_json,
    read_json,
    write_json,
)
from open_garden_planner.models.plant_data import PlantSpeciesData, species_key

_QT_SPINBOX_INT_MIN = -(2**31)
_QT_SPINBOX_INT_MAX = 2**31 - 1
_MAX_CALENDAR_DURATION_DAYS = (date.max - date.min).days
_MAX_CALENDAR_OFFSET_WEEKS = _MAX_CALENDAR_DURATION_DAYS // 7
_SPECIES_CALENDAR_WEEK_FIELDS = {
    "indoor_sow_start", "indoor_sow_end", "direct_sow_start", "direct_sow_end",
    "transplant_start", "transplant_end", "harvest_start", "harvest_end",
}
_SPECIES_CALENDAR_DURATION_FIELDS = {
    "days_to_germination_min", "days_to_germination_max",
    "days_to_maturity_min", "days_to_maturity_max",
    "prick_out_after_days", "harden_off_days",
}


def _get_species_key(species_obj: PlantSpeciesData | dict[str, Any] | str) -> str:
    """Extract canonical lowercased species_key from a species object or string.

    Canonicalizes botanical common names / aliases to scientific name keys
    via bundled species DB so that gallery items, database records, and custom
    lists resolve symmetrically (P0 fix for issue #320).
    """
    from open_garden_planner.services.bundled_species_db import (  # noqa: PLC0415
        lookup_species,
    )

    if isinstance(species_obj, str):
        name = species_obj.strip()
        if not name:
            return "_unknown"
        meta = lookup_species(name)
        if meta:
            return species_key(meta)
        return name.lower()

    if isinstance(species_obj, PlantSpeciesData):
        if species_obj.source_id:
            return species_obj.source_id.strip().lower()
        if species_obj.scientific_name:
            return species_obj.scientific_name.strip().lower()
        if species_obj.common_name:
            meta = lookup_species(species_obj.common_name)
            if meta:
                return species_key(meta)
        return species_key({
            "source_id": species_obj.source_id,
            "scientific_name": species_obj.scientific_name,
            "common_name": species_obj.common_name,
        })

    if isinstance(species_obj, dict):
        if species_obj.get("source_id"):
            return str(species_obj["source_id"]).strip().lower()
        if species_obj.get("scientific_name"):
            return str(species_obj["scientific_name"]).strip().lower()
        if species_obj.get("common_name"):
            meta = lookup_species(str(species_obj["common_name"]))
            if meta:
                return species_key(meta)
        return species_key(species_obj)

    return "_unknown"


@dataclass
class PlantListEntry:
    """An entry in a plant list."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    species_key: str = ""
    species: PlantSpeciesData = field(
        default_factory=lambda: PlantSpeciesData(
            scientific_name="", common_name=""
        )
    )
    added_at: str | None = field(default_factory=lambda: datetime.now(UTC).isoformat())
    note: str = ""
    _added_at_present: bool = field(default=True, repr=False, compare=False)
    _extra_fields: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    _raw_species_data: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    _species_baseline: dict[str, Any] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.species_key and self.species:
            self.species_key = _get_species_key(self.species)

    def to_dict(self) -> dict[str, Any]:
        """Serialize entry to dictionary."""
        species_data = deepcopy(self._raw_species_data)
        species_current = self.species.to_dict() if self.species else {}
        for key, value in species_current.items():
            baseline_value = (
                self._species_baseline.get(key)
                if self._species_baseline is not None
                else None
            )
            if (
                key not in species_data
                or self._species_baseline is None
                or value != baseline_value
            ):
                species_data[key] = deepcopy(value)
        data = deepcopy(self._extra_fields)
        data.update({
            "id": self.id,
            "species_key": self.species_key,
            "species": species_data,
            "note": self.note,
        })
        if self._added_at_present:
            data["added_at"] = self.added_at
        else:
            data.pop("added_at", None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlantListEntry:
        """Deserialize entry from dictionary."""
        species_raw = data.get("species", {})
        try:
            species_obj = (
                PlantSpeciesData.from_dict(species_raw)
                if species_raw
                else PlantSpeciesData(scientific_name="", common_name="")
            )
        except Exception:  # noqa: BLE001
            species_obj = PlantSpeciesData(
                scientific_name=species_raw.get("scientific_name", ""),
                common_name=species_raw.get("common_name", ""),
            )

        entry_id = data.get("id") or str(uuid.uuid4())
        sp_key = data.get("species_key") or _get_species_key(species_obj)
        added_at = data.get("added_at")
        note = data.get("note", "")
        if added_at is not None and not isinstance(added_at, str):
            raise ValueError("plant-list entry has an invalid added_at")
        if not isinstance(note, str):
            raise ValueError("plant-list entry has an invalid note")

        return cls(
            id=entry_id,
            species_key=sp_key,
            species=species_obj,
            added_at=added_at,
            note=note,
            _added_at_present="added_at" in data,
            _extra_fields={
                key: deepcopy(value)
                for key, value in data.items()
                if key not in {"id", "species_key", "species", "added_at", "note"}
            },
            _raw_species_data=deepcopy(species_raw) if isinstance(species_raw, dict) else {},
            _species_baseline=species_obj.to_dict(),
        )


@dataclass
class PlantList:
    """A named collection of plant species."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    entries: list[PlantListEntry] = field(default_factory=list)
    _extra_fields: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        """Serialize list to dictionary."""
        data = deepcopy(self._extra_fields)
        data.update({
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "entries": [entry.to_dict() for entry in self.entries],
        })
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlantList:
        """Deserialize list from dictionary."""
        list_id = data.get("id") or str(uuid.uuid4())
        name = data.get("name", "")
        desc = data.get("description", "")
        raw_entries = data.get("entries", [])
        entries = [PlantListEntry.from_dict(e) for e in raw_entries]
        return cls(
            id=list_id,
            name=name,
            description=desc,
            entries=entries,
            _extra_fields={
                key: deepcopy(value)
                for key, value in data.items()
                if key not in {"id", "name", "description", "entries"}
            },
        )


class PlantListStore(QObject):
    """Persistent storage for plant lists and favourites.

    Persists to ``<app-data>/plant_lists.json`` using same-directory
    atomic writes (temp file + ``os.replace``) to prevent corruption.
    The ``favorites`` list is reserved and always present.
    """

    changed = pyqtSignal()

    FILENAME = "plant_lists.json"
    FAVORITES_ID = "favorites"

    def __init__(self, storage_path: Path | None = None) -> None:
        super().__init__()
        if storage_path is None:
            from open_garden_planner.services.plant_library import get_app_data_dir

            self._path = get_app_data_dir() / self.FILENAME
        else:
            self._path = storage_path

        self._lists: dict[str, PlantList] = {}
        self._document_fields: dict[str, Any] = {}
        self._disk_fingerprint: str | None = None
        self._load_state = PersonalStoreLoadState.ABSENT
        self._load_error: PersonalStoreLoadError | None = None
        self._dirty = False
        self._load()

    @property
    def path(self) -> Path:
        """Return the storage file path."""
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

    # ── Persistence ───────────────────────────────────────────────────────────

    def _ensure_favorites(self) -> None:
        """Ensure the reserved favorites list always exists."""
        if self.FAVORITES_ID not in self._lists:
            self._lists[self.FAVORITES_ID] = PlantList(
                id=self.FAVORITES_ID,
                name="Favorites",
                description="Starred plant species",
                entries=[],
            )

    @staticmethod
    def _validate_document(raw_data: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Validate the persistent envelope before constructing typed records."""
        if isinstance(raw_data, list):
            lists_raw = raw_data  # legacy bare-list format
            document_fields: dict[str, Any] = {}
        elif isinstance(raw_data, dict):
            version = raw_data.get("version")
            if version is not None and version != "1.0":
                raise ValueError(f"unsupported plant-list store version: {version!r}")
            if "lists" not in raw_data:
                raise ValueError("plant-list store is missing its 'lists' array")
            lists_raw = raw_data["lists"]
            if not isinstance(lists_raw, list):
                raise ValueError("plant-list store 'lists' must be an array")
            document_fields = {
                key: deepcopy(value)
                for key, value in raw_data.items()
                if key not in {"version", "lists"}
            }
        else:
            raise ValueError("plant-list store must be an object or legacy array")

        list_ids: set[str] = set()
        entry_ids: set[str] = set()
        for index, raw_list in enumerate(lists_raw):
            if not isinstance(raw_list, dict):
                raise ValueError(f"plant list at index {index} must be an object")
            list_id = raw_list.get("id")
            if not isinstance(list_id, str) or not list_id.strip():
                raise ValueError(f"plant list at index {index} has no valid id")
            if list_id in list_ids:
                raise ValueError(f"duplicate plant-list id: {list_id!r}")
            list_ids.add(list_id)
            if not isinstance(raw_list.get("name", ""), str):
                raise ValueError(f"plant list {list_id!r} has an invalid name")
            if not isinstance(raw_list.get("description", ""), str):
                raise ValueError(f"plant list {list_id!r} has an invalid description")
            raw_entries = raw_list.get("entries", [])
            if not isinstance(raw_entries, list):
                raise ValueError(f"plant list {list_id!r} entries must be an array")
            for entry_index, raw_entry in enumerate(raw_entries):
                if not isinstance(raw_entry, dict):
                    raise ValueError(
                        f"entry {entry_index} in plant list {list_id!r} must be an object"
                    )
                entry_id = raw_entry.get("id")
                if not isinstance(entry_id, str) or not entry_id.strip():
                    raise ValueError(
                        f"entry {entry_index} in plant list {list_id!r} has no valid id"
                    )
                if entry_id in entry_ids:
                    raise ValueError(f"duplicate plant-list entry id: {entry_id!r}")
                entry_ids.add(entry_id)
                for field_name in ("species_key", "added_at"):
                    value = raw_entry.get(field_name)
                    if value is not None and not isinstance(value, str):
                        raise ValueError(
                            f"plant-list entry {entry_id!r} has an invalid {field_name}"
                        )
                if "note" in raw_entry and not isinstance(raw_entry["note"], str):
                    raise ValueError(
                        f"plant-list entry {entry_id!r} has an invalid note"
                    )
                species = raw_entry.get("species", {})
                if not isinstance(species, dict):
                    raise ValueError(
                        f"plant-list entry {entry_id!r} species must be an object"
                    )
                for field_name in ("scientific_name", "common_name"):
                    value = species.get(field_name)
                    if value is not None and not isinstance(value, str):
                        raise ValueError(
                            f"plant-list entry {entry_id!r} has invalid species {field_name}"
                        )
                PlantListStore._validate_species_snapshot(species, entry_id)
        return lists_raw, document_fields

    @staticmethod
    def _validate_species_snapshot(species: dict[str, Any], entry_id: str) -> None:
        """Validate known snapshot field shapes while retaining extensions."""
        string_fields = {
            "scientific_name", "common_name", "family", "genus", "soil_type",
            "flower_color", "foliage_color", "foliage_texture", "image_url",
            "thumbnail_url", "data_source", "source_id", "slug", "description",
            "cycle", "growth_rate", "permaculture_layer", "flower_type",
            "pollination_type", "sun_requirement", "water_needs",
        }
        nullable_string_fields = {
            "frost_tolerance", "nutrient_demand", "n_demand", "p_demand", "k_demand",
        }
        integer_fields = {
            "hardiness_zone_min", "hardiness_zone_max", "indoor_sow_start",
            "indoor_sow_end", "direct_sow_start", "direct_sow_end",
            "transplant_start", "transplant_end", "harvest_start", "harvest_end",
            "days_to_germination_min", "days_to_germination_max",
            "days_to_maturity_min", "days_to_maturity_max",
            "prick_out_after_days", "harden_off_days",
        }
        numeric_fields = {
            "min_height_cm", "max_height_cm", "min_spread_cm", "max_spread_cm",
            "ph_min", "ph_max", "min_germination_temp_c", "seed_depth_cm",
        }
        boolean_fields = {"edible", "flowering"}

        for field_name in string_fields:
            value = species.get(field_name)
            if field_name in species and not isinstance(value, str):
                raise ValueError(
                    f"plant-list entry {entry_id!r} has invalid species {field_name}"
                )
        for field_name in nullable_string_fields:
            value = species.get(field_name)
            if value is not None and not isinstance(value, str):
                raise ValueError(
                    f"plant-list entry {entry_id!r} has invalid species {field_name}"
                )
        for field_name in integer_fields:
            value = species.get(field_name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int)
            ):
                raise ValueError(
                    f"plant-list entry {entry_id!r} has invalid species {field_name}"
                )
            if value is None:
                continue
            if field_name in _SPECIES_CALENDAR_WEEK_FIELDS:
                in_range = abs(value) <= _MAX_CALENDAR_OFFSET_WEEKS
                supported_range = f"±{_MAX_CALENDAR_OFFSET_WEEKS} weeks"
            elif field_name in _SPECIES_CALENDAR_DURATION_FIELDS:
                in_range = 0 <= value <= _MAX_CALENDAR_DURATION_DAYS
                supported_range = f"0..{_MAX_CALENDAR_DURATION_DAYS} days"
            else:
                in_range = _QT_SPINBOX_INT_MIN <= value <= _QT_SPINBOX_INT_MAX
                supported_range = "the supported Qt integer range"
            if not in_range:
                raise ValueError(
                    f"plant-list entry {entry_id!r} has species {field_name} "
                    f"outside {supported_range}"
                )
        for field_name in numeric_fields:
            value = species.get(field_name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"plant-list entry {entry_id!r} has invalid species {field_name}"
                )
            try:
                finite = math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError(
                    f"plant-list entry {entry_id!r} has invalid species {field_name}"
                )
        for field_name in boolean_fields:
            value = species.get(field_name)
            if value is not None and not isinstance(value, bool):
                raise ValueError(
                    f"plant-list entry {entry_id!r} has invalid species {field_name}"
                )
        for field_name in ("functions", "edible_parts"):
            value = species.get(field_name)
            if value is not None and (
                not isinstance(value, list)
                or any(not isinstance(entry, str) for entry in value)
            ):
                raise ValueError(
                    f"plant-list entry {entry_id!r} has invalid species {field_name}"
                )
        raw_data = species.get("raw_data")
        if raw_data is not None and not isinstance(raw_data, dict):
            raise ValueError(
                f"plant-list entry {entry_id!r} has invalid species raw_data"
            )

    def _load(self) -> None:
        """Read and validate disk state without replacing failures with emptiness."""
        self._lists = {}
        self._document_fields = {}
        self._disk_fingerprint = None
        self._load_error = None
        self._dirty = False
        try:
            loaded = read_json(self._path)
            if loaded.state == PersonalStoreLoadState.ABSENT:
                self._load_state = PersonalStoreLoadState.ABSENT
                self._ensure_favorites()
                return
            lists_raw, self._document_fields = self._validate_document(loaded.value)
            for item in lists_raw:
                plant_list = PlantList.from_dict(item)
                self._lists[plant_list.id] = plant_list
            self._disk_fingerprint = loaded.fingerprint
            self._ensure_favorites()
            has_records = any(
                plant_list.id != self.FAVORITES_ID or plant_list.entries
                for plant_list in self._lists.values()
            )
            self._load_state = (
                PersonalStoreLoadState.LOADED
                if has_records
                else PersonalStoreLoadState.EMPTY
            )
        except (
            PersonalStoreLoadError, ValueError, TypeError, KeyError, RecursionError
        ) as exc:
            self._lists = {}
            self._document_fields = {}
            self._disk_fingerprint = None
            self._load_state = PersonalStoreLoadState.FAILED
            self._load_error = (
                exc if isinstance(exc, PersonalStoreLoadError)
                else PersonalStoreLoadError(f"Invalid plant-list store {self._path}: {exc}")
            )

    def reload(self) -> None:
        """Explicitly discard current memory and try loading the file again."""
        self._load()
        self.changed.emit()

    def _ensure_readable(self) -> None:
        if self._load_state == PersonalStoreLoadState.FAILED:
            raise self._load_error or PersonalStoreLoadError(
                f"Plant-list store is unreadable: {self._path}"
            )

    def _ensure_writable(self) -> None:
        self._ensure_readable()

    def _atomic_write(self, text: str) -> str:
        """Compatibility seam for tests and older callers of the store writer."""
        value = loads_json(text)
        return write_json(
            self._path,
            value,
            expected_fingerprint=self._disk_fingerprint,
        )

    def save(self) -> None:
        """Persist the current memory state or report why it was not saved.

        A write failure keeps edits in memory so the UI can offer a retry or an
        explicit reload.
        """
        self._ensure_writable()
        self._ensure_favorites()
        data = {
            **deepcopy(self._document_fields),
            "version": "1.0",
            "lists": [pl.to_dict() for pl in self.all_lists()],
        }
        try:
            # Mutable records and imported objects can be changed by callers;
            # never publish a document that this store would reject on reopen.
            self._validate_document(data)
            self._disk_fingerprint = self._atomic_write(
                json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False)
            )
        except PersonalStoreConflictError:
            self._dirty = True
            self.changed.emit()
            raise
        except Exception as exc:  # noqa: BLE001
            self._dirty = True
            self.changed.emit()
            raise PersonalStorePersistenceError(
                f"Changes remain in memory but could not be saved to {self._path}: {exc}"
            ) from exc
        has_records = any(
            plant_list.id != self.FAVORITES_ID or plant_list.entries
            for plant_list in self._lists.values()
        )
        self._load_state = (
            PersonalStoreLoadState.LOADED
            if has_records
            else PersonalStoreLoadState.EMPTY
        )
        self._dirty = False
        self._load_error = None
        self.changed.emit()

    # ── List operations ───────────────────────────────────────────────────────

    def all_lists(self) -> list[PlantList]:
        """Return all lists with Favorites first, followed by custom lists sorted by name."""
        self._ensure_readable()
        fav = self._lists.get(self.FAVORITES_ID)
        others = [
            pl for pl in self._lists.values() if pl.id != self.FAVORITES_ID
        ]
        others.sort(key=lambda pl: pl.name.lower())
        return [fav] + others if fav else others

    def get_list(self, list_id: str) -> PlantList | None:
        """Return list by ID or None."""
        self._ensure_readable()
        return self._lists.get(list_id)

    def create_list(self, name: str, description: str = "") -> PlantList:
        """Create a new custom plant list."""
        self._ensure_writable()
        clean_name = name.strip()
        if not clean_name:
            clean_name = "New List"
        new_list = PlantList(
            id=str(uuid.uuid4()),
            name=clean_name,
            description=description.strip(),
            entries=[],
        )
        self._lists[new_list.id] = new_list
        self.save()
        return new_list

    def rename_list(self, list_id: str, new_name: str) -> bool:
        """Rename a list. Reserved favorites cannot be renamed."""
        self._ensure_writable()
        if list_id == self.FAVORITES_ID:
            return False
        clean_name = new_name.strip()
        if not clean_name:
            return False
        pl = self._lists.get(list_id)
        if not pl:
            return False
        pl.name = clean_name
        self.save()
        return True

    def delete_list(self, list_id: str) -> bool:
        """Delete a list. Reserved favorites cannot be deleted."""
        self._ensure_writable()
        if list_id == self.FAVORITES_ID:
            return False
        if list_id in self._lists:
            del self._lists[list_id]
            self.save()
            return True
        return False

    # ── Entry operations ──────────────────────────────────────────────────────

    def get_entry(
        self, entry_id: str
    ) -> tuple[PlantList, PlantListEntry] | tuple[None, None]:
        """Find an entry across all lists by its ID."""
        self._ensure_readable()
        for pl in self._lists.values():
            for entry in pl.entries:
                if entry.id == entry_id:
                    return pl, entry
        return None, None

    def add_entry(
        self,
        list_id: str,
        species: PlantSpeciesData,
        note: str = "",
    ) -> PlantListEntry | None:
        """Add a species to a list.

        If already present (matching species_key), updates its snapshot and note,
        avoiding duplicate entries.
        """
        self._ensure_writable()
        pl = self._lists.get(list_id)
        if not pl:
            return None

        sp_key = _get_species_key(species)
        for existing in pl.entries:
            if existing.species_key == sp_key:
                existing.species = species
                if note:
                    existing.note = note.strip()
                self.save()
                return existing

        entry = PlantListEntry(
            id=str(uuid.uuid4()),
            species_key=sp_key,
            species=species,
            added_at=datetime.now(UTC).isoformat(),
            note=note.strip(),
        )
        pl.entries.append(entry)
        self.save()
        return entry

    def remove_entry(self, list_id: str, entry_id: str) -> bool:
        """Remove an entry from a list."""
        self._ensure_writable()
        pl = self._lists.get(list_id)
        if not pl:
            return False
        initial_len = len(pl.entries)
        pl.entries = [e for e in pl.entries if e.id != entry_id]
        if len(pl.entries) < initial_len:
            self.save()
            return True
        return False

    def move_entry(self, from_list_id: str, to_list_id: str, entry_id: str) -> bool:
        """Move an entry from one list to another."""
        if from_list_id == to_list_id:
            return False
        self._ensure_writable()
        from_list = self._lists.get(from_list_id)
        to_list = self._lists.get(to_list_id)
        if not from_list or not to_list:
            return False

        entry_to_move: PlantListEntry | None = None
        new_from_entries: list[PlantListEntry] = []
        for e in from_list.entries:
            if e.id == entry_id:
                entry_to_move = e
            else:
                new_from_entries.append(e)

        if not entry_to_move:
            return False

        from_list.entries = new_from_entries
        # Avoid duplicate in destination
        to_list.entries = [
            e for e in to_list.entries if e.species_key != entry_to_move.species_key
        ]
        to_list.entries.append(entry_to_move)
        self.save()
        return True

    def update_entry_note(self, list_id: str, entry_id: str, note: str) -> bool:
        """Update note for an entry."""
        self._ensure_writable()
        pl = self._lists.get(list_id)
        if not pl:
            return False
        for e in pl.entries:
            if e.id == entry_id:
                e.note = note.strip()
                self.save()
                return True
        return False

    def update_entry_species(
        self, list_id: str, entry_id: str, species: PlantSpeciesData
    ) -> bool:
        """Update species snapshot for an entry ('Update from plan')."""
        self._ensure_writable()
        pl = self._lists.get(list_id)
        if not pl:
            return False
        for e in pl.entries:
            if e.id == entry_id:
                e.species = species
                e.species_key = _get_species_key(species)
                known_fields = set(PlantSpeciesData.__dataclass_fields__)
                e._raw_species_data = {
                    key: value
                    for key, value in e._raw_species_data.items()
                    if key not in known_fields
                }
                e._species_baseline = species.to_dict()
                self.save()
                return True
        return False

    # ── Favorites shortcuts ───────────────────────────────────────────────────

    def is_favorite(self, species_or_key: PlantSpeciesData | dict[str, Any] | str) -> bool:
        """Check whether a species is in the favorites list."""
        self._ensure_readable()
        fav = self._lists.get(self.FAVORITES_ID)
        if not fav:
            return False
        target_key = _get_species_key(species_or_key)
        if not target_key or target_key == "_unknown":
            return False
        return any(e.species_key == target_key for e in fav.entries)

    def toggle_favorite(
        self, species: PlantSpeciesData, note: str = ""
    ) -> bool:
        """Toggle favorite status.

        Returns:
            True if now favorited, False if unfavorited.
        """
        self._ensure_writable()
        fav = self._lists.get(self.FAVORITES_ID)
        if not fav:
            self._ensure_favorites()
            fav = self._lists[self.FAVORITES_ID]

        sp_key = _get_species_key(species)
        for existing in fav.entries:
            if existing.species_key == sp_key:
                self.remove_entry(self.FAVORITES_ID, existing.id)
                return False

        self.add_entry(self.FAVORITES_ID, species, note=note)
        return True

    # ── JSON Export / Import ──────────────────────────────────────────────────

    def export_list_to_json(self, list_id: str) -> str:
        """Export a list to a formatted JSON string."""
        self._ensure_readable()
        pl = self._lists.get(list_id)
        if not pl:
            raise KeyError(f"Plant list not found: {list_id}")
        data = {
            "version": "1.0",
            "type": "open_garden_planner_plant_list",
            "list": pl.to_dict(),
        }
        return json.dumps(data, indent=2)

    def import_list_from_json(self, json_text: str) -> PlantList:
        """Import a plant list from a JSON string.

        Guarantees unique ID (never overwrites the reserved favorites id)
        and unique list name.
        """
        self._ensure_writable()
        data = loads_json(json_text)
        if isinstance(data, dict) and "list" in data:
            list_data = data["list"]
        elif isinstance(data, dict):
            list_data = data
        else:
            raise ValueError("Invalid plant list JSON structure")

        self._validate_document([list_data])

        imported = PlantList.from_dict(list_data)

        # Do not allow overwriting favorites directly via import
        if imported.id == self.FAVORITES_ID:
            imported.name = f"{imported.name} (Imported)"

        # Ensure unique name
        existing_names = {pl.name.lower() for pl in self._lists.values()}
        base_name = imported.name or "Imported List"
        candidate_name = base_name
        counter = 1
        while candidate_name.lower() in existing_names:
            candidate_name = f"{base_name} ({counter})"
            counter += 1
        imported.name = candidate_name

        # Ensure fresh IDs for list and its entries
        imported.id = str(uuid.uuid4())
        for entry in imported.entries:
            entry.id = str(uuid.uuid4())

        self._lists[imported.id] = imported
        self.save()
        return imported


# ── Global store accessor ─────────────────────────────────────────────────────

_STORE: PlantListStore | None = None


def get_plant_list_store(storage_path: Path | None = None) -> PlantListStore:
    """Return application-wide PlantListStore singleton."""
    global _STORE
    if _STORE is None or (storage_path is not None and _STORE.path != storage_path):
        _STORE = PlantListStore(storage_path)
    return _STORE


def reset_plant_list_store() -> None:
    """Reset the module-level singleton (useful in test teardown)."""
    global _STORE
    _STORE = None
