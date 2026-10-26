"""Pure snapshots for task-status edits."""
from __future__ import annotations

import datetime

import pytest

from open_garden_planner.services.task_status import (
    effective_status,
    prepare_task_status,
)


def test_status_change_preserves_unknown_extensions_as_detached_data() -> None:
    source = {
        "task-1": {
            "status": "dismissed",
            "vendor": {"flags": ["keep"]},
        }
    }

    change = prepare_task_status(
        source, "task-1", "done", done_date="2026-10-26"
    )

    assert change.before == source["task-1"]
    assert change.after == {
        "vendor": {"flags": ["keep"]},
        "status": "done",
        "done_date": "2026-10-26",
    }
    change.after["vendor"]["flags"].append("mutated")
    assert source["task-1"]["vendor"]["flags"] == ["keep"]


def test_open_clears_known_status_but_keeps_unknown_fields() -> None:
    change = prepare_task_status(
        {
            "task-1": {
                "status": "done",
                "done_date": "2026-10-25",
                "snooze_until": "2026-10-27",
                "extension": 42,
            }
        },
        "task-1",
        "open",
    )

    assert change.after == {"extension": 42}
    assert not change.is_noop


def test_identical_state_is_a_noop_and_effective_status_is_unchanged() -> None:
    change = prepare_task_status(
        {"task-1": {"status": "done", "done_date": "2026-10-26"}},
        "task-1",
        "done",
        done_date="2026-10-26",
    )

    assert change.is_noop
    assert effective_status(change.after, datetime.date(2026, 10, 26)) == "done"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"done_date": "2026-2-03"}, "done_date"),
        ({"snooze_until": "not-a-date"}, "snooze_until"),
    ],
)
def test_invalid_status_dates_are_refused(
    kwargs: dict[str, str], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        prepare_task_status({}, "task-1", "done", **kwargs)


def test_unknown_status_is_refused() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        prepare_task_status({}, "task-1", "archived")  # type: ignore[arg-type]
