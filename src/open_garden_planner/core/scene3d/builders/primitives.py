"""Primitives of the built world — numpy, item-local frame (Phase 17 L1.6).

Every function returns a valid ``Mesh`` (``Mesh.validate()``), in centimetres
about the item's own origin with +z up. Two shading modes:

- **flat** (``flat_mesh`` and everything built on it: ``boxes``,
  ``bevelled_boxes``, ``sweep``, ``plank``, ``polygon_cap``): a triangle soup in
  which every triangle stores its own WINDING normal. The normal is computed
  from the triangle, never written by hand — the L0 spike once stored the other
  roof slope's normal and the sun lit the wrong face.
- **smooth** (``lathe``, ``tubes``): shared vertices with analytic normals round
  the axis; the winding still faces the same way as the normal.

No function loops over vertices in Python; the loops that exist run over a
footprint's edges or an ear-clipped polygon's handful of triangles.

Ported from ``spike_q3d/meshes.py`` (``box``, ``tubes``, ``_face_normal``,
``_clip_halfplane``) and generalised; the spike builds at scene positions, these
build at the origin of whatever frame the caller is in.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Final, TypeAlias

import numpy as np
from numpy.typing import ArrayLike, NDArray

from ..legacy import triangulate_polygon
from ..mesh import Mesh

F64: TypeAlias = NDArray[np.float64]

#: Triangles whose doubled area is below this (cm²) are dropped: they have no
#: usable winding normal. Far above ``mesh.normal_vs_winding``'s own cut (1e-6).
MIN_DOUBLE_AREA: Final = 1e-4
#: A mitre joint is never longer than this many offsets (a hairpin bend).
MITER_LIMIT: Final = 4.0
#: Path points closer than this (cm) are one point.
MIN_SEGMENT_CM: Final = 1e-3

_WHITE: Final = (1.0, 1.0, 1.0, 1.0)


# ── flat-shaded soup ─────────────────────────────────────────────────────────


def _rows(values: ArrayLike, count: int, width: int) -> F64:
    """``values`` as (count, width): one row is repeated, ``count`` rows are kept."""
    array = np.asarray(values, dtype=np.float64)
    if array.ndim == 1:
        return np.broadcast_to(array, (count, width))
    return array.reshape(count, width)


def flat_mesh(triangles: ArrayLike, colors: ArrayLike = _WHITE,
              outward: ArrayLike | None = None, uvs: ArrayLike | None = None) -> Mesh:
    """A flat-shaded mesh from triangles ``(t, 3, 3)``.

    ``colors``: one linear RGBA or one per triangle. ``outward``: one direction
    or one per triangle — a triangle wound against its direction is flipped, so
    a caller that knows which way a face looks cannot get the winding wrong.
    ``uvs``: one (u, v) or one per triangle. Degenerate triangles are dropped.
    The stored normal of every vertex is its triangle's winding normal.
    """
    tri = np.array(triangles, dtype=np.float64).reshape(-1, 3, 3)
    count = len(tri)
    if count == 0:
        return Mesh.empty()
    col = _rows(colors, count, 4)
    uv = _rows(uvs if uvs is not None else (0.0, 0.0), count, 2)
    cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    if outward is not None:
        flip = (cross * _rows(outward, count, 3)).sum(axis=1) < 0.0
        tri[flip] = tri[flip][:, [0, 2, 1]]
        cross[flip] = -cross[flip]
    length = np.linalg.norm(cross, axis=1)
    keep = length > MIN_DOUBLE_AREA
    kept = int(keep.sum())
    if kept == 0:
        return Mesh.empty()
    normals = cross[keep] / length[keep, None]
    return Mesh.from_arrays(
        tri[keep].reshape(-1, 3),
        np.repeat(normals, 3, axis=0),
        np.repeat(col[keep], 3, axis=0),
        np.repeat(uv[keep], 3, axis=0),
        np.arange(kept * 3),
    )


def quad_triangles(quads: ArrayLike) -> F64:
    """Quads ``(q, 4, 3)`` → triangles ``(2q, 3, 3)``, keeping the winding."""
    quad = np.asarray(quads, dtype=np.float64).reshape(-1, 4, 3)
    return quad[:, [0, 1, 2, 0, 2, 3]].reshape(-1, 3, 3)


def _per_item(values: ArrayLike, count: int) -> F64:
    return np.broadcast_to(np.asarray(values, dtype=np.float64), (count,))


def _turned(local_x: F64, local_y: F64, centers: F64, angles_deg: ArrayLike) -> tuple[F64, F64]:
    """Local (n, k) offsets turned by each item's angle and moved to its centre."""
    angle = np.radians(_per_item(angles_deg, len(centers)))
    cos_a, sin_a = np.cos(angle)[:, None], np.sin(angle)[:, None]
    x = centers[:, 0:1] + local_x * cos_a - local_y * sin_a
    y = centers[:, 1:2] + local_x * sin_a + local_y * cos_a
    return x, y


