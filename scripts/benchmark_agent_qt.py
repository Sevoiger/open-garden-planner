"""Measure Qt queue/service and event-loop gaps with isolated synthetic scenes.

No application window or personal store is opened. Offscreen event-loop gaps are
technical measurements, not native visual acceptance or connector latency.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(
    0, os.environ.get("OGP_BENCH_SOURCE_ROOT", str(Path(__file__).resolve().parents[1] / "src"))
)
from benchmark_agent_reads import stats  # noqa: E402


def run(repeat):
    from PyQt6.QtWidgets import QApplication

    import open_garden_planner.app.settings as settings

    settings.ORGANIZATION_NAME = "ogp_synthetic_benchmark"
    settings.APPLICATION_NAME = f"profile-{os.getpid()}"
    from open_garden_planner.agent_api.bridge import MainThreadBridge
    from open_garden_planner.app.application import GardenPlannerApp
    from open_garden_planner.core.project import ProjectManager
    from open_garden_planner.services import plant_library
    from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
    from open_garden_planner.ui.canvas.items import RectangleItem

    app = QApplication.instance() or QApplication([])
    report = {}
    with tempfile.TemporaryDirectory(prefix="ogp-qt-profile-") as folder:
        plant_library._default_app_data_dir = lambda: Path(folder)
        for name, count, media_size in [
            ("small", 12, 0),
            ("many", 2000, 0),
            ("media", 12, 8_000_000),
        ]:
            scene, pm, bridge = CanvasScene(), ProjectManager(), MainThreadBridge()
            for _ in range(count):
                scene.addItem(RectangleItem(0, 0, 10, 10))
            if media_size:
                next(item for item in scene.items() if hasattr(item, "metadata")).metadata[
                    "extension"
                ] = {"image_data": "A" * media_size}
            window = SimpleNamespace(
                _project_manager=pm,
                canvas_scene=scene,
                canvas_view=SimpleNamespace(
                    command_manager=SimpleNamespace(execute=lambda _: None)
                ),
                _agent_bridge=bridge,
            )
            waits, service, total, gaps = [], [], [], []
            original = bridge.run_on_main

            def timed(fn, original=original, waits=waits, service=service):
                queued = time.perf_counter()

                def measured():
                    entered = time.perf_counter()
                    waits.append((entered - queued) * 1000)
                    try:
                        return fn()
                    finally:
                        service.append((time.perf_counter() - entered) * 1000)

                return original(measured)

            bridge.run_on_main = timed

            def read(window=window):
                started = time.perf_counter()
                value = GardenPlannerApp._agent_document_operation(
                    window, "read", {"domains": ["tasks"]}
                )
                return value, (time.perf_counter() - started) * 1000

            with ThreadPoolExecutor(max_workers=4) as pool:
                for concurrency in (1, 4):
                    waits.clear()
                    service.clear()
                    total.clear()
                    gaps.clear()
                    for _ in range(repeat):
                        futures = [pool.submit(read) for _ in range(concurrency)]
                        previous = time.perf_counter()
                        while not all(future.done() for future in futures):
                            app.processEvents()
                            now = time.perf_counter()
                            gaps.append((now - previous) * 1000)
                            previous = now
                        total.extend(future.result()[1] for future in futures)
                    report[f"{name}_concurrency_{concurrency}"] = {
                        "qt_queue": stats(waits),
                        "qt_service": stats(service),
                        "worker_total": stats(total),
                        "max_event_loop_gap_ms": max(gaps),
                        "samples": len(total),
                    }
            scene.clear()
    settings.create_qsettings().clear()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=12)
    args = parser.parse_args()
    args.output.write_text(json.dumps(run(args.repeat), indent=2) + "\n")
    print(args.output)
