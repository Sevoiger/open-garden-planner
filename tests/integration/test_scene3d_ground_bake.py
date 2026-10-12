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


def on_layer(scene: CanvasScene, *items: Any) -> None:
    """Add items the way a drawing tool does: on the scene's active layer."""
    for item in items:
        item.layer_id = scene.active_layer.id
        scene.addItem(item)


def style(item: Any, pattern: FillPattern, color: QColor | None) -> None:
    """Give an item a fill the way the properties panel does: the stored pattern
    and colour, and the brush made from both."""
    if color is None:
        color = QColor(item.fill_color)  # the object type's own colour: "untinted"
    item.fill_pattern = pattern
    item.fill_color = color
    item.setBrush(create_pattern_brush(pattern, color))


def compare(scene: CanvasScene, registry: BuilderRegistry | None = None, *,
            tile_px: int = 512) -> tuple[float, int, Bake]:
    """Bake ``scene`` through the pipeline and hold it against the canvas's render:
    ``(mean, max)`` difference, and the bake. ``tile_px=2048`` makes the plan ONE
    tile — the bake without a tile border in it."""
    bake = Bake(scene, registry=registry,
                baker=GroundBaker(bare_rgb=canvas_rgb(), tile_px=tile_px))
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


# ── G1, continued: every shape, every path style, sprites, pens ───────────────


def _rectangle(position: str = "border", kind: ObjectType = ObjectType.GENERIC_RECTANGLE) -> Any:
    x, y = POSITIONS[position]
    return RectangleItem(x, y, 300.0, 200.0, object_type=kind)


def _polygon(position: str = "border") -> Any:
    x, y = POSITIONS[position]
    corners = [(0, 0), (300, 0), (300, 80), (120, 80), (120, 200), (0, 200)]  # a concave L
    return PolygonItem([QPointF(x + dx, y + dy) for dx, dy in corners],
                       object_type=ObjectType.TERRACE_PATIO)


def _ellipse(position: str = "border") -> Any:
    x, y = POSITIONS[position]
    return EllipseItem(x, y, 300.0, 180.0, object_type=ObjectType.GENERIC_ELLIPSE)


def _circle(position: str = "border") -> Any:
    x, y = POSITIONS[position]
    return CircleItem(x + 140.0, y + 100.0, 110.0, object_type=ObjectType.POND_POOL)


def _star(position: str = "border") -> Any:
    """A pentagram: its outline crosses itself, and the canvas leaves its centre
    open (the odd-even rule) where the solid path's resolved regions fill it."""
    x, y = POSITIONS[position]
    cx, cy = x + 140.0, y + 100.0
    points = [QPointF(cx + 130.0 * math.cos(math.radians(90.0 + 144.0 * k)),
                      cy + 130.0 * math.sin(math.radians(90.0 + 144.0 * k))) for k in range(5)]
    return PolygonItem(points, object_type=ObjectType.LAWN)


def _path(style: PathFenceStyle, position: str = "border", width: float | None = None) -> Any:
    x, y = POSITIONS[position]
    points = [QPointF(x - 60.5, y + 10.25), QPointF(x + 150.0, y + 60.0),
              QPointF(x + 170.3, y + 190.7), QPointF(x + 330.0, y + 150.0)]
    item = PolylineItem(points, object_type=ObjectType.PATH, path_fence_style=style)
    if width is not None:
        pen = item.pen()
        pen.setWidthF(width)
        item.setPen(pen)
    return item


SHAPES_UNDER_TEST = {"rectangle": _rectangle, "polygon": _polygon, "ellipse": _ellipse,
                     "circle": _circle, "star": _star}


