"""Plane polygons for the 3D pipeline: orientation, nesting, triangulation (L1.1).

A footprint is a set of REGIONS. A region is one OUTER ring, counter-clockwise
seen from above (signed area > 0), and any number of HOLE rings inside it,
clockwise (signed area < 0). That is the orientation pyclipper emits and the one
``Record`` enforces: ``footprints`` holds the outers, ``holes[i]`` the holes of
``footprints[i]``. A closed fence is the case that makes the distinction
load-bearing — its stroke comes back as two rings, and extruding the inner one
as a solid turns a 3.6 m² fence into an 80 m² slab (senior review, fix round 1).

Plain Python on float pairs, except where a test is quadratic and runs on every
edit of a ring — the simplicity test of a large ring and the triangulation of a
region with holes run on numpy. ``triangulate_polygon`` is the ear clipper the
Qt 3D view has always used (US-E6), moved here unchanged so it survives
``legacy.py``; ``triangulate_region`` adds holes by bridging each hole to the
boundary and clipping ears of the merged ring.

ONE predicate says whether a region may be handed to a builder:
``is_buildable_region`` — true exactly when ``cut_region``, the triangulation
the default builder itself uses, cuts it and the triangles add up to the
region's area. The snapshot asks it for every outline it turns into rings, so
"a record's regions can be built" is a checked fact, not a hope (fix round 2).

Caches. ``ring_area``, ``is_simple_ring``, ``cut_region`` and
``is_buildable_region`` are ``lru_cache``d on whole rings and never cleared:
bounded (a few thousand entries each), and at plan sizes a few megabytes at
most — a traced outline of 1,000 vertices is 16 kB per entry.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import Final, TypeAlias

import numpy as np
from numpy.typing import NDArray

Point: TypeAlias = tuple[float, float]
Ring: TypeAlias = tuple[Point, ...]
Region: TypeAlias = tuple[Ring, tuple[Ring, ...]]
Triangle: TypeAlias = tuple[int, int, int]
_Floats: TypeAlias = "NDArray[np.float64] | float"


# ── area and orientation ──────────────────────────────────────────────────────


def _signed_area(polygon: Sequence[Sequence[float]]) -> float:
    total = 0.0
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def signed_area(ring: Sequence[Sequence[float]]) -> float:
    """Shoelace area in cm²: positive for a counter-clockwise ring (seen from above,
    north up), negative for a clockwise one."""
    return _signed_area(ring)


@lru_cache(maxsize=8192)
def ring_area(ring: Ring) -> float:
    """``signed_area`` of an immutable ring, cached — a snapshot checks the
    orientation of every ring of every record, and most rings repeat."""
    return _signed_area(ring)


def ccw(ring: Ring) -> Ring:
    """``ring`` turning counter-clockwise (reversed when it turns clockwise)."""
    return ring if ring_area(ring) >= 0.0 else ring[::-1]


def cw(ring: Ring) -> Ring:
    """``ring`` turning clockwise — the orientation of a hole."""
    return ring if ring_area(ring) <= 0.0 else ring[::-1]


# ── predicates ────────────────────────────────────────────────────────────────


def _cross(o: Sequence[float], a: Sequence[float], b: Sequence[float]) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _point_in_triangle(
    p: Sequence[float], a: Sequence[float], b: Sequence[float], c: Sequence[float]
) -> bool:
    d1 = _cross(p, a, b)
    d2 = _cross(p, b, c)
    d3 = _cross(p, c, a)
    has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not (has_neg and has_pos)


def point_in_ring(x: float, y: float, ring: Sequence[Sequence[float]]) -> bool:
    """True when (x, y) lies inside ``ring`` (even-odd rule; a point exactly on the
    boundary may answer either way)."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def point_in_regions(x: float, y: float, regions: Sequence[Region]) -> bool:
    """True when (x, y) is covered: inside an outer ring and in none of its holes."""
    for outer, holes in regions:
        if point_in_ring(x, y, outer) and not any(point_in_ring(x, y, hole) for hole in holes):
            return True
    return False


def _on_segment(p: Sequence[float], a: Sequence[float], b: Sequence[float]) -> bool:
    return (min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
            and min(a[1], b[1]) <= p[1] <= max(a[1], b[1]))


