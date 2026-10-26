"""Qt-free soil ratings, mismatch and record preparation contracts."""
from __future__ import annotations

import datetime
import subprocess
import sys
from pathlib import Path

import pytest

from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.models.soil_test import SoilTestHistory, SoilTestRecord
from open_garden_planner.services.soil_assessment import (
    MISMATCH_N_HIGH_DEMAND,
    MISMATCH_PH_LOW,
    HealthLevel,
    SoilTestError,
    assess_mismatches,
    build_soil_record_for_agent,
    calculate_amendments,
    health_level,
    prepare_soil_test_change,
)


def test_health_level_keeps_unknown_and_kit_scale_semantics() -> None:
    assert health_level(None, "overall") is HealthLevel.UNKNOWN
    assert health_level(SoilTestRecord(ph=6.0), "ph") is HealthLevel.GOOD
    assert health_level(SoilTestRecord(ph=5.6), "ph") is HealthLevel.FAIR
    assert health_level(SoilTestRecord(n_level=2), "n") is HealthLevel.GOOD
    assert health_level(SoilTestRecord(n_level=4), "n") is HealthLevel.FAIR
    assert health_level(SoilTestRecord(k_level=0), "k") is HealthLevel.POOR
    assert health_level(SoilTestRecord(ca_level=0), "ca") is HealthLevel.UNKNOWN


def test_mismatch_assessment_returns_codes_and_arguments_without_qt_text() -> None:
    record = SoilTestRecord(date="2026-09-01", ph=5.7, n_level=1)
    spec = PlantSpeciesData(
        common_name="Tomato",
        scientific_name="Solanum lycopersicum",
        ph_min=5.8,
        ph_max=7.0,
        n_demand="high",
    )

    mismatches = assess_mismatches(record, [spec])

    assert len(mismatches) == 1
    returned_spec, reasons = mismatches[0]
    assert returned_spec is spec
    assert [reason.code for reason in reasons] == [
        MISMATCH_PH_LOW,
        MISMATCH_N_HIGH_DEMAND,
    ]
    assert reasons[0].translation_key == "{name} needs pH ≥{min:.1f}, current {cur:.1f}"
    assert reasons[0].argument_dict() == {"name": "Tomato", "min": 5.8, "cur": 5.7}


def test_amendment_calculation_is_a_read_only_recommendation() -> None:
    record = SoilTestRecord(
        date="2026-09-01",
        ph=5.2,
        n_level=0,
        p_level=0,
        k_level=1,
        n_ppm=180.0,
        mode="lab",
    )
    before = record.to_dict()

    recommendations = calculate_amendments(record, bed_area_m2=4.0)

    assert recommendations
    assert any(recommendation.target_kind == "ph" for recommendation in recommendations)
    assert record.to_dict() == before
    assert all(recommendation.quantity_g >= 0 for recommendation in recommendations)


def test_agent_record_preparation_pins_date_and_keeps_kit_and_ppm_separate() -> None:
    today = datetime.date(2026, 10, 26)

    record = build_soil_record_for_agent(
        n_level=3,
        test_date="2026-10-25",
        today=today,
        record_id="fixed-id",
    )

    assert record.id == "fixed-id"
    assert record.date == "2026-10-25"
    assert record.n_level == 3
    assert record.n_ppm is None
    assert record.mode == "kit"
    with pytest.raises(SoilTestError, match="Rapitest kit scale"):
        build_soil_record_for_agent(k_level=0, today=today)


def test_soil_record_and_history_round_trip_unknown_fields_detached() -> None:
    raw = {
        "target_id": "bed-1",
        "future_history": {"revision": 8},
        "records": [
            {
                "id": "r1",
                "date": "2026-10-01",
                "ph": 6.5,
                "mode": "kit",
                "future_record": {"lab_method": "newer-app"},
            }
        ],
    }

    history = SoilTestHistory.from_dict(raw)
    round_trip = history.to_dict()
    round_trip["future_history"]["revision"] = 0
    round_trip["records"][0]["future_record"]["lab_method"] = "changed"

    assert raw["future_history"] == {"revision": 8}
    assert raw["records"][0]["future_record"] == {"lab_method": "newer-app"}
    assert SoilTestHistory.from_dict(history.to_dict()) == history


