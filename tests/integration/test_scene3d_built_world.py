"""The built world end to end (Phase 17 L1.6, #385 — §8.10, gates G2 and G3).

live items → ``snapshot_records`` → ``SceneSync`` (production registry) → ``RecordingSink``

A real garden drawn with the real tools and the app's own commands: a house with
its ridge, a fence in every style, a wall, a raised bed with two plants, a round
container with a plant, a hedge and a shed. Asserted:

1. which builder each kind gets, each item's height and where each plant stands;
2. an edit session — raise the bed, move the house, change a fence's style, drag
   the ridge, undo it all — with the EXACT sink calls and builder invocations,
   and after every step incremental == full rebuild;
3. gate G2 on real items: the ridge line in the mesh, posed by the record's
   transform, is the LIVE ridge item's line in the scene, run on to the roof's
   edge — for turned houses and footprints that are not rectangles.

Nothing is rendered: the sink is the recording double (L1.2 runs the same garden
against Qt Quick 3D).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest
from PyQt6.QtCore import QPointF

from open_garden_planner.core.commands import (
    ChangePropertyCommand,
    CreateItemCommand,
    MoveItemsCommand,
)
from open_garden_planner.core.object_height import METADATA_KEY, effective_height_cm
from open_garden_planner.core.object_types import ObjectType, PathFenceStyle
from open_garden_planner.core.scene3d import (
    BuilderRegistry,
    MeshPart,
    Record,
    RecordingSink,
    SceneDiff,
    SceneSync,
    default_builder,
    pose_points,
)
from open_garden_planner.core.scene3d.builders import (
    BUILT_WORLD_KINDS,
    beds,
    buildings,
    fences,
    hedges,
    palette,
    production_registry,
    walls,
)
from open_garden_planner.core.tools import ToolType
from open_garden_planner.ui.canvas.canvas_view import CanvasView
from open_garden_planner.ui.canvas.footprints import item_footprints
from open_garden_planner.ui.canvas.geometry_apply import apply_rotation, build_move_vertex_command
from open_garden_planner.ui.canvas.items.circle_item import CircleItem
from open_garden_planner.ui.canvas.items.polygon_item import PolygonItem
from open_garden_planner.ui.canvas.items.polyline_item import PolylineItem
from open_garden_planner.ui.canvas.items.rectangle_item import RectangleItem
from open_garden_planner.ui.view3d.snapshot import snapshot_records

BEGIN, COMMIT = ("begin", None), ("commit", None)
STYLES = (PathFenceStyle.WOODEN_FENCE, PathFenceStyle.METAL_FENCE, PathFenceStyle.CHAIN_LINK,
          PathFenceStyle.HEDGE_FENCE, PathFenceStyle.STONE_WALL)


def draw_polygon(view: CanvasView, mouse_event: Any, tool: ToolType,
                 corners: list[tuple[float, float]]) -> PolygonItem:
    """Draw a polygon with the real tool: a click per corner, a double click to close."""
    before = set(view.scene().items())
    view.set_active_tool(tool)
    active = view.tool_manager.active_tool
    for x, y in corners:
        active.mouse_press(mouse_event, QPointF(x, y))
    active.mouse_double_click(mouse_event, QPointF(*corners[-1]))
    (polygon,) = [i for i in set(view.scene().items()) - before if isinstance(i, PolygonItem)]
    return polygon


def ridge_of(view: CanvasView, house: PolygonItem) -> PolylineItem:
    wanted = str(house.metadata["ridge_item_id"])
    (ridge,) = [i for i in view.scene().items() if str(getattr(i, "item_id", "")) == wanted]
    assert isinstance(ridge, PolylineItem) and ridge.object_type is ObjectType.ROOF_RIDGE
    return ridge


class Garden:
    """The story's garden, on a real view."""

    def __init__(self, view: CanvasView, mouse_event: Any) -> None:
        self.view = view
        self.scene = view.scene()
        self.commands = view.command_manager
        layer = self.scene.active_layer.id

        # a house, drawn with the HOUSE tool — which creates its ridge (core.roof_ridge)
        self.house = draw_polygon(view, mouse_event, ToolType.HOUSE,
                                  [(1000.0, 800.0), (1900.0, 800.0), (1900.0, 1340.0), (1000.0, 1340.0)])
        self.ridge = ridge_of(view, self.house)

        # a raised bed, drawn with its tool, and two plants put INTO it
        view.set_active_tool(ToolType.RAISED_BED)
        tool = view.tool_manager.active_tool
        tool.mouse_press(mouse_event, QPointF(400.0, 300.0))
        tool.mouse_move(mouse_event, QPointF(700.0, 500.0))
        tool.mouse_release(mouse_event, QPointF(700.0, 500.0))
        self.bed = next(i for i in self.scene.items() if isinstance(i, RectangleItem))
        self.bed_plants = [self._plant(480.0, 400.0, 45.0, layer), self._plant(620.0, 400.0, 70.0, layer)]

        # a round container with a plant
        self.pot = CircleItem(900.0, 400.0, 30.0, object_type=ObjectType.CONTAINER_ROUND, layer_id=layer)
        self.pot.metadata["container_material"] = "terracotta"
        self.commands.execute(CreateItemCommand(self.scene, self.pot, "pot"))
        self.pot_plant = self._plant(900.0, 400.0, 35.0, layer)

        # a fence in every style (and one with none), a wall
        self.fences: dict[str, PolylineItem] = {}
        for index, style in enumerate((PathFenceStyle.NONE, *STYLES)):
            y = 2000.0 + 150.0 * index
            fence = PolylineItem([QPointF(200.0, y), QPointF(900.0, y), QPointF(900.0, y + 90.0)],
                                 object_type=ObjectType.FENCE, layer_id=layer, path_fence_style=style)
            self.commands.execute(CreateItemCommand(self.scene, fence, "fence"))
            self.fences[style.name] = fence
        self.wall = PolylineItem([QPointF(2200.0, 300.0), QPointF(2200.0, 1100.0)],
                                 object_type=ObjectType.WALL, layer_id=layer)
        self.commands.execute(CreateItemCommand(self.scene, self.wall, "wall"))

        # a hedge section, a hedge polygon, a tool shed
        self.hedge = RectangleItem(2600.0, 300.0, 400.0, 60.0, object_type=ObjectType.HEDGE_SECTION,
                                   layer_id=layer)
        self.commands.execute(CreateItemCommand(self.scene, self.hedge, "hedge"))
        self.hedge_polygon = draw_polygon(view, mouse_event, ToolType.HEDGE_POLYGON, [
            (2600.0, 600.0), (3200.0, 600.0), (3200.0, 680.0), (2600.0, 680.0)])
        self.shed = RectangleItem(2600.0, 1000.0, 300.0, 200.0, object_type=ObjectType.TOOL_SHED,
                                  layer_id=layer)
        self.commands.execute(CreateItemCommand(self.scene, self.shed, "shed"))

    def _plant(self, x: float, y: float, height: float, layer: Any) -> CircleItem:
        plant = CircleItem(x, y, 12.0, object_type=ObjectType.PERENNIAL, layer_id=layer)
        plant.metadata[METADATA_KEY] = height
        self.commands.execute(CreateItemCommand(self.scene, plant, "plant"))  # auto-parents
        return plant

    @staticmethod
    def id(item: Any) -> str:
        return str(item.item_id)


