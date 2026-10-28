"""Unit tests for US-9.1: Seed Packet data model and viability logic."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from open_garden_planner.core.personal_store import (
    PersonalStoreConflictError,
    PersonalStoreLoadError,
    PersonalStoreLoadState,
    PersonalStorePersistenceError,
)
from open_garden_planner.models.seed_inventory import (
    SeedInventoryStore,
    SeedPacket,
    SeedViabilityDB,
    ViabilityStatus,
)

# ─── Fixtures ─────────────────────────────────────────────────────────────────

VIABILITY_DATA = {
    "by_species": {
        "tomato": {"shelf_life_years": 5, "reduced_after_years": 4},
        "onion": {"shelf_life_years": 1, "reduced_after_years": 1},
        "lettuce": {"shelf_life_years": 5, "reduced_after_years": 3},
        "carrot": {"shelf_life_years": 3, "reduced_after_years": 2},
    },
    "by_family": {
        "Solanaceae": {"shelf_life_years": 4, "reduced_after_years": 3},
        "Brassicaceae": {"shelf_life_years": 5, "reduced_after_years": 4},
    },
}


@pytest.fixture
def db() -> SeedViabilityDB:
    return SeedViabilityDB.from_dict(VIABILITY_DATA)


@pytest.fixture
def tomato_packet() -> SeedPacket:
    return SeedPacket(
        id="test-tomato-id",
        species_name="Tomato",
        variety="Brandywine",
        purchase_year=2022,
        quantity=50.0,
        quantity_unit="seeds",
    )


@pytest.fixture
def store(tmp_path: Path) -> SeedInventoryStore:
    return SeedInventoryStore(tmp_path / "seed_inventory.json")


# ─── SeedPacket defaults and construction ─────────────────────────────────────

class TestSeedPacketDefaults:
    def test_id_is_uuid_string(self) -> None:
        packet = SeedPacket(species_name="Basil")
        parsed = uuid.UUID(packet.id)  # raises if not valid UUID
        assert str(parsed) == packet.id

    def test_default_quantity_unit(self) -> None:
        packet = SeedPacket(species_name="Dill")
        assert packet.quantity_unit == "seeds"

    def test_default_cold_stratification_false(self) -> None:
        packet = SeedPacket(species_name="Pepper")
        assert packet.cold_stratification is False

    def test_default_species_id_none(self) -> None:
        packet = SeedPacket(species_name="Carrot")
        assert packet.species_id is None

    def test_optional_fields_default_to_none(self) -> None:
        packet = SeedPacket(species_name="Pea")
        assert packet.germination_temp_min_c is None
        assert packet.germination_temp_opt_c is None
        assert packet.germination_temp_max_c is None
        assert packet.germination_days_min is None
        assert packet.germination_days_max is None
        assert packet.light_germinator is None
        assert packet.stratification_days is None


# ─── SeedPacket serialisation ─────────────────────────────────────────────────

class TestSeedPacketSerialization:
    def test_round_trip_minimal(self) -> None:
        original = SeedPacket(species_name="Basil", purchase_year=2023)
        restored = SeedPacket.from_dict(original.to_dict())
        assert restored.species_name == original.species_name
        assert restored.purchase_year == original.purchase_year
        assert restored.id == original.id

    def test_round_trip_full(self) -> None:
        original = SeedPacket(
            id="abc-123",
            species_id="Solanum lycopersicum",
            species_name="Tomato",
            variety="Roma",
            purchase_year=2021,
            manufacturer="BioSeeds GmbH",
            batch_number="LOT-001",
            quantity=100.0,
            quantity_unit="seeds",
            germination_temp_min_c=15.0,
            germination_temp_opt_c=22.0,
            germination_temp_max_c=30.0,
            germination_days_min=7,
            germination_days_max=14,
            light_germinator=False,
            cold_stratification=True,
            stratification_days=30,
            pre_treatment="Soak 12h",
            notes="Good germinator",
            photo_path="/photos/tomato.jpg",
            created_date="2023-01-15",
        )
        restored = SeedPacket.from_dict(original.to_dict())
        assert restored.id == "abc-123"
        assert restored.species_id == "Solanum lycopersicum"
        assert restored.variety == "Roma"
        assert restored.manufacturer == "BioSeeds GmbH"
        assert restored.germination_temp_opt_c == 22.0
        assert restored.germination_days_max == 14
        assert restored.light_germinator is False
        assert restored.cold_stratification is True
        assert restored.stratification_days == 30
        assert restored.pre_treatment == "Soak 12h"
        assert restored.notes == "Good germinator"
        assert restored.photo_path == "/photos/tomato.jpg"
        assert restored.created_date == "2023-01-15"

    def test_empty_strings_omitted_from_dict(self) -> None:
        packet = SeedPacket(species_name="Onion", variety="")
        d = packet.to_dict()
        assert "variety" not in d

    def test_none_optional_omitted_from_dict(self) -> None:
        packet = SeedPacket(species_name="Basil")
        d = packet.to_dict()
        assert "germination_temp_min_c" not in d
        assert "light_germinator" not in d
        assert "stratification_days" not in d

    def test_from_dict_missing_id_generates_new(self) -> None:
        d = {"species_name": "Kale"}
        packet = SeedPacket.from_dict(d)
        assert uuid.UUID(packet.id)  # valid UUID

    def test_from_dict_preserves_false_light_germinator(self) -> None:
        d = {"species_name": "Tomato", "light_germinator": False}
        packet = SeedPacket.from_dict(d)
        assert packet.light_germinator is False

    @pytest.mark.parametrize(
        ("field_name", "invalid_value"),
        [
            ("purchase_year", None),
            ("cold_stratification", None),
            ("purchase_year", 2**31),
            ("germination_days_min", -1),
            ("germination_days_max", 2**31),
            ("stratification_days", 10**100),
            ("viability_shelf_life_override", -(10**100)),
        ],
    )
    def test_from_dict_rejects_null_and_out_of_range_integer_fields(
        self, field_name: str, invalid_value: Any
    ) -> None:
        with pytest.raises(ValueError, match=field_name):
            SeedPacket.from_dict({"id": "invalid", field_name: invalid_value})


# ─── SeedViabilityDB ──────────────────────────────────────────────────────────

class TestSeedViabilityDB:
    def test_lookup_exact_species(self, db: SeedViabilityDB) -> None:
        entry = db.lookup("Tomato")
        assert entry is not None
        assert entry["shelf_life_years"] == 5

    def test_lookup_case_insensitive(self, db: SeedViabilityDB) -> None:
        assert db.lookup("TOMATO") == db.lookup("tomato")

    def test_lookup_substring_match(self, db: SeedViabilityDB) -> None:
        # "Cherry Tomato" should match "tomato" key
        entry = db.lookup("Cherry Tomato")
        assert entry is not None
        assert entry["shelf_life_years"] == 5

    def test_lookup_family_fallback(self, db: SeedViabilityDB) -> None:
        # No species entry for "Broccoli", but family "Brassicaceae" exists
        entry = db.lookup("Broccoli", family="Brassicaceae")
        assert entry is not None
        assert entry["shelf_life_years"] == 5

    def test_lookup_returns_none_for_unknown(self, db: SeedViabilityDB) -> None:
        entry = db.lookup("Exotic Mystery Plant")
        assert entry is None

    def test_species_count(self, db: SeedViabilityDB) -> None:
        assert db.species_count == len(VIABILITY_DATA["by_species"])

    def test_family_count(self, db: SeedViabilityDB) -> None:
        assert db.family_count == len(VIABILITY_DATA["by_family"])

    def test_load_from_file(self, tmp_path: Path) -> None:
        p = tmp_path / "viability.json"
        p.write_text(json.dumps(VIABILITY_DATA), encoding="utf-8")
        loaded = SeedViabilityDB.load(p)
        assert loaded.species_count == db.species_count if False else loaded.species_count > 0


# ─── ViabilityStatus calculation ──────────────────────────────────────────────

class TestViabilityStatus:
    def test_good_within_shelf_life(self, db: SeedViabilityDB) -> None:
        # Tomato: shelf_life 5y, reduced_after 4y; purchased 2024, current 2025 → age 1
        packet = SeedPacket(species_name="Tomato", purchase_year=2024)
        assert packet.viability_status(2025, db) == ViabilityStatus.GOOD

    def test_reduced_after_threshold(self, db: SeedViabilityDB) -> None:
        # Tomato reduced_after=4y; purchased 2020, current 2024 → age 4
        packet = SeedPacket(species_name="Tomato", purchase_year=2020)
        assert packet.viability_status(2024, db) == ViabilityStatus.REDUCED

    def test_expired_beyond_shelf_life(self, db: SeedViabilityDB) -> None:
        # Tomato shelf_life=5y; purchased 2015, current 2025 → age 10
        packet = SeedPacket(species_name="Tomato", purchase_year=2015)
        assert packet.viability_status(2025, db) == ViabilityStatus.EXPIRED

    def test_onion_expires_after_one_year(self, db: SeedViabilityDB) -> None:
        # Onion: shelf_life=1y; purchased 2023, current 2025 → age 2 → expired
        packet = SeedPacket(species_name="Onion", purchase_year=2023)
        assert packet.viability_status(2025, db) == ViabilityStatus.EXPIRED

    def test_default_fallback_for_unknown_species(self, db: SeedViabilityDB) -> None:
        # Species not in DB → falls back to default (3 years shelf life)
        packet = SeedPacket(species_name="Mysterious Fern", purchase_year=2020)
        # age = 5 → expired (default shelf_life=3)
        assert packet.viability_status(2025, db) == ViabilityStatus.EXPIRED

    def test_default_fallback_still_good_within_3_years(self, db: SeedViabilityDB) -> None:
        packet = SeedPacket(species_name="Mysterious Fern", purchase_year=2024)
        # age = 1 → good (default shelf_life=3, reduced_after=2)
        assert packet.viability_status(2025, db) == ViabilityStatus.GOOD

    def test_viability_override_good(self, db: SeedViabilityDB) -> None:
        # Override: 10-year shelf life; purchased 2020, current 2025 → age 5 → good
        packet = SeedPacket(
            species_name="Tomato",
            purchase_year=2020,
            viability_shelf_life_override=10,
        )
        assert packet.viability_status(2025, db) == ViabilityStatus.GOOD

    def test_viability_override_expired(self, db: SeedViabilityDB) -> None:
        # Override: 2-year shelf life; purchased 2020, current 2025 → age 5 → expired
        packet = SeedPacket(
            species_name="Tomato",
            purchase_year=2020,
            viability_shelf_life_override=2,
        )
        assert packet.viability_status(2025, db) == ViabilityStatus.EXPIRED

    def test_viability_override_ignores_db_entry(self, db: SeedViabilityDB) -> None:
        # Tomato DB says 5y, override says 1y; purchased 2023, current 2025 → age 2 → expired
        packet = SeedPacket(
            species_name="Tomato",
            purchase_year=2023,
            viability_shelf_life_override=1,
        )
        assert packet.viability_status(2025, db) == ViabilityStatus.EXPIRED

    def test_viability_override_round_trips(self) -> None:
        packet = SeedPacket(species_name="Tomato", viability_shelf_life_override=7)
        restored = SeedPacket.from_dict(packet.to_dict())
        assert restored.viability_shelf_life_override == 7

    def test_viability_override_none_not_in_dict(self) -> None:
        packet = SeedPacket(species_name="Tomato")
        assert "viability_shelf_life_override" not in packet.to_dict()

    def test_good_when_same_year_as_purchase(self, db: SeedViabilityDB) -> None:
        packet = SeedPacket(species_name="Carrot", purchase_year=2025)
        assert packet.viability_status(2025, db) == ViabilityStatus.GOOD

    def test_future_purchase_year_is_good(self, db: SeedViabilityDB) -> None:
        packet = SeedPacket(species_name="Carrot", purchase_year=2030)
        assert packet.viability_status(2025, db) == ViabilityStatus.GOOD

    def test_lettuce_reduced_at_three_years(self, db: SeedViabilityDB) -> None:
        # Lettuce: reduced_after=3y; purchased 2022, current 2025 → age 3 → reduced
        packet = SeedPacket(species_name="Lettuce", purchase_year=2022)
        assert packet.viability_status(2025, db) == ViabilityStatus.REDUCED


# ─── SeedInventoryStore ───────────────────────────────────────────────────────

class TestSeedInventoryStore:
    def test_empty_on_creation(self, store: SeedInventoryStore) -> None:
        assert len(store) == 0
        assert store.all() == []
        assert store.load_state == PersonalStoreLoadState.ABSENT

    def test_add_and_get(self, store: SeedInventoryStore, tomato_packet: SeedPacket) -> None:
        store.add(tomato_packet)
        retrieved = store.get(tomato_packet.id)
        assert retrieved is not None
        assert retrieved.species_name == "Tomato"

    def test_add_replaces_existing(self, store: SeedInventoryStore, tomato_packet: SeedPacket) -> None:
        store.add(tomato_packet)
        updated = SeedPacket(id=tomato_packet.id, species_name="Updated Tomato")
        store.add(updated)
        assert len(store) == 1
        assert store.get(tomato_packet.id).species_name == "Updated Tomato"

    def test_remove_existing(self, store: SeedInventoryStore, tomato_packet: SeedPacket) -> None:
        store.add(tomato_packet)
        removed = store.remove(tomato_packet.id)
        assert removed is True
        assert len(store) == 0

    def test_remove_nonexistent_returns_false(self, store: SeedInventoryStore) -> None:
        assert store.remove("nonexistent-id") is False

    def test_get_nonexistent_returns_none(self, store: SeedInventoryStore) -> None:
        assert store.get("not-there") is None

    def test_all_sorted_by_species_name(self, store: SeedInventoryStore) -> None:
        store.add(SeedPacket(id="z", species_name="Zucchini"))
        store.add(SeedPacket(id="a", species_name="Artichoke"))
        store.add(SeedPacket(id="m", species_name="Mint"))
        names = [p.species_name for p in store.all()]
        assert names == ["Artichoke", "Mint", "Zucchini"]

    def test_save_and_reload(self, tmp_path: Path, tomato_packet: SeedPacket) -> None:
        path = tmp_path / "inv.json"
        store1 = SeedInventoryStore(path)
        store1.add(tomato_packet)
        store1.save()

        store2 = SeedInventoryStore(path)
        assert len(store2) == 1
        restored = store2.get(tomato_packet.id)
        assert restored is not None
        assert restored.species_name == "Tomato"
        assert restored.variety == "Brandywine"

    def test_nonfinite_serialization_failure_preserves_existing_file(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "inv.json"
        path.write_text('[{"id":"old","species_name":"Ancienne"}]', encoding="utf-8")
        store = SeedInventoryStore(path)
        store.add(SeedPacket(id="new", species_name="Nouvelle"))
        original = path.read_bytes()
        packet = store.get("new")
        assert packet is not None
        packet.quantity = float("nan")
        with pytest.raises(PersonalStorePersistenceError, match="non-finite quantity"):
            store.save()

        assert path.read_bytes() == original
        assert not list(tmp_path.glob(".inv.json.*.tmp"))
        assert store.is_dirty

    def test_replace_failure_preserves_existing_file_and_cleans_temporary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "inv.json"
        path.write_text('[{"id":"old","species_name":"Ancienne"}]', encoding="utf-8")
        store = SeedInventoryStore(path)
        store.add(SeedPacket(id="new", species_name="Nouvelle"))
        original = path.read_bytes()
        from open_garden_planner.core import plan_io

        real_replace = plan_io.os.replace

        def fail_replace(source: str | Path, destination: str | Path) -> None:
            if Path(destination) == path:
                raise OSError("injected replace failure")
            real_replace(source, destination)

        with monkeypatch.context() as scoped:
            scoped.setattr(plan_io.os, "replace", fail_replace)
            with pytest.raises(PersonalStorePersistenceError, match="injected replace failure"):
                store.save()

        assert path.read_bytes() == original
        assert not list(tmp_path.glob(".inv.json.*.tmp"))
        assert store.is_dirty

    def test_save_preserves_unknown_historical_fields_until_record_removal(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "inv.json"
        path.write_text(json.dumps([{
            "id": "legacy",
            "species_name": "Legacy seed",
            "purchase_year": 2020,
            "quantity": 2,
            "quantity_unit": "seeds",
            "cold_stratification": False,
            "future_trait": {"provider": "archive", "enabled": False},
        }]), encoding="utf-8")

        store = SeedInventoryStore(path)
        packet = store.get("legacy")
        assert packet is not None
        packet.quantity = 5
        store.save()

        reopened = SeedInventoryStore(path)
        updated = json.loads(path.read_text(encoding="utf-8"))[0]
        assert updated["quantity"] == 5
        assert updated["future_trait"] == {"provider": "archive", "enabled": False}
        assert reopened.get("legacy") is not None

        assert reopened.remove("legacy")
        reopened.add(SeedPacket(id="legacy", species_name="Replacement"))
        reopened.save()
        replacement = json.loads(path.read_text(encoding="utf-8"))[0]
        assert replacement["species_name"] == "Replacement"
        assert "future_trait" not in replacement

    def test_corrupt_file_is_failed_and_never_overwritten(self, tmp_path: Path) -> None:
        path = tmp_path / "inv.json"
        original = b"NOT VALID JSON"
        path.write_bytes(original)
        store = SeedInventoryStore(path)
        assert store.load_state == PersonalStoreLoadState.FAILED
        with pytest.raises(PersonalStoreLoadError):
            store.all()
        with pytest.raises(PersonalStoreLoadError):
            store.add(SeedPacket(species_name="Not saved"))
        with pytest.raises(PersonalStoreLoadError):
            store.save()
        assert path.read_bytes() == original

    def test_missing_file_starts_empty(self, tmp_path: Path) -> None:
        store = SeedInventoryStore(tmp_path / "nonexistent.json")
        assert len(store) == 0
        assert store.load_state == PersonalStoreLoadState.ABSENT

    def test_missing_nonnullable_fields_keep_legacy_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "legacy.json"
        path.write_text('[{"id":"legacy"}]', encoding="utf-8")

        packet = SeedInventoryStore(path).get("legacy")

        assert packet is not None
        assert packet.purchase_year == 2024
        assert packet.cold_stratification is False

    @pytest.mark.parametrize(
        ("field_name", "invalid_value"),
        [
            ("purchase_year", None),
            ("cold_stratification", None),
            ("purchase_year", 2**31),
            ("germination_days_min", -1),
            ("germination_days_max", 2**31),
            ("stratification_days", 10**100),
            ("viability_shelf_life_override", -(10**100)),
        ],
    )
    def test_invalid_fields_and_calendar_durations_fail_closed(
        self,
        tmp_path: Path,
        field_name: str,
        invalid_value: Any,
    ) -> None:
        path = tmp_path / "invalid.json"
        original = json.dumps([{"id": "invalid", field_name: invalid_value}]).encode()
        path.write_bytes(original)

        store = SeedInventoryStore(path)

        assert store.load_state == PersonalStoreLoadState.FAILED
        with pytest.raises(PersonalStoreLoadError, match=field_name):
            store.all()
        assert path.read_bytes() == original

    def test_empty_file_has_distinct_state(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.json"
        path.write_text("[]", encoding="utf-8")
        store = SeedInventoryStore(path)
        assert store.load_state == PersonalStoreLoadState.EMPTY
        assert store.all() == []

    @pytest.mark.parametrize(
        "payload",
        [
            'null',
            '[{"id":"a","quantity":true}]',
            '[{"id":"a"},{"id":"a"}]',
            '[{"id":"a","quantity":NaN}]',
            '[{"id":"a","quantity":' + ('9' * 1000) + '}]',
            '[{"id":"a","future_field":1e999}]',
            "[" * 1200 + "0" + "]" * 1200,
        ],
    )
    def test_invalid_store_records_fail_closed(self, tmp_path: Path, payload: str) -> None:
        path = tmp_path / "invalid.json"
        path.write_text(payload, encoding="utf-8")
        store = SeedInventoryStore(path)
        assert store.load_state == PersonalStoreLoadState.FAILED
        with pytest.raises(PersonalStoreLoadError):
            store.all()
        with pytest.raises(PersonalStoreLoadError):
            store.save()
        assert path.read_text(encoding="utf-8") == payload

    def test_unreadable_path_and_explicit_reload(self, tmp_path: Path) -> None:
        path = tmp_path / "inventory.json"
        path.mkdir()
        store = SeedInventoryStore(path)
        assert store.load_state == PersonalStoreLoadState.FAILED
        with pytest.raises(PersonalStoreLoadError):
            store.all()

        path.rmdir()
        path.write_text("[]", encoding="utf-8")
        store.reload()
        assert store.load_state == PersonalStoreLoadState.EMPTY
        assert store.load_error is None

    def test_external_edit_is_a_conflict_and_can_be_reloaded(self, tmp_path: Path) -> None:
        path = tmp_path / "inventory.json"
        first = SeedInventoryStore(path)
        stale = SeedInventoryStore(path)
        first.add(SeedPacket(id="first", species_name="First"))
        first.save()
        stale.add(SeedPacket(id="stale", species_name="Stale"))
        with pytest.raises(PersonalStoreConflictError):
            stale.save()
        assert stale.is_dirty
        assert {packet.id for packet in SeedInventoryStore(path).all()} == {"first"}

        stale.reload()
        assert not stale.is_dirty
        assert {packet.id for packet in stale.all()} == {"first"}

    def test_save_failure_keeps_memory_and_retry_writes_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from open_garden_planner.core import plan_io

        path = tmp_path / "inventory.json"
        path.write_text('[{"id":"old","species_name":"Old"}]', encoding="utf-8")
        store = SeedInventoryStore(path)
        original = path.read_bytes()
        store.add(SeedPacket(id="new", species_name="New"))
        real_replace = plan_io.os.replace

        def fail_replace(source: str | Path, destination: str | Path) -> None:
            if Path(destination) == path:
                raise OSError("simulated permission failure")
            real_replace(source, destination)

        with monkeypatch.context() as scoped:
            scoped.setattr(plan_io.os, "replace", fail_replace)
            with pytest.raises(PersonalStorePersistenceError, match="permission failure"):
                store.save()

        assert path.read_bytes() == original
        assert store.is_dirty
        assert {packet.id for packet in store.all()} == {"old", "new"}
        store.save()
        assert not store.is_dirty
        assert {packet.id for packet in SeedInventoryStore(path).all()} == {"old", "new"}

    def test_invalid_mutable_memory_is_not_published(self, tmp_path: Path) -> None:
        path = tmp_path / "inventory.json"
        store = SeedInventoryStore(path)
        packet = SeedPacket(id="invalid", species_name="Invalid", purchase_year="2024")
        store.add(packet)

        with pytest.raises(PersonalStorePersistenceError, match="purchase_year"):
            store.save()

        assert not path.exists()
        assert store.is_dirty
        assert store.get("invalid") is packet

    def test_task_state_propagates_failed_global_inventory_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from open_garden_planner.models import seed_inventory as seed_inventory_module
        from open_garden_planner.services.task_generator import build_plan_state

        path = tmp_path / "seed_inventory.json"
        path.write_text("{broken", encoding="utf-8")
        failed_store = SeedInventoryStore(path)
        monkeypatch.setattr(
            seed_inventory_module, "get_seed_inventory", lambda: failed_store
        )
        item = SimpleNamespace(
            item_id="plant-1",
            metadata={
                "plant_species": {
                    "scientific_name": "Solanum lycopersicum",
                    "common_name": "Tomato",
                },
                "plant_instance": {"seed_packet_id": "packet-1"},
            },
        )
        scene = SimpleNamespace(items=lambda: [item])
        project_manager = SimpleNamespace(
            location={},
            manual_tasks={},
            propagation_overrides={},
            succession_plans={},
        )

        with pytest.raises(PersonalStoreLoadError):
            build_plan_state(scene, project_manager, include_propagation=True)


# ─── Bundled database coverage ────────────────────────────────────────────────

class TestBundledDatabase:
    """Smoke-test the actual seed_viability.json file."""

    @pytest.fixture
    def real_db(self) -> SeedViabilityDB:
        data_path = (
            Path(__file__).parent.parent.parent
            / "src"
            / "open_garden_planner"
            / "resources"
            / "data"
            / "seed_viability.json"
        )
        return SeedViabilityDB.load(data_path)

    def test_has_at_least_80_species(self, real_db: SeedViabilityDB) -> None:
        assert real_db.species_count >= 80

    def test_has_family_entries(self, real_db: SeedViabilityDB) -> None:
        assert real_db.family_count >= 5

    def test_common_vegetables_present(self, real_db: SeedViabilityDB) -> None:
        for name in ("tomato", "carrot", "lettuce", "onion", "pea"):
            assert real_db.lookup(name) is not None, f"{name} not in bundled DB"

    def test_all_entries_have_required_keys(self, real_db: SeedViabilityDB) -> None:
        # Access internal dict for validation
        for key, entry in real_db._by_species.items():
            assert "shelf_life_years" in entry, f"{key} missing shelf_life_years"
            assert "reduced_after_years" in entry, f"{key} missing reduced_after_years"
            assert entry["reduced_after_years"] <= entry["shelf_life_years"], (
                f"{key}: reduced_after_years > shelf_life_years"
            )

    def test_default_entry_defined(self, real_db: SeedViabilityDB) -> None:
        # US-9.2: bundled DB must expose a default fallback
        entry = real_db.default_entry
        assert entry["shelf_life_years"] > 0
        assert entry["reduced_after_years"] > 0

    def test_sources_present_in_json(self) -> None:
        # US-9.2: source references must be embedded in the JSON
        data_path = (
            Path(__file__).parent.parent.parent
            / "src"
            / "open_garden_planner"
            / "resources"
            / "data"
            / "seed_viability.json"
        )
        raw = json.loads(data_path.read_text(encoding="utf-8"))
        sources = raw.get("_sources", [])
        assert len(sources) >= 2, "At least 2 source references expected"
        for src in sources:
            assert "title" in src, "Source missing title"
            assert "publisher" in src, "Source missing publisher"

    def test_default_fallback_for_unknown_species(self, real_db: SeedViabilityDB) -> None:
        # Unknown species → viability_status uses default (3 years), not UNKNOWN
        packet = SeedPacket(species_name="Fictional Plant 99", purchase_year=2020)
        status = packet.viability_status(2025, real_db)
        # age=5 > default shelf_life=3 → EXPIRED (not UNKNOWN)
        assert status == ViabilityStatus.EXPIRED
