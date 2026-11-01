"""Portable export/import switches data only at restart and preserves both copies."""
import json
from pathlib import Path

import pytest

from open_garden_planner.app.settings import get_settings
from open_garden_planner.core.plan_io import write_plan
from open_garden_planner.models.seed_inventory import SeedInventoryStore
from open_garden_planner.services.acquisition_inventory import AcquisitionInventory
from open_garden_planner.services.garden_archive import portable_backup
from open_garden_planner.services.workspace_transfer import (
    activate_workspace,
    prepare_workspace_import,
)


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    from open_garden_planner.app.settings import SETTINGS_NO_ERROR

    class MemorySettings:
        def __init__(self):
            self.values = {}
        def value(self, key, default="", **_kwargs):
            return self.values.get(key, default)
        def setValue(self, key, value):
            self.values[key] = value
        def sync(self):
            pass
        def status(self):
            return SETTINGS_NO_ERROR

    monkeypatch.setattr(get_settings(), "_settings", MemorySettings())


def _write_weather_config(path):
    path.write_text(json.dumps({"location": {"name": "Synthetic", "latitude": 1, "longitude": 2, "timezone": "UTC"},
        "garden_context": "Synthetic", "refresh_hours": 24, "request_spacing_seconds": 1, "models": {}, "ensembles": {},
        "meteociel": {"enabled": False, "groups": {}, "local_page": "https://www.meteociel.fr/previsions/example"}}))