def _ring_points(x: F64, y: F64, z: ArrayLike) -> F64:
    """(n, k) x/y and a per-item z → (n, k, 3)."""
    height = np.broadcast_to(_per_item(z, len(x))[:, None], x.shape)
    return np.stack([x, y, height], axis=-1)


def _band(lower: F64, upper: F64) -> F64:
    """Quads between two rings (n, k, 3) of a counter-clockwise outline: (n, k, 4, 3),
    wound to face outward."""
    return np.stack([lower, np.roll(lower, -1, axis=1), np.roll(upper, -1, axis=1), upper], axis=2)


def boxes(centers: ArrayLike, sizes: ArrayLike, z0: ArrayLike, z1: ArrayLike,
          angles_deg: ArrayLike = 0.0, colors: ArrayLike = _WHITE, *,
          bottom: bool = False) -> Mesh:
    """N upright boxes in one mesh: four sides and a top (and a bottom on request).

    ``centers`` (n, 2); ``sizes`` (n, 2) or one (along, across); ``z0`` / ``z1``
    the bottom and top heights (given, not derived, so a top can be EXACTLY a
    height); ``angles_deg`` turns each box about its own centre, counter-clockwise.
    10 triangles each (12 with a bottom).
    """
    center = np.asarray(centers, dtype=np.float64).reshape(-1, 2)
    count = len(center)
    if count == 0:
        return Mesh.empty()
    half = _rows(sizes, count, 2) / 2.0
    corner = np.array([[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]])  # counter-clockwise
    x, y = _turned(corner[None, :, 0] * half[:, 0:1], corner[None, :, 1] * half[:, 1:2],
                   center, angles_deg)
    low, high = _ring_points(x, y, z0), _ring_points(x, y, z1)
    faces = [_band(low, high), high[:, None, :, :]]
    if bottom:
        faces.append(low[:, None, ::-1, :])
    quads = np.concatenate(faces, axis=1)  # (n, faces, 4, 3)
    per_box = quads.shape[1] * 2
    return flat_mesh(quad_triangles(quads), np.repeat(_rows(colors, count, 4), per_box, axis=0))


def bevelled_boxes(centers: ArrayLike, sizes: ArrayLike, z0: ArrayLike, z1: ArrayLike,
                   bevel: float, angles_deg: ArrayLike = 0.0,
                   colors: ArrayLike = _WHITE) -> Mesh:
    """N upright boxes with chamfered vertical edges and a chamfered top.

    The plan outline is the rectangle with its corners cut by ``bevel``; the top
    is that outline offset inward by ``bevel``, ``bevel`` below ``z1`` — so the
    top face is exactly at ``z1`` and every face is planar. ``bevel`` is limited
    to 40 % of the shorter half side and half the height. 38 triangles each.
    """
    center = np.asarray(centers, dtype=np.float64).reshape(-1, 2)
    count = len(center)
    if count == 0:
        return Mesh.empty()
    half = _rows(sizes, count, 2) / 2.0
    low_z, high_z = _per_item(z0, count), _per_item(z1, count)
    cut = np.minimum(np.minimum(bevel, 0.4 * half.min(axis=1)), 0.5 * (high_z - low_z))
    cut = np.maximum(cut, 0.0)

    def octagon(hx: F64, hy: F64, c: F64) -> tuple[F64, F64]:
        xs = np.stack([-hx + c, hx - c, hx, hx, hx - c, -hx + c, -hx, -hx], axis=1)
        ys = np.stack([-hy, -hy, -hy + c, hy - c, hy, hy, hy - c, -hy + c], axis=1)
        return _turned(xs, ys, center, angles_deg)

    x, y = octagon(half[:, 0], half[:, 1], cut)
    # the same outline offset inward by `cut`: a rectangle `cut` smaller whose corners are
    # cut by (√2 − 1)·cut, so each bevel face is a planar trapezoid
    tx, ty = octagon(half[:, 0] - cut, half[:, 1] - cut, (math.sqrt(2.0) - 1.0) * cut)
    low = _ring_points(x, y, low_z)
    shoulder = _ring_points(x, y, high_z - cut)
    top = _ring_points(tx, ty, high_z)
    fan = top[:, [0, 1, 2, 0, 2, 3, 0, 3, 4, 0, 4, 5, 0, 5, 6, 0, 6, 7]].reshape(count, 6, 3, 3)
    triangles = np.concatenate([
        quad_triangles(_band(low, shoulder)).reshape(count, 16, 3, 3),
        quad_triangles(_band(shoulder, top)).reshape(count, 16, 3, 3),
        fan,
    ], axis=1)
    return flat_mesh(triangles.reshape(-1, 3, 3), np.repeat(_rows(colors, count, 4), 38, axis=0))


