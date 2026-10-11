"""Plan snapshot for the 3D view — live items → plain data.

Runs on the GUI thread; the output contains no Qt object references (the
same snapshot-boundary discipline as the US-E4 worker). Footprints reuse
the US-E3 extraction (rotation/position are Qt's answer via ``mapToScene``)
so the 3D solids can never disagree with the 2D shadow footprints.

Two collectors live here until L1.10 removes Qt 3D:

- ``collect_scene3d_records`` (US-E6) feeds the shipped Qt 3D window with
  scene-space prisms and decals — unchanged.
- ``snapshot_records`` (Phase 17 L1.1, #385, ADR-054) produces contract v2:
  one ``core.scene3d.Record`` per item, in the item-local frame with a rigid
  transform, for ``SceneSync`` and the Qt Quick 3D sink.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Any

import pyclipper
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QGraphicsScene

from open_garden_planner.core.container_model import container_material
from open_garden_planner.core.object_height import effective_height_cm
from open_garden_planner.core.object_types import PathFenceStyle, is_container_type
from open_garden_planner.core.scene3d import (
    PARAM_CONTAINER_MATERIAL,
    PARAM_PATH_STYLE,
    PARAM_PLANT_CATEGORY,
    PARAM_RADIUS_CM,
    PARAM_RIDGE,
    PARAM_SEED,
    PARAM_SPECIES_KEY,
    PARAM_SPECIES_NAME,
    LocalFrame,
    Material,
    Params,
    ParamValue,
    Record,
    Region,
    Rgba,
    Ring,
    Scene3DRecord,
    ccw,
    group_rings,
    is_simple_ring,
    local_frame,
    quantize_cm,
    records_from_raw,
    ring_area,
)
from open_garden_planner.core.shadow_geometry import (
    CLIPPER_SCALE,
    circle_footprint,
    polyline_footprint,
)
from open_garden_planner.models.plant_data import species_key
from open_garden_planner.ui.canvas.footprints import (
    OUTLINE_CIRCLE,
    OUTLINE_POLYGON,
    OUTLINE_POLYLINE,
    ItemOutline,
    item_footprints,
    item_outline,
)
from open_garden_planner.ui.canvas.items.polyline_item import PolylineItem

_log = logging.getLogger(__name__)

_FALLBACK_COLOR = (158, 158, 148, 255)


def _item_color_rgba(item: Any) -> tuple[int, int, int, int]:
    color = getattr(item, "fill_color", None)
    if isinstance(color, str):
        color = QColor(color)
    if isinstance(color, QColor) and color.isValid():
        return (color.red(), color.green(), color.blue(), 255)
    return _FALLBACK_COLOR


def collect_scene3d_records(
    scene: QGraphicsScene, at_date: date | None = None
) -> list[Scene3DRecord]:
    """Every visible item with a footprint → an engine-ready record.

    Items with an effective height become extruded prisms; the rest
    (lawns, paths, in-ground beds …) become thin ground decals, so the
    2D layout stays recognizable from above. ``at_date`` (US-E8) projects
    dated plants to their grown height and canopy spread — scrub the sim
    date and trees grow in 3D.
    """
    raw: list[dict[str, Any]] = []
    # Bottom-to-top stacking order (scene.items() is top-first) so flat decals
    # can be lifted up the 2D layer order — a higher layer renders on top.
    for item in reversed(scene.items()):
        if not item.isVisible():
            continue
        object_type = getattr(item, "object_type", None)
        if object_type is None:
            continue
        metadata = getattr(item, "metadata", None)
        height = effective_height_cm(object_type, metadata, at_date=at_date)
        color = _item_color_rgba(item)
        name = str(getattr(item, "name", "") or object_type.name)
        for footprint in item_footprints(item, at_date):
            if len(footprint) >= 3:
                raw.append(
                    {
                        "footprint": footprint,
                        "height_cm": height,
                        "color_rgba": color,
                        "name": name,
                    }
                )
    return records_from_raw(raw)


# ── contract v2 (Phase 17 L1.1) ──────────────────────────────────────────────

#: A ROOF_RIDGE is not an object of its own in 3D: it reaches the pipeline only
#: as its HOUSE's ``ridge`` param. The app re-derives the ridge item's points
#: whenever the house turns, so a record of its own would change GEOMETRY on a
#: rotation of the house (measured: on 400 of 400) — "rotate → transform only"
#: would be false for the one item a house owns.
_NO_RECORD_KINDS = frozenset({"ROOF_RIDGE"})


@dataclass(frozen=True, slots=True)
class SkippedItem:
    """A visible, typed canvas item that got no record, and why.

    ``reason`` is a short English phrase for logs and diagnostics (never shown
    to the user as it is); ``error`` the exception, when one was raised.
    """

    item_id: str
    kind: str
    reason: str
    error: Exception | None = None


class _Lookup:
    """Items by id, built on first use (only a HOUSE with a ridge asks today)."""

    def __init__(self, items: list[Any]) -> None:
        self._items = items
        self._by_id: dict[str, Any] | None = None

    def get(self, item_id: object) -> Any:
        if self._by_id is None:
            self._by_id = {str(other.item_id): other for other in self._items
                           if hasattr(other, "item_id")}
        return self._by_id.get(str(item_id))


#: What has been logged already — one line per item and KIND of failure (the
#: reason and the exception's type), not one per edit: the pipeline snapshots on
#: every change, and an exception message may carry a value that changes with
#: every mouse move. Bounded: cleared when it overflows.
_reported: set[tuple[str, str, str]] = set()
_REPORTED_MAX = 2048


def _report(skipped: SkippedItem, into: list[SkippedItem] | None) -> None:
    """Hand a skipped item to the caller's list (every time) and to the log (once)."""
    if into is not None:
        into.append(skipped)
    key = (skipped.item_id, skipped.reason, type(skipped.error).__name__)
    if key in _reported:
        return
    if len(_reported) >= _REPORTED_MAX:
        _reported.clear()
    _reported.add(key)
    if skipped.error is not None:
        _log.error("3D snapshot: %s item %r is left out of the 3D view (%s)",
                   skipped.kind, skipped.item_id, skipped.reason, exc_info=skipped.error)
    else:
        _log.warning("3D snapshot: %s item %r is left out of the 3D view (%s)",
                     skipped.kind, skipped.item_id, skipped.reason)