class TestParityPerShape:
    @pytest.mark.parametrize("position", sorted(POSITIONS))
    @pytest.mark.parametrize("angle", [0.0, 33.0, 215.0])
    @pytest.mark.parametrize("shape", ["rectangle", "polygon", "ellipse", "star"])
    def test_every_filled_shape_with_a_texture(self, qtbot: Any, shape: str, angle: float,
                                               position: str) -> None:
        scene = new_scene()
        item = SHAPES_UNDER_TEST[shape](position)
        style(item, FillPattern.BRICK, TINT)
        scene.addItem(item)
        if angle:
            item._apply_rotation(angle)
        mean, worst, _bake = compare(scene)
        assert mean <= (0.002 if angle else 0.0) and worst <= (2 if angle else 0), (mean, worst)

    @pytest.mark.parametrize("position", sorted(POSITIONS))
    def test_a_circle_with_a_texture(self, qtbot: Any, position: str) -> None:
        scene = new_scene()
        item = _circle(position)  # a pond: the WATER texture, the type's own colour
        scene.addItem(item)
        mean, worst, bake = compare(scene)
        assert (mean, worst) == (0.0, 0)
        assert bake.records[str(item.item_id)].material.pattern == "WATER"

    def test_a_turned_circle_keeps_its_outline_and_a_solid_fill(self, qtbot: Any) -> None:
        scene = new_scene()
        item = _circle()
        style(item, FillPattern.SOLID, QColor(40, 90, 200))
        scene.addItem(item)
        item._apply_rotation(33.0)
        mean, _worst, _bake = compare(scene)
        # the same disc; the canvas draws its outline's four arcs from another angle,
        # and the antialiased rim differs by a few steps (measured: mean 0.015)
        assert mean <= 0.05

    def test_known_limit_the_texture_of_a_turned_circle_does_not_turn(self, qtbot: Any) -> None:
        """A circle's record carries no rotation (scene contract: a circle turned
        about its centre changes nothing), so the bake paints its fill texture
        unturned. The SHAPE is exact — every difference lies inside the disc —
        and the number is what the limit costs for the busiest texture."""
        scene = new_scene()
        item = _circle()
        style(item, FillPattern.BRICK, None)
        scene.addItem(item)
        item._apply_rotation(33.0)
        bake = Bake(scene, bare_rgb=canvas_rgb())
        bake.step()
        baked, reference = bake.image(), canvas_render(scene, bake.grid)
        mean, _worst = difference(baked, reference)
        assert 0.05 < mean <= PARITY  # measured 0.9 over the plan; the disc is 5 % of it
        centre = bake.records[str(item.item_id)].transform
        rows, columns = np.nonzero(np.abs(baked[..., :3] - reference[..., :3]).sum(axis=2))
        east, north = columns + 0.5, PLAN_D - (rows + 0.5)
        assert float(np.hypot(east - centre.east_cm, north - centre.north_cm).max()) <= 112.5

    @pytest.mark.parametrize("angle", [0.0, 33.0])
    @pytest.mark.parametrize("stroke", list(StrokeStyle), ids=lambda s: s.name)
    def test_the_outline_pen(self, qtbot: Any, stroke: StrokeStyle, angle: float) -> None:
        scene = new_scene()
        item = _rectangle("corner")
        style(item, FillPattern.SOLID, QColor(250, 240, 180))
        pen = item.pen()
        pen.setColor(QColor(20, 60, 160, 220))
        pen.setWidthF(7.5)
        pen.setStyle(stroke.to_qt_pen_style())
        item.setPen(pen)  # the pen alone, as the project loader sets it
        scene.addItem(item)
        if angle:
            item._apply_rotation(angle)
        mean, worst, bake = compare(scene)
        record = bake.records[str(item.item_id)]
        assert record.material.stroke_rgba == (20, 60, 160, 220)  # the LIVE pen's colour
        assert record.material.paint.stroke_width_cm == 7.5
        assert mean <= (0.002 if angle else 0.0) and worst <= (2 if angle else 0), (mean, worst)

    def test_a_layer_at_half_opacity(self, qtbot: Any) -> None:
        scene = new_scene()
        under, over = _rectangle("border"), _rectangle("corner")
        style(under, FillPattern.GRAVEL, None)
        style(over, FillPattern.WOOD, TINT)
        scene.addItem(under)
        scene.addItem(over)
        over.setOpacity(0.5)
        mean, worst, bake = compare(scene)
        assert bake.records[str(over.item_id)].material.paint.opacity == 0.5
        assert mean <= 0.002 and worst <= 1, (mean, worst)


