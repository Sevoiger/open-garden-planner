"""Botanical references share the durable library and portable attachment path."""
import json
import shutil
import zipfile

import pytest
from PIL import Image

from open_garden_planner.core.plan_io import write_plan
from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services.botanical_media import (
    edit_reference_media,
    import_reference_media,
    primary_reference,
    reference_assets,
    resolve_reference_media,
)
from open_garden_planner.services.garden_archive import portable_backup, restore_backup
from open_garden_planner.services.library_operations import LibraryConflictError
from open_garden_planner.services.plant_library import PlantLibrary


def make_reference(tmp_path):
    root = tmp_path / "app-data"
    library = PlantLibrary(root / "custom_plants.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose", raw_data={"future": {"preserve": True}}))
    source = tmp_path / "reference.jpg"
    Image.new("RGB", (400, 250), "pink").save(source)
    asset = import_reference_media(source, library.path, identity, caption="Rose de référence",
                                  credit="Crédit fournisseur", source_url="https://example.test/rose")
    return library, identity, source, asset


def test_reference_import_dedup_primary_and_remove_preserve_originals(tmp_path):
    library, identity, source, asset = make_reference(tmp_path)
    original = source.read_bytes()
    duplicate = import_reference_media(source, library.path, identity)
    assert duplicate["photo_path"] == asset["photo_path"]
    plant = edit_reference_media(library.get_plant(identity), asset=asset, primary_id=asset["id"])
    library.update_plant(identity, plant)
    reopened = PlantLibrary(library.path)
    saved = reopened.get_plant(identity)
    assert primary_reference(saved)["id"] == asset["id"]
    assert saved.raw_data["future"] == {"preserve": True}
    assert resolve_reference_media(reopened.path, asset["photo_path"]).read_bytes() == original
    assert saved.raw_data["reference_media"][0]["taken_at"] is None
    removed = edit_reference_media(saved, remove_id=asset["id"])
    reopened.update_plant(identity, removed)
    assert reference_assets(reopened.get_plant(identity)) == []
    assert source.read_bytes() == original
    assert resolve_reference_media(reopened.path, asset["photo_path"]).is_file()


def test_reference_association_refuses_concurrent_library_edit(tmp_path):
    library, identity, _source, asset = make_reference(tmp_path)
    revision = library.revision
    stale = edit_reference_media(library.get_plant(identity), asset=asset)
    other = PlantLibrary(library.path)
    updated = other.get_plant(identity)
    updated.common_name = "Correction concurrente"
    other.update_plant(identity, updated)
    with pytest.raises(LibraryConflictError):
        library.update_plant(identity, stale, expected_revision=revision)
    reopened = PlantLibrary(library.path)
    assert reopened.get_plant(identity).common_name == "Correction concurrente"
    assert not reference_assets(reopened.get_plant(identity))


def test_botanical_reference_and_thumbnail_survive_global_portable_backup(tmp_path):
    library, identity, _source, asset = make_reference(tmp_path)
    library.update_plant(identity, edit_reference_media(library.get_plant(identity), asset=asset, primary_id=asset["id"]))
    before = library.path.read_bytes()
    root = library.path.parent
    (root / "unrelated-private.txt").write_text("not selected")
    project = tmp_path / "garden.ogp"
    snapshot = {"version": "1.4", "objects": []}
    write_plan(project, snapshot)
    archive = tmp_path / "backup.zip"
    portable_backup(snapshot, project, archive, app_data=root)
    with zipfile.ZipFile(archive) as contents:
        assert "app-data/" + asset["photo_path"] in contents.namelist()
        assert "app-data/" + asset["thumbnail_path"] in contents.namelist()
        assert "app-data/unrelated-private.txt" not in contents.namelist()
        assert contents.read("app-data/custom_plants.json") == before
    shutil.rmtree(root)
    restored = restore_backup(archive, tmp_path / "restored")
    reopened = PlantLibrary(restored.parent / "app-data/custom_plants.json")
    saved = primary_reference(reopened.get_plant(identity))
    assert resolve_reference_media(reopened.path, saved["photo_path"]).is_file()
    assert resolve_reference_media(reopened.path, saved["thumbnail_path"]).is_file()
    assert saved["credit"] == "Crédit fournisseur"
    assert reopened.path.read_bytes() == before


def test_missing_reference_fails_without_replacing_existing_backup(tmp_path):
    library, identity, _source, asset = make_reference(tmp_path)
    library.update_plant(identity, edit_reference_media(library.get_plant(identity), asset=asset))
    resolve_reference_media(library.path, asset["photo_path"]).unlink()
    project = tmp_path / "garden.ogp"
    snapshot = {"version": "1.4", "objects": []}
    write_plan(project, snapshot)
    backup = tmp_path / "backup.zip"
    backup.write_bytes(b"existing backup")
    with pytest.raises(ValueError, match="unavailable"):
        portable_backup(snapshot, project, backup, app_data=library.path.parent)
    assert backup.read_bytes() == b"existing backup"


def test_reference_paths_cannot_escape_library_root(tmp_path):
    library, identity, _source, asset = make_reference(tmp_path)
    plant = library.get_plant(identity)
    malicious = {**asset, "photo_path": "../outside.jpg"}
    with pytest.raises(ValueError):
        edit_reference_media(plant, asset=malicious)
    document = json.loads(library.path.read_text())
    document["plants"][identity]["raw_data"]["reference_media"] = [malicious]
    library.path.write_text(json.dumps(document))
    project = tmp_path / "garden.ogp"
    write_plan(project, {"version": "1.4", "objects": []})
    with pytest.raises(ValueError):
        portable_backup({"version": "1.4", "objects": []}, project, tmp_path / "backup.zip", app_data=library.path.parent)


def test_unrelated_reference_edit_preserves_future_primary_and_asset_fields(tmp_path):
    library, identity, _source, asset = make_reference(tmp_path)
    plant = edit_reference_media(library.get_plant(identity), asset={**asset, "future": {"keep": True}})
    plant.raw_data["reference_primary_media_id"] = {"future_selection": "keep"}
    edited = edit_reference_media(plant, update_id=asset["id"], fields={"caption": "Correction"})
    assert edited.raw_data["reference_primary_media_id"] == {"future_selection": "keep"}
    assert edited.raw_data["reference_media"][0]["future"] == {"keep": True}
    assert plant.raw_data["reference_media"][0]["caption"] == "Rose de référence"