def segments_touch(a: Sequence[float], b: Sequence[float],
                   c: Sequence[float], d: Sequence[float]) -> bool:
    """True when the closed segments a–b and c–d share at least one point."""
    d1 = _cross(c, d, a)
    d2 = _cross(c, d, b)
    d3 = _cross(a, b, c)
    d4 = _cross(a, b, d)
    if ((d1 > 0) != (d2 > 0)) and d1 != 0 and d2 != 0 and ((d3 > 0) != (d4 > 0)) and d3 != 0 and d4 != 0:
        return True
    return ((d1 == 0 and _on_segment(a, c, d)) or (d2 == 0 and _on_segment(b, c, d))
            or (d3 == 0 and _on_segment(c, a, b)) or (d4 == 0 and _on_segment(d, a, b)))


def is_flat_ring(ring: Sequence[Sequence[float]]) -> bool:
    """True when ``ring`` covers no ground at all: fewer than three corners, or
    every corner on one line. (A bow-tie whose lobes cancel has a zero signed
    area too, but it is not flat — it is a polygon that crosses itself.)"""
    if len(ring) < 3:
        return True
    origin = ring[0]
    far = max(ring, key=lambda p: (p[0] - origin[0]) ** 2 + (p[1] - origin[1]) ** 2)
    return all(_cross(origin, far, p) == 0 for p in ring)


#: From this many vertices on the simplicity test runs on numpy. Below it the
#: plain loop is faster (a rectangle costs microseconds); above it the loop is
#: what an edit of a traced outline would wait for — measured on the dev box:
#: 500 vertices 72 ms in the loop, 1,000 vertices 262 ms.
SIMPLE_RING_NUMPY_FROM: Final = 32
_SIMPLE_RING_ROWS: Final = 512  # edges per block: bounds the rows x n temporaries


def _edges_touch_loop(ring: Ring) -> bool:
    n = len(ring)
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        for j in range(i + 1, n):
            if (j + 1) % n == i or (i + 1) % n == j:
                continue  # a neighbour: they share a vertex by design
            if segments_touch(a, b, ring[j], ring[(j + 1) % n]):
                return True
    return False


def _edges_touch_numpy(ring: Ring) -> bool:
    """``_edges_touch_loop`` on arrays, in two steps.

    First the pairs of non-adjacent edges whose bounding boxes meet — two closed
    segments that share a point have boxes that do, so nothing is lost, and for
    an outline that leaves a few pairs per edge instead of n. Then, on those
    pairs only, the four cross products of ``segments_touch``, written in the
    same order of operations: the answer is the loop's, bit for bit (pinned by
    a test over random rings on both sides of the threshold).
    """
    points = np.asarray(ring, dtype=np.float64)
    n = len(points)
    following = np.roll(points, -1, axis=0)
    low, high = np.minimum(points, following), np.maximum(points, following)
    index = np.arange(n)
    column = index[None, :]
    for start in range(0, n, _SIMPLE_RING_ROWS):
        rows = slice(start, min(start + _SIMPLE_RING_ROWS, n))
        row = index[rows, None]
        near = ((low[rows, None, 0] <= high[None, :, 0]) & (low[None, :, 0] <= high[rows, None, 0])
                & (low[rows, None, 1] <= high[None, :, 1]) & (low[None, :, 1] <= high[rows, None, 1])
                & (column > row) & ((column + 1) % n != row) & ((row + 1) % n != column))
        i, j = np.nonzero(near)
        if not len(i):
            continue
        i = i + start
        if bool(np.any(_touch(points[i, 0], points[i, 1], following[i, 0], following[i, 1],
                              points[j, 0], points[j, 1], following[j, 0], following[j, 1]))):
            return True
    return False


def _touch(ax: _Floats, ay: _Floats, bx: _Floats, by: _Floats,
           cx: _Floats, cy: _Floats, dx: _Floats, dy: _Floats) -> NDArray[np.bool_]:
    """``segments_touch`` on arrays (they broadcast): the closed segments a–b and
    c–d share a point. The four cross products are written in the scalar
    version's order of operations, so the two never disagree."""
    d1 = (dx - cx) * (ay - cy) - (dy - cy) * (ax - cx)  # _cross(c, d, a)
    d2 = (dx - cx) * (by - cy) - (dy - cy) * (bx - cx)  # _cross(c, d, b)
    d3 = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)  # _cross(a, b, c)
    d4 = (bx - ax) * (dy - ay) - (by - ay) * (dx - ax)  # _cross(a, b, d)
    crossing = (((d1 > 0) != (d2 > 0)) & (d1 != 0) & (d2 != 0)
                & ((d3 > 0) != (d4 > 0)) & (d3 != 0) & (d4 != 0))
    touching = (((d1 == 0) & _within(ax, ay, cx, cy, dx, dy))
                | ((d2 == 0) & _within(bx, by, cx, cy, dx, dy))
                | ((d3 == 0) & _within(cx, cy, ax, ay, bx, by))
                | ((d4 == 0) & _within(dx, dy, ax, ay, bx, by)))
    return np.asarray(crossing | touching)