class TestParityOfPaths:
    @pytest.mark.parametrize("position", sorted(POSITIONS))
    @pytest.mark.parametrize("angle", [0.0, 33.0])
    @pytest.mark.parametrize("path_style", list(PathFenceStyle), ids=lambda s: s.name)
    def test_every_path_and_fence_style(self, qtbot: Any, path_style: PathFenceStyle,
                                        angle: float, position: str) -> None:
        """One implementation paints a styled path on the canvas and on the ground
        (``path_style_paint``); what could still differ is the frame it is painted
        in — two styles derive a colour from the item's own coordinates."""
        scene = new_scene()
        item = _path(path_style, position, width=30.0)
        scene.addItem(item)
        if angle:
            item._apply_rotation(angle)
        # ONE tile: the bake IS the canvas's render, bit for bit, turned or not
        assert compare(scene, tile_px=2048)[:2] == (0.0, 0)
        # six tiles: everything wider than a pixel still is; a HAIRLINE (the 0.5 to 1 cm
        # lines of a border, a bar, a mesh) is clipped at each tile's edge by Qt's line
        # rasteriser, which then steps along it from another start — a shift of less
        # than 1/64 px that shows as single pixels (measured: mean <= 0.016, one pixel
        # up to 142 of 255)
        mean, _worst, bake = compare(scene)
        record = bake.records[str(item.item_id)]
        assert record.height_cm is None and record.material.paint.stroke_width_cm == 0.0
        assert mean <= 0.05, (path_style.name, mean)

    def test_a_moved_path_keeps_its_stones_colours(self, qtbot: Any) -> None:
        """Stepping stones are tinted from their ITEM coordinates: a move must not
        re-colour them, on the canvas or on the ground."""
        scene = new_scene()
        item = _path(PathFenceStyle.STEPPING_STONES, width=40.0)
        scene.addItem(item)
        item.moveBy(63.25, 41.5)
        assert compare(scene, tile_px=2048)[:2] == (0.0, 0)
        mean, worst, _bake = compare(scene)
        assert mean <= 0.001 and worst <= 2


class TestParityOfSprites:
    @pytest.mark.parametrize("angle", [0.0, 33.0])
    @pytest.mark.parametrize("kind", ["FIRE_PIT", "RAIN_BARREL", "PERGOLA"])
    def test_a_furniture_item_without_a_height_is_its_sprite_on_the_ground(
            self, qtbot: Any, kind: str, angle: float) -> None:
        scene = new_scene()
        x, y = ACROSS_A_CORNER
        object_type = ObjectType[kind]
        if kind == "PERGOLA":
            item: Any = RectangleItem(x + 0.4, y + 0.7, 300.0, 200.0, object_type=object_type)
        else:
            item = CircleItem(x + 110.3, y + 90.6, 75.0, object_type=object_type)
        scene.addItem(item)
        if angle:
            item._apply_rotation(angle)
        mean, worst, bake = compare(scene, production_registry())
        assert bake.baker.painted_ids() == (str(item.item_id),)
        assert bake.image()[..., :3].std() > 5.0  # a picture, not a flat fill
        if angle and kind != "PERGOLA":
            assert mean <= PARITY  # a circle's sprite does not turn either (known limit)
        else:
            # a sprite is a pixmap drawn through a flip: at a tile's edge its smooth
            # scaling rounds a value differently now and then
            assert mean <= (0.01 if angle else 0.002) and worst <= (16 if angle else 2), (mean, worst)


# ── what is on the ground and what is not ─────────────────────────────────────


