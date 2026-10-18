"""Perennial tracking contracts: uncertain dates and historical observations."""

import math
from copy import deepcopy

import pytest

from open_garden_planner.models.garden_memory import (
    optional_number,
    period,
    period_bounds,
    validate_card,
    validate_event,
    validate_memory,
)


@pytest.mark.parametrize(("text", "precision", "value", "bounds"), [
    ("", "unknown", "", None),
    ("2023", "year", "2023", ("2023-01-01", "2023-12-31")),
    ("2024-02", "month", "2024-02", ("2024-02-01", "2024-02-29")),
    ("2024-02-29", "day", "2024-02-29", ("2024-02-29", "2024-02-29")),
    ("Automne 2023", "season", "2023-autumn", ("2023-09-01", "2023-11-30")),
    ("hiver 2023", "season", "2023-winter", ("2023-12-01", "2024-02-29")),
])
def test_dates_keep_precision_and_original_label(text, precision, value, bounds):
    p = period(text)
    assert p == {"precision": precision, "value": value, "label": text}
    assert period_bounds(p) == bounds
    assert period(p) == p


@pytest.mark.parametrize("value", ["2023-02-29", "2023-00", "pas une date", "2023-13", "20231310"])
def test_invalid_dates_are_not_silently_coerced(value):
    with pytest.raises(ValueError):
        period(value)


@pytest.mark.parametrize("value", [-1, math.inf, math.nan, "NaN", True, "-0.1"])
def test_invalid_numeric_values_are_rejected(value):
    with pytest.raises(ValueError):
        optional_number(value, "quantity")


def test_zero_and_missing_are_distinct():
    assert optional_number(None, "quantity") is None
    assert optional_number(0, "quantity") == 0


def event():
    return validate_event({"type": "amendment", "status": "done", "description": "Fictitious mulch",
                           "period": "Automne 2023", "targets": [{"id": "bed1", "name": "Old bed name"}],
                           "area_m2": 5})


def test_stored_history_retains_identity_and_snapshot_without_aliases():
    e = event()
    raw = {"version": 1, "events": {e["id"]: e}, "requests": {}}
    restored = validate_memory(raw)
    assert restored == raw
    raw["events"][e["id"]]["targets"][0]["name"] = "Renamed"
    assert restored["events"][e["id"]]["targets"][0]["name"] == "Old bed name"
    assert restored["events"][e["id"]]["area_m2"] == 5
    assert "quantity" not in restored["events"][e["id"]]


def test_incomplete_future_or_malformed_stores_are_refused():
    e = event()
    for store in [{"version": 2}, {"version": 1, "events": {"wrong": e}},
                  {"version": 1, "events": {e["id"]: {**e, "unexpected": 1}}}]:
        with pytest.raises(ValueError):
            validate_memory(store)


def test_dormancy_is_distinct_from_death_and_quantity_is_optional():
    dormant = validate_card({"kind": "group", "state": "dormant", "period": None})
    assert dormant["state"] != "confirmed_dead"
    assert "quantity" not in dormant
    assert dormant["period"]["precision"] == "unknown"


@pytest.mark.parametrize("path", ["../private.jpg", "/tmp/photo.jpg", "C:\\photo.jpg", "a/../../b.jpg"])
def test_nonportable_media_references_are_refused(path):
    e = deepcopy(event())
    e["media"] = [path]
    with pytest.raises(ValueError):
        validate_event(e)


def test_french_decimal_input_and_blank_spaces():
    assert optional_number(" 4,5 ", "area_m2") == 4.5
    assert optional_number("  ", "quantity") is None
    assert period("  ")["precision"] == "unknown"
    assert period("Herbst 2023")["value"] == "2023-autumn"


def test_query_bed_type_names_match_the_engine(qtbot):
    from open_garden_planner.core.object_types import ObjectType, is_bed_type
    from open_garden_planner.services.garden_queries import BED_TYPES

    assert {member.name for member in ObjectType if is_bed_type(member)} == BED_TYPES
