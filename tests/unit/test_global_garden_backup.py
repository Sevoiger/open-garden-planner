"""Portable backup includes saved personal stores and verifies every byte."""
import json
import zipfile
from pathlib import Path

import pytest

from open_garden_planner.core.plan_io import write_plan
from open_garden_planner.services.garden_archive import portable_backup, restore_backup


def test_global_backup_round_trip_and_integrity(tmp_path):
    plan = tmp_path / "garden.ogp"
    data = {"version": "1.4", "canvas": {"width": 500, "height": 500}, "objects": []}
    write_plan(plan, data)
    app_data = tmp_path / "user-data"
    (app_data / "weather" / "runs" / "synthetic").mkdir(parents=True)
    (app_data / "custom_plants.json").write_text('{"version":"1.0","plants":{}}')
    (app_data / "plant_lists.json").write_text('[]')
    photo = tmp_path / "seed-photo.txt"
    photo.write_text("photo fictive")
    (app_data / "seed_inventory.json").write_text(json.dumps([{"id": "synthetic", "photo_path": str(photo), "media": [{"caption": "extension opaque"}]}]))
    (app_data / "acquisitions.json").write_text('[{"name":"fictif"}]')
    (app_data / "weather" / "runs" / "synthetic" / ".ogp-weather-run").write_text("marker")
    (app_data / "weather" / "runs" / "synthetic" / "rain.json").write_text('{}')
    (app_data / "weather" / "secret.env").write_text('synthetic secret')
    archive = tmp_path / "backup.zip"
    weather_config = tmp_path / "weather-config.json"
    weather_config.write_text('{"location":"synthetic"}')
    portable_backup(data, plan, archive, app_data=app_data, weather_config=weather_config)
    with zipfile.ZipFile(archive) as contents:
        names = contents.namelist()
        assert "app-data/acquisitions.json" in names
        assert "app-data/weather/runs/synthetic/.ogp-weather-run" in names
        assert not any(name.endswith(".env") for name in names)
        assert "configuration/weather-config.json" in names
        manifest = json.loads(contents.read("backup-manifest.json"))
        assert contents.read(manifest["external_media"][str(photo)]) == b"photo fictive"
    photo.unlink()
    restored = restore_backup(archive, tmp_path / "restored")
    assert restored.is_file()
    seed = json.loads((restored.parent / "app-data" / "seed_inventory.json").read_text())[0]
    assert Path(seed["photo_path"]).read_text() == "photo fictive"
    assert seed["media"] == [{"caption": "extension opaque"}]
    assert (restored.parent / "app-data" / "acquisitions.json").read_text() == '[{"name":"fictif"}]'
    assert (restored.parent / "app-data" / "weather" / "runs" / "synthetic" / "rain.json").is_file()
    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(archive) as original, zipfile.ZipFile(broken, "w") as output:
        for name in original.namelist():
            output.writestr(name, b"tampered" if name == "app-data/plant_lists.json" else original.read(name))
    with pytest.raises(ValueError, match="integrity"):
        restore_backup(broken, tmp_path / "broken-restored")
    assert not (tmp_path / "broken-restored").exists()
    assert json.loads(plan.read_text())["objects"] == []


def test_global_backup_rejects_symbolic_link(tmp_path):
    plan = tmp_path / "garden.ogp"
    data = {"version": "1.4", "objects": []}
    write_plan(plan, data)
    root = tmp_path / "data"
    (root / "weather").mkdir(parents=True)
    (root / "weather" / "outside").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        portable_backup(data, plan, tmp_path / "backup.zip", app_data=root)


def test_backup_never_overwrites_a_project_attachment(tmp_path):
    attachment = tmp_path / "document.txt"
    attachment.write_text("important fictif")
    plan = tmp_path / "garden.ogp"
    data = {"version": "1.4", "objects": [], "synthetic_extension": {"photo_path": "document.txt"}}
    write_plan(plan, data)
    with pytest.raises(ValueError, match="attached document"):
        portable_backup(data, plan, attachment, app_data=tmp_path / "personal")
    assert attachment.read_text() == "important fictif"


def test_large_weather_file_is_streamed_and_over_limit_keeps_prior_backup(tmp_path, monkeypatch):
    import open_garden_planner.services.garden_archive as module

    plan = tmp_path / "garden.ogp"
    data = {"version": "1.4", "objects": []}
    write_plan(plan, data)
    root = tmp_path / "data"
    (root / "weather").mkdir(parents=True)
    weather = root / "weather" / "map.bin"
    weather.write_bytes(b"x" * (2 * 1024 * 1024))
    original_read = Path.read_bytes

    def no_bulk_read(path):
        if path == weather:
            raise AssertionError("weather data must be streamed")
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", no_bulk_read)
    archive = tmp_path / "backup.zip"
    portable_backup(data, plan, archive, app_data=root)
    restored = restore_backup(archive, tmp_path / "restored")
    assert (restored.parent / "app-data/weather/map.bin").stat().st_size == 2 * 1024 * 1024
    previous = archive.read_bytes()
    monkeypatch.setattr(module, "MAX_ARCHIVE_BYTES", 1000)
    with pytest.raises(ValueError, match="exceeds"):
        portable_backup(data, plan, archive, app_data=root)
    assert archive.read_bytes() == previous
