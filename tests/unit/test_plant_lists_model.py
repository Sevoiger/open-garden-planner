"""Unit tests for PlantList and PlantListStore (US-G4, issue #320)."""

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from open_garden_planner.core.personal_store import (
    PersonalStoreConflictError,
    PersonalStoreLoadError,
    PersonalStoreLoadState,
    PersonalStorePersistenceError,
)
from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.models.plant_lists import (
    PlantListEntry,
    PlantListStore,
    _get_species_key,
)


def test_species_key_extraction() -> None:
    """_get_species_key extracts lowercased key with fallback hierarchy."""
    sp = PlantSpeciesData(
        scientific_name="Solanum lycopersicum",
        common_name="Tomato",
        source_id="bundled-tomato",
    )
    assert _get_species_key(sp) == "bundled-tomato"

    sp2 = PlantSpeciesData(
        scientific_name="Malus domestica",
        common_name="Apple",
    )
    assert _get_species_key(sp2) == "malus domestica"

    d = {"common_name": "Pear"}
    assert _get_species_key(d) == "pear"
    d_apple = {"common_name": "Apple Tree"}
    assert _get_species_key(d_apple) == "malus domestica"
    assert _get_species_key("apple tree") == "malus domestica"
    assert _get_species_key("Carrot") == "daucus carota"
    assert _get_species_key("Custom Unknown 123") == "custom unknown 123"


def test_plant_list_entry_serialization_roundtrip() -> None:
    """PlantListEntry round-trips through dict preserving snapshot."""
    sp = PlantSpeciesData(
        scientific_name="Solanum lycopersicum",
        common_name="Tomato",
        max_spread_cm=60.0,
        max_height_cm=150.0,
    )
    entry = PlantListEntry(species=sp, note="Cherry tomato variety")
    assert entry.added_at
    entry_dict = entry.to_dict()

    restored = PlantListEntry.from_dict(entry_dict)
    assert restored.id == entry.id
    assert restored.species_key == entry.species_key
    assert restored.species.scientific_name == "Solanum lycopersicum"
    assert restored.species.max_spread_cm == 60.0
    assert restored.note == "Cherry tomato variety"


def test_plant_list_entry_corrupt_species_dict_fallback() -> None:
    """PlantListEntry handles unresolvable or corrupted species data without crashing."""
    raw = {
        "id": "abc-123",
        "species_key": "mystery-plant",
        "species": {"scientific_name": "Mystery sp.", "cycle": "invalid_enum_value"},
        "note": "A note",
    }
    entry = PlantListEntry.from_dict(raw)
    assert entry.id == "abc-123"
    assert entry.species_key == "mystery-plant"
    assert entry.species.scientific_name == "Mystery sp."
    assert entry.to_dict()["species"]["cycle"] == "invalid_enum_value"


@pytest.mark.parametrize(
    ("stored_added_at", "expected_added_at", "expected_key"),
    [
        ({}, None, False),
        ({"added_at": None}, None, True),
        ({"added_at": ""}, "", True),
    ],
)
def test_plant_list_entry_keeps_unknown_added_at_unknown(
    stored_added_at: dict[str, Any],
    expected_added_at: str | None,
    expected_key: bool,
) -> None:
    raw = {
        "id": "entry",
        "species_key": "example plant",
        "species": {"scientific_name": "Example plant", "common_name": "Example"},
        **stored_added_at,
    }

    entry = PlantListEntry.from_dict(raw)

    assert entry.added_at == expected_added_at
    assert ("added_at" in entry.to_dict()) is expected_key
    assert entry.to_dict().get("added_at") == expected_added_at


