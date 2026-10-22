"""Shared north arrow painting in screen/page coordinates (Y-down)."""

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen


def draw_north_arrow(painter: QPainter, center: QPointF, radius: float,
                     angle_deg: float, color: QColor) -> None:
    """Keep the N upright while the arrow rotates clockwise from screen up."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(center)
    painter.setPen(QPen(color, 1.5))
    painter.save()
    painter.rotate(angle_deg)
    painter.drawLine(QPointF(0, radius * 0.55), QPointF(0, -radius * 0.6))
    painter.drawLine(QPointF(0, -radius * 0.6), QPointF(-radius * 0.18, -radius * 0.25))
    painter.drawLine(QPointF(0, -radius * 0.6), QPointF(radius * 0.18, -radius * 0.25))
    painter.restore()
    angle = math.radians(angle_deg)
    label = QPointF(math.sin(angle) * radius * 0.85, -math.cos(angle) * radius * 0.85)
    font = QFont(painter.font())
    font.setPixelSize(max(10, round(radius * 0.35)))
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(QRectF(label.x() - radius * 0.3, label.y() - radius * 0.3,
                            radius * 0.6, radius * 0.6), Qt.AlignmentFlag.AlignCenter, "N")
    painter.restore()
