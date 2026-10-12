"""An item's look, written in ONE place: the style attributes and the live brush and pen together.

A canvas item states its look twice. Its style ATTRIBUTES — ``fill_color``,
``fill_pattern``, ``stroke_color``, ``stroke_width``, ``stroke_style``
(``GardenItemMixin``) — are what the project file, the clipboard, the properties
panel, the undo of a type change and the 3D ground bake read. Its live Qt
``brush()`` and ``pen()`` are what the canvas paints. Nothing keeps the two in
step but the code that writes them, and every writer that set only one half was
a defect nobody saw on the canvas:

- the offset tool and the corner tools copied brush and pen and left the
  attributes at the object type's defaults: an offset copy of a blue brick
  terrace was saved — and baked into the 3D ground — as default wood;
- the project loader set the pen and left ``stroke_color`` / ``stroke_width`` at
  the type's defaults, and for rectangles the fill colour too: the properties
  panel showed the default stroke for a loaded item, and a rectangle lost its
  colour on the next save.

So: whoever gives an item a pen calls ``adopt_pen``; whoever gives an item the
look of another item calls ``copy_visual_style``. ``style_gaps`` says where an
item's two statements part — the predicate a teardown fixture holds EVERY item
of every scene the 2D integration suites build to
(``tests/integration/conftest.py``), and
``tests/integration/test_item_style_in_step.py`` every operation that makes an
item out of an item.

ONE KIND OF ITEM IS LEFT OUT OF THE STROKE HALF: an item whose picture is a
sprite (``GardenItemMixin.paints_sprite`` — a furniture rectangle, a plant or
furniture circle). Its ``paint`` never asks the pen for the picture; its pen is
``NoPen`` when it is built and a solid black 1.0 once it was loaded (the saver
writes the pen's colour and width, the loader gives the pen the saved
``stroke_style``), while its stroke attributes are its TYPE's. Neither says
anything about what is painted, so neither is corrected by the other.
"""

from __future__ import annotations

import math
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor, QPen

from open_garden_planner.core.fill_patterns import FillPattern, create_pattern_brush
from open_garden_planner.core.object_types import StrokeStyle

__all__ = [
    "MIN_STROKE_WIDTH",
    "adopt_pen",
    "copy_visual_style",
    "fill_brush",
    "paints_sprite",
    "saved_stroke_colour",
    "saved_stroke_width",
    "stroke_style_of",
    "style_gaps",
]

#: The narrowest stroke the properties panel and the properties dialog can set
#: (both spin boxes start here).
MIN_STROKE_WIDTH = 0.5


def stroke_style_of(pen: QPen) -> StrokeStyle | None:
    """The ``StrokeStyle`` a pen draws with — None for a pen that draws nothing or
    with a dash pattern no ``StrokeStyle`` names."""
    style = pen.style()
    for candidate in StrokeStyle:
        if candidate.to_qt_pen_style() == style:
            return candidate
    return None


def paints_sprite(item: Any) -> bool:
    """Whether ``item``'s picture is a sprite and not its brush and pen — the item
    says so itself (``GardenItemMixin.paints_sprite``)."""
    return bool(getattr(item, "paints_sprite", False))