class TestWhatIsPainted:
    def test_bare_ground_is_the_one_meadow_albedo(self, qtbot: Any) -> None:
        from open_garden_planner.spike_q3d import runner
        from open_garden_planner.ui.view3d.quick3d.look import NEUTRAL_DAY

        scene = new_scene()
        bake = Bake(scene)  # the product's default: no colour handed in
        bake.step()
        image = bake.image()
        assert {tuple(int(c) for c in pixel) for pixel in image.reshape(-1, 4)[::997]} == {
            (*MEADOW_RGB, 255)}
        assert GroundBaker().bare_rgb == MEADOW_RGB
        assert "#{:02x}{:02x}{:02x}".format(*MEADOW_RGB) == NEUTRAL_DAY.meadow_albedo \
            == runner.MEADOW_ALBEDO
        assert canvas_rgb() != MEADOW_RGB  # never the canvas's beige

    def test_a_solid_is_not_painted_and_the_lawn_runs_on_under_it(self, qtbot: Any) -> None:
        scene = new_scene()
        lawn = RectangleItem(100.0, 100.0, 800.0, 500.0, object_type=ObjectType.LAWN)
        shed = RectangleItem(450.0, 300.0, 200.0, 150.0, object_type=ObjectType.GARAGE_SHED)
        scene.addItem(lawn)
        scene.addItem(shed)
        with_shed = Bake(scene, registry=production_registry())
        with_shed.step()
        assert with_shed.sync.solid_ids == {str(shed.item_id)}
        assert with_shed.baker.painted_ids() == (str(lawn.item_id),)
        scene.removeItem(shed)
        lawn_alone = Bake(scene, registry=production_registry())
        lawn_alone.step()
        assert np.array_equal(with_shed.image(), lawn_alone.image())

    def test_a_plant_is_never_painted(self, qtbot: Any) -> None:
        """Not even one without a height under a registry that builds nothing for
        it: a plant is the plant builder's, never its 2D sprite laid on the lawn."""
        scene = new_scene()
        tree = CircleItem(550.0, 350.0, 120.0, object_type=ObjectType.TREE)
        scene.addItem(tree)
        bake = Bake(scene)  # the default registry: no plant builder
        bake.step()
        record = bake.records[str(tree.item_id)]
        assert record.height_cm is None and record.material.paint is None
        assert "paint_origin" not in record.params
        assert bake.sync.solid_ids == frozenset() and bake.baker.painted_ids() == ()

    def test_an_item_a_builder_makes_a_solid_for_leaves_the_ground(self, qtbot: Any) -> None:
        scene = new_scene()
        lawn = _rectangle("corner", ObjectType.LAWN)
        scene.addItem(lawn)
        registry = BuilderRegistry()
        bake = Bake(scene, registry=registry, bare_rgb=canvas_rgb())
        bake.step()
        assert bake.baker.painted_ids() == (str(lawn.item_id),)

        def slab(record: Record) -> tuple[MeshPart, ...]:
            return (MeshPart(prism_mesh(record.footprints, 4.0), "vc"),)

        registry.register("LAWN", slab)
        bake.step()
        assert bake.baker.painted_ids() == () and bake.sync.solid_ids == {str(lawn.item_id)}
        assert bake.sink.state() == bake.sync.expected_state()

    def test_an_item_beside_the_plan_is_not_on_the_ground(self, qtbot: Any) -> None:
        scene = new_scene()
        scene.addItem(RectangleItem(PLAN_W + 300.0, 100.0, 200.0, 100.0,
                                    object_type=ObjectType.LAWN))
        bake = Bake(scene)
        bake.step()
        assert bake.baker.painted_ids() == () and len(bake.tiles) == 6


# ── G8: stacking ──────────────────────────────────────────────────────────────


