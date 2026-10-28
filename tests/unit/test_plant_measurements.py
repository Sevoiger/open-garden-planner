"""Measurements retain evidence and project independently of drawing dimensions."""
from copy import deepcopy
from datetime import date

import pytest

from open_garden_planner.core.growth_model import grown_height_cm, grown_spread_cm
from open_garden_planner.services.garden_operations import prepare_garden_operation
from open_garden_planner.services.plant_measurements import measurement_view, update_measurements
from tests.unit.test_garden_operations import document


def test_partial_backdated_measurements_correction_and_deletion_preserve_legacy():
    original = {"plant_instance": {"current_height_cm": 30, "current_diameter_cm": 40,
                                   "future": {"kept": True}}}
    metadata, newest = update_measurements(original, {"date": "2026-10-28", "height_cm": 120})
    metadata, earlier = update_measurements(metadata, {"date": "2025-04-01", "height_cm": 60, "spread_cm": 50})
    view = measurement_view(metadata)
    assert view["current_size"]["height_cm"] == {"value": 120, "date": "2026-10-28", "measurement_id": newest}
    assert view["current_size"]["spread_cm"]["date"] == "2025-04-01"
    metadata, _ = update_measurements(metadata, {"date": "2026-10-28", "height_cm": 110}, newest)
    assert metadata["plant_instance"]["current_height_cm"] == 110
    metadata, _ = update_measurements(metadata, None, newest)
    assert metadata["plant_instance"]["current_height_cm"] == 60
    metadata, _ = update_measurements(metadata, None, earlier)
    assert metadata["plant_instance"]["current_height_cm"] == 30
    assert measurement_view(metadata)["current_size"]["spread_cm"]["value"] == 40
    assert metadata["plant_instance"]["future"] == {"kept": True}
    assert original["plant_instance"]["current_height_cm"] == 30


@pytest.mark.parametrize("fields", [
    {"date": "2026-02-30", "height_cm": 2}, {"date": "2026-10", "height_cm": 2},
    {"date": None, "height_cm": 2}, {"date": "2026-10-28"},
    *({"date": "2026-10-28", "height_cm": value} for value in [0, -1, True, float("nan"), float("inf"), "12"]),
    {"date": "2026-10-28", "height_cm": 2, "typo": 1},
])
def test_invalid_measurement_does_not_modify_source(fields):
    source = {"plant_instance": {"notes": "kept"}}
    before = deepcopy(source)
    with pytest.raises((ValueError, TypeError)):
        update_measurements(source, fields)
    assert source == before


def test_duplicate_day_dimension_requires_edit_but_complementary_dimensions_allowed():
    source, _ = update_measurements({}, {"date": "2026-10-28", "height_cm": 100})
    with pytest.raises(ValueError, match="already exists"):
        update_measurements(source, {"date": "2026-10-28", "height_cm": 110})
    updated, _ = update_measurements(source, {"date": "2026-10-28", "spread_cm": 60})
    assert len(measurement_view(updated)["measurements"]) == 2


def test_dated_growth_observations_anchor_and_interpolate_even_after_pruning():
    metadata = {"plant_instance": {"planting_date": "2000-01-01"}}
    metadata, _ = update_measurements(metadata, {"date": "2025-01-01", "height_cm": 100, "spread_cm": 60})
    metadata, _ = update_measurements(metadata, {"date": "2026-01-01", "height_cm": 80})
    species = {"max_height_cm": 200, "max_spread_cm": 100}
    assert grown_height_cm(species, metadata, date(2026, 1, 1)) == 80
    assert grown_height_cm(species, metadata, date(2025, 1, 1)) == 100
    assert grown_height_cm(species, metadata, date(2025, 7, 2)) == pytest.approx(90, abs=0.1)
    assert grown_height_cm(species, metadata, date(2027, 7, 2)) == pytest.approx(140, abs=0.2)
    assert grown_height_cm({}, metadata, date(2026, 1, 1)) == 80
    assert grown_spread_cm(species, metadata, date(2025, 1, 1)) == 60
    # Before the first observation the display holds its size as an estimate.
    assert grown_height_cm(species, metadata, date(2020, 1, 1)) == 100


