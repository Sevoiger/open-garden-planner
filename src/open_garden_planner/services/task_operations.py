"""Pure preparation for manual-task changes.

The UI and agent API use this module to validate and prepare the same task
change before translating it to the project's existing command history. The
prepared snapshots are detached from the source mapping and retain fields this
version of :class:`ManualTask` does not understand.
"""
from __future__ import annotations

import uuid
from collections.abc import Collection, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import date as _date
from typing import Any, Literal

from open_garden_planner.models.task import ManualTask

_TASK_FIELDS = frozenset({"id", "date", "title", "notes", "bed_id"})
TaskOperation = Literal["add", "edit", "delete"]


@dataclass(frozen=True)
class ManualTaskChange:
    """Detached before/after snapshots for one manual-task operation."""

    task_id: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    operation: TaskOperation

    @property
    def is_noop(self) -> bool:
        """Whether applying this prepared change would leave the task unchanged."""
        if self.before == self.after:
            return True
        if self.operation != "edit" or self.before is None or self.after is None:
            return False

        defaults: dict[str, Any] = {
            "id": self.task_id,
            "date": "",
            "title": "",
            "notes": "",
            "bed_id": None,
        }
        before_known = {key: self.before.get(key, value) for key, value in defaults.items()}
        after_known = {key: self.after.get(key, value) for key, value in defaults.items()}
        before_extra = {key: value for key, value in self.before.items() if key not in _TASK_FIELDS}
        after_extra = {key: value for key, value in self.after.items() if key not in _TASK_FIELDS}
        return before_known == after_known and before_extra == after_extra

    @property
    def task(self) -> ManualTask | None:
        """Return the prepared task model for add/edit, if there is one."""
        if self.after is None:
            return None
        return ManualTask.from_dict(deepcopy(self.after))


def prepare_manual_task_change(
    tasks: Mapping[str, Mapping[str, Any]],
    *,
    operation: TaskOperation,
    task_id: str | None = None,
    date: str | None = None,
    title: str | None = None,
    notes: str | None = None,
    bed_id: str | None = None,
    valid_bed_ids: Collection[str] | None = None,
) -> ManualTaskChange:
    """Validate and prepare one add, edit or delete without mutating *tasks*.

    Add generates a UUID unless ``task_id`` is supplied by a caller such as the
    UI. Edit replaces the stored task fields; omitted/``None`` optional values
    mean an empty date, notes, or bed link, matching the existing agent API.
    Unknown fields on an edited record survive unchanged. If ``valid_bed_ids``
    is supplied, a non-empty bed link must be present in that collection.

    ``ValueError`` means the request is invalid or its target is missing. A
    valid edit with identical values returns a change whose ``is_noop`` is true
    so adapters can avoid adding an empty undo step.
    """
    if operation not in {"add", "edit", "delete"}:
        raise ValueError(f"Unsupported manual-task operation: {operation!r}.")

    if operation == "add":
        resolved_id = task_id or str(uuid.uuid4())
        if resolved_id in tasks:
            raise ValueError(f"A manual task with id {resolved_id!r} already exists.")
        after = _make_task_dict(
            task_id=resolved_id,
            date=date,
            title=title,
            notes=notes,
            bed_id=bed_id,
            valid_bed_ids=valid_bed_ids,
        )
        return ManualTaskChange(resolved_id, None, after, operation)

    if not task_id:
        raise ValueError("task_id is required to edit or delete a task.")
    raw_before = tasks.get(task_id)
    if raw_before is None:
        raise ValueError(f"No manual task has id {task_id!r}.")
    before = deepcopy(dict(raw_before))

    if operation == "delete":
        return ManualTaskChange(task_id, before, None, operation)

    after = _make_task_dict(
        task_id=task_id,
        date=date,
        title=title,
        notes=notes,
        bed_id=bed_id,
        valid_bed_ids=valid_bed_ids,
        extra_fields={key: value for key, value in before.items() if key not in _TASK_FIELDS},
    )
    return ManualTaskChange(task_id, before, after, operation)


def _make_task_dict(
    *,
    task_id: str,
    date: str | None,
    title: str | None,
    notes: str | None,
    bed_id: str | None,
    valid_bed_ids: Collection[str] | None,
    extra_fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    clean_title = title.strip() if isinstance(title, str) else ""
    if not clean_title:
        raise ValueError("title is required and must not be empty.")

    resolved_date = _normalize_date(date)
    clean_notes = "" if notes is None else notes
    if not isinstance(clean_notes, str):
        raise ValueError("notes must be a string.")
    if bed_id is not None and not isinstance(bed_id, str):
        raise ValueError("bed_id must be a string or null.")
    if bed_id is not None and valid_bed_ids is not None and bed_id not in valid_bed_ids:
        raise ValueError(f"No bed with id {bed_id!r} exists in this plan.")

    task = ManualTask(
        id=task_id,
        date=resolved_date,
        title=clean_title,
        notes=clean_notes,
        bed_id=bed_id,
        extra_fields=deepcopy(dict(extra_fields or {})),
    )
    return task.to_dict()


def _normalize_date(value: str | None) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise ValueError("date must be an ISO YYYY-MM-DD date or null.")
    try:
        parsed = _date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"date={value!r} is not an ISO date. Use YYYY-MM-DD.") from None
    if parsed.isoformat() != value:
        raise ValueError(f"date={value!r} is not an ISO date. Use YYYY-MM-DD.")
    return value
