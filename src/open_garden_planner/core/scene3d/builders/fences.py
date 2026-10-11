"""FENCE, per ``PathFenceStyle`` (Phase 17 L1.6).

A polyline's style (``params["path_style"]``, a ``PathFenceStyle`` name; absent
for NONE) picks the fence:

=================  =========================================================
``WOODEN_FENCE``   posts, two rails, pickets — also the look of a FENCE with
                   no style, and of one carrying a PATH style (a fence is a
                   fence whatever its line is drawn like)
``METAL_FENCE``    slim posts, two rails, square bars with spear tips
``CHAIN_LINK``     posts, a top rail, a diamond mesh of wires
``HEDGE_FENCE``    a hedge block along the line (``hedges.py``)
``STONE_WALL``     the wall of ``walls.py``
=================  =========================================================

Everything is built from ``record.path`` — the centre line — segment by
segment, never from the stroke polygon in ``record.footprints``: a fence round a
garden is a closed loop, and its stroke polygon is a ring with a hole.

Colour: in 2D every style paints from the item's own stroke colour (the style
table only pre-sets it), so every part is ``tinted`` — pure white vertices the
sink multiplies with the record's material. A recolour never rebuilds a fence.

The plan holds no cross-section for a fence — its stroke is a line weight
(2–3 cm). Posts, rails and pickets have the L0 board's sizes; a stroke drawn
wider than a post widens the posts. The POSTS are the top: exactly the height.
"""

from __future__ import annotations

from typing import Final

import numpy as np

from ..build import BuilderRegistry
from ..mesh import Mesh, MeshPart
from ..record import PARAM_PATH_STYLE, ParamValue, Record
from .common import BuiltBuilder
from .hedges import (
    CORE_DROP_CM,
    CORE_INSET_CM,
    MAX_LEAVES,
    hedge_parts,
    line_shell,
    seed_of,
)
from .palette import TINT
from .primitives import F64, bevelled_boxes, boxes, sweep, tubes
from .walls import body_thickness_cm, centre_line, wall_mesh
from .walls import triangle_budget as wall_triangle_budget

#: The ``PathFenceStyle`` names that are fences (the others are paths: ground).
FENCE_STYLES: Final = frozenset(
    {"WOODEN_FENCE", "METAL_FENCE", "CHAIN_LINK", "HEDGE_FENCE", "STONE_WALL"}
)
#: What a FENCE without a fence style is built as.
DEFAULT_FENCE_STYLE: Final = "WOODEN_FENCE"

# picket fence (the spike's ``polyline_posts_and_pickets``)
POST_CM: Final = 9.0
POST_SPACING_CM: Final = 200.0
POST_BEVEL_CM: Final = 1.0
RAIL_ACROSS_CM: Final = 4.0
RAIL_TALL_CM: Final = 6.0
PICKET_PITCH_CM: Final = 12.0
PICKET_WIDE_CM: Final = PICKET_PITCH_CM * 0.62
PICKET_THICK_CM: Final = 2.2
# metal fence
METAL_POST_CM: Final = 4.0
METAL_RAIL_CM: Final = 2.5
METAL_BAR_CM: Final = 1.6
METAL_BAR_PITCH_CM: Final = 11.0
# chain link
CHAIN_POST_CM: Final = 5.0
CHAIN_POST_SPACING_CM: Final = 250.0
CHAIN_RAIL_CM: Final = 3.0
CHAIN_WIRE_RADIUS_CM: Final = 0.4
CHAIN_WIRE_PITCH_CM: Final = 25.0
# hedge fence
HEDGE_THICKNESS_CM: Final = 40.0


def is_fence_style(style: ParamValue | None) -> bool:
    return isinstance(style, str) and style in FENCE_STYLES


