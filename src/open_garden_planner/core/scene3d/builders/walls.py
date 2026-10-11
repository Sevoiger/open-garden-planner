"""WALL: a garden wall with a coping, swept along the centre line (Phase 17 L1.6).

Built from ``record.path`` — the polyline's centre line — never from the stroke
polygon in ``record.footprints``: a wall round a garden is a closed loop whose
stroke polygon is an outer ring plus a hole, and extruding it would fill the
garden with stone. A closed centre line is swept as a loop (mitred all round, no
ends); an open one gets two flat ends.

One ``tinted`` part: in 2D a wall is seen in its stroke colour (every polyline
style paints from the item's pen), so that is its colour here.

The plan holds no thickness for a wall — its stroke width is a line weight
(4–5 cm). The body is ``max(stroke, 30)`` cm (the L0 board's wall), the coping
4 cm wider on each side.
"""

from __future__ import annotations

from typing import Final

import numpy as np

from ..build import BuilderRegistry
from ..mesh import Mesh, MeshPart
from ..record import PARAM_PATH_STYLE, Record
from .common import BuiltBuilder
from .palette import TINT
from .primitives import F64, MIN_SEGMENT_CM, clean_path, sweep

#: Body thickness when the stroke is thinner (cm).
WALL_THICKNESS_CM: Final = 30.0
#: How far the coping oversails the body on each side (cm).
COPING_OVERSAIL_CM: Final = 4.0
#: The coping is this tall at most (cm), and at most this share of the wall.
COPING_HEIGHT_CM: Final = 8.0
COPING_SHARE: Final = 0.2


def centre_line(record: Record) -> tuple[F64, bool]:
    """The polyline's centre line without repeated points, and whether it is a
    closed loop (its last point is its first — then the last point is dropped)."""
    line = clean_path(record.path) if len(record.path) >= 2 else np.zeros((0, 2))
    closed = len(line) > 3 and float(np.linalg.norm(line[0] - line[-1])) <= MIN_SEGMENT_CM
    return (line[:-1] if closed else line), closed


def body_thickness_cm(record: Record) -> float:
    """The wall body's thickness: the drawn stroke, but at least a wall's."""
    return max(record.path_width_cm or 0.0, WALL_THICKNESS_CM)


def wall_mesh(line: F64, closed: bool, thickness_cm: float, height_cm: float) -> Mesh:
    """Body plus coping along ``line``; the coping's top is exactly ``height_cm``."""
    half = thickness_cm / 2.0
    cap_half = half + COPING_OVERSAIL_CM
    coping = min(COPING_HEIGHT_CM, COPING_SHARE * height_cm)
    chamfer = min(2.0, coping / 3.0)
    under = height_cm - coping
    profile = [
        (-half, 0.0), (half, 0.0), (half, under), (cap_half, under), (cap_half, height_cm - chamfer),
        (cap_half - chamfer, height_cm), (-cap_half + chamfer, height_cm),
        (-cap_half, height_cm - chamfer), (-cap_half, under), (-half, under),
    ]
    on_the_ground = [False] + [True] * (len(profile) - 1)
    return sweep(profile, line, TINT, closed=closed, edges=on_the_ground)


def _wall(record: Record, height_cm: float) -> tuple[MeshPart, ...]:
    from .fences import fence_parts, is_fence_style  # a fence style on a WALL builds that fence

    style = record.params.get(PARAM_PATH_STYLE)
    if is_fence_style(style) and style != "STONE_WALL":
        return fence_parts(record, height_cm)
    line, closed = centre_line(record)
    mesh = wall_mesh(line, closed, body_thickness_cm(record), height_cm)
    return (MeshPart(mesh, "vc", tinted=True, name="wall"),)


build_wall: Final = BuiltBuilder(_wall, "wall")


def triangle_budget(record: Record) -> int:
    """The most triangles a wall may have: 22 per path segment + 20."""
    return 22 * max(len(record.path) - 1, 1) + 20


def register(registry: BuilderRegistry) -> None:
    registry.register("WALL", build_wall)
