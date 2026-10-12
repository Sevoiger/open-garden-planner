"""The ground bake — every flat surface of the plan painted into ground tiles, as
the 2D canvas paints it (Phase 17 L1.5, #385, ADR-054 addendum "ground bake").

In 3D a lawn, a terrace, a driveway, a path or an in-ground bed is not geometry:
it is part of the ground's texture. ``GroundBaker`` paints that texture from the
snapshot's RECORDS — plain data — and never from the live scene: the L0 spike
baked by hiding items and rendering the scene, which toggles item visibility,
clears the selection and paints the canvas's beige background. Here no
``QGraphicsItem`` is touched, read or created.

What is painted
---------------
A record is painted when the snapshot gave it a paint description
(``Material.paint`` — every item but a plant) AND the scene holds no solid for
it (the ``solid`` ids ``SceneSync`` hands in). A lawn is on the ground; a house
is not, and what lies UNDER a house is whatever flat item was drawn there, or
meadow. Records are painted bottom to top in the snapshot's order — the
canvas's stacking order. Plan ground no item covers is meadow (``MEADOW_RGB``).

Not painted, by decision: selection, labels, the canvas's drop shadows, the
square-foot grid of a bed and the diagnostic borders and badges (crop rotation,
soil mismatch, capacity) — they are the 2D view's overlays, not the garden's
surface.

How it is painted — ONE implementation with the canvas wherever the canvas has
more than a primitive: the fill brush is ``core.fill_patterns.
create_pattern_brush``, a furniture sprite ``core.furniture_renderer.
render_furniture_pixmap``, a styled path ``ui.canvas.items.path_style_paint``.
The item's own ``paint()`` is mirrored only where it is one call
(``drawRect`` / ``drawEllipse`` / ``drawPolygon`` / ``drawPath``), and
``tests/integration/test_scene3d_ground_bake.py`` holds every pattern, shape and
style to the canvas's own render of the same items.

The paint frame. The canvas paints an item in the item's OWN coordinates: a
fill texture is anchored at the item's coordinate origin (not at its centre) and
a sprite is aligned to whole centimetres there. A record's geometry is centred
on its anchor, so ``paint_record`` shifts it back by the record's
``paint_origin`` param and poses the painter with the record's transform —
which reproduces the item's scene transform, and with it the texture's phase.
Known limit: a circle's record does not turn (scene contract), so the fill
texture of a ROTATED circle is painted unturned.

Tiles and dirty tiles
---------------------
The grid is ``core.scene3d.GroundGrid``: tiles of 512 px, each a crop of one
pixel grid over the plan, so neighbours meet without a seam by construction. A
tile is a pure function of the records painted into it, in order. ``bake``
looks again at exactly the tiles whose list can have changed — the tiles under
the old and the new paint bounds of every record that was added, removed,
changed, or gained or lost its solid, and after a re-order all of them — and
re-paints those whose list DID change, or holds a record that did. Every other
tile is handed out as the SAME object as before — which is how the sink knows
not to upload it.

What a bake may cost (a plan file is untrusted input, §8.11)
------------------------------------------------------------
THE CRITERION. The bake may cost what the plan file holds, times a stated
constant — as the 2D canvas does for the same plan. What it must not have is
AMPLIFICATION: a small file that buys superlinear or unbounded work or memory;
and no bound may sit BEHIND the work it bounds.

What it holds: one entry per painted record — the record (the snapshot's own
object), the block of tiles its paint bounds reach (four integers) and its
cost — and per tile the ids it was last painted with (at most
``TILE_RECORDS``). Not one entry per record AND tile: a record that covers the
whole plan is one entry.

What it does per ``bake``: one look at every record the diff names (all of
them, without a diff); ONE walk down the records, topmost first, that hands
each dirty tile its records until the tile is full — at most one comparison per
record and dirty tile, and none once every dirty tile is full; then the dirty
tiles are painted. The bounds of ONE tile, each taken before the work it
bounds:

=====================  =======  ================================================
bound                  a tile   what it stops, and where it is checked
=====================  =======  ================================================
``TILE_RECORDS``       256      records painted into a tile: the TOPMOST ones
                                (the walk above stops handing a full tile
                                records — nothing of a record left out is
                                touched)
``TILE_VERTICES``      262,144  outline vertices × passes of the records of a
                                tile (same place): what every painter does
                                BEFORE it draws — points converted, a path
                                built, a styled path's two offset lines and its
                                base stroke. A styled path counts 8 times, a
                                dashed stroke 3 (points, path, cut to the tile).
                                The topmost record is always painted: an outline
                                is at most the snapshot's ``MAX_ITEM_VERTICES``
                                (16,384), styled 131,072.
``TILE_ELEMENTS``      16,384   the ELEMENTS of path styles — stones, planks,
                                posts, dots; as many as a line is LONG, whatever
                                its vertex count. Generated only inside the tile
                                (the walk is clipped, phase kept), each charged
                                in ``path_style_paint`` before it is handed out.
``TILE_WALK_STEPS``    16,384   the steps of the exact walk that finds them (a
                                whole segment, inside the tile or not): charged
                                before the walk; beyond, arithmetic.
``TILE_DASHES``        32,768   the dashes of dashed pens: a line is cut to the
                                tile, and each run's dashes (its length over
                                three pen widths, a pen at least a pixel wide)
                                are charged before it is stroked — a run the
                                tile cannot afford is stroked solid.
``SPRITE_MAX_PIXELS``  262,144  the pixels a furniture sprite is rendered with
                                (its size is the PLAN's: a "fire pit" of 10 km
                                would be an image of 10^12 pixels) — a larger
                                one is rendered smaller and stretched into its
                                place. Checked before the renderer is asked.
=====================  =======  ================================================

One ``WalkBudget`` per tile carries the element, step and dash bounds through
every record of the tile; a spent one means "no more", never "no bound". A tile
that hit a bound is in ``capped`` (and counted, and logged once) — plain data
for whoever shows the user what the 3D view left out.

Bounded elsewhere: the vertices of one outline (the snapshot refuses an item
above ``MAX_ITEM_VERTICES`` before one vertex is read), the tiles and pixels of
a plan (``GroundGrid``: 256 tiles, 4096 × 4096 px), the fill-brush cache of
``core.fill_patterns`` (200 textures of 256 × 256 px, whatever the plan's
colours) and the sprite cache of ``core.furniture_renderer`` (200 sprites; one
made for the bake is at most ``SPRITE_MAX_PIXELS``, 1 MB). A fill, a solid
stroke or a sprite that reaches far beyond a tile costs the tile's pixels:
``QPainter`` clips to the image.

Memory: the tiles of the largest ground are 64 MB (``MAX_GROUND_PIXELS`` × 4
bytes), held ONCE on this side — the baker's tiles are the very objects the
sync and the sink hold; a bare tile's bytes are shared by all bare tiles of its
size (at most four sizes).

GUI thread only (``QPixmap`` brushes and sprites). ``QtGui`` only: this module
imports no Qt Quick module and nothing of the engine package.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass
from itertools import pairwise
from typing import Final

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF, QTransform

from open_garden_planner.core.fill_patterns import FillPattern, create_pattern_brush
from open_garden_planner.core.furniture_renderer import is_furniture_type, render_furniture_pixmap
from open_garden_planner.core.object_types import ObjectType, PathFenceStyle
from open_garden_planner.core.scene3d import (
    MAX_GROUND_PIXELS,
    MEADOW_RGB,
    PARAM_PAINT_ORIGIN,
    PARAM_PATH_STYLE,
    PARAM_RADIUS_CM,
    SHAPE_CIRCLE,
    SHAPE_ELLIPSE,
    SHAPE_POLYGON,
    SHAPE_POLYLINE,
    SHAPE_RECTANGLE,
    TILE_PX,
    GroundGrid,
    GroundTile,
    Material,
    Paint,
    Record,
    SceneDiff,
    TileKey,
)
from open_garden_planner.core.scene3d.frame import cos_sin
from open_garden_planner.core.scene3d.ground import check_grid_settings
from open_garden_planner.ui.canvas.items.path_style_paint import (
    PathStylePaint,
    WalkBudget,
    segment_inside,
)

__all__ = ["SPRITE_MAX_PIXELS", "TILE_DASHES", "TILE_ELEMENTS", "TILE_RECORDS", "TILE_VERTICES",
           "TILE_WALK_STEPS", "GroundBaker", "WalkBudget", "paint_bounds", "paint_record",
           "tile_budget"]

_log = logging.getLogger(__name__)

#: What a record without a paint description of its own is painted with (a
#: hand-made record in a test): Qt's default pen, opaque.
_PLAIN_PAINT: Final = Paint()
#: The image format a tile is painted in. Premultiplied, because that is what
#: ``QPainter`` blends fastest in (measured: a textured fill of a 512 px tile 0.16
#: ms against 0.22 ms); and as the ground is opaque everywhere its bytes ARE
#: plain RGBA8 — no conversion on the way out.
_TILE_FORMAT: Final = QImage.Format.Format_RGBA8888_Premultiplied
#: How far a record's paint can reach beyond its geometry, in cm, as a multiple of
#: its pen width plus a constant: a styled path draws bars, bumps and soft edges
#: to 1.25 widths beside its centre line; an outline's square caps and mitred
#: joins stay within one pen width. Generous on purpose — a record listed for a
#: tile it does not reach costs a clipped paint call, one that is NOT listed for a
#: tile it reaches is cut off at the tile's edge.
_PATH_REACH: Final = (1.5, 4.0)
_OUTLINE_REACH: Final = (1.0, 2.0)
#: … and the pixels an antialiased edge and a sprite's whole-centimetre alignment
#: can spill, whatever the resolution.
_SPILL_PX: Final = 2.0
#: How far outside a tile a style's elements are still generated, as a multiple of
#: the line's width plus a constant: an element's own reach (``_PATH_REACH``) and
#: one more element — the chain-link mesh is drawn from each element to the next.
_ELEMENT_REACH: Final = (2.5, 8.0)

#: The bounds of ONE tile (module docstring, the table). Measured on the
#: development box: a record costs 0.02–0.15 ms to paint into a tile (1.3 ms when
#: it fills the tile with a turned texture), an outline 1 µs a vertex and pass, a
#: style element 2–5 µs, a step of the exact walk 0.15 µs, a dash 3 µs — so a tile
#: at every bound costs about half a second and a full bake of the largest ground
#: (64 tiles) about half a minute; a plan that is not built to reach them is three
#: orders of magnitude below.
TILE_RECORDS: Final = 256
TILE_VERTICES: Final = 262_144
TILE_ELEMENTS: Final = 16_384
TILE_WALK_STEPS: Final = 16_384
TILE_DASHES: Final = 32_768
SPRITE_MAX_PIXELS: Final = 512 * 512
#: The smallest side ``core.furniture_renderer`` renders a sprite with.
_SPRITE_MIN_SIDE: Final = 4.0
#: What a record's vertices are multiplied with for the tile's vertex bound: the
#: passes its painter makes over them before it draws.
_STYLED_PASSES: Final = 8
_DASHED_PASSES: Final = 3
#: An ellipse whose dashed outline has to be cut to a tile is stroked along chords:
#: as many as keep them this close to the curve, in pixels — and no more than that
#: many, whatever its size (an ellipse of 10 km is then a pixel off, in a tile it
#: crosses as a nearly straight line).
_CHORD_TOLERANCE_PX: Final = 0.02
_ELLIPSE_MAX_CHORDS: Final = 2048


def tile_budget() -> WalkBudget:
    """A fresh budget of one tile: its elements, walk steps and dashes."""
    return WalkBudget(TILE_ELEMENTS, TILE_WALK_STEPS, TILE_DASHES)


def _object_type(kind: str) -> ObjectType | None:
    try:
        return ObjectType[kind]
    except KeyError:
        return None


def _brush(material: Material) -> QBrush:
    """The item's fill brush, made the way the canvas makes it."""
    fill = material.fill_rgba
    if fill is None:
        return QBrush(Qt.BrushStyle.NoBrush)
    color = QColor(*fill)
    try:
        pattern = FillPattern[material.pattern] if material.pattern is not None else None
    except KeyError:
        pattern = None
    return QBrush(color) if pattern is None else create_pattern_brush(pattern, color)