def test_store_rejects_null_entry_note_without_overwriting_source(
    tmp_path: Path,
) -> None:
    path = tmp_path / "plant_lists.json"
    original = json.dumps({
        "version": "1.0",
        "lists": [{
            "id": "orchard",
            "name": "Orchard",
            "entries": [{
                "id": "apple",
                "species_key": "malus domestica",
                "species": {"scientific_name": "Malus domestica"},
                "note": None,
            }],
        }],
    }).encode("utf-8")
    path.write_bytes(original)

    store = PlantListStore(path)

    assert store.load_state == PersonalStoreLoadState.FAILED
    with pytest.raises(PersonalStoreLoadError, match="invalid note"):
        store.all_lists()
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("indoor_sow_start", 1_000_000_000),
        ("direct_sow_end", (date.max - date.min).days // 7 + 1),
        ("days_to_germination_min", (date.max - date.min).days + 1),
        ("prick_out_after_days", -1),
        ("hardiness_zone_min", 2**31),
    ],
)
def test_store_rejects_calendar_integer_fields_outside_technical_limits(
    tmp_path: Path,
    field_name: str,
    invalid_value: int,
) -> None:
    path = tmp_path / "plant_lists.json"
    raw = {
        "version": "1.0",
        "lists": [{
            "id": "garden",
            "name": "Garden",
            "entries": [{
                "id": "plant",
                "species_key": "example plant",
                "species": {
                    "scientific_name": "Example plant",
                    "common_name": "Example",
                    field_name: invalid_value,
                },
            }],
        }],
    }
    original = json.dumps(raw).encode("utf-8")
    path.write_bytes(original)

    store = PlantListStore(path)

    assert store.load_state == PersonalStoreLoadState.FAILED
    with pytest.raises(PersonalStoreLoadError, match=field_name):
        store.all_lists()
    assert path.read_bytes() == original


def test_store_creates_default_favorites(tmp_path: Path) -> None:
    """A fresh store creates a reserved favorites list."""
    storage_file = tmp_path / "plant_lists.json"
    store = PlantListStore(storage_file)

    lists = store.all_lists()
    assert len(lists) == 1
    assert lists[0].id == PlantListStore.FAVORITES_ID
    assert lists[0].name == "Favorites"


def test_store_atomic_persistence(tmp_path: Path) -> None:
    """Adding entries and lists persists atomically to JSON."""
    storage_file = tmp_path / "plant_lists.json"
    store = PlantListStore(storage_file)

    tomato = PlantSpeciesData(
        scientific_name="Solanum lycopersicum", common_name="Tomato"
    )
    store.add_entry(PlantListStore.FAVORITES_ID, tomato, note="My favorite red tomato")

    assert storage_file.exists()
    with open(storage_file, encoding="utf-8") as f:
        data = json.load(f)
    assert "lists" in data
    assert any(pl["id"] == "favorites" for pl in data["lists"])

    # Reload store from same file
    store2 = PlantListStore(storage_file)
    assert store2.is_favorite(tomato)
    fav = store2.get_list(PlantListStore.FAVORITES_ID)
    assert fav is not None
    assert len(fav.entries) == 1
    assert fav.entries[0].note == "My favorite red tomato"


def test_store_cannot_rename_or_delete_favorites(tmp_path: Path) -> None:
    """The reserved favorites list cannot be deleted or renamed."""
    storage_file = tmp_path / "plant_lists.json"
    store = PlantListStore(storage_file)

    assert not store.rename_list(PlantListStore.FAVORITES_ID, "Hacked")
    assert not store.delete_list(PlantListStore.FAVORITES_ID)

    fav = store.get_list(PlantListStore.FAVORITES_ID)
    assert fav is not None
    assert fav.name == "Favorites"


def test_store_crud_custom_lists(tmp_path: Path) -> None:
    """Custom lists can be created, renamed, populated, and deleted."""
    storage_file = tmp_path / "plant_lists.json"
    store = PlantListStore(storage_file)

    orchard = store.create_list("Orchard Trees", "Fruit tree varieties")
    assert orchard.id != PlantListStore.FAVORITES_ID
    assert orchard.name == "Orchard Trees"

    apple = PlantSpeciesData(scientific_name="Malus domestica", common_name="Apple")
    entry = store.add_entry(orchard.id, apple, note="Honeycrisp")
    assert entry is not None
    assert entry.species_key == "malus domestica"

    # Search entry
    found_list, found_entry = store.get_entry(entry.id)
    assert found_list is not None and found_list.id == orchard.id
    assert found_entry is not None and found_entry.id == entry.id

    # Update note
    assert store.update_entry_note(orchard.id, entry.id, "Honeycrisp updated note")
    assert found_entry.note == "Honeycrisp updated note"

    # Rename list
    assert store.rename_list(orchard.id, "Fruit Orchard")
    assert store.get_list(orchard.id).name == "Fruit Orchard"

    # Move entry to favorites
    assert store.move_entry(orchard.id, PlantListStore.FAVORITES_ID, entry.id)
    assert len(store.get_list(orchard.id).entries) == 0
    assert len(store.get_list(PlantListStore.FAVORITES_ID).entries) == 1

    # Delete custom list
    assert store.delete_list(orchard.id)
    assert store.get_list(orchard.id) is None


