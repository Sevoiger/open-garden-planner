"""The ground bake against the 2D canvas (Phase 17 L1.5, #385) — gates G1, G2, G3,
G4, G5 and G8 of the story, the pins of its addendum and the §8.10 integration
test: a real scene, a real plan, an edit session through the app's own commands,
and a recording sink that receives the tiles.

Every parity number in here is the bake against the canvas's OWN render of the
same items (``ground_bake_support.canvas_render``, pattern: docs §8.10.8). The
render tier's twin is ``test_scene3d_ground_render.py``.
"""

from __future__ import annotations

import ast
import math
import statistics
import time
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PyQt6.QtCore import QCoreApplication, QPointF
from PyQt6.QtGui import QColor
from PyQt6.QtTest import QSignalSpy

from open_garden_planner.core import ProjectManager
from open_garden_planner.core.commands import DeleteItemsCommand, MoveItemsCommand
from open_garden_planner.core.fill_patterns import FillPattern, create_pattern_brush
from open_garden_planner.core.object_types import ObjectType, PathFenceStyle, StrokeStyle
from open_garden_planner.core.scene3d import (
    MEADOW_RGB,
    BuilderRegistry,
    GroundSpec,
    GroundTile,
    MeshPart,
    Record,
    SceneDiff,
    changed_tiles,
    prism_mesh,
)
from open_garden_planner.core.scene3d.builders import production_registry
from open_garden_planner.services.scene_rendering import render_scene_region
from open_garden_planner.ui.canvas.arrange import build_arrange_command
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.canvas.items import (
    CircleItem,
    EllipseItem,
    PolygonItem,
    PolylineItem,
    RectangleItem,
)
from open_garden_planner.ui.view3d.ground_bake import GroundBaker
from open_garden_planner.ui.view3d.snapshot import SkipReason, snapshot_records
from tests.integration.ground_bake_support import (
    Bake,
    canvas_render,
    canvas_rgb,
    difference,
    ncc,
    tiles_rgba,
)

REPO = Path(__file__).resolve().parents[2]
PLANS = REPO / "tests" / "fixtures" / "plans"
#: The gate's bound: mean absolute difference per channel, 0..255.
PARITY = 6.0

# A plan of 1100 x 700 cm: 3 x 2 tiles at 1 px/cm, with borders at east 512 and 1024
# and at north 512. The two positions put an item of 300 x 200 across the first
# border, and across the corner where four tiles meet.
PLAN_W, PLAN_D = 1100.0, 700.0
ACROSS_A_BORDER = (380.0, 150.0)
ACROSS_A_CORNER = (400.0, 420.0)
POSITIONS = {"border": ACROSS_A_BORDER, "corner": ACROSS_A_CORNER}
TINT = QColor(200, 40, 60, 200)  # a custom colour, and translucent


def new_scene() -> CanvasScene:
    return CanvasScene(width_cm=PLAN_W, height_cm=PLAN_D)


def style(item: Any, pattern: FillPattern, color: QColor | None) -> None:
    """Give an item a fill the way the properties panel does: the stored pattern
    and colour, and the brush made from both."""
    if color is None:
        color = QColor(item.fill_color)  # the object type's own colour: "untinted"
    item.fill_pattern = pattern
    item.fill_color = color
    item.setBrush(create_pattern_brush(pattern, color))


def compare(scene: CanvasScene, registry: BuilderRegistry | None = None
            ) -> tuple[float, int, Bake]:
    """Bake ``scene`` through the pipeline and hold it against the canvas's render:
    ``(mean, max)`` difference, and the bake."""
    bake = Bake(scene, registry=registry, bare_rgb=canvas_rgb())
    bake.step()
    assert bake.skipped == [] and bake.baker.failures == {}
    mean, worst = difference(bake.image(), canvas_render(scene, bake.grid))
    return mean, worst, bake


# ── G1: parity with the canvas, per pattern ───────────────────────────────────


class TestParityPerPattern:
    """G1 — the bake against the 2D canvas's full-detail render of the same flat
    item: for each of the 24 fill patterns (and the solid fill), tinted and
    untinted, turned and not, at two positions that straddle tiles."""

    @pytest.mark.parametrize("position", sorted(POSITIONS))
    @pytest.mark.parametrize("angle", [0.0, 33.0])
    @pytest.mark.parametrize("tinted", [False, True], ids=["untinted", "tinted"])
    @pytest.mark.parametrize("pattern", list(FillPattern), ids=lambda p: p.name)
    def test_a_rectangle_is_painted_as_the_canvas_paints_it(
            self, qtbot: Any, pattern: FillPattern, tinted: bool, angle: float,
            position: str) -> None:
        scene = new_scene()
        x, y = POSITIONS[position]
        item = RectangleItem(x, y, 300.0, 200.0, object_type=ObjectType.GENERIC_RECTANGLE)
        style(item, pattern, TINT if tinted else None)
        scene.addItem(item)
        if angle:
            item._apply_rotation(angle)
        mean, worst, bake = compare(scene)
        assert len({tile.key for tile in changed_tiles((), bake.tiles)[0]}) == 6
        assert len(bake.baker.painted_ids()) == 1
        assert mean <= PARITY, (pattern.name, mean, worst)
        # measured (docs §8.26.5): not turned, the two renders are the same picture;
        # turned, single values differ by one step of 255 (largest mean seen: 0.0002)
        assert mean <= (0.002 if angle else 0.0), (pattern.name, mean, worst)
        assert worst <= (2 if angle else 0), (pattern.name, mean, worst)

    def test_all_24_textures_and_the_solid_fill_are_covered(self) -> None:
        assert len(FillPattern) == 25 and FillPattern.SOLID in FillPattern
