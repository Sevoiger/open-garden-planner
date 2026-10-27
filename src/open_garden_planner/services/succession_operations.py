"""Pure validation and preparation for succession-plan changes."""
from __future__ import annotations

import datetime
import uuid
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from open_garden_planner.models.succession import SuccessionEntry, SuccessionPlan

_ENTRY_FIELDS = frozenset(
    {
        "id", "species_key", "common_name", "scientific_name",
        "start_date", "end_date", "notes",
    }
)


class SuccessionPlanError(ValueError):
    """A refusal from :func:`build_succession_plan_for_agent`."""


@dataclass(frozen=True)
class SuccessionChange:
    """Detached before/after snapshots for one bed's succession plan."""

    bed_id: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None

    @property
    def is_noop(self) -> bool:
        """Whether the prepared replacement is semantically unchanged."""
        if self.before == self.after:
            return True
        if self.before is None or self.after is None:
            return False
        return SuccessionPlan.from_dict(deepcopy(self.before)) == SuccessionPlan.from_dict(
            deepcopy(self.after)
        )

    @property
    def plan(self) -> SuccessionPlan | None:
        """Return the detached replacement plan, if this change keeps one."""
        if self.after is None:
            return None
        return SuccessionPlan.from_dict(deepcopy(self.after))


def _validated_entries(
    entries: list[dict] | None,
    *,
    known_species_keys: set[str] | None,
    allow_overlap: bool,
    require_species_key: bool = True,
    sort_entries: bool = True,
) -> list[SuccessionEntry]:
    if not entries:
        return []

    parsed: list[tuple[int, datetime.date, datetime.date, str, dict]] = []
    for index, raw in enumerate(entries):
        if not isinstance(raw, dict):
            raise SuccessionPlanError(f"Entry {index} is not an object")

        raw_key = str(raw.get("species_key", "") or "").strip()
        if not raw_key and require_species_key:
            raise SuccessionPlanError(
                f"Entry {index} has no species_key; succession slots must name a "
                "species so rotation and companion checks can be made"
            )
        species_key = raw_key.lower()
        if known_species_keys is not None and species_key not in known_species_keys:
            raise SuccessionPlanError(
                f"Unknown species_key {raw_key!r}. A species key is the canonical "
                "lowercased form (ADR-016), which for a bundled plant is its "
                "scientific name - 'allium sativum', not 'Garlic'. Read the "
                "garden://species resource for the names this plan knows about."
            )

        start_raw = str(raw.get("start_date", "") or "")
        try:
            start = datetime.date.fromisoformat(start_raw)
        except (TypeError, ValueError):
            raise SuccessionPlanError(
                f"Entry {index} ({species_key}) has a missing or non-ISO "
                f"start_date: {raw.get('start_date')!r}. Use YYYY-MM-DD."
            ) from None
        end_raw = str(raw.get("end_date", "") or "")
        try:
            end = datetime.date.fromisoformat(end_raw)
        except (TypeError, ValueError):
            raise SuccessionPlanError(
                f"Entry {index} ({species_key}) has a missing or non-ISO "
                f"end_date: {raw.get('end_date')!r}. Use YYYY-MM-DD."
            ) from None
        if end < start:
            raise SuccessionPlanError(
                f"Entry {index} ({species_key}) ends {end} before it starts {start}."
            )
        parsed.append((index, start, end, species_key, raw))

    chronological = sorted(parsed, key=lambda row: (row[1], row[2], row[0]))
    if not allow_overlap:
        for (
            (previous_index, _previous_start, previous_end, _previous_key, previous_raw),
            (index, start, _end, _key, _raw),
        ) in zip(chronological, chronological[1:], strict=False):
            if start <= previous_end:
                previous_name = (
                    previous_raw.get("common_name")
                    or previous_raw.get("species_key")
                )
                raise SuccessionPlanError(
                    f"Entries {previous_index} and {index} overlap: "
                    f"{previous_name!r} runs to {previous_end} but the next "
                    f"entry starts {start}. One bed cannot grow two crops on "
                    "the same days."
                )

    output_entries = chronological if sort_entries else sorted(parsed, key=lambda row: row[0])
    return [
        SuccessionEntry(
            id=str(raw.get("id") or uuid.uuid4()),
            species_key=canonical_key,
            common_name=str(raw.get("common_name", "") or "").strip(),
            scientific_name=str(raw.get("scientific_name", "") or "").strip(),
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            notes=str(raw.get("notes", "") or ""),
            extra_fields=deepcopy(
                {key: value for key, value in raw.items() if key not in _ENTRY_FIELDS}
            ),
        )
        for _index, start, end, canonical_key, raw in output_entries
    ]