@lru_cache(maxsize=4096)
def _circle_ring(radius_cm: float) -> Ring:
    """A circle's item-local footprint: ``circle_footprint`` about the origin.

    Cached: a plan has few distinct radii, and the ring is a function of the
    radius alone (a circle's footprint does not turn with its item).
    """
    return tuple((quantize_cm(x), quantize_cm(y))
                 for x, y in circle_footprint(0.0, 0.0, radius_cm))


def _quantized(ring: Any) -> Ring:
    return tuple((quantize_cm(x), quantize_cm(y)) for x, y in ring)


@lru_cache(maxsize=2048)
def _polyline_regions(path: Ring, width_cm: float) -> tuple[Region, ...]:
    """A polyline's item-local stroke: the centre line inflated by half its width
    (``polyline_footprint`` — pyclipper, a 1/1000 cm grid), sorted into outer
    rings and the holes they enclose. A closed fence is one outer and one hole."""
    return group_rings([_quantized(ring) for ring in polyline_footprint(list(path), width_cm)])


def _resolved(ring: Ring) -> list[Ring]:
    """A self-crossing ring as simple rings (outers counter-clockwise, holes
    clockwise), by pyclipper's NON-ZERO rule on the 2D shadow's integer grid.
    ``[]`` when nothing with an area is left."""
    scaled = [(round(x * CLIPPER_SCALE), round(y * CLIPPER_SCALE)) for x, y in ring]
    return [_quantized((x / CLIPPER_SCALE, y / CLIPPER_SCALE) for x, y in path)
            for path in pyclipper.SimplifyPolygon(scaled, pyclipper.PFT_NONZERO) if len(path) >= 3]


@lru_cache(maxsize=2048)
def _polygon_regions(ring: Ring) -> tuple[Region, ...]:
    """A drawn polygon as simple rings. A simple one is itself, turned
    counter-clockwise. One that crosses itself (a bow-tie) is resolved with the
    non-zero rule — the rule the 2D shadow fills the caster's own footprint
    with — so the 3D solid stands on the ground the 2D shadow treats as built
    on; its vertices then sit on the clipper grid. (The canvas PAINTS such a
    polygon with Qt's odd-even rule: the same for a bow-tie, not for a
    pentagram, whose centre the shadow fills and the paint leaves open.)"""
    if len(ring) < 3:
        return ()
    if is_simple_ring(ring):
        return ((ccw(ring), ()),)
    return group_rings(_resolved(ring))


@lru_cache(maxsize=1024)
def _material(key: str, fill: Rgba | None, stroke: Rgba | None, pattern: str | None) -> Material:
    return Material(key, fill, stroke, pattern)


def _rgba(color: Any) -> Rgba | None:
    if isinstance(color, str):
        color = QColor(color)
    if isinstance(color, QColor) and color.isValid():
        red, green, blue, alpha = color.getRgb()
        return (red, green, blue, alpha)
    return None


def _finite_ring(ring: Ring) -> bool:
    return math.isfinite(sum(x + y for x, y in ring))