class Pipeline:
    """snapshot → sync → sink with the PRODUCTION registry, counting builder calls."""

    def __init__(self, scene: Any) -> None:
        self.scene = scene
        self.built: list[str] = []
        production = production_registry()
        self.registry = BuilderRegistry(default=self._counting(default_builder))
        for kind in production.kinds():
            self.registry.register(kind, self._counting(production.builder_for(kind)))
        self.sink = RecordingSink()
        self.sync = SceneSync(self.sink, self.registry, validate=True)

    def _counting(self, builder: Any) -> Any:
        def build(record: Record) -> tuple[MeshPart, ...]:
            self.built.append(record.item_id)
            return builder(record)  # type: ignore[no-any-return]
        return build

    def tick(self) -> tuple[SceneDiff, list[tuple[str, str | None]], list[str]]:
        self.sink.clear_calls()
        self.built.clear()
        records = snapshot_records(self.scene)
        result = self.sync.apply(records)
        fresh = SceneSync(RecordingSink(), production_registry(), validate=True)
        fresh.apply(records)
        assert isinstance(fresh.sink, RecordingSink)
        assert self.sink.state() == fresh.sink.state()  # incremental == full rebuild
        assert self.sync.failures == {} and fresh.failures == {}
        return result, self.sink.ops(), list(self.built)

    def top(self, item_id: str) -> float:
        held = self.sink.item(item_id)
        posed = [pose_points(held.transform, part.mesh.positions) for part in held.parts]
        return float(np.concatenate(posed)[:, 2].max())

    def bottom(self, item_id: str) -> float:
        held = self.sink.item(item_id)
        posed = [pose_points(held.transform, part.mesh.positions) for part in held.parts]
        return float(np.concatenate(posed)[:, 2].min())


