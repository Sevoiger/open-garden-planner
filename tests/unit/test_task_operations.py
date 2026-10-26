"""Contract tests for pure manual-task preparation."""

from __future__ import annotations

import pytest

from open_garden_planner.services.task_operations import prepare_manual_task_change


def test_add_prepares_detached_task_with_canonical_fields() -> None:
    tasks: dict[str, dict] = {}

    change = prepare_manual_task_change(
        tasks,
        operation="add",
        date="2026-04-25",
        title="  Mulch tomatoes  ",
        notes="  use straw  ",
        bed_id="bed-1",
        valid_bed_ids={"bed-1"},
    )

    assert change.operation == "add"
    assert change.task_id
    assert change.before is None
    assert change.after == {
        "id": change.task_id,
        "date": "2026-04-25",
        "title": "Mulch tomatoes",
        "notes": "  use straw  ",
        "bed_id": "bed-1",
    }
    assert change.task is not None
    assert change.task.title == "Mulch tomatoes"
    assert tasks == {}


def test_edit_preserves_unknown_fields_and_returns_exact_snapshots() -> None:
    future_value = {"source": "newer-version", "nested": [1, 2]}
    tasks = {
        "t1": {
            "id": "t1",
            "date": "2026-01-02",
            "title": "Old title",
            "future": future_value,
        }
    }

    change = prepare_manual_task_change(
        tasks,
        operation="edit",
        task_id="t1",
        date=None,
        title=" New title ",
        notes=" notes stay exact ",
        bed_id=None,
    )

    assert change.before == tasks["t1"]
    assert change.after == {
        "id": "t1",
        "date": "",
        "title": "New title",
        "notes": " notes stay exact ",
        "future": future_value,
    }
    assert change.after is not tasks["t1"]
    assert change.after["future"] is not future_value
    assert not change.is_noop


def test_edit_identical_is_noop_and_missing_optional_fields_clear() -> None:
    tasks = {
        "t1": {
            "id": "t1",
            "date": "2026-01-02",
            "title": "Water",
            "notes": "",
        }
    }

    same = prepare_manual_task_change(
        tasks,
        operation="edit",
        task_id="t1",
        date="2026-01-02",
        title="Water",
        notes=None,
        bed_id=None,
    )
    cleared = prepare_manual_task_change(
        {"t1": {"id": "t1", "date": "2026-01-02", "title": "Water", "bed_id": "b"}},
        operation="edit",
        task_id="t1",
        date=None,
        title="Water",
        notes=None,
        bed_id=None,
    )

    assert same.is_noop
    assert cleared.after == {"id": "t1", "date": "", "title": "Water"}


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"title": "  "}, "title"),
        ({"title": "Water", "date": "2026-2-05"}, "ISO date"),
        ({"title": "Water", "date": "2026-02-05T00:00:00"}, "ISO date"),
        ({"title": "Water", "bed_id": "unknown", "valid_bed_ids": {"b1"}}, "bed"),
    ],
)
def test_add_refuses_invalid_fields_without_mutating(kwargs: dict, message: str) -> None:
    tasks: dict[str, dict] = {}

    with pytest.raises(ValueError, match=message):
        prepare_manual_task_change(tasks, operation="add", **kwargs)

    assert tasks == {}


def test_edit_and_delete_refuse_unknown_manual_task() -> None:
    with pytest.raises(ValueError, match="No manual task"):
        prepare_manual_task_change({}, operation="edit", task_id="generated")
    with pytest.raises(ValueError, match="No manual task"):
        prepare_manual_task_change({}, operation="delete", task_id="generated")


def test_delete_carries_detached_before_snapshot() -> None:
    tasks = {"t1": {"id": "t1", "title": "Water", "future": {"v": 1}}}

    change = prepare_manual_task_change(tasks, operation="delete", task_id="t1")

    assert change.operation == "delete"
    assert change.after is None
    assert change.before == tasks["t1"]
    assert change.before is not tasks["t1"]
    assert change.before["future"] is not tasks["t1"]["future"]
    assert not change.is_noop
    assert tasks["t1"] == {"id": "t1", "title": "Water", "future": {"v": 1}}
