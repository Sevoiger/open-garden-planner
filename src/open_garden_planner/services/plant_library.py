"""Custom plant library for storing user-defined plant species.

The application singleton keeps its historical API. Callers that need an
isolated or headless store can inject an explicit JSON path.
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from open_garden_planner.core.file_lock import ProcessFileLock
from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services.library_operations import (
    LibraryConflictError,
    LibraryFileError,
    LibraryOperationError,
    bump_record_versions,
    changed_library_record_ids,
    commit_library_document,
    read_library_document,
    serialize_species_record,
)


def get_app_data_dir() -> Path:
    """Get and create the application data directory for user data."""
    app_dir = _default_app_data_dir()
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def _default_app_data_dir() -> Path:
    """Resolve the platform location without creating or reading user data."""
    import sys

    if sys.platform == "win32":
        # Windows: %APPDATA%/OpenGardenPlanner
        import os

        app_data = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        # macOS: ~/Library/Application Support
        app_data = Path.home() / "Library" / "Application Support"
    else:
        # Linux/Unix: ~/.local/share/OpenGardenPlanner
        import os

        xdg_data = os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
        app_data = Path(xdg_data)

    return app_data / "OpenGardenPlanner"


def _lock_path(path: Path) -> Path:
    return path.with_name(path.name + ".writer.lock")


class PlantLibrary:
    """Manage a user-owned library of custom plant species.

    The default constructor uses the application data location. Passing a path
    bypasses that resolver entirely and is suitable for isolated sessions and
    tests. Unknown document and record fields are retained through edits.
    """

    LIBRARY_FILENAME = "custom_plants.json"

    def __init__(self, path: Path | None = None) -> None:
        self._plants: dict[str, PlantSpeciesData] = {}
        self._raw_document: dict[str, Any] = {"version": "1.0", "plants": {}}
        self._raw_plants: dict[str, Any] = {}
        self._disk_fingerprint: str | None = None
        self._load_error: LibraryOperationError | None = None
        self._library_path = (
            Path(path).expanduser().resolve()
            if path is not None
            else get_app_data_dir() / self.LIBRARY_FILENAME
        )
        self._load()

    def _load(self) -> None:
        """Load records while retaining raw JSON for forward-compatible writes."""
        self._plants = {}
        self._raw_document = {"version": "1.0", "plants": {}}
        self._raw_plants = {}
        self._load_error = None
        self._disk_fingerprint = None
        try:
            document, _, fingerprint = read_library_document(self._library_path)
        except LibraryOperationError as exc:
            # Reads degrade to an empty visible list, as before. Mutations are
            # refused until the owner repairs the file; never overwrite it.
            self._load_error = exc
            return

        self._raw_document = deepcopy(document)
        self._raw_plants = deepcopy(document["plants"])
        self._disk_fingerprint = fingerprint
        for plant_id, plant_data in self._raw_plants.items():
            if not isinstance(plant_id, str) or not isinstance(plant_data, dict):
                continue
            try:
                self._plants[plant_id] = PlantSpeciesData.from_dict(plant_data)
            except Exception:
                # Individual records are an untrusted ingestion boundary. Keep
                # their raw JSON untouched for forward compatibility, but omit
                # them from the typed search/edit API until they parse.
                continue

    def _ensure_writable(self) -> str:
        if self._load_error is not None:
            raise LibraryFileError(
                f"Plant library is unreadable; refusing to replace it: {self._load_error}"
            ) from self._load_error
        if self._disk_fingerprint is None:
            raise LibraryFileError("Plant library state has not been loaded")
        return self._disk_fingerprint

    @property
    def revision(self) -> str:
        """Fingerprint of the library state currently loaded by this instance."""
        return self._ensure_writable()

    def _commit(
        self,
        document: dict[str, Any],
        *,
        expected_revision: str | None = None,
    ) -> None:
        loaded_revision = self._ensure_writable()
        if expected_revision is not None and expected_revision != loaded_revision:
            raise LibraryConflictError(
                "Plant library revision changed since this record was opened"
            )
        expected = expected_revision if expected_revision is not None else loaded_revision
        previous_plants = self._raw_document.get("plants", {})
        candidate_plants = document.get("plants", {})
        changed_ids = changed_library_record_ids(previous_plants, candidate_plants)
        if not changed_ids and document == self._raw_document:
            if expected_revision is not None:
                with ProcessFileLock(_lock_path(self._library_path)):
                    _, _, current_revision = read_library_document(self._library_path)
                    if current_revision != expected:
                        raise LibraryConflictError(
                            "Plant library changed before this edit could be checked"
                        )
            return
        if changed_ids:
            bump_record_versions(document, changed_ids)
        with ProcessFileLock(_lock_path(self._library_path)):
            commit_library_document(
                self._library_path,
                document,
                expected_fingerprint=expected,
            )
        self._load()
        if self._load_error is not None:
            raise self._load_error

    def _save(self) -> None:
        """Persist the current typed plants without dropping unknown JSON."""
        self._ensure_writable()
        document = deepcopy(self._raw_document)
        raw_plants = document.setdefault("plants", {})
        for plant_id, plant in self._plants.items():
            previous = raw_plants.get(plant_id)
            if previous is not None and not isinstance(previous, dict):
                raise LibraryConflictError(
                    f"Cannot replace malformed plant record {plant_id!r}"
                )
            raw_plants[plant_id] = serialize_species_record(
                plant,
                existing=previous,
            )
        self._commit(document)

    def add_plant(self, plant: PlantSpeciesData) -> str:
        """Add or replace a custom plant and return its stable library ID."""
        if not isinstance(plant, PlantSpeciesData):
            raise TypeError("plant must be PlantSpeciesData")
        self._ensure_writable()
        stored = deepcopy(plant)
        stored.data_source = "custom"
        if not stored.source_id:
            stored.source_id = str(uuid.uuid4())
        plant_id = stored.source_id

        document = deepcopy(self._raw_document)
        raw_plants = document.setdefault("plants", {})
        previous = raw_plants.get(plant_id)
        if previous is not None and not isinstance(previous, dict):
            raise LibraryConflictError(f"Cannot replace malformed plant record {plant_id!r}")
        if isinstance(previous, dict):
            try:
                PlantSpeciesData.from_dict(previous)
            except Exception as exc:
                raise LibraryConflictError(
                    f"Cannot replace unrecognized plant record {plant_id!r}"
                ) from exc
        raw_plants[plant_id] = serialize_species_record(stored, existing=previous)
        self._commit(document)

        # Retain the historical caller-visible source assignment, but only
        # after the durable write has succeeded.
        plant.data_source = "custom"
        plant.source_id = plant_id
        return plant_id

    def update_plant(
        self,
        plant_id: str,
        plant: PlantSpeciesData,
        *,
        expected_revision: str | None = None,
    ) -> bool:
        """Update an existing typed entry; return False when it is absent."""
        if not isinstance(plant, PlantSpeciesData):
            raise TypeError("plant must be PlantSpeciesData")
        loaded_revision = self._ensure_writable()
        if expected_revision is not None and expected_revision != loaded_revision:
            raise LibraryConflictError(
                "Plant library revision changed since this record was opened"
            )
        if plant_id not in self._plants:
            return False
        stored = deepcopy(plant)
        stored.data_source = "custom"
        stored.source_id = plant_id

        document = deepcopy(self._raw_document)
        raw_plants = document.setdefault("plants", {})
        previous = raw_plants.get(plant_id)
        if not isinstance(previous, dict):
            raise LibraryConflictError(f"Plant record {plant_id!r} is no longer writable")
        raw_plants[plant_id] = serialize_species_record(stored, existing=previous)
        self._commit(document, expected_revision=expected_revision)
        plant.data_source = "custom"
        plant.source_id = plant_id
        return True

    def remove_plant(self, plant_id: str) -> bool:
        """Remove an existing typed entry and return whether it was present."""
        if plant_id not in self._plants:
            return False
        self._ensure_writable()
        document = deepcopy(self._raw_document)
        document.setdefault("plants", {}).pop(plant_id, None)
        self._commit(document)
        return True

    def get_plant(self, plant_id: str) -> PlantSpeciesData | None:
        """Return a typed plant by its exact source/library ID."""
        return self._plants.get(plant_id)

    def get_all_plants(self) -> list[tuple[str, PlantSpeciesData]]:
        """Return typed, parseable plants and their exact IDs."""
        return list(self._plants.items())

    def search_plants(self, query: str) -> list[PlantSpeciesData]:
        """Search visible custom plants by scientific/common name or family."""
        query_lower = query.lower()
        return [
            plant
            for plant in self._plants.values()
            if (
                query_lower in plant.scientific_name.lower()
                or query_lower in plant.common_name.lower()
                or query_lower in plant.family.lower()
            )
        ]

    @property
    def count(self) -> int:
        """Number of records that parse as ``PlantSpeciesData``."""
        return len(self._plants)

    def to_dict(self) -> dict[str, Any]:
        """Export all stored plant records, preserving unknown fields."""
        result = deepcopy(self._raw_plants)
        for plant_id, plant in self._plants.items():
            previous = result.get(plant_id)
            result[plant_id] = serialize_species_record(
                plant,
                existing=previous if isinstance(previous, dict) else None,
            )
        return result

    def import_from_dict(self, data: dict[str, Any]) -> int:
        """Import previously unseen IDs while preserving all supplied extensions."""
        if not isinstance(data, dict):
            raise TypeError("data must map explicit plant IDs to records")
        self._ensure_writable()
        document = deepcopy(self._raw_document)
        raw_plants = document.setdefault("plants", {})
        imported = 0
        for plant_id, plant_data in data.items():
            if not isinstance(plant_id, str) or not plant_id or plant_id in raw_plants:
                continue
            try:
                if not isinstance(plant_data, dict):
                    continue
                plant = PlantSpeciesData.from_dict(plant_data)
                plant.data_source = "custom"
                plant.source_id = plant_id
                raw_plants[plant_id] = serialize_species_record(
                    plant,
                    supplied=plant_data,
                )
                imported += 1
            except Exception:
                # Keep the historical import contract: skip malformed incoming
                # records, while refusing only an unreadable whole library.
                continue

        if imported > 0:
            self._commit(document)
        return imported


# Singleton instance for app-wide access. Merely importing this module does not
# resolve or read the user's application-data directory.
_library_instance: PlantLibrary | None = None


def get_plant_library() -> PlantLibrary:
    """Get the process-wide custom plant library instance."""
    global _library_instance
    if _library_instance is None:
        _library_instance = PlantLibrary()
    return _library_instance