def _export(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    data = source / "personal"
    data.mkdir()
    plan = source / "garden.ogp"
    snapshot = {"version": "1.4", "canvas": {"width": 500, "height": 500}, "objects": []}
    write_plan(plan, snapshot)
    (data / "custom_plants.json").write_text('{"version":"1.0","plants":{}}')
    (data / "plant_lists.json").write_text('[]')
    photo = source / "photo.txt"
    photo.write_text("synthetic photograph")
    (data / "seed_inventory.json").write_text(json.dumps([
        {"id": "seed-1", "species_name": "Synthetic", "purchase_year": 2025, "photo_path": str(photo)}]))
    AcquisitionInventory(data / "acquisitions.json").put({"name": "Synthetic compost", "kind": "Compost"})
    (data / "weather").mkdir()
    (data / "weather" / "latest.json").write_text('{"example":"synthetic"}')
    config = source / "weather-config.json"
    _write_weather_config(config)
    archive = tmp_path / "export.zip"
    portable_backup(snapshot, plan, archive, app_data=data, weather_config=config)
    return archive, plan, data


def test_import_prepare_activate_and_return_to_local(qtbot, monkeypatch, tmp_path):
    import open_garden_planner.services.plant_library as plant_library

    archive, old_plan, old_data = _export(tmp_path)
    monkeypatch.setattr(plant_library, "_workspace_data_dir", None)
    monkeypatch.setattr(plant_library, "_default_app_data_dir", lambda: old_data)
    old_stock = (old_data / "acquisitions.json").read_bytes()
    old_seed = (old_data / "seed_inventory.json").read_bytes()
    plan, data = prepare_workspace_import(archive, tmp_path)
    assert plant_library.get_app_data_dir() == old_data
    assert data.parent.stat().st_mode & 0o077 == 0
    assert (old_data / "acquisitions.json").read_bytes() == old_stock
    assert (old_data / "seed_inventory.json").read_bytes() == old_seed
    settings = get_settings()
    settings.workspace_selection = {"data_dir": str(data), "plan_path": str(plan),
                                    "default_plan": str(old_plan), "pending": True}
    assert plant_library.get_app_data_dir() == old_data
    activate_workspace(settings)
    assert plant_library.get_app_data_dir() == data
    assert settings.last_saved_project == str(plan) and not settings.workspace_selection["pending"]
    assert AcquisitionInventory(data / "acquisitions.json").all()[0]["name"] == "Synthetic compost"
    packet = SeedInventoryStore(data / "seed_inventory.json").all()[0]
    assert Path(packet.photo_path).read_text() == "synthetic photograph"
    assert (data / "weather" / "latest.json").is_file()
    from open_garden_planner.services.weather_workspace import weather_config_path
    assert json.loads(weather_config_path().read_text())["location"]["latitude"] == 1
    settings.workspace_selection = {"data_dir": "", "plan_path": str(old_plan),
                                    "default_plan": str(old_plan), "pending": True}
    assert plant_library.get_app_data_dir() == data
    activate_workspace(settings)
    assert plant_library.get_app_data_dir() == old_data and settings.last_saved_project == str(old_plan)
    assert (old_data / "seed_inventory.json").read_bytes() == old_seed


def test_missing_workspace_refuses_silent_fallback(monkeypatch, tmp_path):
    import open_garden_planner.services.plant_library as plant_library

    monkeypatch.setattr(plant_library, "_workspace_data_dir", None)
    settings = get_settings()
    root = tmp_path / "missing" / "app-data"
    settings.workspace_selection = {"data_dir": str(root), "plan_path": str(root.parent / "garden.ogp"),
                                    "default_plan": "", "pending": True}
    with pytest.raises(ValueError, match="unavailable"):
        activate_workspace(settings)
    assert plant_library.workspace_data_dir() is None and settings.workspace_selection["pending"]


def test_import_rejects_plan_only_backup_without_switch(qtbot, tmp_path):
    plan = tmp_path / "garden.ogp"
    snapshot = {"version": "1.4", "objects": []}
    write_plan(plan, snapshot)
    archive = tmp_path / "old.zip"
    portable_backup(snapshot, plan, archive)
    settings = get_settings()
    before = settings.workspace_selection
    with pytest.raises(ValueError, match="plan-only"):
        prepare_workspace_import(archive, tmp_path)
    assert settings.workspace_selection == before


def test_import_action_schedules_without_switching_open_garden(qtbot, monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QFileDialog, QMessageBox

    import open_garden_planner.services.plant_library as plant_library
    from open_garden_planner.app.application import GardenPlannerApp

    archive, old_plan, old_data = _export(tmp_path)
    monkeypatch.setattr(plant_library, "_workspace_data_dir", None)
    monkeypatch.setattr(plant_library, "_default_app_data_dir", lambda: old_data)
    monkeypatch.setattr(GardenPlannerApp, "_startup_sequence", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_start_update_check", lambda _self: None)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_a, **_kw: (str(archive), ""))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_a, **_kw: str(tmp_path))
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Yes)
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *_a, **_kw: messages.append(_a))
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    settings = get_settings()
    settings.last_saved_project = str(old_plan)
    window._project_manager.mark_dirty()
    old_contents = (old_data / "seed_inventory.json").read_bytes()
    window._on_extract_global_backup()
    selection = settings.workspace_selection
    assert selection["pending"] and Path(selection["data_dir"]).is_dir()
    assert plant_library.get_app_data_dir() == old_data
    assert window._project_manager.is_dirty and (old_data / "seed_inventory.json").read_bytes() == old_contents
    assert messages
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Discard)
    window.close()
    activate_workspace(settings)
    assert plant_library.get_app_data_dir() == Path(selection["data_dir"])
    assert settings.last_saved_project == selection["plan_path"]
    from open_garden_planner.models import plant_lists, seed_inventory
    monkeypatch.setattr(plant_lists, "_STORE", None)
    monkeypatch.setattr(seed_inventory, "_store", None)
    monkeypatch.setattr(plant_library, "_library_instance", None)
    reopened = GardenPlannerApp()
    qtbot.addWidget(reopened)
    assert reopened._resume_last_project()
    assert reopened._project_manager.current_file == Path(selection["plan_path"])
    assert reopened.seed_inventory_view._store.all()[0].species_name == "Synthetic"
    reopened._on_acquisitions()
    assert reopened.acquisition_inventory_view._view.table.item(0, 0).text() == "Synthetic compost"
    reopened.close()


