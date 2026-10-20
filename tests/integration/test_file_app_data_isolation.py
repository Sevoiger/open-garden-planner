"""Every test owns its file-backed libraries, including pre-imported path aliases."""

import pytest

from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.models.plant_lists import get_plant_list_store
from open_garden_planner.services.companion_planting_service import (
    get_app_data_dir as companion_path,
)
from open_garden_planner.services.guild_library import get_app_data_dir as guild_path
from open_garden_planner.services.plant_library import get_app_data_dir, get_plant_library
from open_garden_planner.services.weather_service import get_app_data_dir as weather_path


@pytest.mark.parametrize("label", ["First test", "Second test"])
def test_fresh_libraries_and_captured_aliases_are_confined(tmp_path, label):
    app_data = get_app_data_dir()
    assert app_data.is_relative_to(tmp_path)
    assert all(resolve() == app_data for resolve in (companion_path, guild_path, weather_path))
    library = get_plant_library()
    assert library.get_all_plants() == []
    assert len(get_plant_list_store().all_lists()) == 1  # only Favorites
    library.add_plant(PlantSpeciesData("Testus", label))
    get_plant_list_store().create_list(label)
    assert (app_data / "custom_plants.json").is_file()
    assert (app_data / "plant_lists.json").is_file()
    assert library._library_path.parent == app_data
    assert get_plant_list_store().path.parent == app_data
