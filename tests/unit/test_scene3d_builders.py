"""The built-world builders (Phase 17 L1.6, #385 — gates G1, G2, G5, G6). Qt-free.

Every builder is a pure function ``Record -> tuple[MeshPart, ...]`` in the
item-local frame. ``verify_builder`` holds each to the seam's contract
(deterministic; reads only the geometry part; every mesh validates; top = the
resolved height). This file adds what a planning tool needs on top:

G1  over the whole matrix (``scene3d_builder_cases.CASES``: every kind x sizes x
    turned local geometry x with / without a height): the top is the height to
    the float32, the base is the ground, the fit scale is 1, the triangle budget
    holds, flat faces store their winding normal, an item without a height casts
    no shadow, and the solid stays on its footprint.
G2  the roof's ridge line IS the record's ridge, run on to the roof's edge; the
    slope that faces the sun is the lit one.
G5  every vertex colour is a ``palette`` entry — or pure white on a tinted part.
G6  the same record gives the same mesh; two hedges are never clones.
"""

from __future__ import annotations

import dataclasses
import functools
import math

import numpy as np
import pytest

from open_garden_planner.core.object_height import SOIL_DROP_CM, soil_surface_cm
from open_garden_planner.core.scene3d import (
    BuilderRegistry,
    MeshPart,
    Params,
    Record,
    RecordingSink,
    SceneSync,
    SunState,
    default_builder,
    normal_vs_winding,
    verify_builder,
)
from open_garden_planner.core.scene3d.builders import (
    BUILT_WORLD_KINDS,
    beds,
    buildings,
    fences,
    hedges,
    palette,
    production_registry,
    register,
    walls,
)
from open_garden_planner.core.scene3d.builders.common import BuiltBuilder, nominal_height_cm
from open_garden_planner.core.scene3d.builders.primitives import ccw_ring, offset_ring
from open_garden_planner.core.scene3d.builders.roofs import bounding_box_ridge, gable_roof
from open_garden_planner.core.scene3d.color import srgb8_to_linear
from tests.unit.scene3d_builder_cases import (
    BENT_PATH,
    CASES,
    CLOSED_PATH,
    L_RIDGE,
    L_SHAPE,
    LOOP,
    NEARLY_CLOSED_PATH,
    TRAPEZOID,
    U_RIDGE,
    U_SHAPE,
    Case,
    circle_record,
    covers,
    distance_to_path,
    line_record,
    outside_distance,
    path_length,
    plan_area,
    polygon_record,
    positions,
    rectangle,
    triangle_count,
    turned,
)

REGISTRY = production_registry()
HEIGHT_TOLERANCE_CM = 0.5  # gate G1; every builder is in fact exact to the float32
#: How far a hedge leaf's tip may pass the footprint's outline. Measured over the matrix:
#: 4.9 cm where the core is drawn in by 4 cm; 8.8 cm on a hedge too narrow to draw in
#: (10 cm wide), where leaves root on the outline itself — bounded by the leaf length.
LEAF_OVERSHOOT_CM = 6.0


@functools.cache
def built(record: Record) -> tuple[MeshPart, ...]:
    """The production builder's parts for a record — cached: builders are pure, and
    the matrix asks for the same record from several tests."""
    return REGISTRY.build(record)


def builder_of(record: Record) -> BuiltBuilder:
    builder = REGISTRY.builder_for(record.kind)
    assert isinstance(builder, BuiltBuilder)
    return builder


def budget_of(record: Record) -> int:
    height = record.height_cm or nominal_height_cm(record.kind) or 0.0
    if record.kind in ("HOUSE", "GARAGE_SHED", "TOOL_SHED"):
        return buildings.triangle_budget(record)
    if record.kind == "FENCE" or (record.kind == "WALL" and fences.is_fence_style(
            record.params.get("path_style")) and record.params.get("path_style") != "STONE_WALL"):
        return fences.triangle_budget(record)
    if record.kind == "WALL":
        return walls.triangle_budget(record)
    if record.kind in ("HEDGE_SECTION", "HEDGE_POLYGON"):
        return hedges.triangle_budget(record)
    return beds.triangle_budget(record, height)


# ── registration ─────────────────────────────────────────────────────────────


class TestRegistration:
    def test_exactly_the_built_world_kinds(self) -> None:
        assert REGISTRY.kinds() == BUILT_WORLD_KINDS
        assert frozenset({
            "HOUSE", "GARAGE_SHED", "TOOL_SHED", "FENCE", "WALL", "RAISED_BED", "CONTAINER",
            "WALL_PLANTER", "CONTAINER_ROUND", "GARDEN_BED", "HEDGE_SECTION", "HEDGE_POLYGON",
        }) == BUILT_WORLD_KINDS

    def test_every_kind_is_a_real_object_type(self) -> None:
        from open_garden_planner.core.object_types import ObjectType

        assert {member.name for member in ObjectType} >= BUILT_WORLD_KINDS

    def test_every_other_kind_keeps_the_default(self) -> None:
        for kind in ("PATH", "ROOF_RIDGE", "LAWN", "GREENHOUSE", "PERGOLA", "TABLE_RECTANGULAR",
                     "POND_POOL", "TREE", "SHRUB", "PERENNIAL", "TRELLIS"):
            assert REGISTRY.builder_for(kind) is default_builder

    def test_a_fresh_registry_every_time_and_no_double_registration(self) -> None:
        assert production_registry() is not production_registry()
        registry = BuilderRegistry()
        register(registry)
        with pytest.raises(ValueError, match="already registered"):
            register(registry)

    def test_the_matrix_covers_every_registered_kind(self) -> None:
        assert {case.record.kind for case in CASES} == BUILT_WORLD_KINDS
        assert len(CASES) == 286