def test_replay_conflict_delete_and_target_guards():
    source = document()
    plant, bed = (o["item_id"] for o in source["objects"])
    fields = {"date": "2026-10-28", "height_cm": 120}
    candidate, first = prepare_garden_operation(source, "add_plant_measurement", fields, plant, "measure-1")
    replay, result = prepare_garden_operation(candidate, "add_plant_measurement", fields, plant, "measure-1")
    assert replay == candidate and result["replayed"] and result["measurement_id"] == first["measurement_id"]
    with pytest.raises(ValueError, match="conflict"):
        prepare_garden_operation(candidate, "add_plant_measurement", {**fields, "height_cm": 130}, plant, "measure-1")
    deleted, _ = prepare_garden_operation(candidate, "delete_plant_measurement", {"measurement_id": first["measurement_id"]}, plant)
    replay, result = prepare_garden_operation(deleted, "add_plant_measurement", fields, plant, "measure-1")
    assert result["replayed"] and not result["measurements"] and replay == deleted
    for target in [bed, "bad-id"]:
        with pytest.raises(ValueError):
            prepare_garden_operation(source, "add_plant_measurement", fields, target, "measure-2")
    locked = document(locked=True)
    with pytest.raises(ValueError, match="locked"):
        prepare_garden_operation(locked, "add_plant_measurement", fields, locked["objects"][0]["item_id"], "measure-3")


def test_detached_document_measurement_transaction_and_failed_batch(tmp_path):
    from open_garden_planner.core.document_session import DocumentSession
    from open_garden_planner.core.plan_io import read_plan, write_plan

    path = tmp_path / "measurements.ogp"
    source = document()
    plant = source["objects"][0]["item_id"]
    write_plan(path, source)
    operation = {"operation": "add_plant_measurement", "target_id": plant,
                 "fields": {"date": "2026-10-28", "height_cm": 120}}
    with DocumentSession(path) as host:
        with pytest.raises(ValueError):
            host.dispatch("prepare", {"operations": [operation, {**operation, "fields": {"date": "2026-10-28", "height_cm": -1}}]})
        assert read_plan(path) == source
        prepared = host.dispatch("prepare", {"operations": [operation]})
        applied = host.dispatch("apply", {"preparation_id": prepared["preparation_id"], "request_key": "size-batch"})
        assert applied["persisted"]
        identity = read_plan(path)["objects"][0]["metadata"]["plant_instance"]["size_measurements"][0]["id"]
        host.dispatch("undo", {})
        assert read_plan(path)["objects"][0]["metadata"] == source["objects"][0]["metadata"]
        assert host.dispatch("apply", {"preparation_id": prepared["preparation_id"], "request_key": "size-batch"})["replayed"]
        host.dispatch("redo", {})
        for name, fields in [
            ("edit_plant_measurement", {"measurement_id": identity, "date": "2026-10-27", "height_cm": 115}),
            ("delete_plant_measurement", {"measurement_id": identity}),
        ]:
            prepared = host.dispatch("prepare", {"operations": [{"operation": name, "target_id": plant, "fields": fields}]})
            host.dispatch("apply", {"preparation_id": prepared["preparation_id"], "request_key": name})
        assert not read_plan(path)["objects"][0]["metadata"]["plant_instance"]["size_measurements"]


@pytest.mark.parametrize("instance", [None, [], "legacy", {"size_measurements": None},
                                     {"size_measurements": [{"date": "bad"}]}])
def test_tolerant_render_reads_and_safe_write_refusal(instance):
    metadata = {"plant_instance": instance}
    assert grown_height_cm({"max_height_cm": 200}, metadata, date(2026, 1, 1)) is None
    assert measurement_view(metadata)["measurements"] == []
    before = deepcopy(metadata)
    with pytest.raises(ValueError, match="malformed"):
        update_measurements(metadata, {"date": "2026-10-28", "height_cm": 120})
    assert metadata == before


def test_correction_retains_future_record_fields():
    metadata, identity = update_measurements({}, {"date": "2026-10-28", "height_cm": 120})
    metadata["plant_instance"]["size_measurements"][0]["future"] = {"kept": True}
    corrected, _ = update_measurements(metadata, {"date": "2026-10-28", "height_cm": 125}, identity)
    assert measurement_view(corrected)["measurements"][0]["future"] == {"kept": True}