def without(calls: list[tuple[str, str | None]], item_id: str) -> list[tuple[str, str | None]]:
    """The sink calls that do not concern ``item_id`` (the ROOF_RIDGE item: it has
    no solid of its own, and whether it has a record at all is the snapshot's
    business — the HOUSE carries the ridge)."""
    return [call for call in calls if call[1] != item_id]


@pytest.fixture
def garden(canvas: CanvasView, mouse_event: Any) -> Garden:
    return Garden(canvas, mouse_event)


def test_kinds_builders_heights_and_bases(garden: Garden) -> None:
    pipeline = Pipeline(garden.scene)
    result, calls, built = pipeline.tick()
    records = pipeline.sync.records
    assert set(result.added) == set(records) and calls[0] == BEGIN and calls[-1] == COMMIT
    assert sorted(built) == sorted(records)  # one builder call per item, no more

    # ── kinds → builders ─────────────────────────────────────────────────────
    production = production_registry()
    expected_builder = {
        "HOUSE": buildings.build_house, "TOOL_SHED": buildings.build_shed,
        "FENCE": fences.build_fence, "WALL": walls.build_wall,
        "RAISED_BED": beds.build_raised_bed, "CONTAINER_ROUND": beds.build_round_container,
        "HEDGE_SECTION": hedges.build_hedge, "HEDGE_POLYGON": hedges.build_hedge,
    }
    for kind, builder in expected_builder.items():
        assert production.builder_for(kind) is builder
    kinds = {record.kind for record in records.values()}
    assert kinds - BUILT_WORLD_KINDS <= {"PERENNIAL", "ROOF_RIDGE"}
    assert production.builder_for("PERENNIAL") is default_builder  # plants are L1.7's

    def names(item: Any) -> list[str]:
        return [part.name for part in pipeline.sink.item(Garden.id(item)).parts]

    assert names(garden.house) == ["walls", "roof"]
    assert names(garden.shed) == ["walls", "roof"]
    assert names(garden.bed) == ["bed"]
    assert names(garden.pot) == ["walls", "soil"]
    assert names(garden.hedge) == names(garden.hedge_polygon) == ["hedge"]
    assert names(garden.wall) == ["wall"]
    assert names(garden.fences["NONE"]) == names(garden.fences["WOODEN_FENCE"]) == ["fence"]
    assert names(garden.fences["METAL_FENCE"]) == names(garden.fences["CHAIN_LINK"]) == ["fence"]
    assert names(garden.fences["HEDGE_FENCE"]) == ["hedge"]
    assert names(garden.fences["STONE_WALL"]) == ["wall"]
    triangles = {style: sum(p.mesh.triangle_count for p in pipeline.sink.item(Garden.id(f)).parts)
                 for style, f in garden.fences.items()}
    assert triangles["NONE"] == triangles["WOODEN_FENCE"]
    assert len({triangles[s.name] for s in STYLES}) == len(STYLES)  # five different fences
    assert pipeline.sink.item(Garden.id(garden.house)).parts[1].material_kind == "roof"

    # ── heights: every solid's top is the 2D resolver's height, to the float32 ──
    for item in (garden.house, garden.shed, garden.bed, garden.pot, garden.hedge,
                 garden.hedge_polygon, garden.wall, *garden.fences.values()):
        height = effective_height_cm(item.object_type, item.metadata)
        assert height is not None
        assert pipeline.top(Garden.id(item)) == float(np.float32(height)), item.object_type.name
        assert pipeline.bottom(Garden.id(item)) == 0.0
    assert pipeline.top(Garden.id(garden.house)) == 450.0
    assert pipeline.top(Garden.id(garden.fences["CHAIN_LINK"])) == 120.0

    # ── bases: plants stand on their parent's soil (gate G3) ─────────────────
    for plant, height in zip(garden.bed_plants, (45.0, 70.0), strict=True):
        record = records[Garden.id(plant)]
        assert record.parent_id == Garden.id(garden.bed)
        assert record.transform.base_cm == 38.0  # the 40 cm bed's soil
        assert pipeline.bottom(Garden.id(plant)) == 38.0
        assert pipeline.top(Garden.id(plant)) == 38.0 + height
    pot_plant = records[Garden.id(garden.pot_plant)]
    assert pot_plant.parent_id == Garden.id(garden.pot) and pot_plant.transform.base_cm == 28.0
    assert pipeline.bottom(Garden.id(garden.pot_plant)) == 28.0

    # … and that soil is where the meshes have it: nothing floats, nothing sinks
    soil = np.asarray(palette.SOIL.linear, dtype=np.float32)

    def soil_level(item: Any) -> float:
        for part in pipeline.sink.item(Garden.id(item)).parts:
            mine = np.isclose(part.mesh.colors, soil, atol=1e-6).all(axis=1)
            if mine.any():
                return float(part.mesh.positions[mine][:, 2].max())
        raise AssertionError("no soil")

    assert soil_level(garden.bed) == 38.0 and soil_level(garden.pot) == 28.0

    # ── colour: a fence is tinted with the item's own stroke, a bed is baked ──
    wooden = pipeline.sink.item(Garden.id(garden.fences["WOODEN_FENCE"]))
    assert all(part.tinted for part in wooden.parts)
    assert wooden.material.tint_rgba == (139, 90, 43, 255)  # PATH_FENCE_STYLES[WOODEN_FENCE]
    plain = pipeline.sink.item(Garden.id(garden.fences["NONE"]))
    assert plain.material.tint_rgba == (139, 69, 19, 255)  # OBJECT_STYLES[FENCE].stroke_color
    assert not pipeline.sink.item(Garden.id(garden.bed)).parts[0].tinted

    assert pipeline.tick() == (SceneDiff(), [], [])  # an untouched garden: nothing