# ── gate G1 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("case", CASES, ids=str)
class TestGateG1:
    def test_contract_height_base_fit_budget(self, case: Case) -> None:
        record = case.record
        builder = builder_of(record)
        parts = verify_builder(builder, record)  # deterministic, geometry-only, valid, top = height
        height = record.height_cm if record.height_cm is not None else nominal_height_cm(record.kind)
        if height is None:
            assert parts == ()  # an in-ground bed: ground, no solid
            return
        assert parts, "a kind with a height has a solid"
        points = positions(parts)
        top, bottom = float(points[:, 2].max()), float(points[:, 2].min())
        assert abs(top - height) <= HEIGHT_TOLERANCE_CM
        assert top == float(np.float32(height))  # by construction, not by a fit
        assert bottom == 0.0  # it stands on its own ground: nothing floats, nothing sinks
        assert builder.fit_scale(record) == pytest.approx(1.0, abs=1e-6)
        assert triangle_count(parts) <= budget_of(record)
        assert all(part.mesh.vertex_count for part in parts)  # no empty part reaches the sink

    def test_no_height_no_shadow(self, case: Case) -> None:
        parts = built(case.record)
        casts = {part.casts_shadow for part in parts}
        assert casts <= ({True} if case.record.height_cm is not None else {False})

    def test_every_face_looks_the_way_its_normal_does(self, case: Case) -> None:
        for part in built(case.record):
            dots, flat = normal_vs_winding(part.mesh)
            if part.material_kind == "foliage":
                # exempt from the rule by design (bent normals) — but a trimmed leaf is
                # moved whole, so in fact every leaf still looks the way it faces
                assert float(dots.min()) >= 0.99
                continue
            if flat.any():
                assert float(dots[flat].min()) >= 0.99  # the L0 "wrong roof slope" rule
            if (~flat).any():
                assert float(dots[~flat].min()) > 0.4  # smooth faces (pots, wires) face out too

    def test_the_solid_stays_on_its_footprint(self, case: Case) -> None:
        record = case.record
        parts = built(record)
        if not parts:
            return
        plan = positions(parts)[:, :2]
        if record.shape == "polyline":
            closed = record.path[0] == record.path[-1]
            style = record.params.get("path_style")
            wall_like = style == "STONE_WALL" or (record.kind == "WALL" and not fences.is_fence_style(style))
            if wall_like:
                reach = walls.body_thickness_cm(record) / 2.0 + walls.COPING_OVERSAIL_CM
            elif style == "HEDGE_FENCE":
                reach = max(record.path_width_cm or 0.0, fences.HEDGE_THICKNESS_CM) / 2.0 + 6.0
            else:
                reach = max(record.path_width_cm or 0.0, fences.POST_CM) / 2.0
            away = distance_to_path(plan, record.path[:-1] if closed else record.path, closed=closed)
            # a mitred corner reaches further than the half width: 1 / cos(half the turn)
            assert float(away.max()) <= reach * math.sqrt(2.0) + 1e-3
            return
        ring = record.footprints[0]
        overhang = {"HOUSE": buildings.HOUSE_ROOF.overhang_cm,
                    "GARAGE_SHED": buildings.SHED_ROOF.overhang_cm,
                    "TOOL_SHED": buildings.SHED_ROOF.overhang_cm}.get(record.kind, 0.0)
        if len(parts) == 1 and record.kind in ("HOUSE", "GARAGE_SHED", "TOOL_SHED"):
            overhang = 0.0  # a plain block
        if overhang:  # a roof: inside the footprint grown by the overhang, on its edge somewhere
            eaves = offset_ring(ring, overhang)
            assert outside_distance(plan, [tuple(p) for p in eaves]) <= 1e-3
            assert outside_distance(plan, ring) == pytest.approx(
                float(np.linalg.norm(eaves - ccw_ring(ring), axis=1).max()), abs=1e-3)
        elif record.kind.startswith("HEDGE"):
            # leaves are rooted on a core drawn in by 4 cm; a tip may pass the outline
            inset = len(hedges.core_ring(ring)) and not np.allclose(hedges.core_ring(ring), ccw_ring(ring))
            assert outside_distance(plan, ring) <= (LEAF_OVERSHOOT_CM if inset else hedges.LEAF_LENGTH_CM)
        else:
            assert outside_distance(plan, ring) <= 1e-3  # beds and blocks: exactly the footprint


class TestHeightIsExactForAwkwardNumbers:
    @pytest.mark.parametrize("height", [0.75, 3.0, 17.3, 40.0, 100.1, 333.333, 1234.5])
    @pytest.mark.parametrize("make", [
        lambda h: polygon_record("HOUSE", rectangle(900.0, 540.0), h,
                                 params={"ridge": ((-450.0, 0.0), (450.0, 0.0))}),
        lambda h: polygon_record("GARAGE_SHED", rectangle(400.0, 300.0), h),
        lambda h: line_record("FENCE", BENT_PATH, h, style="WOODEN_FENCE"),
        lambda h: line_record("FENCE", BENT_PATH, h, style="METAL_FENCE"),
        lambda h: line_record("FENCE", BENT_PATH, h, style="CHAIN_LINK"),
        lambda h: line_record("FENCE", BENT_PATH, h, style="HEDGE_FENCE"),
        lambda h: line_record("WALL", BENT_PATH, h),
        lambda h: polygon_record("RAISED_BED", rectangle(300.0, 120.0), h),
        lambda h: polygon_record("CONTAINER", rectangle(80.0, 40.0), h),
        lambda h: circle_record("CONTAINER_ROUND", 25.0, h, params={"container_material": "terracotta"}),
        lambda h: circle_record("CONTAINER_ROUND", 25.0, h),
        lambda h: polygon_record("GARDEN_BED", TRAPEZOID, h),
        lambda h: polygon_record("HEDGE_POLYGON", L_SHAPE, h),
    ], ids=["house", "shed", "picket", "metal", "chain", "hedge-fence", "wall", "raised-bed",
            "container", "pot", "tub", "garden-bed", "hedge"])
    def test_top_is_the_float32_of_the_height(self, make, height: float) -> None:
        record = make(height)
        parts = verify_builder(REGISTRY.builder_for(record.kind), record)
        points = positions(parts)
        assert float(points[:, 2].max()) == float(np.float32(height))
        assert float(points[:, 2].min()) == 0.0
        assert builder_of(record).fit_scale(record) == pytest.approx(1.0, abs=1e-6)


# ── buildings: gate G2 ───────────────────────────────────────────────────────


def ridge_line_in_mesh(parts: tuple[MeshPart, ...], height: float) -> np.ndarray:
    """The plan points of the mesh's top line — the ridge tile's apex."""
    roof = next(part for part in parts if part.name == "roof")
    points = roof.mesh.positions.astype(np.float64)
    top = points[points[:, 2] == np.float32(height)][:, :2]
    return np.unique(np.round(top, 4), axis=0)


