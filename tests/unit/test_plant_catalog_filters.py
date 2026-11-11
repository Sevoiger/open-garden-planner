"""Catalogue filters derive personal facts from explicit specimen links only."""
from copy import deepcopy

import pytest

from open_garden_planner.models.plant_data import PlantSpeciesData, SunRequirement
from open_garden_planner.services.plant_catalog_filters import (
    catalogue_relations,
    filter_catalogue,
    specimen_summaries,
    update_catalogue_relations,
)


def plant(name, **raw):
    return PlantSpeciesData("Rosa " + name, name, max_height_cm=100, max_spread_cm=60,
                             sun_requirement=SunRequirement.FULL_SUN, raw_data=raw)


def snapshot():
    return {"objects": [
        {"item_id": "bed", "object_type": "GARDEN_BED", "name": "Massif nord"},
        {"item_id": "placed", "object_type": "SHRUB", "name": "Rose gauche", "metadata": {
            "plant_instance": {"library_plant_id": "rose", "cultivation_mode": "ground", "relation": "owned",
                               "media_assets": [{"id": "personal", "kind": "personal"}]},
            "garden_tracking": {"state": "dormant", "zone_id": "bed"},
        }},
        {"item_id": "near", "object_type": "SHRUB", "name": "Rosa Rose", "metadata": {"plant_instance": {}}},
    ], "plant_specimens": {"pot": {"item_id": "pot", "object_type": "PERENNIAL", "name": "Rose pot", "metadata": {
        "plant_instance": {"library_plant_id": "rose", "cultivation_mode": "pot", "relation": "owned",
                           "location": "Terrasse", "media_assets": [{"kind": "reference"}]},
        "garden_tracking": {"state": "planned"},
    }}}}


def test_wanted_and_owned_are_not_exclusive_and_import_defaults_to_one_owned():
    entries = [("rose", plant("Rose", catalog_relations=["wanted"])), ("imported", plant("Imported"))]
    before = deepcopy(entries[0][1].to_dict())
    summaries = specimen_summaries(entries, snapshot())
    assert summaries["rose"]["relations"] == {"wanted", "owned", "present"}
    assert summaries["rose"]["count"] == 2
    assert summaries["imported"]["relations"] == {"owned"}
    assert summaries["imported"]["count"] == 1
    assert entries[0][1].to_dict() == before
    for relation in ("wanted", "present"):
        assert [identity for identity, _ in filter_catalogue(entries, summaries, relation=relation)] == ["rose"]


def test_mode_bed_month_and_personal_photo_filters():
    entries = [("rose", plant("Rose", botanical_profile={"category": "Arbuste"},
                               botanical_calendar={"flowering": {"months": [12, 1, 2]}, "planting": {"months": [3]}})),
               ("manual", PlantSpeciesData("", "Nom provisoire"))]
    summaries = specimen_summaries(entries, snapshot())
    assert len(filter_catalogue(entries, summaries, mode="pot", location="terrasse", category="Arbuste")) == 1
    assert len(filter_catalogue(entries, summaries, mode="ground", location="massif nord")) == 1
    assert [identity for identity, _ in filter_catalogue(entries, summaries, month=1, activity="flowering")] == ["rose"]
    assert filter_catalogue(entries, summaries, month=1, activity="planting") == []
    assert [identity for identity, _ in filter_catalogue(entries, summaries, incomplete=True)] == ["manual"]
    assert [identity for identity, _ in filter_catalogue(entries, summaries, without_personal_photo=True)] == ["manual"]
    assert [identity for identity, _ in filter_catalogue(entries, summaries, sort="count")] == ["rose", "manual"]


def test_relation_changes_preserve_unknown_flags_and_payload():
    source = plant("Rose", catalog_relations=["wanted", "future-relation"], future={"keep": True})
    saved = update_catalogue_relations(source, {"owned", "wanted"})
    assert catalogue_relations(saved) == {"owned", "wanted", "future-relation"}
    assert saved.raw_data["future"] == {"keep": True}
    assert source.raw_data["catalog_relations"] == ["wanted", "future-relation"]
    with pytest.raises(ValueError, match="unrecognized"):
        update_catalogue_relations(plant("Malformed", catalog_relations={"future": True}), {"wanted"})
