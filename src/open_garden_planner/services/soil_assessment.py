"""Pure soil ratings, mismatch rules, amendment plans and test preparation.

This module owns the soil judgements used by both the canvas facade and the
Agent API. It has no Qt imports; callers provide records, plant snapshots and
the optional amendment catalog explicitly.
"""
from __future__ import annotations

import datetime
import math
import uuid
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from open_garden_planner.models.amendment import (
    FIX_IMPROVES_AERATION,
    FIX_IMPROVES_DRAINAGE,
    FIX_IMPROVES_WATER_RETENTION,
    FIX_LOWERS_PH,
    FIX_RAISES_PH,
    Amendment,
    AmendmentRecommendation,
)
from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.models.soil_test import SoilTestHistory, SoilTestRecord
from open_garden_planner.services.amendment_loader import (
    AmendmentLoader,
    get_default_loader,
)

GLOBAL_TARGET_ID = "global"


class HealthLevel(Enum):
    """Coarse soil-health bucket used by the canvas overlay."""

    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"
    UNKNOWN = "unknown"


PARAM_OVERALL = "overall"
PARAM_PH = "ph"
PARAM_N = "n"
PARAM_P = "p"
PARAM_K = "k"
ALL_PARAMS: tuple[str, ...] = (PARAM_OVERALL, PARAM_PH, PARAM_N, PARAM_P, PARAM_K)

_HEALTH_RGBA: dict[HealthLevel, tuple[int, int, int, int]] = {
    HealthLevel.GOOD: (100, 200, 100, 80),
    HealthLevel.FAIR: (255, 200, 0, 80),
    HealthLevel.POOR: (220, 60, 60, 80),
}

MISMATCH_PH_LOW = "ph_low"
MISMATCH_PH_HIGH = "ph_high"
MISMATCH_N_HIGH_DEMAND = "n_high_demand"
MISMATCH_P_HIGH_DEMAND = "p_high_demand"
MISMATCH_K_HIGH_DEMAND = "k_high_demand"
MISMATCH_REASON_CODES: tuple[str, ...] = (
    MISMATCH_PH_LOW,
    MISMATCH_PH_HIGH,
    MISMATCH_N_HIGH_DEMAND,
    MISMATCH_P_HIGH_DEMAND,
    MISMATCH_K_HIGH_DEMAND,
)

# Accepted Rapitest kit level per nutrient, inclusive. Lab ppm fields have a
# separate unit and are deliberately never converted into these categorical
# levels by the assessment engine.
RAPITEST_LEVEL_RANGES: dict[str, tuple[int, int]] = {
    "n": (0, 4),
    "p": (0, 4),
    "k": (1, 4),
    "ca": (0, 2),
    "mg": (0, 2),
    "s": (0, 2),
}
SOIL_TEXTURES: tuple[str, ...] = ("sandy", "loamy", "clayey", "compacted")

_RANK = {
    HealthLevel.UNKNOWN: -1,
    HealthLevel.GOOD: 0,
    HealthLevel.FAIR: 1,
    HealthLevel.POOR: 2,
}
_SECONDARY_TARGET = 2
_NUTRIENT_KINDS: tuple[str, ...] = ("n", "p", "k", "ca", "mg", "s")
_FIX_TO_TEXTURE = {
    "sandy": (FIX_IMPROVES_WATER_RETENTION,),
    "clayey": (FIX_IMPROVES_DRAINAGE, FIX_IMPROVES_AERATION),
    "compacted": (FIX_IMPROVES_AERATION,),
}
class SoilTestError(ValueError):
    """A refusal while validating a soil-test record."""


class SoilTestOperationError(ValueError):
    """A refusal while preparing a soil-test history change."""


@dataclass(frozen=True)
class SoilMismatchReason:
    """Stable reason code plus translation source and interpolation values."""

    code: str
    translation_key: str
    arguments: tuple[tuple[str, str | int | float], ...]

    def argument_dict(self) -> dict[str, str | int | float]:
        """Return a detached mapping for a presentation adapter."""
        return dict(self.arguments)