# ── polygons in plan ─────────────────────────────────────────────────────────


def signed_area(ring: ArrayLike) -> float:
    """Shoelace area of a plan polygon: positive when counter-clockwise."""
    points = np.asarray(ring, dtype=np.float64).reshape(-1, 2)
    if len(points) < 3:
        return 0.0
    following = np.roll(points, -1, axis=0)
    return float((points[:, 0] * following[:, 1] - following[:, 0] * points[:, 1]).sum() / 2.0)


def clean_path(points: ArrayLike) -> F64:
    """A polyline without repeated points (segments shorter than ``MIN_SEGMENT_CM``)."""
    path = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    if len(path) < 2:
        return path
    keep = np.ones(len(path), dtype=bool)
    keep[1:] = np.linalg.norm(np.diff(path, axis=0), axis=1) > MIN_SEGMENT_CM
    return path[keep]


def ccw_ring(ring: ArrayLike) -> F64:
    """A plan polygon as (n, 2), counter-clockwise, without repeated or closing points."""
    points = clean_path(ring)
    if len(points) > 1 and float(np.linalg.norm(points[0] - points[-1])) <= MIN_SEGMENT_CM:
        points = points[:-1]
    if signed_area(points) < 0.0:
        points = points[::-1]
    return np.ascontiguousarray(points)


def _left_normals(directions: F64) -> F64:
    return np.stack([-directions[:, 1], directions[:, 0]], axis=1)


def _miter(before: F64, after: F64) -> F64:
    """Offset direction at a joint between two unit normals: along it, a unit
    offset of both adjoining edges is kept (length 1 / cos of half the turn),
    limited to ``MITER_LIMIT`` at a hairpin."""
    total = before + after
    scale = 1.0 + (before * after).sum(axis=1)
    safe = scale > 2.0 / (MITER_LIMIT * MITER_LIMIT)
    out = np.empty_like(total)
    out[safe] = total[safe] / scale[safe, None]
    sharp = ~safe
    if sharp.any():
        length = np.linalg.norm(total[sharp], axis=1)
        fallback = np.where(length[:, None] > 1e-9,
                            total[sharp] / np.maximum(length, 1e-9)[:, None] * MITER_LIMIT,
                            after[sharp])
        out[sharp] = fallback
    return out


def offset_ring(ring: ArrayLike, distance: float) -> F64:
    """A counter-clockwise plan polygon moved outward by ``distance`` cm (inward when
    negative), mitred corners. Every edge keeps its direction and moves exactly
    ``distance`` — a roof's overhang, a bed's soil inset."""
    points = ccw_ring(ring)
    if len(points) < 3:
        return points
    edge = np.roll(points, -1, axis=0) - points
    edge /= np.maximum(np.linalg.norm(edge, axis=1, keepdims=True), 1e-12)
    outward = -_left_normals(edge)  # right of travel on a counter-clockwise ring
    return points + distance * _miter(np.roll(outward, 1, axis=0), outward)


