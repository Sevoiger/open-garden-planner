"""The builder seam: one record in, mesh parts out (Phase 17 L1.1, ADR-054).

A ``Builder`` is a PURE function of one ``Record``::

    Builder = Callable[[Record], tuple[MeshPart, ...]]

It returns meshes in the item-local frame (``mesh`` module) and may read ONLY
the record's geometry part — ``kind``, ``shape``, ``footprints``, ``holes``,
``path``, ``path_width_cm``, ``height_cm``, ``params``
(``record.GEOMETRY_FIELDS``). Not the transform (the engine poses the Model),
not the material (the sink tints), not ``item_id`` / ``name`` / ``parent_id``
(take the seed from ``params["seed"]``). That rule is what makes the three
signatures honest: the pipeline calls a builder only when ``geometry_sig``
changed, so anything else a builder read would go stale. ``verify_builder``
enforces it in tests.

``BuilderRegistry`` maps ``Record.kind`` to a builder; L1.6 (built world) and
L1.7 (plants) register theirs. Every other kind gets ``default_builder`` — the
honest v0: a prism of the footprint (outer rings minus their holes) up to the
resolved height, and nothing at all for an item the plan gives no height (the
ground bake, L1.5, paints flat surfaces).
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
from collections.abc import Callable, Iterable, Sequence
from typing import Final, TypeAlias

import numpy as np
from numpy.typing import NDArray

from .mesh import Mesh, MeshError, MeshPart
from .polygon import (
    Region,
    ccw,
    cw,
    is_flat_ring,
    is_simple_ring,
    ring_area,
    triangulate_region,
)
from .record import Material, Record, Ring, Transform

Builder: TypeAlias = Callable[[Record], tuple[MeshPart, ...]]

#: Truth gate "built heights" (``ogp-lush-cinematic`` §1): the top of a
#: shadow-casting item is its resolved height to this fraction. Plants are held
#: to 0.03 — L1.7 passes it to ``verify_builder``.
BUILT_HEIGHT_TOLERANCE: Final = 0.01
# ``verify_builder``'s plan-view check bounds EXCESS only — geometry where the
# plan has none. It never asks a mesh to cover its footprint: a flower, a grass
# tuft, the posts and pickets of a fence fill a fraction of theirs and are right.
#: Its default reach: a top surface may lie this far outside the footprint
#: (eaves, a post wider than its fence line, a canopy a few percent over its
#: circle). Pass 0.0 for a builder that must stay inside, None to switch it off.
PLAN_MARGIN_CM: Final = 50.0
#: The share of the footprint's area that may lie beyond that reach.
PLAN_AREA_TOLERANCE: Final = 0.01
#: A hole of the footprint may be covered from above by at most this share:
#: eaves over a courtyard cover its rim, a builder that extruded the hole ring as
#: a solid covers all of it.
HOLE_COVER_LIMIT: Final = 0.5


class BuilderContractError(Exception):
    """A builder broke the seam's contract (``verify_builder``)."""


def item_seed(item_id: str, salt: str = "") -> int:
    """Stable per-item seed, so no two plants are clones and none changes between
    runs. Ported from the spike (``meshes.item_seed``); feed it ``params["seed"]``."""
    digest = hashlib.md5(f"{item_id}:{salt}".encode(), usedforsecurity=False)
    return int(digest.hexdigest()[:12], 16)


def _walls(ring: Ring, height_cm: float, positions: list[float], normals: list[float]) -> None:
    """Two triangles per edge with the flat normal on the RIGHT of the direction
    of travel: outward for a counter-clockwise outer ring, into the hole — away
    from the solid — for a clockwise hole ring."""
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        edge = math.hypot(x2 - x1, y2 - y1)
        if edge <= 1e-9:
            continue
        nx, ny = (y2 - y1) / edge, -(x2 - x1) / edge
        for px, py, pz in (
            (x1, y1, 0.0), (x2, y2, 0.0), (x2, y2, height_cm),
            (x1, y1, 0.0), (x2, y2, height_cm), (x1, y1, height_cm),
        ):
            positions.extend((px, py, pz))
            normals.extend((nx, ny, 0.0))