@dataclass(frozen=True)
class SoilTestChange:
    """Detached before/after snapshots for one target's test history."""

    target_id: str
    operation: Literal["add", "edit", "delete"]
    record_id: str
    before: dict[str, Any] | None
    after: dict[str, Any]

    @property
    def is_noop(self) -> bool:
        """Whether the prepared history is semantically unchanged."""
        if self.operation != "edit" or self.before is None:
            return False
        before = SoilTestHistory.from_dict(deepcopy(self.before))
        after = SoilTestHistory.from_dict(deepcopy(self.after))
        return before == after


def _ph_level(ph: float | None) -> HealthLevel:
    if ph is None:
        return HealthLevel.UNKNOWN
    if 6.0 <= ph <= 7.0:
        return HealthLevel.GOOD
    if 5.5 <= ph < 6.0 or 7.0 < ph <= 7.5:
        return HealthLevel.FAIR
    return HealthLevel.POOR


def _npk_level(level: int | None) -> HealthLevel:
    """Rate a Rapitest 0–4 N/P/K reading; 2 and 3 are both good."""
    if level is None:
        return HealthLevel.UNKNOWN
    if level <= 1:
        return HealthLevel.POOR
    if level == 4:
        return HealthLevel.FAIR
    return HealthLevel.GOOD


def health_level(record: SoilTestRecord | None, parameter: str) -> HealthLevel:
    """Rate one pH/N/P/K parameter, or the worst known one overall."""
    if record is None:
        return HealthLevel.UNKNOWN
    if parameter == PARAM_PH:
        return _ph_level(record.ph)
    if parameter == PARAM_N:
        return _npk_level(record.n_level)
    if parameter == PARAM_P:
        return _npk_level(record.p_level)
    if parameter == PARAM_K:
        return _npk_level(record.k_level)
    levels = [
        _ph_level(record.ph),
        _npk_level(record.n_level),
        _npk_level(record.p_level),
        _npk_level(record.k_level),
    ]
    known = [level for level in levels if level is not HealthLevel.UNKNOWN]
    return max(known, key=_RANK.__getitem__) if known else HealthLevel.UNKNOWN


def overlay_rgba(level: HealthLevel) -> tuple[int, int, int, int] | None:
    """Return the tint for a known level; unknown readings have no tint."""
    return _HEALTH_RGBA.get(level)


def _level_deficit(current: int | None, target: int) -> int:
    return 0 if current is None else max(0, target - current)


def _level_effect(amendment: Amendment, kind: str) -> int:
    return int(getattr(amendment, f"{kind}_level_effect", 0))


def _pick_ph(
    current_ph: float | None,
    target_ph: float,
    amendments: list[Amendment],
    used: set[str],
) -> Amendment | None:
    if current_ph is None:
        return None
    delta = target_ph - current_ph
    if abs(delta) < 0.1:
        return None
    needed_fix = FIX_RAISES_PH if delta > 0 else FIX_LOWERS_PH
    return next(
        (
            amendment
            for amendment in amendments
            if amendment.id not in used
            and needed_fix in amendment.fixes
            and amendment.ph_effect_per_100g_m2 != 0
        ),
        None,
    )


def _pick_best_coverage(
    amendments: list[Amendment],
    used: set[str],
    deficits: dict[str, int],
    prefer_organic: bool,
) -> tuple[Amendment, str] | None:
    best: tuple[Amendment, str] | None = None
    best_key: tuple[int, int, int] | None = None
    for index, candidate in enumerate(amendments):
        if candidate.id in used:
            continue
        covered = [
            kind
            for kind in _NUTRIENT_KINDS
            if deficits.get(kind, 0) > 0 and _level_effect(candidate, kind) > 0
        ]
        if not covered:
            continue
        primary = max(
            covered,
            key=lambda kind: (deficits[kind], -_NUTRIENT_KINDS.index(kind)),
        )
        organic_score = (1 if candidate.organic else 0) if prefer_organic else 0
        key = (len(covered), organic_score, -index)
        if best_key is None or key > best_key:
            best_key = key
            best = candidate, primary
    return best


