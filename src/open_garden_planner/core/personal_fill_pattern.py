"""Portable, repeating owner textures; dimensions use scene centimeters."""

import math

from PyQt6.QtGui import QBrush, QPixmap, QTransform

from open_garden_planner.core.png_asset import decode_png

PATTERN_KEY = "personal_fill_pattern"
DEFAULT_TILE_WIDTH_CM = 256.0


def personal_pattern_brush(record: object) -> QBrush | None:
    """Invalid imported metadata falls back to the item's built-in material."""
    if not isinstance(record, dict):
        return None
    encoded = record.get("png")
    width = record.get("width_cm")
    if not isinstance(encoded, str) or not isinstance(width, (int, float)) or isinstance(width, bool):
        return None
    if not 1 <= width <= 10000 or not math.isfinite(width):
        return None
    image = decode_png(encoded)
    if image is None:
        return None
    # Keep original colors/alpha and aspect ratio. Compensate the canvas Y flip.
    brush = QBrush(QPixmap.fromImage(image.mirrored(False, True)))
    scale = width / image.width()
    brush.setTransform(QTransform.fromScale(scale, scale))
    return brush