def _set_height(item: Any, value: float) -> ChangePropertyCommand:
    """The properties panel's object-height edit: one undoable metadata write."""
    def apply(target: Any, new: float | None) -> None:
        if new is None:
            target.metadata.pop(METADATA_KEY, None)
        else:
            target.metadata[METADATA_KEY] = new

    return ChangePropertyCommand(item, "object height", item.metadata.get(METADATA_KEY), value, apply)


def _set_style(item: PolylineItem, style: PathFenceStyle) -> ChangePropertyCommand:
    """The properties panel's style combo (``_on_property_changed``): the style and
    its preset stroke — colour and width."""
    def apply(target: Any, new: PathFenceStyle) -> None:
        target.path_fence_style = new
        target.apply_style_preset()
        target.update()

    return ChangePropertyCommand(item, "path/fence style", item.path_fence_style, style, apply)


def test_edit_session_sends_exact_sink_calls(garden: Garden) -> None:
    pipeline = Pipeline(garden.scene)
    pipeline.tick()
    commands = garden.commands
    bed_id, house_id, ridge_id = Garden.id(garden.bed), Garden.id(garden.house), Garden.id(garden.ridge)
    plant_ids = [Garden.id(plant) for plant in garden.bed_plants]
    start_state = pipeline.sink.state()
    builds_at_start = pipeline.sync.build_count

    # ── raise the bed from 40 to 75 cm: ONE rebuild (the bed), its plants re-posed ──
    commands.execute(_set_height(garden.bed, 75.0))
    result, calls, built = pipeline.tick()
    assert result == SceneDiff(transform=tuple(plant_ids), geometry=(bed_id,))
    assert calls == [BEGIN, ("replace_geometry", bed_id),
                     *[("update_transform", plant_id) for plant_id in plant_ids], COMMIT]
    assert built == [bed_id]  # no plant mesh was rebuilt
    assert pipeline.top(bed_id) == 75.0
    assert [pipeline.bottom(plant_id) for plant_id in plant_ids] == [73.0, 73.0]
    assert pipeline.sink.item(Garden.id(garden.pot_plant)).transform.base_cm == 28.0  # untouched

    # ── move the house: ONE update_transform, no builder call ────────────────
    commands.execute(MoveItemsCommand([garden.house], QPointF(137.25, -42.5)))
    result, calls, built = pipeline.tick()
    assert house_id in result.transform and house_id not in result.geometry
    assert without(calls, ridge_id) == [BEGIN, ("update_transform", house_id), COMMIT]
    assert [b for b in built if b != ridge_id] == []  # the house and its roof were NOT rebuilt
    assert pipeline.sink.item(house_id).transform.east_cm == 1450.0 + 137.25

    # ── change a fence's style: geometry (style + stroke width) and material (stroke) ──
    fence = garden.fences["WOODEN_FENCE"]
    fence_id = Garden.id(fence)
    before_parts = pipeline.sink.item(fence_id).parts
    commands.execute(_set_style(fence, PathFenceStyle.METAL_FENCE))
    result, calls, built = pipeline.tick()
    assert result == SceneDiff(material=(fence_id,), geometry=(fence_id,))
    assert calls == [BEGIN, ("replace_geometry", fence_id), ("update_material", fence_id), COMMIT]
    assert built == [fence_id]
    held = pipeline.sink.item(fence_id)
    assert held.parts != before_parts and pipeline.top(fence_id) == 120.0
    assert held.material.tint_rgba == (60, 60, 60, 255)  # PATH_FENCE_STYLES[METAL_FENCE]
    assert pipeline.sync.records[fence_id].params["path_style"] == "METAL_FENCE"
    assert held.parts == production_registry().build(pipeline.sync.records[fence_id])

    # ── drag the ridge along the west wall: the HOUSE is rebuilt, nothing else ──
    old = garden.ridge.points[0]
    target = garden.ridge.mapFromScene(QPointF(1000.0 + 137.25, 900.0 - 42.5))  # on the moved wall
    commands.execute(build_move_vertex_command(garden.ridge, 0, old, target))
    result, calls, built = pipeline.tick()
    assert house_id in result.geometry and house_id not in result.transform
    assert without(calls, ridge_id) == [BEGIN, ("replace_geometry", house_id), COMMIT]
    assert [b for b in built if b != ridge_id] == [house_id]
    line = _ridge_in_scene(pipeline.sink.item(house_id), 450.0)
    assert float(np.linalg.norm(line - np.array([1137.25, 857.5]), axis=1).min()) < 35.0

    # ── undo all four: back to the very first state, with the fewest calls ───
    for _ in range(4):
        commands.undo()
    result, calls, built = pipeline.tick()
    assert set(result.geometry) - {ridge_id} == {bed_id, fence_id, house_id}
    assert set(result.transform) - {ridge_id} == {*plant_ids, house_id}
    assert set(result.material) == {fence_id}
    assert sorted(b for b in built if b != ridge_id) == sorted([bed_id, fence_id, house_id])
    assert pipeline.sink.state() == start_state
    rebuilt = [bed_id, fence_id, house_id]  # once going, once coming back — and nothing else
    extra = pipeline.sync.build_count - builds_at_start
    assert extra - sum(1 for _ in range(2) for _ in rebuilt) in (0, 1, 2)  # + the ridge item, if it has a record


