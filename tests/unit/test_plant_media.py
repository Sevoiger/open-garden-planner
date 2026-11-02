"""Portable specimen media preserve originals and distinguish dates and kinds."""
from copy import deepcopy
from uuid import uuid4

import pytest
from PIL import Image

from open_garden_planner.core.plan_io import read_plan, write_plan
from open_garden_planner.core.project_data import ProjectData
from open_garden_planner.services.garden_archive import portable_backup, restore_backup
from open_garden_planner.services.plant_media import (
    edit_media_metadata,
    import_specimen_media,
    resolve_media,
)
from open_garden_planner.services.plant_specimens import add_specimen


def test_two_specimens_keep_distinct_photos_and_portable_thumbnails(tmp_path):
    project = tmp_path / "garden.ogp"
    document = ProjectData().to_dict()
    write_plan(project, document)
    identities = []
    for index, color in enumerate(("red", "blue")):
        source = tmp_path / f"photo{index}.jpg"
        Image.new("RGB", (500, 300), color).save(source)
        original = source.read_bytes()
        document, identity = add_specimen(document, "rose", color,
                                          cultivation_mode="pot" if index == 0 else "ground")
        identities.append(identity)
        asset = import_specimen_media(source, project, identity, caption=color)
        assert resolve_media(project, asset["photo_path"]).read_bytes() == original
        assert asset["taken_at"] is None  # Filesystem dates are not dates taken.
        assert asset["thumbnail_path"] != asset["photo_path"]
        specimen = document["plant_specimens"][identity]
        specimen["metadata"] = edit_media_metadata(specimen["metadata"], asset=asset)
    records = [document["plant_specimens"][identity]["metadata"]["plant_instance"]["media_assets"][0]
               for identity in identities]
    assert records[0]["id"] != records[1]["id"]
    archive = tmp_path / "portable.zip"
    portable_backup(document, project, archive)
    restored = restore_backup(archive, tmp_path / "moved")
    reopened = read_plan(restored)
    for identity in identities:
        asset = reopened["plant_specimens"][identity]["metadata"]["plant_instance"]["media_assets"][0]
        assert resolve_media(restored, asset["photo_path"]).is_file()
        assert resolve_media(restored, asset["thumbnail_path"]).is_file()


def test_deduplication_primary_and_removal_never_delete_original(tmp_path):
    project = tmp_path / "garden.ogp"
    project.write_text("{}")
    source = tmp_path / "photo.png"
    Image.new("RGB", (32, 32), "green").save(source)
    identity = str(uuid4())
    first = import_specimen_media(source, project, identity)
    second = import_specimen_media(source, project, identity)
    assert first["photo_path"] == second["photo_path"]
    metadata = edit_media_metadata({}, asset=first)
    metadata = edit_media_metadata(metadata, asset=second, primary_id=first["id"])
    assert len(metadata["plant_instance"]["media_assets"]) == 1
    before = deepcopy(metadata)
    removed = edit_media_metadata(metadata, remove_id=first["id"])
    assert removed["plant_instance"]["media_assets"] == []
    assert metadata == before
    assert resolve_media(project, first["photo_path"]).is_file()
    assert source.is_file()


@pytest.mark.parametrize("standard_ifd", [True, False])
def test_exif_date_is_separate_from_import_and_disk_failure_preserves_source(tmp_path, monkeypatch, standard_ifd):
    from PIL import ExifTags

    project = tmp_path / "garden.ogp"
    project.write_text("{}")
    source = tmp_path / "photo.jpg"
    exif = Image.Exif()
    if standard_ifd:
        exif[ExifTags.IFD.Exif] = {36867: "2024:06:12 10:30:00"}
    else:
        exif[36867] = "2024:06:12 10:30:00"
    Image.new("RGB", (32, 32), "green").save(source, exif=exif)
    before = source.read_bytes()
    asset = import_specimen_media(source, project, str(uuid4()))
    assert asset["taken_at"] == "2024-06-12T10:30:00"
    assert asset["imported_at"] != asset["taken_at"]
    def disk_full(*_args):
        raise OSError("disk full")
    monkeypatch.setattr("open_garden_planner.services.plant_media._atomic_bytes", disk_full)
    with pytest.raises(OSError, match="disk full"):
        import_specimen_media(source, project, str(uuid4()))
    assert source.read_bytes() == before


def test_invalid_or_external_folder_is_refused(tmp_path):
    project = tmp_path / "garden.ogp"
    project.write_text("{}")
    source = tmp_path / "photo.png"
    Image.new("RGB", (32, 32)).save(source)
    with pytest.raises(ValueError):
        import_specimen_media(source, project, str(uuid4()), folder="../outside")


def test_caption_correction_preserves_original_provenance_and_extensions():
    asset = {"id": "a" * 64, "kind": "personal", "photo_path": "photos/original.jpg",
             "taken_at": None, "imported_at": "2026-01-01T00:00:00+00:00", "credit": "Crédit",
             "future": {"preserve": True}}
    metadata = {"plant_instance": {"media_assets": [asset]}}
    result = edit_media_metadata(metadata, update_id=asset["id"], fields={"caption": "Nouvelle légende"})
    saved = result["plant_instance"]["media_assets"][0]
    assert saved == {**asset, "caption": "Nouvelle légende"}
    assert "caption" not in metadata["plant_instance"]["media_assets"][0]
