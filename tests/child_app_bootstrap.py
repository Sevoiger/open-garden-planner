"""Explicit isolation prelude for tests launching OGP in a fresh interpreter."""

from pathlib import Path


def isolated_app_prelude(directory: Path) -> str:
    """Protect libraries and settings before importing or constructing the app.

    A parent-process monkeypatch cannot cross an interpreter boundary. This
    prelude must be included by tests whose child actually loads application data;
    static/import-only probes do not need to construct or inspect any user store.
    """
    return (
        "import atexit, os\n"
        "from pathlib import Path\n"
        "import open_garden_planner.app.settings as _child_settings\n"
        "_child_settings.ORGANIZATION_NAME = 'cofade_test'\n"
        "_child_settings.APPLICATION_NAME = f'OGP isolated child {os.getpid()}'\n"
        "_child_settings._settings_instance = None\n"
        "_child_settings.create_qsettings().clear()\n"
        "atexit.register(lambda: _child_settings.create_qsettings().clear())\n"
        "from open_garden_planner.services import plant_library as _child_plants\n"
        f"_child_data = Path({str(directory)!r}) / 'app-data'\n"
        "_child_plants._default_app_data_dir = lambda: _child_data\n"
        "assert _child_plants.get_app_data_dir() == _child_data\n"
        "assert _child_settings.create_qsettings().applicationName().startswith('OGP isolated child ')\n"
    )