def _pick_structural(
    fix_tag: str, amendments: list[Amendment], used: set[str]
) -> Amendment | None:
    return next(
        (
            amendment
            for amendment in amendments
            if amendment.id not in used and fix_tag in amendment.fixes
        ),
        None,
    )


def _structural_fixes_for(soil_texture: str | None) -> tuple[str, ...]:
    return _FIX_TO_TEXTURE.get(soil_texture or "", ())


def _credit_secondaries(
    pick: Amendment,
    deficits: dict[str, int],
    currents: dict[str, int],
    targets: dict[str, int],
    recommendation: AmendmentRecommendation,
) -> None:
    for kind in ("ca", "mg", "s"):
        effect = _level_effect(pick, kind)
        if effect <= 0 or deficits.get(kind, 0) <= 0:
            continue
        current = currents[kind]
        target = targets[kind]
        recommendation.credits.append((kind, float(current), float(target)))
        deficits[kind] = max(0, deficits[kind] - effect)
        currents[kind] = min(target, current + effect)


def _compute_ph(
    amendment: Amendment,
    current_ph: float | None,
    target_ph: float,
    bed_area_m2: float,
) -> AmendmentRecommendation:
    current = current_ph if current_ph is not None else target_ph
    delta = abs(target_ph - current)
    effect = abs(amendment.ph_effect_per_100g_m2) or 1.0
    return AmendmentRecommendation(
        amendment=amendment,
        quantity_g=(delta / effect) * 100.0 * bed_area_m2,
        target_kind="ph",
        current_value=current,
        target_value=target_ph,
    )


def _compute_nutrient(
    amendment: Amendment,
    current_level: int,
    target_level: int,
    kind: str,
    bed_area_m2: float,
) -> AmendmentRecommendation:
    return AmendmentRecommendation(
        amendment=amendment,
        quantity_g=max(0, target_level - current_level)
        * amendment.application_rate_g_m2
        * bed_area_m2,
        target_kind=kind,
        current_value=float(current_level),
        target_value=float(target_level),
    )


def _compute_structural(
    amendment: Amendment, fix_tag: str, bed_area_m2: float
) -> AmendmentRecommendation:
    return AmendmentRecommendation(
        amendment=amendment,
        quantity_g=amendment.application_rate_g_m2 * bed_area_m2,
        target_kind="structure",
        current_value=0.0,
        target_value=1.0,
        structural_fix=fix_tag,
    )


def _effective_demand(
    spec: PlantSpeciesData,
) -> tuple[str | None, str | None, str | None]:
    n, p, k = spec.n_demand, spec.p_demand, spec.k_demand
    if n is None and spec.nutrient_demand:
        mapped = {"heavy": "high", "light": "low", "fixer": "fixer"}.get(
            spec.nutrient_demand
        )
        n = p = k = mapped
    return n, p, k