def test_store_toggle_favorite(tmp_path: Path) -> None:
    """toggle_favorite toggles status and persists."""
    storage_file = tmp_path / "plant_lists.json"
    store = PlantListStore(storage_file)

    carrot = PlantSpeciesData(scientific_name="Daucus carota", common_name="Carrot")
    assert not store.is_favorite(carrot)

    # First toggle: adds to favorites
    result1 = store.toggle_favorite(carrot)
    assert result1 is True
    assert store.is_favorite(carrot)

    # Second toggle: removes from favorites
    result2 = store.toggle_favorite(carrot)
    assert result2 is False
    assert not store.is_favorite(carrot)


def test_store_update_from_plan(tmp_path: Path) -> None:
    """update_entry_species updates the stored snapshot explicitly."""
    storage_file = tmp_path / "plant_lists.json"
    store = PlantListStore(storage_file)

    plant_orig = PlantSpeciesData(
        scientific_name="Solanum lycopersicum",
        common_name="Tomato",
        max_height_cm=100.0,
    )
    entry = store.add_entry(PlantListStore.FAVORITES_ID, plant_orig)
    assert entry is not None

    plant_modified = PlantSpeciesData(
        scientific_name="Solanum lycopersicum",
        common_name="Tomato",
        max_height_cm=180.0,
    )
    assert store.update_entry_species(
        PlantListStore.FAVORITES_ID, entry.id, plant_modified
    )

    fav = store.get_list(PlantListStore.FAVORITES_ID)
    assert fav.entries[0].species.max_height_cm == 180.0


def test_export_import_json(tmp_path: Path) -> None:
    """Lists can be exported and imported as JSON round-trip."""
    storage_file = tmp_path / "plant_lists.json"
    store = PlantListStore(storage_file)

    herbs = store.create_list("Kitchen Herbs", "Cooking herbs")
    basil = PlantSpeciesData(scientific_name="Ocimum basilicum", common_name="Basil")
    store.add_entry(herbs.id, basil, note="Sweet basil")

    exported_json = store.export_list_to_json(herbs.id)
    assert "Kitchen Herbs" in exported_json
    assert "Ocimum basilicum" in exported_json

    # Import into a second store
    storage_file2 = tmp_path / "plant_lists_profile2.json"
    store2 = PlantListStore(storage_file2)
    imported = store2.import_list_from_json(exported_json)

    assert imported.name == "Kitchen Herbs"
    assert len(imported.entries) == 1
    assert imported.entries[0].species.scientific_name == "Ocimum basilicum"
    assert imported.entries[0].note == "Sweet basil"


def test_species_key_branches() -> None:
    """_get_species_key covers all branches and fallback paths."""
    assert _get_species_key("") == "_unknown"
    assert _get_species_key("   ") == "_unknown"

    sp_source = PlantSpeciesData(
        source_id="src-apple", scientific_name="Malus", common_name="Apple"
    )
    assert _get_species_key(sp_source) == "src-apple"

    sp_common_hit = PlantSpeciesData(scientific_name="", common_name="Apple Tree")
    assert _get_species_key(sp_common_hit) == "malus domestica"

    sp_common_miss = PlantSpeciesData(
        scientific_name="", common_name="Exotic Alien Plant 999"
    )
    assert _get_species_key(sp_common_miss) == "exotic alien plant 999"

    d_src = {"source_id": "custom-id-1"}
    assert _get_species_key(d_src) == "custom-id-1"

    d_sci = {"scientific_name": "Rosa canina"}
    assert _get_species_key(d_sci) == "rosa canina"

    d_comm_hit = {"common_name": "Apple Tree"}
    assert _get_species_key(d_comm_hit) == "malus domestica"

    d_comm_miss = {"common_name": "Unknown Flower 456"}
    assert _get_species_key(d_comm_miss) == "unknown flower 456"

    d_empty: dict[str, Any] = {}
    assert _get_species_key(d_empty) == "_unknown"

    sp_no_common = PlantSpeciesData(scientific_name="", common_name="")
    assert _get_species_key(sp_no_common) == "_unknown"

    assert _get_species_key(None) == "_unknown"  # type: ignore[arg-type]
    assert _get_species_key(12345) == "_unknown"  # type: ignore[arg-type]