def expected_ridge(ridge: tuple[tuple[float, float], tuple[float, float]],
                   beyond: tuple[float, float]) -> np.ndarray:
    start, end = np.asarray(ridge[0]), np.asarray(ridge[1])
    along = (end - start) / np.linalg.norm(end - start)
    return np.stack([start - beyond[0] * along, end + beyond[1] * along])


class TestRidgeIsTheRecordsRidge:
    """Gate G2, in the item-local frame (the real items, turned and moved, are in
    ``tests/integration/test_scene3d_built_world.py``)."""

    @pytest.mark.parametrize("angle", [0.0, 17.0, 90.0, 213.5])
    @pytest.mark.parametrize(("name", "ring", "ridge"), [
        ("rectangle", rectangle(900.0, 540.0), ((-450.0, 0.0), (450.0, 0.0))),
        ("off-centre", rectangle(900.0, 540.0), ((-450.0, 120.0), (450.0, 120.0))),
        ("L", L_SHAPE, L_RIDGE),
        ("short-axis", rectangle(900.0, 540.0), ((0.0, -270.0), (0.0, 270.0))),
    ])
    def test_a_ridge_that_meets_its_walls_squarely_runs_30_cm_on(
        self, name: str, ring, ridge, angle: float
    ) -> None:
        local_ridge = turned(ridge, angle)
        record = polygon_record("HOUSE", turned(ring, angle), 450.0,
                                params={"ridge": (local_ridge[0], local_ridge[1])})
        line = ridge_line_in_mesh(built(record), 450.0)
        expected = expected_ridge((local_ridge[0], local_ridge[1]), (30.0, 30.0))
        assert len(line) == 2, name
        for point in expected:
            assert float(np.linalg.norm(line - point, axis=1).min()) <= 0.1  # the gate: 1 mm
            assert float(np.linalg.norm(line - point, axis=1).min()) <= 2e-3  # measured: float32

    def test_a_ridge_that_meets_a_wall_at_an_angle_runs_overhang_over_cos_theta_on(self) -> None:
        """A dragged ridge: from the middle of the west wall to a point 250 cm up the
        east wall. The roof overhangs each wall by 30 cm measured SQUARE to it, so the
        ridge reaches the roof's edge 30 / cos θ beyond its end."""
        ridge = ((-450.0, -100.0), (450.0, 150.0))
        record = polygon_record("HOUSE", rectangle(900.0, 540.0), 450.0, params={"ridge": ridge})
        cos_theta = 900.0 / math.hypot(900.0, 250.0)  # the ridge against the walls' normal (±x)
        line = ridge_line_in_mesh(built(record), 450.0)
        expected = expected_ridge(ridge, (30.0 / cos_theta, 30.0 / cos_theta))
        assert 30.0 / cos_theta == pytest.approx(31.136, abs=1e-3)
        for point in expected:
            assert float(np.linalg.norm(line - point, axis=1).min()) <= 0.1
        assert np.allclose(sorted(line[:, 0]), [-480.0, 480.0], atol=1e-3)  # on the roof's edges

    def test_the_ridge_tile_does_not_bridge_a_courtyard(self) -> None:
        """A U-shaped house: its canonical ridge runs wall to wall THROUGH the
        courtyard. The roof is two planes through that line over both wings; the
        ridge tile exists only where there is roof — its outer ends are the record's
        ridge ends run on by the overhang, its inner ends the courtyard's eaves."""
        record = polygon_record("HOUSE", U_SHAPE, 450.0, params={"ridge": U_RIDGE})
        line = ridge_line_in_mesh(built(record), 450.0)
        assert np.allclose(line[:, 1], 0.0, atol=1e-3)  # every apex point is ON the ridge line
        assert np.allclose(sorted(line[:, 0]), [-480.0, -220.0, 220.0, 480.0], atol=2e-3)
        roof = next(part for part in built(record) if part.name == "roof").mesh
        assert not covers([roof], 0.0, 100.0) and covers([roof], 350.0, 100.0)  # open courtyard

    def test_a_trapezoid(self) -> None:
        """Slanted end walls (the ridge meets them at atan(150 / 400))."""
        ridge = ((-325.0, 0.0), (325.0, 0.0))
        record = polygon_record("HOUSE", TRAPEZOID, 450.0, params={"ridge": ridge})
        cos_theta = math.cos(math.atan2(150.0, 400.0))
        line = ridge_line_in_mesh(built(record), 450.0)
        for point in expected_ridge(ridge, (30.0 / cos_theta, 30.0 / cos_theta)):
            assert float(np.linalg.norm(line - point, axis=1).min()) <= 0.1

    def test_the_ridge_changes_the_mesh(self) -> None:
        ring = rectangle(900.0, 540.0)
        centred = polygon_record("HOUSE", ring, 450.0, params={"ridge": ((-450.0, 0.0), (450.0, 0.0))})
        dragged = polygon_record("HOUSE", ring, 450.0, params={"ridge": ((-450.0, 90.0), (450.0, 0.0))})
        assert not centred.same_geometry(dragged)
        assert built(centred) != built(dragged)
        assert ridge_line_in_mesh(built(dragged), 450.0)[:, 1].max() > 80.0

    def test_wall_tops_meet_the_roof_s_underside(self) -> None:
        """No gap between a wall and the slab above it: gable ends rise to the ridge,
        eaves stay low, and every wall top lies exactly ``slab`` under the tiles."""
        style = buildings.HOUSE_ROOF
        roof = gable_roof(rectangle(900.0, 540.0), ((-450.0, 0.0), (450.0, 0.0)), 450.0, style)
        assert roof is not None
        walls_points = roof.walls.positions.astype(np.float64)
        crease = 450.0 - style.cap_rise_cm
        expected = crease - style.slab_cm - roof.slope * np.abs(walls_points[:, 1])
        tops = walls_points[:, 2] > 0.0
        assert np.allclose(walls_points[tops, 2], expected[tops], atol=1e-3)
        assert float(walls_points[:, 2].max()) == pytest.approx(crease - style.slab_cm, abs=1e-3)
        assert roof.eave_cm == pytest.approx(crease - style.slab_cm - roof.slope * 270.0, abs=1e-9)
        assert roof.slope == pytest.approx(math.tan(math.radians(38.0)))  # eaves at 222 cm: no clamp

    def test_a_low_or_deep_building_keeps_its_eaves_up(self) -> None:
        style = buildings.HOUSE_ROOF
        deep = gable_roof(rectangle(900.0, 1000.0), ((-450.0, 0.0), (450.0, 0.0)), 450.0, style)
        assert deep is not None and deep.eave_cm == 220.0
        assert deep.slope < math.tan(math.radians(38.0))
        low = gable_roof(rectangle(300.0, 200.0), ((-150.0, 0.0), (150.0, 0.0)), 150.0, style)
        assert low is not None and low.eave_cm == pytest.approx(0.75 * (150.0 - 5.0 - 12.0))

    def test_the_slope_the_sun_faces_is_the_lit_one(self) -> None:
        """The L0 bug: each roof slab stored the OTHER slope's normal. Here the ridge
        runs east-west; the south slope must look south and catch a southern sun."""
        record = polygon_record("HOUSE", rectangle(900.0, 540.0), 450.0,
                                params={"ridge": ((-450.0, 0.0), (450.0, 0.0))})
        roof = next(part for part in built(record) if part.name == "roof")
        assert roof.material_kind == "roof"
        mesh = roof.mesh
        tile = np.asarray(palette.ROOF_TILE.linear, dtype=np.float32)
        is_tile = np.isclose(mesh.colors, tile, atol=1e-6).all(axis=1)
        points, normals = mesh.positions[is_tile], mesh.normals[is_tile].astype(np.float64)
        assert (normals[:, 2] > 0.5).all()  # tiles look up
        south, north = points[:, 1] < -1.0, points[:, 1] > 1.0
        assert south.any() and north.any()
        assert (normals[south, 1] < -0.3).all() and (normals[north, 1] > 0.3).all()
        noon = np.asarray(SunState(35.0, 180.0).direction_scene)  # the sun in the south
        lit, shaded = normals[south] @ noon, normals[north] @ noon
        assert float(lit.min()) > float(shaded.max()) + 0.5
        assert float(lit.min()) > 0.9  # 38° pitch against a 35° sun: almost square on
        evening = np.asarray(SunState(20.0, 0.0).direction_scene)  # … and from the north
        assert float((normals[north] @ evening).min()) > float((normals[south] @ evening).max())

    def test_underside_and_fascia(self) -> None:
        record = polygon_record("HOUSE", rectangle(900.0, 540.0), 450.0,
                                params={"ridge": ((-450.0, 0.0), (450.0, 0.0))})
        mesh = next(part for part in built(record) if part.name == "roof").mesh
        timber = np.isclose(mesh.colors, np.asarray(palette.TIMBER.linear, np.float32),
                            atol=1e-6).all(axis=1)
        normals = mesh.normals[timber]
        assert (normals[:, 2] < 0.1).all()  # the underside looks down, the fascia sideways
        assert (normals[:, 2] < -0.5).any() and (np.abs(normals[:, 2]) < 1e-6).any()
        outline = mesh.positions[:, :2]
        assert float(np.abs(outline[:, 0]).max()) == pytest.approx(480.0, abs=1e-3)  # 30 cm overhang
        assert float(np.abs(outline[:, 1]).max()) == pytest.approx(300.0, abs=1e-3)

    def test_a_house_without_a_usable_ridge_is_a_plain_block(self) -> None:
        ring = rectangle(900.0, 540.0)
        block = default_builder(polygon_record("HOUSE", ring, 450.0))
        for params in ({}, {"ridge": ((10.0, 0.0), (10.0, 0.0))}, {"ridge": "nonsense"},
                       {"ridge": ((0.0, 0.0),)}, {"ridge": ((0.0, True), (1.0, 2.0))},
                       {"ridge": ((2000.0, 0.0), (2000.0, 500.0))}):  # a line that misses the house
            parts = built(polygon_record("HOUSE", ring, 450.0, params=params))
            assert len(parts) == 1 and parts[0].name == "walls" and parts[0].tinted
            assert parts[0].mesh == block[0].mesh
        too_low = built(polygon_record("HOUSE", ring, 15.0,
                                       params={"ridge": ((-450.0, 0.0), (450.0, 0.0))}))
        assert [part.name for part in too_low] == ["walls"]  # no room for a slab: a block

    def test_buildings_have_tinted_walls_and_a_roof_of_their_own_material(self) -> None:
        for record in (
            polygon_record("HOUSE", rectangle(900.0, 540.0), 450.0,
                           params={"ridge": ((-450.0, 0.0), (450.0, 0.0))}),
            polygon_record("GARAGE_SHED", rectangle(400.0, 300.0), 250.0),
            polygon_record("TOOL_SHED", rectangle(200.0, 150.0), 250.0),
        ):
            walls_part, roof_part = built(record)
            assert (walls_part.name, walls_part.material_kind, walls_part.tinted) == ("walls", "vc", True)
            assert (roof_part.name, roof_part.material_kind, roof_part.tinted) == ("roof", "roof", False)


