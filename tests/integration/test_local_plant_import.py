"""Owner JSON imports preserve provenance, private edits and existing stores."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from open_garden_planner.models.plant_lists import PlantListStore
from open_garden_planner.services import plant_library
from open_garden_planner.services.french_plant_catalog import (
    french_collections,
    localize_classic_lists,
)
from open_garden_planner.services.local_plant_import import install_collections, read_sheets
from open_garden_planner.services.species_catalog import offline_catalog, search_offline


def _sheet(directory, *, name="example", url="https://example.test/plant", height="2,50 m"):
    directory.mkdir(exist_ok=True)
    record = {"schema_version": "1.0", "url": url, "safe_name": name,
              "nom_botanique": "Testus 'Example'", "nom_commun": "Plante de test",
              "description_text": "Feuillage &eacute;toil&eacute;", "title": "Plante exemple",
              "characteristics": {"Hauteur à maturité": height, "Largeur à maturité": "40 cm",
                  "Exposition": "Soleil, Mi-ombre", "Croissance": "normale",
                  "Rusticité": "Jusqu'à -20.5°C", "Période de floraison": "Juillet à Août",
                  "Inflorescence": "Double"}, "floraison_mois": [6, 7, 8], "plantation_mois": [3, 4]}
    path = directory / f"{name}.json"
    path.write_text(json.dumps(record, ensure_ascii=False))
    return path


def test_sheets_map_units_and_keep_ambiguities_and_provenance(tmp_path):
    source = _sheet(tmp_path)
    plant, = read_sheets(tmp_path)
    assert plant.max_height_cm == 250
    assert plant.max_spread_cm == 40
    assert plant.description == "Feuillage étoilé"
    assert plant.common_name == "Plante de test"
    assert plant.sun_requirement.value == plant.cycle.value == plant.flower_type.value == "unknown"
    assert plant.hardiness_zone_min is None
    assert plant.transplant_start is None
    assert plant.image_url == ""
    assert plant.raw_data["local_sheet"] == json.loads(source.read_text())
    assert len(plant.raw_data["sha256"]) == 64


@pytest.mark.parametrize("bad", ["1 à 2 m", "-3 m", "0 cm", "250"])
def test_ambiguous_measurements_are_refused_before_import(tmp_path, bad):
    _sheet(tmp_path, height=bad)
    with pytest.raises(ValueError):
        read_sheets(tmp_path)


def test_duplicate_source_url_is_not_silently_overwritten(tmp_path):
    _sheet(tmp_path, name="first")
    _sheet(tmp_path, name="second")
    with pytest.raises(ValueError, match="Duplicate"):
        read_sheets(tmp_path)


def _load_library(monkeypatch, app_data):
    monkeypatch.setattr(plant_library, "get_app_data_dir", lambda: app_data)
    monkeypatch.setattr(plant_library, "_library_instance", None)


def test_import_search_and_list_replay_preserve_user_edits(qtbot, monkeypatch, tmp_path):
    source_dir = tmp_path / "sources"
    _sheet(source_dir)
    app_data = tmp_path / "data"
    app_data.mkdir()
    old = {"version": "1.0", "plants": {"mine": {"scientific_name": "Personal", "common_name": "Privée"}}}
    library_path = app_data / "custom_plants.json"
    library_path.write_text(json.dumps(old))
    original = library_path.read_bytes()
    plants = read_sheets(source_dir)
    collections = [("test-batch", "Mes plantes", plants)]
    result = install_collections(app_data, collections)
    assert result["added_to_search"] == 1
    assert any(Path(p).read_bytes() == original for p in result["backups"])
    assert json.loads(library_path.read_bytes())["plants"]["mine"] == old["plants"]["mine"]
    _load_library(monkeypatch, app_data)
    assert len(search_offline("Plante de test")) == 1
    store = PlantListStore(app_data / "plant_lists.json")
    collection = next(item for item in store.all_lists() if item.name == "Mes plantes")
    collection.name = "Renommée"
    collection.entries.clear()
    store.save()
    payload = json.loads(library_path.read_bytes())
    payload["plants"][plants[0].source_id]["common_name"] = "Nom personnel"
    library_path.write_text(json.dumps(payload))
    before = {p.name: p.read_bytes() for p in app_data.glob("*.json")}
    assert install_collections(app_data, collections)["added_to_search"] == 0
    assert before == {p.name: p.read_bytes() for p in app_data.glob("*.json")}
    from open_garden_planner.ui.panels.plant_lists_panel import PlantListsPanel
    with patch("open_garden_planner.ui.panels.plant_lists_panel.get_plant_list_store", return_value=store):
        panel = PlantListsPanel()
        qtbot.addWidget(panel)
        assert panel._list_combo.findText("Renommée") >= 0


@pytest.mark.parametrize("target", ["custom_plants.json", "plant_lists.json"])
def test_corrupt_store_refused_without_touching_other_files(tmp_path, target):
    path = tmp_path / target
    path.write_bytes(b'{"broken":')
    before = path.read_bytes()
    with pytest.raises(ValueError):
        install_collections(tmp_path, french_collections())
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_interrupted_two_store_import_is_completed_by_replay(monkeypatch, tmp_path):
    source = tmp_path / "sources"
    _sheet(source)
    app_data = tmp_path / "data"
    collections = [("batch", "Mes plantes", read_sheets(source))]
    with (patch.object(PlantListStore, "_atomic_write", side_effect=OSError("disk full")),
          pytest.raises(OSError)):
        install_collections(app_data, collections)
    assert (app_data / "custom_plants.json").exists()
    assert not (app_data / "plant_lists.json").exists()
    result = install_collections(app_data, collections)
    assert result["added_to_search"] == 0
    assert result["collections"] == [{"name": "Mes plantes", "count": 1}]


def test_completed_batch_does_not_restore_deleted_custom_species_or_add_new_content(tmp_path):
    source = tmp_path / "sources"
    _sheet(source)
    app_data = tmp_path / "data"
    first = read_sheets(source)
    install_collections(app_data, [("batch", "Collection", first)])
    path = app_data / "custom_plants.json"
    payload = json.loads(path.read_bytes())
    payload["plants"].pop(first[0].source_id)
    path.write_text(json.dumps(payload))
    _sheet(source, name="second", url="https://example.test/another-plant")
    before = {p.name: p.read_bytes() for p in app_data.glob("*.json")}
    result = install_collections(app_data, [("batch", "Collection", read_sheets(source))])
    assert result["added_to_search"] == 0
    assert result["skipped_collections"] == 1
    assert before == {p.name: p.read_bytes() for p in app_data.glob("*.json")}


def test_french_catalog_has_full_names_and_unspecified_generic_sage(monkeypatch, tmp_path):
    collections = french_collections()
    assert sum(len(rows) for _, _, rows in collections) == 198
    install_collections(tmp_path, collections)
    _load_library(monkeypatch, tmp_path)
    assert len(offline_catalog()) == 198  # French records replace, not duplicate, bundled identities.
    assert search_offline("Tomate")[0].common_name == "Tomate"
    assert search_offline("Tomato")[0].common_name == "Tomate"
    sage = next(p for p in search_offline("sauge") if p.common_name.startswith("Sauge —"))
    assert sage.scientific_name == ""
    assert sage.max_spread_cm is None and sage.max_height_cm is None
    assert sage.cycle.value == sage.sun_requirement.value == "unknown"
    assert search_offline("sauge officinale")[0].scientific_name == "Salvia officinalis"


def test_classic_name_translation_preserves_notes_geometry_and_replay(tmp_path):
    from open_garden_planner.services.classic_plant_lists import install_classics

    path = tmp_path / "plant_lists.json"
    install_classics(path)
    before = json.loads(path.read_bytes())
    assert localize_classic_lists(tmp_path) == 31
    after = json.loads(path.read_bytes())
    old_entries = [e for row in before["lists"] for e in row["entries"]]
    new_entries = [e for row in after["lists"] for e in row["entries"]]
    for old, new in zip(old_entries, new_entries, strict=True):
        old["species"]["common_name"] = new["species"]["common_name"]
        assert old == new
    assert localize_classic_lists(tmp_path) == 0