def polygon_triangles(ring: ArrayLike) -> F64:
    """Ear-clipped plan triangles ``(t, 3, 2)`` of a polygon, each counter-clockwise."""
    points = np.asarray(ring, dtype=np.float64).reshape(-1, 2)
    if len(points) < 3:
        return np.zeros((0, 3, 2))
    index = triangulate_polygon([(float(x), float(y)) for x, y in points])
    if not index:
        return np.zeros((0, 3, 2))
    return points[np.asarray(index, dtype=np.int64)]


def polygon_cap(ring: ArrayLike, z: float, color: ArrayLike = _WHITE, *, up: bool = True) -> Mesh:
    """A flat polygon at height ``z``, facing up (or down)."""
    plan = polygon_triangles(ring)
    if not len(plan):
        return Mesh.empty()
    triangles = np.concatenate([plan, np.full((len(plan), 3, 1), float(z))], axis=2)
    return flat_mesh(triangles, color, outward=(0.0, 0.0, 1.0 if up else -1.0))


def prism(ring: ArrayLike, z0: float, z1: float, color: ArrayLike = _WHITE, *,
          top: bool = True) -> Mesh:
    """A plan polygon extruded from ``z0`` to ``z1``: its walls and, unless ``top``
    is False, its lid. Any simple polygon, either winding."""
    points = ccw_ring(ring)
    if len(points) < 3 or z1 <= z0:
        return Mesh.empty()
    low = np.concatenate([points, np.full((len(points), 1), float(z0))], axis=1)[None]
    high = np.concatenate([points, np.full((len(points), 1), float(z1))], axis=1)[None]
    walls = flat_mesh(quad_triangles(_band(low, high)), color)
    return Mesh.merge([walls, polygon_cap(points, z1, color)]) if top else walls


def split_triangles_by_line(triangles: ArrayLike, origin: ArrayLike,
                            normal: ArrayLike) -> tuple[F64, F64]:
    """Plan triangles ``(t, 3, 2)`` cut by the line through ``origin`` with unit
    left-normal ``normal``: (those on the normal's side, those on the other), every
    piece counter-clockwise like its source. A roof's two slopes."""
    source = np.asarray(triangles, dtype=np.float64).reshape(-1, 3, 2)
    base = np.asarray(origin, dtype=np.float64)
    direction = np.asarray(normal, dtype=np.float64)
    tolerance = 1e-7
    sides: tuple[list[F64], list[F64]] = ([], [])
    for triangle in source:  # a footprint's handful of triangles
        distance = (triangle - base) @ direction
        if not (distance < -tolerance).any():
            sides[0].append(triangle)
            continue
        if not (distance > tolerance).any():
            sides[1].append(triangle)
            continue
        pieces: tuple[list[F64], list[F64]] = ([], [])
        for index in range(3):
            here, there = triangle[index], triangle[(index + 1) % 3]
            d_here, d_there = float(distance[index]), float(distance[(index + 1) % 3])
            if d_here >= -tolerance:
                pieces[0].append(here)
            if d_here <= tolerance:
                pieces[1].append(here)
            if (d_here > tolerance and d_there < -tolerance) or (
                    d_here < -tolerance and d_there > tolerance):
                crossing = here + (there - here) * (d_here / (d_here - d_there))
                pieces[0].append(crossing)
                pieces[1].append(crossing)
        for side, polygon in zip(sides, pieces, strict=True):
            for index in range(1, len(polygon) - 1):
                side.append(np.stack([polygon[0], polygon[index], polygon[index + 1]]))
    empty = np.zeros((0, 3, 2))
    return (np.stack(sides[0]) if sides[0] else empty, np.stack(sides[1]) if sides[1] else empty)


# ── sweep ────────────────────────────────────────────────────────────────────