class TestShedRidge:
    """The spike derived a shed's ridge from its bounding box in the SCENE frame;
    in the item-local frame it is the building's own long axis, and turns with it."""

    @pytest.mark.parametrize(("ring", "expected"), [
        (rectangle(400.0, 300.0), ((-200.0, 0.0), (200.0, 0.0))),
        (rectangle(150.0, 200.0), ((0.0, -100.0), (0.0, 100.0))),
        (rectangle(300.0, 300.0), ((-150.0, 0.0), (150.0, 0.0))),   # a square: along x
        (((0.0, 0.0), (400.0, 0.0), (400.0, 100.0), (100.0, 100.0), (100.0, 300.0), (0.0, 300.0)),
         ((0.0, 150.0), (400.0, 150.0))),
    ])
    def test_long_axis_of_the_local_bounding_box(self, ring, expected) -> None:
        assert bounding_box_ridge(ring) == expected
        assert bounding_box_ridge(ring[:2]) is None

    @pytest.mark.parametrize("angle", [17.0, 90.0, 213.5])
    def test_it_turns_with_a_footprint_drawn_at_an_angle(self, angle: float) -> None:
        """A shed drawn as a turned polygon: the ridge is parallel to its long walls,
        not to the axes of its frame (the plain bounding box would lay it askew)."""
        ring = turned(rectangle(400.0, 300.0), angle)
        start, end = bounding_box_ridge(ring)
        expected = sorted(turned(((-200.0, 0.0), (200.0, 0.0)), angle))
        assert np.allclose([start, end], expected, atol=1e-6)
        line = ridge_line_in_mesh(built(polygon_record("GARAGE_SHED", ring, 250.0)), 250.0)
        on_the_eaves = sorted(turned(((-220.0, 0.0), (220.0, 0.0)), angle))
        assert np.allclose(sorted(map(tuple, line)), on_the_eaves, atol=2e-3)

    def test_the_shed_s_ridge_tile_runs_along_it(self) -> None:
        record = polygon_record("GARAGE_SHED", rectangle(400.0, 300.0), 250.0)
        line = ridge_line_in_mesh(built(record), 250.0)
        overhang = buildings.SHED_ROOF.overhang_cm
        assert np.allclose(line, [[-200.0 - overhang, 0.0], [200.0 + overhang, 0.0]], atol=1e-3)
        deep = polygon_record("TOOL_SHED", rectangle(150.0, 200.0), 250.0)
        line = ridge_line_in_mesh(built(deep), 250.0)
        assert np.allclose(line, [[0.0, -100.0 - overhang], [0.0, 100.0 + overhang]], atol=1e-3)