def _frame_of(item: Any, anchor: tuple[float, float], *, rotatable: bool) -> LocalFrame:
    """The item's scene transform — Qt's ``sceneTransform()``, never a
    re-derivation from pos / rotation / transformOriginPoint — split about ``anchor``."""
    matrix = item.sceneTransform()
    return local_frame(matrix.m11(), matrix.m12(), matrix.m21(), matrix.m22(),
                       matrix.dx(), matrix.dy(), anchor[0], anchor[1], rotatable=rotatable)


def _ridge_param(house_frame: LocalFrame, ridge: Any) -> ParamValue | None:
    """A HOUSE's ridge ends in the HOUSE's local frame, from the LIVE ridge item
    (ADR-046: an edited ridge is the ridge), or None when there is no usable one.

    The ridge is ANOTHER item's geometry, and the app re-writes its points by
    ``+ delta`` on every move of the house, so its float error grows with each
    move. Measured: 1.1e-10 cm after 20,000 random moves — three orders of
    magnitude below the record grid, so it never reaches the HOUSE's geometry
    signature (0 changes in those 20,000 moves; pinned over 1,000 by
    ``test_scene3d_snapshot.py::TestHouseRidge``).
    """
    if not isinstance(ridge, PolylineItem):
        return None
    points = ridge.points
    if len(points) < 2:
        return None
    ends: list[tuple[float, float]] = []
    for point in (points[0], points[-1]):
        scene_point = ridge.mapToScene(point)
        end = house_frame.local_from_scene(scene_point.x(), scene_point.y())  # on the record grid
        if not (math.isfinite(end[0]) and math.isfinite(end[1])):
            return None
        ends.append(end)
    return (ends[0], ends[1])


def _regions_of(outline: ItemOutline, frame: LocalFrame) -> tuple[tuple[Region, ...], Ring, float | None,
                                                                 float | None]:
    """``(regions, path, path_width_cm, radius_cm)`` of an outline in the record's
    frame; no regions means the item covers no ground. Raises ``ValueError`` for
    geometry that is not finite."""
    if outline.shape == OUTLINE_CIRCLE:
        radius = outline.radius_cm
        if radius is None or not math.isfinite(radius):
            raise ValueError(f"a circle of radius {radius!r}")
        if radius <= 0.0:
            return (), (), None, None
        radius = quantize_cm(radius)
        return ((_circle_ring(radius), ()),), (), None, radius
    ring = frame.local_ring(outline.points)
    if not _finite_ring(ring):
        raise ValueError("geometry that is not finite")
    if outline.shape == OUTLINE_POLYLINE:
        width = outline.width_cm
        if width is None or not math.isfinite(width):
            raise ValueError(f"a stroke width of {width!r}")
        if len(ring) < 2:
            return (), ring, width, None
        return _polyline_regions(ring, width), ring, width, None
    if outline.shape == OUTLINE_POLYGON:
        return _polygon_regions(ring), (), None, None
    # an ellipse or a rectangle: convex by construction; a mirrored scene transform
    # (baked into the ring) turns it clockwise, and without an area it covers nothing
    if len(ring) < 3 or ring_area(ring) == 0.0:
        return (), (), None, None
    return ((ccw(ring), ()),), (), None, None


def _record_of(item: Any, object_type: Any, item_id: str, at_date: date | None,
               lookup: _Lookup) -> Record | None:
    """One item → its record; None when it covers no ground (not an error).
    Anything it raises is contained by ``snapshot_records``. ``lookup`` finds
    another item by id (a HOUSE's ridge)."""
    outline = item_outline(item, at_date)
    if outline is None:
        return None
    kind: str = object_type.name
    shape = outline.shape
    frame = _frame_of(item, outline.anchor, rotatable=shape != OUTLINE_CIRCLE)
    if not math.isfinite(frame.east_cm + frame.north_cm):
        raise ValueError("a position that is not finite")
    regions, path, width, radius = _regions_of(outline, frame)
    if not regions:
        return None
    params: dict[str, ParamValue] = {PARAM_SEED: item_id}
    if radius is not None:
        params[PARAM_RADIUS_CM] = radius
    if shape == OUTLINE_POLYLINE:
        style = getattr(item, "path_fence_style", PathFenceStyle.NONE)
        if style is not PathFenceStyle.NONE:
            params[PARAM_PATH_STYLE] = style.name
    metadata = getattr(item, "metadata", None) or {}
    species = metadata.get("plant_species")
    species_name = ""
    if isinstance(species, dict):
        params[PARAM_SPECIES_KEY] = species_key(species)
        species_name = str(species.get("common_name") or "")
    species_name = (species_name or str(getattr(item, "plant_species", "") or "")).strip()
    if species_name:
        params[PARAM_SPECIES_NAME] = species_name
    category = getattr(item, "plant_category", None)
    if category is not None:
        params[PARAM_PLANT_CATEGORY] = category.name
    if is_container_type(object_type):
        params[PARAM_CONTAINER_MATERIAL] = container_material(metadata)
    if kind == "HOUSE":
        ridge_id = metadata.get("ridge_item_id")
        if ridge_id:
            ridge = _ridge_param(frame, lookup.get(ridge_id))
            if ridge is not None:
                params[PARAM_RIDGE] = ridge
    stroke = getattr(item, "stroke_color", None)
    if stroke is None and shape == OUTLINE_POLYLINE:
        stroke = item.pen().color()  # the line's visible colour: a preset-less polyline
    height = effective_height_cm(object_type, metadata, at_date=at_date)
    if height is not None and not 0.0 < height < math.inf:
        height = None  # e.g. a hand-edited "Infinity": not a height, so no solid
    pattern = getattr(item, "fill_pattern", None)
    parent = getattr(item, "parent_bed_id", None)
    holes = tuple(region_holes for _outer, region_holes in regions)
    return Record(
        item_id=item_id,
        kind=kind,
        shape=shape,
        footprints=tuple(outer for outer, _holes in regions),
        transform=frame.transform(),
        material=_material(kind, _rgba(getattr(item, "fill_color", None)), _rgba(stroke),
                           pattern.name if pattern is not None else None),
        height_cm=height,
        path=path,
        path_width_cm=width,
        params=Params(params),
        parent_id=str(parent) if parent is not None else None,
        name=str(getattr(item, "name", "") or ""),
        holes=holes if any(holes) else (),
    )