def prism_mesh(
    footprints: Sequence[Ring], height_cm: float, holes: Sequence[Sequence[Ring]] = ()
) -> Mesh:
    """Item-local prism(s) from z = 0 up to ``height_cm``: one solid per region.

    ``footprints`` are the outer rings; ``holes[i]`` (when given) the rings cut
    out of ``footprints[i]`` — pass ``record.footprints, height, record.holes``.
    Each region gets walls around its outer ring, walls around every hole facing
    into the hole, and a cap that covers the outer MINUS the holes. Neutral white
    vertex colours (a tinted part), flat normals, no bottom cap.

    A hole-free ring produces exactly the vertices of the Qt 3D view's
    ``extrude_footprint`` (same ear clipper, ``polygon.triangulate_polygon``), so
    the two views cannot disagree. A flat ring (fewer than three corners, or all
    of them on one line) and a non-positive height give no geometry. A ring that
    is not a simple polygon (a bow-tie) raises ``ValueError``: no cap of it would
    be the plan's shape, and an honest "no solid" beats a wrong one.
    """
    if holes and len(holes) != len(footprints):
        raise ValueError(f"{len(holes)} hole groups for {len(footprints)} footprint rings")
    if height_cm <= 0:
        return Mesh.empty()
    positions: list[float] = []
    normals: list[float] = []
    for index, raw in enumerate(footprints):
        if is_flat_ring(tuple(raw)):
            continue  # fewer than three corners, or all on one line: no ground covered
        outer = ccw(tuple(raw))
        inner = tuple(cw(tuple(hole)) for hole in (holes[index] if holes else ()))
        for ring in (outer, *inner):
            if not is_simple_ring(ring):
                raise ValueError(
                    f"a footprint ring of {len(ring)} vertices is not a simple polygon "
                    "(it crosses or touches itself)"
                )
        vertices, triangles = triangulate_region(outer, inner)
        doubled = [
            (vertices[b][0] - vertices[a][0]) * (vertices[c][1] - vertices[a][1])
            - (vertices[b][1] - vertices[a][1]) * (vertices[c][0] - vertices[a][0])
            for a, b, c in triangles
        ]
        expected = ring_area(outer) + sum(ring_area(hole) for hole in inner)
        slack = 1e-9 * max(1.0, abs(expected))
        # every cap triangle faces up and together they cover the region exactly —
        # the two facts a wrong triangulation cannot both satisfy
        if (doubled and min(doubled) < -slack) or abs(sum(doubled) / 2.0 - expected) > slack:
            raise ValueError(
                f"the cap of a footprint ring covers {sum(doubled) / 2.0!r} cm² "
                f"(smallest triangle {min(doubled, default=0.0) / 2.0!r}); its region is {expected!r} cm²"
            )
        _walls(outer, height_cm, positions, normals)
        for hole in inner:
            _walls(hole, height_cm, positions, normals)
        for triangle in triangles:
            for vertex in triangle:
                x, y = vertices[vertex]
                positions.extend((x, y, height_cm))
                normals.extend((0.0, 0.0, 1.0))
    if not positions:
        return Mesh.empty()
    count = len(positions) // 3
    return Mesh.from_arrays(
        positions,
        normals,
        np.ones((count, 4), np.float32),
        np.zeros((count, 2), np.float32),
        np.arange(count, dtype=np.uint32),
    )


def default_builder(record: Record) -> tuple[MeshPart, ...]:
    """The honest v0 for a kind without a builder of its own.

    With a height: ONE part — a prism of the local footprint, holes cut out,
    whose top is exactly ``height_cm``, neutral and ``tinted`` (the sink colours
    it from the item's material, so a recolour is a material update). Without a
    height: no parts — the item is decoration, casts no shadow in 2D and has no
    solid in 3D. Raises ``ValueError`` for a ring that is not a simple polygon
    (``prism_mesh``); ``SceneSync`` then shows the item without a solid and
    reports it.
    """
    if record.height_cm is None:
        return ()
    mesh = prism_mesh(record.footprints, record.height_cm, record.holes)
    if not mesh.vertex_count:
        return ()
    return (MeshPart(mesh, "vc", casts_shadow=True, tinted=True, name="prism"),)


