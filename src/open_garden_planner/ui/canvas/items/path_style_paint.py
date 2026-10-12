"""How a styled path or fence line is painted — from plain values.

The ten style painters of ``PolylineItem`` (gravel path, stepping stones, paved
path, boardwalk, dirt path; wooden, metal and chain-link fence, hedge, stone
wall), moved here unchanged so that ONE implementation serves two callers:

- ``PolylineItem.paint`` for the live canvas item, and
- the 3D ground bake (``ui/view3d/ground_bake.py``, Phase 17 L1.5), which paints
  a path from a scene record and must not touch the live scene.

A painter reads three things and nothing else: the polyline's points in the
ITEM's own coordinates (two styles derive a colour from them), its pen (width
and colour) and its path. Selection is the item's own business and is drawn by
the item, after the style.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Sequence

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen

from open_garden_planner.core.object_types import PathFenceStyle


class PathStylePaint:
    """Paints one polyline in a path / fence style (module docstring).

    ``points``: the polyline in item coordinates. ``pen``: the item's pen.
    ``path``: the item's path through those points (built from them when not
    given).
    """

    def __init__(self, points: Sequence[QPointF], pen: QPen,
                 path: QPainterPath | None = None) -> None:
        self._points = list(points)
        self._pen = pen
        if path is None:
            path = QPainterPath()
            if self._points:
                path.moveTo(self._points[0])
                for point in self._points[1:]:
                    path.lineTo(point)
        self._path = path

    def pen(self) -> QPen:
        return self._pen

    def path(self) -> QPainterPath:
        return self._path

    def paint(self, painter: QPainter, style: PathFenceStyle) -> bool:
        """Paint the polyline in ``style``. False — and nothing painted — for
        ``PathFenceStyle.NONE`` and for a style without a painter of its own: the
        caller then strokes the plain path."""
        painters: dict[PathFenceStyle, Callable[[QPainter], None]] = {
            PathFenceStyle.GRAVEL_PATH: self._paint_gravel_path,
            PathFenceStyle.STEPPING_STONES: self._paint_stepping_stones,
            PathFenceStyle.PAVED_PATH: self._paint_paved_path,
            PathFenceStyle.WOODEN_BOARDWALK: self._paint_wooden_boardwalk,
            PathFenceStyle.DIRT_PATH: self._paint_dirt_path,
            PathFenceStyle.WOODEN_FENCE: self._paint_wooden_fence,
            PathFenceStyle.METAL_FENCE: self._paint_metal_fence,
            PathFenceStyle.CHAIN_LINK: self._paint_chain_link,
            PathFenceStyle.HEDGE_FENCE: self._paint_hedge_fence,
            PathFenceStyle.STONE_WALL: self._paint_stone_wall,
        }
        style_painter = painters.get(style)
        if style_painter is None:
            return False
        style_painter(painter)
        return True

    def _get_segment_normal(self, p1: QPointF, p2: QPointF) -> tuple[float, float]:
        """Get unit normal vector perpendicular to segment p1->p2."""
        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        length = math.hypot(dx, dy)
        if length < 0.001:
            return (0.0, 1.0)
        return (-dy / length, dx / length)

    def _get_segment_direction(self, p1: QPointF, p2: QPointF) -> tuple[float, float]:
        """Get unit direction vector from p1 to p2."""
        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        length = math.hypot(dx, dy)
        if length < 0.001:
            return (1.0, 0.0)
        return (dx / length, dy / length)

    # ── Path style renderers ──

    def _paint_gravel_path(self, painter: QPainter) -> None:
        """Gravel path: wide stippled stroke with border lines."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.pen().widthF()
        color = self.pen().color()

        # Wide base fill
        base_pen = QPen(QColor(color.red(), color.green(), color.blue(), 100))
        base_pen.setWidthF(width)
        base_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        base_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(base_pen)
        painter.drawPath(self.path())

        # Border lines
        border_pen = QPen(QColor(color.red() - 30, color.green() - 30, color.blue() - 30))
        border_pen.setWidthF(1.0)
        for offset_sign in (-1, 1):
            offset_path = self._offset_path(width / 2.0 * offset_sign)
            if offset_path:
                painter.setPen(border_pen)
                painter.drawPath(offset_path)

        # Stipple dots along the path
        dot_pen = QPen(Qt.PenStyle.NoPen)
        painter.setPen(dot_pen)
        dot_colors = [
            QColor(color.red() + 20, color.green() + 15, color.blue() + 10),
            QColor(color.red() - 10, color.green() - 15, color.blue() - 10),
            QColor(color.red(), color.green() - 5, color.blue() + 5),
        ]
        spacing = 4.0
        for i, (pt, _dir_x, _dir_y, nx, ny) in enumerate(self._walk_path(spacing)):
            dot_color = dot_colors[i % len(dot_colors)]
            painter.setBrush(QBrush(dot_color))
            # Vary position slightly across width
            offset = ((i * 7) % 5 - 2) * width / 6.0
            cx = pt.x() + nx * offset
            cy = pt.y() + ny * offset
            r = 1.0 + (i % 3) * 0.5
            painter.drawEllipse(QPointF(cx, cy), r, r)

        painter.restore()

    def _paint_stepping_stones(self, painter: QPainter) -> None:
        """Stepping stones: rounded rectangles spaced along the path."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.pen().widthF()
        color = self.pen().color()

        stone_spacing = width * 1.8
        stone_w = width * 0.8
        stone_h = width * 0.6

        for pt, dir_x, dir_y, _nx, _ny in self._walk_path(stone_spacing):
            painter.save()
            painter.translate(pt.x(), pt.y())
            angle = math.degrees(math.atan2(dir_y, dir_x))
            painter.rotate(angle)

            stone_color = QColor(
                color.red() + ((int(pt.x()) * 7) % 15 - 7),
                color.green() + ((int(pt.y()) * 11) % 15 - 7),
                color.blue() + ((int(pt.x() + pt.y()) * 13) % 15 - 7),
            )
            painter.setBrush(QBrush(stone_color))
            painter.setPen(QPen(stone_color.darker(130), 0.5))
            painter.drawRoundedRect(
                QRectF(-stone_w / 2, -stone_h / 2, stone_w, stone_h),
                2.0, 2.0,
            )
            painter.restore()

        painter.restore()

    def _paint_paved_path(self, painter: QPainter) -> None:
        """Paved path: solid wide stroke with brick-like cross lines."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.pen().widthF()
        color = self.pen().color()

        # Wide base fill
        base_pen = QPen(color)
        base_pen.setWidthF(width)
        base_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        base_pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(base_pen)
        painter.drawPath(self.path())

        # Cross lines (brick pattern)
        line_pen = QPen(color.darker(120))
        line_pen.setWidthF(0.5)
        painter.setPen(line_pen)

        brick_spacing = width * 0.8
        half_w = width / 2.0
        for pt, _dir_x, _dir_y, nx, ny in self._walk_path(brick_spacing):
            x1 = pt.x() + nx * half_w
            y1 = pt.y() + ny * half_w
            x2 = pt.x() - nx * half_w
            y2 = pt.y() - ny * half_w
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # Border lines
        border_pen = QPen(color.darker(140))
        border_pen.setWidthF(0.8)
        painter.setPen(border_pen)
        for offset_sign in (-1, 1):
            offset_path = self._offset_path(width / 2.0 * offset_sign)
            if offset_path:
                painter.drawPath(offset_path)

        painter.restore()

    def _paint_wooden_boardwalk(self, painter: QPainter) -> None:
        """Wooden boardwalk: planks across the path with gaps."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.pen().widthF()
        color = self.pen().color()

        plank_spacing = width * 0.35
        plank_w = width * 0.3
        half_w = width / 2.0

        for i, (pt, dir_x, dir_y, _nx, _ny) in enumerate(self._walk_path(plank_spacing)):
            # Alternate plank color slightly
            shade = 10 if i % 2 == 0 else -10
            plank_color = QColor(
                max(0, min(255, color.red() + shade)),
                max(0, min(255, color.green() + shade)),
                max(0, min(255, color.blue() + shade)),
            )
            painter.setBrush(QBrush(plank_color))
            painter.setPen(QPen(plank_color.darker(130), 0.5))

            # Draw plank as rotated rectangle
            painter.save()
            painter.translate(pt.x(), pt.y())
            angle = math.degrees(math.atan2(dir_y, dir_x))
            painter.rotate(angle)
            painter.drawRect(QRectF(-plank_w / 2, -half_w, plank_w, width))
            painter.restore()

        # Side rails
        rail_pen = QPen(color.darker(140))
        rail_pen.setWidthF(1.5)
        painter.setPen(rail_pen)
        for offset_sign in (-1, 1):
            offset_path = self._offset_path(half_w * offset_sign)
            if offset_path:
                painter.drawPath(offset_path)

        painter.restore()

    def _paint_dirt_path(self, painter: QPainter) -> None:
        """Dirt path: soft-edged wide stroke with organic feel."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.pen().widthF()
        color = self.pen().color()

        # Soft wide base
        base_pen = QPen(QColor(color.red(), color.green(), color.blue(), 140))
        base_pen.setWidthF(width)
        base_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        base_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(base_pen)
        painter.drawPath(self.path())

        # Softer outer edge
        edge_pen = QPen(QColor(color.red(), color.green(), color.blue(), 50))
        edge_pen.setWidthF(width + 3.0)
        edge_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        edge_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(edge_pen)
        painter.drawPath(self.path())

        # Core center line
        core_pen = QPen(color.darker(110))
        core_pen.setWidthF(width * 0.4)
        core_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        core_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(core_pen)
        painter.drawPath(self.path())

        painter.restore()

    # ── Fence style renderers ──

    def _paint_wooden_fence(self, painter: QPainter) -> None:
        """Wooden fence: posts with horizontal rails."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = self.pen().color()
        width = self.pen().widthF()

        # Horizontal rails (two parallel lines)
        rail_pen = QPen(color)
        rail_pen.setWidthF(1.2)
        painter.setPen(rail_pen)
        for offset_sign in (-0.3, 0.3):
            offset_path = self._offset_path(width * offset_sign)
            if offset_path:
                painter.drawPath(offset_path)

        # Fence posts
        post_spacing = width * 3.0
        post_size = width * 0.6

        painter.setPen(QPen(color.darker(130), 0.8))
        painter.setBrush(QBrush(color))

        for pt, dir_x, dir_y, _nx, _ny in self._walk_path(post_spacing):
            painter.save()
            painter.translate(pt.x(), pt.y())
            angle = math.degrees(math.atan2(dir_y, dir_x))
            painter.rotate(angle)
            painter.drawRect(QRectF(-post_size / 3, -post_size / 2, post_size / 1.5, post_size))
            painter.restore()

        painter.restore()

    def _paint_metal_fence(self, painter: QPainter) -> None:
        """Metal/wrought iron fence: thin posts with decorative tops."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = self.pen().color()
        width = self.pen().widthF()

        # Base rail
        rail_pen = QPen(color)
        rail_pen.setWidthF(1.5)
        painter.setPen(rail_pen)
        painter.drawPath(self.path())

        # Vertical bars
        bar_spacing = width * 2.0
        bar_height = width * 2.5

        for pt, _dir_x, _dir_y, nx, ny in self._walk_path(bar_spacing):
            painter.setPen(QPen(color, 0.8))
            # Draw vertical bar (perpendicular to path)
            x1 = pt.x() + nx * bar_height / 2
            y1 = pt.y() + ny * bar_height / 2
            x2 = pt.x() - nx * bar_height / 2
            y2 = pt.y() - ny * bar_height / 2
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

            # Decorative spear top
            painter.setBrush(QBrush(color))
            painter.setPen(Qt.PenStyle.NoPen)
            tip = QPointF(x1, y1)
            painter.drawEllipse(tip, 1.2, 1.2)

        painter.restore()

    def _paint_chain_link(self, painter: QPainter) -> None:
        """Chain link fence: diamond/zigzag pattern between posts."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = self.pen().color()
        width = self.pen().widthF()

        # Top and bottom rails
        rail_pen = QPen(color.darker(120))
        rail_pen.setWidthF(1.0)
        painter.setPen(rail_pen)
        for offset_sign in (-1, 1):
            offset_path = self._offset_path(width * offset_sign)
            if offset_path:
                painter.drawPath(offset_path)

        # Diamond/zigzag mesh pattern
        mesh_pen = QPen(QColor(color.red(), color.green(), color.blue(), 150))
        mesh_pen.setWidthF(0.5)
        painter.setPen(mesh_pen)

        mesh_spacing = width * 0.8
        half_w = width

        zigzag_up = True
        prev_pt: QPointF | None = None
        for pt, _dir_x, _dir_y, nx, ny in self._walk_path(mesh_spacing):
            offset = half_w * (0.5 if zigzag_up else -0.5)
            mesh_pt = QPointF(pt.x() + nx * offset, pt.y() + ny * offset)
            if prev_pt is not None:
                painter.drawLine(prev_pt, mesh_pt)
            prev_pt = mesh_pt
            zigzag_up = not zigzag_up

        # Posts at intervals
        post_spacing = width * 8.0
        painter.setPen(QPen(color.darker(130), 1.5))
        for pt, _dir_x, _dir_y, nx, ny in self._walk_path(post_spacing):
            x1 = pt.x() + nx * half_w
            y1 = pt.y() + ny * half_w
            x2 = pt.x() - nx * half_w
            y2 = pt.y() - ny * half_w
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        painter.restore()

    def _paint_hedge_fence(self, painter: QPainter) -> None:
        """Hedge: wide organic green stroke with leaf-like bumps."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = self.pen().color()
        width = self.pen().widthF()

        # Wide green base
        base_pen = QPen(color)
        base_pen.setWidthF(width)
        base_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        base_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(base_pen)
        painter.drawPath(self.path())

        # Leaf bumps along edges
        bump_spacing = width * 0.5
        half_w = width / 2.0
        painter.setPen(Qt.PenStyle.NoPen)

        for i, (pt, _dir_x, _dir_y, nx, ny) in enumerate(self._walk_path(bump_spacing)):
            shade = 15 if i % 2 == 0 else -15
            leaf_color = QColor(
                max(0, min(255, color.red() + shade)),
                max(0, min(255, color.green() + shade + 10)),
                max(0, min(255, color.blue() + shade)),
            )
            painter.setBrush(QBrush(leaf_color))
            # Alternate bumps on each side
            side = 1 if i % 2 == 0 else -1
            offset = half_w * 0.7 * side
            cx = pt.x() + nx * offset
            cy = pt.y() + ny * offset
            r = width * 0.25 + (i % 3) * 0.5
            painter.drawEllipse(QPointF(cx, cy), r, r * 0.8)

        painter.restore()

    def _paint_stone_wall(self, painter: QPainter) -> None:
        """Stone wall: irregular stone blocks along the path."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = self.pen().color()
        width = self.pen().widthF()

        # Base fill
        base_pen = QPen(color)
        base_pen.setWidthF(width)
        base_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        base_pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(base_pen)
        painter.drawPath(self.path())

        # Stone blocks
        stone_spacing = width * 0.6
        half_w = width / 2.0

        for i, (pt, dir_x, dir_y, nx, ny) in enumerate(self._walk_path(stone_spacing)):
            # Vary stone color
            shade = ((i * 17) % 30) - 15
            stone_color = QColor(
                max(0, min(255, color.red() + shade)),
                max(0, min(255, color.green() + shade)),
                max(0, min(255, color.blue() + shade - 5)),
            )
            painter.setBrush(QBrush(stone_color))
            painter.setPen(QPen(stone_color.darker(130), 0.5))

            # Draw stone as a slightly irregular rectangle
            stone_w = width * (0.4 + (i % 3) * 0.1)
            stone_h = width * (0.35 + (i % 2) * 0.15)
            # Offset for two rows
            row_offset = half_w * 0.3 * (1 if i % 2 == 0 else -1)

            painter.save()
            painter.translate(pt.x() + nx * row_offset, pt.y() + ny * row_offset)
            angle = math.degrees(math.atan2(dir_y, dir_x))
            painter.rotate(angle)
            painter.drawRoundedRect(
                QRectF(-stone_w / 2, -stone_h / 2, stone_w, stone_h),
                1.5, 1.5,
            )
            painter.restore()

        # Border lines
        border_pen = QPen(color.darker(140))
        border_pen.setWidthF(0.8)
        painter.setPen(border_pen)
        for offset_sign in (-1, 1):
            offset_path = self._offset_path(half_w * offset_sign)
            if offset_path:
                painter.drawPath(offset_path)

        painter.restore()

    # ── Helper methods ──

    def _walk_path(
        self, spacing: float
    ) -> Iterator[tuple[QPointF, float, float, float, float]]:
        """Walk along the polyline yielding points at regular intervals.

        Yields:
            Tuples of (point, dir_x, dir_y, normal_x, normal_y)
        """
        points = self._points
        if len(points) < 2:
            return

        dist_remaining = 0.0
        for seg_idx in range(len(points) - 1):
            p1 = points[seg_idx]
            p2 = points[seg_idx + 1]
            seg_dx = p2.x() - p1.x()
            seg_dy = p2.y() - p1.y()
            seg_len = math.hypot(seg_dx, seg_dy)
            if seg_len < 0.001:
                continue

            dir_x = seg_dx / seg_len
            dir_y = seg_dy / seg_len
            nx, ny = -dir_y, dir_x  # Normal

            t = dist_remaining
            while t <= seg_len:
                pt = QPointF(p1.x() + dir_x * t, p1.y() + dir_y * t)
                yield (pt, dir_x, dir_y, nx, ny)
                t += spacing

            dist_remaining = t - seg_len

    def _offset_path(self, offset: float) -> QPainterPath | None:
        """Create an offset path parallel to the polyline.

        Args:
            offset: Perpendicular offset distance (positive = left of direction)

        Returns:
            Offset QPainterPath, or None if not enough points
        """
        points = self._points
        if len(points) < 2:
            return None

        path = QPainterPath()
        first = True
        for i in range(len(points) - 1):
            p1 = points[i]
            p2 = points[i + 1]
            nx, ny = self._get_segment_normal(p1, p2)
            op1 = QPointF(p1.x() + nx * offset, p1.y() + ny * offset)
            op2 = QPointF(p2.x() + nx * offset, p2.y() + ny * offset)
            if first:
                path.moveTo(op1)
                first = False
            else:
                path.lineTo(op1)
            path.lineTo(op2)
        return path
