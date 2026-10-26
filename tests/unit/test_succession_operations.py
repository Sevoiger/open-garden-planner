"""Pure validation and snapshots for succession-plan writes."""
from __future__ import annotations

import pytest

from open_garden_planner.models.succession import SuccessionEntry, SuccessionPlan
from open_garden_planner.services.succession_operations import (
    SuccessionPlanError,
    build_succession_plan_for_agent,
    prepare_succession_change,
)


def _entry(
    entry_id: str,
    species_key: str,
    start: str,
    end: str,
    **extra: object,
) -> dict:
    return {
        "id": entry_id,
        "species_key": species_key,
        "common_name": species_key.title(),
        "start_date": start,
        "end_date": end,
        **extra,
    }


def test_agent_builder_refuses_overlap_but_gui_policy_can_keep_it() -> None:
    entries = [
        _entry("a", "lettuce", "2026-03-01", "2026-04-10"),
        _entry("b", "beans", "2026-04-01", "2026-05-01"),
    ]

    with pytest.raises(SuccessionPlanError, match="overlap"):
        build_succession_plan_for_agent(entries, "bed-1", 2026)

    plan = build_succession_plan_for_agent(
        entries, "bed-1", 2026, allow_overlap=True
    )
    assert plan is not None
    assert len(plan.entries) == 2


def test_prepare_preserves_extensions_and_original_entry_order() -> None:
    before = {
        "bed_id": "bed-1",
        "year": 2026,
        "future_plan_field": {"keep": True},
        "entries": [
            _entry(
                "late",
                "beans",
                "2026-06-01",
                "2026-08-01",
                future_entry_field={"count": 3},
            ),
            _entry("early", "lettuce", "2026-03-01", "2026-04-01"),
        ],
    }
    replacement = SuccessionPlan(
        bed_id="bed-1",
        year=2027,
        entries=[
            SuccessionEntry(
                id="late",
                species_key="beans",
                common_name="Beans",
                start_date="2027-06-01",
                end_date="2027-08-01",
                extra_fields={"new_entry_field": 9},
            ),
            SuccessionEntry(
                id="early",
                species_key="lettuce",
                common_name="Lettuce",
                start_date="2027-03-01",
                end_date="2027-04-01",
            ),
        ],
    )

    change = prepare_succession_change(
        {"bed-1": before}, bed_id="bed-1", new_plan=replacement
    )

    assert change.before == before
    assert change.after is not None
    assert change.after["future_plan_field"] == {"keep": True}
    assert [entry["id"] for entry in change.after["entries"]] == ["late", "early"]
    assert change.after["entries"][0]["future_entry_field"] == {"count": 3}
    assert change.after["entries"][0]["new_entry_field"] == 9


def test_prepare_delete_and_noop_are_explicit() -> None:
    plan = SuccessionPlan(
        bed_id="bed-1",
        year=2026,
        entries=[
            SuccessionEntry(
                id="a",
                species_key="lettuce",
                common_name="Lettuce",
                start_date="2026-03-01",
                end_date="2026-04-01",
            )
        ],
    )
    unchanged = prepare_succession_change(
        {"bed-1": plan.to_dict()}, bed_id="bed-1", new_plan=plan
    )
    deleted = prepare_succession_change(
        {"bed-1": plan.to_dict()}, bed_id="bed-1", new_plan=None
    )

    assert unchanged.is_noop
    assert not deleted.is_noop
    assert deleted.after is None


def test_invalid_dates_and_bed_mismatch_are_refused() -> None:
    with pytest.raises(SuccessionPlanError, match="ends"):
        build_succession_plan_for_agent(
            [_entry("a", "lettuce", "2026-05-01", "2026-04-01")],
            "bed-1",
            2026,
        )
    with pytest.raises(SuccessionPlanError, match="does not match"):
        prepare_succession_change(
            {},
            bed_id="bed-1",
            new_plan=SuccessionPlan(bed_id="bed-2", year=2026),
        )