class BuilderRegistry:
    """``Record.kind`` → builder, with a default for every other kind.

    An instance, not a module global: the wiring (L1.3) creates one and hands it
    to each builder package's ``register(registry)`` — no import-order surprises.
    A ``SceneSync`` notices a builder registered or replaced after it has built:
    its next ``apply`` rebuilds the items that builder now owns.
    """

    def __init__(self, default: Builder = default_builder) -> None:
        self._default = default
        self._builders: dict[str, Builder] = {}

    def register(self, kind: str, builder: Builder, *, replace: bool = False) -> None:
        """Use ``builder`` for records of ``kind`` (an ObjectType name).

        Registering a kind twice is an error — two packages claiming one kind is
        a bug, not a preference — unless ``replace=True`` says it is meant.
        """
        if kind in self._builders and not replace:
            raise ValueError(f"a builder for kind {kind!r} is already registered")
        self._builders[kind] = builder

    def register_many(self, kinds: Iterable[str], builder: Builder, *,
                      replace: bool = False) -> None:
        for kind in kinds:
            self.register(kind, builder, replace=replace)

    def builder_for(self, kind: str) -> Builder:
        return self._builders.get(kind, self._default)

    def kinds(self) -> frozenset[str]:
        """The kinds with a builder of their own."""
        return frozenset(self._builders)

    def build(self, record: Record) -> tuple[MeshPart, ...]:
        """Run the builder of ``record.kind``. Exceptions propagate — ``SceneSync``
        is where a failing builder is contained."""
        return self.builder_for(record.kind)(record)


def checked_parts(parts: object) -> tuple[MeshPart, ...]:
    """``parts`` if it is a tuple of ``MeshPart``, else ``BuilderContractError``."""
    if not isinstance(parts, tuple) or not all(isinstance(p, MeshPart) for p in parts):
        raise BuilderContractError(
            f"a builder returns a tuple of MeshPart, got {type(parts).__name__}: {parts!r:.80}"
        )
    return parts


def _top_cm(parts: Sequence[MeshPart]) -> float | None:
    tops = [float(p.mesh.positions[:, 2].max()) for p in parts if p.mesh.vertex_count]
    return max(tops) if tops else None


# ── the plan-view check ───────────────────────────────────────────────────────

Triangles2D: TypeAlias = tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]


def _top_triangles(parts: Sequence[MeshPart]) -> tuple[Triangles2D, NDArray[np.float64]]:
    """The plan view of every upward-facing triangle: its corners (t, 2) each, and
    its plan area. A triangle faces up when it is wound counter-clockwise seen
    from above."""
    corners: list[list[NDArray[np.float64]]] = [[], [], []]
    areas: list[NDArray[np.float64]] = []
    for part in parts:
        mesh = part.mesh
        if not len(mesh.indices):
            continue
        p = mesh.positions.astype(np.float64)
        a, b, c = (p[mesh.indices[:, k]][:, :2] for k in range(3))
        twice = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
        up = twice > 1e-9
        for store, corner in zip(corners, (a, b, c), strict=True):
            store.append(corner[up])
        areas.append(twice[up] / 2.0)
    if not areas:
        empty = np.zeros((0, 2))
        return (empty, empty, empty), np.zeros(0)
    return ((np.concatenate(corners[0]), np.concatenate(corners[1]), np.concatenate(corners[2])),
            np.concatenate(areas))


def _inside(points: NDArray[np.float64], ring: Ring) -> NDArray[np.bool_]:
    """Even-odd point-in-ring for many points at once."""
    x, y = points[:, 0], points[:, 1]
    inside = np.zeros(len(points), dtype=bool)
    xj, yj = ring[-1]
    for xi, yi in ring:
        if yi != yj:
            crosses = ((yi > y) != (yj > y)) & (x < (xj - xi) * (y - yi) / (yj - yi) + xi)
            inside ^= crosses
        xj, yj = xi, yi
    return inside


def _distance_to_rings(points: NDArray[np.float64], rings: Iterable[Ring]) -> NDArray[np.float64]:
    """Distance from each point to the nearest edge of any ring."""
    best = np.full(len(points), np.inf)
    for ring in rings:
        ax, ay = ring[-1]
        for bx, by in ring:
            dx, dy = bx - ax, by - ay
            length2 = dx * dx + dy * dy
            if length2 > 0.0:
                t = np.clip(((points[:, 0] - ax) * dx + (points[:, 1] - ay) * dy) / length2, 0.0, 1.0)
                best = np.minimum(best, np.hypot(points[:, 0] - (ax + t * dx),
                                                 points[:, 1] - (ay + t * dy)))
            ax, ay = bx, by
    return best


