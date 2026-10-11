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
import inspect
import math
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from PyQt6.QtCore import QPointF

import open_garden_planner
from open_garden_planner.core import ProjectManager
from open_garden_planner.core.commands import CreateItemCommand, MoveItemsCommand
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
from open_garden_planner.core.shadow_geometry import (
    ShadowCaster,
    compute_scene_shadows,
    shadow_direction_scene,
)
from open_garden_planner.core.solar import solar_position
from open_garden_planner.ui.canvas import footprints, sun_heatmap, sun_shadow_controller
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.canvas.canvas_view import CanvasView
from open_garden_planner.ui.canvas.items.circle_item import CircleItem
from open_garden_planner.ui.canvas.items.polygon_item import PolygonItem
from open_garden_planner.ui.canvas.items.rectangle_item import RectangleItem
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
    (ObjectType.CONTAINER, {}, 28.0),
    (ObjectType.CONTAINER, {"container_height_cm": 45.0}, 43.0),
    (ObjectType.WALL_PLANTER, {}, 28.0),
    (ObjectType.CONTAINER_ROUND, {}, 28.0),
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
        expected = {"RAISED_BED": (40.0, 38.0), "CONTAINER_ROUND": (30.0, 28.0)}
        for rid in on_soil:
            parent = records[records[rid].parent_id or ""]
            assert (parent.height_cm, bases[rid]) == expected[parent.kind]
        in_ground = [r for r in records.values() if r.parent_id and r.transform.base_cm == 0.0]
        # an in-ground bed has no height, a trellis holds no soil: both are the ground
        assert {records[r.parent_id or ""].kind for r in in_ground} <= {"GARDEN_BED", "TRELLIS"}
        casters = collect_shadow_casters(scene, at_date=JUNE)
        assert sum(1 for c in casters if c.base_cm > 0.0) == raised
        assert {c.base_cm for c in casters} <= {0.0, 28.0, 38.0}


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

    def test_a_bed_height_change_recomputes_the_overlay(self, canvas: CanvasView) -> None:
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
