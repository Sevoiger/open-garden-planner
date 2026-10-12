"""Measurements of a rendered 3D frame — plain numpy, no Qt, no app (Phase 17 L1.4).

The instruments of ``scripts/render_beauty_board.py`` and of the looks' render
tier. Each one turns PIXELS into a number an artifact or a truth gate is judged
by; none of them renders, loads a plan or imports the app — the caller hands over
frames as ``(h, w, 3+)`` sRGB arrays and the geometry as plain numbers. That is
what lets ``tests/unit/test_board_probes.py`` check every instrument on synthetic
frames, with a positive control for each, in the GPU-less run.

Ported from the L0 spike's ``probes.py`` (frame statistics, the isolated-dark-pixel
count, the pixel bearing) and extended for what L1.4's gates ask: a PERSPECTIVE
camera (``Pinhole``: our own projection and its inverse, independent of the
engine's), the shadow IoU on a uniform ground grid seen through that camera, the
luminance step across the horizon, and the orientation of a top-down frame.

Frames: sRGB codes, row 0 on top. Scene: x = East, y = North, z = up, centimetres.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

Array = NDArray[np.float64]
Mask = NDArray[np.bool_]
Polygon = Sequence[tuple[float, float]]

# ── frame statistics (the board's per-shot numbers) ──────────────────────────

#: An isolated dark pixel: luma under 40 while the mean of its eight neighbours is
#: over 110 — a near-black dot on a lit surface. ``sharpnessAmount`` 0.08 overshot
#: every thin lit/dark edge into them: 1,500-3,100 per daytime frame at 1280x720.
SPECKLE_MAX_LUMA, SPECKLE_MIN_NEIGHBOURS = 40.0, 110.0
#: A channel at or above this is clipped.
CLIPPED_CODE = 254


def luma(rgb: Any) -> Array:
    """Rec. 709 luma of an ``(..., 3+)`` array of sRGB codes."""
    values = np.asarray(rgb, dtype=np.float64)
    return np.asarray(0.2126 * values[..., 0] + 0.7152 * values[..., 1] + 0.0722 * values[..., 2])


def isolated_dark_pixels(rgb: Any) -> int:
    """Pixels darker than ``SPECKLE_MAX_LUMA`` in lit surroundings."""
    values = luma(rgb)
    height, width = values.shape
    padded = np.pad(values, 1, mode="edge")
    around = sum(padded[1 + dy:1 + dy + height, 1 + dx:1 + dx + width]
                 for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dy, dx) != (0, 0)) / 8.0
    return int(((values < SPECKLE_MAX_LUMA) & (around > SPECKLE_MIN_NEIGHBOURS)).sum())


def frame_stats(rgb: Any) -> dict[str, Any]:
    """A board frame's own numbers: isolated dark pixels, the share of pixels with a
    clipped channel (per cent) and the mean luma."""
    values = np.asarray(rgb, dtype=np.float64)[..., :3]
    return {"isolated_dark_px": isolated_dark_pixels(values),
            "clipped_pct": round(float((values.max(axis=2) >= CLIPPED_CODE).mean()) * 100.0, 3),
            "mean_luma": round(float(luma(values).mean()), 2)}


# ── our own camera ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Pinhole:
    """A perspective camera as plain arithmetic: ``eye`` looking at ``target`` with the
    world's up, a VERTICAL field of view, an image of ``size`` = (width, height) pixels.

    Independent of the engine: it knows nothing but the shot's own numbers, so a
    mirrored or swapped engine mapping cannot agree with it.
    """

    eye: tuple[float, float, float]
    target: tuple[float, float, float]
    fov_deg: float
    size: tuple[int, int]

    def _basis(self) -> tuple[Array, Array, Array, Array]:
        eye = np.asarray(self.eye, dtype=np.float64)
        forward = np.asarray(self.target, dtype=np.float64) - eye
        forward /= np.linalg.norm(forward)
        right = np.cross(forward, np.array([0.0, 0.0, 1.0]))
        right /= np.linalg.norm(right)
        return eye, forward, right, np.cross(right, forward)

    @property
    def _focal(self) -> float:
        """Pixels per unit of tan(angle): half the height over tan(half the fov)."""
        return (self.size[1] / 2.0) / math.tan(math.radians(self.fov_deg) / 2.0)

    def project(self, points: Any) -> tuple[Array, Array, Mask]:
        """Scene points ``(n, 3)`` -> pixel ``x``, ``y`` (continuous; pixel (i, j) covers
        [i, i+1) x [j, j+1)) and whether each lies in front of the camera."""
        eye, forward, right, up = self._basis()
        offsets = np.atleast_2d(np.asarray(points, dtype=np.float64)) - eye
        depth = offsets @ forward
        safe = np.where(depth > 1e-6, depth, 1.0)
        x = self.size[0] / 2.0 + (offsets @ right) / safe * self._focal
        y = self.size[1] / 2.0 - (offsets @ up) / safe * self._focal
        return x, y, depth > 1e-6

    def rays(self, x: Any, y: Any) -> Array:
        """World directions (not normalised) through pixel positions ``x``, ``y``."""
        _eye, forward, right, up = self._basis()
        cam_x = (np.asarray(x, dtype=np.float64) - self.size[0] / 2.0) / self._focal
        cam_y = (self.size[1] / 2.0 - np.asarray(y, dtype=np.float64)) / self._focal
        return np.asarray(forward + cam_x[..., None] * right + cam_y[..., None] * up)

    def bearing_deg(self, x: float, y: float) -> float:
        """Compass bearing (clockwise from north) of the direction through a pixel."""
        ray = self.rays(np.array(x), np.array(y))
        return float(math.degrees(math.atan2(ray[0], ray[1])) % 360.0)

    def elevation_deg(self, x: float, y: float) -> float:
        """Elevation above the horizon of the direction through a pixel."""
        ray = self.rays(np.array(x), np.array(y))
        return float(math.degrees(math.atan2(ray[2], math.hypot(ray[0], ray[1]))))

    def horizon_row(self) -> float:
        """The image row of the geometric horizon straight ahead (elevation 0)."""
        _eye, forward, _right, _up = self._basis()
        pitch = math.atan2(forward[2], math.hypot(forward[0], forward[1]))
        return self.size[1] / 2.0 + math.tan(pitch) * self._focal

    def scaled(self, size: tuple[int, int]) -> Pinhole:
        """The same camera for an image of another size (a grab is in DEVICE pixels)."""
        return Pinhole(self.eye, self.target, self.fov_deg, size)


def polygon_mask(xs: Any, ys: Any, polygon: Polygon) -> Mask:
    """Which of the points ``(xs, ys)`` lie inside ``polygon`` (even-odd rule)."""
    x = np.asarray(xs, dtype=np.float64)
    y = np.asarray(ys, dtype=np.float64)
    inside = np.zeros(x.shape, dtype=bool)
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        if y1 == y2:
            continue
        crosses = (y1 > y) != (y2 > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            at = (x2 - x1) * (y - y1) / (y2 - y1) + x1
        inside ^= crosses & (x < at)
    return inside


def polygons_mask(xs: Any, ys: Any, polygons: Sequence[Polygon]) -> Mask:
    """Even-odd union of several polygons — what the 2D shadow overlay paints."""
    mask = np.zeros(np.asarray(xs).shape, dtype=bool)
    for polygon in polygons:
        mask ^= polygon_mask(xs, ys, polygon)
    return mask


def convex_hull(points: Any) -> list[tuple[float, float]]:
    """The convex hull of 2D points, counter-clockwise (Andrew's monotone chain)."""
    ordered = sorted({(float(x), float(y)) for x, y in np.asarray(points, dtype=np.float64)})
    if len(ordered) < 3:
        return ordered

    def turn(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for point in ordered:
        while len(lower) >= 2 and turn(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(ordered):
        while len(upper) >= 2 and turn(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


# ── gate G2: the shadow of a box, seen in perspective ────────────────────────


def box_silhouette(camera: Pinhole, footprint: Polygon, height: float) -> list[tuple[float, float]]:
    """The image-space outline of a prism over ``footprint`` (a convex one: its
    silhouette is the hull of its projected corners)."""
    corners = [(x, y, z) for x, y in footprint for z in (0.0, height)]
    px, py, _front = camera.project(corners)
    return convex_hull(np.column_stack([px, py]))


def shadow_iou(rgb: Any, camera: Pinhole, footprint: Polygon, height: float,
               analytic: Sequence[Polygon], *, region: tuple[float, float, float, float],
               lit_reference: tuple[float, float, float, float], step_cm: float = 2.0,
               margin_px: float = 2.0) -> dict[str, Any]:
    """IoU of the shadow a frame shows against the analytic 2D shadow — on a uniform
    GROUND grid (``step_cm``), so that the number is an area ratio like the 2D one.

    ``rgb``: the perspective frame. ``camera``: the shot (``size`` is replaced by the
    frame's own). ``footprint`` / ``height``: the box caster. ``analytic``: the 2D
    shadow polygons (``core.shadow_geometry.compute_scene_shadows``). ``region``:
    (east0, north0, east1, north1) of ground to judge. ``lit_reference``: a patch of
    ground the sun certainly reaches.

    A ground point counts when the camera sees it: inside the frame and not behind
    the caster (its pixel outside the caster's silhouette, grown by ``margin_px``),
    and not under the caster. "Shadow" is darker than halfway between the lit
    ground and the darkest ground in the region — measured from the frame, not from
    the answer.
    """
    frame = np.asarray(rgb, dtype=np.float64)[..., :3]
    rows, cols = frame.shape[:2]
    view = camera.scaled((cols, rows))
    values = luma(frame)

    def sample(east0: float, north0: float, east1: float, north1: float
               ) -> tuple[Array, Array, Array, Mask]:
        east = np.arange(east0 + step_cm / 2.0, east1, step_cm)
        north = np.arange(north0 + step_cm / 2.0, north1, step_cm)
        xs, ys = np.meshgrid(east, north)
        px, py, front = view.project(np.column_stack([xs.ravel(), ys.ravel(), np.zeros(xs.size)]))
        col, row = np.floor(px).astype(np.int64), np.floor(py).astype(np.int64)
        seen = front & (col >= 0) & (col < cols) & (row >= 0) & (row < rows)
        picked = np.full(xs.size, np.nan)
        picked[seen] = values[row[seen], col[seen]]
        outline = box_silhouette(view, footprint, height)
        centre = np.mean(np.asarray(outline), axis=0)
        grown = [(x + (x - centre[0]) / max(math.hypot(x - centre[0], y - centre[1]), 1e-9) * margin_px,
                  y + (y - centre[1]) / max(math.hypot(x - centre[0], y - centre[1]), 1e-9) * margin_px)
                 for x, y in outline]
        hidden = polygon_mask(px, py, grown)
        return xs, ys, picked.reshape(xs.shape), (seen & ~hidden).reshape(xs.shape)

    xs, ys, seen_luma, visible = sample(*region)
    visible &= ~polygon_mask(xs, ys, footprint)
    _lx, _ly, lit_luma, lit_seen = sample(*lit_reference)
    if not lit_seen.any() or not visible.any():
        return {"iou": 0.0, "measured_cells": 0, "analytic_cells": 0, "visible_cells": 0,
                "lit_luma": None, "threshold": None}
    lit = float(np.median(lit_luma[lit_seen]))
    dark = float(np.percentile(seen_luma[visible], 2.0))
    threshold = (lit + dark) / 2.0
    measured = visible & (seen_luma < threshold)
    expected = visible & polygons_mask(xs, ys, analytic)
    union = int((measured | expected).sum())
    both = int((measured & expected).sum())
    out: dict[str, Any] = {
        "iou": round(both / union, 4) if union else 0.0,
        "measured_cells": int(measured.sum()), "analytic_cells": int(expected.sum()),
        "visible_cells": int(visible.sum()), "lit_luma": round(lit, 1), "dark_luma": round(dark, 1),
        "threshold": round(threshold, 1), "step_cm": step_cm}
    for name, mask in (("centroid_measured_cm", measured), ("centroid_analytic_cm", expected)):
        out[name] = ([round(float(xs[mask].mean()), 1), round(float(ys[mask].mean()), 1)]
                     if mask.any() else None)
    return out


# ── gate G4: where the sky draws its sun ─────────────────────────────────────


def sun_disc(rgb: Any, camera: Pinhole) -> dict[str, Any]:
    """The compass bearing and the elevation of the sky's sun disc in a frame that
    looks at the sky: the peak of "bright AND warm" above the horizon.

    ``found`` is False when nothing stands out (contrast of the peak over the
    median at most 40) — a sky without a disc, a night sky.
    """
    frame = np.asarray(rgb, dtype=np.float64)[..., :3]
    rows, cols = frame.shape[:2]
    view = camera.scaled((cols, rows))
    band = max(1, min(rows, int(math.floor(view.horizon_row()))))
    score = luma(frame[:band]) + 0.8 * (frame[:band, :, 0] - frame[:band, :, 2])
    row, col = np.unravel_index(int(np.argmax(score)), score.shape)
    contrast = float(score[row, col] - np.median(score))
    if contrast <= 40.0:
        return {"found": False, "contrast": round(contrast, 1)}
    # the disc is a plateau: take the centre of everything within 2 % of the peak
    peak = score >= score[row, col] - 0.02 * abs(float(score[row, col]))
    peak_rows, peak_cols = np.nonzero(peak)
    x, y = float(peak_cols.mean()) + 0.5, float(peak_rows.mean()) + 0.5
    return {"found": True, "contrast": round(contrast, 1), "pixel": [round(x, 1), round(y, 1)],
            "bearing_deg": round(view.bearing_deg(x, y), 2),
            "elevation_deg": round(view.elevation_deg(x, y), 2)}


def bearing_error_deg(measured: float, expected: float) -> float:
    """Signed difference of two compass bearings, in (-180, 180]."""
    return ((measured - expected + 180.0) % 360.0) - 180.0


# ── gate G5: fog meets sky ───────────────────────────────────────────────────


def horizon_step(rgb: Any, camera: Pinhole, *, reach_deg: float = 0.6, line_deg: float = 0.25,
                 columns: tuple[float, float] = (0.0, 1.0)) -> dict[str, Any]:
    """How the fogged ground meets the sky at the geometric horizon — three numbers
    of the row-mean luma within ``reach_deg`` of the horizon:

    ``step``   the largest change from one row to the next: a CLIFF where the meadow
               meets the sky (the low preset without fog: 84.5 luma in one row);
    ``dip``    how far the darkest row lies under BOTH the rows ``line_deg`` above
               and below it: a thin dark line (the sky's ground hemisphere reaching
               its dark end within a texel: 9-10 luma);
    ``ridge``  the same for a thin BRIGHT line (a fog brighter than the sky it meets).

    ``sky_luma`` / ``ground_luma``: the means just above and below, for the record.
    ``columns``: the share of the image width to use (to keep a sun disc out).
    """
    frame = np.asarray(rgb, dtype=np.float64)[..., :3]
    rows, cols = frame.shape[:2]
    view = camera.scaled((cols, rows))
    horizon = view.horizon_row()
    per_degree = math.tan(math.radians(1.0)) * view._focal  # noqa: SLF001 - its own module
    left, right = int(columns[0] * cols), max(int(columns[1] * cols), int(columns[0] * cols) + 1)
    row_luma = luma(frame[:, left:right]).mean(axis=1)
    apart = max(2, int(round(line_deg * per_degree)))
    first = max(int(math.floor(horizon - reach_deg * per_degree)), apart)
    last = min(int(math.ceil(horizon + reach_deg * per_degree)), rows - 1 - apart)
    if last <= first:
        return {"step": 0.0, "dip": 0.0, "ridge": 0.0, "horizon_row": round(horizon, 1),
                "rows_per_degree": round(per_degree, 2), "judged_rows": 0}
    near = row_luma[first:last + 1]
    above, below = row_luma[first - apart:last + 1 - apart], row_luma[first + apart:last + 1 + apart]
    step = float(np.abs(np.diff(near)).max()) if near.size > 1 else 0.0
    dip = max(0.0, float((np.minimum(above, below) - near).max()))
    ridge = max(0.0, float((near - np.maximum(above, below)).max()))
    return {"step": round(step, 2), "dip": round(dip, 2), "ridge": round(ridge, 2),
            "sky_luma": round(float(row_luma[first - apart:first].mean()), 2),
            "ground_luma": round(float(row_luma[last + 1:last + 1 + apart].mean()), 2),
            "horizon_row": round(horizon, 1), "rows_per_degree": round(per_degree, 2),
            "judged_rows": int(near.size)}


# ── north is up: the orientation of a top-down frame ─────────────────────────


def ncc(first: Any, second: Any) -> float:
    """Normalised cross-correlation of two arrays of one shape (0 for a flat one)."""
    a = np.asarray(first, dtype=np.float64)
    b = np.asarray(second, dtype=np.float64)
    a = a - a.mean()
    b = b - b.mean()
    scale = math.sqrt(float((a * a).sum()) * float((b * b).sum()))
    return float((a * b).sum() / scale) if scale > 0.0 else 0.0


def orientation_scores(seen: Any, predicted: Any) -> dict[str, Any]:
    """Does a top-down frame show the plan the way OUR projection predicts it?

    ``seen``: what the frame shows (a mask or an image, h x w); ``predicted``: the
    same thing computed on the CPU from the records. The correlation with the
    prediction as it is must beat the correlation with each of its mirror images.
    """
    target = np.asarray(seen, dtype=np.float64)
    made = np.asarray(predicted, dtype=np.float64)
    scores = {"identity": ncc(target, made), "flip_v": ncc(target, made[::-1]),
              "flip_h": ncc(target, made[:, ::-1]), "flip_both": ncc(target, made[::-1, ::-1])}
    best = max(scores, key=lambda name: scores[name])
    return {"ncc": {name: round(value, 4) for name, value in scores.items()},
            "best": best, "ok": best == "identity"}