def _hole_cover(triangles: Triangles2D, hole: Ring) -> float:
    """The share of a hole that upward-facing triangles cover, from a 24 x 24
    lattice of points INSIDE the hole — so a slab of two huge triangles over a
    small hole is seen as surely as a cap of many small ones."""
    xs = [x for x, _y in hole]
    ys = [y for _x, y in hole]
    steps = (np.arange(24) + 0.5) / 24.0
    gx, gy = np.meshgrid(min(xs) + steps * (max(xs) - min(xs)), min(ys) + steps * (max(ys) - min(ys)))
    lattice = np.column_stack([gx.ravel(), gy.ravel()])
    lattice = lattice[_inside(lattice, hole)]
    a, b, c = triangles
    if not len(lattice) or not len(a):
        return 0.0
    near = ((np.maximum(np.maximum(a[:, 0], b[:, 0]), c[:, 0]) >= min(xs))
            & (np.minimum(np.minimum(a[:, 0], b[:, 0]), c[:, 0]) <= max(xs))
            & (np.maximum(np.maximum(a[:, 1], b[:, 1]), c[:, 1]) >= min(ys))
            & (np.minimum(np.minimum(a[:, 1], b[:, 1]), c[:, 1]) <= max(ys)))
    a, b, c = a[near], b[near], c[near]
    covered = np.zeros(len(lattice), dtype=bool)
    px, py = lattice[:, 0][:, None], lattice[:, 1][:, None]
    for start in range(0, len(a), 2048):  # points x triangles, a slab of triangles at a time
        ta, tb, tc = (corner[start:start + 2048] for corner in (a, b, c))
        d1 = (tb[:, 0] - ta[:, 0]) * (py - ta[:, 1]) - (tb[:, 1] - ta[:, 1]) * (px - ta[:, 0])
        d2 = (tc[:, 0] - tb[:, 0]) * (py - tb[:, 1]) - (tc[:, 1] - tb[:, 1]) * (px - tb[:, 0])
        d3 = (ta[:, 0] - tc[:, 0]) * (py - tc[:, 1]) - (ta[:, 1] - tc[:, 1]) * (px - tc[:, 0])
        covered |= ((d1 >= 0) & (d2 >= 0) & (d3 >= 0)).any(axis=1)
    return float(covered.mean())


def plan_overreach(
    parts: Sequence[MeshPart], regions: Sequence[Region], margin_cm: float
) -> tuple[float, float]:
    """How far a solid's top strays from its footprint, seen from above.

    Returns ``(beyond_cm2, hole_cover)``:

    - ``beyond_cm2`` — upward-facing area lying more than ``margin_cm`` outside
      the footprint (outer rings minus holes). Estimated at four points per
      triangle (its centroid and the points halfway to each corner), overlapping
      layers counted each time: a net for gross errors, not a rasteriser.
    - ``hole_cover`` — the largest share, 0 … 1, of any hole that upward-facing
      triangles cover, measured on a lattice of points inside the hole (0.0
      without holes).
    """
    triangles, areas = _top_triangles(parts)
    a, b, c = triangles
    if not len(areas) or not regions:
        return 0.0, 0.0
    hole_cover = max(
        (_hole_cover(triangles, hole) for _outer, holes in regions for hole in holes),
        default=0.0,
    )
    centre = (a + b + c) / 3.0
    points = np.concatenate([centre, (centre + a) / 2.0, (centre + b) / 2.0, (centre + c) / 2.0])
    weights = np.tile(areas / 4.0, 4)
    covered = np.zeros(len(points), dtype=bool)
    for outer, holes in regions:
        in_hole = np.zeros(len(points), dtype=bool)
        for hole in holes:
            in_hole |= _inside(points, hole)
        covered |= _inside(points, outer) & ~in_hole
    outside = ~covered
    if not outside.any():
        return 0.0, hole_cover
    rings = [ring for outer, holes in regions for ring in (outer, *holes)]
    far = _distance_to_rings(points[outside], rings) > margin_cm
    return float(weights[outside][far].sum()), hole_cover


