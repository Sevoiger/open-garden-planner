"""Prepare portable workspaces before switching any live personal stores."""
from __future__ import annotations

import json
import os
import shutil
import stat
import zipfile
from pathlib import Path
from uuid import uuid4

from open_garden_planner.core.plan_io import read_plan
from open_garden_planner.core.project_data import FILE_VERSION, _is_newer_file_version
from open_garden_planner.services.garden_archive import restore_backup


def _validate_data_tree(data: Path) -> None:
    """Do not follow links or special files into a different personal store."""
    if data.is_symlink() or not data.is_dir() or data.resolve() != data:
        raise ValueError("Imported personal data folder is unavailable or unsafe")

    def on_error(error: OSError) -> None:
        raise error

    for directory, subdirs, filenames in os.walk(data, followlinks=False, onerror=on_error):
        for name in (*subdirs, *filenames):
            path = Path(directory) / name
            mode = path.lstat().st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ValueError("Imported personal data contains a link or special file")


def _validate_weather_config(folder: Path) -> None:
    from open_garden_planner.services.weather_collection import validate_config

    config = folder / "configuration" / "weather-config.json"
    if (config.parent.is_symlink() or config.is_symlink() or not config.is_file()
            or config.resolve().parent != config.parent):
        raise ValueError("Imported weather configuration is unavailable")
    try:
        if config.stat().st_size > 8_000_000:
            raise ValueError("Weather configuration is too large")
        value = json.loads(config.read_text(encoding="utf-8"))
        validate_config(value)
        if (not isinstance(value["location"]["name"], str)
                or not isinstance(value["garden_context"], str)
                or not isinstance(value["meteociel"]["enabled"], bool)):
            raise ValueError("Incomplete weather configuration")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid weather configuration in export") from exc


def validate_workspace(folder: Path, plan: Path) -> tuple[Path, Path]:
    """Read a workspace and chosen plan without mutating either one."""
    folder, plan = Path(folder).resolve(), Path(plan).resolve()
    data = folder / "app-data"
    if (data.is_symlink() or not data.is_dir() or data.resolve().parent != folder
            or not (folder / "backup-manifest.json").is_file()):
        raise ValueError("This folder is not an imported workspace")
    _validate_data_tree(data)
    _validate_weather_config(folder)
    if not plan.is_file() or plan.suffix.lower() != ".ogp":
        raise ValueError("The selected garden plan is unavailable")
    raw_plan = read_plan(plan)
    version = str(raw_plan.get("version", "1.0"))
    if _is_newer_file_version(version, FILE_VERSION):
        raise ValueError("The exported garden requires a newer version of Open Garden Planner")
    # Use the real detached Qt deserialization, but do not publish to a scene,
    # acquire a document lease, or synchronize plants into a personal library.
    from open_garden_planner.core.project import ProjectManager
    from open_garden_planner.core.project_data import ProjectData
    from open_garden_planner.services.smart_symbol_library import (
        SmartSymbolLibrary,
        use_smart_symbol_library,
    )

    try:
        manager = ProjectManager()
        library = SmartSymbolLibrary(user_dir=data / "smart_symbols")
        with use_smart_symbol_library(library):
            prepared = manager._prepare_scene_data(ProjectData.from_dict(raw_plan))
        if manager.last_load_skipped_items_count:
            raise ValueError("The exported garden contains unsupported objects")
        del prepared
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"The exported garden cannot be opened: {exc}") from exc

    from open_garden_planner.core.personal_store import PersonalStoreLoadState
    from open_garden_planner.models.plant_lists import PlantListStore
    from open_garden_planner.models.seed_inventory import SeedInventoryStore
    from open_garden_planner.services.acquisition_inventory import AcquisitionInventory
    from open_garden_planner.services.library_operations import read_library_document
    from open_garden_planner.services.plant_library import PlantLibrary

    read_library_document(data / PlantLibrary.LIBRARY_FILENAME)
    lists = PlantListStore(data / PlantListStore.FILENAME)
    seeds = SeedInventoryStore(data / SeedInventoryStore.FILENAME)
    for store in (lists, seeds):
        if store.load_state == PersonalStoreLoadState.FAILED:
            raise store.load_error or ValueError("Unreadable personal data")
    AcquisitionInventory(data / AcquisitionInventory.FILENAME)
    return plan, data


def prepare_workspace_import(archive: Path, parent: Path) -> tuple[Path, Path]:
    """Extract and validate a complete export without touching active data."""
    with zipfile.ZipFile(archive) as contents:
        if "backup-manifest.json" not in contents.namelist():
            raise ValueError("This is a plan-only archive, not a complete export")
    destination = Path(parent) / ("garden-import-" + uuid4().hex[:12])
    plan = restore_backup(Path(archive), destination)
    try:
        (destination / "app-data").mkdir(exist_ok=True)
        return validate_workspace(destination, plan)
    except Exception:
        # Only this freshly-created extraction is removed; the archive and
        # all previously existing workspaces remain untouched.
        shutil.rmtree(destination)
        raise


def activate_workspace(settings) -> None:
    """Apply a scheduled selection at process start, before singleton stores exist."""
    from open_garden_planner.app.settings import WorkspaceSelectionConflictError
    from open_garden_planner.services.plant_library import configure_workspace_data_dir

    for _attempt in range(3):
        selection = settings.workspace_selection
        if selection is None:
            configure_workspace_data_dir(None)
            return
        data_path = selection["data_dir"]
        if data_path:
            data = Path(data_path)
            plan = Path(selection["plan_path"])
            if (not data.is_absolute() or data.name != "app-data" or data.is_symlink()
                    or not data.is_dir() or data.resolve() != data or
                    not plan.is_absolute() or plan.suffix.lower() != ".ogp" or
                    (selection["pending"] and (not plan.is_file() or
                     not (data.parent / "backup-manifest.json").is_file()))):
                raise ValueError("Imported workspace is unavailable")
            _validate_data_tree(data)
            _validate_weather_config(data.parent)
            configure_workspace_data_dir(data)
        else:
            configure_workspace_data_dir(None)
        if not selection["pending"]:
            return
        try:
            settings.activate_scheduled_workspace(selection)
        except WorkspaceSelectionConflictError:
            continue
        return
    raise WorkspaceSelectionConflictError("Workspace selection keeps changing; restart OGP")