# ── gate G2 on real items ────────────────────────────────────────────────────


def _ridge_in_scene(held: Any, height: float) -> np.ndarray:
    """The plan points of the mesh's top line (the ridge tile's apex), posed in the scene."""
    roof = next(part for part in held.parts if part.name == "roof")
    posed = pose_points(held.transform, roof.mesh.positions)
    top = posed[roof.mesh.positions[:, 2] == np.float32(height)][:, :2]
    return np.unique(np.round(top, 3), axis=0)


def _expected_live_ridge(house: PolygonItem, ridge: PolylineItem, overhang: float) -> np.ndarray:
    """The LIVE ridge item's two ends in the scene, each run on to the roof's edge:
    ``overhang / cos θ`` along the ridge, θ between the ridge and the outward normal
    of the wall it ends on — computed here from the canvas items alone."""
    points = ridge.points
    ends = [np.array([p.x(), p.y()]) for p in (ridge.mapToScene(points[0]), ridge.mapToScene(points[-1]))]
    along = (ends[1] - ends[0]) / np.linalg.norm(ends[1] - ends[0])
    (footprint,) = item_footprints(house)
    outline = np.asarray(footprint)
    out = []
    for end, direction in ((ends[0], -along), (ends[1], along)):
        best, cos_theta = math.inf, 1.0
        for a, b in zip(outline, np.roll(outline, -1, axis=0), strict=True):
            edge = b - a
            t = float(np.clip(((end - a) @ edge) / (edge @ edge), 0.0, 1.0))
            distance = float(np.linalg.norm(end - (a + t * edge)))
            if distance < best:
                normal = np.array([edge[1], -edge[0]]) / np.linalg.norm(edge)
                best, cos_theta = distance, abs(float(normal @ direction))
        assert best < 1e-6  # the live ridge ends ON the house's outline
        out.append(end + direction * (overhang / cos_theta))
    return np.stack(out)


