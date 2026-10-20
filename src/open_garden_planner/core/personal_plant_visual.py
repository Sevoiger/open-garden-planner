"""Portable owner PNGs carried by the plant snapshot, never by an external path."""

import base64
import binascii
from functools import lru_cache
from pathlib import Path

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PyQt6.QtGui import QImage, QImageReader, QPixmap

VISUAL_KEY = "personal_visual_png"
MAX_BYTES = 4 * 1024 * 1024
MAX_PIXELS = 4096 * 4096


@lru_cache(maxsize=2)
def _decode(encoded: str) -> QImage | None:
    if len(encoded) > ((MAX_BYTES + 2) // 3) * 4:
        return None
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return None
    if len(data) > MAX_BYTES or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer, b"png")
    size = reader.size()
    if size.width() <= 0 or size.height() <= 0 or size.width() * size.height() > MAX_PIXELS:
        return None
    image = reader.read()
    return None if image.isNull() else image


def import_png(path: Path) -> str:
    """Read bounded original PNG bytes; reject unsupported or corrupt images."""
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("PNG exceeds 4 MiB")
    encoded = base64.b64encode(data).decode("ascii")
    if _decode(encoded) is None:
        raise ValueError("Invalid PNG or image exceeds 16 megapixels")
    return encoded


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
    image = _decode(encoded)
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
