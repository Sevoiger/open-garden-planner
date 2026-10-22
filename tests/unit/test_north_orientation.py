"""Independent direction checks for north bearings and daily solar results."""

import math
from datetime import UTC, date, datetime

import pytest

from open_garden_planner.core.north_orientation import drawing_azimuth, normalize_north_angle
from open_garden_planner.core.project import ProjectData
from open_garden_planner.core.shade_aggregation import (
    HeatmapGrid,
    IdentifiedCaster,
    SunReceiver,
    SunSample,
    compute_heatmap,
    compute_sun_exposure,
    point_rasterizer_reference,
)


@pytest.mark.parametrize(("raw", "expected"), [(0, 0), (135, 135), (360, 0), (-90, 270), (495, 135)])
def test_normalization_and_legacy(raw, expected):
    assert normalize_north_angle(raw) == expected
    assert ProjectData.from_dict({"north_angle_deg": raw}).to_dict()["north_angle_deg"] == expected
    assert ProjectData.from_dict({}).north_angle_deg == 0


@pytest.mark.parametrize("invalid", [True, None, float("nan"), float("inf"), "bad"])
def test_reject_invalid_bearings(invalid):
    with pytest.raises(ValueError):
        normalize_north_angle(invalid)


@pytest.mark.parametrize("angle", [0, 90, 135, 270])
def test_south_sun_casts_shadow_towards_drawing_north(monkeypatch, angle):
    import open_garden_planner.core.shade_aggregation as aggregation

    day = date(2026, 6, 21)
    # A geographic south sun at 45° casts a 100 cm shadow to true north.
    monkeypatch.setattr(aggregation, "daylight_samples", lambda *_args: [
        SunSample(datetime(2026, 6, 21, 12, tzinfo=UTC), 45, 180)
    ])
    footprint = ((130, 130), (170, 130), (170, 170), (130, 170))
    grid = HeatmapGrid(0, 0, 10, 30, 30)
    expected_x = 150 + 80 * math.sin(math.radians(angle))
    expected_y = 150 + 80 * math.cos(math.radians(angle))
    assert drawing_azimuth(0, angle) == angle
    result = compute_heatmap([(footprint, 100)], 50, 0, day, grid,
                             point_rasterizer_reference, north_angle_deg=angle)
    assert result[grid.cell_at(expected_x, expected_y)] == 0
    opposite = grid.cell_at(300 - expected_x, 300 - expected_y)
    assert result[opposite] == 15
    sparse = compute_sun_exposure([IdentifiedCaster("house", (footprint,), 100)],
                                  [SunReceiver("plant", expected_x, expected_y)],
                                  50, 0, day, grid, point_rasterizer_reference,
                                  north_angle_deg=angle)
    assert dict(sparse.receiver_minutes) == {"plant": 0}
    assert (sparse.minutes == result).all()
