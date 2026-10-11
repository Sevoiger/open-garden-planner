"""Plane polygons of the 3D pipeline: orientation, nesting, triangulation with holes.

Phase 17 L1.1, fix round 1 (#385). Qt-free. The defect this module exists for: a
closed fence's stroke is TWO rings — an outer and a hole — and the first contract
handed both to builders as "footprints". Extruded as two solids, 3.6 m² of fence
became an 80 m² slab, and every gate passed. So: outers and holes are told apart
by orientation, grouped, and a region is triangulated as outer MINUS holes.
"""

from __future__ import annotations

import math
import random

import pytest

from open_garden_planner.core import scene3d
from open_garden_planner.core.scene3d import (
    ccw,
    cw,
    group_rings,
    is_buildable_region,
    is_flat_ring,
    is_simple_ring,
    point_in_regions,
    point_in_ring,
    regions_area,
    ring_area,
    signed_area,
    triangulate_polygon,
    triangulate_region,
)
from open_garden_planner.core.scene3d import legacy as legacy_module
from open_garden_planner.core.scene3d import polygon as polygon_module
from open_garden_planner.core.shadow_geometry import polyline_footprint

SQUARE = ((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0))
L_SHAPE = ((0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (50.0, 50.0), (50.0, 100.0), (0.0, 100.0))
BOW_TIE = ((0.0, 0.0), (400.0, 300.0), (400.0, 0.0), (0.0, 300.0))
LOPSIDED_BOW_TIE = ((0.0, 0.0), (400.0, 300.0), (400.0, 0.0), (0.0, 200.0))
PENTAGRAM = ((0.0, 100.0), (58.8, -80.9), (-95.1, 30.9), (95.1, 30.9), (-58.8, -80.9))


def rect(x0: float, y0: float, x1: float, y1: float, *, clockwise: bool = False):
    ring = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    return ring[::-1] if clockwise else ring


def tri_area(points, triangle) -> float:
    a, b, c = (points[i] for i in triangle)
    return ((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2.0


def assert_triangulates(outer, holes, *, flat_corners: bool = False) -> None:
    """The three facts of a correct triangulation of outer-minus-holes: every
    triangle counter-clockwise, their areas summing to the region's, and no
    triangle's centroid in a hole or outside the outer ring. ``flat_corners``:
    the region may have three vertices of different rings on one line, and an
    ear without an area yields no triangle — so there may be fewer."""
    points, triangles = triangulate_region(outer, holes)
    assert points == tuple(outer) + tuple(p for hole in holes for p in hole)
    areas = [tri_area(points, t) for t in triangles]
    assert all(area > 0.0 for area in areas), min(areas)
    expected = signed_area(outer) + sum(signed_area(h) for h in holes)
    assert sum(areas) == pytest.approx(expected, rel=1e-9)
    for triangle in triangles:
        cx = sum(points[i][0] for i in triangle) / 3.0
        cy = sum(points[i][1] for i in triangle) / 3.0
        assert point_in_regions(cx, cy, ((tuple(outer), tuple(holes)),)), (cx, cy)
    # outer + holes joined by one bridge each: n + 2h vertices on the merged ring
    full = len(points) + 2 * len(holes) - 2
    assert len(triangles) <= full if flat_corners else len(triangles) == full
    assert min(areas) > 1e-9  # no triangle that is nothing


# ── orientation ───────────────────────────────────────────────────────────────


class TestOrientation:
    def test_signed_area_is_positive_counter_clockwise(self) -> None:
        assert signed_area(SQUARE) == 10000.0
        assert signed_area(SQUARE[::-1]) == -10000.0
        assert signed_area(L_SHAPE) == 7500.0
        assert ring_area(SQUARE) == signed_area(SQUARE)
        assert signed_area(BOW_TIE) == 0.0  # two lobes of opposite sense cancel

    def test_ccw_and_cw_reverse_only_when_needed(self) -> None:
        assert ccw(SQUARE) is SQUARE and ccw(SQUARE[::-1]) == SQUARE
        assert cw(SQUARE) == SQUARE[::-1] and cw(SQUARE[::-1]) == SQUARE[::-1]

    def test_point_in_ring_and_regions(self) -> None:
        assert point_in_ring(50.0, 50.0, SQUARE) and not point_in_ring(150.0, 50.0, SQUARE)
        assert point_in_ring(25.0, 75.0, L_SHAPE) and not point_in_ring(75.0, 75.0, L_SHAPE)
        hole = rect(40.0, 40.0, 60.0, 60.0, clockwise=True)
        regions = ((SQUARE, (hole,)),)
        assert point_in_regions(10.0, 10.0, regions)
        assert not point_in_regions(50.0, 50.0, regions)  # in the hole
        assert not point_in_regions(500.0, 50.0, regions)


class TestSimple:
    @pytest.mark.parametrize("ring", [SQUARE, SQUARE[::-1], L_SHAPE, rect(-5.0, -5.0, 5.0, 5.0)])
    def test_simple_rings(self, ring) -> None:
        assert is_simple_ring(tuple(ring))

    @pytest.mark.parametrize(
        "ring",
        [BOW_TIE, LOPSIDED_BOW_TIE, PENTAGRAM,
         ((0.0, 0.0), (1.0, 1.0)),  # two points
         ((0.0, 0.0), (10.0, 0.0), (20.0, 0.0)),  # no area
         ((0.0, 0.0), (10.0, 0.0), (10.0, 0.0), (0.0, 10.0)),  # a repeated vertex
         ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (5.0, 0.0), (0.0, 10.0))],  # touches its own edge
        ids=["bow-tie", "lopsided bow-tie", "pentagram", "two points", "collinear",
             "repeated vertex", "touching"],
    )
    def test_rings_that_are_not_simple(self, ring) -> None:
        assert not is_simple_ring(tuple(ring))

    def test_a_lopsided_bow_tie_has_an_area_and_a_valid_looking_orientation(self) -> None:
        """Why orientation alone is not enough: this ring crosses itself and still
        has a non-zero signed area, so a sign check waves it through."""
        assert signed_area(LOPSIDED_BOW_TIE) != 0.0
        assert not is_simple_ring(LOPSIDED_BOW_TIE)

    def test_flat_is_not_the_same_as_no_net_area(self) -> None:
        """A symmetric bow-tie has a signed area of exactly zero and still covers
        ground. "Skip the rings without area" would drop it silently; only a ring
        whose corners all lie on one line is skipped."""
        assert signed_area(BOW_TIE) == 0.0 and not is_flat_ring(BOW_TIE)
        assert is_flat_ring(()) and is_flat_ring(((0.0, 0.0), (1.0, 1.0)))
        assert is_flat_ring(((0.0, 0.0), (10.0, 0.0), (20.0, 0.0)))
        assert is_flat_ring(((5.0, 5.0), (5.0, 5.0), (5.0, 5.0)))  # one point, three times
        assert is_flat_ring(((0.0, 0.0), (30.0, 40.0), (15.0, 20.0), (60.0, 80.0)))
        assert not is_flat_ring(SQUARE) and not is_flat_ring(SQUARE[::-1])
        assert not is_flat_ring(LOPSIDED_BOW_TIE) and not is_flat_ring(PENTAGRAM)


def _wobbly(n: int, rng: random.Random, *, tangle: bool) -> tuple[tuple[float, float], ...]:
    """A star-shaped ring of ``n`` vertices (simple by construction); ``tangle``
    swaps two of its vertices, which makes two pairs of edges cross."""
    radii = [300.0 + rng.uniform(-80.0, 80.0) for _ in range(n)]
    ring = [(r * math.cos(2.0 * math.pi * k / n), r * math.sin(2.0 * math.pi * k / n))
            for k, r in enumerate(radii)]
    if tangle:
        i = rng.randrange(n)
        j = (i + rng.randrange(2, n - 2)) % n
        ring[i], ring[j] = ring[j], ring[i]
    return tuple(ring)


class TestSimpleRingAtScale:
    """The simplicity test is quadratic and runs on every edit of a ring. From
    ``SIMPLE_RING_NUMPY_FROM`` vertices on it runs on numpy — and must say exactly
    what the plain loop says."""

    @pytest.mark.parametrize("n", [5, 31, 32, 33, 100, 257, 600])
    def test_both_implementations_agree(self, n: int) -> None:
        rng = random.Random(385 + n)
        verdicts = set()
        for case in range(12):
            ring = _wobbly(n, rng, tangle=case % 2 == 1)
            loop = polygon_module._edges_touch_loop(ring)
            assert polygon_module._edges_touch_numpy(ring) is loop, (n, case)
            assert is_simple_ring(ring) is (not loop)
            verdicts.add(loop)
        assert verdicts == {True, False}  # both answers were exercised

    @pytest.mark.parametrize("n", [40, 300])
    def test_touching_without_crossing_is_seen_at_scale(self, n: int) -> None:
        """The cases only the ``== 0`` branches catch: a vertex lying exactly on
        another edge, and a vertex repeated elsewhere on the ring."""
        base = [(float(k), 0.0) for k in range(n // 2)]  # a comb's back, then its teeth
        top = [(float(k), 50.0 + (k % 3)) for k in reversed(range(n // 2))]
        ring = tuple(base + top)
        assert is_simple_ring(ring)
        on_an_edge = list(ring)
        on_an_edge[-1] = (2.5, 0.0)  # the last vertex now sits on the edge (2, 0)-(3, 0)
        assert not is_simple_ring(tuple(on_an_edge))
        assert polygon_module._edges_touch_numpy(tuple(on_an_edge))
        assert polygon_module._edges_touch_loop(tuple(on_an_edge))
        repeated = list(ring)
        repeated[-1] = ring[3]  # a vertex visited twice
        assert not is_simple_ring(tuple(repeated))

    def test_a_crossing_in_the_last_block_of_rows_is_found(self) -> None:
        """The array version works through the edges in blocks; a ring longer than
        two blocks whose only crossing lies among its last edges must still be seen."""
        n = 2 * polygon_module._SIMPLE_RING_ROWS + 100
        ring = _wobbly(n, random.Random(1), tangle=False)
        assert is_simple_ring(ring)
        crossed = list(ring)
        crossed[n - 40], crossed[n - 10] = crossed[n - 10], crossed[n - 40]
        assert polygon_module._edges_touch_loop(tuple(crossed))
        assert not is_simple_ring(tuple(crossed))

    def test_small_rings_take_the_loop_and_large_ones_the_arrays(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        used: list[str] = []
        loop, arrays = polygon_module._edges_touch_loop, polygon_module._edges_touch_numpy
        monkeypatch.setattr(polygon_module, "_edges_touch_loop",
                            lambda ring: used.append("loop") or loop(ring))
        monkeypatch.setattr(polygon_module, "_edges_touch_numpy",
                            lambda ring: used.append("arrays") or arrays(ring))
        threshold = polygon_module.SIMPLE_RING_NUMPY_FROM
        rng = random.Random(7)
        assert is_simple_ring(_wobbly(threshold - 1, rng, tangle=False))
        assert is_simple_ring(_wobbly(threshold, rng, tangle=False))
        assert used == ["loop", "arrays"]


# ── nesting ───────────────────────────────────────────────────────────────────


class TestGroupRings:
    def test_a_ring_alone_is_a_region_without_holes(self) -> None:
        assert group_rings([SQUARE]) == ((SQUARE, ()),)

    def test_a_clockwise_ring_inside_becomes_the_hole(self) -> None:
        hole = rect(40.0, 40.0, 60.0, 60.0, clockwise=True)
        assert group_rings([hole, SQUARE]) == ((SQUARE, (hole,)),)

    def test_a_hole_joins_the_smallest_outer_around_it(self) -> None:
        """An island inside a hole: outer A, hole in A, island B in that hole, a
        hole in B. B's hole belongs to B although A contains it too."""
        a = rect(0.0, 0.0, 100.0, 100.0)
        hole_a = rect(10.0, 10.0, 90.0, 90.0, clockwise=True)
        b = rect(30.0, 30.0, 70.0, 70.0)
        hole_b = rect(40.0, 40.0, 60.0, 60.0, clockwise=True)
        assert group_rings([a, hole_a, b, hole_b]) == ((a, (hole_a,)), (b, (hole_b,)))
        assert regions_area(group_rings([a, hole_a, b, hole_b])) == 10000 - 6400 + 1600 - 400

    def test_degenerate_rings_are_dropped_and_an_orphan_hole_is_an_error(self) -> None:
        assert group_rings([((0.0, 0.0), (1.0, 1.0)), ((0.0, 0.0), (5.0, 0.0), (9.0, 0.0))]) == ()
        with pytest.raises(ValueError, match="no outer ring"):
            group_rings([rect(0.0, 0.0, 5.0, 5.0, clockwise=True)])

    @pytest.mark.parametrize(
        ("label", "path", "outers", "holes"),
        [("closed loop", [(0, 0), (1000, 0), (1000, 800), (0, 800), (0, 0)], 1, 1),
         ("2 cm gap", [(0, 2), (0, 800), (1000, 800), (1000, 0), (0, 0)], 1, 1),
         ("open U", [(0, 800), (0, 0), (1000, 0), (1000, 800)], 1, 0),
         ("crossing", [(100, 100), (400, 400), (400, 100), (100, 400)], 1, 1),
         ("two crossings", [(0, 0), (300, 300), (600, 0), (600, 300), (300, 0), (0, 300), (0, 0)],
          1, 3)],
    )
    def test_what_pyclipper_returns_for_a_stroke(self, label, path, outers, holes) -> None:
        """The premise, measured: outers come back counter-clockwise, holes clockwise."""
        rings = [tuple(r) for r in polyline_footprint([(float(x), float(y)) for x, y in path], 10.0)]
        regions = group_rings(rings)
        assert len(regions) == outers, label
        assert sum(len(h) for _o, h in regions) == holes, label
        assert len(rings) == outers + holes

    # ── fix round 2 (P1): who owns a hole is not decided on a boundary point ───

    def test_a_hole_is_not_given_to_an_island_that_touches_it(self) -> None:
        """The re-review's case. Outer A, a hole H in it whose FIRST vertex is
        (20, 50), an island B inside H touching it exactly there. Ownership used
        to be decided by "which outer contains ``hole[0]``" — a point on B's
        boundary, where the point test may answer either way: H went to B, the
        island it surrounds (measured at 123a435, in every order of the rings)."""
        a = rect(0.0, 0.0, 100.0, 100.0)
        h = ((20.0, 50.0), (50.0, 80.0), (80.0, 50.0), (50.0, 20.0))
        b = ((20.0, 50.0), (40.0, 45.0), (40.0, 55.0))
        assert ring_area(h) < 0.0 < ring_area(b)
        for rings in ([a, h, b], [b, h, a], [h, b, a], [b, a, h]):
            assert dict(group_rings(rings)) == {a: (h,), b: ()}

    def test_a_hole_whose_every_vertex_lies_on_its_outer_ring(self) -> None:
        """A diamond cut out of a square, corner to edge: no vertex of the hole is
        inside anything, and it is still that square's hole — whichever vertex it
        starts with. (The point test answers "inside" for a point on the square's
        left or bottom edge and "outside" on its top or right one: the old rule,
        "ask about ``hole[0]``", lost this hole for two of the four starts.)"""
        a = rect(0.0, 0.0, 100.0, 100.0)
        diamond = ((0.0, 50.0), (50.0, 100.0), (100.0, 50.0), (50.0, 0.0))
        assert ring_area(diamond) < 0.0
        other = rect(200.0, 0.0, 300.0, 100.0)
        on_the_boundary = [point_in_ring(x, y, a) for x, y in diamond]
        assert True in on_the_boundary and False in on_the_boundary  # the premise
        for start in range(4):
            hole = diamond[start:] + diamond[:start]
            assert group_rings([other, hole, a]) == ((other, ()), (a, (hole,))), start

    def test_the_owner_must_be_larger_than_the_hole(self) -> None:
        """An island contains points of the hole around it; it is smaller than
        that hole and so can never own it — and a hole in nothing still raises."""
        hole = rect(0.0, 0.0, 100.0, 100.0, clockwise=True)
        island = rect(25.0, 25.0, 45.0, 45.0)
        x, y = polygon_module._point_inside(hole)
        assert point_in_ring(x, y, island)  # it sits exactly where the hole's inside point is taken
        with pytest.raises(ValueError, match="no outer ring"):
            group_rings([hole, island])
        around = rect(-10.0, -10.0, 110.0, 110.0)
        assert group_rings([hole, island, around]) == ((island, ()), (around, (hole,)))

    @pytest.mark.parametrize("seed", range(40))
    def test_point_inside_is_strictly_inside(self, seed: int) -> None:
        rng = random.Random(seed)
        ring = _wobbly(rng.randrange(3, 40), rng, tangle=False)
        if seed % 2:
            ring = ring[::-1]
        shift = rng.randrange(len(ring))
        ring = ring[shift:] + ring[:shift]
        x, y = polygon_module._point_inside(ring)
        assert point_in_ring(x, y, ring)
        assert _distance_to_ring(x, y, ring) > 1e-6

    @pytest.mark.parametrize(
        "ring",
        [L_SHAPE, L_SHAPE[::-1], SQUARE[::-1],
         # the corner's triangle holds other vertices: a notch reaching into it
         ((0.0, 0.0), (100.0, 0.0), (10.0, 10.0), (0.0, 100.0)),
         ((0.0, 0.0), (100.0, 0.0), (20.0, 5.0), (10.0, 10.0), (5.0, 20.0), (0.0, 100.0)),
         ((100.0, 100.0), (100.0, 300.0), (300.0, 300.0), (300.0, 260.0), (140.0, 260.0),
          (140.0, 140.0), (300.0, 140.0), (300.0, 100.0)),
         # a vertex on the line between the corner's neighbours
         ((0.0, 0.0), (100.0, 0.0), (50.0, 50.0), (0.0, 100.0))],
        ids=["L", "L clockwise", "square clockwise", "a notch", "a notch of three", "a C",
             "a vertex on the chord"],
    )
    def test_point_inside_of_awkward_rings(self, ring) -> None:
        x, y = polygon_module._point_inside(tuple(ring))
        assert point_in_ring(x, y, ring) and _distance_to_ring(x, y, ring) > 1e-6


def _distance_to_ring(x: float, y: float, ring) -> float:
    best = math.inf
    for i in range(len(ring)):
        (ax, ay), (bx, by) = ring[i - 1], ring[i]
        dx, dy = bx - ax, by - ay
        t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(x - (ax + t * dx), y - (ay + t * dy)))
    return best


def review_ladder(rungs: int, width: float = 6.0):
    """The ladder of the re-review, drawn as ONE stroke: two rails 100 cm apart
    and a rung every 40 cm. → its region ``(outer, holes)``; ``rungs - 2`` holes
    from 6 rungs on (the first cells merge with the rails' ends)."""
    path = [(0.0, 0.0)]
    for k in range(rungs):
        x = 40.0 * k
        path += ([(x, 100.0), (x + 40.0, 100.0), (x + 40.0, 0.0), (x, 0.0)] if k % 2 == 0 else
                 [(x + 40.0, 0.0), (x + 40.0, 100.0), (x, 100.0)])
    path = [*dict.fromkeys(path), (0.0, 0.0)]
    rings = [tuple((round(x, 7), round(y, 7)) for x, y in ring)
             for ring in polyline_footprint(path, width)]
    ((outer, holes),) = group_rings(rings)
    return outer, holes


# ── triangulation ─────────────────────────────────────────────────────────────


class TestTriangulateRegion:
    def test_without_holes_it_is_the_qt3d_ear_clipper(self) -> None:
        points, triangles = triangulate_region(L_SHAPE)
        assert points == L_SHAPE and triangles == triangulate_polygon(L_SHAPE)
        assert len(triangles) == 4

    def test_the_moved_function_is_one_object_everywhere(self) -> None:
        """``triangulate_polygon`` left ``legacy.py`` (it outlives it); every old
        import path still reaches the same function."""
        assert legacy_module.triangulate_polygon is polygon_module.triangulate_polygon
        assert scene3d.triangulate_polygon is polygon_module.triangulate_polygon
        assert legacy_module.sun_direction_scene is scene3d.frame.sun_direction_scene

    def test_a_frame(self) -> None:
        assert_triangulates(SQUARE, (rect(40.0, 40.0, 60.0, 60.0, clockwise=True),))

    def test_the_closed_fence(self) -> None:
        rings = [tuple(r) for r in polyline_footprint(
            [(0.0, 0.0), (1000.0, 0.0), (1000.0, 800.0), (0.0, 800.0), (0.0, 0.0)], 10.0)]
        ((outer, holes),) = group_rings(rings)
        assert_triangulates(outer, holes)
        _points, triangles = triangulate_region(outer, holes)
        cap = sum(tri_area(_points, t) for t in triangles)
        assert cap == pytest.approx(35962.0, abs=1.0)  # the stroke — not 1,600,162

    def test_several_holes_and_a_concave_outer(self) -> None:
        outer = ((0.0, 0.0), (300.0, 0.0), (300.0, 100.0), (200.0, 100.0), (200.0, 200.0),
                 (300.0, 200.0), (300.0, 300.0), (0.0, 300.0))
        holes = (rect(20.0, 20.0, 80.0, 80.0, clockwise=True),
                 rect(120.0, 20.0, 180.0, 80.0, clockwise=True),
                 rect(20.0, 220.0, 180.0, 280.0, clockwise=True),
                 rect(60.0, 120.0, 140.0, 180.0, clockwise=True))
        assert_triangulates(outer, holes)

    def test_a_hole_that_only_sees_another_hole(self) -> None:
        """The inner hole is boxed in by a C-shaped hole: its only neighbours are
        that hole's vertices, so it can be bridged only after the C is."""
        outer = rect(0.0, 0.0, 400.0, 400.0)
        c_hole = ((100.0, 100.0), (100.0, 300.0), (300.0, 300.0), (300.0, 260.0), (140.0, 260.0),
                  (140.0, 140.0), (300.0, 140.0), (300.0, 100.0))
        assert signed_area(c_hole) < 0
        inner = rect(170.0, 170.0, 230.0, 230.0, clockwise=True)
        assert_triangulates(outer, (inner, c_hole))
        assert_triangulates(outer, (c_hole, inner))

    def test_a_stroke_that_crosses_itself_twice(self) -> None:
        """Two triangles and the diamond between them: three holes in one outer."""
        rings = [tuple(r) for r in polyline_footprint(
            [(0.0, 0.0), (300.0, 300.0), (600.0, 0.0), (600.0, 300.0), (300.0, 0.0), (0.0, 300.0),
             (0.0, 0.0)], 10.0)]
        ((outer, holes),) = group_rings(rings)
        assert len(holes) == 3
        assert_triangulates(outer, holes)

    @pytest.mark.parametrize("seed", range(12))
    def test_random_stars_with_random_holes(self, seed: int) -> None:
        rng = random.Random(seed)
        count = rng.randrange(5, 14)
        outer = tuple(
            (r * math.cos(a), r * math.sin(a))
            for a, r in ((2 * math.pi * k / count, rng.uniform(600.0, 1000.0)) for k in range(count))
        )
        assert is_simple_ring(outer) and signed_area(outer) > 0
        holes = []
        for k in range(rng.randrange(1, 5)):  # small convex holes on a ring of radius 300
            angle = 2 * math.pi * k / 4 + rng.uniform(-0.2, 0.2)
            cx, cy = 300.0 * math.cos(angle), 300.0 * math.sin(angle)
            sides = rng.randrange(3, 8)
            radius = rng.uniform(40.0, 110.0)
            holes.append(tuple(
                (cx + radius * math.cos(-2 * math.pi * j / sides), cy + radius * math.sin(-2 * math.pi * j / sides))
                for j in range(sides)
            ))
        assert all(signed_area(h) < 0 for h in holes)
        assert_triangulates(outer, tuple(holes))

    def test_rings_that_touch_are_refused_not_guessed(self) -> None:
        """A hole lying ON the outer ring has no bridge that touches nothing: the
        function raises rather than return a cap it cannot vouch for."""
        outer = rect(0.0, 0.0, 100.0, 100.0)
        hole = rect(0.0, 0.0, 100.0, 100.0, clockwise=True)  # the same square
        with pytest.raises(ValueError):
            triangulate_region(outer, (hole,))


# ── fix round 2: the cost of many holes, and the one predicate ────────────────


class TestManyHoles:
    """Review round 2, P2-4. A ladder drawn as one stroke: 20 holes took 24 ms in
    the first version and 58 holes 2.6 s — on every geometry tick of that item
    (and a closed fence along a traced outline of 400 vertices 12.6 s). The
    bridge of a hole is now found in one pass over the edges."""

    @pytest.mark.parametrize("rungs", [4, 20, 60])
    def test_a_ladder_triangulates(self, rungs: int) -> None:
        outer, holes = review_ladder(rungs)
        assert len(holes) >= rungs - 2
        assert_triangulates(outer, holes, flat_corners=True)

    def test_an_ear_without_an_area_yields_no_triangle(self) -> None:
        """Found by this round's own test of the ladder. Its long diagonal is
        crossed by every rung, so vertices of the outer ring and of several holes
        lie on ONE line — the diagonal's edge. Ears along it have a doubled area
        of 1e-14 cm² (noise around an exact zero): counter-clockwise by luck,
        nothing in fact, and in the engine's float32 thirteen of them came out
        wound DOWNWARDS (``Mesh.validate`` refused the fence). They are clipped
        and yield no triangle."""
        outer, holes = review_ladder(20)
        points, triangles = triangulate_region(outer, holes)
        full = len(points) + 2 * len(holes) - 2
        assert (full, len(triangles)) == (239, 226)  # 13 ears without an area
        areas = [tri_area(points, t) for t in triangles]
        assert min(areas) > 0.009 and sum(areas) == pytest.approx(
            regions_area(((outer, holes),)), rel=1e-12)
        assert polygon_module._FLAT_CORNER == 4e-15

    def test_the_work_per_hole_is_one_pass_not_a_search(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The cliff, pinned as a COUNT (a clock would be a flake on a loaded
        box): each hole tests ONE block of nearby boundary vertices against the
        edges. Measured: 58 blocks for 58 holes, 94 for 94."""
        blocks = 0
        touch = polygon_module._touch

        def counting(*args):
            nonlocal blocks
            blocks += 1
            return touch(*args)

        monkeypatch.setattr(polygon_module, "_touch", counting)
        for rungs in (60, 100):
            outer, holes = review_ladder(rungs)
            blocks = 0
            triangulate_region(outer, holes)
            assert len(holes) <= blocks <= 2 * len(holes), (rungs, len(holes), blocks)

    def test_beyond_the_bounds_it_refuses_instead_of_freezing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outer, holes = review_ladder(20)
        vertices = len(outer) + sum(len(h) for h in holes)
        assert (len(holes), vertices) == (20, 201)
        monkeypatch.setattr(polygon_module, "MAX_HOLED_REGION_VERTICES", vertices - 1)
        with pytest.raises(ValueError, match="201 vertices with 20 holes is beyond"):
            triangulate_region(outer, holes)
        monkeypatch.setattr(polygon_module, "MAX_HOLED_REGION_VERTICES", vertices)
        triangulate_region(outer, holes)
        monkeypatch.setattr(polygon_module, "MAX_BRIDGE_WORK", 20 * 201 - 1)
        with pytest.raises(ValueError, match="beyond"):
            triangulate_region(outer, holes)
        monkeypatch.setattr(polygon_module, "MAX_BRIDGE_WORK", 20 * 201)
        triangulate_region(outer, holes)
        # a region without holes has no bound here
        monkeypatch.setattr(polygon_module, "MAX_HOLED_REGION_VERTICES", 0)
        monkeypatch.setattr(polygon_module, "MAX_BRIDGE_WORK", 0)
        assert len(triangulate_region(L_SHAPE)[1]) == 4
        assert (polygon_module.MAX_HOLED_REGION_VERTICES, polygon_module.MAX_BRIDGE_WORK) == (0, 0)

    def test_the_real_bounds(self) -> None:
        assert polygon_module.MAX_HOLED_REGION_VERTICES == 8000
        assert polygon_module.MAX_BRIDGE_WORK == 500_000

    def test_a_closed_fence_along_a_traced_outline(self) -> None:
        """One hole, many vertices: 400 path vertices → 1,201 ring vertices. The
        first version took 12.6 s for this region; it is cut and proven here."""
        rng = random.Random(400)
        path = list(_wobbly(400, rng, tangle=False))
        rings = [tuple((round(x, 7), round(y, 7)) for x, y in ring)
                 for ring in polyline_footprint([*path, path[0]], 4.0)]
        ((outer, holes),) = group_rings(rings)
        assert len(holes) == 1 and len(outer) + len(holes[0]) > 800
        vertices, triangles = polygon_module.cut_region(outer, holes)
        assert len(triangles) == len(vertices)  # n + 2h - 2 with one hole
        assert sum(tri_area(vertices, t) for t in triangles) == pytest.approx(
            regions_area(((outer, holes),)), rel=1e-9)


class TestBuildable:
    """THE predicate: a region may be handed to a builder exactly when the default
    builder's own triangulation cuts it and the cut is proven."""

    HOLE = rect(40.0, 40.0, 60.0, 60.0, clockwise=True)

    def test_what_is_buildable(self) -> None:
        assert is_buildable_region(SQUARE)
        assert is_buildable_region(SQUARE, (self.HOLE,))
        assert is_buildable_region(L_SHAPE, ())
        outer, holes = review_ladder(20)
        assert is_buildable_region(outer, holes)

    @pytest.mark.parametrize(
        ("label", "outer", "holes"),
        [("a clockwise outer ring", SQUARE[::-1], ()),
         ("an outer ring that crosses itself", LOPSIDED_BOW_TIE[::-1], ()),
         ("an outer ring without area", ((0.0, 0.0), (10.0, 0.0), (20.0, 0.0)), ()),
         ("a counter-clockwise hole", SQUARE, (rect(40.0, 40.0, 60.0, 60.0),)),
         ("a hole that crosses itself", rect(0.0, 0.0, 400.0, 400.0),
          (((100.0, 100.0), (300.0, 300.0), (300.0, 100.0), (100.0, 250.0)),)),
         ("a hole on the outer ring", SQUARE, (SQUARE[::-1],)),
         ("a hole that reaches outside", SQUARE, (rect(50.0, 40.0, 150.0, 60.0, clockwise=True),)),
         ("a hole outside", SQUARE, (rect(200.0, 40.0, 250.0, 60.0, clockwise=True),)),
         ("two holes that overlap", SQUARE, (rect(20.0, 20.0, 60.0, 60.0, clockwise=True),
                                             rect(40.0, 40.0, 80.0, 80.0, clockwise=True))),
         ("two holes that touch in a point", SQUARE, (rect(20.0, 20.0, 50.0, 50.0, clockwise=True),
                                                      rect(50.0, 50.0, 80.0, 80.0, clockwise=True)))],
    )
    def test_what_is_not(self, label: str, outer, holes) -> None:
        assert not is_buildable_region(tuple(outer), tuple(holes)), label
        with pytest.raises(ValueError):
            polygon_module.cut_region(tuple(outer), tuple(holes))

    def test_touching_is_not_refused_for_its_own_sake(self) -> None:
        """The predicate is "it can be cut and the cut is proven", not "the rings
        keep their distance": a hole that touches the outer ring in one point is a
        region with a pinch, and it is cut correctly."""
        diamond = ((0.0, 50.0), (50.0, 80.0), (80.0, 50.0), (50.0, 20.0))
        assert is_buildable_region(SQUARE, (diamond,))
        assert_triangulates(SQUARE, (diamond,), flat_corners=True)

    def test_cut_region_is_the_triangulation_with_the_proof(self) -> None:
        vertices, triangles = polygon_module.cut_region(SQUARE, (self.HOLE,))
        points, plain = triangulate_region(SQUARE, (self.HOLE,))
        assert vertices == points and triangles == tuple(plain)
        assert isinstance(triangles, tuple)  # cached: nobody can edit what the next caller gets
        assert sum(tri_area(vertices, t) for t in triangles) == 10000.0 - 400.0

    def test_a_wrong_triangulation_does_not_get_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The proof is not decoration: a triangulation that forgets the hole, or
        winds a triangle downwards, is refused whatever produced it."""
        polygon_module.cut_region.cache_clear()
        filled = (tuple(SQUARE) + tuple(self.HOLE), [(0, 1, 2), (0, 2, 3)])  # covers the hole too
        monkeypatch.setattr(polygon_module, "triangulate_region", lambda *_a: filled)
        with pytest.raises(ValueError, match="covers 10000.0 cm"):
            polygon_module.cut_region(SQUARE, (self.HOLE,))
        flipped = (tuple(SQUARE), [(0, 1, 2), (0, 3, 2), (0, 2, 3), (0, 2, 3)])  # the sum is right
        monkeypatch.setattr(polygon_module, "triangulate_region", lambda *_a: flipped)
        with pytest.raises(ValueError, match="smallest triangle -5000.0"):
            polygon_module.cut_region(SQUARE)
        polygon_module.cut_region.cache_clear()

    def test_both_answers_are_remembered(self) -> None:
        """The snapshot asks on every tick; a region that cannot be built must not
        be tried again each time."""
        outer = tuple((x + 0.125, y) for x, y in SQUARE)  # a region no other test has asked about
        reaching_out = (tuple((x + 0.125, y) for x, y in rect(50.0, 40.0, 150.0, 60.0, clockwise=True)),)
        before = is_buildable_region.cache_info()
        assert not is_buildable_region(outer, reaching_out)
        assert not is_buildable_region(outer, reaching_out)
        assert is_buildable_region(outer) and is_buildable_region(outer)
        after = is_buildable_region.cache_info()
        assert (after.misses - before.misses, after.hits - before.hits) == (2, 2)

    def test_the_default_builder_builds_exactly_what_the_predicate_accepts(self) -> None:
        from open_garden_planner.core.scene3d import prism_mesh

        outer, holes = review_ladder(20)
        mesh = prism_mesh((outer,), 120.0, (holes,))
        mesh.validate()
        assert mesh.vertex_count
        overlapping = (rect(20.0, 20.0, 60.0, 60.0, clockwise=True),
                       rect(40.0, 40.0, 80.0, 80.0, clockwise=True))
        assert not is_buildable_region(SQUARE, overlapping)
        with pytest.raises(ValueError):
            prism_mesh((SQUARE,), 120.0, (overlapping,))