def _within(px: _Floats, py: _Floats, sx: _Floats, sy: _Floats,
            ex: _Floats, ey: _Floats) -> NDArray[np.bool_]:
    """``_on_segment`` on arrays: p inside the bounding box of s–e."""
    return np.asarray((np.minimum(sx, ex) <= px) & (px <= np.maximum(sx, ex))
                      & (np.minimum(sy, ey) <= py) & (py <= np.maximum(sy, ey)))


@lru_cache(maxsize=4096)
def is_simple_ring(ring: Ring) -> bool:
    """True when ``ring`` is a simple polygon: at least three vertices, a non-zero
    area, and no two non-adjacent edges sharing a point (no bow-tie, no touching).

    Quadratic in the vertex count, so it is cached (the snapshot asks it for every
    user-drawn polygon and the default builder for every ring it extrudes, and
    rings repeat between snapshots) and, from ``SIMPLE_RING_NUMPY_FROM`` vertices
    on, vectorised: a ring that is being edited misses the cache on every tick.
    """
    n = len(ring)
    if n < 3 or _signed_area(ring) == 0.0:
        return False
    if any(ring[i] == ring[(i + 1) % n] for i in range(n)):
        return False  # an edge without a length
    if n >= SIMPLE_RING_NUMPY_FROM:
        return not _edges_touch_numpy(ring)
    return not _edges_touch_loop(ring)


# ── nesting ───────────────────────────────────────────────────────────────────


def _point_inside(ring: Ring) -> Point:
    """A point STRICTLY inside a simple ring (of either orientation).

    At the ring's lowest-leftmost corner — a convex one — the triangle with its
    two neighbours either holds no other vertex, and then its centroid is
    inside the ring, or it does, and then the midpoint between the corner and
    the vertex in it that is farthest from the neighbours' line is.
    """
    n = len(ring)
    at = min(range(n), key=lambda i: (ring[i][1], ring[i][0]))
    corner, before, after = ring[at], ring[at - 1], ring[(at + 1) % n]
    if _cross(before, corner, after) < 0:
        before, after = after, before  # the clockwise ring: read the corner the other way round
    best, reach = None, 0.0
    for point in ring:
        if point in (corner, before, after) or not _point_in_triangle(point, before, corner, after):
            continue
        height = abs(_cross(before, after, point))
        if best is None or height > reach:
            best, reach = point, height
    if best is None:
        return ((corner[0] + before[0] + after[0]) / 3.0, (corner[1] + before[1] + after[1]) / 3.0)
    return ((corner[0] + best[0]) / 2.0, (corner[1] + best[1]) / 2.0)


def group_rings(rings: Sequence[Ring]) -> tuple[Region, ...]:
    """Sort a flat list of rings — outers counter-clockwise, holes clockwise, as
    pyclipper returns them — into regions: each hole joins the smallest outer
    that contains it. Rings with fewer than three vertices or no area are dropped;
    a hole inside no outer raises ``ValueError`` (it is not a footprint).

    Ownership is decided by a point strictly INSIDE the hole and by size: the
    owner is the smallest outer ring that is larger than the hole and contains
    that point. (A vertex of the hole would not do — a hole may touch its outer
    ring, or an island inside it, exactly there, and a point on a boundary has
    no inside. Nor would containment alone: an island inside the hole contains
    points of it too, and is smaller than the hole.)
    """
    outers: list[Ring] = []
    holes: list[Ring] = []
    for ring in rings:
        if len(ring) < 3:
            continue
        area = ring_area(ring)
        if area > 0.0:
            outers.append(ring)
        elif area < 0.0:
            holes.append(ring)
    owned: list[list[Ring]] = [[] for _ in outers]
    for hole in holes:
        x, y = _point_inside(hole)
        size = -ring_area(hole)
        best = -1
        for index, outer in enumerate(outers):
            if ring_area(outer) > size and point_in_ring(x, y, outer) and (
                best < 0 or ring_area(outer) < ring_area(outers[best])
            ):
                best = index
        if best < 0:
            raise ValueError("a hole ring lies inside no outer ring")
        owned[best].append(hole)
    return tuple((outer, tuple(mine)) for outer, mine in zip(outers, owned, strict=True))


