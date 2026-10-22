"""Plan orientation, independent of geometry (CAD Y-up). No Qt dependency.

north_angle_deg is clockwise from the top of the drawing: 0=up, 90=right,
135=down-right. Add this bearing to a geographic solar azimuth before using
it in drawing coordinates. Stored object coordinates never change.
"""

from open_garden_planner.core.plan_io import finite_number


def normalize_north_angle(value: object) -> float:
    return finite_number(value, "north_angle_deg") % 360.0


def drawing_azimuth(azimuth_deg: float, north_angle_deg: float) -> float:
    return (azimuth_deg + north_angle_deg) % 360.0