def sweep(profile: ArrayLike, path: ArrayLike, colors: ArrayLike = _WHITE, *,
          closed: bool = False, profile_closed: bool = True, caps: bool = True,
          cap_color: ArrayLike | None = None, edges: Sequence[bool] | None = None) -> Mesh:
    """A cross-section swept along a horizontal polyline, joints mitred.

    ``profile`` (p, 2): points ``(lateral, z)`` — lateral is cm to the LEFT of the
    direction of travel — listed counter-clockwise in that plane (lateral to the
    right, z up: the section seen from ahead, looking back). ``path`` (m, 2):
    plan points. ``closed``: the path is a loop (do not repeat the first point).
    ``profile_closed``: the last profile point joins the first; an open profile
    is a sheet (a bed's frame without its buried underside). ``colors``: one, or
    one per profile edge. ``edges``: False leaves a profile edge out (a face lying
    on the ground). ``caps``: flat ends on an open path with a closed profile, in
    ``cap_color`` (default: the first colour).

    Each wall is one planar quad per path segment: at a joint both sides move
    along the mitre, so they stay on lines parallel to the segment.
    """
    section = np.asarray(profile, dtype=np.float64).reshape(-1, 2)
    line = clean_path(path)
    if closed and len(line) > 2 and float(np.linalg.norm(line[0] - line[-1])) <= MIN_SEGMENT_CM:
        line = line[:-1]
    if len(section) < 2 or len(line) < 2 or (closed and len(line) < 3):
        return Mesh.empty()
    points = len(line)
    travel = (np.roll(line, -1, axis=0) - line)[: points if closed else points - 1]
    travel /= np.maximum(np.linalg.norm(travel, axis=1, keepdims=True), 1e-12)
    left = _left_normals(travel)
    if closed:
        miter = _miter(np.roll(left, 1, axis=0), left)
    else:
        miter = np.empty((points, 2))
        miter[0], miter[-1] = left[0], left[-1]
        if points > 2:
            miter[1:-1] = _miter(left[:-1], left[1:])
    plan = line[:, None, :] + section[None, :, 0:1] * miter[:, None, :]  # (m, p, 2)
    rise = np.broadcast_to(section[None, :, 1:2], (points, len(section), 1))
    stations = np.concatenate([plan, rise], axis=2)  # (m, p, 3)

    edge_count = len(section) if profile_closed else len(section) - 1
    used = np.ones(edge_count, dtype=bool) if edges is None else np.asarray(edges, dtype=bool)
    start = np.arange(edge_count)[used]
    end = (start + 1) % len(section)
    here = stations if closed else stations[:-1]
    there = np.roll(stations, -1, axis=0) if closed else stations[1:]
    quads = np.stack([here[:, start], here[:, end], there[:, end], there[:, start]], axis=2)
    edge_colors = _rows(colors, edge_count, 4)[used]
    triangles = [quad_triangles(quads)]
    triangle_colors = [np.tile(np.repeat(edge_colors, 2, axis=0), (len(here), 1))]
    if caps and profile_closed and not closed:
        index = triangulate_polygon([(float(a), float(b)) for a, b in section])
        if index:
            corner = np.asarray(index, dtype=np.int64)
            triangles.append(stations[0][corner[:, [0, 2, 1]]])  # looks back along the path
            triangles.append(stations[-1][corner])
            cap = np.asarray(cap_color if cap_color is not None else _rows(colors, edge_count, 4)[0],
                             dtype=np.float64)
            triangle_colors.append(np.broadcast_to(cap, (2 * len(corner), 4)))
    return flat_mesh(np.concatenate(triangles), np.concatenate(triangle_colors))


def plank(start: ArrayLike, end: ArrayLike, width: float, z0: float, z1: float,
          bevel: float = 0.5, color: ArrayLike = _WHITE) -> Mesh:
    """One board from ``start`` to ``end`` (plan points): ``width`` across, from
    ``z0`` up to ``z1``, its four long edges chamfered by ``bevel``. 28 triangles."""
    half = width / 2.0
    cut = max(0.0, min(bevel, 0.4 * half, 0.4 * (z1 - z0)))
    section = [(-half + cut, z0), (half - cut, z0), (half, z0 + cut), (half, z1 - cut),
               (half - cut, z1), (-half + cut, z1), (-half, z1 - cut), (-half, z0 + cut)]
    return sweep(section, np.asarray([start, end], dtype=np.float64).reshape(2, 2), color)


# ── surfaces of revolution and tubes (smooth) ────────────────────────────────


