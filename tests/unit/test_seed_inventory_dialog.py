"""Tests for SeedTableModel and SeedPacketEditDialog (US-9.3)."""
# ruff: noqa: ARG002
from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from open_garden_planner.models.seed_inventory import (
    SeedInventoryStore,
    SeedPacket,
    SeedViabilityDB,
)
from open_garden_planner.ui.dialogs.seed_inventory_dialog import (
    SeedPacketEditDialog,
    SeedTableModel,
)

# ── Minimal viability DB fixture ────────────────────────────────────────────────

_MINI_DB_DATA = {
    "by_species": {"tomato": {"shelf_life_years": 5, "reduced_after_years": 4}},
    "by_family": {},
    "_default": {"shelf_life_years": 3, "reduced_after_years": 2},
}


@pytest.fixture
def db() -> SeedViabilityDB:
    return SeedViabilityDB.from_dict(_MINI_DB_DATA)


@pytest.fixture
def tmp_store(tmp_path: Path) -> SeedInventoryStore:
    store = SeedInventoryStore(tmp_path / "test_inventory.json")
    return store


# ── SeedTableModel tests (no Qt event loop needed) ────────────────────────────

class TestSeedTableModel:
    def test_row_count_empty(self, tmp_store: SeedInventoryStore, db: SeedViabilityDB) -> None:
        model = SeedTableModel(tmp_store, db)
        assert model.rowCount() == 0

    def test_row_count_after_add(self, tmp_store: SeedInventoryStore, db: SeedViabilityDB) -> None:
        tmp_store.add(SeedPacket(species_name="Tomato", purchase_year=2023))
        tmp_store.add(SeedPacket(species_name="Carrot", purchase_year=2024))
        model = SeedTableModel(tmp_store, db)
        assert model.rowCount() == 2

    def test_column_count(self, tmp_store: SeedInventoryStore, db: SeedViabilityDB) -> None:
        model = SeedTableModel(tmp_store, db)
        assert model.columnCount() == 7

    def test_packet_at_valid(self, tmp_store: SeedInventoryStore, db: SeedViabilityDB) -> None:
        p = SeedPacket(species_name="Basil", purchase_year=2024)
        tmp_store.add(p)
        model = SeedTableModel(tmp_store, db)
        result = model.packet_at(0)
        assert result is not None
        assert result.species_name == "Basil"

    def test_packet_at_invalid(self, tmp_store: SeedInventoryStore, db: SeedViabilityDB) -> None:
        model = SeedTableModel(tmp_store, db)
        assert model.packet_at(0) is None
        assert model.packet_at(-1) is None

    def test_display_data_name(self, tmp_store: SeedInventoryStore, db: SeedViabilityDB) -> None:
        from PyQt6.QtCore import Qt
        tmp_store.add(SeedPacket(species_name="Pepper", purchase_year=2024))
        model = SeedTableModel(tmp_store, db)
        idx = model.index(0, 0)
        assert model.data(idx, Qt.ItemDataRole.DisplayRole) == "Pepper"

    def test_display_data_year(self, tmp_store: SeedInventoryStore, db: SeedViabilityDB) -> None:
        from PyQt6.QtCore import Qt
        tmp_store.add(SeedPacket(species_name="Pea", purchase_year=2021))
        model = SeedTableModel(tmp_store, db)
        idx = model.index(0, 2)  # Year column
        assert model.data(idx, Qt.ItemDataRole.DisplayRole) == "2021"

    def test_display_quantity_with_unit(self, tmp_store: SeedInventoryStore, db: SeedViabilityDB) -> None:
        from PyQt6.QtCore import Qt
        tmp_store.add(SeedPacket(species_name="Bean", purchase_year=2024, quantity=50.0, quantity_unit="seeds"))
        model = SeedTableModel(tmp_store, db)
        idx = model.index(0, 3)  # Quantity column
        assert model.data(idx, Qt.ItemDataRole.DisplayRole) == "50 seeds"

    def test_display_quantity_zero_is_explicit(self, tmp_store: SeedInventoryStore, db: SeedViabilityDB) -> None:
        from PyQt6.QtCore import Qt
        tmp_store.add(SeedPacket(species_name="Bean", purchase_year=2024, quantity=0.0))
        model = SeedTableModel(tmp_store, db)
        idx = model.index(0, 3)
        assert model.data(idx, Qt.ItemDataRole.DisplayRole) == "0 seeds"

    def test_background_role_good(self, tmp_store: SeedInventoryStore, db: SeedViabilityDB) -> None:
        from PyQt6.QtCore import Qt
        tmp_store.add(SeedPacket(species_name="Tomato", purchase_year=2024))
        model = SeedTableModel(tmp_store, db)
        model._current_year = 2024
        idx = model.index(0, 0)
        brush = model.data(idx, Qt.ItemDataRole.BackgroundRole)
        assert brush is not None

    def test_set_headers_updates_model(self, tmp_store: SeedInventoryStore, db: SeedViabilityDB) -> None:
        from PyQt6.QtCore import Qt
        model = SeedTableModel(tmp_store, db)
        headers = ["A", "B", "C", "D", "E", "F", "G"]
        model.set_headers(headers)
        result = model.headerData(0, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
        assert result == "A"

    def test_reload_updates_rows(self, tmp_store: SeedInventoryStore, db: SeedViabilityDB) -> None:
        model = SeedTableModel(tmp_store, db)
        assert model.rowCount() == 0
        tmp_store.add(SeedPacket(species_name="Kale", purchase_year=2025))
        model._reload()
        assert model.rowCount() == 1


# ── SeedPacketEditDialog tests ──────────────────────────────────────────────────

class TestSeedPacketEditDialog:
    def test_create_new_packet_defaults(self, qtbot) -> None:
        dlg = SeedPacketEditDialog()
        qtbot.addWidget(dlg)
        assert dlg._year_spin.value() == datetime.date.today().year
        assert dlg._qty_spin.value() == 0.0
        assert dlg._cold_strat_cb.isChecked() is False
        assert dlg._override_cb.isChecked() is False

    def test_get_packet_returns_correct_data(self, qtbot) -> None:
        dlg = SeedPacketEditDialog()
        qtbot.addWidget(dlg)
        dlg._name_edit.setText("Tomato")
        dlg._variety_edit.setText("Gardener's Delight")
        dlg._year_spin.setValue(2022)
        dlg._qty_spin.setValue(25)
        dlg._unit_combo.setCurrentIndex(0)  # seeds
        packet = dlg.get_packet()
        assert packet.species_name == "Tomato"
        assert packet.variety == "Gardener's Delight"
        assert packet.purchase_year == 2022
        assert packet.quantity == 25.0
        assert packet.quantity_unit == "seeds"

    def test_populate_existing_packet(self, qtbot) -> None:
        existing = SeedPacket(
            species_name="Carrot",
            variety="Nantes",
            purchase_year=2020,
            quantity=100.0,
            quantity_unit="seeds",
            manufacturer="Vilmorin",
            cold_stratification=True,
            stratification_days=28,
            viability_shelf_life_override=4,
            notes="Stored in fridge",
        )
        dlg = SeedPacketEditDialog(packet=existing)
        qtbot.addWidget(dlg)
        assert dlg._name_edit.text() == "Carrot"
        assert dlg._variety_edit.text() == "Nantes"
        assert dlg._year_spin.value() == 2020
        assert dlg._qty_spin.value() == 100.0
        assert dlg._manufacturer_edit.text() == "Vilmorin"
        assert dlg._cold_strat_cb.isChecked() is True
        assert dlg._strat_days_spin.value() == 28
        assert dlg._override_cb.isChecked() is True
        assert dlg._override_spin.value() == 4
        assert dlg._notes_edit.toPlainText() == "Stored in fridge"

    def test_populate_and_get_preserves_values_outside_default_spin_ranges(
        self, qtbot
    ) -> None:
        existing = SeedPacket(
            species_name="Carrot",
            purchase_year=1900,
            germination_days_min=400,
            germination_days_max=500,
            cold_stratification=True,
            stratification_days=600,
            viability_shelf_life_override=75,
        )

        dlg = SeedPacketEditDialog(packet=existing)
        qtbot.addWidget(dlg)

        assert dlg._year_spin.value() == 1900
        assert dlg._days_min.value() == 400
        assert dlg._days_max.value() == 500
        assert dlg._strat_days_spin.value() == 600
        assert dlg._override_spin.value() == 75
        assert dlg.get_packet().purchase_year == 1900
        assert dlg.get_packet().germination_days_min == 400
        assert dlg.get_packet().germination_days_max == 500
        assert dlg.get_packet().stratification_days == 600
        assert dlg.get_packet().viability_shelf_life_override == 75

    def test_editing_notes_preserves_other_historical_values(self, qtbot, tmp_path) -> None:
        existing = SeedPacket(
            id="legacy-packet",
            species_id="legacy-species-id",
            species_name="Carrot",
            quantity=0.04,
            quantity_unit="packets",
            germination_temp_min_c=-20.0,
            germination_temp_opt_c=100.0,
            germination_days_min=0,
            germination_days_max=400,
            cold_stratification=False,
            stratification_days=0,
            notes="old note",
            photo_path="/legacy/photo.jpg",
            created_date="2018-04-03",
        )
        path = tmp_path / "seeds.json"
        original = existing.to_dict()
        original["future_extension"] = {"nested": [0, False, None]}
        path.write_text(json.dumps([original]), encoding="utf-8")
        store = SeedInventoryStore(path)
        dlg = SeedPacketEditDialog(packet=store.get(existing.id))
        qtbot.addWidget(dlg)
        dlg._notes_edit.setPlainText("updated note")

        result = dlg.get_packet()

        assert result.notes == "updated note"
        assert result.species_id == existing.species_id
        assert result.quantity == existing.quantity
        assert result.quantity_unit == existing.quantity_unit
        assert result.germination_temp_min_c == existing.germination_temp_min_c
        assert result.germination_temp_opt_c == existing.germination_temp_opt_c
        assert result.germination_days_min == existing.germination_days_min
        assert result.germination_days_max == existing.germination_days_max
        assert result.stratification_days == existing.stratification_days
        assert result.photo_path == existing.photo_path
        assert result.created_date == existing.created_date
        store.add(result)
        store.save()
        reopened = SeedInventoryStore(path).get(existing.id)
        assert reopened.to_dict() == result.to_dict()
        assert json.loads(path.read_text(encoding="utf-8"))[0]["future_extension"] == {
            "nested": [0, False, None],
        }

    def test_get_packet_preserves_id(self, qtbot) -> None:
        existing = SeedPacket(species_name="Pea")
        dlg = SeedPacketEditDialog(packet=existing)
        qtbot.addWidget(dlg)
        dlg._name_edit.setText("Pea Updated")
        result = dlg.get_packet()
        assert result.id == existing.id

    def test_cold_strat_disables_days_spin(self, qtbot) -> None:
        dlg = SeedPacketEditDialog()
        qtbot.addWidget(dlg)
        assert dlg._strat_days_spin.isEnabled() is False
        dlg._cold_strat_cb.setChecked(True)
        assert dlg._strat_days_spin.isEnabled() is True

    def test_override_disables_spin_when_unchecked(self, qtbot) -> None:
        dlg = SeedPacketEditDialog()
        qtbot.addWidget(dlg)
        assert dlg._override_spin.isEnabled() is False
        dlg._override_cb.setChecked(True)
        assert dlg._override_spin.isEnabled() is True

    def test_get_packet_no_override_when_unchecked(self, qtbot) -> None:
        dlg = SeedPacketEditDialog()
        qtbot.addWidget(dlg)
        dlg._name_edit.setText("Lettuce")
        dlg._override_cb.setChecked(False)
        result = dlg.get_packet()
        assert result.viability_shelf_life_override is None

    def test_get_packet_stratification_none_when_unchecked(self, qtbot) -> None:
        dlg = SeedPacketEditDialog()
        qtbot.addWidget(dlg)
        dlg._name_edit.setText("Spinach")
        dlg._cold_strat_cb.setChecked(False)
        result = dlg.get_packet()
        assert result.stratification_days is None

    def test_light_combo_values(self, qtbot) -> None:
        dlg = SeedPacketEditDialog()
        qtbot.addWidget(dlg)
        # Default is Indifferent (None)
        assert dlg._light_combo.currentData() is None
        # Select light germinator
        dlg._light_combo.setCurrentIndex(1)
        assert dlg._light_combo.currentData() is True
        # Select dark germinator
        dlg._light_combo.setCurrentIndex(2)
        assert dlg._light_combo.currentData() is False

    def test_title_new_vs_edit(self, qtbot) -> None:
        dlg_new = SeedPacketEditDialog()
        qtbot.addWidget(dlg_new)
        assert "Add" in dlg_new.windowTitle()

        existing = SeedPacket(species_name="Basil")
        dlg_edit = SeedPacketEditDialog(packet=existing)
        qtbot.addWidget(dlg_edit)
        assert "Edit" in dlg_edit.windowTitle()
