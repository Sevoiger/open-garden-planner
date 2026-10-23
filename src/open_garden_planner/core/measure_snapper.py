"""Anchor-point snapper for the measure tool.

Detects object anchor points (centers and edge midpoints) near a given
scene position, enabling precise object-to-object distance measurement.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

from PyQt6.QtCore import QPointF
from PyQt6.QtWidgets import QGraphicsItem

if TYPE_CHECKING:
    from open_garden_planner.core.constraints import Constraint


class AnchorType(Enum):
    """Type of anchor point on an object."""

    CENTER = auto()
    EDGE_TOP = auto()
    EDGE_BOTTOM = auto()
    EDGE_LEFT = auto()
    EDGE_RIGHT = auto()
    CORNER = auto()
    ENDPOINT = auto()


@dataclass
class AnchorPoint:
    """A snap-able anchor point on an object."""

    point: QPointF
    anchor_type: AnchorType
    item: QGraphicsItem
    anchor_index: int = 0  # Distinguishes same-type anchors (e.g. vertex 0 vs 2)


def get_anchor_points(item: QGraphicsItem) -> list[AnchorPoint]:
    """Compute all anchor points for a garden item in scene coordinates.

    Supports RectangleItem, CircleItem, PolygonItem, PolylineItem, and
    construction geometry items.
    Returns center plus edge midpoints appropriate for each type.

    Args:
        item: A graphics item on the scene.

    Returns:
        List of AnchorPoint instances in scene coordinates.
    """
    from open_garden_planner.ui.canvas.items import (
        ArcItem,
        CircleItem,
        EllipseItem,
        PolygonItem,
        PolylineItem,
        RectangleItem,
    )
    from open_garden_planner.ui.canvas.items.construction_item import (
        ConstructionCircleItem,
        ConstructionLineItem,
    )

    anchors: list[AnchorPoint] = []

    if isinstance(item, RectangleItem):
        anchors = _rect_anchors(item)
    elif isinstance(item, EllipseItem):
        anchors = _ellipse_anchors(item)
    elif isinstance(item, CircleItem):
        anchors = _circle_anchors(item)
    elif isinstance(item, PolygonItem):
        anchors = _polygon_anchors(item)
    elif isinstance(item, PolylineItem):
        anchors = _polyline_anchors(item)
    elif isinstance(item, ConstructionLineItem):
        anchors = _construction_line_anchors(item)
    elif isinstance(item, ConstructionCircleItem):
        anchors = _construction_circle_anchors(item)
    elif isinstance(item, ArcItem):
        anchors = _arc_anchors(item)

    return anchors


_EDGE_TYPES = frozenset({AnchorType.EDGE_TOP, AnchorType.EDGE_BOTTOM,
                         AnchorType.EDGE_LEFT, AnchorType.EDGE_RIGHT})


def resolve_anchor_point(item: QGraphicsItem, anchor_type: AnchorType,
                         anchor_index: int = 0) -> QPointF | None:
    """Resolve a stored reference without confusing rectangle sides with edge indices."""
    from open_garden_planner.ui.canvas.items import PolygonItem, PolylineItem

    anchors = get_anchor_points(item)
    indexed_edge = isinstance(item, (PolygonItem, PolylineItem)) and anchor_type in _EDGE_TYPES
    for anchor in anchors:
        if anchor.anchor_index == anchor_index and (
            anchor.anchor_type in _EDGE_TYPES if indexed_edge else anchor.anchor_type == anchor_type
        ):
            return anchor.point
    return None


def get_constraint_anchor_points(item: QGraphicsItem,
                                 constraints: Iterable[Constraint]) -> list[AnchorPoint]:
    """Include stored polygon edge names as aliases after an edge changes direction."""
    anchors = get_anchor_points(item)
    from open_garden_planner.ui.canvas.items import PolygonItem, PolylineItem

    if not isinstance(item, (PolygonItem, PolylineItem)):
        return anchors
    by_index = {a.anchor_index: a.point for a in anchors if a.anchor_type in _EDGE_TYPES}
    keys = {(a.anchor_type, a.anchor_index) for a in anchors}
    for constraint in constraints:
        for ref in (constraint.anchor_a, constraint.anchor_b, constraint.anchor_c):
            if (ref is None or ref.item_id != item.item_id or ref.anchor_type not in _EDGE_TYPES
                    or (ref.anchor_type, ref.anchor_index) in keys):
                continue
            point = by_index.get(ref.anchor_index)
            if point is not None:
                anchors.append(AnchorPoint(point, ref.anchor_type, item, ref.anchor_index))
                keys.add((ref.anchor_type, ref.anchor_index))
    return anchors


def _arc_anchors(item: QGraphicsItem) -> list[AnchorPoint]:
    """Anchors for an arc: center + two endpoints.

    The angular midpoint of the arc is exposed separately by
    ``MidpointSnapProvider`` (see ``core/snap/providers/midpoint.py``)
    so it gets the correct snap glyph and priority.
    """
    from open_garden_planner.ui.canvas.items import ArcItem

    assert isinstance(item, ArcItem)
    return [
        AnchorPoint(point=item.center, anchor_type=AnchorType.CENTER, item=item),
        AnchorPoint(
            point=item.start_point(), anchor_type=AnchorType.ENDPOINT, item=item,
            anchor_index=0,
        ),
        AnchorPoint(
            point=item.end_point(), anchor_type=AnchorType.ENDPOINT, item=item,
            anchor_index=1,
        ),
    ]


def _rect_anchors(item: QGraphicsItem) -> list[AnchorPoint]:
    """Get anchors for a rectangle item."""
    from open_garden_planner.ui.canvas.items import RectangleItem

    assert isinstance(item, RectangleItem)
    rect = item.rect()

    # Non-corner anchors (unique types, no index needed)
    single_anchors = [
        (QPointF(rect.center().x(), rect.center().y()), AnchorType.CENTER),
        (QPointF(rect.center().x(), rect.top()), AnchorType.EDGE_TOP),
        (QPointF(rect.center().x(), rect.bottom()), AnchorType.EDGE_BOTTOM),
        (QPointF(rect.left(), rect.center().y()), AnchorType.EDGE_LEFT),
        (QPointF(rect.right(), rect.center().y()), AnchorType.EDGE_RIGHT),
    ]

    anchors = [
        AnchorPoint(
            point=item.mapToScene(lp),
            anchor_type=at,
            item=item,
        )
        for lp, at in single_anchors
    ]

    # Corners need anchor_index to distinguish same-type anchors
    corners = [
        QPointF(rect.left(), rect.top()),      # 0: top-left
        QPointF(rect.right(), rect.top()),     # 1: top-right
        QPointF(rect.left(), rect.bottom()),   # 2: bottom-left
        QPointF(rect.right(), rect.bottom()),  # 3: bottom-right
    ]
    for i, corner in enumerate(corners):
        anchors.append(AnchorPoint(
            point=item.mapToScene(corner),
            anchor_type=AnchorType.CORNER,
            item=item,
            anchor_index=i,
        ))

    return anchors


def _circle_anchors(item: QGraphicsItem) -> list[AnchorPoint]:
    """Get anchors for a circle item."""
    from open_garden_planner.ui.canvas.items import CircleItem

    assert isinstance(item, CircleItem)
    rect = item.rect()
    cx = rect.x() + rect.width() / 2
    cy = rect.y() + rect.height() / 2

    local_points = [
        (QPointF(cx, cy), AnchorType.CENTER),
        (QPointF(cx, rect.top()), AnchorType.EDGE_TOP),
        (QPointF(cx, rect.bottom()), AnchorType.EDGE_BOTTOM),
        (QPointF(rect.left(), cy), AnchorType.EDGE_LEFT),
        (QPointF(rect.right(), cy), AnchorType.EDGE_RIGHT),
    ]

    return [
        AnchorPoint(
            point=item.mapToScene(lp),
            anchor_type=at,
            item=item,
        )
        for lp, at in local_points
    ]


def _ellipse_anchors(item: QGraphicsItem) -> list[AnchorPoint]:
    """Get anchors for an ellipse item: center + 4 quadrant points (N/E/S/W)."""
    from open_garden_planner.ui.canvas.items import EllipseItem

    assert isinstance(item, EllipseItem)
    rect = item.rect()
    cx = rect.x() + rect.width() / 2
    cy = rect.y() + rect.height() / 2

    local_points = [
        (QPointF(cx, cy), AnchorType.CENTER),
        (QPointF(cx, rect.top()), AnchorType.EDGE_TOP),
        (QPointF(cx, rect.bottom()), AnchorType.EDGE_BOTTOM),
        (QPointF(rect.left(), cy), AnchorType.EDGE_LEFT),
        (QPointF(rect.right(), cy), AnchorType.EDGE_RIGHT),
    ]

    return [
        AnchorPoint(
            point=item.mapToScene(lp),
            anchor_type=at,
            item=item,
        )
        for lp, at in local_points
    ]


def _polygon_anchors(item: QGraphicsItem) -> list[AnchorPoint]:
    """Get anchors for a polygon item.

    Returns center of bounding rect plus midpoints of each edge.
    """
    from open_garden_planner.ui.canvas.items import PolygonItem

    assert isinstance(item, PolygonItem)
    polygon = item.polygon()
    count = polygon.count()

    anchors: list[AnchorPoint] = []

    # Center of the polygon bounding rect
    brect = polygon.boundingRect()
    center = QPointF(brect.center().x(), brect.center().y())
    anchors.append(AnchorPoint(
        point=item.mapToScene(center),
        anchor_type=AnchorType.CENTER,
        item=item,
    ))

    # Vertices (corners)
    for i in range(count):
        vertex = polygon.at(i)
        anchors.append(AnchorPoint(
            point=item.mapToScene(vertex),
            anchor_type=AnchorType.CORNER,
            item=item,
            anchor_index=i,
        ))

    # Edge midpoints (including closing edge)
    if count >= 2:
        for i in range(count):
            p1 = polygon.at(i)
            p2 = polygon.at((i + 1) % count)
            mid = QPointF((p1.x() + p2.x()) / 2, (p1.y() + p2.y()) / 2)
            # Classify edge direction based on dominant axis
            dx = abs(p2.x() - p1.x())
            dy = abs(p2.y() - p1.y())
            if dy > dx:
                # More vertical edge -> LEFT or RIGHT
                at = AnchorType.EDGE_LEFT if mid.x() < center.x() else AnchorType.EDGE_RIGHT
            else:
                # More horizontal edge -> TOP or BOTTOM
                at = AnchorType.EDGE_TOP if mid.y() < center.y() else AnchorType.EDGE_BOTTOM
            anchor_type = at
            anchors.append(AnchorPoint(
                point=item.mapToScene(mid),
                anchor_type=anchor_type,
                item=item,
                anchor_index=i,
            ))

    return anchors


def _polyline_anchors(item: QGraphicsItem) -> list[AnchorPoint]:
    """Get anchors for a polyline item.

    Returns center of bounding rect, endpoints, and segment midpoints.
    """
    from open_garden_planner.ui.canvas.items import PolylineItem

    assert isinstance(item, PolylineItem)
    points = item.points

    anchors: list[AnchorPoint] = []

    if not points:
        return anchors

    # Center of the bounding rect
    brect = item.boundingRect()
    center = QPointF(brect.center().x(), brect.center().y())
    anchors.append(AnchorPoint(
        point=item.mapToScene(center),
        anchor_type=AnchorType.CENTER,
        item=item,
    ))

    # All vertices (endpoints and intermediate points)
    for i, pt in enumerate(points):
        anchors.append(AnchorPoint(
            point=item.mapToScene(pt),
            anchor_type=AnchorType.ENDPOINT,
            item=item,
            anchor_index=i,
        ))

    # Segment midpoints
    for i in range(len(points) - 1):
        p1 = points[i]
        p2 = points[i + 1]
        mid = QPointF((p1.x() + p2.x()) / 2, (p1.y() + p2.y()) / 2)
        # Classify edge direction based on dominant axis
        dx = abs(p2.x() - p1.x())
        dy = abs(p2.y() - p1.y())
        anchor_type = AnchorType.EDGE_LEFT if dy > dx else AnchorType.EDGE_TOP
        anchors.append(AnchorPoint(
            point=item.mapToScene(mid),
            anchor_type=anchor_type,
            item=item,
            anchor_index=i,
        ))

    return anchors


def _construction_line_anchors(item: QGraphicsItem) -> list[AnchorPoint]:
    """Get anchors for a construction line item (endpoints + midpoint)."""
    from open_garden_planner.ui.canvas.items.construction_item import ConstructionLineItem

    assert isinstance(item, ConstructionLineItem)
    line = item.line()
    scene_p1 = item.mapToScene(line.p1())
    scene_p2 = item.mapToScene(line.p2())
    mid = QPointF(
        (scene_p1.x() + scene_p2.x()) / 2,
        (scene_p1.y() + scene_p2.y()) / 2,
    )

    return [
        AnchorPoint(point=scene_p1, anchor_type=AnchorType.ENDPOINT, item=item, anchor_index=0),
        AnchorPoint(point=scene_p2, anchor_type=AnchorType.ENDPOINT, item=item, anchor_index=1),
        AnchorPoint(point=mid, anchor_type=AnchorType.CENTER, item=item),
    ]


def _construction_circle_anchors(item: QGraphicsItem) -> list[AnchorPoint]:
    """Get anchors for a construction circle item (center + 4 cardinal points)."""
    from open_garden_planner.ui.canvas.items.construction_item import ConstructionCircleItem

    assert isinstance(item, ConstructionCircleItem)
    rect = item.rect()
    cx = rect.x() + rect.width() / 2
    cy = rect.y() + rect.height() / 2

    local_points = [
        (QPointF(cx, cy), AnchorType.CENTER),
        (QPointF(cx, rect.top()), AnchorType.EDGE_TOP),
        (QPointF(cx, rect.bottom()), AnchorType.EDGE_BOTTOM),
        (QPointF(rect.left(), cy), AnchorType.EDGE_LEFT),
        (QPointF(rect.right(), cy), AnchorType.EDGE_RIGHT),
    ]

    return [
        AnchorPoint(point=item.mapToScene(lp), anchor_type=at, item=item)
        for lp, at in local_points
    ]


def find_nearest_anchor(
    scene_pos: QPointF,
    scene_items: list[QGraphicsItem],
    threshold: float = 15.0,
) -> AnchorPoint | None:
    """Find the nearest anchor point to a scene position within threshold.

    Args:
        scene_pos: The mouse position in scene coordinates.
        scene_items: All items in the scene.
        threshold: Maximum distance in scene units (cm) to snap.

    Returns:
        The nearest AnchorPoint, or None if nothing is within threshold.
    """
    from open_garden_planner.ui.canvas.items import GardenItemMixin
    from open_garden_planner.ui.canvas.items.construction_item import (
        ConstructionCircleItem,
        ConstructionLineItem,
    )

    best: AnchorPoint | None = None
    best_dist = threshold

    for item in scene_items:
        # Include garden items and construction geometry items
        is_garden = isinstance(item, GardenItemMixin)
        is_construction = isinstance(item, (ConstructionLineItem, ConstructionCircleItem))
        if not (is_garden or is_construction):
            continue
        # Skip items that aren't selectable (hidden/locked layers)
        if not (item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable):
            continue

        for anchor in get_anchor_points(item):
            dx = anchor.point.x() - scene_pos.x()
            dy = anchor.point.y() - scene_pos.y()
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best = anchor

    return best