class _Run:
    """A centre line as per-segment arrays (starts, unit directions, lengths, angles)."""

    def __init__(self, line: F64, closed: bool) -> None:
        self.closed = closed
        self.start = line if closed else line[:-1]
        end = np.roll(line, -1, axis=0) if closed else line[1:]
        step = end - self.start
        self.length = np.linalg.norm(step, axis=1)
        self.along = step / np.maximum(self.length, 1e-12)[:, None]
        self.angle = np.degrees(np.arctan2(self.along[:, 1], self.along[:, 0]))
        self.count = len(self.start)

    def at(self, segment: F64, distance: F64) -> F64:
        """Plan points ``distance`` cm along the given segments."""
        index = segment.astype(np.int64)
        points: F64 = self.start[index] + self.along[index] * distance[:, None]
        return points

    def spread(self, counts: F64) -> tuple[F64, F64]:
        """For ``counts[k]`` things on segment k: (segment index, running index) per thing."""
        number = counts.astype(np.int64)
        segment = np.repeat(np.arange(self.count), number)
        first = np.repeat(np.cumsum(number) - number, number)
        return segment.astype(np.float64), (np.arange(int(number.sum())) - first).astype(np.float64)

    def posts(self, spacing: float) -> tuple[F64, F64]:
        """Post centres and their segments' angles: one at every corner and every
        ``spacing`` cm at most, a shared corner only once."""
        per_segment = np.maximum(2, np.ceil(self.length / spacing) + 1)
        segment, index = self.spread(per_segment)
        seg = segment.astype(np.int64)
        distance = self.length[seg] * index / (per_segment[seg] - 1)
        first_of_line = (seg == 0) & (not self.closed)
        keep = (index > 0) | first_of_line
        return self.at(segment[keep], distance[keep]), self.angle[seg[keep]]

    def every(self, pitch: float) -> tuple[F64, F64, F64]:
        """Things centred every ``pitch`` cm along each segment: (centres, angles, index)."""
        segment, index = self.spread(np.floor(self.length / pitch))
        seg = segment.astype(np.int64)
        return self.at(segment, (index + 0.5) * pitch), self.angle[seg], index

    def mid(self) -> F64:
        centres: F64 = self.start + self.along * (self.length / 2.0)[:, None]
        return centres


def _scale(height_cm: float) -> float:
    """Details shrink with a fence lower than 60 cm (a knee rail), never grow."""
    return min(1.0, height_cm / 60.0)


def picket_fence(run: _Run, height_cm: float, post_cm: float) -> Mesh:
    k = _scale(height_cm)
    post_at, post_angle = run.posts(POST_SPACING_CM)
    posts = bevelled_boxes(post_at, (post_cm, post_cm), 0.0, height_cm, POST_BEVEL_CM * k,
                           post_angle, TINT)
    rail_size = np.stack([run.length, np.full(run.count, RAIL_ACROSS_CM)], axis=1)
    rails = [boxes(run.mid(), rail_size, share * height_cm,
                   min(share * height_cm + RAIL_TALL_CM, (share + 0.2) * height_cm), run.angle, TINT)
             for share in (0.25, 0.75)]
    at, angle, index = run.every(PICKET_PITCH_CM)
    jitter = (index * 7919 % 13) / 13.0 * 4.0  # 0 … 3.7 cm: no two neighbours level
    pickets = boxes(at, (PICKET_WIDE_CM, PICKET_THICK_CM), 2.0 * k, height_cm - (4.0 - jitter) * k,
                    angle, TINT)
    return Mesh.merge([posts, *rails, pickets])


def metal_fence(run: _Run, height_cm: float, post_cm: float) -> Mesh:
    k = _scale(height_cm)
    post_at, post_angle = run.posts(POST_SPACING_CM)
    posts = boxes(post_at, (post_cm, post_cm), 0.0, height_cm, post_angle, TINT)
    rail_size = np.stack([run.length, np.full(run.count, METAL_RAIL_CM)], axis=1)
    low, high = 10.0 * k, height_cm - 12.0 * k
    rails = [boxes(run.mid(), rail_size, z, z + METAL_RAIL_CM * k, run.angle, TINT)
             for z in (low, high - METAL_RAIL_CM * k)]
    at, angle, _index = run.every(METAL_BAR_PITCH_CM)
    bars = boxes(at, (METAL_BAR_CM, METAL_BAR_CM), low, height_cm - 2.0 * k, angle, TINT)
    return Mesh.merge([posts, *rails, bars])