def calculate_amendments(
    record: SoilTestRecord | None,
    target_ph: float = 6.5,
    target_n: int = 3,
    target_p: int = 3,
    target_k: int = 3,
    bed_area_m2: float = 0.0,
    loader: AmendmentLoader | None = None,
    enabled_ids: set[str] | None = None,
    prefer_organic: bool = True,
) -> list[AmendmentRecommendation]:
    """Compute pH, nutrient and texture recommendations from one snapshot."""
    if record is None or bed_area_m2 <= 0.0:
        return []

    all_amendments = (loader or get_default_loader()).get_amendments()
    pool = (
        list(all_amendments)
        if enabled_ids is None
        else [amendment for amendment in all_amendments if amendment.id in enabled_ids]
    )
    used_ids: set[str] = set()
    results: list[AmendmentRecommendation] = []
    deficits = {
        "n": _level_deficit(record.n_level, target_n),
        "p": _level_deficit(record.p_level, target_p),
        "k": _level_deficit(record.k_level, target_k),
        "ca": _level_deficit(record.ca_level, _SECONDARY_TARGET),
        "mg": _level_deficit(record.mg_level, _SECONDARY_TARGET),
        "s": _level_deficit(record.s_level, _SECONDARY_TARGET),
    }
    currents = {
        kind: getattr(record, f"{kind}_level") or 0 for kind in _NUTRIENT_KINDS
    }
    targets = {
        "n": target_n,
        "p": target_p,
        "k": target_k,
        "ca": _SECONDARY_TARGET,
        "mg": _SECONDARY_TARGET,
        "s": _SECONDARY_TARGET,
    }

    ph_pick = _pick_ph(record.ph, target_ph, pool, used_ids)
    if ph_pick is not None:
        ph_rec = _compute_ph(ph_pick, record.ph, target_ph, bed_area_m2)
        _credit_secondaries(ph_pick, deficits, currents, targets, ph_rec)
        results.append(ph_rec)
        used_ids.add(ph_pick.id)

    while any(deficit > 0 for deficit in deficits.values()):
        picked = _pick_best_coverage(pool, used_ids, deficits, prefer_organic)
        if picked is None:
            break
        amendment, primary_kind = picked
        primary_deficit = deficits[primary_kind]
        primary_current = currents[primary_kind]
        primary_target = targets[primary_kind]
        recommendation = _compute_nutrient(
            amendment,
            primary_current,
            primary_target,
            primary_kind,
            bed_area_m2,
        )
        deficits[primary_kind] = 0
        currents[primary_kind] = primary_target
        for kind in _NUTRIENT_KINDS:
            if kind == primary_kind:
                continue
            effect = _level_effect(amendment, kind)
            if effect <= 0 or deficits.get(kind, 0) <= 0:
                continue
            current = currents[kind]
            target = targets[kind]
            credit = min(deficits[kind], primary_deficit * effect)
            recommendation.credits.append(
                (kind, float(current), float(min(target, current + credit)))
            )
            deficits[kind] = max(0, deficits[kind] - credit)
            currents[kind] = min(target, current + credit)
        results.append(recommendation)
        used_ids.add(amendment.id)

    for fix in _structural_fixes_for(record.soil_texture):
        amendment = _pick_structural(fix, pool, used_ids)
        if amendment is None:
            continue
        results.append(_compute_structural(amendment, fix, bed_area_m2))
        used_ids.add(amendment.id)
    return results


def _mismatch_reason(
    code: str, translation_key: str, **arguments: str | int | float
) -> SoilMismatchReason:
    return SoilMismatchReason(code, translation_key, tuple(arguments.items()))


def assess_mismatches(
    record: SoilTestRecord | None,
    plant_specs: list[PlantSpeciesData],
) -> list[tuple[PlantSpeciesData, list[SoilMismatchReason]]]:
    """Return mismatch codes and presentation keys without rendering text."""
    if record is None:
        return []
    results: list[tuple[PlantSpeciesData, list[SoilMismatchReason]]] = []
    for spec in plant_specs:
        reasons: list[SoilMismatchReason] = []
        name = spec.common_name or spec.scientific_name or ""
        if record.ph is not None and spec.ph_min is not None and spec.ph_max is not None:
            if record.ph < spec.ph_min - 0.05:
                reasons.append(
                    _mismatch_reason(
                        MISMATCH_PH_LOW,
                        "{name} needs pH ≥{min:.1f}, current {cur:.1f}",
                        name=name,
                        min=spec.ph_min,
                        cur=record.ph,
                    )
                )
            elif record.ph > spec.ph_max + 0.05:
                reasons.append(
                    _mismatch_reason(
                        MISMATCH_PH_HIGH,
                        "{name} needs pH ≤{max:.1f}, current {cur:.1f}",
                        name=name,
                        max=spec.ph_max,
                        cur=record.ph,
                    )
                )
        n_demand, p_demand, k_demand = _effective_demand(spec)
        for demand, kind, level, code in (
            (n_demand, "N", record.n_level, MISMATCH_N_HIGH_DEMAND),
            (p_demand, "P", record.p_level, MISMATCH_P_HIGH_DEMAND),
            (k_demand, "K", record.k_level, MISMATCH_K_HIGH_DEMAND),
        ):
            if demand == "high" and level is not None and level < 2:
                reasons.append(
                    _mismatch_reason(
                        code,
                        "{name} is a heavy {kind} feeder (current level: {lvl})",
                        name=name,
                        kind=kind,
                        lvl=level,
                    )
                )
        if reasons:
            results.append((spec, reasons))
    return results