class TestStacking:
    def test_overlapping_items_paint_in_the_canvas_order_and_follow_an_arrange(
            self, qtbot: Any) -> None:
        """G8. Two overlapping flat items: the later one is on top; "bring to
        front" re-bakes the tiles they share, and the bake matches the canvas
        before and after."""
        from open_garden_planner.core.stacking import ArrangeMode

        scene = new_scene()
        red = RectangleItem(380.0, 150.0, 300.0, 200.0, object_type=ObjectType.GENERIC_RECTANGLE)
        blue = RectangleItem(500.0, 250.0, 300.0, 200.0, object_type=ObjectType.GENERIC_RECTANGLE)
        style(red, FillPattern.SOLID, QColor(220, 30, 30))
        style(blue, FillPattern.SOLID, QColor(30, 30, 220))
        on_layer(scene, red, blue)
        bake = Bake(scene, bare_rgb=canvas_rgb())
        bake.step()
        row, column = int(PLAN_D - 300.0), 600  # east 600, north 300: a point both cover
        assert tuple(bake.image()[row, column, :3]) == (30, 30, 220)  # blue, added last
        assert difference(bake.image(), canvas_render(scene, bake.grid)) == (0.0, 0)
        before = bake.tiles

        command, _outcome = build_arrange_command(scene, [red], ArrangeMode.BRING_TO_FRONT)
        assert command is not None
        command.execute()
        result = bake.step()
        assert result == SceneDiff(reordered=True)  # no record changed: only their order
        assert tuple(bake.image()[row, column, :3]) == (220, 30, 30)
        assert difference(bake.image(), canvas_render(scene, bake.grid)) == (0.0, 0)
        uploaded, dropped = changed_tiles(before, bake.tiles)
        # the two items share tiles (0, 0) and (1, 0), and overlap in both: those two
        # are painted anew and sent, and no other
        assert dropped == () and {tile.key for tile in uploaded} == {(0, 0), (1, 0)}
        assert set(bake.baker.last_repainted) == {(0, 0), (1, 0)}
        command.undo()
        bake.step()
        assert tuple(bake.image()[row, column, :3]) == (30, 30, 220)
        assert bake.sink.state() == bake.sync.expected_state()

    def test_a_reorder_of_items_that_do_not_share_a_tile_repaints_nothing(self, qtbot: Any) -> None:
        from open_garden_planner.core.stacking import ArrangeMode

        scene = new_scene()
        west = RectangleItem(50.0, 50.0, 200.0, 150.0, object_type=ObjectType.LAWN)
        east = RectangleItem(700.0, 50.0, 200.0, 150.0, object_type=ObjectType.LAWN)
        on_layer(scene, west, east)
        bake = Bake(scene)
        bake.step()
        command, _outcome = build_arrange_command(scene, [west], ArrangeMode.BRING_TO_FRONT)
        assert command is not None
        command.execute()
        assert bake.step().reordered
        assert bake.baker.last_repainted == ()


# ── G4: the live scene is never touched ───────────────────────────────────────


class TestTheLiveSceneIsLeftAlone:
    def _scene_with_a_selection(self) -> tuple[CanvasScene, list[Any]]:
        scene = CanvasScene()
        ProjectManager().load(scene, PLANS / "bench_small.ogp")
        flat = [item for item in scene.items()
                if getattr(getattr(item, "object_type", None), "name", "") in ("LAWN", "POND_POOL")]
        assert len(flat) == 2
        for item in flat:
            item.setSelected(True)
        QCoreApplication.processEvents()  # what loading and selecting queued is delivered
        return scene, flat

    def test_no_selection_change_no_visibility_change_no_changed_signal(self, qtbot: Any) -> None:
        """G4. Full bakes and incremental ones, with a selection active."""
        scene, flat = self._scene_with_a_selection()
        visible = {id(item): item.isVisible() for item in scene.items()}
        selection, changed = QSignalSpy(scene.selectionChanged), QSignalSpy(scene.changed)
        bake = Bake(scene, registry=production_registry())
        bake.step()
        assert len(bake.baker.last_repainted) == 20 and len(bake.baker.painted_ids()) == 11
        bake.baker.clear()
        bake.step()  # a second full bake
        bake.sync.reset()
        bake.step()  # … and one after a reset
        QCoreApplication.processEvents()
        assert len(selection) == 0 and len(changed) == 0
        assert {id(item): item.isVisible() for item in scene.items()} == visible
        assert scene.selectedItems() and set(scene.selectedItems()) == set(flat)
        assert scene.labels_enabled and scene.shadows_enabled  # what a 2D render switches off

    def test_the_spies_see_what_the_old_way_did(self, qtbot: Any) -> None:
        """Positive control: baking by rendering the live scene — what the L0 spike
        did — clears the selection and makes the scene report a change."""
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QImage, QPainter

        scene, _flat = self._scene_with_a_selection()
        selection = QSignalSpy(scene.selectionChanged)
        image = QImage(240, 160, QImage.Format.Format_ARGB32)
        painter = QPainter(image)
        render_scene_region(scene, painter, QRectF(0, 0, 240, 160), QRectF(0, 0, 2400, 1600))
        painter.end()
        QCoreApplication.processEvents()
        assert len(selection) >= 1


