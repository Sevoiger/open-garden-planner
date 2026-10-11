"""Plane polygons for the 3D pipeline: orientation, nesting, triangulation (L1.1).

A footprint is a set of REGIONS. A region is one OUTER ring, counter-clockwise
seen from above (signed area > 0), and any number of HOLE rings inside it,
clockwise (signed area < 0). That is the orientation pyclipper emits and the one
``Record`` enforces: ``footprints`` holds the outers, ``holes[i]`` the holes of
``footprints[i]``. A closed fence is the case that makes the distinction
load-bearing — its stroke comes back as two rings, and extruding the inner one
as a solid turns a 3.6 m² fence into an 80 m² slab (senior review, fix round 1).

Plain Python on float pairs, with one exception: the simplicity test of a large
ring runs on numpy (it is quadratic, and it runs on every edit of that ring).
``triangulate_polygon`` is the ear clipper the Qt 3D view has always used
(US-E6), moved here unchanged so it survives ``legacy.py``;
``triangulate_region`` adds holes by bridging each hole to the boundary and
clipping ears of the merged ring.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from functools import lru_cache
from typing import Final, TypeAlias

import numpy as np
from numpy.typing import NDArray

Point: TypeAlias = tuple[float, float]
Ring: TypeAlias = tuple[Point, ...]
Region: TypeAlias = tuple[Ring, tuple[Ring, ...]]
Triangle: TypeAlias = tuple[int, int, int]


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
        ax, ay, bx, by = points[i, 0], points[i, 1], following[i, 0], following[i, 1]  # edge i: a -> b
        cx, cy, dx, dy = points[j, 0], points[j, 1], following[j, 0], following[j, 1]  # edge j: c -> d
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
        if bool(np.any(crossing | touching)):
            return True
    return False


def _within(px: NDArray[np.float64], py: NDArray[np.float64], sx: NDArray[np.float64],
            sy: NDArray[np.float64], ex: NDArray[np.float64], ey: NDArray[np.float64]) -> NDArray[np.bool_]:
    """``_on_segment`` on arrays: p inside the bounding box of s–e."""
    return ((np.minimum(sx, ex) <= px) & (px <= np.maximum(sx, ex))
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


def group_rings(rings: Sequence[Ring]) -> tuple[Region, ...]:
    """Sort a flat list of rings — outers counter-clockwise, holes clockwise, as
    pyclipper returns them — into regions: each hole joins the smallest outer
    that contains it. Rings with fewer than three vertices or no area are dropped;
    a hole inside no outer raises ``ValueError`` (it is not a footprint)."""
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
        x, y = hole[0]
        best = -1
        for index, outer in enumerate(outers):
            if point_in_ring(x, y, outer) and (
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


def _blocked(points: Sequence[Point], a: int, b: int,
             edges: Sequence[tuple[int, int]]) -> bool:
    """True when the bridge points[a]–points[b] touches any edge anywhere but at
    its own two ends."""
    pa, pb = points[a], points[b]
    ends = (a, b)
    for e1, e2 in edges:
        first, second = e1 in ends, e2 in ends
        if first and second:
            return True  # the bridge would lie on an existing edge
        if first or second:
            # an edge that starts at one end of the bridge: it blocks only when its
            # far end lies on the bridge (a collinear overlap)
            far = points[e2 if first else e1]
            if _cross(pa, pb, far) == 0 and _on_segment(far, pa, pb):
                return True
            continue
        if segments_touch(pa, pb, points[e1], points[e2]):
            return True
    return False


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


def _ear_clip(points: Sequence[Point], loop: list[int]) -> list[Triangle]:
    """Clip ears of a counter-clockwise loop of indices into ``points``. The loop
    may visit a vertex twice (a bridge to a hole runs out and back): a second
    visit to an ear's own corner never blocks that ear."""
    triangles: list[Triangle] = []
    loop = list(loop)
    while len(loop) > 3:
        m = len(loop)
        for i in range(m):
            ia, ib, ic = loop[i - 1], loop[i], loop[(i + 1) % m]
            a, b, c = points[ia], points[ib], points[ic]
            if _cross(a, b, c) <= 0:
                continue
            corners = (ia, ib, ic)
            if any(j not in corners and points[j] not in (a, b, c)
                   and _point_in_triangle(points[j], a, b, c) for j in loop):
                continue
            triangles.append((ia, ib, ic))
            loop.pop(i)
            break
        else:
            raise ValueError("the region cannot be triangulated (degenerate or self-touching rings)")
    last = _cross(points[loop[0]], points[loop[1]], points[loop[2]])
    if last < 0:
        raise ValueError("the region cannot be triangulated (a clockwise remainder)")
    if last > 0:
        triangles.append((loop[0], loop[1], loop[2]))
    return triangles


def triangulate_region(
    outer: Ring, holes: Sequence[Ring] = ()
) -> tuple[tuple[Point, ...], list[Triangle]]:
    """Triangulate one region: ``(vertices, triangles)``.

    ``vertices`` is ``outer`` followed by every hole, unchanged — the triangles
    index into it, are all counter-clockwise, and cover exactly the outer minus
    the holes. ``outer`` must be a simple counter-clockwise ring and each hole a
    simple clockwise ring inside it; anything the algorithm cannot triangulate
    raises ``ValueError`` rather than returning a wrong cap.

    Without holes this is ``triangulate_polygon`` (the ear clipper the Qt 3D view
    uses). With holes, each one is first joined to the boundary by a *bridge* —
    the shortest vertex-to-vertex segment that crosses no edge — which turns the
    region into one ring that walks out to the hole, around it and back.
    """
    points: tuple[Point, ...] = tuple(outer) + tuple(p for hole in holes for p in hole)
    if not holes:
        return points, triangulate_polygon(outer)
    loop = list(range(len(outer)))
    pending: list[list[int]] = []
    start = len(outer)
    for hole in holes:
        pending.append(list(range(start, start + len(hole))))
        start += len(hole)
    while pending:
        edges = [(loop[i], loop[(i + 1) % len(loop)]) for i in range(len(loop))]
        for ring in pending:
            edges.extend((ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring)))
        merged = False
        for ring in pending:
            candidates = sorted(
                ((math.dist(points[h], points[loop[at]]), k, at)
                 for k, h in enumerate(ring) for at in range(len(loop))),
            )
            for _distance, k, at in candidates:
                h, o = ring[k], loop[at]
                if not _inside_corner(points, loop, at, h):
                    continue
                if _blocked(points, o, h, edges):
                    continue
                # out along the bridge, once around the hole, and back
                loop[at + 1:at + 1] = ring[k:] + ring[:k] + [h, o]
                pending.remove(ring)
                merged = True
                break
            if merged:
                break
        if not merged:
            raise ValueError("no bridge from a hole to its outer ring (rings touch or cross)")
    return points, _ear_clip(points, loop)