def is_test_overdue(history: SoilTestHistory | None, today: datetime.date) -> bool:
    """Check the March/April and September/October 180-day sampling window."""
    if today.month not in {3, 4, 9, 10} or history is None or not history.records:
        return False
    latest = history.latest
    if latest is None:
        return False
    try:
        last_date = datetime.date.fromisoformat(latest.date)
    except (ValueError, TypeError):
        return True
    return (today - last_date).days > 180


def build_soil_record_for_agent(
    *,
    ph: float | None = None,
    n_level: int | None = None,
    p_level: int | None = None,
    k_level: int | None = None,
    ca_level: int | None = None,
    mg_level: int | None = None,
    s_level: int | None = None,
    soil_texture: str | None = None,
    test_date: str | None = None,
    notes: str | None = None,
    today: datetime.date | None = None,
    record_id: str | None = None,
) -> SoilTestRecord:
    """Validate kit measurements and prepare a record without mutating state.

    ``*_ppm`` fields are intentionally not accepted here: lab values and
    Rapitest categorical levels are distinct units, and this package has no
    conversion policy. At least one recognized measurement is required.
    """
    reference = today or datetime.date.today()
    if ph is not None:
        if isinstance(ph, bool) or not isinstance(ph, (int, float)):
            raise SoilTestError(f"ph={ph!r} must be a number.")
        if not math.isfinite(float(ph)):
            raise SoilTestError(f"ph={ph!r} is not a finite number.")
        if not 0.0 <= float(ph) <= 14.0:
            raise SoilTestError(f"ph={ph} is outside 0.0-14.0. Pass the measured pH value.")

    levels: dict[str, int | None] = {}
    for name, value in (
        ("n", n_level),
        ("p", p_level),
        ("k", k_level),
        ("ca", ca_level),
        ("mg", mg_level),
        ("s", s_level),
    ):
        levels[name] = None if value is None else _check_level(name, value)

    if soil_texture is not None and soil_texture not in SOIL_TEXTURES:
        raise SoilTestError(
            f"soil_texture={soil_texture!r} is not one of {', '.join(SOIL_TEXTURES)}."
        )

    resolved_date = reference
    if test_date:
        try:
            parsed = datetime.date.fromisoformat(test_date)
        except (TypeError, ValueError):
            raise SoilTestError(
                f"test_date={test_date!r} is not an ISO date. Use YYYY-MM-DD."
            ) from None
        if parsed > reference:
            raise SoilTestError(
                f"test_date={test_date} is in the future (today is "
                f"{reference.isoformat()}). A test cannot have been taken yet."
            )
        resolved_date = parsed

    if ph is None and all(value is None for value in levels.values()):
        raise SoilTestError(
            "A soil test with no readings is noise. Pass at least one of ph, "
            "n_level, p_level, k_level, ca_level, mg_level or s_level."
        )

    return SoilTestRecord(
        id=record_id or str(uuid.uuid4()),
        date=resolved_date.isoformat(),
        ph=float(ph) if ph is not None else None,
        n_level=levels["n"],
        p_level=levels["p"],
        k_level=levels["k"],
        ca_level=levels["ca"],
        mg_level=levels["mg"],
        s_level=levels["s"],
        notes=notes or "",
        mode="kit",
        soil_texture=soil_texture,
    )


