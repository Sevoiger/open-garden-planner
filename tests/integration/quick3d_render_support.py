"""The judging half of the 3D render tier: start the driver, read frames, predict pixels.

Not a test module. See ``quick3d_render_driver.py`` for why the tier is split into
a process that renders (real QPA) and tests that judge (the offscreen test
process), and ``docs/08-crosscutting-concepts`` §8.10.4 for the pattern.

Running the tier (opt-in; it opens windows on the desktop)::

    # Windows (PowerShell):  $env:OGP_RENDER3D = "1"; venv\\Scripts\\python.exe -m pytest <files>
    # Linux:                 OGP_RENDER3D=1 xvfb-run -a -s "-screen 0 1920x1080x24" \\
    #                            python -m pytest <files>

    <files> = tests/integration/test_quick3d_render.py tests/integration/test_quick3d_engine.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from open_garden_planner.core.scene3d import Record, pose_ring
from open_garden_planner.ui.view3d.quick3d.shots import TopDownShot, top_down_pixel

REPO = Path(__file__).resolve().parents[2]
DRIVER = Path(__file__).resolve().with_name("quick3d_render_driver.py")
QML_SOURCE = REPO / "src" / "open_garden_planner" / "ui" / "view3d" / "quick3d" / "qml"
DRIVER_TIMEOUT_S = 840
QT_ERRORS_EXIT = 4


def render_tier_skip_reason() -> str | None:
    """None when the render tier can run here, else why it is skipped."""
    if os.environ.get("OGP_RENDER3D") != "1":
        return "render tier (opt-in): set OGP_RENDER3D=1 — it needs a real window system"
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        return "render tier on Linux needs an X display: run under xvfb-run"
    return None


render_tier = [
    pytest.mark.skipif(render_tier_skip_reason() is not None,
                       reason=render_tier_skip_reason() or ""),
    # one driver process serves a whole module (seconds on a GPU, a minute or two on
    # a software rasteriser); the global 180 s per-test timeout would abort the
    # module fixture the first test pays for
    pytest.mark.timeout(900),
]


def render_env(scratch: Path, **extra: str) -> dict[str, str]:
    """The child's environment: a REAL Qt platform and THIS checkout's code.

    The test process runs ``QT_QPA_PLATFORM=offscreen`` (tests/conftest.py), which
    selects the software scene graph — no 3D. Windows: the default platform and
    Direct3D 11 (the real GPU, or WARP where there is none). Linux: xcb + OpenGL
    (Mesa llvmpipe in a container) and a private settings home.

    PYTHONPATH: a ``python tests/…/driver.py`` child would otherwise import whatever
    the venv's editable install points at — from a git worktree, the main checkout.
    """
    env = dict(os.environ)
    env.pop("QT_QPA_PLATFORM", None)
    if sys.platform.startswith("linux"):
        env["QT_QPA_PLATFORM"] = "xcb"
        env.setdefault("QSG_RHI_BACKEND", "opengl")
        env.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
        env["XDG_CONFIG_HOME"] = str(scratch / "config_home")
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            env["QTWEBENGINE_DISABLE_SANDBOX"] = "1"  # Chromium refuses root with a sandbox
    paths = [str(REPO / "src"), str(REPO), env.get("PYTHONPATH", "")]
    env["PYTHONPATH"] = os.pathsep.join(p for p in paths if p)
    env.update(extra)
    return env


def run_driver(out: Path, scenarios: str, *, qml_dir: Path | None = None,
               env: dict[str, str] | None = None) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    """Run the driver; return the process and its ``result.json`` ({} when it wrote none)."""
    command = [sys.executable, str(DRIVER), "--out", str(out), "--scenarios", scenarios,
               "--watchdog-s", str(DRIVER_TIMEOUT_S - 60)]
    if qml_dir is not None:
        command += ["--qml-dir", str(qml_dir)]
    proc = subprocess.run(  # noqa: S603 - fixed argv, our own code
        command, env=env if env is not None else render_env(out), capture_output=True, text=True,
        timeout=DRIVER_TIMEOUT_S, cwd=REPO)
    result_file = out / "result.json"
    result = json.loads(result_file.read_text(encoding="utf-8")) if result_file.is_file() else {}
    keep_artifacts(out, proc)
    return proc, result


def keep_artifacts(out: Path, proc: subprocess.CompletedProcess[str]) -> None:
    """Copy a driver run's frames, ``result.json`` and log to ``OGP_RENDER3D_ARTIFACTS``
    (CI uploads that directory) — a failed render on a runner nobody can log into is
    only debuggable from its pictures."""
    target = os.environ.get("OGP_RENDER3D_ARTIFACTS")
    if not target:
        return
    destination = Path(target) / f"{out.parent.name}-{out.name}"
    shutil.copytree(out, destination, dirs_exist_ok=True)
    (destination / "driver-stdio.txt").write_text(
        f"exit {proc.returncode}\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}",
        encoding="utf-8")


def failure_report(proc: subprocess.CompletedProcess[str], out: Path) -> str:
    """Everything a failed driver run left behind, for the assertion message."""
    log = out / "render.log"
    tail = log.read_text(encoding="utf-8", errors="replace")[-3000:] if log.is_file() else "<no log>"
    return f"exit {proc.returncode}\n--- stderr ---\n{proc.stderr[-2000:]}\n--- render.log ---\n{tail}"


# ── pixels ───────────────────────────────────────────────────────────────────


def load_rgb(path: Path) -> np.ndarray:
    """A PNG as an (h, w, 3) int array."""
    from PyQt6.QtGui import QImage

    image = QImage(str(path))
    assert not image.isNull(), f"cannot read {path}"
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    bits = image.constBits()
    bits.setsize(image.sizeInBytes())
    rows = np.frombuffer(bytes(bits), np.uint8).reshape(image.height(), image.bytesPerLine() // 4, 4)
    return rows[:, :image.width(), :3].astype(np.int64)


def luma(rgb: np.ndarray) -> np.ndarray:
    return 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]


# The top-down view is not tone-mapped: a pixel is linear radiance, so the probe
# colours of the driver (pure red / blue / yellow tints) clip to pure channels there.
def red_mask(rgb: np.ndarray) -> np.ndarray:
    return (rgb[..., 0] > 150) & (rgb[..., 1] < 70) & (rgb[..., 2] < 70)


def blue_mask(rgb: np.ndarray) -> np.ndarray:
    return (rgb[..., 2] > 150) & (rgb[..., 0] < 70) & (rgb[..., 1] < 90)


def yellow_mask(rgb: np.ndarray) -> np.ndarray:
    return (rgb[..., 0] > 150) & (rgb[..., 1] > 150) & (rgb[..., 2] < 90)


def polygon_mask(points: Sequence[tuple[float, float]], width: int, height: int) -> np.ndarray:
    """Pixels whose CENTRE lies inside the pixel-space polygon (even-odd rule)."""
    xs, ys = np.meshgrid(np.arange(width) + 0.5, np.arange(height) + 0.5)
    inside = np.zeros((height, width), dtype=bool)
    count = len(points)
    for index in range(count):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % count]
        if y1 == y2:
            continue
        crosses = (y1 > ys) != (y2 > ys)
        at = (x2 - x1) * (ys - y1) / (y2 - y1) + x1
        inside ^= crosses & (xs < at)
    return inside


def scene_polygon_mask(polygon: Sequence[tuple[float, float]], shot: TopDownShot,
                       shape: tuple[int, ...], dpr: float = 1.0) -> np.ndarray:
    """Where OUR projection puts a scene-frame polygon in a top-down frame."""
    height, width = shape[:2]
    pixels = [top_down_pixel(x, y, shot, (width, height), dpr) for x, y in polygon]
    return polygon_mask(pixels, width, height)


def footprint_mask(record: Record, shot: TopDownShot, shape: tuple[int, ...],
                   dpr: float = 1.0) -> np.ndarray:
    """The CPU prediction of a prism in the top-down view: its posed footprint
    (``core.scene3d.pose_ring``) — an orthographic top view shows the top face only."""
    return scene_polygon_mask(pose_ring(record.transform, record.footprints[0]), shot, shape, dpr)


def iou(first: np.ndarray, second: np.ndarray) -> float:
    union = int((first | second).sum())
    return float((first & second).sum() / union) if union else 0.0


def centroid(mask: np.ndarray) -> tuple[float, float]:
    """Centre of a mask in continuous pixel coordinates (pixel centres at +0.5)."""
    rows, cols = np.nonzero(mask)
    assert rows.size, "empty mask"
    return float(cols.mean() + 0.5), float(rows.mean() + 0.5)