def test_activated_workspace_keeps_working_after_save_as_and_old_plan_removed(qtbot, monkeypatch, tmp_path):
    import open_garden_planner.services.plant_library as plant_library

    archive, old_plan, _old_data = _export(tmp_path)
    monkeypatch.setattr(plant_library, "_workspace_data_dir", None)
    plan, data = prepare_workspace_import(archive, tmp_path)
    settings = get_settings()
    settings.workspace_selection = {"data_dir": str(data), "plan_path": str(plan),
                                    "default_plan": str(old_plan), "pending": True}
    activate_workspace(settings)
    saved_as = tmp_path / "elsewhere.ogp"
    write_plan(saved_as, {"version": "1.4", "objects": []})
    settings.last_saved_project = str(saved_as)
    plan.unlink()
    plant_library.configure_workspace_data_dir(None)
    activate_workspace(settings)
    assert plant_library.get_app_data_dir() == data
    assert settings.last_saved_project == str(saved_as)


def test_newer_plan_is_rejected_before_scheduling(qtbot, tmp_path):
    from open_garden_planner.core.project_data import FILE_VERSION, _is_newer_file_version

    archive, original, data = _export(tmp_path)
    snapshot = {"version": "999.0", "objects": []}
    write_plan(original, snapshot)
    portable_backup(snapshot, original, archive, app_data=data, weather_config=original.parent / "weather-config.json")
    assert _is_newer_file_version("999.0", FILE_VERSION)
    settings = get_settings()
    before = settings.workspace_selection
    with pytest.raises(ValueError, match="newer version"):
        prepare_workspace_import(archive, tmp_path)
    assert settings.workspace_selection == before


