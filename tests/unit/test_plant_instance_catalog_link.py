"""An instance may link to one exact personal-library species record."""

import pytest

from open_garden_planner.models.plant_data import PlantInstance, PlantSpeciesData
from open_garden_planner.services.plant_library import PlantLibrary


def test_plant_instance_library_link_roundtrips_without_replacing_snapshot():
    snapshot = PlantSpeciesData("Rosa ancien", "Rosier observé")
    instance = PlantInstance(
        variety_cultivar="Cultivar observé",
        notes="Note historique",
        size_measurements=[{"id": "measure-1", "date": "2025-05-01", "height_cm": 42}],
        custom_fields={"Pépinière": "Jardin local"},
        species_data=snapshot,
        library_plant_id="rose-catalog-record",
    )

    restored = PlantInstance.from_dict(instance.to_dict())

    assert restored == instance
    assert restored.species_data == snapshot
    assert restored.library_plant_id == "rose-catalog-record"
    assert restored.size_measurements == instance.size_measurements


def test_legacy_instance_without_catalog_link_remains_unlinked():
    instance = PlantInstance.from_dict({
        "variety_cultivar": "Cultivar ancien",
        "notes": "Conservée",
        "size_measurements": [{"id": "measure-1", "date": "2024-04-01", "height_cm": 30}],
        "future_instance_field": {"kept": True},
    })

    assert instance.library_plant_id is None
    assert instance.notes == "Conservée"
    assert instance.size_measurements[0]["id"] == "measure-1"


def test_library_resolution_uses_only_the_exact_link_id(tmp_path):
    library = PlantLibrary(tmp_path / "custom_plants.json")
    record = PlantSpeciesData(
        "Rosa catalog", "Rose du catalogue", source_id="rose-record", data_source="custom"
    )
    library.add_plant(record)

    linked = PlantInstance(
        species_data=PlantSpeciesData("Rosa snapshot", "Rose photographiée"),
        library_plant_id="rose-record",
    )
    stale = PlantInstance(
        species_data=PlantSpeciesData("Rosa catalog", "Rose du catalogue"),
        library_plant_id="removed-record",
    )
    unlinked = PlantInstance(species_data=record)

    assert library.get_plant_for_instance(linked) is library.get_plant("rose-record")
    assert library.get_plant_for_instance(linked.to_dict()) is library.get_plant("rose-record")
    assert library.get_plant_for_instance(stale) is None
    assert library.get_plant_for_instance(unlinked) is None
    assert linked.species_data.scientific_name == "Rosa snapshot"


@pytest.mark.parametrize("library_plant_id", ["", 7, False, []])
def test_plant_instance_rejects_malformed_explicit_link(library_plant_id):
    with pytest.raises(ValueError, match="library_plant_id"):
        PlantInstance.from_dict({"library_plant_id": library_plant_id})
