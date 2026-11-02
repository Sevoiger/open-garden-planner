"""Unplaced plants share .ogp persistence and existing measurement/event rules."""
from uuid import uuid4

from open_garden_planner.core.project_data import ProjectData
from open_garden_planner.services.garden_operations import prepare_garden_operation
from open_garden_planner.services.plant_specimens import add_specimen


def test_two_unplaced_specimens_share_species_without_creating_shapes():
    original = {"objects": [], "future": {"keep": True}}
    document, first = add_specimen(original, "rose", "Rose pot", cultivation_mode="pot")
    document, second = add_specimen(document, "rose", "Rose terre", cultivation_mode="ground")
    assert first != second
    assert document["objects"] == []
    assert original == {"objects": [], "future": {"keep": True}}
    reopened = ProjectData.from_dict(document).to_dict()
    assert reopened["plant_specimens"] == document["plant_specimens"]
    assert reopened["future"] == {"keep": True}


def test_unplaced_measurements_and_events_use_existing_history():
    document, identity = add_specimen({"objects": []}, "rose", "Rose pot")
    measured, _ = prepare_garden_operation(document, "add_plant_measurement", {
        "date": "2026-04-01", "height_cm": 34,
    }, identity, "measurement-once")
    instance = measured["plant_specimens"][identity]["metadata"]["plant_instance"]
    assert instance["current_height_cm"] == 34
    event = {"type": "observation", "status": "done", "description": "Première pousse",
             "targets": [{"id": identity, "name": "Rose pot"}]}
    observed, result = prepare_garden_operation(measured, "add_event", event, request_key=str(uuid4()))
    assert len(observed["garden_memory"]["events"]) == 1
    assert result["replayed"] is False
    assert observed["objects"] == []