# ── polylines: built from the centre line, never from the stroke polygon ─────


FENCE_STYLES = ["WOODEN_FENCE", "METAL_FENCE", "CHAIN_LINK", "HEDGE_FENCE", "STONE_WALL"]


class TestPolylinesAreBuiltFromTheCentreLine:
    def test_the_trap_is_real(self) -> None:
        """A closed fence's stroke polygon is an outer ring and a hole; extruding
        every ring of ``footprints`` (the default builder) fills the garden."""
        record = line_record("FENCE", CLOSED_PATH, 120.0)
        assert len(record.footprints) == 2
        slab = [part.mesh for part in default_builder(record)]
        assert covers(slab, 37.0, 11.0)  # the garden is under a lid …
        assert plan_area(slab) == pytest.approx(803.0 * 603.0, rel=1e-3)  # … all 48 m² of it

    @pytest.mark.parametrize("kind", ["FENCE", "WALL"])
    @pytest.mark.parametrize("style", [None, *FENCE_STYLES])
    @pytest.mark.parametrize("path", [CLOSED_PATH, NEARLY_CLOSED_PATH], ids=["closed", "nearly-closed"])
    def test_a_loop_encloses_nothing(self, kind: str, style: str | None, path) -> None:
        record = line_record(kind, path, 120.0, style=style)
        meshes = [part.mesh for part in built(record)]
        assert meshes
        for x, y in ((37.0, 11.0), (310.0, 180.0), (-290.0, -210.0), (20.0, 250.0)):
            assert not covers(meshes, x, y)  # the garden inside the fence is open sky
        ring = LOOP
        length = path_length([*ring, ring[0]])
        area = plan_area(meshes)
        assert area < 0.3 * 800.0 * 600.0  # a strip along 28 m of line, not the enclosed 48 m²
        solid = style == "STONE_WALL" or (kind == "WALL" and style is None)
        if solid:  # a wall is a solid strip: plan area = centre-line length x coping width
            width = walls.body_thickness_cm(record) + 2.0 * walls.COPING_OVERSAIL_CM
            if path is CLOSED_PATH:
                assert area == pytest.approx(length * width, rel=1e-4)
            else:  # 5 cm short of closed: the two flat ends overlap the first corner
                assert area == pytest.approx(length * width, rel=0.02)

    @pytest.mark.parametrize("style", FENCE_STYLES)
    def test_a_closed_line_and_its_open_twin_differ_only_at_the_seam(self, style: str) -> None:
        closed = built(line_record("FENCE", CLOSED_PATH, 120.0, style=style))
        nearly = built(line_record("FENCE", NEARLY_CLOSED_PATH, 120.0, style=style))
        assert triangle_count(closed) > 0 and triangle_count(nearly) > 0
        assert float(positions(closed)[:, 2].max()) == float(positions(nearly)[:, 2].max()) == 120.0

    def test_a_wall_s_plan_area_is_length_times_width(self) -> None:
        record = line_record("WALL", BENT_PATH, 200.0, width=4.0)
        (wall,) = built(record)
        assert (wall.name, wall.tinted, wall.material_kind) == ("wall", True, "vc")
        width = 30.0 + 2.0 * walls.COPING_OVERSAIL_CM  # a 4 cm stroke: the nominal 30 cm body
        assert plan_area([wall.mesh]) == pytest.approx(path_length(BENT_PATH) * width, rel=1e-4)
        wide = line_record("WALL", BENT_PATH, 200.0, width=45.0)  # a stroke wider than a wall: obeyed
        assert plan_area([built(wide)[0].mesh]) == pytest.approx(
            path_length(BENT_PATH) * (45.0 + 8.0), rel=1e-4)

    def test_wall_profile(self) -> None:
        (wall,) = built(line_record("WALL", ((-250.0, 0.0), (250.0, 0.0)), 200.0, width=4.0))
        points = wall.mesh.positions.astype(np.float64)
        assert float(np.abs(points[:, 1]).max()) == pytest.approx(19.0, abs=1e-4)  # coping: 15 + 4
        body = points[points[:, 2] < 191.9]
        assert float(np.abs(body[:, 1]).max()) == pytest.approx(15.0, abs=1e-4)
        assert wall.mesh.triangle_count == 9 * 2 + 2 * 8  # nine faces, two capped ends

    def test_the_style_picks_the_fence(self) -> None:
        signatures = {}
        for style in [None, *FENCE_STYLES, "GRAVEL_PATH"]:
            parts = built(line_record("FENCE", BENT_PATH, 120.0, style=style))
            signatures[style] = (tuple(p.name for p in parts), triangle_count(parts))
            assert all(part.tinted for part in parts)  # a fence is seen in its own stroke colour
        assert signatures[None] == signatures["WOODEN_FENCE"] == signatures["GRAVEL_PATH"]
        assert len({signatures[s] for s in FENCE_STYLES}) == len(FENCE_STYLES)  # five different fences
        assert signatures["HEDGE_FENCE"][0] == ("core", "leaves")
        assert signatures["STONE_WALL"][0] == ("wall",)

    def test_a_fence_style_on_a_wall_builds_that_fence(self) -> None:
        for style in ("WOODEN_FENCE", "METAL_FENCE", "CHAIN_LINK", "HEDGE_FENCE"):
            as_wall = built(line_record("WALL", BENT_PATH, 120.0, style=style))
            as_fence = built(line_record("FENCE", BENT_PATH, 120.0, style=style))
            assert [p.mesh for p in as_wall] == [p.mesh for p in as_fence]
        stone = built(line_record("WALL", BENT_PATH, 120.0, style="STONE_WALL"))
        assert stone == built(line_record("WALL", BENT_PATH, 120.0)) != ()

    def test_picket_fence_anatomy(self) -> None:
        """Posts are the top; pickets stop 0.3 … 4 cm under them (the L0 fence)."""
        (fence,) = built(line_record("FENCE", ((0.0, 0.0), (400.0, 0.0)), 120.0, style="WOODEN_FENCE"))
        points = fence.mesh.positions.astype(np.float64)
        at_top = points[points[:, 2] == 120.0]
        assert set(np.round(at_top[:, 0] / 200.0).astype(int)) == {0, 1, 2}  # a post every 2 m
        assert float(np.abs(at_top[:, 1]).max()) <= 4.5  # 9 cm posts
        tops = np.unique(np.round(points[:, 2], 3))
        picket_tops = tops[(tops > 110.0) & (tops < 120.0)]
        assert picket_tops.min() >= 116.0 - 1e-3 and picket_tops.max() <= 119.7 + 1e-3
        assert len(picket_tops) > 5  # no two neighbours level
        assert fence.mesh.triangle_count == 3 * 38 + 2 * 10 + 33 * 10

    def test_a_shared_corner_has_one_post(self) -> None:
        (bent,) = built(line_record("FENCE", ((0.0, 0.0), (100.0, 0.0), (100.0, 100.0)), 120.0))
        tops = bent.mesh.positions[bent.mesh.positions[:, 2] == 120.0][:, :2].astype(np.float64)
        centres = {(round(float(x) / 100.0), round(float(y) / 100.0)) for x, y in tops}
        assert centres == {(0, 0), (1, 0), (1, 1)}
        assert bent.mesh.triangle_count == 3 * 38 + 2 * 2 * 10 + 2 * 8 * 10
        (loop,) = built(line_record("FENCE", CLOSED_PATH, 120.0))
        loop_tops = loop.mesh.positions[loop.mesh.positions[:, 2] == 120.0]
        posts = len(loop_tops) // 42  # a bevelled post: 18 top-face + 24 bevel vertices at the top
        assert posts == 4 + 3 + 2 + 3 + 2  # 4 corners + 3 along each 8 m side + 2 along each 6 m side

    @pytest.mark.parametrize("style", ["WOODEN_FENCE", "METAL_FENCE", "CHAIN_LINK"])
    def test_fifty_metres_of_fence_do_not_explode(self, style: str) -> None:
        record = line_record("FENCE", ((-2500.0, 0.0), (2500.0, 0.0)), 120.0, style=style)
        count = triangle_count(built(record))
        assert count <= fences.triangle_budget(record)
        per_metre = count / 50.0
        assert per_metre <= {"WOODEN_FENCE": 105.0, "METAL_FENCE": 100.0, "CHAIN_LINK": 60.0}[style]
        double = line_record("FENCE", ((-5000.0, 0.0), (5000.0, 0.0)), 120.0, style=style)
        assert triangle_count(built(double)) <= 2 * count + 100  # linear in the length

    def test_a_stroke_wider_than_a_post_widens_the_posts(self) -> None:
        (fence,) = built(line_record("FENCE", ((0.0, 0.0), (400.0, 0.0)), 120.0, width=20.0))
        assert float(np.abs(fence.mesh.positions[:, 1]).max()) == pytest.approx(10.0, abs=1e-4)

    def test_degenerate_lines(self) -> None:
        assert built(line_record("FENCE", ((0.0, 0.0), (0.0, 0.0)), 120.0)) == ()
        empty = dataclasses.replace(line_record("FENCE", BENT_PATH, 120.0), path=())
        assert built(empty) == ()
        assert built(dataclasses.replace(line_record("WALL", BENT_PATH, 200.0), path=())) == ()