def test_store_initialization_variations(tmp_path: Path) -> None:
    """Store initializes correctly with default path, bare list JSON, primitives, and malformed files."""
    # Default path uses app data dir
    store_default = PlantListStore(None)
    assert store_default.path.name == "plant_lists.json"

    # Bare list JSON
    bare_file = tmp_path / "bare_list.json"
    bare_file.write_text(
        json.dumps([{"id": "list-bare", "name": "Bare List", "entries": []}]),
        encoding="utf-8",
    )
    store_bare = PlantListStore(bare_file)
    assert store_bare.get_list("list-bare") is not None
    assert store_bare.get_list(PlantListStore.FAVORITES_ID) is not None
    assert store_bare.load_state == PersonalStoreLoadState.LOADED

    # Primitive string JSON
    prim_file = tmp_path / "prim.json"
    prim_file.write_text(json.dumps("invalid string payload"), encoding="utf-8")
    store_prim = PlantListStore(prim_file)
    assert store_prim.load_state == PersonalStoreLoadState.FAILED
    with pytest.raises(PersonalStoreLoadError):
        store_prim.all_lists()
    with pytest.raises(PersonalStoreLoadError):
        store_prim.save()
    assert prim_file.read_text(encoding="utf-8") == json.dumps("invalid string payload")

    # Corrupt JSON file
    corrupt_file = tmp_path / "corrupt.json"
    corrupt_file.write_text("{corrupt json content", encoding="utf-8")
    store_corrupt = PlantListStore(corrupt_file)
    assert store_corrupt.load_state == PersonalStoreLoadState.FAILED
    with pytest.raises(PersonalStoreLoadError):
        store_corrupt.all_lists()
    with pytest.raises(PersonalStoreLoadError):
        store_corrupt.create_list("must not overwrite")
    assert corrupt_file.read_text(encoding="utf-8") == "{corrupt json content"


def test_store_atomic_write_error(tmp_path: Path, monkeypatch: Any) -> None:
    """Atomic write handles exceptions and cleans up temporary file."""
    import os

    storage_file = tmp_path / "error_write.json"
    store = PlantListStore(storage_file)

    def mock_replace(_src: str, _dst: str) -> None:
        raise OSError("Disk simulated write failure")

    monkeypatch.setattr(os, "replace", mock_replace)

    with pytest.raises(PersonalStorePersistenceError, match="Disk simulated write failure"):
        store.save()
    assert store.is_dirty
    assert not storage_file.exists()


def test_store_states_and_unreadable_file_recovery(tmp_path: Path) -> None:
    path = tmp_path / "plant_lists.json"
    store = PlantListStore(path)
    assert store.load_state == PersonalStoreLoadState.ABSENT

    path.write_text('{"version":"1.0","lists":[]}', encoding="utf-8")
    store.reload()
    assert store.load_state == PersonalStoreLoadState.EMPTY
    assert store.get_list(PlantListStore.FAVORITES_ID) is not None

    original = b'{"version":"1.0","lists":[{"id":"bad","entries":7}]}'
    path.write_bytes(original)
    store.reload()
    assert store.load_state == PersonalStoreLoadState.FAILED
    with pytest.raises(PersonalStoreLoadError):
        store.all_lists()
    with pytest.raises(PersonalStoreLoadError):
        store.save()
    assert path.read_bytes() == original

    path.write_text('{"version":"1.0","lists":[]}', encoding="utf-8")
    store.reload()
    assert store.load_state == PersonalStoreLoadState.EMPTY
    assert store.load_error is None


