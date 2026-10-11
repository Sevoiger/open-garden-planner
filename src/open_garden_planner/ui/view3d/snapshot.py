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

Three things on record about the contract-v2 half (review of fix round 1):

- Its caches — ``_circle_ring``, ``_polyline_regions``, ``_polygon_regions``,
  ``_material`` — are module-global ``lru_cache``s keyed by whole rings and never
  cleared. Bounded (1,024 to 4,096 entries each), and small at plan sizes: an
  outline of 1,000 vertices is 16 kB per entry. A ring that is being edited
  adds an entry per tick and pushes the oldest out.
- This module is a second place that knows the clipper's scale
  (``CLIPPER_SCALE``; the first is ``core/shadow_geometry``): it resolves a
  self-crossing outline and sorts rings into regions itself. Both belong in ONE
  ``core`` function that the 2D footprints call as well — the follow-up that
  makes the 2D shadow of a closed stroke right should move them there.
- Until then 2D and 3D disagree ON PURPOSE for two shapes: a closed stroke (2D:
  every ring a solid; 3D: the stroke, with its hole) and a polygon that crosses
  itself (2D: the ring as drawn; 3D: the resolved regions).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Final

import numpy as np
import pyclipper
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QGraphicsScene

import open_garden_planner.core.scene3d.polygon as _polygon
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
    local_frame,
    quantize_cm,
    records_from_raw,
    ring_area,
)
from open_garden_planner.core.scene3d.polygon import (
    exceeds_work_bound,
    is_buildable_region,
    is_region_too_large,
    regions_area,
)
from open_garden_planner.core.scene3d.record import (
    MAX_LENGTH_CM,
    PARAM_OUTLINE_RESOLVED,
    RecordRangeError,
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
    item_footprints,
    item_outline,
)
from open_garden_planner.ui.canvas.items.polyline_item import PolylineItem

if TYPE_CHECKING:
    from open_garden_planner.ui.canvas.footprints import ItemOutline

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


class SkipReason(StrEnum):
    """Why an item got no record — a stable CODE. A UI maps it to a ``tr()`` text;
    the value is never shown to the user as it is."""

    #: Building its record raised (malformed metadata, geometry that is not finite …).
    RECORD_FAILED = "record_failed"
    #: Another item, lower in the paint order, already has this id.
    DUPLICATE_ID = "duplicate_id"
    #: Its outline cannot be turned into regions a builder can build
    #: (``core.scene3d.is_buildable_region``), not even after resolving it.
    OUTLINE_UNBUILDABLE = "outline_unbuildable"
    #: One of its lengths — height, width, radius, a coordinate, its position —
    #: is beyond ``core.scene3d.MAX_LENGTH_CM`` or infinite (a hand-edited plan).
    OUT_OF_RANGE = "out_of_range"


@dataclass(frozen=True, slots=True)
class SkippedItem:
    """A visible, typed canvas item that got no record, and why.

    Plain data, safe to keep: ``reason`` is a ``SkipReason`` code; of an exception
    it holds the type's name and the message as strings, never the exception
    itself (its traceback would keep the scene and the item alive). Two reports of
    the same failure are equal.
    """

    item_id: str
    kind: str
    reason: SkipReason
    error_type: str = ""  # the exception's class name; "" when none was raised
    error_message: str = ""


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
#: every mouse move. Bounded in memory AND in volume: once it is full nothing new
#: is logged for the rest of the session (one line says so) — clearing it instead
#: would log every broken item again on every snapshot.
_reported: set[tuple[str, str, str]] = set()
_REPORTED_MAX = 2048
_NO_MORE = ("", "no more", "")  # the marker of "the one line that says so was written"
#: The LOG's words for a code (English, for whoever reads ``app.log``; never shown in the UI).
_LOG_TEXT: dict[SkipReason, str] = {
    SkipReason.RECORD_FAILED: "its record could not be built",
    SkipReason.DUPLICATE_ID: "another item already has this id",
    SkipReason.OUTLINE_UNBUILDABLE: "its outline cannot be made into regions a builder can build",
    SkipReason.OUT_OF_RANGE: "one of its lengths is beyond what a plan can hold",
}