# ── beds: the soil rule ──────────────────────────────────────────────────────


def soil_level(parts: tuple[MeshPart, ...]) -> float:
    soil = np.asarray(palette.SOIL.linear, dtype=np.float32)
    for part in parts:
        mesh = part.mesh
        is_soil = np.isclose(mesh.colors, soil, atol=1e-6).all(axis=1)
        if is_soil.any():
            levels = np.unique(mesh.positions[is_soil][:, 2])
            assert len(levels) == 1
            assert (mesh.normals[is_soil][:, 2] > 0.999).all()  # a flat bed of soil, looking up
            return float(levels[0])
    raise AssertionError("no soil")


class TestSoilRule:
    @pytest.mark.parametrize("height", [10.0, 30.0, 40.0, 75.0, 120.5])
    @pytest.mark.parametrize("make", [
        lambda h: polygon_record("RAISED_BED", rectangle(300.0, 120.0), h),
        lambda h: polygon_record("CONTAINER", rectangle(80.0, 40.0), h),
        lambda h: polygon_record("WALL_PLANTER", rectangle(120.0, 25.0), h),
        lambda h: circle_record("CONTAINER_ROUND", 25.0, h),
        lambda h: circle_record("CONTAINER_ROUND", 25.0, h, params={"container_material": "terracotta"}),
        lambda h: polygon_record("GARDEN_BED", TRAPEZOID, h),
    ], ids=["raised-bed", "container", "wall-planter", "tub", "pot", "garden-bed"])
    def test_the_soil_lies_two_cm_below_the_rim_where_plants_stand(self, make, height: float) -> None:
        """Truth gate "Base": the mesh's soil and the plant's base are ONE function."""
        parts = built(make(height))
        assert soil_level(parts) == float(np.float32(soil_surface_cm(height)))
        assert soil_level(parts) == float(np.float32(height - SOIL_DROP_CM))
        assert float(positions(parts)[:, 2].max()) == float(np.float32(height))  # the rim

    def test_a_rim_lower_than_the_drop_has_no_soil_face(self) -> None:
        parts = built(polygon_record("RAISED_BED", rectangle(300.0, 120.0), 1.5))
        assert soil_surface_cm(1.5) == 0.0
        with pytest.raises(AssertionError, match="no soil"):
            soil_level(parts)

    def test_raised_bed_is_planks_and_soil_inside_the_footprint(self) -> None:
        (bed,) = built(polygon_record("RAISED_BED", rectangle(300.0, 120.0), 40.0))
        assert (bed.name, bed.material_kind, bed.tinted) == ("bed", "vc", False)
        points = bed.mesh.positions.astype(np.float64)
        assert float(np.abs(points[:, 0]).max()) == 150.0 and float(np.abs(points[:, 1]).max()) == 60.0
        soil = np.isclose(bed.mesh.colors, np.asarray(palette.SOIL.linear, np.float32),
                          atol=1e-6).all(axis=1)
        inner = points[soil]
        assert float(np.abs(inner[:, 0]).max()) == 145.0 and float(np.abs(inner[:, 1]).max()) == 55.0
        assert beds.plank_rows(40.0) == 3 and beds.plank_rows(90.0) == 6 and beds.plank_rows(5.0) == 1
        groove = np.isclose(bed.mesh.colors, np.asarray(palette.PLANK_GROOVE.linear, np.float32),
                            atol=1e-6).all(axis=1)
        assert set(np.round(points[groove][:, 2], 2)) >= {12.53, 13.33, 14.13, 25.87, 26.67, 27.47}

    def test_containers_are_tinted_walls_and_baked_soil(self) -> None:
        for record in (polygon_record("CONTAINER", rectangle(80.0, 40.0), 30.0),
                       circle_record("CONTAINER_ROUND", 25.0, 30.0),
                       polygon_record("GARDEN_BED", TRAPEZOID, 25.0)):
            walls_part, soil_part = built(record)
            assert (walls_part.name, walls_part.tinted) == ("walls", True)
            assert (soil_part.name, soil_part.tinted, soil_part.material_kind) == ("soil", False, "vc")

    def test_a_round_container_s_rim_is_the_plans_footprint(self) -> None:
        from open_garden_planner.core.shadow_geometry import circle_footprint

        for material in ("terracotta", "plastic"):
            record = circle_record("CONTAINER_ROUND", 25.0, 30.0, params={"container_material": material})
            walls_part, _soil = built(record)
            points = walls_part.mesh.positions.astype(np.float64)
            radius = np.hypot(points[:, 0], points[:, 1])
            assert float(radius.max()) == pytest.approx(25.0, abs=1e-4)  # nothing outside the footprint
            rim = points[np.isclose(radius, 25.0, atol=1e-3)]
            for corner in circle_footprint(0.0, 0.0, 25.0):  # the 2D shadow's own 24 vertices
                assert np.isclose(rim[:, :2], corner, atol=1e-4).all(axis=1).any()
        pot = built(circle_record("CONTAINER_ROUND", 25.0, 30.0,
                                  params={"container_material": "terracotta"}))[0].mesh
        tub = built(circle_record("CONTAINER_ROUND", 25.0, 30.0))[0].mesh
        foot = lambda mesh: float(np.hypot(*mesh.positions[mesh.positions[:, 2] == 0.0][:, :2].T).max())  # noqa: E731
        assert foot(pot) == pytest.approx(18.0, abs=1e-4) and foot(tub) == pytest.approx(21.5, abs=1e-4)

    def test_the_material_shapes_the_container_and_never_colours_it(self) -> None:
        meshes = {}
        for material in ("terracotta", "plastic", "wood", "metal", "unobtainium", None):
            params = {} if material is None else {"container_material": material}
            walls_part, _soil = built(polygon_record("CONTAINER", rectangle(80.0, 40.0), 30.0,
                                                     params=params))
            assert np.all(walls_part.mesh.colors == 1.0)
            meshes[material] = walls_part.mesh
        assert meshes["unobtainium"] == meshes[None] == meshes["plastic"] == meshes["metal"]
        assert meshes["wood"] != meshes["plastic"] != meshes["terracotta"]

    def test_an_in_ground_bed_is_ground(self) -> None:
        assert built(polygon_record("GARDEN_BED", TRAPEZOID, None)) == ()
        assert nominal_height_cm("GARDEN_BED") is None


