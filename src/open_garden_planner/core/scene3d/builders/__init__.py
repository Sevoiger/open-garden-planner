"""The production builders of the 3D scene (Phase 17 L1.6, #385, ADR-054 addendum).

``Record -> tuple[MeshPart, ...]`` functions, one module per family, each with a
module-level ``register(registry)``. Qt-free: standard library, numpy and
Qt-free ``core`` siblings (the AST scan in ``tests/unit/test_scene3d_contract.py``
covers this package).

=================  ==========================================================
``buildings``      HOUSE (walls + gable roof from the live ridge), GARAGE_SHED,
                   TOOL_SHED (ridge from the local bounding box)
``fences``         FENCE, per ``PathFenceStyle``
``walls``          WALL
``beds``           RAISED_BED, CONTAINER, WALL_PLANTER, CONTAINER_ROUND, and a
                   GARDEN_BED that was given a height
``hedges``         HEDGE_SECTION, HEDGE_POLYGON
=================  ==========================================================

Every other kind keeps ``build.default_builder`` — a prism up to its height, or
nothing for an item without one: paths, lawns and in-ground beds are ground
(the ground bake paints them); furniture, glass, water and pergolas are L4.

Shared by all of them: ``primitives`` (boxes, sweep, plank, lathe, tubes),
``palette`` (the mirrored 2D colours), ``common`` (the top is the height; no
height, no shadow), ``roofs`` (the gable roof).

The wiring (L1.3) calls ``production_registry()`` once and hands the result to
``SceneSync``.
"""

from __future__ import annotations

from ..build import BuilderRegistry
from . import beds, buildings, fences, hedges, walls

__all__ = ["BUILT_WORLD_KINDS", "production_registry", "register"]

#: The kinds the built world registers a builder for (pinned by a test).
BUILT_WORLD_KINDS: frozenset[str] = frozenset({
    "HOUSE", "GARAGE_SHED", "TOOL_SHED",
    "FENCE", "WALL",
    "RAISED_BED", "CONTAINER", "WALL_PLANTER", "CONTAINER_ROUND", "GARDEN_BED",
    "HEDGE_SECTION", "HEDGE_POLYGON",
})


def register(registry: BuilderRegistry) -> None:
    """Register every built-world builder (this story's five families)."""
    buildings.register(registry)
    fences.register(registry)
    walls.register(registry)
    beds.register(registry)
    hedges.register(registry)


def production_registry() -> BuilderRegistry:
    """A fresh registry holding every production builder."""
    registry = BuilderRegistry()
    register(registry)
    # ── L1.7 (plants) adds its line HERE at merge: ``plants.register(registry)`` ──
    return registry
