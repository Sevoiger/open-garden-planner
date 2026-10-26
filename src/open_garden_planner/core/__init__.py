"""Core business logic and document management.

Public names stay available from this package, while importing a small pure
module such as :mod:`core.plan_io` does not eagerly import the Qt command stack.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "AddConstraintCommand",
    "AddHarvestRecordCommand",
    "AddJournalNoteCommand",
    "AddPestLogCommand",
    "AddSoilTestCommand",
    "AlignItemsCommand",
    "ArrayAlongPathCommand",
    "BooleanShapeCommand",
    "CircularArrayCommand",
    "Command",
    "CommandManager",
    "CreateItemCommand",
    "CreateItemsCommand",
    "DeleteItemsCommand",
    "DeleteHarvestRecordCommand",
    "DeleteJournalNoteCommand",
    "DeletePestLogCommand",
    "DeleteSoilTestCommand",
    "EditConstraintDistanceCommand",
    "EditHarvestRecordCommand",
    "EditJournalNoteCommand",
    "EditPestLogCommand",
    "EditSoilTestCommand",
    "GridArrayCommand",
    "LinearArrayCommand",
    "MirrorItemsCommand",
    "MoveItemsCommand",
    "RemoveConstraintCommand",
    "SetParentBedCommand",
    "SetSuccessionPlanCommand",
    "SetTaskStatusCommand",
    "ProjectData",
    "ProjectManager",
    "calculate_area_and_perimeter",
    "format_area",
    "format_length",
]

_EXPORT_MODULES = {
    **{name: "commands" for name in __all__ if name.endswith("Command") or name == "CommandManager"},
    **dict.fromkeys(("calculate_area_and_perimeter", "format_area", "format_length"), "measurements"),
    "ProjectData": "project_data",
    "ProjectManager": "project",
}


def __getattr__(name: str) -> Any:
    """Load historical package exports only when a caller requests them."""
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{module_name}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