FOOTPRINTS = {
    "rectangle": [(1000.0, 800.0), (1900.0, 800.0), (1900.0, 1340.0), (1000.0, 1340.0)],
    "l_shape": [(1000.0, 800.0), (1900.0, 800.0), (1900.0, 1200.0), (1450.0, 1200.0),
                (1450.0, 1400.0), (1000.0, 1400.0)],
    "trapezoid": [(1000.0, 800.0), (1800.0, 800.0), (1650.0, 1200.0), (1150.0, 1200.0)],
}


class TestRidgeIsTheLiveRidge:
    @pytest.mark.parametrize("angle", [0.0, 17.0, 90.0, 213.5])
    @pytest.mark.parametrize("shape", sorted(FOOTPRINTS))
    def test_mesh_ridge_equals_the_canvas_ridge(
        self, canvas: CanvasView, mouse_event: Any, shape: str, angle: float
    ) -> None:
        house = draw_polygon(canvas, mouse_event, ToolType.HOUSE, FOOTPRINTS[shape])
        ridge = ridge_of(canvas, house)
        apply_rotation(house, angle)  # the canvas re-derives the ridge (ADR-046)
        house.moveBy(321.7, -45.3)
        pipeline = Pipeline(canvas.scene())
        pipeline.tick()
        held = pipeline.sink.item(str(house.item_id))
        record = pipeline.sync.records[str(house.item_id)]
        assert record.transform.rotation_deg == pytest.approx(angle) and "ridge" in record.params
        line = _ridge_in_scene(held, 450.0)
        expected = _expected_live_ridge(house, ridge, buildings.HOUSE_ROOF.overhang_cm)
        assert len(line) == 2
        worst = max(float(np.linalg.norm(line - point, axis=1).min()) for point in expected)
        assert worst <= 0.1  # gate G2: one millimetre
        assert worst <= 5e-3  # measured 6.1e-4: float32 vertices a few thousand cm from the origin
        if shape != "trapezoid":  # the ridge meets these walls squarely: exactly 30 cm on
            ends = [ridge.mapToScene(p) for p in (ridge.points[0], ridge.points[-1])]
            length = math.hypot(ends[1].x() - ends[0].x(), ends[1].y() - ends[0].y())
            assert float(np.linalg.norm(line[0] - line[1])) == pytest.approx(length + 60.0, abs=5e-3)

    def test_dragging_the_ridge_changes_the_house_s_geometry_and_its_mesh(
        self, canvas: CanvasView, mouse_event: Any
    ) -> None:
        house = draw_polygon(canvas, mouse_event, ToolType.HOUSE, FOOTPRINTS["rectangle"])
        ridge = ridge_of(canvas, house)
        house_id = str(house.item_id)
        apply_rotation(house, 17.0)
        pipeline = Pipeline(canvas.scene())
        pipeline.tick()
        before = pipeline.sync.records[house_id]
        roof_before = pipeline.sink.item(house_id).parts[1].mesh

        wall_point = house.mapToScene(QPointF(1000.0, 900.0))  # 170 cm along the west wall
        old = ridge.points[0]
        canvas.command_manager.execute(
            build_move_vertex_command(ridge, 0, old, ridge.mapFromScene(wall_point)))
        result, calls, built = pipeline.tick()
        after = pipeline.sync.records[house_id]
        assert house_id in result.geometry and house_id not in result.transform
        assert before.geometry_sig != after.geometry_sig and not before.same_geometry(after)
        assert before.params["ridge"] != after.params["ridge"]
        assert ("replace_geometry", house_id) in calls and house_id in built
        held = pipeline.sink.item(house_id)
        assert held.parts[1].mesh != roof_before
        line = _ridge_in_scene(held, 450.0)
        expected = _expected_live_ridge(house, ridge, buildings.HOUSE_ROOF.overhang_cm)
        assert max(float(np.linalg.norm(line - point, axis=1).min()) for point in expected) <= 0.1
        assert pipeline.top(house_id) == 450.0  # the ridge height is still the house's height

        canvas.command_manager.undo()
        pipeline.tick()
        assert pipeline.sync.records[house_id].same_geometry(before)
        assert pipeline.sink.item(house_id).parts[1].mesh == roof_before

    @pytest.mark.parametrize("angle", [0.0, 60.0, 213.5])
    def test_the_slope_that_faces_the_sun_is_lit_on_a_turned_house(
        self, canvas: CanvasView, mouse_event: Any, angle: float
    ) -> None:
        """Normals posed by the record's transform: the slope whose outward side
        looks at the sun has the larger n·l — wherever the house is turned."""
        from open_garden_planner.core.scene3d import SunState
        from open_garden_planner.core.scene3d.frame import cos_sin

        house = draw_polygon(canvas, mouse_event, ToolType.HOUSE, FOOTPRINTS["rectangle"])
        apply_rotation(house, angle)
        pipeline = Pipeline(canvas.scene())
        pipeline.tick()
        held = pipeline.sink.item(str(house.item_id))
        mesh = held.parts[1].mesh
        tile = np.isclose(mesh.colors, np.asarray(palette.ROOF_TILE.linear, np.float32),
                          atol=1e-6).all(axis=1)
        c, s = cos_sin(held.transform.rotation_deg)
        local = mesh.normals[tile].astype(np.float64)
        posed = np.stack([c * local[:, 0] - s * local[:, 1], s * local[:, 0] + c * local[:, 1],
                          local[:, 2]], axis=1)
        # the two slopes look along ±(the ridge's left normal) in the scene
        facing = np.array([-s, c])  # local +y (north of the east-west ridge), turned
        azimuth = math.degrees(math.atan2(facing[0], facing[1])) % 360.0  # the compass bearing it faces
        sun = np.asarray(SunState(30.0, azimuth).direction_scene)
        toward = posed[posed[:, :2] @ facing > 0.1]
        away = posed[posed[:, :2] @ facing < -0.1]
        assert len(toward) and len(away)
        assert float((toward @ sun).min()) > 0.9  # square on to a 30° sun
        assert float((toward @ sun).min()) > float((away @ sun).max()) + 0.5