def test_unreadable_path_is_not_presented_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "plant_lists.json"
    path.mkdir()
    store = PlantListStore(path)
    assert store.load_state == PersonalStoreLoadState.FAILED
    with pytest.raises(PersonalStoreLoadError):
        store.all_lists()
    with pytest.raises(PersonalStoreLoadError):
        store.create_list("not saved")
    assert path.is_dir()


def test_store_preserves_unknown_fields_through_an_edit(tmp_path: Path) -> None:
    path = tmp_path / "plant_lists.json"
    raw = {
        "version": "1.0",
        "future_document_field": {"keep": True},
        "lists": [{
            "id": "orchard",
            "name": "Orchard",
            "description": "",
            "future_list_field": [1, 2, 3],
            "entries": [{
                "id": "apple",
                "species_key": "malus domestica",
                "added_at": "2025-01-01T00:00:00+00:00",
                "note": "old",
                "future_entry_field": "keep me",
                "species": {
                    "scientific_name": "Malus domestica",
                    "common_name": "Apple",
                    "future_species_field": {"source": "archive"},
                },
            }],
        }],
    }
    path.write_text(json.dumps(raw), encoding="utf-8")
    store = PlantListStore(path)
    assert store.update_entry_note("orchard", "apple", "updated")

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["future_document_field"] == {"keep": True}
    orchard = next(item for item in saved["lists"] if item["id"] == "orchard")
    assert orchard["future_list_field"] == [1, 2, 3]
    entry = orchard["entries"][0]
    assert entry["future_entry_field"] == "keep me"
    assert entry["note"] == "updated"
    assert entry["species"]["future_species_field"] == {"source": "archive"}


def test_store_external_edit_is_a_conflict_and_can_be_explicitly_reloaded(
    tmp_path: Path,
) -> None:
    path = tmp_path / "plant_lists.json"
    first = PlantListStore(path)
    stale = PlantListStore(path)
    first.create_list("First writer")

    with pytest.raises(PersonalStoreConflictError):
        stale.create_list("Stale writer")
    assert stale.is_dirty
    assert stale.get_list(next(
        row.id for row in stale.all_lists() if row.name == "Stale writer"
    )) is not None
    disk_names = {row.name for row in PlantListStore(path).all_lists()}
    assert "First writer" in disk_names
    assert "Stale writer" not in disk_names

    stale.reload()
    assert not stale.is_dirty
    assert {row.name for row in stale.all_lists()} == disk_names


