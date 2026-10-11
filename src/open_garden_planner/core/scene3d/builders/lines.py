"""FENCE and WALL: from a polyline's kind and style to its solid (Phase 17 L1.6).

The ONE place that decides what a line item is built as. The geometry lives in
``fences.py`` and ``walls.py``; neither knows about the other or about kinds.

A polyline's style is ``params["path_style"]``: a ``PathFenceStyle`` name, absent
for the plain line (``NONE``).

==========  ================================  ================================
kind        style                             built as
==========  ================================  ================================
``FENCE``   none                              the default fence: posts, rails,
                                              pickets (``WOODEN_FENCE``)
``WALL``    none                              the wall (``STONE_WALL``)
either      one of the five fence styles      that style, whatever the kind —
                                              the style is what 2D draws
either      anything else: a path style, a    a PLAIN panel — the stroke as
            name no builder knows             wide as drawn, up to the height
==========  ================================  ================================

The last row is the honest fallback. A style this module has no solid for —
a path style on a fence, or a fence style added to ``PATH_FENCE_STYLES`` after
this was written — is NOT quietly given some other style's look (it used to
become a picket fence). It gets the one body the plan itself describes: its
line, its stroke width, its height — the same solid the 2D shadow model casts.
A new fence style is caught before that, at development time: the mirror
``fences.FENCE_STYLES`` is pinned against the 2D table by a test.
"""

from __future__ import annotations

from typing import Final

from ..build import BuilderRegistry
from ..mesh import MeshPart
from ..record import PARAM_PATH_STYLE, Record
from . import fences, walls
from .common import BuiltBuilder, centre_line

#: kind -> what a line of that kind is built as when it carries no style
DEFAULT_SOLID: Final = {"FENCE": fences.DEFAULT_FENCE_STYLE, "WALL": "STONE_WALL"}


def solid_of(record: Record) -> str:
    """What the line is built as: one of the five fence styles, or ``fences.PLAIN``
    (the table in the module docstring)."""
    style = record.params.get(PARAM_PATH_STYLE)
    if style is None:
        return DEFAULT_SOLID.get(record.kind, fences.PLAIN)
    if fences.is_fence_style(style):
        return str(style)
    return fences.PLAIN


def line_parts(record: Record, height_cm: float) -> tuple[MeshPart, ...]:
    """The parts of a FENCE or WALL record, built from its centre line."""
    line, closed = centre_line(record)
    if len(line) < 2:
        return ()
    solid = solid_of(record)
    if solid == "STONE_WALL":
        return walls.wall_parts(record, line, closed, height_cm)
    return fences.fence_parts(record, line, closed, height_cm, solid)


build_fence: Final = BuiltBuilder(line_parts, "fence")
build_wall: Final = BuiltBuilder(line_parts, "wall")


def triangle_budget(record: Record) -> int:
    """The most triangles a FENCE or WALL record may have, by what it is built as
    (``walls.triangle_budget``, ``fences.triangle_budget``)."""
    solid = solid_of(record)
    if solid == "STONE_WALL":
        return walls.triangle_budget(record)
    line, closed = centre_line(record)
    return fences.triangle_budget(line, closed, max(len(record.path) - 1, 1), solid)


def register(registry: BuilderRegistry) -> None:
    registry.register("FENCE", build_fence)
    registry.register("WALL", build_wall)