def _report(skipped: SkippedItem, into: list[SkippedItem] | None,
            error: BaseException | None = None) -> None:
    """Hand a skipped item to the caller's list (every time) and to the log (once).
    ``error``, the exception itself, goes to the log only."""
    if into is not None:
        into.append(skipped)
    key = (skipped.item_id, str(skipped.reason), skipped.error_type)
    if key in _reported:
        return
    if len(_reported) >= _REPORTED_MAX:
        if _NO_MORE not in _reported:
            _reported.add(_NO_MORE)
            _log.warning("3D snapshot: %d different failures have been logged; further ones are "
                         "left out of the log (they are still reported to the caller)",
                         _REPORTED_MAX)
        return
    _reported.add(key)
    line = "3D snapshot: %s item %r is left out of the 3D view: %s [%s]"
    words = (skipped.kind, skipped.item_id, _LOG_TEXT[skipped.reason], skipped.reason.value)
    if error is not None:
        _log.error(line, *words, exc_info=error)
    elif skipped.error_message:
        _log.warning(line + " — %s", *words, skipped.error_message)
    else:
        _log.warning(line, *words)


@dataclass(frozen=True, slots=True)
class _Refused:
    """Why an outline has no regions a builder can build — the words of the report."""

    why: str


#: Refused for its SIZE: cutting it would cost more than ``core.scene3d``'s work
#: bound. Nothing else is tried on such an outline — no other reading of it is smaller.
_TOO_LARGE: Final = _Refused(
    "the outline is too large for the 3D view: cutting it is beyond the work bound "
    "(core.scene3d.polygon.MAX_REGION_WORK)")
_NO_READING: Final = _Refused("the outline cannot be made into regions a builder can build")


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


class _UnbuildableOutline(ValueError):
    """An outline that cannot be turned into regions a builder can build."""


#: How far a region whose rings TOUCH is drawn in, in clipper units (1/1000 cm):
#: 0.002 cm, twenty micrometres. Where two parts of a resolved outline meet in a
#: single point — the pocket of a self-crossing polygon touching its outer ring,
#: a neck pinched to nothing — no triangulation is possible as it stands; drawn
#: in by this much the parts stand a hair apart and each can be built. Only such
#: outlines pay it; the rest keep their exact coordinates.
_SEPARATION: int = 2
#: A resolved outline is compared with the DRAWN one on a lattice of this many
#: points a side; points closer to a drawn edge than the tolerance are not
#: judged (the separation and the clipper grid both live there).
_LATTICE: int = 48
_OUTLINE_TOLERANCE_CM = 0.01


def _to_grid(ring: Ring) -> list[tuple[int, int]]:
    return [(round(x * CLIPPER_SCALE), round(y * CLIPPER_SCALE)) for x, y in ring]


def _tree_regions(tree: Any) -> tuple[Region, ...]:
    """The regions of a pyclipper PolyTree: every outer ring with the holes
    pyclipper itself says are its own — the hierarchy is the clipper's, not a
    guess from a point test. An island inside a hole is a region of its own.
    Orientation is set from the ring's PLACE in the tree, never read off the
    contour: pyclipper hands a split-off piece out the wrong way round now and
    then (measured)."""
    regions: list[Region] = []
    outers = list(tree.Childs)
    while outers:
        node = outers.pop(0)
        outer = _quantized((x / CLIPPER_SCALE, y / CLIPPER_SCALE) for x, y in node.Contour)
        holes: list[Ring] = []
        for child in node.Childs:
            hole = _quantized((x / CLIPPER_SCALE, y / CLIPPER_SCALE) for x, y in child.Contour)
            if len(hole) >= 3 and ring_area(hole) != 0.0:
                holes.append(hole if ring_area(hole) < 0.0 else hole[::-1])
            outers.extend(child.Childs)
        if len(outer) >= 3 and ring_area(outer) != 0.0:
            regions.append((ccw(outer), tuple(holes)))
    return tuple(regions)


def _judged(regions: tuple[Region, ...]) -> _Refused | None:
    """None when every region can be built; otherwise why not — at the first that cannot."""
    for outer, holes in regions:
        if not is_buildable_region(outer, holes):
            return _TOO_LARGE if is_region_too_large(outer, holes) else _NO_READING
    return None


def _buildable(regions: tuple[Region, ...]) -> bool:
    return _judged(regions) is None