def snapshot_records(
    scene: QGraphicsScene,
    at_date: date | None = None,
    *,
    skipped: list[SkippedItem] | None = None,
) -> dict[str, Record]:
    """Every visible item with a footprint → one contract-v2 ``Record``.

    Returns a dict keyed by item id, insertion-ordered BOTTOM TO TOP (paint
    order). Runs on the GUI thread; the records are plain immutable data.

    ``skipped``: pass a list to learn what had to be left out because something
    is WRONG with it — one ``SkippedItem`` is appended per such item, in paint
    order (the list is not cleared first; without one the report goes to the log
    only). It stays empty on a healthy plan.

    Left out by design, silently: invisible items (as ``collect_scene3d_records``
    does), items without an ``object_type``, items that cover no ground (text,
    groups, arcs, a polyline shorter than two points, a shape without an area),
    and ``ROOF_RIDGE`` items (a ridge reaches 3D as its HOUSE's ``ridge`` param).

    Left out because something is wrong, and REPORTED — in ``skipped`` every
    time, and in the log once per item and kind of failure: an item whose
    record could not be built
    (malformed metadata, geometry that is not finite — any exception is
    contained to that item) and a second item carrying an id already taken (the
    lower one wins). One bad item never blanks the view.

    What a record holds, and why (``core.scene3d.record``, §8.26.1):

    - geometry in the ITEM-LOCAL frame, centred on the outline's anchor
      (``ui.canvas.footprints.item_outline``), on the record grid; the pose
      (``transform``) from the item's ``sceneTransform()``. A circle's rotation
      is 0 by construction: its footprint does not turn. A scene transform that
      is not rigid (an item in a scaled group) is baked into the local geometry.
    - ``footprints`` are simple OUTER rings, counter-clockwise, and ``holes``
      what is cut out of them: a polyline's stroke is sorted into both (a closed
      fence is one outer and one hole), a self-crossing polygon is resolved into
      simple rings by the non-zero rule, the 2D shadow's rule for a footprint.
    - ``height_cm``: ``effective_height_cm(..., at_date)`` — the 2D shadow's
      resolver; None means decoration that casts no shadow.
    - ``transform.base_cm`` is 0.0 here; L1.6 stands a plant on its parent bed
      (``parent_id``) with it.
    - ``at_date`` (US-E8) grows dated plants: canopy radius and height.
    """
    items: list[Any] = scene.items()  # top-first; garden items are duck-typed below
    lookup = _Lookup(items)
    records: dict[str, Record] = {}
    for item in reversed(items):
        if not item.isVisible():
            continue
        object_type = getattr(item, "object_type", None)
        item_uuid = getattr(item, "item_id", None)
        if object_type is None or item_uuid is None:
            continue
        kind = str(getattr(object_type, "name", object_type))
        if kind in _NO_RECORD_KINDS:
            continue
        item_id = str(item_uuid)
        try:
            record = _record_of(item, object_type, item_id, at_date, lookup)
        except Exception as error:  # one bad item must not blank the view (docstring)
            _report(SkippedItem(item_id, kind, "its record could not be built", error), skipped)
            continue
        if record is None:
            continue
        if item_id in records:
            _report(SkippedItem(item_id, kind, "another item already has this id"), skipped)
            continue
        records[item_id] = record
    return records