# ── hedges: gate G6 ──────────────────────────────────────────────────────────


class TestHedges:
    def test_parts(self) -> None:
        core, leaves = built(polygon_record("HEDGE_SECTION", rectangle(200.0, 50.0), 150.0))
        assert (core.name, core.material_kind, core.tinted) == ("core", "vc", False)
        assert (leaves.name, leaves.material_kind, leaves.tinted) == ("leaves", "foliage", False)
        assert float(core.mesh.positions[:, 2].max()) == 147.0  # under the clipped top
        assert float(leaves.mesh.positions[:, 2].max()) == 150.0  # the clipped top IS the height
        assert float(leaves.mesh.positions[:, 2].min()) >= 0.0  # no leaf in the ground

    def test_the_top_is_clipped_flat(self) -> None:
        _core, leaves = built(polygon_record("HEDGE_SECTION", rectangle(200.0, 50.0), 150.0))
        z = leaves.mesh.positions[:, 2]
        assert int((z == 150.0).sum()) > 50  # many leaf vertices lie ON the trimmed top

    def test_leaf_count_follows_the_shell_area(self) -> None:
        assert hedges.leaf_count(3000.0) == 200 and hedges.leaf_count(30000.0) == 1000
        assert hedges.leaf_count(1e9) == hedges.MAX_LEAVES == 6000
        small = built(polygon_record("HEDGE_SECTION", rectangle(100.0, 40.0), 60.0))[1].mesh
        large = built(polygon_record("HEDGE_SECTION", rectangle(300.0, 60.0), 150.0))[1].mesh
        ring = hedges.core_ring(rectangle(100.0, 40.0))
        expected = hedges.leaf_count(hedges.shell_area_cm2(hedges.ring_shell(ring, 60.0)))
        assert small.triangle_count == 2 * expected
        assert large.triangle_count > small.triangle_count

    def test_wind_weights(self) -> None:
        _core, leaves = built(polygon_record("HEDGE_SECTION", rectangle(200.0, 50.0), 150.0))
        uv = leaves.mesh.uvs
        assert float(uv.min()) >= 0.0 and float(uv.max()) <= 1.0
        assert float(uv[:, 0].max()) > 0.95 and float(uv[:, 0].min()) < 0.1  # crown sways, foot does not
        assert len(np.unique(uv[:, 1])) > 100  # a phase per leaf

    def test_the_same_hedge_is_the_same_mesh(self) -> None:
        record = polygon_record("HEDGE_POLYGON", L_SHAPE, 150.0, item_id="hedge-a")
        first, second = built(record), built(record)
        for a, b in zip(first, second, strict=True):
            assert float(np.abs(a.mesh.positions - b.mesh.positions).max()) <= 1e-6
            assert float(np.abs(a.mesh.colors - b.mesh.colors).max()) <= 1e-6
            assert np.array_equal(a.mesh.indices, b.mesh.indices)

    def test_two_hedges_are_never_clones(self) -> None:
        """Same footprint, same height, another item: another hedge."""
        one = built(polygon_record("HEDGE_POLYGON", L_SHAPE, 150.0, item_id="hedge-a"))
        two = built(polygon_record("HEDGE_POLYGON", L_SHAPE, 150.0, item_id="hedge-b"))
        assert one[0].mesh == two[0].mesh  # the core is the footprint: identical
        a, b = one[1].mesh.positions, two[1].mesh.positions
        assert a.shape == b.shape
        moved = np.linalg.norm(a.astype(np.float64) - b.astype(np.float64), axis=1) > 1.0
        assert float(moved.mean()) > 0.95  # practically every leaf sits elsewhere
        fence_one = built(line_record("FENCE", BENT_PATH, 120.0, style="HEDGE_FENCE", item_id="f-1"))
        fence_two = built(line_record("FENCE", BENT_PATH, 120.0, style="HEDGE_FENCE", item_id="f-2"))
        assert fence_one[1].mesh != fence_two[1].mesh

    def test_the_seed_is_the_record_s_param_and_nothing_else(self) -> None:
        record = polygon_record("HEDGE_POLYGON", L_SHAPE, 150.0, item_id="hedge-a")
        renamed = dataclasses.replace(record, item_id="another-id", name="Yew")
        assert built(renamed) == built(record)  # the id is not read: params["seed"] is
        reseeded = dataclasses.replace(record, params=Params({"seed": "hedge-z"}))
        assert built(reseeded)[1].mesh != built(record)[1].mesh

    def test_a_hedge_fence_is_tinted_and_as_thick_as_a_hedge(self) -> None:
        record = line_record("FENCE", ((-250.0, 0.0), (250.0, 0.0)), 120.0, width=8.0, style="HEDGE_FENCE")
        core, leaves = built(record)
        assert core.tinted and leaves.tinted and leaves.material_kind == "foliage"
        assert np.all(leaves.mesh.colors == 1.0) and np.all(core.mesh.colors == 1.0)
        assert float(np.abs(core.mesh.positions[:, 1]).max()) == pytest.approx(16.0, abs=1e-4)  # 40/2 − 4
        assert float(np.abs(leaves.mesh.positions[:, 1]).max()) < 20.0 + 6.0