def _inside_ring(px: Any, py: Any, ring: Ring) -> Any:
    """Even-odd point-in-ring for arrays of points."""
    inside = np.zeros(len(px), dtype=bool)
    x1, y1 = ring[-1]
    for x2, y2 in ring:
        if y1 != y2:
            inside ^= ((y1 > py) != (y2 > py)) & (px < (x2 - x1) * (py - y1) / (y2 - y1) + x1)
        x1, y1 = x2, y2
    return inside


def _matches_outline(outline: Ring, regions: tuple[Region, ...]) -> bool:
    """True when ``regions`` cover what the DRAWN outline fills by the non-zero
    rule and nothing else — checked where it can be checked without trusting
    the clipper: at ``_LATTICE`` x ``_LATTICE`` points over the outline's
    bounding box, each one's winding number about the drawn ring against its
    being inside a region. A reading that put a hole where there is ground, or
    lost a piece, differs at every lattice point of that piece."""
    xs = [x for x, _y in outline]
    ys = [y for _x, y in outline]
    steps = (np.arange(_LATTICE) + 0.5) / _LATTICE
    gx, gy = np.meshgrid(min(xs) + steps * (max(xs) - min(xs)), min(ys) + steps * (max(ys) - min(ys)))
    px, py = gx.ravel(), gy.ravel()
    winding = np.zeros(len(px), dtype=np.int64)
    judged = np.ones(len(px), dtype=bool)
    x1, y1 = outline[-1]
    for x2, y2 in outline:
        dx, dy = x2 - x1, y2 - y1
        if dx or dy:
            along = np.clip(((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy), 0.0, 1.0)
            judged &= np.hypot(px - (x1 + along * dx), py - (y1 + along * dy)) >= _OUTLINE_TOLERANCE_CM
        if y1 != y2:
            crossed = ((y1 > py) != (y2 > py)) & (px < dx * (py - y1) / dy + x1)
            winding += np.where(crossed, 1 if y2 > y1 else -1, 0)
        x1, y1 = x2, y2
    covered = np.zeros(len(px), dtype=bool)
    for outer, holes in regions:
        mine = _inside_ring(px, py, outer)
        for hole in holes:
            mine &= ~_inside_ring(px, py, hole)
        covered |= mine
    return bool(np.all((winding != 0)[judged] == covered[judged]))


def _clip_tree(subject: list[list[tuple[int, int]]], fill: int, *, strictly_simple: bool = False,
               band: list[list[tuple[int, int]]] | None = None) -> Any:
    """The union of ``subject`` under ``fill`` as a pyclipper PolyTree — minus a
    band ``_SEPARATION`` wide along every ring of ``band``, when one is given.
    None when no ring of ``subject`` has an area."""
    clipper = pyclipper.Pyclipper()
    clipper.StrictlySimple = strictly_simple
    try:
        clipper.AddPaths(subject, pyclipper.PT_SUBJECT, True)
    except pyclipper.ClipperException:
        return None
    if band is None:
        return clipper.Execute2(pyclipper.CT_UNION, fill, fill)
    stroke = pyclipper.PyclipperOffset()
    stroke.AddPaths(band, pyclipper.JT_SQUARE, pyclipper.ET_CLOSEDLINE)
    clipper.AddPaths(stroke.Execute(_SEPARATION), pyclipper.PT_CLIP, True)
    return clipper.Execute2(pyclipper.CT_DIFFERENCE, fill, pyclipper.PFT_NONZERO)


def _resolved_regions(rings: list[list[tuple[int, int]]], fill: int,
                      outline: Ring | None = None) -> tuple[Region, ...] | _Refused:
    """Rings on the clipper grid → regions a builder can build, or why not.

    Three readings of the rings' union under ``fill``, the first that holds:

    1. the union with ``StrictlySimple``, read as a PolyTree — rings that do not
       touch themselves, pyclipper's own hierarchy, the outline's own vertices;
    2. the plain union with a band of ``_SEPARATION`` cut away along its rings —
       for parts that meet in a point: drawn apart by a hair, each can be built;
    3. the rings themselves with that band cut away along every one of THEM —
       no reading of the clipper's is trusted, at the price of a cut along every
       drawn edge, also those inside the solid.

    "Holds" is checked, not assumed, because pyclipper's answer for an outline
    that retraces or touches itself is not always right (measured: a piece
    handed out the wrong way round, a hole hung under the wrong ring): every
    region must be buildable (``is_buildable_region``), and — for a drawn
    ``outline`` — the regions must cover exactly what the outline fills
    (``_matches_outline``); for a stroke (no outline) the regions' area must be
    the rings' own, less at most the band.

    ``()`` when nothing with an area is there; ``_NO_READING`` when no reading
    holds; ``_TOO_LARGE`` as soon as one reading is refused for its size — the
    others would only be refused again.
    """
    plain = _clip_tree(rings, fill)
    paths = pyclipper.PolyTreeToPaths(plain) if plain is not None else []
    if not paths:
        return ()
    scale2 = float(CLIPPER_SCALE) ** 2
    area = sum(pyclipper.Area(ring) for ring in rings) / scale2  # a stroke's rings are oriented
    rim = (sum(math.dist(path[i - 1], path[i]) for path in paths for i in range(len(path)))
           * (2.0 * _SEPARATION + 2.0) / scale2)  # both sides of the band, and the grid's own unit

    def holds(regions: tuple[Region, ...], *, drawn_in: bool) -> bool | _Refused:
        if not regions:
            return False
        refused = _judged(regions)
        if refused is not None:
            return refused if refused is _TOO_LARGE else False
        if outline is not None:
            return _matches_outline(outline, regions)
        got = regions_area(regions)
        return bool((area - rim if drawn_in else area - 1e-6) <= got <= area + 1e-6)

    strict = _clip_tree(rings, fill, strictly_simple=True)
    regions = _tree_regions(strict) if strict is not None else ()
    verdict = holds(regions, drawn_in=False)
    for band in (paths, rings):
        if verdict is True:
            return regions
        if verdict is _TOO_LARGE:
            return _TOO_LARGE
        regions = _tree_regions(_clip_tree(band if band is paths else rings, fill, band=band))
        verdict = holds(regions, drawn_in=True)
    if verdict is True:
        return regions
    return _TOO_LARGE if verdict is _TOO_LARGE else _NO_READING


@lru_cache(maxsize=2048)
def _polyline_regions(path: Ring, width_cm: float) -> tuple[Region, ...] | _Refused:
    """A polyline's item-local stroke as buildable regions, or why it has none.

    The centre line inflated by half its width (``polyline_footprint`` —
    pyclipper, a 1/1000 cm grid), its rings taken as they come and sorted into
    outer rings and the holes they enclose: a closed fence is one outer and one
    hole.

    Its SIZE is judged first, on the rings as they come back and before they are
    sorted or tested: the largest outer ring with every hole, as one region. A
    stroke refused for size — then, or while a region of it is cut — is not
    looked at again: refusing must cost less than building.

    A stroke that is not buildable for another reason goes through
    ``_resolved_regions``. That is no rare path: two butt ends that meet corner
    to corner — a fence drawn back to a point half its width beside its start —
    make pyclipper return ONE ring that touches itself, and every such fence
    takes it (the reading that holds is the stroke drawn in by ``_SEPARATION``:
    its outline 0.002 cm inside the drawn one, its two ends a hair apart). The
    regions are checked like any other; there is no flag for it in the record —
    a stroke's rings never were the drawn vertices, and what builds a fence or a
    wall reads ``path`` and ``path_width_cm``.
    """
    rings = [_quantized(ring) for ring in polyline_footprint(list(path), width_cm)]
    outers = [ring for ring in rings if len(ring) >= 3 and ring_area(ring) > 0.0]
    holes = [ring for ring in rings if len(ring) >= 3 and ring_area(ring) < 0.0]
    if outers and exceeds_work_bound(max(outers, key=len), holes):
        return _TOO_LARGE
    try:
        regions = group_rings(rings)
    except ValueError:
        regions = None
    if regions is not None:
        refused = _judged(regions)
        if refused is None:
            return regions
        if refused is _TOO_LARGE:
            return _TOO_LARGE
    # the stroke's rings are oriented (outers counter-clockwise, holes clockwise):
    # their non-zero union is the stroke
    return _resolved_regions([_to_grid(ring) for ring in rings if len(ring) >= 3],
                             pyclipper.PFT_NONZERO)


def _without_repeats(ring: Ring) -> Ring:
    """``ring`` without a vertex placed twice in a row (a doubled click, a closing
    vertex that repeats the first): the same outline, said once. The FIRST of
    such a run is the one kept, so the ring still starts where it was drawn."""
    kept: list[tuple[float, float]] = []
    for point in ring:
        if not kept or point != kept[-1]:
            kept.append(point)
    while len(kept) > 1 and kept[-1] == kept[0]:
        kept.pop()
    return tuple(kept) if len(kept) != len(ring) else ring


#: What comparing ONE reading of a resolved outline with the drawn one costs, in
#: ``core.scene3d``'s work units, per lattice point and vertex of the outline
#: (``_matches_outline``); up to three readings are compared. An outline that
#: crosses itself and has more vertices than this allows — about 1,000 — is not
#: resolved: it is refused for its size like any other.
_OUTLINE_CHECK_UNITS: Final = 20


def _resolving_is_too_much(vertices: int) -> bool:
    return 3 * _OUTLINE_CHECK_UNITS * _LATTICE * _LATTICE * vertices > _polygon.MAX_REGION_WORK


@lru_cache(maxsize=4096)
def _circle_regions(radius_cm: float) -> tuple[Region, ...] | _Refused:
    """A circle's footprint ring as its one region — asked the predicate like
    every other shape (a radius of 2e-7 cm leaves a ring on the record grid that
    is no polygon at all)."""
    ring = _circle_ring(radius_cm)
    return ((ring, ()),) if is_buildable_region(ring) else _NO_READING


@lru_cache(maxsize=2048)
def _polygon_regions(ring: Ring) -> tuple[tuple[Region, ...] | _Refused, bool]:
    """A drawn polygon as ``(regions, resolved)`` — or, for regions, why it has none.

    A simple polygon is itself, turned counter-clockwise, vertex for vertex
    (``resolved`` False) — also when a vertex was merely placed twice in a row.
    One that crosses or touches itself is RESOLVED: the non-zero rule — the rule
    the 2D shadow fills the caster's own footprint with — so the 3D solid stands
    on the ground the 2D shadow treats as built on; its vertices then sit on the
    clipper grid, it may be one ring, several, or rings with holes, and parts
    that met in a point are drawn in by ``_SEPARATION``. (The canvas PAINTS such
    a polygon with Qt's odd-even rule: the same for a bow-tie, not for a
    pentagram, whose centre the shadow fills and the paint leaves open.)
    """
    ring = _without_repeats(ring)
    if len(ring) < 3:
        return (), False
    outer = ccw(ring)
    # ``is_buildable_region`` judges the ring's SIZE before anything that costs —
    # the simplicity test included — so nothing has to be asked about it first
    if is_buildable_region(outer):  # a simple polygon that was cut, and the cut proven
        return ((outer, ()),), False
    if is_region_too_large(outer) or _resolving_is_too_much(len(ring)):
        return _TOO_LARGE, False  # refused for its size: never resolved
    return _resolved_regions([_to_grid(ring)], pyclipper.PFT_NONZERO, outline=ring), True


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
                                                                 float | None, bool]:
    """``(regions, path, path_width_cm, radius_cm, resolved)`` of an outline in
    the record's frame; no regions means the item covers no ground, ``resolved``
    that the regions are not the drawn outline (``PARAM_OUTLINE_RESOLVED``).
    Raises ``ValueError`` for geometry that is not finite and
    ``_UnbuildableOutline`` for an outline no builder could build."""
    if outline.shape == OUTLINE_CIRCLE:
        radius = outline.radius_cm
        if radius is None or math.isnan(radius):
            raise ValueError(f"a circle of radius {radius!r}")
        if abs(radius) > MAX_LENGTH_CM:  # before a ring of that size is made
            raise RecordRangeError(f"a radius of {radius!r} cm is beyond ±{MAX_LENGTH_CM:g} cm")
        if radius <= 0.0:
            return (), (), None, None, False
        radius = quantize_cm(radius)
        disc = _circle_regions(radius)
        if isinstance(disc, _Refused):
            raise _UnbuildableOutline(f"a circle of radius {radius!r} cm: {disc.why}")
        return disc, (), None, radius, False
    ring = frame.local_ring(outline.points)
    if not _finite_ring(ring):
        raise ValueError("geometry that is not finite")
    if any(abs(x) > MAX_LENGTH_CM or abs(y) > MAX_LENGTH_CM for x, y in ring):
        # before the clipper sees it: its integers end at 4.6e18, and a record
        # could not hold the result anyway
        raise RecordRangeError(f"an outline that reaches beyond ±{MAX_LENGTH_CM:g} cm")
    if outline.shape == OUTLINE_POLYLINE:
        width = outline.width_cm
        if width is None or math.isnan(width):
            raise ValueError(f"a stroke width of {width!r}")
        if abs(width) > MAX_LENGTH_CM:
            raise RecordRangeError(f"a stroke width of {width!r} cm is beyond ±{MAX_LENGTH_CM:g} cm")
        if len(ring) < 2:
            return (), ring, width, None, False
        stroke = _polyline_regions(ring, width)
        if isinstance(stroke, _Refused):
            raise _UnbuildableOutline(stroke.why)
        return stroke, ring, width, None, False
    if outline.shape == OUTLINE_POLYGON:
        regions, resolved = _polygon_regions(ring)
        if isinstance(regions, _Refused):
            raise _UnbuildableOutline(regions.why)
        return regions, (), None, None, resolved
    # an ellipse or a rectangle: convex by construction; a mirrored scene transform
    # (baked into the ring) turns it clockwise, and without an area it covers nothing
    if len(ring) < 3 or ring_area(ring) == 0.0:
        return (), (), None, None, False
    outer = ccw(ring)
    if not is_buildable_region(outer):  # … and asked like every other shape: a sliver of
        raise _UnbuildableOutline(_NO_READING.why)  # 2e-7 cm is no polygon on the record grid
    return ((outer, ()),), (), None, None, False


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
    regions, path, width, radius, resolved = _regions_of(outline, frame)
    if not regions:
        return None
    params: dict[str, ParamValue] = {PARAM_SEED: item_id}
    if resolved:
        params[PARAM_OUTLINE_RESOLVED] = True
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
    if kind == "HOUSE" and not resolved:  # a drawn ridge belongs to the DRAWN outline
        ridge_id = metadata.get("ridge_item_id")
        if ridge_id:
            ridge = _ridge_param(frame, lookup.get(ridge_id))
            if ridge is not None:
                params[PARAM_RIDGE] = ridge
    stroke = getattr(item, "stroke_color", None)
    if stroke is None and shape == OUTLINE_POLYLINE:
        stroke = item.pen().color()  # the line's visible colour: a preset-less polyline
    height = effective_height_cm(object_type, metadata, at_date=at_date)
    if height is not None and height <= 0.0:
        height = None  # no height. One that is no usable number — 1e300, 1e-300 — is the Record's to refuse.
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
    contained to that item), a second item carrying an id already taken (the
    lower one wins), an item whose outline cannot be turned into regions a
    builder can build, and an item with a length beyond
    ``core.scene3d.MAX_LENGTH_CM`` — a height of 1e300 from a hand-edited plan
    is a finite number and an infinite float32; such an item gets no record
    rather than a solid nobody can draw. One bad item never blanks the view.

    What a record holds, and why (``core.scene3d.record``, §8.26.1):

    - geometry in the ITEM-LOCAL frame, centred on the outline's anchor
      (``ui.canvas.footprints.item_outline``), on the record grid; the pose
      (``transform``) from the item's ``sceneTransform()``. A circle's rotation
      is 0 by construction: its footprint does not turn. A scene transform that
      is not rigid (an item in a scaled group) is baked into the local geometry.
    - ``footprints`` are simple OUTER rings, counter-clockwise, and ``holes``
      what is cut out of them: a polyline's stroke is sorted into both (a closed
      fence is one outer and one hole), a polygon that crosses itself is
      resolved by the non-zero rule, the 2D shadow's rule for a footprint —
      and says so in ``params`` (``outline_resolved``). EVERY region of a record
      satisfies ``core.scene3d.is_buildable_region``: the default builder's own
      triangulation has cut it.
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
        except RecordRangeError as error:  # a length beyond what a plan can hold
            _report(SkippedItem(item_id, kind, SkipReason.OUT_OF_RANGE,
                                type(error).__name__, str(error)), skipped)
            continue
        except _UnbuildableOutline as error:
            _report(SkippedItem(item_id, kind, SkipReason.OUTLINE_UNBUILDABLE,
                                type(error).__name__, str(error)), skipped)
            continue
        except Exception as error:  # one bad item must not blank the view (docstring)
            _report(SkippedItem(item_id, kind, SkipReason.RECORD_FAILED,
                                type(error).__name__, str(error)), skipped, error)
            continue
        if record is None:
            continue
        if item_id in records:
            _report(SkippedItem(item_id, kind, SkipReason.DUPLICATE_ID), skipped)
            continue
        records[item_id] = record
    return records