def regions_area(regions: Sequence[Region]) -> float:
    """Covered area in cm²: the outers minus their holes."""
    return sum(ring_area(outer) + sum(ring_area(hole) for hole in holes)
               for outer, holes in regions)


# ── triangulation ─────────────────────────────────────────────────────────────


def triangulate_polygon(polygon: Sequence[Sequence[float]]) -> list[Triangle]:
    """Ear-clipping triangulation; returns index triples into ``polygon``.

    Handles convex and concave simple polygons; input winding is
    irrelevant (normalized to CCW internally, indices refer to the
    ORIGINAL order). Falls back to a fan for pathological inputs so the
    renderer always gets triangles rather than nothing.
    """
    n = len(polygon)
    if n < 3:
        return []
    indices = list(range(n))
    if _signed_area(polygon) < 0:
        indices.reverse()  # normalize to CCW traversal
    triangles: list[Triangle] = []
    guard = 0
    while len(indices) > 3 and guard < 10000:
        guard += 1
        ear_found = False
        m = len(indices)
        for i in range(m):
            prev_i, cur_i, next_i = (
                indices[(i - 1) % m],
                indices[i],
                indices[(i + 1) % m],
            )
            a, b, c = polygon[prev_i], polygon[cur_i], polygon[next_i]
            if _cross(a, b, c) <= 0:
                continue  # reflex or collinear — not an ear
            if any(
                _point_in_triangle(polygon[j], a, b, c)
                for j in indices
                if j not in (prev_i, cur_i, next_i)
            ):
                continue
            triangles.append((prev_i, cur_i, next_i))
            indices.pop(i)
            ear_found = True
            break
        if not ear_found:
            break  # numeric degeneracy — fan out the rest below
    if len(indices) == 3:
        triangles.append((indices[0], indices[1], indices[2]))
    elif len(indices) > 3:
        anchor = indices[0]
        for i in range(1, len(indices) - 1):
            triangles.append((anchor, indices[i], indices[i + 1]))
    return triangles


def _inside_corner(points: Sequence[Point], loop: Sequence[int], at: int, target: int) -> bool:
    """True when points[target] lies strictly inside the interior angle of the
    counter-clockwise ``loop`` at position ``at``."""
    n = len(loop)
    prev, here, nxt = points[loop[at - 1]], points[loop[at]], points[loop[(at + 1) % n]]
    t = points[target]
    left_of_in = _cross(prev, here, t) > 0
    left_of_out = _cross(here, nxt, t) > 0
    if _cross(prev, here, nxt) > 0:  # convex corner: inside both half-planes
        return left_of_in and left_of_out
    return left_of_in or left_of_out  # reflex corner: inside either


#: The size limits of ``triangulate_region`` for a region WITH holes. Its cost has
#: two parts: the bridges — per hole one pass over every edge, so holes x vertices
#: — and the ear clipping of the merged ring, one array pass per vertex for an
#: ordinary outline. Measured on the dev box under load (the first version in
#: brackets): a closed fence along a traced outline, 1 hole, 1,201 vertices 45 ms
#: (12.6 s), 7,287 vertices 0.29 s; a ladder drawn as one stroke, 58 holes / 591
#: vertices 43 ms (2.5 s), 94 / 977 89 ms, 312 / 3,219 0.43 s; a square mesh drawn
#: as one stroke, 400 holes / 1,609 vertices 0.31 s. Beyond either limit — about
#: 0.3 s — it refuses with ``ValueError`` instead of holding the GUI thread: the
#: region is then not buildable. A region without holes has no limit here.
MAX_HOLED_REGION_VERTICES: Final = 8000  # outer ring and holes together
MAX_BRIDGE_WORK: Final = 500_000  # holes x vertices
#: From this many vertices on, a ring WITHOUT holes is cut by the array ear clipper
#: as well. Below it ``triangulate_polygon`` — the clipper the Qt 3D view uses —
#: is faster and gives that view's triangles vertex for vertex; above it the plain
#: loop is what an edit of a traced outline waits for on every tick. Measured, same
#: machine, same load: 100 vertices 2.3 ms (loop) / 4.0 ms (arrays); 500: 76 / 25;
#: 1,000: 272 / 30.
ARRAY_EAR_CLIP_FROM: Final = 256
_BRIDGE_CANDIDATES: Final = 6  # boundary vertices tried per block, nearest first