def saved_stroke_width(value: object) -> float | None:
    """A stroke width read from a file, if it is one the app could have written: a
    finite number not below ``MIN_STROKE_WIDTH`` — else None. Never raises."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        width = float(value)
    except OverflowError:  # an integer of 400 digits
        return None
    return width if math.isfinite(width) and width >= MIN_STROKE_WIDTH else None


def saved_stroke_colour(value: object) -> QColor | None:
    """A stroke colour read from a file, if it is a colour: a string Qt can read
    (``#aarrggbb``, ``#rrggbb``, a colour name) — else None. Never raises. A fully
    transparent colour IS one: the panel's colour dialog offers the alpha channel."""
    if not isinstance(value, str):
        return None
    colour = QColor(value)
    return colour if colour.isValid() else None


def fill_brush(item: Any, colour: QColor) -> QBrush:
    """The brush that paints ``colour`` in ``item``'s OWN pattern — the
    ``fill_pattern`` it states (plain for an item that states none). What
    ``style_gaps`` expects of an item whose ``fill_color`` is ``colour``."""
    pattern = getattr(item, "fill_pattern", None) or FillPattern.SOLID
    return create_pattern_brush(pattern, QColor(colour))


def adopt_pen(item: Any, pen: QPen) -> None:
    """Give ``item`` the pen — and say so in its stroke attributes.

    Two cases set the pen and leave the attributes alone: a pen that draws
    nothing (``NoPen`` states no stroke), and an item whose picture is a sprite
    (``paints_sprite`` — see the module docstring: its attributes stay its
    type's, as they were before this helper existed).
    """
    item.setPen(QPen(pen))
    if (pen.style() == Qt.PenStyle.NoPen or paints_sprite(item)
            or not hasattr(item, "stroke_color")):
        return
    item.stroke_color = QColor(pen.color())
    item.stroke_width = pen.widthF()
    style = stroke_style_of(pen)
    if style is not None and hasattr(item, "stroke_style"):
        item.stroke_style = style


def copy_visual_style(source: Any, dest: Any) -> None:
    """Make ``dest`` look like ``source``: brush, pen and area-label flag — and
    the style attributes that describe them.

    The fill attributes are the source's own (a textured brush cannot be asked
    for its pattern or its tint). A source that states no fill — a polyline —
    hands on what its brush is: a plain colour, or none at all (transparent).
    The stroke attributes are read off the pen that is copied.
    """
    brush = QBrush(source.brush())
    dest.setBrush(brush)
    if hasattr(dest, "fill_color"):
        colour = getattr(source, "fill_color", None)
        pattern = getattr(source, "fill_pattern", None)
        if colour is not None:
            dest.fill_color = QColor(colour)
            if pattern is not None:
                dest.fill_pattern = pattern
        elif brush.style() in (Qt.BrushStyle.NoBrush, Qt.BrushStyle.SolidPattern):
            dest.fill_color = (QColor(0, 0, 0, 0) if brush.style() == Qt.BrushStyle.NoBrush
                               else QColor(brush.color()))
            dest.fill_pattern = FillPattern.SOLID
    adopt_pen(dest, source.pen())
    if getattr(source, "area_label_visible", False):
        dest.area_label_visible = True


def style_gaps(item: Any) -> list[str]:
    """Where ``item``'s style attributes do NOT describe its live brush and pen —
    [] when they are in step. An attribute that is None states nothing and is
    not held against anything; a pen that draws nothing is not held against the
    stroke attributes, and neither is the pen of an item that paints a sprite
    (``paints_sprite``)."""
    gaps: list[str] = []
    pen = item.pen()
    if pen.style() != Qt.PenStyle.NoPen and not paints_sprite(item):
        colour = getattr(item, "stroke_color", None)
        if colour is not None and QColor(colour) != pen.color():
            gaps.append(f"stroke_color {QColor(colour).name(QColor.NameFormat.HexArgb)} "
                        f"!= pen {pen.color().name(QColor.NameFormat.HexArgb)}")
        width = getattr(item, "stroke_width", None)
        if width is not None and abs(float(width) - pen.widthF()) > 1e-9:
            gaps.append(f"stroke_width {width} != pen {pen.widthF()}")
        style = getattr(item, "stroke_style", None)
        if style is not None and style.to_qt_pen_style() != pen.style():
            gaps.append(f"stroke_style {style.name} != pen {pen.style().name}")
    colour = getattr(item, "fill_color", None)
    pattern = getattr(item, "fill_pattern", None)
    brush = item.brush()
    if colour is not None and pattern is not None and brush.style() != Qt.BrushStyle.NoBrush:
        expected = create_pattern_brush(pattern, QColor(colour))
        if not _same_brush(brush, expected):
            gaps.append(f"fill {pattern.name} {QColor(colour).name(QColor.NameFormat.HexArgb)} "
                        f"is not the brush ({brush.style().name}, "
                        f"{brush.color().name(QColor.NameFormat.HexArgb)})")
    return gaps


def _same_brush(first: QBrush, second: QBrush) -> bool:
    """Two brushes paint the same: style and transform, and the colour of a plain
    one or the PIXELS of a textured one (``QBrush.__eq__`` compares cache keys —
    the same texture made twice is two keys). Two plain brushes that are both
    fully transparent paint the same — nothing — whatever their colours: a line
    states its type's fill, ``#00808080`` for a wall, and paints with
    ``#00000000``."""
    if first.style() != second.style() or first.transform() != second.transform():
        return False
    if first.style() == Qt.BrushStyle.TexturePattern:
        return bool(first.textureImage() == second.textureImage())
    mine, theirs = first.color(), second.color()
    return bool(mine == theirs or (mine.alpha() == 0 and theirs.alpha() == 0))