def test_user_can_reselect_an_older_import_with_another_plan(qtbot, monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QFileDialog, QMessageBox

    import open_garden_planner.services.plant_library as plant_library
    from open_garden_planner.app.application import GardenPlannerApp

    archive, original, old_data = _export(tmp_path)
    first_plan, first_data = prepare_workspace_import(archive, tmp_path)
    other_plan = tmp_path / "saved-as.ogp"
    write_plan(other_plan, {"version": "1.4", "objects": []})
    first_plan.unlink()
    second_plan, second_data = prepare_workspace_import(archive, tmp_path)
    monkeypatch.setattr(plant_library, "_workspace_data_dir", None)
    monkeypatch.setattr(plant_library, "_default_app_data_dir", lambda: old_data)
    settings = get_settings()
    settings.workspace_selection = {"data_dir": str(second_data), "plan_path": str(second_plan),
                                    "default_plan": str(original), "pending": False}
    activate_workspace(settings)
    monkeypatch.setattr(GardenPlannerApp, "_startup_sequence", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_start_update_check", lambda _self: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *_a, **_kw: None)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_a, **_kw: str(first_data.parent))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_a, **_kw: (str(other_plan), ""))
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    window._on_use_imported_folder()
    chosen = settings.workspace_selection
    assert chosen["pending"] and chosen["data_dir"] == str(first_data) and chosen["plan_path"] == str(other_plan)
    assert plant_library.get_app_data_dir() == second_data
    window.close()
    activate_workspace(settings)
    assert plant_library.get_app_data_dir() == first_data
    assert settings.last_saved_project == str(other_plan)


def test_last_saved_plan_is_scoped_per_workspace_and_preserves_newer_local_save(qtbot, monkeypatch, tmp_path):
    import open_garden_planner.services.plant_library as plant_library

    archive, old_plan, old_data = _export(tmp_path)
    monkeypatch.setattr(plant_library, "_workspace_data_dir", None)
    monkeypatch.setattr(plant_library, "_default_app_data_dir", lambda: old_data)
    settings = get_settings()
    settings.last_saved_project = str(old_plan)
    plan, data = prepare_workspace_import(archive, tmp_path)
    settings.workspace_selection = {"data_dir": str(data), "plan_path": str(plan),
                                    "default_plan": str(old_plan), "pending": True}
    activate_workspace(settings)
    assert settings.last_saved_project == str(plan)
    plant_library.configure_workspace_data_dir(None)
    newer_local = tmp_path / "new-local.ogp"
    write_plan(newer_local, {"objects": []})
    settings.last_saved_project = str(newer_local)
    plant_library.configure_workspace_data_dir(data)
    assert settings.last_saved_project == str(plan)
    settings.workspace_selection = {"data_dir": "", "plan_path": str(old_plan),
                                    "default_plan": str(old_plan), "pending": True}
    activate_workspace(settings)
    assert plant_library.get_app_data_dir() == old_data
    assert settings.last_saved_project == str(newer_local)


def test_reselecting_workspace_prefers_its_latest_saved_plan(qtbot, monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QFileDialog, QMessageBox

    import open_garden_planner.services.plant_library as plant_library
    from open_garden_planner.app.application import GardenPlannerApp

    archive, original, old_data = _export(tmp_path)
    first_plan, first_data = prepare_workspace_import(archive, tmp_path)
    second_plan, second_data = prepare_workspace_import(archive, tmp_path)
    monkeypatch.setattr(plant_library, "_workspace_data_dir", None)
    monkeypatch.setattr(plant_library, "_default_app_data_dir", lambda: old_data)
    settings = get_settings()
    settings.workspace_selection = {"data_dir": str(first_data), "plan_path": str(first_plan),
                                    "default_plan": str(original), "pending": True}
    activate_workspace(settings)
    newer = tmp_path / "later.ogp"
    write_plan(newer, {"version": "1.4", "objects": []})
    settings.last_saved_project = str(newer)
    settings.workspace_selection = {"data_dir": str(second_data), "plan_path": str(second_plan),
                                    "default_plan": str(original), "pending": True}
    activate_workspace(settings)
    monkeypatch.setattr(GardenPlannerApp, "_startup_sequence", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_start_update_check", lambda _self: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *_a, **_kw: None)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_a, **_kw: str(first_data.parent))
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    window._on_use_imported_folder()
    assert first_plan.exists()
    assert settings.workspace_selection["plan_path"] == str(newer)
    assert plant_library.get_app_data_dir() == second_data
    window.close()
    activate_workspace(settings)
    assert plant_library.get_app_data_dir() == first_data and settings.last_saved_project == str(newer)


def test_import_rejects_plan_that_fails_real_detached_loading(qtbot, tmp_path):
    archive, plan, data = _export(tmp_path)
    malformed = {"version": "1.4", "objects": [], "layers": ["not a layer"]}
    write_plan(plan, malformed)
    portable_backup(malformed, plan, archive, app_data=data, weather_config=plan.parent / "weather-config.json")
    settings = get_settings()
    before = settings.workspace_selection
    with pytest.raises(ValueError, match="cannot be opened"):
        prepare_workspace_import(archive, tmp_path)
    assert settings.workspace_selection == before


def test_competing_activation_does_not_replace_newer_saved_plan(qtbot, monkeypatch, tmp_path):
    import open_garden_planner.services.plant_library as plant_library

    archive, original, old_data = _export(tmp_path)
    monkeypatch.setattr(plant_library, "_workspace_data_dir", None)
    monkeypatch.setattr(plant_library, "_default_app_data_dir", lambda: old_data)
    plan, data = prepare_workspace_import(archive, tmp_path)
    settings = get_settings()
    settings.workspace_selection = {"data_dir": str(data), "plan_path": str(plan),
                                    "default_plan": str(original), "pending": True}
    saved_later = tmp_path / "later.ogp"
    write_plan(saved_later, {"objects": []})
    real_commit = settings.activate_scheduled_workspace
    once = []

    def competitor(expected):
        if not once:
            once.append(True)
            real_commit(expected)
            settings.last_saved_project = str(saved_later)
        return real_commit(expected)

    monkeypatch.setattr(settings, "activate_scheduled_workspace", competitor)
    activate_workspace(settings)
    assert settings.last_saved_project == str(saved_later)
    assert not settings.workspace_selection["pending"]


def test_competing_import_selection_is_not_erased(qtbot, monkeypatch, tmp_path):
    import open_garden_planner.services.plant_library as plant_library

    archive, original, old_data = _export(tmp_path)
    monkeypatch.setattr(plant_library, "_workspace_data_dir", None)
    monkeypatch.setattr(plant_library, "_default_app_data_dir", lambda: old_data)
    first_plan, first_data = prepare_workspace_import(archive, tmp_path)
    second_plan, second_data = prepare_workspace_import(archive, tmp_path)
    settings = get_settings()
    first = {"data_dir": str(first_data), "plan_path": str(first_plan),
             "default_plan": str(original), "pending": True}
    second = {"data_dir": str(second_data), "plan_path": str(second_plan),
              "default_plan": str(original), "pending": True}
    settings.workspace_selection = first
    real_commit = settings.activate_scheduled_workspace
    once = []

    def competitor(expected):
        if not once:
            once.append(True)
            settings.compare_and_set_workspace_selection(first, second)
        return real_commit(expected)

    monkeypatch.setattr(settings, "activate_scheduled_workspace", competitor)
    activate_workspace(settings)
    assert settings.workspace_selection["data_dir"] == str(second_data)
    assert not settings.workspace_selection["pending"]
    assert plant_library.get_app_data_dir() == second_data


def test_moved_workspace_retains_seed_photos_and_can_be_exported_again(qtbot, tmp_path):
    import shutil

    archive, _original, _old_data = _export(tmp_path)
    plan, data = prepare_workspace_import(archive, tmp_path)
    moved = tmp_path / "moved-workspace"
    shutil.move(str(data.parent), moved)
    moved_data = moved / "app-data"
    moved_plan = moved / plan.name
    store = SeedInventoryStore(moved_data / "seed_inventory.json")
    seed = store.all()[0]
    assert Path(seed.photo_path).parent == moved / "personal-media"
    assert Path(seed.photo_path).read_text() == "synthetic photograph"
    seed.quantity = 2
    store.add(seed)
    store.save()
    moved_again = tmp_path / "moved-again"
    shutil.move(str(moved), moved_again)
    moved = moved_again
    moved_data = moved / "app-data"
    moved_plan = moved / plan.name
    seed = SeedInventoryStore(moved_data / "seed_inventory.json").all()[0]
    assert Path(seed.photo_path).parent == moved / "personal-media"
    assert Path(seed.photo_path).read_text() == "synthetic photograph" and seed.quantity == 2
    from open_garden_planner.core.plan_io import read_plan
    from open_garden_planner.services.workspace_transfer import validate_workspace
    validate_workspace(moved, moved_plan)
    exported = tmp_path / "moved-export.zip"
    portable_backup(read_plan(moved_plan), moved_plan, exported, app_data=moved_data,
                    weather_config=moved / "configuration" / "weather-config.json")
    restored_plan, restored_data = prepare_workspace_import(exported, tmp_path)
    restored_seed = SeedInventoryStore(restored_data / "seed_inventory.json").all()[0]
    assert Path(restored_seed.photo_path).read_text() == "synthetic photograph"
    assert restored_plan.is_file()


def test_import_validation_uses_only_imported_smart_symbols(qtbot, monkeypatch, tmp_path):
    import open_garden_planner.services.smart_symbol_library as smart_symbols
    from open_garden_planner.services.workspace_transfer import validate_workspace

    current = tmp_path / "current-symbols"
    current.mkdir()
    folder = tmp_path / "imported"
    target = folder / "app-data" / "smart_symbols"
    target.mkdir(parents=True)
    (folder / "backup-manifest.json").write_text('{}')
    (folder / "configuration").mkdir()
    _write_weather_config(folder / "configuration" / "weather-config.json")
    plan = folder / "garden.ogp"
    write_plan(plan, {"version": "1.4", "objects": [{"type": "group",
        "item_id": "00000000-0000-4000-8000-000000000001", "children": [],
        "smart_symbol": {"id": "synthetic_symbol", "version": 1, "params": {}}}]})

    def definition(extreme):
        return {"id": "synthetic_symbol", "version": 1, "name": "Synthetic", "category": "test",
                "parameters": [], "elements": [{"kind": "rect", "x": 1e308 if extreme else 0,
                                                   "y": 0, "w": 1e308 if extreme else 10, "h": 10}]}

    (current / "symbol.json").write_text(json.dumps(definition(True)))
    (target / "symbol.json").write_text(json.dumps(definition(False)))
    active = smart_symbols.SmartSymbolLibrary(user_dir=current)
    monkeypatch.setattr(smart_symbols, "_default_library", active)
    validate_workspace(folder, plan)
    assert smart_symbols.get_smart_symbol_library() is active
    (current / "symbol.json").write_text(json.dumps(definition(False)))
    active.reload()
    (target / "symbol.json").write_text(json.dumps(definition(True)))
    with pytest.raises(ValueError, match="cannot be opened"):
        validate_workspace(folder, plan)
    assert smart_symbols.get_smart_symbol_library() is active


def test_invalid_personal_store_import_leaves_no_new_workspace(qtbot, tmp_path):
    archive, plan, data = _export(tmp_path)
    (data / "acquisitions.json").write_text("null")
    from open_garden_planner.core.plan_io import read_plan
    portable_backup(read_plan(plan), plan, archive, app_data=data, weather_config=plan.parent / "weather-config.json")
    before = set(tmp_path.glob("garden-import-*"))
    with pytest.raises(Exception, match="Invalid acquisition"):
        prepare_workspace_import(archive, tmp_path)
    assert set(tmp_path.glob("garden-import-*")) == before
    assert (data / "acquisitions.json").read_text() == "null"


def test_symlinked_workspace_subdirectory_is_refused(qtbot, monkeypatch, tmp_path):
    import open_garden_planner.services.plant_library as plant_library
    from open_garden_planner.services.workspace_transfer import validate_workspace

    archive, original, _old_data = _export(tmp_path)
    monkeypatch.setattr(plant_library, "_workspace_data_dir", None)
    plan, data = prepare_workspace_import(archive, tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("untouched")
    (data / "guilds").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="link or special file"):
        validate_workspace(data.parent, plan)
    settings = get_settings()
    settings.workspace_selection = {"data_dir": str(data), "plan_path": str(plan),
                                    "default_plan": str(original), "pending": True}
    with pytest.raises(ValueError, match="link or special file"):
        activate_workspace(settings)
    assert plant_library.workspace_data_dir() is None
    assert (outside / "keep.txt").read_text() == "untouched"


def test_missing_imported_weather_configuration_never_falls_back(qtbot, monkeypatch, tmp_path):
    import open_garden_planner.services.plant_library as plant_library
    from open_garden_planner.services.weather_workspace import weather_config_path

    archive, original, _old_data = _export(tmp_path)
    monkeypatch.setattr(plant_library, "_workspace_data_dir", None)
    plan, data = prepare_workspace_import(archive, tmp_path)
    settings = get_settings()
    settings.workspace_selection = {"data_dir": str(data), "plan_path": str(plan),
                                    "default_plan": str(original), "pending": True}
    activate_workspace(settings)
    (data.parent / "configuration" / "weather-config.json").unlink()
    with pytest.raises(OSError, match="weather configuration"):
        weather_config_path()
    plant_library.configure_workspace_data_dir(None)
    with pytest.raises(ValueError, match="weather configuration"):
        activate_workspace(settings)
    assert plant_library.workspace_data_dir() is None


def test_moved_workspace_missing_photo_does_not_read_original(qtbot, tmp_path):
    import shutil

    from open_garden_planner.core.personal_store import PersonalStoreLoadError
    from open_garden_planner.core.plan_io import read_plan
    from open_garden_planner.services.workspace_transfer import validate_workspace

    archive, _original, _old_data = _export(tmp_path)
    plan, data = prepare_workspace_import(archive, tmp_path)
    copied = tmp_path / "copied-workspace"
    shutil.copytree(data.parent, copied)
    photos = list((copied / "personal-media").iterdir())
    assert len(photos) == 1
    photos[0].unlink()
    original_photo = SeedInventoryStore(data / "seed_inventory.json").all()[0].photo_path
    assert Path(original_photo).is_file()
    with pytest.raises(PersonalStoreLoadError, match="missing"):
        SeedInventoryStore(copied / "app-data" / "seed_inventory.json").all()
    with pytest.raises(PersonalStoreLoadError, match="missing"):
        validate_workspace(copied, copied / plan.name)
    with pytest.raises(ValueError, match="missing"):
        portable_backup(read_plan(copied / plan.name), copied / plan.name, tmp_path / "bad.zip",
                        app_data=copied / "app-data", weather_config=copied / "configuration" / "weather-config.json")
    assert Path(original_photo).is_file() and not (tmp_path / "bad.zip").exists()


def test_symlinked_weather_configuration_folder_is_refused(qtbot, monkeypatch, tmp_path):
    import open_garden_planner.services.plant_library as plant_library
    from open_garden_planner.services.weather_workspace import weather_config_path
    from open_garden_planner.services.workspace_transfer import validate_workspace

    archive, original, _old_data = _export(tmp_path)
    monkeypatch.setattr(plant_library, "_workspace_data_dir", None)
    plan, data = prepare_workspace_import(archive, tmp_path)
    configuration = data.parent / "configuration"
    external = tmp_path / "external-configuration"
    configuration.rename(external)
    configuration.symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="weather configuration"):
        validate_workspace(data.parent, plan)
    settings = get_settings()
    settings.workspace_selection = {"data_dir": str(data), "plan_path": str(plan),
                                    "default_plan": str(original), "pending": True}
    with pytest.raises(ValueError, match="weather configuration"):
        activate_workspace(settings)
    plant_library.configure_workspace_data_dir(data)
    with pytest.raises(OSError, match="weather configuration"):
        weather_config_path()
    assert (external / "weather-config.json").is_file()


def test_open_stores_refuse_replaced_imported_data_root(tmp_path):
    """An identical external copy must not defeat cached-store conflict checks."""
    import shutil

    from open_garden_planner.core.personal_store import (
        PersonalStoreLoadError,
        PersonalStorePersistenceError,
    )
    from open_garden_planner.services.guild_library import GuildLibrary
    from open_garden_planner.services.plant_library import (
        PlantLibrary,
        configure_workspace_data_dir,
    )
    from open_garden_planner.services.weather_workspace import WeatherWorkspace

    container = tmp_path / "workspace"
    data = container / "app-data"
    data.mkdir(parents=True)
    (data / "acquisitions.json").write_text('[]')
    (data / "seed_inventory.json").write_text('[]')
    (container / "backup-manifest.json").write_text('{}')
    (data / "custom_plants.json").write_text('{"version":"1.0","plants":{}}')
    external = tmp_path / "external"
    shutil.copytree(data, external)
    configure_workspace_data_dir(data)
    try:
        acquisitions = AcquisitionInventory(data / "acquisitions.json")
        seeds = SeedInventoryStore(data / "seed_inventory.json")
        library = PlantLibrary(data / "custom_plants.json")
        guild = GuildLibrary(bundled_dir=tmp_path, user_dir=data / "guilds")
        weather = WeatherWorkspace(root=data / "weather", config={})
        data.rename(container / "original-data")
        data.symlink_to(external, target_is_directory=True)
        with pytest.raises(PersonalStoreLoadError, match="replaced"):
            acquisitions.reload()
        with pytest.raises(OSError, match="replaced"):
            weather.load()
        with pytest.raises(OSError, match="replaced"):
            acquisitions.put({"name": "Compost", "kind": "Compost"})
        with pytest.raises(PersonalStorePersistenceError, match="replaced"):
            seeds.save()
        with pytest.raises(OSError, match="replaced"):
            library._commit({"version": "1.0", "plants": {}, "new_field": True})
        with pytest.raises(OSError, match="replaced"):
            guild.save(None)
        with pytest.raises(OSError, match="replaced"):
            weather.refresh()
        with pytest.raises(OSError, match="replaced"):
            weather.generate()
        with pytest.raises(OSError, match="replaced"):
            write_plan(container / "garden.ogp", {"version": "1.4", "objects": []})
        assert (external / "acquisitions.json").read_text() == '[]'
        assert (external / "seed_inventory.json").read_text() == '[]'
        assert (external / "custom_plants.json").read_text() == '{"version":"1.0","plants":{}}'
    finally:
        configure_workspace_data_dir(None)


def test_companion_rule_symlink_does_not_overwrite_external_file(tmp_path):
    from open_garden_planner.services.companion_planting_service import CompanionPlantingService
    from open_garden_planner.services.plant_library import configure_workspace_data_dir

    data = tmp_path / "workspace" / "app-data"
    data.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text("original")
    configure_workspace_data_dir(data)
    try:
        service = CompanionPlantingService()
        (data / "custom_companion_rules.json").symlink_to(outside)
        from open_garden_planner.services.companion_planting_service import CompanionRelationship

        with pytest.raises(OSError, match="symbolic link"):
            service.add_custom_rule(CompanionRelationship(plant_a="tomato", plant_b="basil", type="good", reason="test"))
        assert service.get_custom_rules() == []
        assert outside.read_text() == "original"
    finally:
        configure_workspace_data_dir(None)


def test_project_save_refuses_workspace_replaced_before_path_resolution(tmp_path):
    from open_garden_planner.core.project import ProjectManager
    from open_garden_planner.services.plant_library import configure_workspace_data_dir

    workspace = tmp_path / "workspace"
    data = workspace / "app-data"
    data.mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    (external / "garden.ogp").write_text("external plan")
    configure_workspace_data_dir(data)
    try:
        workspace.rename(tmp_path / "original-workspace")
        workspace.symlink_to(external, target_is_directory=True)
        with pytest.raises(OSError, match="replaced"):
            ProjectManager().save(object(), workspace / "garden.ogp")
        assert (external / "garden.ogp").read_text() == "external plan"
    finally:
        configure_workspace_data_dir(None)


def test_companion_rules_replace_hard_link_without_changing_external_file(tmp_path):
    import os

    from open_garden_planner.services.companion_planting_service import (
        CompanionPlantingService,
        CompanionRelationship,
    )
    from open_garden_planner.services.plant_library import configure_workspace_data_dir

    data = tmp_path / "workspace" / "app-data"
    data.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text('{"rules": []}')
    os.link(outside, data / "custom_companion_rules.json")
    configure_workspace_data_dir(data)
    try:
        service = CompanionPlantingService()
        service.add_custom_rule(CompanionRelationship(plant_a="tomato", plant_b="basil", type="good", reason="test"))
        assert len(service.get_custom_rules()) == 1
        assert outside.read_text() == '{"rules": []}'
    finally:
        configure_workspace_data_dir(None)
