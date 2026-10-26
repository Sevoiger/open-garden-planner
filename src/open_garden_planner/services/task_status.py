"""Per-task status resolution (US-C2 — task management).

Generated tasks (see :mod:`open_garden_planner.services.task_generator`) are
recomputed every refresh and carry no persistent state of their own. The user's
interactions with a task — snoozing, completing, dismissing — are stored
separately, keyed by ``task_id``, as small dicts of the form::

    {"status": "done", "done_date": "2026-06-20"}
    {"snooze_until": "2026-07-01"}

:func:`effective_status` collapses such a stored dict plus "today" into a single
display state. The module is pure: no Qt, no I/O, defensive ISO parsing.
"""
from __future__ import annotations

import datetime
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

TASK_STATUSES: tuple[str, ...] = ("open", "done", "dismissed")
_STATUS_FIELDS = frozenset({"status", "done_date", "snooze_until"})


@dataclass(frozen=True)
class TaskStatusChange:
    """Detached before/after state for one task-status edit."""

    task_id: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None

    @property
    def is_noop(self) -> bool:
        """Whether applying the prepared change leaves storage untouched."""
        return self.before == self.after


def prepare_task_status(
    task_states: Mapping[str, Mapping[str, Any]],
    task_id: str,
    status: Literal["open", "done", "dismissed"],
    *,
    done_date: str | None = None,
    snooze_until: str | None = None,
) -> TaskStatusChange:
    """Validate one status mutation and return detached snapshots.

    A plain ``open`` removes known status fields while retaining any unknown
    extension fields. A snooze is represented by ``snooze_until`` and remains
    compatible with :func:`effective_status`'s render-time expiry rule.
    """
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id is required.")
    if status not in TASK_STATUSES:
        raise ValueError(
            f"status={status!r} is unsupported. Use one of {', '.join(TASK_STATUSES)}."
        )

    resolved_done_date = _validate_iso_date(done_date, "done_date")
    resolved_snooze_until = _validate_iso_date(snooze_until, "snooze_until")

    raw_before = task_states.get(task_id)
    before = deepcopy(dict(raw_before)) if raw_before is not None else None
    after: dict[str, Any] = {
        key: deepcopy(value)
        for key, value in (before or {}).items()
        if key not in _STATUS_FIELDS
    }
    if status != "open" or resolved_snooze_until:
        after["status"] = status
        if resolved_done_date:
            after["done_date"] = resolved_done_date
        if resolved_snooze_until:
            after["snooze_until"] = resolved_snooze_until

    return TaskStatusChange(task_id, before, after or None)


def _validate_iso_date(value: str | None, field_name: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO YYYY-MM-DD date or null.")
    try:
        parsed = datetime.date.fromisoformat(value)
    except ValueError:
        raise ValueError(
            f"{field_name}={value!r} is not an ISO date. Use YYYY-MM-DD."
        ) from None
    if parsed.isoformat() != value:
        raise ValueError(
            f"{field_name}={value!r} is not an ISO date. Use YYYY-MM-DD."
        )
    return value


def _parse_iso(value: object) -> datetime.date | None:
    """Parse an ISO date string defensively (non-string / invalid → None)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def effective_status(state: dict | None, today: datetime.date) -> str:
    """Collapse a stored per-task state dict into a single display status.

    Resolution order (first match wins):

    1. ``None`` or empty dict           → ``"open"``.
    2. ``snooze_until`` in the future    → ``"snoozed"`` (a past snooze falls
       through, so the task re-surfaces as ``"open"``).
    3. ``status == "dismissed"``         → ``"dismissed"``.
    4. ``status == "done"``              → ``"done"`` when ``done_date`` is
       present and within the last 7 days, otherwise ``"archived"``. A done task
       with no (or an unparseable) ``done_date`` is treated as ``"archived"`` —
       there is no date to keep it surfaced, which is exactly what legacy
       ``task_completions`` folded on load want (hidden, never resurfacing).
    5. anything else                     → ``"open"``.

    Args:
        state: Stored per-task state dict, or ``None``.
        today: Reference date for snooze/age comparisons.

    Returns:
        One of ``"open" | "snoozed" | "done" | "dismissed" | "archived"``.
    """
    if not state:
        return "open"

    snooze_until = _parse_iso(state.get("snooze_until"))
    if snooze_until is not None and snooze_until > today:
        return "snoozed"

    status = state.get("status")
    if status == "dismissed":
        return "dismissed"

    if status == "done":
        done_date = _parse_iso(state.get("done_date"))
        if done_date is not None and (today - done_date).days <= 7:
            return "done"
        return "archived"

    return "open"


def is_visible_active(effective: str) -> bool:
    """Return True for statuses that belong in the active (non-archived) view.

    ``"open"`` and ``"done"`` are shown in the main list (a freshly-done task
    stays visible for up to a week before it ages into ``"archived"``);
    ``"snoozed"``, ``"dismissed"`` and ``"archived"`` are hidden from it.
    """
    return effective in ("open", "done")
