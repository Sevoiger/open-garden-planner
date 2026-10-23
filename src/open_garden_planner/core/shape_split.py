"""Split filled scene paths by an infinite axis, using Qt's boolean geometry."""

import math
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QPainterPath, QPolygonF

from open_garden_planner.core.shape_boolean import _polygon_area, item_to_painter_path

if TYPE_CHECKING:
    from open_garden_planner.ui.canvas.canvas_scene import CanvasScene


def split_path(path: QPainterPath, a: QPointF, b: QPointF) -> list[QPolygonF]:
    dx, dy = b.x() - a.x(), b.y() - a.y()
    length = math.hypot(dx, dy)
    if not math.isfinite(length) or length < 1e-6 or path.isEmpty():
        return []
    dx, dy = dx / length, dy / length
    nx, ny = -dy, dx
    bounds = path.boundingRect()
    corners = [bounds.topLeft(), bounds.topRight(), bounds.bottomLeft(), bounds.bottomRight()]
    ts = [(p.x() - a.x()) * dx + (p.y() - a.y()) * dy for p in corners]
    ss = [(p.x() - a.x()) * nx + (p.y() - a.y()) * ny for p in corners]
    pad = max(bounds.width(), bounds.height(), 1.0)

    def point(t: float, s: float) -> QPointF:
        return QPointF(a.x() + t * dx + s * nx, a.y() + t * dy + s * ny)

    sides = []
    for low, high in ((0.0, max(ss) + pad), (min(ss) - pad, 0.0)):
        if high <= low:
            return []
        clip = QPainterPath()
        clip.addPolygon(QPolygonF([point(min(ts) - pad, low), point(max(ts) + pad, low),
                                  point(max(ts) + pad, high), point(min(ts) - pad, high)]))
        clip.closeSubpath()
        polygons = [poly for poly in path.intersected(clip).simplified().toFillPolygons()
                    if _polygon_area(poly) > 1e-6]
        if not polygons:
            return []
        sides.extend(polygons)
    return sides


def can_split_shape(item: Any, scene: "CanvasScene") -> bool:
    """Replacement cannot silently discard identity, relationships or constraints."""
    from open_garden_planner.core.object_types import get_valid_types_for_shape, is_plant_type
    from open_garden_planner.ui.canvas.items import (
        CircleItem,
        EllipseItem,
        PolygonItem,
        RectangleItem,
    )

    if not isinstance(item, (CircleItem, EllipseItem, PolygonItem, RectangleItem)) or item.scene() is not scene:
        return False
    if (is_plant_type(item.object_type) or item.parentItem() is not None
            or item.parent_bed_id is not None or item.has_children
            or scene.constraint_graph.get_item_constraints(item.item_id)):
        return False
    if item.object_type is None:
        return False
    if item.object_type not in get_valid_types_for_shape('polygon') and item.object_type.name not in {
            'GENERIC_RECTANGLE', 'GENERIC_CIRCLE', 'GENERIC_ELLIPSE'}:
        return False
    layer = scene.get_layer_by_id(item.layer_id) if item.layer_id is not None else None
    if layer is not None and (layer.locked or not layer.visible):
        return False
    if any(key in item.metadata for key in ('plant_species', 'guild', 'garden_tracking',
                                           'ridge_item_id', 'owner_polygon_id')):
        return False
    identity = str(item.item_id)
    for other in scene.items():
        metadata = getattr(other, 'metadata', {})
        if not isinstance(metadata, dict):
            continue
        tracking = metadata.get('garden_tracking', {})
        if isinstance(tracking, dict) and tracking.get('zone_id') == identity:
            return False
        if any(metadata.get(key) == identity for key in ('ridge_item_id', 'owner_polygon_id')):
            return False
    for view in scene.views():
        manager = getattr(view.window(), '_project_manager', None)
        if manager is None:
            manager = getattr(getattr(view, '_soil_service', None), '_pm', None)
        if manager is not None and manager.has_item_references(item.item_id):
            return False
    return item_to_painter_path(item) is not None