def build_succession_plan_for_agent(
    entries: list[dict] | None,
    bed_id: str,
    year: int,
    *,
    known_species_keys: set[str] | None = None,
    allow_overlap: bool = False,
) -> SuccessionPlan | None:
    """Validate agent entries into a plan; an empty list deletes the plan.

    API writes default to refusing overlapping date ranges. The GUI may request
    ``allow_overlap=True`` to preserve its existing warning-and-display flow;
    both paths use the same date, identity and field validation below.
    """
    if not entries:
        return None
    return SuccessionPlan(
        bed_id=bed_id,
        year=year,
        entries=_validated_entries(
            entries,
            known_species_keys=known_species_keys,
            allow_overlap=allow_overlap,
        ),
    )


def prepare_succession_change(
    succession_plans: Mapping[str, Mapping[str, Any]],
    *,
    bed_id: str,
    new_plan: SuccessionPlan | None,
    allow_overlap: bool = True,
    require_species_key: bool = False,
) -> SuccessionChange:
    """Validate and snapshot one bed's plan replacement or deletion.

    Unknown plan and entry fields in the existing record are merged into the
    replacement when an entry keeps its id. ``allow_overlap`` is explicit so
    UI and API callers can preserve their respective established policies.
    The edit command keeps accepting historical model entries without a
    canonical key; the dialog still requires a plant name, while API writes use
    :func:`build_succession_plan_for_agent`'s stricter species-key contract.
    """
    if not isinstance(bed_id, str) or not bed_id:
        raise SuccessionPlanError("bed_id is required.")

    raw_before = succession_plans.get(bed_id)
    before = deepcopy(dict(raw_before)) if raw_before is not None else None
    if new_plan is None:
        return SuccessionChange(bed_id=bed_id, before=before, after=None)
    if new_plan.bed_id != bed_id:
        raise SuccessionPlanError(
            f"Plan bed_id {new_plan.bed_id!r} does not match target {bed_id!r}."
        )

    existing = SuccessionPlan.from_dict(deepcopy(before)) if before is not None else None
    old_entries = {entry.id: entry for entry in existing.entries} if existing else {}
    validated_entries = _validated_entries(
        [entry.to_dict() for entry in new_plan.entries],
        known_species_keys=None,
        allow_overlap=allow_overlap,
        require_species_key=require_species_key,
        sort_entries=False,
    )
    for entry in validated_entries:
        previous = old_entries.get(entry.id)
        if previous is not None:
            entry.extra_fields = {
                **deepcopy(previous.extra_fields),
                **deepcopy(entry.extra_fields),
            }

    plan = SuccessionPlan(
        bed_id=bed_id,
        year=new_plan.year,
        entries=validated_entries,
        extra_fields={
            **(deepcopy(existing.extra_fields) if existing is not None else {}),
            **deepcopy(new_plan.extra_fields),
        },
    )
    return SuccessionChange(
        bed_id=bed_id,
        before=before,
        after=plan.to_dict(),
    )


__all__ = [
    "SuccessionChange",
    "SuccessionPlanError",
    "build_succession_plan_for_agent",
    "prepare_succession_change",
]