def _bridge_position(points: Sequence[Point], xy: NDArray[np.float64], loop: list[int],
                     hole_vertex: int, waiting_first: NDArray[np.intp],
                     waiting_second: NDArray[np.intp]) -> int:
    """The position in ``loop`` of the nearest boundary vertex that ``hole_vertex``
    can be bridged to: the segment between them touches no edge of the boundary
    or of a hole still to be merged (``waiting_first`` → ``waiting_second``, this
    hole's own edges among them), and leaves the boundary into its inside.

    Boundary vertices are tried nearest first, a block at a time, each block
    against every edge at once. Per hole that is one sort of the boundary plus,
    usually, one block: O(L log L + E) for a boundary of L vertices and E edges.
    """
    index = np.asarray(loop, dtype=np.intp)
    first = np.concatenate([index, waiting_first])
    second = np.concatenate([np.roll(index, -1), waiting_second])
    cx, cy, dx, dy = xy[first, 0], xy[first, 1], xy[second, 0], xy[second, 1]
    hx, hy = float(xy[hole_vertex, 0]), float(xy[hole_vertex, 1])
    at_hole = (first == hole_vertex) | (second == hole_vertex)
    order = np.argsort(np.hypot(xy[index, 0] - hx, xy[index, 1] - hy), kind="stable")
    for start in range(0, len(order), _BRIDGE_CANDIDATES):
        positions = [int(at) for at in order[start:start + _BRIDGE_CANDIDATES]
                     if _inside_corner(points, loop, int(at), hole_vertex)]
        if not positions:
            continue
        ends = index[positions]
        ax, ay = xy[ends, 0][:, None], xy[ends, 1][:, None]
        starts_here = first[None, :] == ends[:, None]
        at_end = starts_here | (second[None, :] == ends[:, None])
        # an edge that starts at an end of the bridge blocks it only when its far
        # end lies ON the bridge (they would overlap); every other edge when it
        # touches the bridge anywhere
        far = np.where(starts_here | (first == hole_vertex)[None, :], second[None, :], first[None, :])
        fx, fy = xy[far, 0], xy[far, 1]
        overlapping = (((hx - ax) * (fy - ay) - (hy - ay) * (fx - ax) == 0)
                       & _within(fx, fy, ax, ay, hx, hy))
        touching = _touch(ax, ay, hx, hy, cx[None, :], cy[None, :], dx[None, :], dy[None, :])
        blocked = np.where(at_end | at_hole[None, :], overlapping, touching).any(axis=1)
        free = np.flatnonzero(~blocked)
        if len(free):
            return positions[int(free[0])]
    raise ValueError("no bridge from a hole to its outer ring (rings touch or cross)")


#: Three vertices on one line have a cross product of zero — in exact arithmetic.
#: In floats it comes out as noise of either sign, about 4 · 2⁻⁵³ · (the largest
#: coordinate) · (the corner's two edges). A corner whose doubled area is below
#: this many times that product is FLAT: clipping it yields no triangle.
_FLAT_CORNER: Final = 4e-15


