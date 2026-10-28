"""Bounded PNG import and decoding shared by portable personal images."""

import base64
import binascii
from functools import lru_cache
from pathlib import Path

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
from PyQt6.QtGui import QImage, QImageReader

MAX_BYTES = 16 * 1024 * 1024
MAX_PIXELS = 4096 * 4096


@lru_cache(maxsize=2)
def decode_png(encoded: str) -> QImage | None:
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
        raise ValueError("PNG exceeds 16 MiB")
    encoded = base64.b64encode(data).decode("ascii")
    if decode_png(encoded) is None:
        raise ValueError("Invalid PNG or image exceeds 16 megapixels")
    return encoded
