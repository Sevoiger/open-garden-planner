"""Explicitly remember an owner's visual without rewriting botanical identities."""

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from open_garden_planner.core.file_lock import ProcessFileLock
from open_garden_planner.core.personal_plant_visual import VISUAL_KEY
from open_garden_planner.models.plant_data import species_key
from open_garden_planner.services.library_operations import (
    bump_record_versions,
    changed_library_record_ids,
    commit_library_document,
)
from open_garden_planner.services.local_plant_import import _read_custom_store
from open_garden_planner.services.plant_library import PlantLibrary


def remember_visual(library: PlantLibrary, snapshot: dict[str, Any]) -> str:
    """Patch only the art field, back up and atomically save, then refresh memory.

    This is a library preference, separate from the document's undo history.
    Other fields (including unknown fields) and unrelated records stay untouched.
    """
    key = species_key(snapshot)
    if key == "_unknown":
        raise ValueError("A named plant is required")
    visual = snapshot.get("raw_data", {}).get(VISUAL_KEY)
    if visual is not None and not isinstance(visual, str):
        raise ValueError("Invalid personal visual")
    path = library._library_path
    with ProcessFileLock(path.with_name(path.name + ".writer.lock")):
        store, original, fingerprint = _read_custom_store(path)
        before_plants = deepcopy(store["plants"])
        matches: list[str] = [record_id for record_id, record in store["plants"].items()
                   if species_key(record) == key]
        if not matches:
            record_id = f"personal-visual:{uuid4()}"
            store["plants"][record_id] = deepcopy(snapshot)
            matches = [record_id]
        for record_id in matches:
            raw = store["plants"][record_id].setdefault("raw_data", {})
            if visual is None:
                raw.pop(VISUAL_KEY, None)
            else:
                raw[VISUAL_KEY] = visual

        changed_ids = changed_library_record_ids(before_plants, store["plants"])
        if original is not None:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            path.with_name(f"{path.stem}.before-visual-{stamp}.json").write_bytes(original)
        if changed_ids:
            bump_record_versions(store, changed_ids)
        commit_library_document(
            path,
            store,
            expected_fingerprint=fingerprint,
        )
    library._load()
    return matches[0]