def _check_level(name: str, value: Any) -> int:
    low, high = RAPITEST_LEVEL_RANGES[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise SoilTestError(
            f"{name}_level={value!r} must be a whole number on the Rapitest "
            f"kit scale ({low}-{high}), not a lab reading. This tool takes the "
            "categorical kit scale; the soil dialog records lab ppm values."
        )
    if not low <= value <= high:
        raise SoilTestError(
            f"{name}_level={value} is outside the Rapitest kit scale "
            f"({low}-{high}) for {name}. Pass the categorical kit reading, not "
            "a lab value in ppm — a ppm number here would be read as a kit "
            "level and would silently change every recommendation."
        )
    return value


def prepare_soil_test_change(
    soil_tests: Mapping[str, Mapping[str, Any]],
    *,
    operation: Literal["add", "edit", "delete"],
    target_id: str,
    record: SoilTestRecord | None = None,
    record_id: str | None = None,
) -> SoilTestChange:
    """Prepare one history mutation, preserving record and history extensions."""
    if not target_id:
        raise SoilTestOperationError("target_id is required.")
    if operation not in {"add", "edit", "delete"}:
        raise SoilTestOperationError(f"Unsupported soil-test operation: {operation!r}.")
    if operation in {"add", "edit"} and record is None:
        raise SoilTestOperationError("A soil-test record is required.")
    if operation == "delete" and not record_id:
        raise SoilTestOperationError("record_id is required to delete a soil test.")

    raw_before = soil_tests.get(target_id)
    before = deepcopy(dict(raw_before)) if raw_before is not None else None
    if raw_before is None:
        if operation != "add":
            raise SoilTestOperationError(f"No soil-test history exists for {target_id!r}.")
        history = SoilTestHistory(target_id=target_id)
    else:
        history = SoilTestHistory.from_dict(deepcopy(dict(raw_before)))

    if operation == "add":
        assert record is not None
        if any(existing.id == record.id for existing in history.records):
            raise SoilTestOperationError(
                f"Soil-test record {record.id!r} already exists for {target_id!r}."
            )
        history.records.append(SoilTestRecord.from_dict(record.to_dict()))
        resolved_record_id = record.id
    elif operation == "edit":
        assert record is not None
        resolved_record_id = record.id
        for index, existing in enumerate(history.records):
            if existing.id != record.id:
                continue
            updated = SoilTestRecord.from_dict(record.to_dict())
            updated.extra_fields = {
                **deepcopy(existing.extra_fields),
                **deepcopy(updated.extra_fields),
            }
            history.records[index] = updated
            break
        else:
            raise SoilTestOperationError(
                f"No soil-test record {record.id!r} exists for {target_id!r}."
            )
    else:
        resolved_record_id = record_id or ""
        remaining = [item for item in history.records if item.id != resolved_record_id]
        if len(remaining) == len(history.records):
            raise SoilTestOperationError(
                f"No soil-test record {resolved_record_id!r} exists for {target_id!r}."
            )
        history.records = remaining

    return SoilTestChange(
        target_id=target_id,
        operation=operation,
        record_id=resolved_record_id,
        before=before,
        after=history.to_dict(),
    )


__all__ = [
    "ALL_PARAMS",
    "GLOBAL_TARGET_ID",
    "HealthLevel",
    "MISMATCH_K_HIGH_DEMAND",
    "MISMATCH_N_HIGH_DEMAND",
    "MISMATCH_PH_HIGH",
    "MISMATCH_PH_LOW",
    "MISMATCH_P_HIGH_DEMAND",
    "MISMATCH_REASON_CODES",
    "PARAM_K",
    "PARAM_N",
    "PARAM_OVERALL",
    "PARAM_P",
    "PARAM_PH",
    "RAPITEST_LEVEL_RANGES",
    "SOIL_TEXTURES",
    "SoilMismatchReason",
    "SoilTestChange",
    "SoilTestError",
    "SoilTestOperationError",
    "assess_mismatches",
    "build_soil_record_for_agent",
    "calculate_amendments",
    "health_level",
    "is_test_overdue",
    "overlay_rgba",
    "prepare_soil_test_change",
]
