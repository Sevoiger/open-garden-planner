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

Two bounds hold for everything here, because the rings come out of a plan file
and a plan file can be written by anybody (security review of fix round 3):

- WORK. Whatever is superlinear — the simplicity test, sorting rings into
  regions, a bridge, the ear clipping, counting the pairs of a path — is paid
  for in work units out of a ``WorkBudget`` BEFORE it runs, and refused
  (``RegionTooLargeError``) when the budget does not cover it. One region may
  cost ``MAX_REGION_WORK``; so may everything ONE item's outline needs, which
  ``ui.view3d.snapshot`` pays into one budget.
- MEMORY. What is remembered about rings (``ring_keyed``: ``ring_area``, the
  verdict and the cut of a region) is bounded by the VERTICES it holds, not by
  the number of answers, and a ring of more than ``MAX_ITEM_VERTICES`` vertices
  is never hashed and never a key.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from typing import Any, Final, Generic, NamedTuple, NoReturn, ParamSpec, TypeAlias, TypeVar

import numpy as np
from numpy.typing import NDArray

Point: TypeAlias = tuple[float, float]
Ring: TypeAlias = tuple[Point, ...]
Region: TypeAlias = tuple[Ring, tuple[Ring, ...]]
Triangle: TypeAlias = tuple[int, int, int]
_Floats: TypeAlias = "NDArray[np.float64] | float"


# ── what is remembered ────────────────────────────────────────────────────────

#: The most vertices ONE item's outline may have in the 3D pipeline: as it is
#: DRAWN (a polygon's ring, the points of a path) and as it is MADE (every ring
#: the clipper returns for it, together). The first is asked of the item before
#: one vertex is read, the second of the clipper's answer before one ring of it
#: is. Far above what the work bound lets through (a plain outline of about
#: 3,500 vertices; a stroke of about 4,300), so it never refuses what could be
#: built — it is what makes the answer for 200,000 points ONE comparison, and
#: what bounds a key.
MAX_ITEM_VERTICES: Final = 16_384
#: How many vertices ONE ``ring_keyed`` function holds at most, keys and answers
#: together. At 104 bytes a vertex (a tuple of two floats, and its place in the
#: ring) that is 27 MB if nothing were shared; the rings are the records' own, so
#: a full cache adds little to what the records already hold. (Before: 2,048 to
#: 8,192 ANSWERS each, whatever their size — a traced outline of 4,000 vertices
#: dragged for a minute was a gigabyte.)
HELD_VERTICES: Final = 262_144
_ENTRY_VERTICES: Final = 8  # what an answer weighs beyond its rings: the entry itself

_P = ParamSpec("_P")
_R = TypeVar("_R")


class CacheInfo(NamedTuple):
    """What a ``ring_keyed`` function holds (``cache_info()``)."""

    hits: int
    misses: int
    max_vertices: int
    vertices: int
    entries: int


class _Entry(Generic[_R]):
    __slots__ = ("answer", "asked", "weight")

    def __init__(self, answer: _R, weight: int, asked: int) -> None:
        self.answer, self.weight, self.asked = answer, weight, asked


