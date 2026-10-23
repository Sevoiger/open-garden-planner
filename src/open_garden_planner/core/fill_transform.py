"""Safe portable texture transforms, independent from object geometry."""

import math

FILL_TRANSFORM_KEY = "fill_transform"


def fill_origin_values(record: object) -> tuple[float, float]:
    """Internal origin compensation when persistence folds pos() into geometry."""
    def origin(key: str) -> float:
        value = record.get(key, 0.0) if isinstance(record, dict) else 0.0
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return 0.0
        return float(value)

    return origin("origin_x_cm"), origin("origin_y_cm")


def fill_transform_values(record: object) -> tuple[float, float]:
    if not isinstance(record, dict):
        return 0.0, 1.0
    angle, scale = record.get("angle_deg", 0.0), record.get("scale", 1.0)
    if (isinstance(angle, bool) or not isinstance(angle, (int, float))
            or not math.isfinite(angle)):
        angle = 0.0
    if (isinstance(scale, bool) or not isinstance(scale, (int, float))
            or not math.isfinite(scale) or not 0.01 <= scale <= 100):
        scale = 1.0
    return float(angle) % 360, float(scale)


def fill_offset_values(record: object) -> tuple[float, float]:
    """Return local surface offsets in cm, independent of tile angle/scale."""
    def offset(key: str) -> float:
        value = record.get(key, 0.0) if isinstance(record, dict) else 0.0
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not -100000 <= value <= 100000):
            return 0.0
        return float(value)

    return offset("offset_x_cm"), offset("offset_y_cm")
