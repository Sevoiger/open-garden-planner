"""Plants stand on their parent (Phase 17 L1.6, #385 — gates G3 and G4's drift guard).

Truth gate "Base" (``ogp-lush-cinematic`` §1): a plant in a raised bed or a
container stands on the soil, not inside the box. The rule lives ONCE —
``core.object_height.standing_base_cm`` behind ``ui.canvas.footprints.item_base_cm``
— and is asked by

- ``snapshot_records`` → a record's ``transform.base_cm`` (3D), and
- ``collect_shadow_casters`` → ``ShadowCaster.base_cm`` (the 2D shadow overlay
  and the hours-of-sun heatmap).

Everything here goes through a real scene, real items and the app's own commands.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import math
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PyQt6.QtCore import QPointF

import open_garden_planner
from open_garden_planner.core import ProjectManager
from open_garden_planner.core.commands import (
    ChangePropertyCommand,
    CreateItemCommand,
    MoveItemsCommand,
    ResizeItemCommand,
    SetParentBedCommand,
)
from open_garden_planner.core.object_height import METADATA_KEY, SOIL_DROP_CM
from open_garden_planner.core.object_types import ObjectType
from open_garden_planner.core.scene3d import (
    BuilderRegistry,
    MeshPart,
    Record,
    RecordingSink,
    SceneDiff,
    SceneSync,
    default_builder,
    diff,
)
from open_garden_planner.core.shade_aggregation import (
    SAMPLE_STEP_MINUTES,
    HeatmapGrid,
    daylight_samples,
)
from open_garden_planner.core.shadow_geometry import (
    ShadowCaster,
    compute_scene_shadows,
    shadow_direction_scene,
)
from open_garden_planner.core.solar import solar_position
from open_garden_planner.ui.canvas import footprints, sun_heatmap, sun_shadow_controller
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.canvas.canvas_view import CanvasView
from open_garden_planner.ui.canvas.geometry_apply import (
    apply_rect_like_geometry,
    apply_rotation,
    build_rect_resize,
)
from open_garden_planner.ui.canvas.items.circle_item import CircleItem
from open_garden_planner.ui.canvas.items.polygon_item import PolygonItem
from open_garden_planner.ui.canvas.items.rectangle_item import RectangleItem
from open_garden_planner.ui.canvas.sun_heatmap import SunHeatmapController
from open_garden_planner.ui.canvas.sun_shadow_controller import (
    SunShadowController,
    SunShadowOverlayItem,
    collect_shadow_casters,
)
from open_garden_planner.ui.view3d import snapshot as snapshot_module
from open_garden_planner.ui.view3d.snapshot import snapshot_records

PLANS = Path(__file__).resolve().parents[1] / "fixtures" / "plans"
JUNE = date(2026, 6, 21)
BERLIN = {"latitude": 52.52, "longitude": 13.405}
BEGIN, COMMIT = ("begin", None), ("commit", None)


class Garden:
    """A raised bed with one perennial on it, created through the command path."""

    def __init__(self, view: CanvasView, parent_type: ObjectType = ObjectType.RAISED_BED) -> None:
        self.view = view
        self.scene = view.scene()
        self.commands = view.command_manager
        layer = self.scene.active_layer.id
        if parent_type is ObjectType.CONTAINER_ROUND:
            self.bed: Any = CircleItem(500.0, 400.0, 60.0, object_type=parent_type, layer_id=layer)
        elif parent_type is ObjectType.GARDEN_BED:
            self.bed = PolygonItem(
                [QPointF(400.0, 300.0), QPointF(700.0, 300.0), QPointF(700.0, 500.0),
                 QPointF(400.0, 500.0)], object_type=parent_type, layer_id=layer)
        else:
            self.bed = RectangleItem(400.0, 300.0, 300.0, 200.0, object_type=parent_type,
                                     layer_id=layer)
        self.plant = CircleItem(500.0, 400.0, 15.0, object_type=ObjectType.PERENNIAL, layer_id=layer)
        self.plant.metadata[METADATA_KEY] = 60.0
        self.commands.execute(CreateItemCommand(self.scene, self.bed, "bed"))
        self.commands.execute(CreateItemCommand(self.scene, self.plant, "plant"))  # auto-parents
        self.bed_id, self.plant_id = str(self.bed.item_id), str(self.plant.item_id)

    def records(self) -> dict[str, Record]:
        return snapshot_records(self.scene)

    def base(self) -> float:
        return self.records()[self.plant_id].transform.base_cm

    def caster_base(self) -> float:
        (caster,) = [c for c in collect_shadow_casters(self.scene) if c.height_cm == 60.0]
        return caster.base_cm


class TestGateG3:
    def test_a_plant_in_a_40_cm_raised_bed_stands_at_38(self, canvas: CanvasView) -> None:
        """The issue's "plant in a 40 cm bed at base 40" and the style contract's
        "base = parent's effective height (- 2 cm soil drop)" are one gate: the bed
        is 40 cm tall, the soil lies 2 cm below its rim, the plant stands on the soil."""
        garden = Garden(canvas)
        records = garden.records()
        assert garden.plant.parent_bed_id == garden.bed.item_id  # the PROPERTY, set by the command
        assert "parent_bed_id" not in garden.plant.metadata
        assert records[garden.bed_id].height_cm == 40.0 and SOIL_DROP_CM == 2.0
        assert records[garden.plant_id].parent_id == garden.bed_id
        assert records[garden.plant_id].transform.base_cm == 38.0
        assert records[garden.plant_id].base_cm == 38.0
        assert records[garden.bed_id].transform.base_cm == 0.0  # the bed stands on the ground
        # the plant's own height is measured from its soil: it occupies 38 … 98 cm
        assert records[garden.plant_id].height_cm == 60.0

    def test_a_bed_height_change_reaches_the_plant_as_a_transform_only(self, canvas: CanvasView) -> None:
        garden = Garden(canvas)
        built: list[str] = []

        def build(record: Record) -> tuple[MeshPart, ...]:
            built.append(record.item_id)
            return default_builder(record)

        sink = RecordingSink()
        sync = SceneSync(sink, BuilderRegistry(default=build), validate=True)
        sync.apply(garden.records())
        assert sink.item(garden.plant_id).transform.base_cm == 38.0
        plant_parts = sink.item(garden.plant_id).parts
        sink.clear_calls()
        built.clear()

        garden.bed.metadata[METADATA_KEY] = 75.0  # the bed grows from 40 to 75 cm
        result = sync.apply(garden.records())
        assert result == SceneDiff(transform=(garden.plant_id,), geometry=(garden.bed_id,))
        assert sink.ops() == [BEGIN, ("replace_geometry", garden.bed_id),
                              ("update_transform", garden.plant_id), COMMIT]
        assert built == [garden.bed_id]  # the plant's mesh was NOT rebuilt
        held = sink.item(garden.plant_id)
        assert held.transform.base_cm == 73.0 and held.parts is plant_parts
        assert float(sink.item(garden.bed_id).parts[0].mesh.bounds()[1][2]) == 75.0

    def test_moving_the_bed_does_not_change_the_base(self, canvas: CanvasView) -> None:
        garden = Garden(canvas)
        before = garden.records()
        garden.commands.execute(MoveItemsCommand([garden.bed, garden.plant], QPointF(55.5, -20.25)))
        after = garden.records()
        assert diff(before, after) == SceneDiff(transform=(garden.bed_id, garden.plant_id))
        assert after[garden.plant_id].transform.base_cm == 38.0

    def test_the_base_is_on_the_record_grid(self, canvas: CanvasView) -> None:
        garden = Garden(canvas)
        garden.bed.metadata[METADATA_KEY] = 40.0 + 1.0 / 3.0
        assert garden.base() == round(40.0 + 1.0 / 3.0 - 2.0, 7)


PARENT_CASES = [
    # parent type, parent metadata, expected base
    (ObjectType.RAISED_BED, {}, 38.0),
    (ObjectType.RAISED_BED, {METADATA_KEY: 90.0}, 88.0),
    (ObjectType.CONTAINER, {}, 30.0),                      # a container: AT its fill height
    (ObjectType.CONTAINER, {"container_height_cm": 45.0}, 45.0),
    (ObjectType.WALL_PLANTER, {}, 30.0),
    (ObjectType.CONTAINER_ROUND, {}, 30.0),
    (ObjectType.GARDEN_BED, {}, 0.0),                      # in the ground
    (ObjectType.GARDEN_BED, {METADATA_KEY: 25.0}, 23.0),   # the user raised it
    (ObjectType.TRELLIS, {}, 0.0),                         # a plant parent without soil
]


class TestTheOneRule:
    @pytest.mark.parametrize(("parent_type", "metadata", "expected"), PARENT_CASES,
                             ids=lambda v: v.name if isinstance(v, ObjectType) else str(v))
    def test_3d_base_and_2d_caster_base_agree(
        self, canvas: CanvasView, parent_type: ObjectType, metadata: dict, expected: float
    ) -> None:
        garden = Garden(canvas, parent_type)
        garden.bed.metadata.update(metadata)
        assert garden.plant.parent_bed_id == garden.bed.item_id
        assert garden.base() == expected
        assert garden.caster_base() == expected

    def test_no_parent_is_the_ground(self, canvas: CanvasView) -> None:
        garden = Garden(canvas)
        loner = CircleItem(2000.0, 1500.0, 15.0, object_type=ObjectType.PERENNIAL)
        loner.metadata[METADATA_KEY] = 45.0
        garden.scene.addItem(loner)
        assert garden.records()[str(loner.item_id)].transform.base_cm == 0.0
        assert all(c.base_cm == 0.0 for c in collect_shadow_casters(garden.scene)
                   if c.height_cm == 45.0)

    def test_re_parenting_is_a_transform_change_and_nothing_else(self, canvas: CanvasView) -> None:
        garden = Garden(canvas)
        before = garden.records()
        garden.plant.parent_bed_id = None  # the link, not geometry
        loose = garden.records()
        assert diff(before, loose) == SceneDiff(transform=(garden.plant_id,))
        assert loose[garden.plant_id].transform.base_cm == 0.0
        garden.plant.parent_bed_id = garden.bed.item_id
        assert diff(loose, garden.records()) == SceneDiff(transform=(garden.plant_id,))
        assert garden.records() == before

    def test_a_dangling_parent_link_is_the_ground(self, canvas: CanvasView) -> None:
        garden = Garden(canvas)
        garden.scene.removeItem(garden.bed)
        assert garden.plant.parent_bed_id is not None
        assert garden.base() == 0.0 and garden.caster_base() == 0.0

    def test_a_hidden_parent_is_not_there(self, canvas: CanvasView) -> None:
        """A hidden bed has no shadow and no solid; its plants must not float above
        nothing. The rule follows what is visible, like the caster walk itself."""
        garden = Garden(canvas)
        garden.bed.setVisible(False)
        assert garden.base() == 0.0 and garden.caster_base() == 0.0
        garden.bed.setVisible(True)
        assert garden.base() == 38.0 and garden.caster_base() == 38.0

    def test_a_nonsense_parent_height_is_the_ground(self, canvas: CanvasView) -> None:
        garden = Garden(canvas)
        garden.bed.metadata[METADATA_KEY] = float("inf")
        assert garden.base() == 0.0 and garden.caster_base() == 0.0

    def test_the_base_follows_the_date_of_the_height_resolver(self, canvas: CanvasView) -> None:
        """``at_date`` reaches the parent's height exactly as it reaches the item's."""
        garden = Garden(canvas)
        seen: list[Any] = []
        original = footprints.standing_base_cm

        def spy(parent_type: Any, parent_metadata: Any, at_date: Any = None) -> float:
            seen.append(at_date)
            return original(parent_type, parent_metadata, at_date)

        footprints.standing_base_cm = spy  # type: ignore[assignment]
        try:
            snapshot_records(garden.scene, JUNE)
            collect_shadow_casters(garden.scene, at_date=JUNE)
        finally:
            footprints.standing_base_cm = original  # type: ignore[assignment]
        assert seen == [JUNE, JUNE]

    @pytest.mark.parametrize(("plan", "raised"), [("bench_small.ogp", 22), ("bench_large.ogp", 240)])
    def test_the_bench_plans(self, qtbot: Any, plan: str, raised: int) -> None:  # noqa: ARG002
        scene = CanvasScene()
        ProjectManager().load(scene, PLANS / plan)
        records = snapshot_records(scene, JUNE)
        bases = {rid: r.transform.base_cm for rid, r in records.items()}
        on_soil = [rid for rid, base in bases.items() if base > 0.0]
        assert len(on_soil) == raised
        expected = {"RAISED_BED": (40.0, 38.0), "CONTAINER_ROUND": (30.0, 30.0)}
        for rid in on_soil:
            parent = records[records[rid].parent_id or ""]
            assert (parent.height_cm, bases[rid]) == expected[parent.kind]
        in_ground = [r for r in records.values() if r.parent_id and r.transform.base_cm == 0.0]
        # an in-ground bed has no height, a trellis holds no soil: both are the ground
        assert {records[r.parent_id or ""].kind for r in in_ground} <= {"GARDEN_BED", "TRELLIS"}
        casters = collect_shadow_casters(scene, at_date=JUNE)
        assert sum(1 for c in casters if c.base_cm > 0.0) == raised
        assert {c.base_cm for c in casters} <= {0.0, 30.0, 38.0}


def _add(garden: Garden, item: Any, name: str) -> Any:
    garden.commands.execute(CreateItemCommand(garden.scene, item, name))
    return item


def _perennial(garden: Garden, x: float, y: float, height: float) -> CircleItem:
    plant = CircleItem(x, y, 15.0, object_type=ObjectType.PERENNIAL,
                       layer_id=garden.scene.active_layer.id)
    plant.metadata[METADATA_KEY] = height
    return plant


def _bases(garden: Garden, plant: Any) -> tuple[float, float]:
    """(3D record base, 2D caster base) of one plant, found by its unique height."""
    record = garden.records()[str(plant.item_id)]
    (caster,) = [c for c in collect_shadow_casters(garden.scene)
                 if c.height_cm == plant.metadata[METADATA_KEY]]
    return record.transform.base_cm, caster.base_cm


class TestTheLinkAloneNeverLifts:
    """Review round 1, P1-1. ``parent_bed_id`` is bookkeeping, not geometry: the app
    evaluates it when a plant is created or dropped and nowhere else, and
    ``set_parent_bed`` sets it on purpose without containment. A solid is lifted
    only when the link holds AND its centre is inside the parent's shape.

    (a) is the case that was new wrong output; (b)–(d) are the limits that remain
    — pinned so that reconciling bed membership (a follow-up issue) changes them
    knowingly."""

    def test_a_shrinking_the_bed_away_from_its_plant_puts_the_plant_on_the_ground(
        self, canvas: CanvasView
    ) -> None:
        """Expected base 0. WHY: the resize does not touch the link, so the plant
        keeps ``parent_bed_id`` — but it now stands on the lawn, 40 cm from a bed
        it is no longer in. Lifting it would start its shadow ``38 / tan α`` away
        from it (141.8 cm at 15°) with lit ground in between."""
        garden = Garden(canvas)  # bed x 400 … 700, plant centre at x = 500
        before = garden.records()
        assert _bases(garden, garden.plant) == (38.0, 38.0)

        old, new = build_rect_resize(garden.bed, 60.0, 200.0)  # the panel's and the agent's path
        garden.commands.execute(ResizeItemCommand(garden.bed, old, new, apply_rect_like_geometry))
        assert garden.bed.mapRectToScene(garden.bed.rect()).right() == 460.0
        assert garden.plant.parent_bed_id == garden.bed.item_id  # the link survives: bookkeeping
        assert not footprints.centre_is_inside(garden.plant, garden.bed)
        assert _bases(garden, garden.plant) == (0.0, 0.0)
        after = garden.records()
        # the bed: a new outline around a new centre; the plant: only its base (38 -> 0)
        assert diff(before, after) == SceneDiff(transform=(garden.bed_id, garden.plant_id),
                                                geometry=(garden.bed_id,))
        assert after[garden.plant_id].transform == dataclasses.replace(
            before[garden.plant_id].transform, base_cm=0.0)

        # the shadow starts AT the plant again: no lit gap between a plant and its own shadow
        (plant,) = [c for c in collect_shadow_casters(garden.scene) if c.height_cm == 60.0]
        direction = shadow_direction_scene(180.0)
        shadow = compute_scene_shadows([plant], 15.0, 180.0)
        near = min(x * direction[0] + y * direction[1] for polygon in shadow for x, y in polygon)
        footprint_far = max(x * direction[0] + y * direction[1] for x, y in plant.footprint)
        assert near < footprint_far  # round 0: near - footprint_far == 111.8 cm of lit ground

        garden.commands.undo()  # the bed is 3 m wide again: the plant is back on its soil
        assert _bases(garden, garden.plant) == (38.0, 38.0)
        assert garden.records() == before

    def test_b_a_bed_drawn_around_a_plant_does_not_lift_it(self, canvas: CanvasView) -> None:
        """Expected base 0 — a KNOWN LIMIT, not the wanted end state. WHY: membership
        is decided when a PLANT is created or dropped; creating a bed links nothing,
        so the plant has no parent, and without the link there is no base. The
        same-looking plan drawn bed-first gives 38. Fixing it means reconciling
        membership when a bed is created or resized (follow-up issue), not guessing
        a parent from geometry here."""
        scene = canvas.scene()
        layer = scene.active_layer.id
        plant = CircleItem(500.0, 400.0, 15.0, object_type=ObjectType.PERENNIAL, layer_id=layer)
        plant.metadata[METADATA_KEY] = 60.0
        canvas.command_manager.execute(CreateItemCommand(scene, plant, "plant"))
        bed = RectangleItem(400.0, 300.0, 300.0, 200.0, object_type=ObjectType.RAISED_BED,
                            layer_id=layer)
        canvas.command_manager.execute(CreateItemCommand(scene, bed, "bed"))
        assert footprints.centre_is_inside(plant, bed)  # geometrically it IS in the bed
        assert plant.parent_bed_id is None               # … but nothing linked it
        assert snapshot_records(scene)[str(plant.item_id)].transform.base_cm == 0.0
        assert [c.base_cm for c in collect_shadow_casters(scene) if c.height_cm == 60.0] == [0.0]

    def test_c_a_climber_on_a_trellis_inside_a_raised_bed_stays_on_the_ground(
        self, canvas: CanvasView
    ) -> None:
        """Expected: climber 0, its neighbour in the same bed 38 — a KNOWN LIMIT. WHY:
        a plant is linked to the SMALLEST parent that contains it
        (``find_smallest_bed_containing``), which is the trellis; a trellis holds no
        soil and has no base of its own (parents do not nest), so the rule has
        nothing to lift the climber onto."""
        garden = Garden(canvas)  # bed 400 … 700 x 300 … 500 with a plant at (500, 400)
        trellis = _add(garden, RectangleItem(560.0, 440.0, 120.0, 40.0, object_type=ObjectType.TRELLIS,
                                             layer_id=garden.scene.active_layer.id), "trellis")
        climber = _add(garden, _perennial(garden, 620.0, 460.0, 61.0), "climber")
        assert climber.parent_bed_id == trellis.item_id
        assert footprints.centre_is_inside(climber, garden.bed)  # it stands in the bed's box
        assert _bases(garden, climber) == (0.0, 0.0)
        assert _bases(garden, garden.plant) == (38.0, 38.0)      # 120 cm away, same bed
        assert garden.records()[str(trellis.item_id)].transform.base_cm == 0.0

    def test_d_a_pot_on_a_raised_bed_stands_on_the_ground_with_its_plant_at_the_pot_s_soil(
        self, canvas: CanvasView
    ) -> None:
        """Expected: plant 30 (the pot's soil: its fill height), pot 0 — a KNOWN LIMIT.
        WHY: only plants carry a parent link; a container has none, so it stands on the
        ground whatever it is drawn on, and its plant stands on the pot's own soil, not
        at 38 + 30 = 68."""
        garden = Garden(canvas)
        pot = _add(garden, CircleItem(620.0, 440.0, 30.0, object_type=ObjectType.CONTAINER_ROUND,
                                      layer_id=garden.scene.active_layer.id), "pot")
        potted = _add(garden, _perennial(garden, 620.0, 440.0, 61.0), "potted plant")
        assert potted.parent_bed_id == pot.item_id  # the smallest parent again
        records = garden.records()
        assert records[str(pot.item_id)].height_cm == 30.0
        assert records[str(pot.item_id)].transform.base_cm == 0.0
        assert records[str(pot.item_id)].parent_id is None
        assert _bases(garden, potted) == (30.0, 30.0)

    def test_an_explicit_link_without_containment_does_not_lift(self, canvas: CanvasView) -> None:
        """The agent's ``set_parent_bed`` and the panel's Link action may link a plant
        that is not in the bed (``link_is_geometric: false``). Expected base 0: the
        same function answers ``link_is_geometric`` and the lift."""
        garden = Garden(canvas)
        loner = _add(garden, _perennial(garden, 1500.0, 1200.0, 61.0), "loner")
        assert loner.parent_bed_id is None
        garden.commands.execute(SetParentBedCommand(garden.scene, loner, None, garden.bed.item_id))
        assert loner.parent_bed_id == garden.bed.item_id
        assert not footprints.centre_is_inside(loner, garden.bed)
        assert _bases(garden, loner) == (0.0, 0.0)

    def test_a_plant_dragged_inside_its_bed_keeps_its_base(self, canvas: CanvasView) -> None:
        garden = Garden(canvas)
        garden.commands.execute(MoveItemsCommand([garden.plant], QPointF(150.0, 60.0)))
        assert footprints.centre_is_inside(garden.plant, garden.bed)
        assert _bases(garden, garden.plant) == (38.0, 38.0)

    def test_a_turned_bed_is_tested_in_its_own_frame(self, canvas: CanvasView) -> None:
        """Containment is Qt's (``mapFromScene`` + ``contains``), so a rotated bed
        is tested as it is drawn, not by its scene bounding box."""
        garden = Garden(canvas)
        apply_rotation(garden.bed, 45.0)  # about the bed's centre (550, 400)
        inside = _add(garden, _perennial(garden, 550.0, 400.0, 61.0), "centre")
        corner = _add(garden, _perennial(garden, 690.0, 310.0, 62.0), "old corner")
        assert _bases(garden, inside) == (38.0, 38.0)
        # (690, 310) was inside the unrotated bed and is inside its scene bounding box,
        # but not inside the turned rectangle: never linked, never lifted
        assert garden.bed.sceneBoundingRect().contains(QPointF(690.0, 310.0))
        assert corner.parent_bed_id is None
        assert _bases(garden, corner) == (0.0, 0.0)

    def test_one_point_in_bed_test_in_the_app(self) -> None:
        """The construct ``parent.contains(parent.mapFromScene(point))`` and the
        membership point ``mapToScene(boundingRect().center())`` each exist once."""
        src = Path(open_garden_planner.__file__).parent
        contains_sites: set[str] = set()
        users: dict[str, set[str]] = {}
        for path in src.rglob("*.py"):
            relative = path.relative_to(src).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "id", getattr(node.func, "attr", ""))
                if name in ("bed_membership_point", "shape_contains", "centre_is_inside"):
                    users.setdefault(name, set()).add(relative)
                first = node.args[0] if node.args else None
                if (name == "contains" and isinstance(first, ast.Call)
                        and getattr(first.func, "attr", "") == "mapFromScene"):
                    contains_sites.add(relative)
        assert contains_sites == {"ui/canvas/footprints.py"}
        assert users["shape_contains"] == {
            "ui/canvas/footprints.py",      # inside centre_is_inside
            "ui/canvas/canvas_scene.py",    # find_smallest_bed_containing
        }
        assert users["bed_membership_point"] == {
            "ui/canvas/footprints.py",      # inside centre_is_inside
            "core/commands.py",             # auto-parenting on creation
            "ui/canvas/canvas_view.py",     # the re-link after a drag
            "app/application.py",           # the agent's re-link after move_object
        }
        assert users["centre_is_inside"] == {
            "ui/canvas/footprints.py",      # item_base_cm: the lift
            "app/application.py",           # set_parent_bed's link_is_geometric
        }


class TestHeatmapEndToEnd:
    """Review round 1, P2-3: a raised plant through the real ``SunHeatmapController``
    and its worker thread — the tip shift, read off the hours-of-sun grid."""

    def test_the_raised_plant_s_tip_is_in_the_grid(self, qtbot: Any) -> None:
        scene = CanvasScene(1000.0, 1000.0)
        view = CanvasView(scene)
        qtbot.addWidget(view)
        layer = scene.active_layer.id
        bed = RectangleItem(350.0, 150.0, 300.0, 300.0, object_type=ObjectType.RAISED_BED,
                            layer_id=layer)                      # 3 m x 3 m, 40 cm high
        plant = CircleItem(500.0, 300.0, 30.0, object_type=ObjectType.PERENNIAL, layer_id=layer)
        plant.metadata[METADATA_KEY] = 60.0                      # in the bed's middle
        commands = view.command_manager
        commands.execute(CreateItemCommand(scene, bed, "bed"))
        commands.execute(CreateItemCommand(scene, plant, "plant"))
        assert plant.parent_bed_id == bed.item_id
        winter = date(2026, 12, 21)  # a low sun: at noon the shadows run due north, far

        def hours_of_sun() -> tuple[np.ndarray, HeatmapGrid]:
            controller = SunHeatmapController(scene, lambda: BERLIN)
            with qtbot.waitSignal(controller.finished, timeout=60000) as blocker:
                assert controller.run_for_day(winter)
            assert blocker.args == [True]
            assert controller.last_minutes is not None and controller.last_grid is not None
            return controller.last_minutes.copy(), controller.last_grid

        raised, grid = hours_of_sun()
        commands.execute(SetParentBedCommand(scene, plant, bed.item_id, None))  # same plan, plant
        ground, grid_ground = hours_of_sun()                                    # on the ground
        assert grid == grid_ground and raised.shape == ground.shape

        # Lifting the plant only ADDS shade: what it shaded from the ground lies inside the
        # bed's own shadow or inside its raised shadow.
        assert np.all(raised <= ground)
        lost = ground - raised
        assert lost.max() >= SAMPLE_STEP_MINUTES  # … and it really does add some

        # The tip, due north of the plant at solar noon: r + (base + h) / tan a.
        noon = max(daylight_samples(BERLIN["latitude"], BERLIN["longitude"], winter),
                   key=lambda sample: sample.elevation_deg)
        assert 13.0 < noon.elevation_deg < 15.0 and abs(noon.azimuth_deg - 180.0) < 3.0
        tan_a = math.tan(math.radians(noon.elevation_deg))
        raised_tip = 30.0 + (38.0 + 60.0) / tan_a   # about 4.2 m north of the plant's centre
        ground_tip = 30.0 + 60.0 / tan_a            # about 2.7 m: where the tip was before L1.6
        bed_reach = 150.0 + 40.0 / tan_a            # the bed's own shadow, from the plant's centre
        assert ground_tip < bed_reach < raised_tip  # from the ground the tip never left the bed's

        _row, column = grid.cell_at(500.0, 300.0)
        rows = np.nonzero(lost[:, column] > 0.0)[0]
        north = (rows + 0.5) * grid.cell_cm + grid.y0_cm - 300.0  # cm north of the plant's centre
        assert north.min() > bed_reach - 2.0 * grid.cell_cm   # nothing changed under the bed's shadow
        # the farthest cell that lost sun to the plant is the raised tip, to a cell and the
        # few centimetres the nearest 15-minute sample misses true noon by
        assert north.max() == pytest.approx(raised_tip, abs=2.0 * grid.cell_cm)
        assert north.max() - ground_tip == pytest.approx(38.0 / tan_a, abs=2.0 * grid.cell_cm)
        # … and every cell between the bed's shadow and that tip lost at least one sample
        between = lost[rows.min():rows.max() + 1, column]
        assert np.all(between >= SAMPLE_STEP_MINUTES)


class TestOneCasterWalk:
    """Gate G4's drift guard: the heatmap and the overlay consume the same casters,
    and 3D takes its base from the same function."""

    def test_overlay_and_heatmap_call_the_same_function(self) -> None:
        assert sun_heatmap.collect_shadow_casters is sun_shadow_controller.collect_shadow_casters
        assert sun_shadow_controller.item_base_cm is footprints.item_base_cm
        assert snapshot_module.item_base_cm is footprints.item_base_cm

    def test_nothing_else_in_the_app_walks_the_scene_for_casters(self) -> None:
        """Every producer of ``ShadowCaster`` and every reader of ``parent_bed_id``
        that turns it into a height is named here. A second walk fails this test."""
        src = Path(open_garden_planner.__file__).parent
        calls: dict[str, set[str]] = {}
        for path in src.rglob("*.py"):
            relative = path.relative_to(src).as_posix()
            if relative.startswith("spike_q3d/"):
                continue  # the dormant spike keeps its own copy until L1.10
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "id", getattr(node.func, "attr", ""))
                    calls.setdefault(name, set()).add(relative)
        assert calls["ShadowCaster"] == {"ui/canvas/sun_shadow_controller.py"}  # one maker
        # … and every consumer: nobody else turns casters into shadows (review P2-4)
        assert calls["compute_scene_shadows"] == {
            "ui/canvas/sun_shadow_controller.py",   # the overlay's recompute
            "core/shade_aggregation.py",            # the heatmap, once per daylight sample
        }
        assert calls["caster_fields"] == {"core/shadow_geometry.py"}  # the ONE unpacking, by type
        assert calls["collect_shadow_casters"] == {
            "ui/canvas/sun_shadow_controller.py",   # the overlay
            "ui/canvas/sun_heatmap.py",             # the heatmap
        }
        assert calls["item_base_cm"] == {
            "ui/canvas/sun_shadow_controller.py",   # 2D: the caster walk
            "ui/view3d/snapshot.py",                # 3D: the record walk
        }
        assert calls["standing_base_cm"] == {"ui/canvas/footprints.py"}  # inside item_base_cm
        assert calls["soil_surface_cm"] == {
            "core/object_height.py",                # inside standing_base_cm
            "core/scene3d/builders/beds.py",        # where the 3D bed meshes put their soil
        }
        assert "parent_bed_id" not in inspect.getsource(sun_shadow_controller)
        assert "parent_bed_id" not in inspect.getsource(sun_heatmap)

    def test_the_overlay_paints_the_raised_shadow(self, canvas: CanvasView) -> None:
        """End to end through the real controller: the overlay's path IS the union of
        the casters with their bases, and its tip lies base / tan α beyond where the
        same plant cast from the ground."""
        garden = Garden(canvas)
        instant = datetime(2026, 6, 21, 18, 0, tzinfo=UTC)  # an evening sun, long shadows
        position = solar_position(BERLIN["latitude"], BERLIN["longitude"], instant)
        assert 5.0 < position.elevation_deg < 20.0
        controller = SunShadowController(garden.scene, lambda: BERLIN)
        controller.set_sim_datetime(instant)
        controller.set_enabled(True)
        overlay = next(i for i in garden.scene.items() if isinstance(i, SunShadowOverlayItem))
        path = overlay.path()
        painted = [(path.elementAt(i).x, path.elementAt(i).y) for i in range(path.elementCount())]
        casters = collect_shadow_casters(garden.scene, at_date=controller.clock.plan_date)
        assert {c.base_cm for c in casters} == {0.0, 38.0}
        expected = compute_scene_shadows(casters, position.elevation_deg, position.azimuth_deg)
        assert set(painted) == {point for polygon in expected for point in polygon}

        direction = shadow_direction_scene(position.azimuth_deg)

        def reach(points: Any) -> float:
            return max(x * direction[0] + y * direction[1] for x, y in points)

        plant = next(c for c in casters if c.base_cm > 0.0)
        tan_a = math.tan(math.radians(position.elevation_deg))
        raised_tip = reach(p for poly in compute_scene_shadows(
            [plant], position.elevation_deg, position.azimuth_deg) for p in poly)
        ground_tip = reach(p for poly in compute_scene_shadows(
            [ShadowCaster(plant.footprint, plant.height_cm)], position.elevation_deg,
            position.azimuth_deg) for p in poly)
        assert raised_tip - ground_tip == pytest.approx(38.0 / tan_a, abs=0.1)
        # What the user sees: the plant's tip now sticks out of the overlay …
        assert reach(painted) == pytest.approx(raised_tip, abs=1e-6)
        # … where, cast from the ground, it never left the bed's own shadow.
        bed = next(c for c in casters if c.base_cm == 0.0)
        bed_tip = reach(p for poly in compute_scene_shadows(
            [bed], position.elevation_deg, position.azimuth_deg) for p in poly)
        assert ground_tip < bed_tip < raised_tip
        controller.set_enabled(False)

    def test_the_overlay_s_key_carries_the_base(self, canvas: CanvasView) -> None:
        """Footprint and height unchanged, only the base differs: still a recompute."""
        garden = Garden(canvas)
        controller = SunShadowController(garden.scene, lambda: BERLIN)
        controller.set_sim_datetime(datetime(2026, 6, 21, 12, 0, tzinfo=UTC))
        controller.set_enabled(True)
        count = controller.recompute_count
        controller.recompute_now()
        assert controller.recompute_count == count  # nothing changed: the key holds
        garden.plant.parent_bed_id = None  # only the BASE changes: footprint and height stay
        controller.recompute_now()
        assert controller.recompute_count == count + 1
        controller.set_enabled(False)

    def test_a_bed_height_change_and_a_re_parent_recompute_the_overlay_through_the_command_stack(
        self, canvas: CanvasView, qtbot: Any
    ) -> None:
        """Neither edit repaints anything — a height is metadata, the link a Python
        attribute — so ``scene.changed`` never fires for them (#173). What reaches
        the overlay is the command stack's ``stack_changed``, wired to
        ``schedule_recompute`` exactly as ``application.py`` does (its connect line
        is pinned by ``test_shadow_overlay.py::test_app_wires_stack_changed_to_sun_controller``).
        Nobody calls ``recompute_now`` here."""
        garden = Garden(canvas)
        instant = datetime(2026, 6, 21, 18, 0, tzinfo=UTC)
        position = solar_position(BERLIN["latitude"], BERLIN["longitude"], instant)
        tan_a = math.tan(math.radians(position.elevation_deg))
        direction = shadow_direction_scene(position.azimuth_deg)
        controller = SunShadowController(garden.scene, lambda: BERLIN)
        controller.set_sim_datetime(instant)
        controller.set_enabled(True)
        garden.commands.stack_changed.connect(controller.schedule_recompute)  # the app's wiring
        overlay = next(i for i in garden.scene.items() if isinstance(i, SunShadowOverlayItem))
        plant_far = max(x * direction[0] + y * direction[1]
                        for x, y in next(c for c in collect_shadow_casters(garden.scene)
                                         if c.height_cm == 60.0).footprint)

        def tip() -> float:
            """How far the overlay reaches beyond the plant's footprint, in cm."""
            path = overlay.path()
            return max(path.elementAt(i).x * direction[0] + path.elementAt(i).y * direction[1]
                       for i in range(path.elementCount())) - plant_far

        def settled(expected_count: int) -> None:
            qtbot.wait(400)  # the debounce, then quiet
            assert controller.recompute_count == expected_count

        qtbot.wait(400)  # the echoes of creating the items
        count = controller.recompute_count
        assert tip() == pytest.approx((38.0 + 60.0) / tan_a, abs=0.01)

        def set_height(item: Any, value: float | None) -> None:  # the properties panel's apply_func
            if value is None:
                item.metadata.pop(METADATA_KEY, None)
            else:
                item.metadata[METADATA_KEY] = value

        garden.commands.execute(
            ChangePropertyCommand(garden.bed, "object height", None, 75.0, set_height))
        settled(count + 1)
        assert tip() == pytest.approx((73.0 + 60.0) / tan_a, abs=0.01)  # the plant rose with its bed

        garden.commands.execute(
            SetParentBedCommand(garden.scene, garden.plant, garden.bed.item_id, None))
        settled(count + 2)
        # unlinked, the plant casts from the ground and ends inside the 75 cm bed's own shadow,
        # which is now the overlay's far edge — about 90 cm short of where the raised tip was
        assert 60.0 / tan_a < tip() < (73.0 + 60.0) / tan_a - 50.0

        garden.commands.undo()  # linked again
        settled(count + 3)
        assert tip() == pytest.approx((73.0 + 60.0) / tan_a, abs=0.01)
        garden.commands.undo()  # the bed is 40 cm again
        settled(count + 4)
        assert tip() == pytest.approx((38.0 + 60.0) / tan_a, abs=0.01)

        # negative control: without the wiring the same edit goes unseen
        garden.commands.stack_changed.disconnect(controller.schedule_recompute)
        garden.commands.redo()
        settled(count + 4)
        controller.set_enabled(False)
