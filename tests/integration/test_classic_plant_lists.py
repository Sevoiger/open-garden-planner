"""Starter data -> existing atomic store -> GUI list; preserve user records/retries."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.models.plant_lists import PlantListStore
from open_garden_planner.services.classic_plant_lists import export_classics, install_classics
from open_garden_planner.ui.panels.plant_lists_panel import PlantListsPanel


def test_classic_import_preserves_favorites_and_lists_and_replay_is_noop(qtbot, tmp_path):
    path = tmp_path / "plant_lists.json"
    store = PlantListStore(path)
    custom = PlantSpeciesData(scientific_name="Fictionalis testii", common_name="Plante fictive")
    favorite = store.add_entry(store.FAVORITES_ID, custom, "Note personnelle à conserver")
    user_list = store.create_list("Ma liste privée")
    store.add_entry(user_list.id, custom, "Autre note")
    original = path.read_bytes()
    before = json.loads(original)["lists"]
    result = install_classics(path)
    assert len(result["installed"]) == 4
    assert sum(r["count"] for r in result["installed"]) == 36
    assert Path(result["backup"]).read_bytes() == original
    payload = json.loads(path.read_bytes())
    assert all(row in payload["lists"] for row in before)
    reopened = PlantListStore(path)
    assert reopened.get_entry(favorite.id)[1].note == "Note personnelle à conserver"
    starter_id = result["installed"][0]["id"]
    reopened.rename_list(starter_id, "Ma sélection renommée")
    removed = reopened.get_list(starter_id).entries[0]
    reopened.remove_entry(starter_id, removed.id)
    edited_bytes = path.read_bytes()
    assert install_classics(path)["skipped"] == 4
    assert path.read_bytes() == edited_bytes  # no overwrite of edits, no replacement backup
    panel = PlantListsPanel(store=PlantListStore(path))
    qtbot.addWidget(panel)
    index = panel._list_combo.findData(starter_id)
    panel._list_combo.setCurrentIndex(index)
    assert panel._entries_list.count() == 8
    assert all(e.species.scientific_name for e in panel._store.get_list(starter_id).entries)


@pytest.mark.parametrize("payload", [b"{broken", b'{"version":"9.0","lists":[]}',
                                     b'{"version":"1.0","lists":[{"id":"x","entries":null}]}'])
def test_unreadable_library_is_refused_without_overwriting_it(tmp_path, payload):
    path = tmp_path / "plant_lists.json"
    path.write_bytes(payload)
    with pytest.raises((ValueError, TypeError, KeyError)):
        install_classics(path)
    assert path.read_bytes() == payload
    assert not list(tmp_path.glob("*.before-classics*"))


def test_interrupted_import_can_be_retried_without_duplicate_lists(tmp_path, monkeypatch):
    path = tmp_path / "plant_lists.json"
    original_write = PlantListStore._atomic_write
    calls = 0

    def fail_second_write(self, text):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("Simulated write failure")
        original_write(self, text)

    monkeypatch.setattr(PlantListStore, "_atomic_write", fail_second_write)
    with pytest.raises(OSError):
        install_classics(path)
    assert len(PlantListStore(path).all_lists()) == 2  # favorites and the first completed list
    monkeypatch.setattr(PlantListStore, "_atomic_write", original_write)
    result = install_classics(path)
    assert result["skipped"] == 1 and len(result["installed"]) == 3
    store = PlantListStore(path)
    assert len(store.all_lists()) == 5
    keys = [e.species_key for row in store.all_lists() for e in row.entries]
    assert len(keys) == len(set(keys)) == 36


def test_portable_lists_use_existing_import_and_cli_handles_accents(qtbot, tmp_path):
    directory = tmp_path / "Plantes classiques à importer"
    files = export_classics(directory)
    store = PlantListStore(tmp_path / "restored.json")
    for file in files:
        store.import_list_from_json(file.read_text(encoding="utf-8"))
    assert sum(len(row.entries) for row in store.all_lists()) == 36
    path = tmp_path / "Nouvelle bibliothèque" / "plant_lists.json"
    root = Path(__file__).parents[2]
    completed = subprocess.run([sys.executable, str(root / "scripts/import_classic_plants.py"),
                                "--storage-path", str(path)], capture_output=True, text=True, check=True)
    assert len(json.loads(completed.stdout)["installed"]) == 4
    assert len(PlantListStore(path).all_lists()) == 5