def verify_builder(
    builder: Builder,
    record: Record,
    *,
    height_tolerance: float = BUILT_HEIGHT_TOLERANCE,
    height_tolerance_cm: float | None = None,
    plan_margin_cm: float | None = PLAN_MARGIN_CM,
    hole_cover_limit: float | None = HOLE_COVER_LIMIT,
) -> tuple[MeshPart, ...]:
    """Run ``builder`` on ``record`` and assert the seam's contract; return the parts.

    The test instrument for every builder (use it over each kind the builder
    handles and over the bench plans' records). Raises ``BuilderContractError``
    when the builder

    - does not return a tuple of ``MeshPart``;
    - is not deterministic (two calls differ);
    - reads the transform, the material or an unsigned field (``item_id``,
      ``name``, ``parent_id``): its output changed when only that changed;
    - emits a mesh ``MeshPart.validate`` rejects;
    - builds a shadow-casting item whose top is not its resolved height: within
      ``height_tolerance`` (a fraction of the height; truth gate "built
      heights"), or — when ``height_tolerance_cm`` is given — within that many
      centimetres instead (issue #385's L1.6 gate is ±0.5 cm absolute);
    - builds where the plan has NOTHING, seen from above — two upper bounds on
      excess, each with its own keyword:

      * ``plan_margin_cm`` (default ``PLAN_MARGIN_CM``): at most
        ``PLAN_AREA_TOLERANCE`` of the footprint's area (and at least 1 cm²)
        may be upward-facing surface lying further than this outside the
        footprint (the outer rings minus their holes). ``0.0`` holds a builder
        to its footprint, a larger value allows wider eaves, ``None`` switches
        this bound off.
      * ``hole_cover_limit`` (default ``HOLE_COVER_LIMIT``): at most this share
        of any HOLE of the footprint may be covered from above (a closed fence
        extruded with its inside filled covers all of it). ``None`` switches
        this bound off.

      Neither bound asks for coverage: a mesh that fills a fraction of its
      footprint — a flower, a tuft, pickets inside a fence's stroke — passes.
    """
    parts = checked_parts(builder(record))
    if checked_parts(builder(record)) != parts:
        raise BuilderContractError("the builder is not deterministic: two calls on one record differ")
    variants: dict[str, Record] = {
        "transform": dataclasses.replace(record, transform=Transform(
            record.transform.east_cm + 137.25, record.transform.north_cm - 42.5,
            (record.transform.rotation_deg + 33.0) % 360.0, record.transform.base_cm + 7.0)),
        "material": dataclasses.replace(record, material=Material(
            record.material.key + "*", fill_rgba=(1, 2, 3, 255), stroke_rgba=(4, 5, 6, 255),
            pattern="VERIFY")),
        "item_id": dataclasses.replace(record, item_id=record.item_id + "-verify-builder"),
        "name": dataclasses.replace(record, name=record.name + " (verify builder)"),
        "parent_id": dataclasses.replace(
            record, parent_id=(record.parent_id or "") + "verify-builder-parent"),
    }
    for field, variant in variants.items():
        if checked_parts(builder(variant)) != parts:
            raise BuilderContractError(
                f"the builder's output depends on the record's {field}; a builder may read only "
                "the geometry part (kind, shape, footprints, holes, path, path_width_cm, "
                "height_cm, params)"
            )
    for index, part in enumerate(parts):
        try:
            part.validate()
        except MeshError as error:
            raise BuilderContractError(f"part {index} ({part.name or part.material_kind}): {error}") from error
    top = _top_cm(parts)
    if record.height_cm is not None and top is not None:
        if height_tolerance_cm is not None:
            allowed, rule = height_tolerance_cm, f"±{height_tolerance_cm:g} cm"
        else:
            allowed, rule = height_tolerance * record.height_cm, f"{height_tolerance:.0%}"
        if abs(top - record.height_cm) > allowed + 1e-4:
            raise BuilderContractError(
                f"the mesh top is {top:g} cm but the plan's resolved height is "
                f"{record.height_cm:g} cm (tolerance {rule}): truth before beauty"
            )
    if (plan_margin_cm is not None or hole_cover_limit is not None) and parts and record.footprints:
        beyond, hole_cover = plan_overreach(parts, record.regions, plan_margin_cm or 0.0)
        plan_area = record.plan_area_cm2
        if hole_cover_limit is not None and hole_cover > hole_cover_limit:
            raise BuilderContractError(
                f"the solid covers {hole_cover:.0%} of a hole of the footprint: holes are not "
                "built over (iterate record.regions, or pass record.holes to prism_mesh)"
            )
        if plan_margin_cm is not None and beyond > max(PLAN_AREA_TOLERANCE * plan_area, 1.0):
            raise BuilderContractError(
                f"{beyond:,.0f} cm² of top surface lie more than {plan_margin_cm:g} cm outside a "
                f"footprint of {plan_area:,.0f} cm²: the solid stands where the plan has nothing"
            )
    return parts