def chain_link_fence(run: _Run, height_cm: float, post_cm: float) -> Mesh:
    k = _scale(height_cm)
    post_at, post_angle = run.posts(CHAIN_POST_SPACING_CM)
    posts = boxes(post_at, (post_cm, post_cm), 0.0, height_cm, post_angle, TINT)
    rail_size = np.stack([run.length, np.full(run.count, CHAIN_RAIL_CM)], axis=1)
    bottom, top = 3.0 * k, height_cm - 5.0 * k
    rail = boxes(run.mid(), rail_size, top, top + CHAIN_RAIL_CM * k, run.angle, TINT)
    # two families of 45° wires across each panel, clipped to it
    rise = top - bottom
    segment, index = run.spread(np.ceil((run.length + rise) / CHAIN_WIRE_PITCH_CM))
    seg = segment.astype(np.int64)
    origin = index * CHAIN_WIRE_PITCH_CM - rise  # where the wire meets its starting edge
    near = np.maximum(origin, 0.0)
    far = np.minimum(origin + rise, run.length[seg])
    keep = far - near > 0.5
    segment, near, far, origin = segment[keep], near[keep], far[keep], origin[keep]
    wires = []
    for z_near, z_far in ((bottom + (near - origin), bottom + (far - origin)),   # rising
                          (top - (near - origin), top - (far - origin))):        # falling
        a = np.concatenate([run.at(segment, near), z_near[:, None]], axis=1)
        b = np.concatenate([run.at(segment, far), z_far[:, None]], axis=1)
        wires.append(tubes(a, b, CHAIN_WIRE_RADIUS_CM * k, CHAIN_WIRE_RADIUS_CM * k, TINT, sides=3))
    return Mesh.merge([posts, rail, *wires])


def hedge_fence_parts(record: Record, line: F64, closed: bool,
                      height_cm: float) -> tuple[MeshPart, ...]:
    half = max(record.path_width_cm or 0.0, HEDGE_THICKNESS_CM) / 2.0 - CORE_INSET_CM
    core_top = max(height_cm - CORE_DROP_CM, 0.5 * height_cm)
    profile = [(-half, 0.0), (half, 0.0), (half, core_top), (-half, core_top)]
    core = sweep(profile, line, TINT, closed=closed, edges=[False, True, True, True])
    shell = line_shell(line, closed, half, height_cm)
    return hedge_parts(core, shell, height_cm, seed_of(record), tinted=True)


def fence_parts(record: Record, height_cm: float) -> tuple[MeshPart, ...]:
    """The parts of a polyline built as a fence in its style (module docstring)."""
    line, closed = centre_line(record)
    if len(line) < 2:
        return ()
    style = record.params.get(PARAM_PATH_STYLE)
    if not is_fence_style(style):
        style = DEFAULT_FENCE_STYLE
    stroke = record.path_width_cm or 0.0
    if style == "STONE_WALL":
        mesh = wall_mesh(line, closed, body_thickness_cm(record), height_cm)
        return (MeshPart(mesh, "vc", tinted=True, name="wall"),)
    if style == "HEDGE_FENCE":
        return hedge_fence_parts(record, line, closed, height_cm)
    run = _Run(line, closed)
    if style == "METAL_FENCE":
        mesh = metal_fence(run, height_cm, max(METAL_POST_CM, stroke))
    elif style == "CHAIN_LINK":
        mesh = chain_link_fence(run, height_cm, max(CHAIN_POST_CM, stroke))
    else:
        mesh = picket_fence(run, height_cm, max(POST_CM, stroke))
    return (MeshPart(mesh, "vc", tinted=True, name="fence"),)


build_fence: Final = BuiltBuilder(fence_parts, "fence")


def path_length_cm(record: Record) -> float:
    line, closed = centre_line(record)
    if len(line) < 2:
        return 0.0
    return float(_Run(line, closed).length.sum())


def triangle_budget(record: Record) -> int:
    """The most triangles a fence may have, by style — per METRE of centre line
    and per segment, so 50 m of fence cannot explode:

    picket 105 / m + 60 / segment · metal 100 / m + 35 / segment ·
    chain link 55 / m + 110 / segment · each + 60. A stone wall and a hedge fence
    have their own budgets (``walls``, ``hedges``).
    """
    style = record.params.get(PARAM_PATH_STYLE)
    if style == "STONE_WALL":
        return wall_triangle_budget(record)
    if style == "HEDGE_FENCE":
        return 2 * MAX_LEAVES + 8 * max(len(record.path), 2) + 40
    metres = path_length_cm(record) / 100.0
    segments = max(len(record.path) - 1, 1)
    per_metre, per_segment = {"METAL_FENCE": (100, 35), "CHAIN_LINK": (55, 110)}.get(
        style if isinstance(style, str) else "", (105, 60))
    return int(per_metre * metres) + per_segment * segments + 60


def register(registry: BuilderRegistry) -> None:
    registry.register("FENCE", build_fence)