class RingKeyed(Generic[_P, _R]):
    """A function of rings with its answers remembered — what ``functools.lru_cache``
    does, bounded by the VERTICES held instead of the number of answers.

    An answer about a traced outline of 4,000 vertices is a thousand times an
    answer about a rectangle; a bound on entries is no bound on memory. Answers
    are kept until their keys (``vertices(*args)``) and what ``holds(answer)``
    says of the answers add up to ``max_vertices``; then the least recently
    asked go, down to three quarters. A call about more than
    ``MAX_ITEM_VERTICES`` vertices is answered and NOT kept: such a ring is
    never hashed and never a key. Positional arguments only are remembered.
    Safe to call from several threads, like ``lru_cache``; an exception is not
    remembered.
    """

    def __init__(self, function: Callable[_P, _R], max_vertices: int,
                 vertices: Callable[..., int], holds: Callable[[Any], int] | None) -> None:
        self.__wrapped__ = function
        self.__name__ = function.__name__
        self.__doc__ = function.__doc__
        self._vertices, self._holds, self._max = vertices, holds, max_vertices
        self._entries: dict[tuple[Any, ...], _Entry[_R]] = {}
        self._held = self._asked = self._hits = self._misses = 0
        self._lock = threading.Lock()

    def __call__(self, *args: _P.args, **kwargs: _P.kwargs) -> _R:
        if kwargs:
            return self.__wrapped__(*args, **kwargs)
        size = self._vertices(*args)
        if size > MAX_ITEM_VERTICES:
            return self.__wrapped__(*args, **kwargs)  # never hashed, never a key
        with self._lock:
            self._asked += 1
            entry = self._entries.get(args)
            if entry is not None:
                entry.asked = self._asked
                self._hits += 1
                return entry.answer
            self._misses += 1
        answer = self.__wrapped__(*args, **kwargs)
        weight = _ENTRY_VERTICES + size + (self._holds(answer) if self._holds is not None else 0)
        with self._lock:
            if args not in self._entries:
                self._entries[args] = _Entry(answer, weight, self._asked)
                self._held += weight
                if self._held > self._max:
                    self._forget()
        return answer

    def _forget(self) -> None:
        """Drop the least recently asked answers until three quarters of the room is in use."""
        for key, entry in sorted(self._entries.items(), key=lambda item: item[1].asked):
            if self._held <= self._max * 3 // 4:
                break
            del self._entries[key]
            self._held -= entry.weight

    def cache_clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._held = self._hits = self._misses = 0

    def cache_info(self) -> CacheInfo:
        return CacheInfo(self._hits, self._misses, self._max, self._held, len(self._entries))


def ring_keyed(max_vertices: int = HELD_VERTICES, *, vertices: Callable[..., int] = len,
               holds: Callable[[Any], int] | None = None,
               ) -> Callable[[Callable[_P, _R]], RingKeyed[_P, _R]]:
    """Decorator: remember a function of rings, bounded by vertices (``RingKeyed``).
    ``vertices(*args)`` says how many vertices a call's arguments hold (default:
    the length of its one ring), ``holds(answer)`` how many its answer does."""

    def remembered(function: Callable[_P, _R]) -> RingKeyed[_P, _R]:
        return RingKeyed(function, max_vertices, vertices, holds)

    return remembered


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