def test_edit_preparation_preserves_unknown_fields_and_returns_detached_snapshots() -> None:
    raw_history = {
        "target_id": "bed-1",
        "future_history": {"revision": 8},
        "records": [
            {
                "id": "r1",
                "date": "2026-10-01",
                "ph": 6.5,
                "future_record": {"lab_method": "newer-app"},
            },
            {"id": "r2", "date": "2026-09-01", "n_level": 2},
        ],
    }
    soil_tests = {"bed-1": raw_history}
    edited = SoilTestRecord.from_dict(raw_history["records"][0])
    edited.ph = 6.8

    change = prepare_soil_test_change(
        soil_tests,
        operation="edit",
        target_id="bed-1",
        record=edited,
    )

    assert change.record_id == "r1"
    assert change.before == raw_history
    assert change.after["future_history"] == {"revision": 8}
    assert change.after["records"][0]["future_record"] == {"lab_method": "newer-app"}
    assert change.after["records"][0]["ph"] == 6.8
    assert change.after is not raw_history
    assert change.after["future_history"] is not raw_history["future_history"]
    assert not change.is_noop
    assert soil_tests["bed-1"] == raw_history


def test_add_preparation_creates_one_history_without_mutating_source() -> None:
    soil_tests: dict[str, dict] = {}
    record = SoilTestRecord(id="r1", date="2026-10-01", n_level=2)

    change = prepare_soil_test_change(
        soil_tests,
        operation="add",
        target_id="global",
        record=record,
    )

    assert change.operation == "add"
    assert change.target_id == "global"
    assert change.record_id == "r1"
    assert change.before is None
    assert change.after == {
        "target_id": "global",
        "records": [
            {"id": "r1", "date": "2026-10-01", "n_level": 2}
        ],
    }
    assert not change.is_noop
    assert soil_tests == {}


def test_add_preparation_rejects_duplicate_record_identity() -> None:
    soil_tests = {
        "bed-1": {
            "target_id": "bed-1",
            "records": [{"id": "r1", "date": "2026-10-01", "n_level": 2}],
        }
    }

    with pytest.raises(ValueError, match="already exists"):
        prepare_soil_test_change(
            soil_tests,
            operation="add",
            target_id="bed-1",
            record=SoilTestRecord(id="r1", date="2026-10-02", n_level=3),
        )


def test_unchanged_edit_is_noop_and_delete_keeps_history_extensions() -> None:
    raw_history = {
        "target_id": "bed-1",
        "future_history": ["opaque"],
        "records": [
            {
                "id": "r1",
                "date": "2026-10-01",
                "mode": "kit",
                "ph": 6.5,
                "future_record": {"v": 1},
            }
        ],
    }
    same_record = SoilTestRecord.from_dict(raw_history["records"][0])
    same = prepare_soil_test_change(
        {"bed-1": raw_history},
        operation="edit",
        target_id="bed-1",
        record=same_record,
    )
    deleted = prepare_soil_test_change(
        {"bed-1": raw_history},
        operation="delete",
        target_id="bed-1",
        record_id="r1",
    )

    assert same.is_noop
    assert deleted.after["future_history"] == ["opaque"]
    assert deleted.after["records"] == []
    assert not deleted.is_noop


def test_edit_or_delete_missing_record_is_refused_before_any_mutation() -> None:
    soil_tests = {
        "bed-1": {"target_id": "bed-1", "records": [{"id": "r1", "date": "2026-01-01"}]}
    }
    before = {"bed-1": {"target_id": "bed-1", "records": [{"id": "r1", "date": "2026-01-01"}]}}

    with pytest.raises(ValueError, match="No soil-test record"):
        prepare_soil_test_change(
            soil_tests,
            operation="delete",
            target_id="bed-1",
            record_id="generated",
        )

    assert soil_tests == before


def test_assessment_and_soil_models_import_with_qt_blocked() -> None:
    script = """
import importlib.abc
import sys

class QtBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("PyQt6", "PySide")):
            raise RuntimeError("Qt import blocked: " + fullname)

sys.meta_path.insert(0, QtBlocker())
from open_garden_planner.models.soil_test import SoilTestHistory, SoilTestRecord
from open_garden_planner.services.soil_assessment import calculate_amendments, health_level

assert health_level(SoilTestRecord(), "overall").value == "unknown"
assert SoilTestHistory(target_id="bed-1").records == []
"""
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
