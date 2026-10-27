"""Pure project document data and file-version helpers.

This module contains the detached .ogp value model only. Scene management,
settings, Qt commands, and global library synchronization stay in
:mod:`open_garden_planner.core.project`.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from open_garden_planner.core.north_orientation import normalize_north_angle
from open_garden_planner.models.garden_memory import validate_memory

# File format version for backward compatibility.
#
# 1.4 (Phase 13 Package B): adds ``bezier`` and ``arc`` item types.
# v1.3 files load transparently with no new items.
#
# A short-lived earlier draft also wrote a top-level ``paper_layouts``
# array for the Paper Space MVP; that feature was dropped before PR
# #191 merged because the existing ``pdf_report_service`` already
# covers print-to-PDF at chosen paper sizes. The loader silently
# ignores any ``paper_layouts`` key it encounters so projects saved by
# those draft builds still open.
FILE_VERSION = "1.4"


def _is_newer_file_version(file_version: str, supported_version: str) -> bool:
    """Return True if ``file_version`` is strictly newer than ``supported_version``.

    Versions are ``X.Y`` strings. Unparseable strings are treated as
    equal-or-older so legacy files (e.g. an early build that wrote
    "1.0" or no version key at all) still open.
    """
    def _parse(v: str) -> tuple[int, int] | None:
        try:
            parts = v.split(".")
            return (int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            return None

    a = _parse(file_version)
    b = _parse(supported_version)
    if a is None or b is None:
        return False
    return a > b


@dataclass
class ProjectData:
    """Data structure for a project."""

    canvas_width: float = 5000.0
    canvas_height: float = 3000.0
    objects: list[dict[str, Any]] = field(default_factory=list)
    layers: list[dict[str, Any]] = field(default_factory=list)
    constraints: list[dict[str, Any]] = field(default_factory=list)
    guides: list[dict[str, Any]] = field(default_factory=list)
    north_angle_deg: float = 0.0
    location: dict[str, Any] | None = None
    task_completions: list[str] = field(default_factory=list)
    seed_inventory: list[dict[str, Any]] = field(default_factory=list)
    # US-9.5: per-species user overrides for propagation step dates
    # shape: {species_key: {step_id: {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}}}
    propagation_overrides: dict[str, Any] = field(default_factory=dict)
    # US-10.5: crop rotation history
    crop_rotation: dict[str, Any] = field(default_factory=dict)
    # US-10.7: season management
    season_year: int | None = None
    # List of dicts: {"year": int, "file": "relative/or/absolute/path.ogp"}
    linked_seasons: list[dict[str, Any]] = field(default_factory=list)
    # US-12.10a: per-bed (and "global" default) soil test history
    # shape: {target_id: SoilTestHistory.to_dict()} where target_id is bed UUID or "global"
    soil_tests: dict[str, Any] = field(default_factory=dict)
    # US-12.7: per-bed and per-plant pest/disease log history
    # shape: {target_id: PestLogHistory.to_dict()} where target_id is a bed or plant UUID
    pest_disease_logs: dict[str, Any] = field(default_factory=dict)
    # US-12.6: user-entered prices for the shopping list, keyed by ShoppingListItem.id
    shopping_list_prices: dict[str, float] = field(default_factory=dict)
    # US-12.6: shopping-list rows the user already owns. Excluded from CSV /
    # PDF / clipboard export and from the dialog's grand total, but still
    # rendered (dimmed) in the table so the toggle is discoverable.
    excluded_shopping_items: list[str] = field(default_factory=list)

    # US-12.11: amendment-library allowlist + organic-preference flag.
    # ``None`` means "every amendment in the bundled library is enabled" — the
    # default for new projects so the calculator behaves identically to legacy
    # files. A non-``None`` list is the user's explicit toggleable allowlist.
    enabled_amendments: list[str] | None = None
    prefer_organic: bool = True

    # US-12.8: per-bed succession plans
    # shape: {bed_id: SuccessionPlan.to_dict()} where bed_id is a bed UUID string
    succession_plans: dict[str, Any] = field(default_factory=dict)
    auto_assign_permaculture_layers: bool = False

    # US-12.9: garden journal map-linked notes
    # shape: {note_id: JournalNote.to_dict()}; canvas pins reference notes by id
    garden_journal_notes: dict[str, Any] = field(default_factory=dict)

    # US-C2 (#188): task management.
    # manual_tasks shape: {task_id: ManualTask.to_dict()} (user-created tasks).
    manual_tasks: dict[str, Any] = field(default_factory=dict)
    # task_states shape: {task_id: {"status": "done"|"dismissed"|"open",
    #   "done_date"?: "YYYY-MM-DD", "snooze_until"?: "YYYY-MM-DD"}} — per-task
    # status for BOTH generated (deterministic id) and manual tasks. Sparse:
    # plain "open" with no snooze is never stored. Legacy task_completions are
    # folded into this on load (see ProjectManager.load).
    task_states: dict[str, Any] = field(default_factory=dict)

    # US-C1 (#188): harvest / yield log.
    # shape: {target_id: HarvestHistory.to_dict()} where target_id is a plant
    # or bed UUID string.
    harvest_logs: dict[str, Any] = field(default_factory=dict)

    garden_memory: dict[str, Any] = field(default_factory=dict)
    extra_fields: dict[str, Any] = field(default_factory=dict)
    source_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data: dict[str, Any] = {
            "version": FILE_VERSION,
            "metadata": {
                **deepcopy(self.source_metadata),
                "modified": datetime.now(UTC).isoformat(),
            },
            "canvas": {
                "width": self.canvas_width,
                "height": self.canvas_height,
            },
            "north_angle_deg": normalize_north_angle(self.north_angle_deg),
            "layers": self.layers,
            "objects": self.objects,
        }
        data = {**deepcopy(self.extra_fields), **data}
        if self.constraints:
            data["constraints"] = self.constraints
        if self.guides:
            data["guides"] = self.guides
        if self.location:
            data["location"] = self.location
        if self.task_completions:
            data["task_completions"] = sorted(self.task_completions)
        if self.seed_inventory:
            data["seed_inventory"] = self.seed_inventory
        if self.propagation_overrides:
            data["propagation_overrides"] = self.propagation_overrides
        if self.crop_rotation:
            data["crop_rotation"] = self.crop_rotation
        if self.season_year is not None:
            data["season_year"] = self.season_year
        if self.linked_seasons:
            data["linked_seasons"] = self.linked_seasons
        if self.soil_tests:
            data["soil_tests"] = self.soil_tests
        if self.pest_disease_logs:
            data["pest_disease_logs"] = self.pest_disease_logs
        if self.shopping_list_prices:
            data["shopping_list_prices"] = self.shopping_list_prices
        if self.excluded_shopping_items:
            data["excluded_shopping_items"] = sorted(self.excluded_shopping_items)

        if self.enabled_amendments is not None:
            data["enabled_amendments"] = sorted(self.enabled_amendments)
        if self.prefer_organic is False:
            data["prefer_organic"] = False
        if self.succession_plans:
            data["succession_plans"] = self.succession_plans
        if self.auto_assign_permaculture_layers:
            data["auto_assign_permaculture_layers"] = True
        if self.garden_journal_notes:
            data["garden_journal_notes"] = self.garden_journal_notes
        if self.manual_tasks:
            data["manual_tasks"] = self.manual_tasks
        if self.task_states:
            data["task_states"] = self.task_states
        if self.harvest_logs:
            data["harvest_logs"] = self.harvest_logs
        if self.garden_memory:
            data["garden_memory"] = validate_memory(self.garden_memory)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectData:
        """Create ProjectData from dictionary."""
        canvas = data.get("canvas", {})
        known = (set(cls.__dataclass_fields__) - {"canvas_width", "canvas_height", "extra_fields", "source_metadata"}) | {"version", "metadata", "canvas", "paper_layouts"}
        return cls(
            extra_fields=deepcopy({key: value for key, value in data.items() if key not in known}),
            source_metadata=deepcopy(data.get("metadata", {})) if isinstance(data.get("metadata", {}), dict) else {},
            canvas_width=canvas.get("width", 5000.0),
            canvas_height=canvas.get("height", 3000.0),
            layers=data.get("layers", []),
            objects=data.get("objects", []),
            constraints=data.get("constraints", []),
            guides=data.get("guides", []),
            north_angle_deg=normalize_north_angle(data.get("north_angle_deg", 0.0)),
            location=data.get("location") or None,
            task_completions=data.get("task_completions", []),
            seed_inventory=data.get("seed_inventory", []),
            propagation_overrides=data.get("propagation_overrides", {}),
            crop_rotation=data.get("crop_rotation", {}),
            season_year=data.get("season_year"),
            linked_seasons=data.get("linked_seasons", []),
            soil_tests=data.get("soil_tests", {}),
            pest_disease_logs=data.get("pest_disease_logs", {}),
            shopping_list_prices=data.get("shopping_list_prices", {}),
            excluded_shopping_items=list(data.get("excluded_shopping_items", [])),

            enabled_amendments=data.get("enabled_amendments"),
            prefer_organic=bool(data.get("prefer_organic", True)),
            succession_plans=data.get("succession_plans", {}),
            auto_assign_permaculture_layers=bool(data.get("auto_assign_permaculture_layers", False)),
            garden_journal_notes=data.get("garden_journal_notes", {}),
            manual_tasks=data.get("manual_tasks", {}),
            task_states=data.get("task_states", {}),
            harvest_logs=data.get("harvest_logs", {}),
            garden_memory=validate_memory(data.get("garden_memory", {})),
            # Note: `data.get("paper_layouts", ...)` is intentionally
            # not consumed here — the Paper Space feature was dropped
            # before PR #191 merged but earlier draft builds may have
            # written this key. Silently ignored.
        )