def lathe(profile: ArrayLike, colors: ArrayLike = _WHITE, segments: int = 24) -> Mesh:
    """A surface of revolution about +z.

    ``profile`` (p, 2): points ``(radius, z)`` listed so that the surface's
    outside is on the RIGHT of travel — up the outer wall, over the rim, down the
    inner wall. Ring vertex j sits at angle ``j·360°/segments``: with 24 segments
    exactly the vertices of the 2D footprint (``shadow_geometry.circle_footprint``),
    so a rim of radius r IS the plan's 24-gon. Smooth round the axis, a hard
    edge at every profile point. ``colors``: one, or one per profile edge.
    2·segments triangles per profile edge.
    """
    section = np.asarray(profile, dtype=np.float64).reshape(-1, 2)
    if len(section) < 2 or segments < 3:
        return Mesh.empty()
    step = section[1:] - section[:-1]
    length = np.linalg.norm(step, axis=1)
    keep = length > MIN_SEGMENT_CM
    if not keep.any():
        return Mesh.empty()
    first, second, step, length = section[:-1][keep], section[1:][keep], step[keep], length[keep]
    edge_colors = _rows(colors, len(section) - 1, 4)[keep]
    edge_count = len(first)
    angle = np.arange(segments) * (2.0 * math.pi / segments)
    cos_a, sin_a = np.cos(angle), np.sin(angle)
    out_r, out_z = step[:, 1] / length, -step[:, 0] / length  # the edge's normal in (r, z)

    def ring(radius: F64, z: F64) -> F64:
        return np.stack([radius[:, None] * cos_a[None, :], radius[:, None] * sin_a[None, :],
                         np.broadcast_to(z[:, None], (edge_count, segments))], axis=-1)

    positions = np.stack([ring(first[:, 0], first[:, 1]), ring(second[:, 0], second[:, 1])], axis=1)
    normal = np.stack([out_r[:, None] * cos_a[None, :], out_r[:, None] * sin_a[None, :],
                       np.broadcast_to(out_z[:, None], (edge_count, segments))], axis=-1)
    normals = np.stack([normal, normal], axis=1)
    j = np.arange(segments)
    following = (j + 1) % segments
    quad = np.stack([j, following, following + segments, j, following + segments, j + segments],
                    axis=1).reshape(-1)
    index = quad[None, :] + (np.arange(edge_count) * 2 * segments)[:, None]
    return Mesh.from_arrays(
        positions.reshape(-1, 3), normals.reshape(-1, 3),
        np.repeat(edge_colors, 2 * segments, axis=0),
        np.zeros((edge_count * 2 * segments, 2)), index.reshape(-1),
    )


def tubes(starts: ArrayLike, ends: ArrayLike, radius_start: ArrayLike, radius_end: ArrayLike,
          color: ArrayLike = _WHITE, sides: int = 6) -> Mesh:
    """Frustums from ``starts[i]`` to ``ends[i]`` (3D points) in one mesh — wires,
    rails. Smooth round each axis, open ends. ``2·sides`` triangles each.
    Ported from the spike's ``meshes.tubes``."""
    a = np.asarray(starts, dtype=np.float64).reshape(-1, 3)
    b = np.asarray(ends, dtype=np.float64).reshape(-1, 3)
    count = len(a)
    if count == 0 or sides < 3:
        return Mesh.empty()
    axis = b - a
    axis /= np.maximum(np.linalg.norm(axis, axis=1, keepdims=True), 1e-12)
    helper = np.where(np.abs(axis[:, 2:3]) < 0.9, [[0.0, 0.0, 1.0]], [[1.0, 0.0, 0.0]])
    u = np.cross(axis, helper)
    u /= np.maximum(np.linalg.norm(u, axis=1, keepdims=True), 1e-12)
    v = np.cross(axis, u)
    angle = np.arange(sides) * (2.0 * math.pi / sides)
    ring = (np.cos(angle)[None, :, None] * u[:, None, :]
            + np.sin(angle)[None, :, None] * v[:, None, :])
    bottom = a[:, None, :] + ring * _per_item(radius_start, count)[:, None, None]
    top = b[:, None, :] + ring * _per_item(radius_end, count)[:, None, None]
    j = np.arange(sides)
    following = (j + 1) % sides
    quad = np.stack([j, following, following + sides, j, following + sides, j + sides],
                    axis=1).reshape(-1)
    index = quad[None, :] + (np.arange(count) * 2 * sides)[:, None]
    return Mesh.from_arrays(
        np.concatenate([bottom, top], axis=1).reshape(-1, 3),
        np.concatenate([ring, ring], axis=1).reshape(-1, 3),
        np.repeat(_rows(color, count, 4), 2 * sides, axis=0),
        np.zeros((count * 2 * sides, 2)), index.reshape(-1),
    )
