"""Botanical traits share the species store and preserve their source."""
from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services.botanical_profile import trait, update_traits
from open_garden_planner.services.plant_library import PlantLibrary


def test_manual_trait_preserves_source_and_future_fields_on_reopen(tmp_path):
    plant = PlantSpeciesData("Rosa", "Rose", raw_data={
        "local_sheet": {"characteristics": {"Cultivar": "Source cultivar"}},
        "botanical_profile": {"future": {"keep": True}},
    })
    assert trait(plant, "cultivar") == "Source cultivar"
    changed = update_traits(plant, {"cultivar": "Correction", "synonyms": "Alias"})
    library = PlantLibrary(tmp_path / "library.json")
    identity = library.add_plant(changed)
    reopened = PlantLibrary(tmp_path / "library.json")
    saved = reopened.get_plant(identity)
    assert trait(saved, "cultivar") == "Correction"
    assert saved.raw_data["local_sheet"] == plant.raw_data["local_sheet"]
    assert saved.raw_data["botanical_profile"]["future"] == {"keep": True}
    assert reopened.search_plants("Alias")
    assert trait(plant, "cultivar") == "Source cultivar"
