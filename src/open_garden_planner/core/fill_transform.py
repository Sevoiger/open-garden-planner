"""Safe portable texture transforms, independent from object geometry."""

import math

FILL_TRANSFORM_KEY = "fill_transform"


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