# ── gate G5: colours ─────────────────────────────────────────────────────────


def _srgb8(linear: np.ndarray) -> np.ndarray:
    value = np.clip(linear.astype(np.float64), 0.0, 1.0)
    encoded = np.where(value <= 0.0031308, value * 12.92, 1.055 * value ** (1.0 / 2.4) - 0.055)
    return np.round(encoded * 255.0)


PALETTE_SRGB = np.asarray([entry.srgb for entry in palette.ALL], dtype=np.float64)


@pytest.mark.parametrize("case", CASES, ids=str)
def test_every_vertex_colour_is_a_2d_table_entry(case: Case) -> None:
    """Gate G5. A tinted part is pure white — the sink multiplies it with the item's
    own colour. Every other vertex colour maps back (linear → sRGB, ± 1/255) to an
    entry of ``builders/palette.py``, whose entries are pinned to their 2D sources
    by ``test_scene3d_builders_palette.py``. No stray literal."""
    for part in built(case.record):
        colors = part.mesh.colors
        assert np.all(colors[:, 3] == 1.0)
        if part.tinted:
            assert np.all(colors == 1.0), f"{part.name}: a tinted part carries no colour of its own"
            continue
        unique = np.unique(colors[:, :3], axis=0)
        srgb = _srgb8(unique)
        nearest = np.abs(srgb[:, None, :] - PALETTE_SRGB[None, :, :]).max(axis=2).min(axis=1)
        assert float(nearest.max()) <= 1.0, f"{part.name}: {srgb[nearest.argmax()]} is in no 2D table"


def test_which_colours_each_kind_uses() -> None:
    def used(record: Record) -> set[str]:
        names: set[str] = set()
        for part in built(record):
            if part.tinted:
                names.add("TINT")
                continue
            for entry in palette.ALL:
                target = np.asarray(srgb8_to_linear(entry.rgba8), dtype=np.float32)
                if np.isclose(part.mesh.colors, target, atol=1e-6).all(axis=1).any():
                    names.add(entry.name)
        return names

    house = polygon_record("HOUSE", rectangle(900.0, 540.0), 450.0,
                           params={"ridge": ((-450.0, 0.0), (450.0, 0.0))})
    assert used(house) == {"TINT", "ROOF_TILE", "ROOF_TILE_CAP", "TIMBER"}
    assert used(polygon_record("TOOL_SHED", rectangle(200.0, 150.0), 250.0)) == {
        "TINT", "SHINGLE", "SHINGLE_CAP", "TIMBER"}
    assert used(polygon_record("RAISED_BED", rectangle(300.0, 120.0), 40.0)) == {
        "PLANK", "PLANK_GROOVE", "SOIL"}
    assert used(circle_record("CONTAINER_ROUND", 25.0, 30.0)) == {"TINT", "SOIL"}
    assert used(line_record("FENCE", BENT_PATH, 120.0)) == {"TINT"}
    assert used(line_record("WALL", BENT_PATH, 200.0)) == {"TINT"}
    assert used(polygon_record("HEDGE_POLYGON", L_SHAPE, 150.0)) == {
        "HEDGE_CORE", "HEDGE_LEAF_0", "HEDGE_LEAF_1", "HEDGE_LEAF_2", "HEDGE_LEAF_3"}


# ── through the pipeline ─────────────────────────────────────────────────────


def test_the_whole_matrix_through_scene_sync_and_a_recording_sink() -> None:
    records = {}
    for index, case in enumerate(CASES):
        records[f"item-{index}"] = dataclasses.replace(case.record, item_id=f"item-{index}")
    sink = RecordingSink()
    sync = SceneSync(sink, production_registry(), validate=True)
    sync.apply(records)
    assert sync.failures == {} and sync.build_count == len(records)
    assert sink.item_ids() == tuple(records)
    sink.clear_calls()
    assert sync.apply(records).is_empty and sink.calls == []  # nothing changed: nothing built
    assert sync.build_count == len(records)