@ring_keyed()
def ring_area(ring: Ring) -> float:
    """``signed_area`` of an immutable ring, remembered — a snapshot checks the
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
    for start in range(0, n, _SIMPLE_RING_ROWS):
        rows = slice(start, min(start + _SIMPLE_RING_ROWS, n))
        later = slice(start, n)  # a pair is looked at once: an edge against the LATER ones
        row = index[rows, None]
        column = index[None, later]
        near = ((low[rows, None, 0] <= high[None, later, 0]) & (low[None, later, 0] <= high[rows, None, 0])
                & (low[rows, None, 1] <= high[None, later, 1]) & (low[None, later, 1] <= high[rows, None, 1])
                & (column > row) & ((column + 1) % n != row) & ((row + 1) % n != column))
        i, j = np.nonzero(near)
        if not len(i):
            continue
        i, j = i + start, j + start
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


def is_simple_ring(ring: Ring) -> bool:
    """True when ``ring`` is a simple polygon: at least three vertices, a non-zero
    area, and no two non-adjacent edges sharing a point (no bow-tie, no touching).

    Quadratic in the vertex count: from ``SIMPLE_RING_NUMPY_FROM`` vertices on it
    runs on arrays, and a ring whose test ALONE would cost more than
    ``MAX_REGION_WORK`` (about 7,000 vertices) is refused with
    ``RegionTooLargeError`` — a question is not answered at any price. Not
    remembered: its one caller, the cut of a region, is (``cut_region``).
    """
    n = len(ring)
    if _simplicity_work(n) > MAX_REGION_WORK:
        _refuse(f"the simplicity test of a ring of {n} vertices")
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


def _ring_holds(ring: Ring) -> Callable[[float, float], bool]:
    """``point_in_ring`` for ONE ring and many points: the same test — the same
    numbers in the same order of operations — on arrays when the ring is large.
    (A ladder of 180 cells asks its outer ring of 1,100 vertices 180 times.)"""
    if len(ring) < SIMPLE_RING_NUMPY_FROM:
        return lambda x, y: point_in_ring(x, y, ring)
    xi = np.asarray([x for x, _y in ring], dtype=np.float64)
    yi = np.asarray([y for _x, y in ring], dtype=np.float64)
    xj, yj = np.roll(xi, 1), np.roll(yi, 1)

    def holds(x: float, y: float) -> bool:
        at = np.flatnonzero((yi > y) != (yj > y))
        if not len(at):
            return False
        crossings = x < (xj[at] - xi[at]) * (y - yi[at]) / (yj[at] - yi[at]) + xi[at]
        return bool(np.count_nonzero(crossings) & 1)

    return holds


def group_rings(rings: Sequence[Ring], work: WorkBudget | None = None) -> tuple[Region, ...]:
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

    Every hole asks every outer ring: holes x outers, and each question costs the
    outer ring's vertices. That product is paid into ``work`` (a budget of its
    own when none is given) BEFORE the first question — 2,000 small holes in
    2,000 small outer rings took 3.8 s here when nothing asked. Beyond the
    budget: ``RegionTooLargeError``.
    """
    outers: list[Ring] = []
    holes: list[Ring] = []
    areas: list[float] = []
    for ring in rings:
        if len(ring) < 3:
            continue
        area = ring_area(ring)
        if area > 0.0:
            outers.append(ring)
            areas.append(area)
        elif area < 0.0:
            holes.append(ring)
    owned: list[list[Ring]] = [[] for _ in outers]
    if holes:
        budget = work if work is not None else WorkBudget("sorting rings into regions")
        budget.spend(len(holes) * sum(_ask_work(len(outer)) for outer in outers)
                     + _INSIDE_VERTEX_UNITS * sum(len(hole) for hole in holes))
    holds = [_ring_holds(outer) for outer in outers] if holes else []
    for hole in holes:
        x, y = _point_inside(hole)
        size = -ring_area(hole)
        best = -1
        for index in range(len(outers)):
            if areas[index] > size and holds[index](x, y) and (
                best < 0 or areas[index] < areas[best]
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


# ── what a cut may cost ───────────────────────────────────────────────────────

#: THE WORK BOUND — what cutting ONE region may cost, with holes or without, and
#: what everything ONE item's outline needs may cost together (the snapshot pays
#: the clipper, the sorting and every region's cut into one ``WorkBudget``: a
#: thousand regions that each fit are not a thousand bounds). What would cost
#: more is refused (``RegionTooLargeError``) instead of holding the GUI thread,
#: and the refusal comes FIRST: a step is paid for before it runs — before the
#: simplicity test, before a bridge, before an ear, before the clipper is asked.
#:
#: Work is counted, not timed, so the verdict is the same on every machine. A
#: unit is priced at about a nanosecond on the development box (the price list
#: below); the things a cut does each pay as they go:
#:
#: - the simplicity test of each ring — every pair of its edges, n²;
#: - a bridge per hole — one sort of the boundary, then a block of candidates at
#:   a time against EVERY edge, as many blocks as it takes to find a free one;
#: - the ear clipping — every corner looked at, and every vertex still in the
#:   loop each time one is tested.
#:
#: What fits, measured (``scripts/measure_region_work.py``, which prints the
#: table in section 8.26.1): a plain traced outline of about 3,500 vertices; a
#: comb of about 1,600; a closed fence along about 1,400 traced points; a
#: square mesh of 16 x 16 openings drawn as one stroke. At the bound a unit
#: took 0.35 to 1.5 ns on the development box, quiet to busy: 0.05 to 0.22 s.
MAX_REGION_WORK: Final = 150_000_000

# The price list, in work units. Fitted to measurements on the development box
# (the script above prints units against milliseconds for each kind of region).
_REGION_UNITS: Final = 3_000  # a region, whatever its size: what asking about it costs at least
_PAIR_UNITS: Final = 3  # every pair of the segments of one ring or path, on arrays: per n² …
_NEAR_PAIR_UNITS: Final = 350  # … and per pair whose boxes meet (those are tested one by one)
_SMALL_PAIR_UNITS: Final = 250  # … in the plain loop (rings below SIMPLE_RING_NUMPY_FROM)
_ASK_UNITS: Final = 12_000  # group_rings: one outer ring asked about one point, on arrays …
_ASK_VERTEX_UNITS: Final = 3  # … per vertex of that ring
_ASK_SMALL_VERTEX_UNITS: Final = 120  # … in the plain loop (rings below SIMPLE_RING_NUMPY_FROM)
_INSIDE_VERTEX_UNITS: Final = 700  # … and the point inside a hole: per vertex of the hole
_HOLE_UNITS: Final = 140_000  # a bridge: setting the search up, per hole …
_HOLE_VERTEX_UNITS: Final = 100  # … per boundary vertex (sorted by distance) …
_HOLE_EDGE_UNITS: Final = 60  # … and per edge of the boundary and the holes still waiting
_BRIDGE_UNITS: Final = 60  # a block of candidates: per edge it is tested against
_EAR_STEP_UNITS: Final = 300  # the ear clipper: per corner looked at
_EAR_TEST_UNITS: Final = 6_000  # per corner TESTED against the loop …
_EAR_VERTEX_UNITS: Final = 3  # … per vertex of the loop (which of them are near?) …
_EAR_NEAR_UNITS: Final = 400  # … and per vertex near, when they are few (one by one)
_EAR_MANY_UNITS: Final = 15_000  # … or, when they are many (arrays): this …
_EAR_MANY_NEAR_UNITS: Final = 40  # … and this per vertex near
_SHARED_CLIP_UNITS: Final = 200  # triangulate_polygon (small rings without holes): per n²

#: From this many vertices on, a ring WITHOUT holes is cut by the array ear clipper
#: as well. Below it ``triangulate_polygon`` — the clipper the Qt 3D view uses —
#: is faster and gives that view's triangles vertex for vertex; above it the plain
#: loop is what an edit of a traced outline waits for on every tick. Measured, same
#: machine, same load: 100 vertices 2.3 ms (loop) / 4.0 ms (arrays); 500: 76 / 25;
#: 1,000: 272 / 30.
ARRAY_EAR_CLIP_FROM: Final = 256
_BRIDGE_CANDIDATES: Final = 6  # boundary vertices tried per block, nearest first
_FEW_NEAR: Final = 16  # up to this many vertices near an ear are tested one by one


class RegionTooLargeError(ValueError):
    """Cutting a region would cost more than ``MAX_REGION_WORK``: it is refused
    for its SIZE, whatever its shape. (A ``ValueError``, like every other reason
    a region cannot be cut.)"""


def _too_large(what: str) -> str:
    return (f"{what} is beyond what is cut here (the work bound, MAX_REGION_WORK = "
            f"{MAX_REGION_WORK:,} units)")


def _refuse(what: str) -> NoReturn:
    raise RegionTooLargeError(_too_large(what))


class WorkBudget:
    """What is left to spend — on ONE region's cut, or on everything ONE item's
    outline needs. ``spend`` raises ``RegionTooLargeError`` the moment the bound
    is passed, so nothing runs for longer than the bound: pay for a step BEFORE
    it runs wherever its price is known beforehand, and as it goes where it is
    not (the ears, the bridges)."""

    __slots__ = ("budget", "left", "what")

    def __init__(self, what: str = "an outline", budget: int | None = None) -> None:
        self.what = what
        self.budget = MAX_REGION_WORK if budget is None else budget
        self.left = self.budget

    @property
    def spent(self) -> int:
        return self.budget - self.left

    def spend(self, units: int) -> None:
        self.left -= units
        if self.left < 0:
            _refuse(self.what)

    def refuse(self) -> NoReturn:
        """Refuse outright: for a size that no budget covers (``MAX_ITEM_VERTICES``)."""
        _refuse(self.what)


def _simplicity_work(vertices: int) -> int:
    """What ``is_simple_ring`` costs for a ring of that many vertices."""
    pair = _PAIR_UNITS if vertices >= SIMPLE_RING_NUMPY_FROM else _SMALL_PAIR_UNITS
    return pair * vertices * vertices


def _ask_work(vertices: int) -> int:
    """What asking ONE outer ring of that many vertices about ONE point costs (``_ring_holds``)."""
    if vertices >= SIMPLE_RING_NUMPY_FROM:
        return _ASK_UNITS + _ASK_VERTEX_UNITS * vertices
    return _ASK_SMALL_VERTEX_UNITS * (vertices + 2)


def touching_pairs(points: Sequence[Sequence[float]], work: WorkBudget, *, closed: bool,
                   reach: float = 0.0) -> tuple[int, int]:
    """``(near, touching)`` among the pairs of NON-ADJACENT segments of a path —
    of a ring when ``closed``.

    ``touching``: the pairs that share a point — where a polygon crosses or
    touches itself, where a path crosses its own line. ``near``: the pairs whose
    bounding boxes meet once each is widened by ``reach``; every touching pair
    is among them. These two numbers are what the clipper's work on the path
    depends on, and they are counted BEFORE the clipper sees it: 800 points
    scribbled across a plan cross 73,000 times, and resolving them took ten
    seconds.

    The count itself is every pair once, on arrays, and is paid into ``work``
    before it runs; the near pairs are paid before they are tested one by one.
    """
    segments = len(points) if closed else len(points) - 1
    if segments < 3:
        return 0, 0
    work.spend(_PAIR_UNITS * segments * segments)
    xy = np.asarray(points, dtype=np.float64)
    first, second = xy[:segments], np.roll(xy, -1, axis=0)[:segments]
    low, high = np.minimum(first, second) - reach, np.maximum(first, second) + reach
    index = np.arange(segments)
    near_pairs = touching = 0
    for start in range(0, segments, _SIMPLE_RING_ROWS):
        rows = slice(start, min(start + _SIMPLE_RING_ROWS, segments))
        later = slice(start, segments)
        row = index[rows, None]
        column = index[None, later]
        near = ((low[rows, None, 0] <= high[None, later, 0]) & (low[None, later, 0] <= high[rows, None, 0])
                & (low[rows, None, 1] <= high[None, later, 1]) & (low[None, later, 1] <= high[rows, None, 1])
                & (column > row + 1))
        if closed:
            near &= ~((row == 0) & (column == segments - 1))  # the ring's last segment meets its first
        i, j = np.nonzero(near)
        if not len(i):
            continue
        near_pairs += len(i)
        work.spend(_NEAR_PAIR_UNITS * len(i))
        i, j = i + start, j + start
        touching += int(np.count_nonzero(_touch(first[i, 0], first[i, 1], second[i, 0], second[i, 1],
                                                first[j, 0], first[j, 1], second[j, 0], second[j, 1])))
    return near_pairs, touching


def stack_height(rings: Sequence[Sequence[Sequence[float]]], *, closed: bool, reach: float = 0.0) -> int:
    """The most segments of ``rings`` — of paths when not ``closed`` — that ONE
    horizontal line crosses, each segment's height widened by ``reach``.

    What a sweep-line clipper pays per step: it carries every edge the line
    crosses. A zigzag of 4,000 points whose teeth stand side by side, crossing
    nothing, has a stack of 4,000 and took the clipper 1.6 s to inflate; lying on
    its side it has a stack of 2 and took 4 ms. Linear to compute (one sort).
    """
    lows, highs = [], []
    for ring in rings:
        if len(ring) < 2:
            continue
        y = np.asarray(ring, dtype=np.float64)[:, 1]
        following = np.roll(y, -1)
        if not closed:
            y, following = y[:-1], following[:-1]
        lows.append(np.minimum(y, following))
        highs.append(np.maximum(y, following))
    if not lows:
        return 0
    low, high = np.sort(np.concatenate(lows) - reach), np.sort(np.concatenate(highs) + reach)
    # at the k-th lowest lower end: k + 1 segments have begun, and those whose upper end
    # lies below it have ended
    return int((np.arange(1, len(low) + 1) - np.searchsorted(high, low, side="left")).max())


def _triangulation_floor(outer_vertices: int, hole_sizes: Sequence[int]) -> int:
    """The LEAST ``triangulate_region`` can cost for rings of these sizes: every
    hole bridged at its first block, every corner an ear at first sight."""
    holes = len(hole_sizes)
    if not holes and outer_vertices < ARRAY_EAR_CLIP_FROM:
        return _SHARED_CLIP_UNITS * outer_vertices * outer_vertices
    vertices = outer_vertices + sum(hole_sizes)
    loop = vertices + 2 * holes
    # every ear but the last triangle is tested once at least, and its own three
    # corners are always near it
    ear = _EAR_TEST_UNITS + 3 * _EAR_NEAR_UNITS + _EAR_VERTEX_UNITS * loop
    bridge = (_HOLE_UNITS + _HOLE_VERTEX_UNITS * outer_vertices
              + (_HOLE_EDGE_UNITS + _BRIDGE_UNITS) * vertices)
    return holes * bridge + max(loop - 3, 0) * ear


def region_work(outer: Sequence[Sequence[float]], holes: Sequence[Sequence[Sequence[float]]] = ()) -> int:
    """The least ``cut_region`` can cost for this region, in work units — from
    the SIZES of its rings alone (their vertex counts; no coordinate is read), so
    it costs nothing to ask. A region with ``region_work(...) > MAX_REGION_WORK``
    is refused before any test runs; one below may still be refused while it is
    cut, when its shape makes the bridges or the ears cost more than the least."""
    sizes = [len(hole) for hole in holes]
    return (_REGION_UNITS + _simplicity_work(len(outer)) + sum(_simplicity_work(size) for size in sizes)
            + _triangulation_floor(len(outer), sizes))


def exceeds_work_bound(outer: Sequence[Sequence[float]],
                       holes: Sequence[Sequence[Sequence[float]]] = ()) -> bool:
    """True when rings of these SIZES are beyond ``MAX_REGION_WORK`` whatever
    their shape — ``region_work(...) > MAX_REGION_WORK``. Costs nothing to ask;
    ask it before doing anything to an outline that costs."""
    return region_work(outer, holes) > MAX_REGION_WORK


def _bridge_position(points: Sequence[Point], xy: NDArray[np.float64], loop: list[int],
                     hole_vertex: int, waiting_first: NDArray[np.intp],
                     waiting_second: NDArray[np.intp], work: WorkBudget) -> int:
    """The position in ``loop`` of the nearest boundary vertex that ``hole_vertex``
    can be bridged to: the segment between them touches no edge of the boundary
    or of a hole still to be merged (``waiting_first`` → ``waiting_second``, this
    hole's own edges among them), and leaves the boundary into its inside.

    Boundary vertices are tried nearest first, a block at a time, each block
    against every edge at once. Per hole that is one sort of the boundary plus,
    usually, one block: O(L log L + E) for a boundary of L vertices and E edges.
    A hole whose nearest boundary vertices are all hidden takes block after
    block — which is why each block is paid for (``work``).
    """
    index = np.asarray(loop, dtype=np.intp)
    first = np.concatenate([index, waiting_first])
    second = np.concatenate([np.roll(index, -1), waiting_second])
    work.spend(_HOLE_UNITS + _HOLE_VERTEX_UNITS * len(index) + _HOLE_EDGE_UNITS * len(first))
    cx, cy, dx, dy = xy[first, 0], xy[first, 1], xy[second, 0], xy[second, 1]
    hx, hy = float(xy[hole_vertex, 0]), float(xy[hole_vertex, 1])
    at_hole = (first == hole_vertex) | (second == hole_vertex)
    order = np.argsort(np.hypot(xy[index, 0] - hx, xy[index, 1] - hy), kind="stable")
    block = _BRIDGE_UNITS * len(first)
    for start in range(0, len(order), _BRIDGE_CANDIDATES):
        positions = [int(at) for at in order[start:start + _BRIDGE_CANDIDATES]
                     if _inside_corner(points, loop, int(at), hole_vertex)]
        if not positions:
            work.spend(_BRIDGE_CANDIDATES * _EAR_STEP_UNITS)
            continue
        work.spend(block)
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


def _ear_clip(points: Sequence[Point], loop: Sequence[int], work: WorkBudget) -> list[Triangle]:
    """Clip ears of a counter-clockwise loop of indices into ``points``. The loop
    may visit a vertex twice (a bridge to a hole runs out and back): a second
    visit to an ear's own corner never blocks that ear.

    Each candidate corner is tested against every vertex still in the loop at
    once; after a clipped ear the search goes on at its neighbour. O(n) array
    passes for a well-behaved loop of n vertices, O(n²) at worst — a comb, whose
    every tooth has to wait for its neighbour — so every corner looked at and
    every test is paid for (``work``).

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
    test = _EAR_TEST_UNITS + _EAR_VERTEX_UNITS * n
    remaining, at, misses = n, 0, 0
    while remaining > 3:
        a, c = before[at], after[at]
        ax, ay, bx, by, cx, cy = xs[a], ys[a], xs[at], ys[at], xs[c], ys[c]
        ear = False
        if (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) > 0:  # a convex corner
            work.spend(test)
            # only a vertex inside the triangle's bounding box can be inside the triangle
            near = np.flatnonzero(alive & (x >= min(ax, bx, cx)) & (x <= max(ax, bx, cx))
                                  & (y >= min(ay, by, cy)) & (y <= max(ay, by, cy)))
            work.spend(_EAR_NEAR_UNITS * len(near) if len(near) <= _FEW_NEAR
                       else _EAR_MANY_UNITS + _EAR_MANY_NEAR_UNITS * len(near))
            if len(near) <= _FEW_NEAR:
                # the usual case — the ear's own corners and a neighbour or two: the
                # same test, written out (forty array calls on three numbers cost
                # more than the three numbers)
                ear = True
                for k in near.tolist():
                    qx, qy = xs[k], ys[k]
                    if (qx == ax and qy == ay) or (qx == bx and qy == by) or (qx == cx and qy == cy):
                        continue
                    e1 = (ax - qx) * (by - qy) - (ay - qy) * (bx - qx)
                    e2 = (bx - qx) * (cy - qy) - (by - qy) * (cx - qx)
                    e3 = (cx - qx) * (ay - qy) - (cy - qy) * (ax - qx)
                    if not ((e1 < 0 or e2 < 0 or e3 < 0) and (e1 > 0 or e2 > 0 or e3 > 0)):
                        ear = False
                        break
            else:
                px, py = x[near], y[near]
                d1 = (ax - px) * (by - py) - (ay - py) * (bx - px)  # _point_in_triangle, for each
                d2 = (bx - px) * (cy - py) - (by - py) * (cx - px)
                d3 = (cx - px) * (ay - py) - (cy - py) * (ax - px)
                inside = ~(((d1 < 0) | (d2 < 0) | (d3 < 0)) & ((d1 > 0) | (d2 > 0) | (d3 > 0)))
                corner = (((px == ax) & (py == ay)) | ((px == bx) & (py == by))
                          | ((px == cx) & (py == cy)))
                ear = not bool(np.any(inside & ~corner))
        else:
            work.spend(_EAR_STEP_UNITS)
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


def _described(outer: Sequence[object], holes: Sequence[Sequence[object]]) -> str:
    vertices = len(outer) + sum(len(hole) for hole in holes)
    return f"a region of {vertices} vertices with {len(holes)} holes"


def _triangulate(outer: Ring, holes: Sequence[Ring], work: WorkBudget) -> tuple[tuple[Point, ...], list[Triangle]]:
    """``triangulate_region``, paying into ``work``."""
    points: tuple[Point, ...] = tuple(outer) + tuple(p for hole in holes for p in hole)
    if not holes:
        if len(outer) >= ARRAY_EAR_CLIP_FROM and ring_area(outer) > 0.0:
            return points, _ear_clip(points, range(len(outer)), work)
        work.spend(_SHARED_CLIP_UNITS * len(outer) * len(outer))
        return points, triangulate_polygon(outer)
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
                              second[begins[number]:], work)
        # out along the bridge, once around the hole, and back
        loop[at + 1:at + 1] = ring[k:] + ring[:k] + [ring[k], loop[at]]
    return points, _ear_clip(points, loop, work)


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
    bridge always exists.

    Its work is bounded like ``cut_region``'s (``MAX_REGION_WORK``): refused
    with ``RegionTooLargeError`` before it starts when the rings' sizes alone
    put it beyond the bound, and while it runs when the shape does.
    """
    what = _described(outer, holes)
    if _triangulation_floor(len(outer), [len(hole) for hole in holes]) > MAX_REGION_WORK:
        _refuse(what)
    return _triangulate(outer, holes, WorkBudget(what))


# ── the one predicate ─────────────────────────────────────────────────────────


_Cut: TypeAlias = tuple[tuple[Point, ...], tuple[Triangle, ...]]
_BUILDABLE, _UNBUILDABLE, _TOO_LARGE = 0, 1, 2
#: Up to this many vertices, and without holes, a region is looked up before it
#: is sized up (a rectangle is asked on every tick; ``_cut`` judges its size
#: anyway). Anything larger is sized up BEFORE it is hashed.
_SMALL_REGION: Final = 64


class _Examined(NamedTuple):
    """What cutting a region came to: the verdict, the work it took, the cut
    when there is one and the refusal's words when there is not."""

    verdict: int
    spent: int
    cut: _Cut | None
    why: str


def _region_vertices(outer: Ring, holes: tuple[Ring, ...]) -> int:
    return len(outer) + sum(len(hole) for hole in holes)


@ring_keyed(vertices=_region_vertices, holds=lambda examined: len(examined.cut[1]) if examined.cut else 0)
def _examined(outer: Ring, holes: tuple[Ring, ...]) -> _Examined:
    """``_cut``, remembered — refusals included: a region that cannot be built
    must not be tried again on every tick. ONE memory for the verdict, what it
    cost and the cut, so that the builder gets the cut the verdict made."""
    work = WorkBudget(_described(outer, holes))
    try:
        cut = _cut(outer, holes, work)
    except RegionTooLargeError as error:
        return _Examined(_TOO_LARGE, work.spent, None, str(error))
    except ValueError as error:
        return _Examined(_UNBUILDABLE, work.spent, None, str(error))
    return _Examined(_BUILDABLE, work.spent, cut, "")


def _examine(outer: Ring, holes: tuple[Ring, ...]) -> _Examined:
    """``_examined`` — after the SIZE: a region whose rings alone put it beyond
    the bound is answered from their lengths, never hashed and never a key."""
    if (len(outer) > _SMALL_REGION or holes) and exceeds_work_bound(outer, holes):
        return _Examined(_TOO_LARGE, 0, None, _too_large(_described(outer, holes)))
    return _examined(outer, holes)


def cut_region(outer: Ring, holes: tuple[Ring, ...] = ()) -> _Cut:
    """``triangulate_region``, with the result PROVEN before it is handed out.

    Raises ``ValueError`` unless the outer ring is a simple counter-clockwise
    polygon, every hole a simple clockwise one, every triangle of the result
    faces up, and the triangles' areas add up to the region's (outer minus
    holes) — the two facts a wrong triangulation cannot both satisfy. This is
    what the default builder calls (``build.prism_mesh``); remembered, so asking
    whether a region can be built costs nothing again when it then is — and a
    refusal is remembered with its words.

    The size is judged FIRST: ``RegionTooLargeError`` (a ``ValueError``) when
    ``region_work`` is beyond ``MAX_REGION_WORK`` — raised before the simplicity
    test or anything else that costs — and when the cut itself, once started,
    would pass the bound.
    """
    examined = _examine(outer, holes)
    if examined.cut is None:
        raise (RegionTooLargeError if examined.verdict == _TOO_LARGE else ValueError)(examined.why)
    return examined.cut


def cut_work(outer: Ring, holes: tuple[Ring, ...] = ()) -> int:
    """What ``cut_region`` spends on this region, in work units — by cutting it
    (nothing is remembered). At least ``region_work(outer, holes)``; raises what
    ``cut_region`` raises. For measurements and tests: a count is the same on
    every machine, a duration is not."""
    work = WorkBudget(_described(outer, holes))
    _cut(outer, holes, work)
    return work.spent


def region_cost(outer: Ring, holes: tuple[Ring, ...] = ()) -> int:
    """What finding out about this region HAS cost, in work units — remembered
    with the verdict, and the same whether the region could be built or not
    (``region_work`` is the least a cut can cost, from the sizes alone; this is
    what it did cost). 0 for a region refused from its size alone. What a
    caller that judges several regions against ONE budget pays per region."""
    return _examine(outer, holes).spent


def _cut(outer: Ring, holes: tuple[Ring, ...], work: WorkBudget) -> _Cut:
    if exceeds_work_bound(outer, holes):
        _refuse(work.what)
    work.spend(_REGION_UNITS)
    for ring in (outer, *holes):
        work.spend(_simplicity_work(len(ring)))
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
    vertices, triangles = _triangulate(outer, holes, work)
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


def is_buildable_region(outer: Ring, holes: tuple[Ring, ...] = ()) -> bool:
    """THE predicate for "a builder can turn this region into a solid": true
    exactly when ``cut_region`` — the default builder's own triangulation — cuts
    it and proves the cut. The snapshot keeps only regions for which it holds.

    It implies what a builder of its own may rely on: the outer ring is a simple
    counter-clockwise polygon, the holes are simple, clockwise and inside it,
    and the triangulation exists. Remembered, false answers included.
    """
    return _examine(outer, holes).verdict == _BUILDABLE


def is_region_too_large(outer: Ring, holes: tuple[Ring, ...] = ()) -> bool:
    """True when ``cut_region`` refuses the region for its SIZE (the work bound)
    — as opposed to its shape. No other reading of the same outline will be
    smaller, so a caller that gets True stops there. Remembered with the verdict."""
    return _examine(outer, holes).verdict == _TOO_LARGE
