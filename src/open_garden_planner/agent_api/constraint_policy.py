"""Qt-free proofs of which rigid translations preserve existing constraints.

This is an invariance check, not a solver or a residual tolerance. A relation
may already be unsatisfied: a permitted translation preserves that same relation
rather than silently claiming to repair it. Exact comparisons of requested
translation components avoid a tolerance band that repeated calls could exploit.
Unknown/new types fail closed until their geometry has been classified.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

# Drift-guarded against the actual ConstraintType enum in the tests. Values are
# machine names from the canonical constraint serialization, not translated UI.
TRANSLATION_FREE_TYPES = frozenset({"EQUAL", "PARALLEL", "PERPENDICULAR"})
PAIR_TRANSLATION_TYPES = frozenset({"DISTANCE", "EDGE_LENGTH", "COINCIDENT", "POINT_ON_CIRCLE"})
TRIPLE_TRANSLATION_TYPES = frozenset({"ANGLE", "POINT_ON_EDGE", "TANGENT"})
CLASSIFIED_TYPES = (TRANSLATION_FREE_TYPES | PAIR_TRANSLATION_TYPES | TRIPLE_TRANSLATION_TYPES
                    | {"FIXED", "HORIZONTAL", "VERTICAL", "HORIZONTAL_DISTANCE", "VERTICAL_DISTANCE",
                       "SYMMETRY_HORIZONTAL", "SYMMETRY_VERTICAL"})


def translation_preserves_constraint(
    constraint: Mapping[str, Any], deltas: Mapping[str, tuple[float, float]],
) -> bool:
    """Prove invariance using every anchor's effective scene translation.

    Missing IDs in deltas are stationary objects. A size or edge-direction
    equality is independent of position. Other relations require equal anchor
    shifts, equal relevant axis components, or mirror shifts about their stored
    WORLD axis. A fixed object cannot translate. Three-point relations inspect
    the third anchor too; moving only the first two is insufficient.
    """
    kind = constraint.get("constraint_type")
    if kind not in CLASSIFIED_TYPES:
        return False
    keys = ("anchor_a", "anchor_b", "anchor_c") if kind in TRIPLE_TRANSLATION_TYPES else ("anchor_a", "anchor_b")
    shifts = []
    for key in keys:
        anchor = constraint.get(key)
        if not isinstance(anchor, Mapping) or not isinstance(anchor.get("item_id"), str):
            return False
        delta = deltas.get(anchor["item_id"], (0.0, 0.0))
        if not all(math.isfinite(v) for v in delta):
            return False
        shifts.append(delta)
    a, b = shifts[:2]
    if kind in TRANSLATION_FREE_TYPES:
        return True
    if kind == "FIXED":
        return a == (0.0, 0.0)
    if kind in PAIR_TRANSLATION_TYPES:
        return a == b
    if kind in TRIPLE_TRANSLATION_TYPES:
        return a == b == shifts[2]
    if kind in {"HORIZONTAL", "VERTICAL_DISTANCE"}:
        return a[1] == b[1]
    if kind in {"VERTICAL", "HORIZONTAL_DISTANCE"}:
        return a[0] == b[0]
    if kind == "SYMMETRY_HORIZONTAL":
        # xA == xB and yA + yB == 2 * world_axis_y.
        return a[0] == b[0] and a[1] == -b[1]
    if kind == "SYMMETRY_VERTICAL":
        return a[1] == b[1] and a[0] == -b[0]
    return False