def _ear_clip(points: Sequence[Point], loop: Sequence[int]) -> list[Triangle]:
    """Clip ears of a counter-clockwise loop of indices into ``points``. The loop
    may visit a vertex twice (a bridge to a hole runs out and back): a second
    visit to an ear's own corner never blocks that ear.

    Each candidate corner is tested against every vertex still in the loop at
    once; after a clipped ear the search goes on at its neighbour. O(n) array
    passes for a well-behaved loop of n vertices, O(n²) at worst.

    An ear without an area — three vertices of different rings on one line, as
    along the edge of a diagonal stroke that other strokes cross — is clipped
    like any other and yields NO triangle (``_FLAT_CORNER``): it covers nothing,
    and rounded to the engine's float32 it would come out wound either way.
    """
    n = len(loop)
    xs = [points[i][0] for i in loop]
    ys = [points[i][1] for i in loop]
    x, y = np.asarray(xs, dtype=np.float64), np.asarray(ys, dtype=np.float64)
    alive = np.ones(n, dtype=bool)
    before = [n - 1, *range(n - 1)]
    after = [*range(1, n), 0]
    triangles: list[Triangle] = []
    remaining, at, misses = n, 0, 0
    while remaining > 3:
        a, c = before[at], after[at]
        ax, ay, bx, by, cx, cy = xs[a], ys[a], xs[at], ys[at], xs[c], ys[c]
        ear = False
        if (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) > 0:  # a convex corner
            # only a vertex inside the triangle's bounding box can be inside the triangle
            near = np.flatnonzero(alive & (x >= min(ax, bx, cx)) & (x <= max(ax, bx, cx))
                                  & (y >= min(ay, by, cy)) & (y <= max(ay, by, cy)))
            px, py = x[near], y[near]
            d1 = (ax - px) * (by - py) - (ay - py) * (bx - px)  # _point_in_triangle, for each
            d2 = (bx - px) * (cy - py) - (by - py) * (cx - px)
            d3 = (cx - px) * (ay - py) - (cy - py) * (ax - px)
            inside = ~(((d1 < 0) | (d2 < 0) | (d3 < 0)) & ((d1 > 0) | (d2 > 0) | (d3 > 0)))
            corner = (((px == ax) & (py == ay)) | ((px == bx) & (py == by))
                      | ((px == cx) & (py == cy)))
            ear = not bool(np.any(inside & ~corner))
        if ear:
            reach = max(abs(ax), abs(ay), abs(bx), abs(by), abs(cx), abs(cy))
            edges = abs(bx - ax) + abs(by - ay) + abs(cx - ax) + abs(cy - ay)
            if (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) > _FLAT_CORNER * reach * edges:
                triangles.append((loop[a], loop[at], loop[c]))
            alive[at] = False
            after[a], before[c] = c, a
            remaining -= 1
            at, misses = a, 0
            continue
        at = c
        misses += 1
        if misses > remaining:
            raise ValueError("the region cannot be triangulated (degenerate or self-touching rings)")
    b, c = after[at], after[after[at]]
    last = _cross(points[loop[at]], points[loop[b]], points[loop[c]])
    if last < 0:
        raise ValueError("the region cannot be triangulated (a clockwise remainder)")
    if last > 0:
        triangles.append((loop[at], loop[b], loop[c]))
    return triangles


def triangulate_region(
    outer: Ring, holes: Sequence[Ring] = ()
) -> tuple[tuple[Point, ...], list[Triangle]]:
    """Triangulate one region: ``(vertices, triangles)``.

    ``vertices`` is ``outer`` followed by every hole, unchanged — the triangles
    index into it, are all counter-clockwise, and cover exactly the outer minus
    the holes (n + 2h − 2 of them for n vertices and h holes, fewer where three
    vertices lie on one line). ``outer`` must be a simple counter-clockwise ring and each hole a
    simple clockwise ring strictly inside it, none touching another; anything
    the algorithm cannot triangulate raises ``ValueError`` rather than returning
    a wrong cap. Use ``cut_region`` to get the result PROVEN (areas checked).

    Without holes this is ``triangulate_polygon`` (the ear clipper the Qt 3D view
    uses) — up to ``ARRAY_EAR_CLIP_FROM`` vertices; a larger ring is cut by the
    array ear clipper used for regions with holes: the same polygon, other
    triangles, a ninth of the time at 1,000 vertices. With holes, each one is first joined to the boundary by a *bridge* —
    a vertex-to-vertex segment that touches no edge — which turns the region
    into one ring that walks out to the hole, around it and back. Holes are
    taken rightmost first, each from its rightmost vertex: no hole still waiting
    can then stand between that vertex and the boundary to its right, so a
    bridge always exists. Cost: O(H · (L log L + E)) for the bridges of H holes
    plus the ear clipping of the merged ring — quadratic in the vertex count at
    worst, in array passes; ``MAX_HOLED_REGION_VERTICES`` and ``MAX_BRIDGE_WORK``
    bound it, and beyond them this raises.
    """
    points: tuple[Point, ...] = tuple(outer) + tuple(p for hole in holes for p in hole)
    if not holes:
        if len(outer) >= ARRAY_EAR_CLIP_FROM and ring_area(outer) > 0.0:
            try:
                return points, _ear_clip(points, range(len(outer)))
            except ValueError:
                pass  # a ring it cannot finish: the shared clipper below has a fan to fall back on
        return points, triangulate_polygon(outer)
    if len(points) > MAX_HOLED_REGION_VERTICES or len(holes) * len(points) > MAX_BRIDGE_WORK:
        raise ValueError(
            f"a region of {len(points)} vertices with {len(holes)} holes is beyond what is "
            f"triangulated here ({MAX_HOLED_REGION_VERTICES} vertices, holes x vertices "
            f"{MAX_BRIDGE_WORK})"
        )
    xy = np.asarray(points, dtype=np.float64)
    loop = list(range(len(outer)))
    waiting: list[list[int]] = []
    start = len(outer)
    for hole in holes:
        waiting.append(list(range(start, start + len(hole))))
        start += len(hole)
    waiting.sort(key=lambda ring: max(points[i][0] for i in ring), reverse=True)
    rings = [np.asarray(ring, dtype=np.intp) for ring in waiting]
    first = np.concatenate(rings)  # the edges of every hole, in the order they are merged
    second = np.concatenate([np.roll(ring, -1) for ring in rings])
    begins = np.cumsum([0, *(len(ring) for ring in waiting)])
    for number, ring in enumerate(waiting):
        k = max(range(len(ring)), key=lambda j: points[ring[j]])  # its rightmost vertex
        at = _bridge_position(points, xy, loop, ring[k], first[begins[number]:],
                              second[begins[number]:])
        # out along the bridge, once around the hole, and back
        loop[at + 1:at + 1] = ring[k:] + ring[:k] + [ring[k], loop[at]]
    return points, _ear_clip(points, loop)