def test_store_save_failure_keeps_memory_explicit_and_can_be_retried(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import os

    path = tmp_path / "plant_lists.json"
    store = PlantListStore(path)
    orchard = store.create_list("Orchard")
    disk_before = path.read_bytes()
    real_replace = os.replace

    def fail_replace(source: str, destination: str) -> None:
        if Path(destination) == path:
            raise OSError("simulated disk full")
        real_replace(source, destination)

    with monkeypatch.context() as scoped:
        scoped.setattr(os, "replace", fail_replace)
        with pytest.raises(PersonalStorePersistenceError, match="simulated disk full"):
            store.rename_list(orchard.id, "Pending in memory")

    assert path.read_bytes() == disk_before
    assert store.get_list(orchard.id).name == "Pending in memory"
    assert store.is_dirty
    store.save()
    assert not store.is_dirty
    assert PlantListStore(path).get_list(orchard.id).name == "Pending in memory"


def test_invalid_mutable_memory_is_not_published(tmp_path: Path) -> None:
    path = tmp_path / "plant_lists.json"
    store = PlantListStore(path)
    favorites = store.get_list(PlantListStore.FAVORITES_ID)
    assert favorites is not None
    favorites.name = 42  # type: ignore[assignment]

    with pytest.raises(PersonalStorePersistenceError, match="invalid name"):
        store.save()

    assert not path.exists()
    assert store.is_dirty


def test_import_rejects_duplicate_keys_and_nonstandard_numbers(tmp_path: Path) -> None:
    store = PlantListStore(tmp_path / "plant_lists.json")
    for payload in (
        '{"id":"one","id":"two","name":"Duplicate","entries":[]}',
        '{"id":"one","name":"Non-finite","entries":[],"future":NaN}',
        '{"id":"one","name":"Overflow","entries":[],"future":1e999}',
    ):
        with pytest.raises(ValueError):
            store.import_list_from_json(payload)
    assert [item.id for item in store.all_lists()] == [PlantListStore.FAVORITES_ID]


def test_store_rejects_invalid_and_overflowing_known_species_fields(
    tmp_path: Path,
) -> None:
    path = tmp_path / "plant_lists.json"
    payload = {
        "version": "1.0",
        "lists": [{
            "id": "list",
            "name": "Garden",
            "entries": [{
                "id": "entry",
                "species": {
                    "scientific_name": "Example plant",
                    "common_name": "Example",
                    "min_height_cm": int("9" * 1000),
                },
            }],
        }],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    store = PlantListStore(path)

    assert store.load_state == PersonalStoreLoadState.FAILED
    with pytest.raises(PersonalStoreLoadError, match="min_height_cm"):
        store.all_lists()

    malformed = json.loads(json.dumps(payload))
    malformed["lists"][0]["entries"][0]["species"]["min_height_cm"] = "tall"
    path.write_text(json.dumps(malformed), encoding="utf-8")
    store.reload()
    assert store.load_state == PersonalStoreLoadState.FAILED

    non_finite_extension = b'{"version":"1.0","lists":[],"future_field":1e999}'
    path.write_bytes(non_finite_extension)
    store.reload()
    assert store.load_state == PersonalStoreLoadState.FAILED
    assert path.read_bytes() == non_finite_extension


def test_store_edge_cases(tmp_path: Path) -> None:
    """Store handles invalid IDs, empty names, and duplicate species correctly."""
    storage_file = tmp_path / "plant_lists_edge.json"
    store = PlantListStore(storage_file)

    # Empty list name defaults to 'New List'
    empty_list = store.create_list("   ")
    assert empty_list.name == "New List"

    # Rename list failure branches
    assert not store.rename_list(empty_list.id, "   ")
    assert not store.rename_list("nonexistent_list_id", "Valid Name")

    # Delete list failure branch
    assert not store.delete_list("nonexistent_list_id")

    # Get entry failure branch
    assert store.get_entry("nonexistent_entry_id") == (None, None)

    # Add entry to nonexistent list
    sp1 = PlantSpeciesData(scientific_name="Beta vulgaris", common_name="Beet")
    assert store.add_entry("nonexistent_list_id", sp1) is None

    # Add duplicate entry updates snapshot and note
    entry1 = store.add_entry(empty_list.id, sp1, note="Initial note")
    assert entry1 is not None
    assert len(empty_list.entries) == 1
    assert entry1.note == "Initial note"

    entry1_updated = store.add_entry(empty_list.id, sp1, note="Updated beet note")
    assert entry1_updated is entry1
    assert len(empty_list.entries) == 1
    assert entry1.note == "Updated beet note"

    # Adding again with empty note does not overwrite existing note
    entry1_no_note = store.add_entry(empty_list.id, sp1, note="")
    assert entry1_no_note is entry1
    assert entry1.note == "Updated beet note"

    # Remove entry failures
    assert not store.remove_entry("nonexistent_list_id", entry1.id)
    assert not store.remove_entry(empty_list.id, "nonexistent_entry_id")

    # Move entry edge cases
    list2 = store.create_list("Second List")
    sp2 = PlantSpeciesData(scientific_name="Allium cepa", common_name="Onion")
    entry2 = store.add_entry(empty_list.id, sp2)
    assert entry2 is not None

    # Get entry when searching for second entry in list
    found_pl, found_e = store.get_entry(entry2.id)
    assert found_pl is not None and found_e is not None
    assert found_e.id == entry2.id

    # Move to same list
    assert not store.move_entry(empty_list.id, empty_list.id, entry1.id)
    # Move with invalid from/to
    assert not store.move_entry("invalid_id", list2.id, entry1.id)
    assert not store.move_entry(empty_list.id, "invalid_id", entry1.id)
    # Move nonexistent entry
    assert not store.move_entry(empty_list.id, list2.id, "invalid_entry_id")

    # Move entry with multiple items in from_list
    assert store.move_entry(empty_list.id, list2.id, entry1.id)
    assert len(empty_list.entries) == 1  # entry2 remains
    assert len(list2.entries) == 1

    # Update note edge cases
    assert not store.update_entry_note("nonexistent_list_id", entry1.id, "note")
    assert not store.update_entry_note(list2.id, "nonexistent_entry_id", "note")

    # Update species edge cases
    assert not store.update_entry_species("nonexistent_list_id", entry1.id, sp1)
    assert not store.update_entry_species(list2.id, "nonexistent_entry_id", sp1)


def test_favorites_edge_cases(tmp_path: Path) -> None:
    """is_favorite and toggle_favorite handle missing favorites list and unknown keys."""
    storage_file = tmp_path / "fav_edges.json"
    store = PlantListStore(storage_file)

    sp = PlantSpeciesData(scientific_name="Fragaria vesca", common_name="Wild Strawberry")
    sp2 = PlantSpeciesData(scientific_name="Daucus carota", common_name="Carrot")
    assert not store.is_favorite("")
    assert not store.is_favorite("   ")
    assert not store.is_favorite("_unknown")

    # Add multiple items to favorites
    store.add_entry(PlantListStore.FAVORITES_ID, sp)
    store.add_entry(PlantListStore.FAVORITES_ID, sp2)
    assert store.is_favorite(sp)
    assert store.is_favorite(sp2)

    # Toggle removes sp2 when sp is earlier in the list
    assert store.toggle_favorite(sp2) is False
    assert not store.is_favorite(sp2)
    assert store.is_favorite(sp)

    # Simulate favorites list temporarily deleted
    del store._lists[PlantListStore.FAVORITES_ID]
    assert not store.is_favorite(sp)

    # toggle_favorite ensures favorites recreated
    assert store.toggle_favorite(sp) is True
    assert store.is_favorite(sp) is True


def test_export_import_edge_cases(tmp_path: Path) -> None:
    """export_list_to_json and import_list_from_json handle error conditions and collision renaming."""
    storage_file = tmp_path / "export_import_edge.json"
    store = PlantListStore(storage_file)

    # Export nonexistent list raises KeyError
    with pytest.raises(KeyError, match="Plant list not found"):
        store.export_list_to_json("nonexistent_list_id")

    # Import non-dict structure raises ValueError
    with pytest.raises(ValueError, match="Invalid plant list JSON structure"):
        store.import_list_from_json("[1, 2, 3]")

    # Import without wrapper 'list' key
    raw_dict_json = json.dumps({
        "id": "raw-custom",
        "name": "Raw Custom",
        "entries": [],
    })
    imported_raw = store.import_list_from_json(raw_dict_json)
    assert imported_raw.name == "Raw Custom"

    # Import with reserved favorites ID
    fav_import_json = json.dumps({
        "list": {
            "id": PlantListStore.FAVORITES_ID,
            "name": "Favorites",
            "entries": [],
        }
    })
    imported_fav = store.import_list_from_json(fav_import_json)
    assert imported_fav.name == "Favorites (Imported)"

    # Import with duplicate name increments counter
    dup1 = store.import_list_from_json(raw_dict_json)
    assert dup1.name == "Raw Custom (1)"
    dup2 = store.import_list_from_json(raw_dict_json)
    assert dup2.name == "Raw Custom (2)"


def test_global_store_singleton(tmp_path: Path) -> None:
    """get_plant_list_store and reset_plant_list_store manage application singleton."""
    from open_garden_planner.models.plant_lists import (
        get_plant_list_store,
        reset_plant_list_store,
    )

    reset_plant_list_store()
    p1 = tmp_path / "store1.json"
    p2 = tmp_path / "store2.json"

    s1 = get_plant_list_store(p1)
    s1_again = get_plant_list_store()
    assert s1 is s1_again
    assert s1.path == p1

    s2 = get_plant_list_store(p2)
    assert s2 is not s1
    assert s2.path == p2

    reset_plant_list_store()