# ── G3: one small edit ────────────────────────────────────────────────────────


class TimedBaker(GroundBaker):
    """The baker, with a stopwatch around ``bake`` alone."""

    def __init__(self) -> None:
        super().__init__()
        self.milliseconds: list[float] = []

    def bake(self, records: Mapping[str, Record], changes: SceneDiff | None,
             solid: Collection[str], width_cm: float, depth_cm: float
             ) -> tuple[GroundTile, ...]:
        started = time.perf_counter()
        try:
            return super().bake(records, changes, solid, width_cm, depth_cm)
        finally:
            self.milliseconds.append((time.perf_counter() - started) * 1000.0)


def _loaded(plan: str) -> CanvasScene:
    scene = CanvasScene()
    ProjectManager().load(scene, PLANS / plan)
    return scene


def _flat_item_up_to_three_metres(scene: CanvasScene, bake: Bake) -> Any:
    """The largest painted item of the plan that is no longer than 3 m."""
    painted = set(bake.baker.painted_ids())
    best: tuple[float, Any] | None = None
    for item in scene.items():
        if str(getattr(item, "item_id", "")) not in painted:
            continue
        rect = item.sceneBoundingRect()
        size = max(rect.width(), rect.height())
        if size <= 300.0 and (best is None or size > best[0]):
            best = (size, item)
    assert best is not None
    return best[1]


class TestOneSmallEdit:
    @pytest.mark.parametrize(("plan", "tiles", "painted"), [("bench_small.ogp", 20, 11),
                                                           ("bench_large.ogp", 60, 45)])
    def test_a_move_repaints_at_most_four_tiles_in_ten_milliseconds(
            self, qtbot: Any, plan: str, tiles: int, painted: int) -> None:
        """G3. Move one flat item of at most 3 m, 31 times: every move re-paints at
        most four tiles, looks at ONE record, and leaves every other tile the very
        object it was; the bake alone takes at most 10 ms in the median."""
        scene = _loaded(plan)
        baker = TimedBaker()
        bake = Bake(scene, registry=production_registry(), baker=baker)
        bake.step()
        assert len(bake.tiles) == tiles and len(baker.painted_ids()) == painted
        item = _flat_item_up_to_three_metres(scene, bake)
        full_ms = baker.milliseconds[-1]
        baker.milliseconds.clear()
        most_tiles = most_records = 0
        for index in range(31):
            before = {tile.key: tile for tile in bake.tiles}
            painted_before = baker.records_painted
            delta = QPointF(37.5, -22.25) if index % 2 == 0 else QPointF(-37.5, 22.25)
            MoveItemsCommand([item], delta).execute()
            result = bake.step()
            assert result == SceneDiff(transform=(str(item.item_id),))
            assert baker.last_examined == 1
            repainted = set(baker.last_repainted)
            most_tiles = max(most_tiles, len(repainted))
            most_records = max(most_records, baker.records_painted - painted_before)
            assert 1 <= len(repainted) <= 4
            for tile in bake.tiles:
                if tile.key not in repainted:
                    assert tile is before[tile.key]  # untouched: the same object, the same bits
            uploaded, dropped = changed_tiles(tuple(before.values()), bake.tiles)
            assert dropped == () and {tile.key for tile in uploaded} <= repainted
        median = statistics.median(baker.milliseconds)
        print(f"\nG3 {plan}: full bake {full_ms:.1f} ms; per move: median {median:.2f} ms, "
              f"max {max(baker.milliseconds):.2f} ms, at most {most_tiles} tiles and "
              f"{most_records} record paints")
        assert median <= 10.0
        assert bake.sink.state().ground == bake.sync.expected_state().ground
