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
re-paints exactly the tiles whose list changed: the tiles under the old and the
new paint bounds of every record that was added, removed, changed, or gained or
lost its solid, and the tiles in which two records swapped places. Every other
tile is handed out as the SAME object as before — which is how the sink knows
not to upload it.

GUI thread only (``QPixmap`` brushes and sprites). ``QtGui`` only: this module
imports no Qt Quick module and nothing of the engine package.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Iterable, Mapping
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
from open_garden_planner.ui.canvas.items.path_style_paint import PathStylePaint

__all__ = ["GroundBaker", "paint_bounds", "paint_record"]

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


def _box(points: Iterable[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for x, y in points:
        xs.append(x)
        ys.append(y)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


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


def _paint_sprite(painter: QPainter, object_type: ObjectType | None, rect: QRectF) -> bool:
    """A furniture item's sprite, as ``RectangleItem.paint`` / ``CircleItem.paint``
    draw it: rendered at the item's size, blitted into the rect aligned to whole
    centimetres, flipped about its centre (the canvas is drawn through a
    Y-flip). False — nothing painted — for a type without a sprite."""
    if object_type is None or not is_furniture_type(object_type):
        return False
    pixmap = render_furniture_pixmap(object_type=object_type, width=rect.width(),
                                     height=rect.height())
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


def _paint_regions(painter: QPainter, record: Record, ox: float, oy: float) -> None:
    """Every region of the record — outer rings minus their holes — as one path."""
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.OddEvenFill)
    for outer, holes in record.regions:
        for ring in (outer, *holes):
            path.addPolygon(QPolygonF(_item_points(ring, ox, oy)))
            path.closeSubpath()
    painter.drawPath(path)


def _paint_filled(painter: QPainter, record: Record, paint: Paint, ox: float, oy: float) -> None:
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
    painter.setPen(_pen(material, paint, paint.stroke_width_cm))
    furniture = object_type is not None and is_furniture_type(object_type)
    # a furniture item without its sprite file has no brush on the canvas either
    painter.setBrush(QBrush(Qt.BrushStyle.NoBrush) if furniture else _brush(material))
    if rect is not None:
        if shape == SHAPE_RECTANGLE:
            painter.drawRect(rect)
        else:
            painter.drawEllipse(rect)
    elif record.outline:
        # the polygon AS DRAWN, with the canvas's fill rule (QGraphicsPolygonItem's)
        painter.drawPolygon(QPolygonF(_item_points(record.outline, ox, oy)),
                            Qt.FillRule.OddEvenFill)
    elif len(record.footprints) == 1 and not record.holes:
        painter.drawPolygon(QPolygonF(_item_points(record.footprints[0], ox, oy)),
                            Qt.FillRule.OddEvenFill)
    else:
        _paint_regions(painter, record, ox, oy)


def _paint_polyline(painter: QPainter, record: Record, paint: Paint, ox: float, oy: float) -> None:
    """A path or a line: its style's painter (shared with ``PolylineItem``), else
    the plain stroke of ``QGraphicsPathItem.paint``."""
    points = _item_points(record.path, ox, oy)
    if len(points) < 2:
        return
    material = record.material
    pen = _pen(material, paint, record.path_width_cm or 0.0)
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
    if PathStylePaint(points, pen, path).paint(painter, style):
        return
    painter.setPen(pen)
    painter.setBrush(_brush(material))  # none for a line; a filled one is filled, as on the canvas
    painter.drawPath(path)


def paint_record(painter: QPainter, record: Record) -> None:
    """Paint one record's surface as the 2D canvas paints the item.

    ``painter``'s transform must map SCENE centimetres to the device (east to +x;
    for a north-up image, north to −y). The record is painted in the item's own
    coordinate frame (module docstring); the painter is left as it was found.
    Antialiasing and smooth pixmap transforms are the caller's to switch on — the
    canvas and its exports paint with both.
    """
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
            _paint_polyline(painter, record, paint, ox, oy)
        elif record.shape in (SHAPE_CIRCLE, SHAPE_ELLIPSE, SHAPE_RECTANGLE, SHAPE_POLYGON):
            _paint_filled(painter, record, paint, ox, oy)
    finally:
        painter.restore()


# ── the baker ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Painted:
    """A record that is on the ground, and the tiles its paint reaches."""

    record: Record
    tiles: frozenset[TileKey]


class GroundBaker:
    """Bakes the plan's ground from records, tile by tile (module docstring).

    The ``core.scene3d.GroundBaking`` a ``SceneSync`` drives::

        baker = GroundBaker()
        sync = SceneSync(sink, production_registry(), ground_baker=baker)
        sync.apply(snapshot_records(scene, at_date), sun=..., ground=GroundSpec(width, depth))

    ``bare_rgb``: the colour of ground no item covers (``MEADOW_RGB`` — a caller
    whose look has another meadow albedo passes that one; the parity tests pass
    the canvas's own background colour). ``tile_px`` / ``max_pixels``: the grid
    (``GroundGrid.for_plan``).

    Instruments, for tests and diagnostics: ``last_repainted`` (the tiles the
    last ``bake`` painted anew), ``last_changed`` (those of them whose picture
    came out different — what a sink uploads), ``last_examined`` (the records it
    looked at), ``tiles_painted`` and ``records_painted`` (running totals) and
    ``failures`` (items whose paint raised: such an item is left out of the tile,
    the rest of it is painted, the failure is logged once).
    """

    def __init__(self, *, bare_rgb: tuple[int, int, int] = MEADOW_RGB, tile_px: int = TILE_PX,
                 max_pixels: int = MAX_GROUND_PIXELS) -> None:
        if len(bare_rgb) != 3 or not all(type(c) is int and 0 <= c <= 255 for c in bare_rgb):
            raise ValueError(f"bare_rgb is three ints in 0..255, got {bare_rgb!r}")
        self._bare_rgb = (bare_rgb[0], bare_rgb[1], bare_rgb[2])
        self._tile_px = tile_px
        self._max_pixels = max_pixels
        self._grid: GroundGrid | None = None
        self._painted: dict[str, _Painted] = {}
        self._members: dict[TileKey, set[str]] = {}
        self._order: dict[TileKey, tuple[str, ...]] = {}
        self._tiles: dict[TileKey, GroundTile] = {}
        self._bare_pixels: dict[tuple[int, int], bytes] = {}
        self._reported: set[tuple[str, str]] = set()
        self.failures: dict[str, tuple[str, str]] = {}
        self.last_repainted: tuple[TileKey, ...] = ()
        self.last_changed: tuple[TileKey, ...] = ()
        self.last_examined = 0
        self.tiles_painted = 0
        self.records_painted = 0

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

    def tiles(self) -> tuple[GroundTile, ...]:
        """Every tile of the ground as it was last baked, row by row from the
        south-west corner."""
        grid = self._grid
        if grid is None:
            return ()
        return tuple(self._tiles[key] for key in grid.tile_keys())

    def image(self) -> QImage:
        """The whole ground as ONE north-up image (tests and diagnostics; the
        engine gets the tiles). Null before the first ``bake``."""
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
        self._members = {}
        self._order = {}
        self._tiles = {}
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
                dirty |= held.tiles
                for key in held.tiles:
                    self._members[key].discard(item_id)
                del self._painted[item_id]
            if not wanted or record is None:
                continue
            bounds = paint_bounds(record, grid.px_per_cm)
            if bounds is None:
                continue
            tiles = frozenset(grid.tiles_touching(*bounds))
            if not tiles:
                continue  # beside the plan: nothing of it is on the ground
            self._painted[item_id] = _Painted(record, tiles)
            for key in tiles:
                self._members.setdefault(key, set()).add(item_id)
            dirty |= tiles
        self.last_examined = examined
        position: dict[str, int] | None = None
        if changes is None or changes.reordered:
            # two records that swapped places re-paint the tiles they share
            position = {item_id: index for index, item_id in enumerate(records)}
            for key, painted in self._order.items():
                if key not in dirty and any(position[first] > position[second]
                                            for first, second in pairwise(painted)):
                    dirty.add(key)
        if dirty and position is None:
            position = {item_id: index for index, item_id in enumerate(records)}
        repainted = sorted(dirty)
        changed: list[TileKey] = []
        for key in repainted:
            order = tuple(sorted(self._members.get(key, ()),
                                 key=(position or {}).__getitem__))
            tile = self._paint_tile(grid, key, [self._painted[item_id].record for item_id in order])
            self._order[key] = order
            previous = self._tiles.get(key)
            if previous is None or previous != tile:  # the same picture again is the same tile
                self._tiles[key] = tile
                changed.append(key)
        self.last_repainted = tuple(repainted)
        self.last_changed = tuple(changed)
        return tuple(self._tiles[key] for key in grid.tile_keys())

    # ── internals ────────────────────────────────────────────────────────────

    def _paint_tile(self, grid: GroundGrid, key: TileKey, records: list[Record]) -> GroundTile:
        """One tile, from scratch: bare ground, then ``records`` bottom to top."""
        x0, y0, width, height = grid.tile_px_rect(key)
        east, north, width_cm, depth_cm = grid.tile_cm_rect(key)
        self.tiles_painted += 1
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
            for record in records:
                self.records_painted += 1
                self.failures.pop(record.item_id, None)
                try:
                    paint_record(painter, record)
                except Exception as error:  # one item must not blank a tile
                    self._report(record, error)
        finally:
            painter.end()
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