def _pen(material: Material, paint: Paint, width_cm: float) -> QPen:
    stroke = material.stroke_rgba
    if stroke is None or paint.stroke_style == "NoPen":
        return QPen(Qt.PenStyle.NoPen)
    pen = QPen(QColor(*stroke))
    pen.setWidthF(width_cm)
    pen.setStyle(getattr(Qt.PenStyle, paint.stroke_style))
    pen.setCapStyle(getattr(Qt.PenCapStyle, paint.stroke_cap))
    pen.setJoinStyle(getattr(Qt.PenJoinStyle, paint.stroke_join))
    return pen


def _is_dashed(pen: QPen) -> bool:
    return pen.style() not in (Qt.PenStyle.SolidLine, Qt.PenStyle.NoPen)


def _box(points: Iterable[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for x, y in points:
        xs.append(x)
        ys.append(y)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _ellipse_chords(radius_px: float) -> int:
    """The chords an ellipse of that (larger) radius is cut into: ``_CHORD_TOLERANCE_PX``
    from the curve, at least 24, at most ``_ELLIPSE_MAX_CHORDS``."""
    if not radius_px > _CHORD_TOLERANCE_PX:  # also for a NaN
        return 24
    chords = math.pi / math.sqrt(2.0 * _CHORD_TOLERANCE_PX / radius_px)
    return int(min(max(chords, 24.0), float(_ELLIPSE_MAX_CHORDS)))


def _cost(record: Record, px_per_cm: float = 1.0) -> int:
    """What painting the record into ONE tile is charged against the tile's vertex
    bound: its outline vertices times the passes made over them before anything
    is drawn (module docstring, the table)."""
    paint = record.material.paint or _PLAIN_PAINT
    dashed = paint.stroke_style not in ("SolidLine", "NoPen")
    if record.shape == SHAPE_POLYLINE:
        if record.params.get(PARAM_PATH_STYLE) is not None:
            return len(record.path) * _STYLED_PASSES
        return len(record.path) * (_DASHED_PASSES if dashed else 1)
    if dashed and record.shape in (SHAPE_CIRCLE, SHAPE_ELLIPSE):
        box = _local_box(record)  # its dashed outline is cut along chords
        radius = max(box[2] - box[0], box[3] - box[1]) / 2.0 if box is not None else 0.0
        return _ellipse_chords(radius * px_per_cm) * _DASHED_PASSES
    if record.outline:
        vertices = len(record.outline)
    else:
        vertices = sum(len(outer) + sum(len(hole) for hole in holes)
                       for outer, holes in record.regions)
    return vertices * (_DASHED_PASSES if dashed else 1)


def _local_box(record: Record) -> tuple[float, float, float, float] | None:
    """The record's geometry as a box in its LOCAL frame, or None when it holds
    nothing that could be painted."""
    if record.shape == SHAPE_CIRCLE:
        radius = record.params.get(PARAM_RADIUS_CM)
        if isinstance(radius, float) and radius > 0.0:
            return (-radius, -radius, radius, radius)
    if record.shape == SHAPE_POLYLINE and record.path:
        return _box(record.path)
    if record.outline:
        return _box(record.outline)
    return _box(point for ring in record.footprints for point in ring)


def paint_bounds(record: Record, px_per_cm: float = 1.0
                 ) -> tuple[float, float, float, float] | None:
    """``(east0, north0, east1, north1)`` — a scene-space box no pixel of
    ``paint_record(record)`` falls outside of, at ``px_per_cm``; None for a record
    with nothing to paint."""
    box = _local_box(record)
    if box is None:
        return None
    paint = record.material.paint or _PLAIN_PAINT
    if record.shape == SHAPE_POLYLINE:
        factor, constant = _PATH_REACH
        width = record.path_width_cm or 0.0
    else:
        factor, constant = _OUTLINE_REACH
        width = paint.stroke_width_cm
    reach = factor * width + constant + _SPILL_PX / px_per_cm
    transform = record.transform
    cos, sin = cos_sin(transform.rotation_deg)
    x0, y0, x1, y1 = box
    east = [cos * x - sin * y for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    north = [sin * x + cos * y for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    return (transform.east_cm + min(east) - reach, transform.north_cm + min(north) - reach,
            transform.east_cm + max(east) + reach, transform.north_cm + max(north) + reach)


# ── painting one record ───────────────────────────────────────────────────────


def _scale(painter: QPainter) -> float:
    """Device pixels per unit of the coordinates the painter paints in now."""
    return max(math.sqrt(abs(painter.transform().determinant())), 1e-9)


def _paint_sprite(painter: QPainter, object_type: ObjectType | None, rect: QRectF) -> bool:
    """A furniture item's sprite, as ``RectangleItem.paint`` / ``CircleItem.paint``
    draw it: rendered at the item's size, blitted into the rect aligned to whole
    centimetres, flipped about its centre (the canvas is drawn through a
    Y-flip). False — nothing painted — for a type without a sprite.

    The size is the PLAN's: it is bounded before the renderer is asked. A sprite
    of more than ``SPRITE_MAX_PIXELS`` is rendered with that many, in its own
    proportions, and stretched into its rect — an image of the item's own size
    would be the item's area in pixels, whatever a tile is (and would sit in the
    renderer's cache). A sprite within the bound is asked for exactly as the
    canvas asks for it: the same image, the same cache entry."""
    if object_type is None or not is_furniture_type(object_type):
        return False
    width, height = rect.width(), rect.height()
    if not (math.isfinite(width) and math.isfinite(height) and width > 0.0 and height > 0.0):
        return False
    # what the renderer would make of it: no side below its minimum
    wide, high = max(width, _SPRITE_MIN_SIDE), max(height, _SPRITE_MIN_SIDE)
    if wide * high > SPRITE_MAX_PIXELS:
        factor = math.sqrt(SPRITE_MAX_PIXELS / (wide * high))
        wide, high = max(wide * factor, _SPRITE_MIN_SIDE), max(high * factor, _SPRITE_MIN_SIDE)
        if wide * high > SPRITE_MAX_PIXELS:  # a sliver: its short side is at the minimum
            if wide > high:
                wide = SPRITE_MAX_PIXELS / high
            else:
                high = SPRITE_MAX_PIXELS / wide
        width, height = wide, high
    pixmap = render_furniture_pixmap(object_type=object_type, width=width, height=height)
    if pixmap is None:
        return False
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.save()
    painter.translate(rect.center())
    painter.scale(1.0, -1.0)
    painter.translate(-rect.center())
    painter.drawPixmap(rect.toAlignedRect(), pixmap)
    painter.restore()
    return True


def _item_points(ring: Iterable[tuple[float, float]], ox: float, oy: float) -> list[QPointF]:
    return [QPointF(x + ox, y + oy) for x, y in ring]


def _axis_rect(ring: tuple[tuple[float, float], ...], ox: float, oy: float) -> QRectF | None:
    """The rectangle a four-corner ring is, in item coordinates — None when it is
    not an upright rectangle (an item under a transform that is not rigid: its
    record holds the sheared outline)."""
    if len(ring) != 4:
        return None
    xs = sorted({round(x, 6) for x, _y in ring})
    ys = sorted({round(y, 6) for _x, y in ring})
    if len(xs) != 2 or len(ys) != 2:
        return None
    return QRectF(xs[0] + ox, ys[0] + oy, xs[1] - xs[0], ys[1] - ys[0])


def _element_clip(painter: QPainter, width_cm: float) -> tuple[float, float, float, float] | None:
    """The painter's device, as a box in the coordinates it paints in now, grown by
    what a style's element can reach: outside of it no element need be generated.
    None when the painter's transform cannot be inverted (nothing would be seen)."""
    inverse, invertible = painter.transform().inverted()
    if not invertible:
        return None
    box = inverse.mapRect(QRectF(painter.viewport()))
    factor, constant = _ELEMENT_REACH
    grow = factor * width_cm + constant + _SPILL_PX / _scale(painter)
    return (box.left() - grow, box.top() - grow, box.right() + grow, box.bottom() + grow)


def _runs_inside(points: list[QPointF], clip: tuple[float, float, float, float]
                 ) -> Iterator[tuple[float, float, list[QPointF]]]:
    """The parts of a polyline that lie inside ``clip``: ``(start, length,
    points)`` — the distance along the WHOLE line at which the run starts, how long
    it is, and its points. A line that lies inside altogether is one run from 0
    through its own points."""
    travelled = 0.0
    run: list[QPointF] = []
    run_start = run_length = 0.0
    for first, second in pairwise(points):
        dx, dy = second.x() - first.x(), second.y() - first.y()
        length = math.hypot(dx, dy)
        if not length > 0.0:
            continue
        dir_x, dir_y = dx / length, dy / length
        span = segment_inside(first.x(), first.y(), dir_x, dir_y, length, clip)
        if run and (span is None or span[0] > 0.0):
            yield run_start, run_length, run
            run = []
        if span is not None:
            if not run:
                run_start, run_length = travelled + span[0], 0.0
                run = [first if span[0] <= 0.0 else
                       QPointF(first.x() + dir_x * span[0], first.y() + dir_y * span[0])]
            run_length += span[1] - span[0]
            if span[1] >= length:
                run.append(second)
            else:
                run.append(QPointF(first.x() + dir_x * span[1], first.y() + dir_y * span[1]))
                yield run_start, run_length, run
                run = []
        travelled += length
    if run:
        yield run_start, run_length, run


def _dash_count(length: float, width: float, scale: float) -> int:
    """An upper bound of the dashes a dashed stroke of that length makes: every
    pattern has at most one dash in three units, and a unit is the pen's width —
    for a pen thinner than a pixel, a pixel (Qt steps such a line pixel by
    pixel)."""
    unit = max(width, 1.0 / scale)
    return int(min(length / (3.0 * unit), 1e12)) + 1


def _length(points: list[QPointF], closed: bool) -> float:
    total = sum(math.hypot(b.x() - a.x(), b.y() - a.y()) for a, b in pairwise(points))
    if closed and len(points) > 1:
        total += math.hypot(points[0].x() - points[-1].x(), points[0].y() - points[-1].y())
    return total


def _stroke_dashed(painter: QPainter, points: list[QPointF], pen: QPen, width_cm: float,
                   budget: WalkBudget, *, closed: bool = False) -> None:
    """A dashed line, cut to the painter's device BEFORE it is stroked. Each run
    inside is stroked with the dash offset of the place it starts at, so the
    dashes lie where the whole line's would and the dasher is never handed the
    length outside; and each run's dashes are charged to ``budget`` first — a run
    the budget does not cover is stroked solid. (Qt's own dasher was measured to
    skip what lies outside an image, but that is its implementation's kindness,
    not a bound of ours — and over kilometres it loses the dash phase.)"""
    clip = _element_clip(painter, width_cm)
    if clip is None or len(points) < 2:
        return
    scale = _scale(painter)
    line = [*points, points[0]] if closed else points
    # A dash pattern and its offset are in units of the pen's width — and in PIXELS for
    # a pen up to a pixel wide (measured, Qt 6.11's raster engine: offsetting such a
    # run in pen widths puts its dashes somewhere else)
    unit = max(width_cm, 1.0 / scale)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for start, length, run in _runs_inside(line, clip):
        run_pen = QPen(pen)
        if budget.take_dashes(_dash_count(length, width_cm, scale)):
            run_pen.setDashOffset(start / unit)
        else:
            run_pen.setStyle(Qt.PenStyle.SolidLine)
        painter.setPen(run_pen)
        path = QPainterPath()
        path.moveTo(run[0])
        whole = closed and len(run) == len(line) and start == 0.0
        for point in (run[1:-1] if whole else run[1:]):
            path.lineTo(point)
        if whole:
            path.closeSubpath()  # the outline itself: its last corner is a join, as Qt's is
        painter.drawPath(path)


def _ellipse_points(rect: QRectF, scale: float) -> list[QPointF]:
    """An ellipse as the closed line a dashed pen runs along — from three o'clock
    toward larger y, as Qt's own ellipse does — in chords (``_ellipse_chords``)."""
    centre, a, b = rect.center(), rect.width() / 2.0, rect.height() / 2.0
    chords = _ellipse_chords(max(a, b) * scale)
    step = 2.0 * math.pi / chords
    return [QPointF(centre.x() + a * math.cos(k * step), centre.y() + b * math.sin(k * step))
            for k in range(chords)]


def _rect_points(rect: QRectF) -> list[QPointF]:
    return [rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft()]


def _paint_filled(painter: QPainter, record: Record, paint: Paint, ox: float, oy: float,
                  budget: WalkBudget) -> None:
    """A rectangle, an ellipse, a circle or a polygon: its sprite, or pen and brush
    and the one call the item's own ``paint()`` makes."""
    material = record.material
    shape = record.shape
    object_type = _object_type(record.kind)
    rect: QRectF | None = None
    if shape == SHAPE_CIRCLE:
        radius = record.params.get(PARAM_RADIUS_CM)
        if isinstance(radius, float) and radius > 0.0:
            rect = QRectF(ox - radius, oy - radius, 2.0 * radius, 2.0 * radius)
    elif shape in (SHAPE_RECTANGLE, SHAPE_ELLIPSE) and len(record.footprints) == 1:
        ring = record.footprints[0]
        if shape == SHAPE_RECTANGLE:
            rect = _axis_rect(ring, ox, oy)
        else:
            box = _box(ring)
            # the ring is the ellipse at 24 angles from 0°: an upright one has its
            # first vertex on the local x axis
            if box is not None and abs(ring[0][1]) < 1e-6:
                rect = QRectF(box[0] + ox, box[1] + oy, box[2] - box[0], box[3] - box[1])
    if rect is not None and shape != SHAPE_ELLIPSE and _paint_sprite(painter, object_type, rect):
        return
    pen = _pen(material, paint, paint.stroke_width_cm)
    furniture = object_type is not None and is_furniture_type(object_type)
    # a furniture item without its sprite file has no brush on the canvas either
    brush = QBrush(Qt.BrushStyle.NoBrush) if furniture else _brush(material)
    # The outline of a polygon: one closed ring of points, several for a record of
    # regions (a rectangle, an ellipse and a circle are their rect).
    rings: list[list[QPointF]]
    if rect is not None:
        rings = []
    elif record.outline:
        rings = [_item_points(record.outline, ox, oy)]
    else:
        rings = [_item_points(ring, ox, oy)
                 for outer, holes in record.regions for ring in (outer, *holes)]

    def draw() -> None:
        if rect is not None:
            if shape == SHAPE_RECTANGLE:
                painter.drawRect(rect)
            else:
                painter.drawEllipse(rect)
        elif len(rings) == 1:
            # the polygon AS DRAWN, with the canvas's fill rule (QGraphicsPolygonItem's)
            painter.drawPolygon(QPolygonF(rings[0]), Qt.FillRule.OddEvenFill)
        else:  # every region — outer rings minus their holes — as one path
            path = QPainterPath()
            path.setFillRule(Qt.FillRule.OddEvenFill)
            for ring in rings:
                path.addPolygon(QPolygonF(ring))
                path.closeSubpath()
            painter.drawPath(path)

    if not _is_dashed(pen):
        painter.setPen(pen)
        painter.setBrush(brush)
        draw()
        return
    # A dashed outline: its dashes are as many as it is LONG, and are charged to the
    # tile before they are stroked.
    width = paint.stroke_width_cm
    scale = _scale(painter)
    clip = _element_clip(painter, width)
    if clip is None:
        return
    if rect is not None:
        inside = (clip[0] <= rect.left() and rect.right() <= clip[2]
                  and clip[1] <= rect.top() and rect.bottom() <= clip[3])
    else:
        inside = all(clip[0] <= point.x() <= clip[2] and clip[1] <= point.y() <= clip[3]
                     for ring in rings for point in ring)
    if inside:
        # all of it lies in this tile: the canvas's own call — solid when the tile has
        # not that many dashes left
        if rect is None:
            perimeter = sum(_length(ring, True) for ring in rings)
        elif shape == SHAPE_RECTANGLE:
            perimeter = 2.0 * (rect.width() + rect.height())
        else:  # Ramanujan's: a bound is all that is needed
            a, b = rect.width() / 2.0, rect.height() / 2.0
            perimeter = math.pi * (3.0 * (a + b) - math.sqrt((3.0 * a + b) * (a + 3.0 * b)))
        if not budget.take_dashes(_dash_count(perimeter, width, scale)):
            pen.setStyle(Qt.PenStyle.SolidLine)
        painter.setPen(pen)
        painter.setBrush(brush)
        draw()
        return
    # It reaches beyond the tile: the fill as it is, and the outline cut to the tile —
    # each run with the dash offset of the place it starts at. (Handed the whole
    # outline, Qt's dasher skips what lies outside — and was measured to put the
    # dashes of a rectangle, a polygon and an ellipse somewhere else than it does in
    # one image of the whole plan; cut by us they lie where the canvas has them.)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(brush)
    draw()
    if rect is not None:
        rings = [_rect_points(rect) if shape == SHAPE_RECTANGLE else _ellipse_points(rect, scale)]
    for closed_line in rings:
        _stroke_dashed(painter, closed_line, pen, width, budget, closed=True)


def _paint_polyline(painter: QPainter, record: Record, paint: Paint, ox: float, oy: float,
                    budget: WalkBudget) -> None:
    """A path or a line: its style's painter (shared with ``PolylineItem``), else
    the plain stroke of ``QGraphicsPathItem.paint``. A style's elements are
    generated only inside the painter's device and charged to ``budget``; so are
    the dashes of a dashed line."""
    points = _item_points(record.path, ox, oy)
    if len(points) < 2:
        return
    material = record.material
    width_cm = record.path_width_cm or 0.0
    pen = _pen(material, paint, width_cm)
    path = QPainterPath()
    path.moveTo(points[0])
    for point in points[1:]:
        path.lineTo(point)
    style_name = record.params.get(PARAM_PATH_STYLE)
    style = PathFenceStyle.NONE
    if isinstance(style_name, str):
        try:
            style = PathFenceStyle[style_name]
        except KeyError:
            style = PathFenceStyle.NONE
    if style is not PathFenceStyle.NONE:
        clip = _element_clip(painter, width_cm)
        if clip is None:
            return
        if PathStylePaint(points, pen, path, clip=clip, budget=budget).paint(painter, style):
            return
    brush = _brush(material)  # none for a line; a filled one is filled, as on the canvas
    if not _is_dashed(pen):
        painter.setPen(pen)
        painter.setBrush(brush)
        painter.drawPath(path)
        return
    if brush.style() != Qt.BrushStyle.NoBrush:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(brush)
        painter.drawPath(path)
    _stroke_dashed(painter, points, pen, width_cm, budget)


def paint_record(painter: QPainter, record: Record, *, budget: WalkBudget | None = None) -> None:
    """Paint one record's surface as the 2D canvas paints the item.

    ``painter``'s transform must map SCENE centimetres to the device (east to +x;
    for a north-up image, north to −y). The record is painted in the item's own
    coordinate frame (module docstring); the painter is left as it was found.
    Antialiasing and smooth pixmap transforms are the caller's to switch on — the
    canvas and its exports paint with both.

    ``budget``: what a styled path's walk and a dashed pen's dashes may cost
    (``WalkBudget``) — the caller's when several records share one image (a
    tile), else one tile's worth for this call. Elements and dashes are generated
    only inside the painter's device: what lies beside the image is never drawn.
    """
    if budget is None:
        budget = tile_budget()
    paint = record.material.paint or _PLAIN_PAINT
    origin = record.params.get(PARAM_PAINT_ORIGIN)
    ox, oy = 0.0, 0.0
    if isinstance(origin, tuple) and len(origin) == 2:
        first, second = origin
        if isinstance(first, float) and isinstance(second, float):
            ox, oy = first, second
    transform = record.transform
    cos, sin = cos_sin(transform.rotation_deg)
    painter.save()
    try:
        # the item's scene transform: scene = T + R · (item − origin)
        painter.setTransform(QTransform(
            cos, sin, -sin, cos,
            transform.east_cm - (cos * ox - sin * oy),
            transform.north_cm - (sin * ox + cos * oy)), True)
        if paint.opacity < 1.0:
            painter.setOpacity(painter.opacity() * paint.opacity)
        if record.shape == SHAPE_POLYLINE:
            _paint_polyline(painter, record, paint, ox, oy, budget)
        elif record.shape in (SHAPE_CIRCLE, SHAPE_ELLIPSE, SHAPE_RECTANGLE, SHAPE_POLYGON):
            _paint_filled(painter, record, paint, ox, oy, budget)
    finally:
        painter.restore()


# ── the baker ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Painted:
    """A record that is on the ground: the block of tiles its paint bounds reach
    (first and last column, first and last row — four integers, however many
    tiles that is) and what it costs a tile."""

    record: Record
    columns: tuple[int, int]
    rows: tuple[int, int]
    cost: int

    @property
    def count(self) -> int:
        return (self.columns[1] - self.columns[0] + 1) * (self.rows[1] - self.rows[0] + 1)

    def covers(self, key: TileKey) -> bool:
        return (self.columns[0] <= key[0] <= self.columns[1]
                and self.rows[0] <= key[1] <= self.rows[1])

    def tiles(self) -> Iterator[TileKey]:
        for row in range(self.rows[0], self.rows[1] + 1):
            for column in range(self.columns[0], self.columns[1] + 1):
                yield (column, row)


class GroundBaker:
    """Bakes the plan's ground from records, tile by tile (module docstring).

    The ``core.scene3d.GroundBaking`` a ``SceneSync`` drives::

        baker = GroundBaker()
        sync = SceneSync(sink, production_registry(), ground_baker=baker)
        sync.apply(snapshot_records(scene, at_date), sun=..., ground=GroundSpec(width, depth))

    ``bare_rgb``: the colour of ground no item covers (``MEADOW_RGB`` — a caller
    whose look has another meadow albedo passes that one; the parity tests pass
    the canvas's own background colour). ``tile_px`` / ``max_pixels``: the grid
    (``GroundGrid.for_plan``) — validated HERE, where they enter
    (``ground.check_grid_settings``): they can make a ground coarser, never
    larger than the stated bounds.

    Instruments, for tests and diagnostics: ``last_repainted`` (the tiles the
    last ``bake`` painted anew), ``last_changed`` (those of them whose picture
    came out different — what a sink uploads), ``last_examined`` (the records it
    looked at), ``tiles_painted``, ``records_painted``, ``elements_generated``,
    ``steps_walked``, ``dashes_charged`` and ``admission_checks`` (running
    totals), ``entries_held()``, ``capped`` (the tiles that ARE painted with a
    bound of the module docstring in force — records left out, a style's elements
    cut short, a dashed run stroked solid: the user sees less than the plan
    there) with ``capped_tiles`` (how often that happened) and ``failures``
    (items whose paint raised: such an item is left out of the tile, the rest of
    it is painted, the failure is logged once).
    """

    def __init__(self, *, bare_rgb: tuple[int, int, int] = MEADOW_RGB, tile_px: int = TILE_PX,
                 max_pixels: int = MAX_GROUND_PIXELS) -> None:
        if len(bare_rgb) != 3 or not all(type(c) is int and 0 <= c <= 255 for c in bare_rgb):
            raise ValueError(f"bare_rgb is three ints in 0..255, got {bare_rgb!r}")
        check_grid_settings(tile_px, max_pixels)  # before anything is allocated with them
        self._bare_rgb = (bare_rgb[0], bare_rgb[1], bare_rgb[2])
        self._tile_px = tile_px
        self._max_pixels = max_pixels
        self._grid: GroundGrid | None = None
        self._painted: dict[str, _Painted] = {}
        self._order: dict[TileKey, tuple[str, ...]] = {}
        self._tiles: dict[TileKey, GroundTile] = {}
        self._capped: set[TileKey] = set()
        self._bare_pixels: dict[tuple[int, int], bytes] = {}
        self._reported: set[tuple[str, str]] = set()
        self._cap_reported = False
        self.failures: dict[str, tuple[str, str]] = {}
        self.last_repainted: tuple[TileKey, ...] = ()
        self.last_changed: tuple[TileKey, ...] = ()
        self.last_examined = 0
        self.tiles_painted = 0
        self.records_painted = 0
        self.elements_generated = 0
        self.steps_walked = 0
        self.dashes_charged = 0
        self.admission_checks = 0
        self.capped_tiles = 0

    # ── what it holds ────────────────────────────────────────────────────────

    @property
    def grid(self) -> GroundGrid | None:
        """The grid of the plan it last baked (None before the first ``bake`` and
        after ``clear``)."""
        return self._grid

    @property
    def bare_rgb(self) -> tuple[int, int, int]:
        return self._bare_rgb

    def painted_ids(self) -> tuple[str, ...]:
        """The items that are on the ground now."""
        return tuple(self._painted)

    @property
    def capped(self) -> frozenset[TileKey]:
        """The tiles that are painted with a bound in force right now: something of
        the plan is missing from the ground there (module docstring)."""
        return frozenset(self._capped)

    def entries_held(self) -> int:
        """What the bookkeeping holds: one entry per painted record and, per tile,
        one per record it was last painted with (at most ``TILE_RECORDS``)."""
        return len(self._painted) + sum(len(order) for order in self._order.values())

    def tiles(self) -> tuple[GroundTile, ...]:
        """Every tile of the ground as it was last baked, row by row from the
        south-west corner."""
        grid = self._grid
        if grid is None:
            return ()
        return tuple(self._tiles[key] for key in grid.tile_keys())

    def image(self) -> QImage:
        """The whole ground as ONE north-up image. A TEST AND TOOL HELPER — the
        engine gets the tiles, and nothing in the product calls this: it allocates
        the whole ground a second time (at most ``max_pixels`` × 4 bytes, 64 MB).
        Null before the first ``bake``."""
        grid = self._grid
        if grid is None:
            return QImage()
        whole = QImage(grid.width_px, grid.height_px, _TILE_FORMAT)
        whole.fill(QColor(*self._bare_rgb))
        painter = QPainter(whole)
        for key in grid.tile_keys():
            x0, y0, width, height = grid.tile_px_rect(key)
            tile = self._tiles[key]
            part = QImage(tile.pixels, width, height, 4 * width, _TILE_FORMAT)
            painter.drawImage(x0, grid.height_px - y0 - height, part)
        painter.end()
        return whole

    # ── GroundBaking ─────────────────────────────────────────────────────────

    def clear(self) -> None:
        """Forget every tile: the next ``bake`` paints the whole ground anew."""
        self._grid = None
        self._painted = {}
        self._order = {}
        self._tiles = {}
        self._capped = set()
        self.failures = {}
        self.last_repainted = ()
        self.last_changed = ()

    def fresh(self) -> GroundBaker:
        """An empty baker with this one's settings."""
        return GroundBaker(bare_rgb=self._bare_rgb, tile_px=self._tile_px,
                           max_pixels=self._max_pixels)

    def bake(self, records: Mapping[str, Record], changes: SceneDiff | None,
             solid: Collection[str], width_cm: float, depth_cm: float
             ) -> tuple[GroundTile, ...]:
        """The ground of ``records`` on a plan of ``width_cm`` × ``depth_cm`` — every
        tile; one that was not re-painted is the object it was
        (``core.scene3d.GroundBaking``). With ``changes`` only the records it names
        are looked at; without, all of them — the tiles are the same either way."""
        grid = self._grid
        dirty: set[TileKey] = set()
        if grid is None or (grid.width_cm, grid.depth_cm) != (width_cm, depth_cm):
            self.clear()
            grid = GroundGrid.for_plan(width_cm, depth_cm, tile_px=self._tile_px,
                                       max_pixels=self._max_pixels)
            self._grid = grid
            dirty.update(grid.tile_keys())
            changes = None
        candidates: Iterable[str]
        if changes is None:
            candidates = dict.fromkeys((*records, *self._painted))
        else:
            candidates = dict.fromkeys((*changes.removed, *changes.added, *changes.geometry,
                                        *changes.transform, *changes.material))
        examined = 0
        touched: set[str] = set()  # the painted records that are new, or no longer what they were
        for item_id in candidates:
            examined += 1
            record = records.get(item_id)
            held = self._painted.get(item_id)
            wanted = (record is not None and record.material.paint is not None
                      and item_id not in solid)
            if wanted and held is not None and record is not None and (
                    held.record is record or (held.record.same_geometry(record)
                                              and held.record.same_transform(record)
                                              and held.record.same_material(record))):
                continue  # painted, and nothing of it changed
            if held is not None:
                dirty.update(held.tiles())
                del self._painted[item_id]
            if not wanted or record is None:
                self.failures.pop(item_id, None)
                continue
            bounds = paint_bounds(record, grid.px_per_cm)
            block = grid.tile_block(*bounds) if bounds is not None else None
            if block is None:
                continue  # nothing to paint, or beside the plan: not on the ground
            entry = _Painted(record, (block[0], block[2]), (block[1], block[3]),
                             _cost(record, grid.px_per_cm))
            self._painted[item_id] = entry
            touched.add(item_id)
            dirty.update(entry.tiles())
        self.last_examined = examined
        if changes is None or changes.reordered:
            # records that swapped places: every tile that holds records is looked at
            # again (and re-painted only if its list came out different)
            dirty.update(key for key, order in self._order.items() if order)
        repainted: list[TileKey] = []
        changed: list[TileKey] = []
        if dirty:
            for key, (order, capped) in sorted(self._admit(records, dirty).items()):
                if (key in self._tiles and order == self._order.get(key, ())
                        and capped == (key in self._capped) and touched.isdisjoint(order)):
                    continue  # the same records, none of them changed: the same picture
                tile = self._paint_tile(grid, key, [self._painted[item_id].record
                                                    for item_id in order], capped)
                self._order[key] = order
                repainted.append(key)
                previous = self._tiles.get(key)
                if previous is None or previous != tile:  # the same picture again is the same tile
                    self._tiles[key] = tile
                    changed.append(key)
        self.last_repainted = tuple(repainted)
        self.last_changed = tuple(changed)
        return tuple(self._tiles[key] for key in grid.tile_keys())

    # ── internals ────────────────────────────────────────────────────────────

    def _admit(self, records: Mapping[str, Record], dirty: set[TileKey]
               ) -> dict[TileKey, tuple[tuple[str, ...], bool]]:
        """For every dirty tile: the ids it is painted with, bottom to top, and
        whether a bound left any out — ONE walk down the records, topmost first.

        A tile takes records until it holds ``TILE_RECORDS`` or the next one would
        take its cost beyond ``TILE_VERTICES`` (the topmost record of a tile is
        always taken); then it is full and is asked about no further record. The
        walk ends when every dirty tile is full. So the work is at most one test
        per record and dirty tile — ``admission_checks`` counts them — and what a
        bound leaves out is never sorted, summed or touched.
        """
        open_tiles: dict[TileKey, tuple[list[str], int]] = {key: ([], 0) for key in dirty}
        full: dict[TileKey, tuple[tuple[str, ...], bool]] = {}
        painted = self._painted
        # a dict is walked backwards in place; another mapping through a copy of its keys
        for item_id in (reversed(records) if isinstance(records, dict)
                        else reversed(list(records))):
            if not open_tiles:
                break
            entry = painted.get(item_id)
            if entry is None:
                continue
            if entry.count <= len(open_tiles):
                keys = [key for key in entry.tiles() if key in open_tiles]
                self.admission_checks += entry.count
            else:
                keys = [key for key in open_tiles if entry.covers(key)]
                self.admission_checks += len(open_tiles)
            for key in keys:
                ids, cost = open_tiles[key]
                if ids and (len(ids) >= TILE_RECORDS or cost + entry.cost > TILE_VERTICES):
                    ids.reverse()
                    full[key] = (tuple(ids), True)
                    del open_tiles[key]
                else:
                    ids.append(item_id)
                    open_tiles[key] = (ids, cost + entry.cost)
        for key, (ids, _cost_so_far) in open_tiles.items():
            ids.reverse()
            full[key] = (tuple(ids), False)
        return full

    def _paint_tile(self, grid: GroundGrid, key: TileKey, records: list[Record],
                    capped: bool) -> GroundTile:
        """One tile, from scratch: bare ground, then its records bottom to top —
        within the tile's budget (module docstring)."""
        x0, y0, width, height = grid.tile_px_rect(key)
        east, north, width_cm, depth_cm = grid.tile_cm_rect(key)
        self.tiles_painted += 1
        self._capped.discard(key)
        if not records:
            pixels = self._bare_pixels.get((width, height))
            if pixels is None:
                pixels = bytes((*self._bare_rgb, 255)) * (width * height)
                self._bare_pixels[(width, height)] = pixels
            return GroundTile(key[0], key[1], east, north, width_cm, depth_cm, width, height, pixels)
        image = QImage(width, height, _TILE_FORMAT)
        if image.isNull():
            raise MemoryError(f"no image of {width} x {height} px for ground tile {key}")
        image.fill(QColor(*self._bare_rgb))
        painter = QPainter(image)
        try:
            # what the canvas and its exports paint with
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            scale = grid.px_per_cm
            # scene (east, north) → this tile's pixels, north up: row 0 is its north edge.
            # Whole-pixel offsets of ONE grid: every tile is a crop of the same image.
            painter.setTransform(QTransform(scale, 0.0, 0.0, -scale, float(-x0), float(y0 + height)))
            budget = tile_budget()  # ONE for the whole tile
            for record in records:
                self.records_painted += 1
                self.failures.pop(record.item_id, None)
                try:
                    paint_record(painter, record, budget=budget)
                except Exception as error:  # one item must not blank a tile
                    self._report(record, error)
            self.elements_generated += budget.elements_spent
            self.steps_walked += budget.steps_spent
            self.dashes_charged += budget.dashes_spent
            capped = capped or budget.truncated
        finally:
            painter.end()
        if capped:
            self._capped.add(key)
            self.capped_tiles += 1
            if not self._cap_reported:
                self._cap_reported = True
                _log.warning(
                    "3D ground: tile %s holds more than one tile is painted with (%d records, "
                    "%d outline vertices and passes, %d path-style elements, %d dashes); the "
                    "rest is left out of the ground. Further tiles are only counted "
                    "(GroundBaker.capped)", key, TILE_RECORDS, TILE_VERTICES, TILE_ELEMENTS,
                    TILE_DASHES)
        bits = image.constBits()
        if bits is None:
            raise MemoryError(f"ground tile {key} has no pixel data")
        return GroundTile(key[0], key[1], east, north, width_cm, depth_cm, width, height,
                          bits.asstring(image.sizeInBytes()))

    def _report(self, record: Record, error: Exception) -> None:
        self.failures[record.item_id] = (type(error).__name__, str(error))
        seen = (record.item_id, type(error).__name__)
        if seen not in self._reported and len(self._reported) < 256:
            self._reported.add(seen)
            _log.error("3D ground: %s item %r could not be painted; it is left out of the ground",
                       record.kind, record.item_id, exc_info=error)