# ── the one predicate ─────────────────────────────────────────────────────────


@lru_cache(maxsize=2048)
def cut_region(outer: Ring, holes: tuple[Ring, ...] = ()) -> tuple[tuple[Point, ...], tuple[Triangle, ...]]:
    """``triangulate_region``, with the result PROVEN before it is handed out.

    Raises ``ValueError`` unless the outer ring is a simple counter-clockwise
    polygon, every hole a simple clockwise one, every triangle of the result
    faces up, and the triangles' areas add up to the region's (outer minus
    holes) — the two facts a wrong triangulation cannot both satisfy. This is
    what the default builder calls (``build.prism_mesh``); cached, so asking
    whether a region can be built costs nothing again when it then is.
    """
    if not is_simple_ring(outer) or not ring_area(outer) > 0.0:
        raise ValueError(
            f"a footprint ring of {len(outer)} vertices is not a simple polygon "
            "(it crosses or touches itself)"
        )
    for hole in holes:
        if not is_simple_ring(hole) or not ring_area(hole) < 0.0:
            raise ValueError(
                f"a hole ring of {len(hole)} vertices is not a simple polygon "
                "(it crosses or touches itself)"
            )
    vertices, triangles = triangulate_region(outer, holes)
    doubled = [
        (vertices[b][0] - vertices[a][0]) * (vertices[c][1] - vertices[a][1])
        - (vertices[b][1] - vertices[a][1]) * (vertices[c][0] - vertices[a][0])
        for a, b, c in triangles
    ]
    expected = ring_area(outer) + sum(ring_area(hole) for hole in holes)
    slack = 1e-9 * max(1.0, abs(expected))
    if (doubled and min(doubled) < -slack) or abs(sum(doubled) / 2.0 - expected) > slack:
        raise ValueError(
            f"the cap of a footprint ring covers {sum(doubled) / 2.0!r} cm² "
            f"(smallest triangle {min(doubled, default=0.0) / 2.0!r}); its region is {expected!r} cm²"
        )
    return vertices, tuple(triangles)


@lru_cache(maxsize=4096)
def is_buildable_region(outer: Ring, holes: tuple[Ring, ...] = ()) -> bool:
    """THE predicate for "a builder can turn this region into a solid": true
    exactly when ``cut_region`` — the default builder's own triangulation — cuts
    it and proves the cut. The snapshot keeps only regions for which it holds.

    It implies what a builder of its own may rely on: the outer ring is a simple
    counter-clockwise polygon, the holes are simple, clockwise and inside it,
    and the triangulation exists. Cached, false answers included.
    """
    try:
        cut_region(outer, holes)
    except ValueError:
        return False
    return True
