"""Portable owner PNGs carried by the plant snapshot, never by an external path."""

from functools import lru_cache

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap

from open_garden_planner.core.png_asset import decode_png
from open_garden_planner.core.png_asset import import_png as import_png

VISUAL_KEY = "personal_visual_png"


def personal_visual_pixmap(species: object, *, canvas: bool = False) -> QPixmap | None:
    """Return original proportions/alpha, compensating Y-up only on the canvas."""
    if not isinstance(species, dict):
        return None
    raw = species.get("raw_data")
    encoded = raw.get(VISUAL_KEY) if isinstance(raw, dict) else None
    if not isinstance(encoded, str):
        return None
    return _pixmap(encoded, canvas)


@lru_cache(maxsize=8)
def _pixmap(encoded: str, canvas: bool) -> QPixmap | None:
    image = decode_png(encoded)
    if image is None:
        return None
    # Square transparent padding makes the existing plant draw rect preserve aspect.
    fitted = image.scaled(1024, 1024, Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
    from PyQt6.QtGui import QPainter  # noqa: PLC0415

    square = QImage(1024, 1024, QImage.Format.Format_ARGB32_Premultiplied)
    square.fill(Qt.GlobalColor.transparent)
    painter = QPainter(square)
    painter.drawImage((1024 - fitted.width()) // 2, (1024 - fitted.height()) // 2, fitted)
    painter.end()
    return QPixmap.fromImage(square.mirrored(False, True) if canvas else square)
