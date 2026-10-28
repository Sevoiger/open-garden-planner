"""Profile real live MCP provider paths on synthetic data, with one Qt main thread.

Latency samples have no cProfile/tracemalloc. Extra diagnostic calls measure
exclusive CPU/wall stages and process-wide transient Python allocations separately.
The Qt heartbeat measures event delivery, not native visual acceptance or FPS.
"""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import json
import os
import platform
import pstats
import resource
import socket
import sys
import tempfile
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.environ.get("OGP_BENCH_SOURCE_ROOT", str(Path(__file__).resolve().parents[1] / "src")))

from benchmark_agent_reads import stats  # noqa: E402


def stages(profile):
    """Exclusive times: nested calls are never counted twice in one profile."""
    totals = {}
    for (filename, _, name), (_, _, own, _, _) in pstats.Stats(profile).stats.items():
        phase = "other"
        if "deepcopy" in name or name.startswith("_deepcopy"):
            phase = "copy"
        elif "acquire" in name or "wait" in name:
            phase = "wait"
        elif "fingerprint" in name or "hashlib" in filename or "sha256" in name:
            phase = "hash"
        elif "read_bytes" in name or "read' of" in name or "read_text" in name:
            phase = "disk_read"
        elif "validate" in name or name in {"_read_operations", "_parse_species"}:
            phase = "validation"
        elif "json" in filename or "encode" in name or "decode" in name:
            phase = "json"
        elif "projection" in filename or "mapping" in filename or "queries" in filename:
            phase = "projection"
        elif "project.py" in filename:
            phase = "scene_capture"
        totals[phase] = totals.get(phase, 0) + own * 1000
    return totals


def boundaries(profile):
    """Inclusive diagnostic boundaries; never add them to exclusive stages."""
    result = {}
    for (_filename, _, name), (_, _, _, cumulative, callers) in pstats.Stats(profile).stats.items():
        if name in {"snapshot_dict", "read_library_document", "project_library_snapshot", "project_document_state"}:
            result[name] = cumulative * 1000
        if name == "deepcopy":
            for (caller_file, _, caller_name), metrics in callers.items():
                if caller_file.endswith("app/application.py") and caller_name == "<lambda>":
                    result["legacy_final_snapshot_copy"] = metrics[3] * 1000
                elif caller_file.endswith("app/domain_adapter.py") and caller_name == "snapshot":
                    result["document_final_snapshot_copy"] = metrics[3] * 1000
    return result


def run(repeat, concurrencies, datasets, http_enabled, visible=False, diagnostics_only=False):
    from PyQt6.QtCore import QEventLoop, QObject, Qt, QThread, QTimer, pyqtSignal
    from PyQt6.QtWidgets import QApplication

    import open_garden_planner.app.settings as settings

    # Isolation is installed before constructing any storage or application object.
    settings.ORGANIZATION_NAME = "ogp_synthetic_reactivity"
    settings.APPLICATION_NAME = f"profile-{os.getpid()}"
    from open_garden_planner.agent_api.bridge import MainThreadBridge
    from open_garden_planner.agent_api.mapping import plan_summary_from_snapshot
    from open_garden_planner.agent_api.providers import AgentProviders
    from open_garden_planner.agent_api.read_projection import project_library_snapshot
    from open_garden_planner.agent_api.server import AgentApiServer
    from open_garden_planner.app.application import GardenPlannerApp
    from open_garden_planner.core.project import ProjectManager
    from open_garden_planner.services import plant_library
    from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
    from open_garden_planner.ui.canvas.canvas_view import CanvasView
    from open_garden_planner.ui.canvas.items import RectangleItem

    class Completion(QObject):
        done = pyqtSignal()

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    report = {"python": platform.python_version(), "platform": platform.platform(),
              "qt_platform": app.platformName(), "repeat": repeat,
              "concurrencies": concurrencies, "datasets": {},
              "visible_canvas": visible,
              "diagnostics_only": diagnostics_only,
              "method": "real live adapters; Qt main thread; isolated stores; no production tracing"}
    with tempfile.TemporaryDirectory(prefix="ogp-reactivity-") as root:
        root = Path(root)
        plant_library._default_app_data_dir = lambda: root
        specifications = {"small": (12, 0, 0), "many": (2000, 0, 0),
                          "media": (12, 8_000_000, 0), "history": (12, 0, 10_000)}
        for dataset in datasets:
            print(f"dataset={dataset}", file=sys.stderr, flush=True)
            count, media_size, history_size = specifications[dataset]
            scene, pm, bridge = CanvasScene(), ProjectManager(), MainThreadBridge()
            with scene.suspend_z_refresh():
                for _ in range(count):
                    scene.addItem(RectangleItem(0, 0, 10, 10))
            if media_size:
                scene.items()[0].metadata["extension"] = {"image_data": "A" * media_size}
            pm._manual_tasks = {"task": {"id": "task", "title": "Synthetic"}}
            pm._extra_fields["operation_receipts"] = {
                str(i): {"status": "applied", "fingerprint": str(i), "results": []}
                for i in range(history_size)
            }
            plants = {str(i): {"source_id": str(i), "common_name": f"Plant {i}",
                               "scientific_name": f"Fictitia {i}", "data_source": "custom",
                               "raw_data": {"html": "Source " * 100}} for i in range(count)}
            if media_size:
                plants["0"]["raw_data"]["personal_visual_png"] = "B" * media_size
            library_document = {"version": "1.0", "plants": plants}
            if history_size:
                library_document["_ogp_library_operations"] = {
                    "version": 1, "requests": {str(i): {"request_hash": str(i), "result": {}}
                                               for i in range(history_size)},
                    "transactions": {}, "record_versions": {},
                }
            (root / "custom_plants.json").write_text(json.dumps(library_document), encoding="utf-8")
            window = SimpleNamespace(_project_manager=pm, canvas_scene=scene, _agent_bridge=bridge,
                                     canvas_view=SimpleNamespace(command_manager=SimpleNamespace(execute=lambda _: None)),
                                     _update_plant_database_panel=lambda: None)
            view = None
            if visible:
                view = CanvasView(scene)
                view.resize(1000, 650)
                view.setWindowTitle("OGP — synthetic reactivity probe")
                view.show()
                app.processEvents()
            for name in ("_agent_snapshot", "_agent_document_operation", "_agent_library_operation"):
                setattr(window, name, getattr(GardenPlannerApp, name).__get__(window))
            timing = {"queue": [], "qt": [], "profiles": [], "boundaries": [], "submission": [], "profile": False}
            original_bridge = bridge.run_on_main

            def timed(fn, *, timeout=5.0, timing=timing, original_bridge=original_bridge):
                queued = time.perf_counter()

                def on_qt():
                    assert QThread.currentThread() is app.thread()
                    started = time.perf_counter()
                    timing["queue"].append((started - queued) * 1000)
                    profile = cProfile.Profile() if timing["profile"] else None
                    try:
                        return profile.runcall(fn) if profile else fn()
                    finally:
                        timing["qt"].append((time.perf_counter() - started) * 1000)
                        if profile:
                            timing["profiles"].append(stages(profile))
                            timing["boundaries"].append(boundaries(profile))

                return original_bridge(on_qt, timeout=timeout)

            bridge.run_on_main = timed
            operations = {
                "legacy_summary": lambda window=window: plan_summary_from_snapshot(window._agent_snapshot()).model_dump(),
                "document_tasks": lambda window=window: window._agent_document_operation("read", {"domains": ["tasks"]}),
                "library_page": lambda window=window: project_library_snapshot(window._agent_library_operation("snapshot", {}), limit=2),
                "library_search": lambda window=window: project_library_snapshot(window._agent_library_operation("snapshot", {}), query="Fictitia 1", limit=2),
                "library_prepare": lambda window=window: window._agent_library_operation("prepare", {"records": [
                    {"source_id": "benchmark-new", "scientific_name": "Fictitia new", "common_name": "Synthetic new"}]}),
            }
            with ThreadPoolExecutor(max_workers=max(concurrencies)) as pool:
                def group(fn, concurrency, timing=timing):
                    completion, loop, timer = Completion(), QEventLoop(), QTimer()
                    gaps, durations, errors = [], [], []
                    previous = time.perf_counter()

                    def heartbeat():
                        nonlocal previous
                        now = time.perf_counter()
                        gaps.append((now - previous) * 1000)
                        previous = now

                    def request():
                        started = time.perf_counter()
                        try:
                            return fn()
                        except Exception as exc:
                            errors.append(f"{type(exc).__name__}: {exc}")
                            return None
                        finally:
                            durations.append((time.perf_counter() - started) * 1000)

                    futures = [pool.submit(request) for _ in range(concurrency)]

                    received = 0

                    def finish():
                        nonlocal received
                        received += 1
                        if received == concurrency:
                            heartbeat()
                            loop.quit()

                    completion.done.connect(finish, Qt.ConnectionType.QueuedConnection)
                    for future in futures:
                        future.add_done_callback(lambda _future: completion.done.emit())
                    timer.setInterval(2)
                    timer.timeout.connect(heartbeat)
                    timer.start()
                    # Report thread submission separately: it is harness work,
                    # not a gap in Qt event delivery while its loop is running.
                    loop_started = time.perf_counter()
                    timing["submission"].append((loop_started - previous) * 1000)
                    previous = loop_started
                    loop.exec()
                    timer.stop()
                    return [future.result() for future in futures], durations, gaps, errors

                measurements = {}
                for name, fn in operations.items():
                    entry = {"concurrency": {}}
                    for concurrency in ([] if diagnostics_only else concurrencies):
                        print(f"  {name} concurrency={concurrency}", file=sys.stderr, flush=True)
                        timing["queue"].clear()
                        timing["qt"].clear()
                        timing["submission"].clear()
                        durations, gaps, errors = [], [], []
                        for _ in range(repeat):
                            _, elapsed, intervals, failures = group(fn, concurrency)
                            durations.extend(elapsed)
                            gaps.extend(intervals)
                            errors.extend(failures)
                        entry["concurrency"][str(concurrency)] = {
                            "total": stats(durations), "bridge_queue": stats(timing["queue"]),
                            "qt_service": stats(timing["qt"]), "heartbeat_gap": stats(gaps),
                            "harness_submission": stats(timing["submission"]),
                            "max_event_gap_ms": max(gaps), "errors": errors, "samples": len(durations),
                        }
                    # Instrumented diagnostics run separately from latency samples.
                    worker_profile = cProfile.Profile()
                    timing["profile"] = True
                    timing["profiles"].clear()
                    timing["boundaries"].clear()
                    group(lambda worker_profile=worker_profile, fn=fn: worker_profile.runcall(fn), 1)
                    timing["profile"] = False
                    entry["exclusive_profile_ms"] = {"qt": list(timing["profiles"]), "worker": stages(worker_profile)}
                    entry["inclusive_diagnostic_ms"] = {"qt": list(timing["boundaries"]), "worker": boundaries(worker_profile)}
                    tracemalloc.start()
                    group(fn, 1)
                    _, entry["peak_python_allocated_bytes"] = tracemalloc.get_traced_memory()
                    tracemalloc.stop()
                    measurements[name] = entry
                if http_enabled:
                    providers = AgentProviders(**dict.fromkeys(AgentProviders.__dataclass_fields__))
                    providers = replace(providers, snapshot=window._agent_snapshot,
                                        document_operation=window._agent_document_operation,
                                        library_operation=window._agent_library_operation)
                    with socket.socket() as listener:
                        listener.bind(("127.0.0.1", 0))
                        port = listener.getsockname()[1]
                    server = AgentApiServer(providers, port=port, writes_enabled=True, write_token="synthetic-profile")
                    server.start()
                    try:
                        values, _, _, failures = group(lambda server=server: asyncio.run(http_measure(server, repeat, concurrencies)), 1)
                        if failures:
                            raise RuntimeError(f"HTTP harness failed: {failures}")
                        measurements["http"] = values[0]
                    finally:
                        server.stop()
                report["datasets"][dataset] = {"objects": count, "media_bytes": media_size,
                                               "receipts": history_size, "operations": measurements}
            scene.clear()
            if view is not None:
                view.close()
                view.deleteLater()
                app.processEvents()
            pm.release_document_ownership()
    settings.create_qsettings().clear()
    report["process_peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
    return report


async def http_measure(server, repeat, concurrencies):
    """Real transport: client wall time and server tool-dispatch spans separately."""
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.server.fastmcp.tools.tool_manager import ToolManager

    server_spans = {}
    original = ToolManager.call_tool

    async def measured(manager, name, *args, **kwargs):
        started = time.perf_counter()
        try:
            return await original(manager, name, *args, **kwargs)
        finally:
            server_spans.setdefault(name, []).append((time.perf_counter() - started) * 1000)

    ToolManager.call_tool = measured
    result = {}
    try:
        async with (
            httpx.AsyncClient(headers={"Authorization": "Bearer synthetic-profile"}, timeout=60) as http_client,
            streamable_http_client(server.url, http_client=http_client) as (read, write, _),
            ClientSession(read, write) as client,
        ):
            await client.initialize()
            for name, arguments in [
                ("get_plan_summary", {}), ("get_document_state", {"domains": ["tasks"]}),
                ("get_custom_plant_library", {"limit": 2}),
                ("get_custom_plant_library", {"query": "Fictitia 1", "limit": 2}),
                    ("prepare_plant_library_import", {"records": [{"source_id": "http-new", "scientific_name": "Fictitia http", "common_name": "Synthetic http"}]}),
            ]:
                key = f"{name}:{'search' if 'query' in arguments else 'default'}"
                result[key] = {}
                for concurrency in concurrencies:
                    print(f"  HTTP {key} concurrency={concurrency}", file=sys.stderr, flush=True)
                    server_spans[name] = []
                    elapsed, failures = [], []

                    async def call(name=name, arguments=arguments, elapsed=elapsed, failures=failures):
                        started = time.perf_counter()
                        response = await client.call_tool(name, arguments)
                        elapsed.append((time.perf_counter() - started) * 1000)
                        if response.isError:
                            failures.append(str(response.content))

                    for _ in range(repeat):
                        await asyncio.gather(*(call() for _ in range(concurrency)))
                    result[key][str(concurrency)] = {"client": stats(elapsed),
                                                     "server_dispatch": stats(server_spans[name]),
                                                     "errors": failures}
    finally:
        ToolManager.call_tool = original
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=6)
    parser.add_argument("--concurrency", default="1,2,4,8,16,32")
    parser.add_argument("--datasets", default="small,many,media,history")
    parser.add_argument("--http", action="store_true")
    parser.add_argument("--visible", action="store_true", help="Show only the synthetic canvas; use QT_QPA_PLATFORM=cocoa for native event delivery")
    parser.add_argument("--diagnostics-only", action="store_true", help="Only separate profiling/allocation samples; no latency or HTTP run")
    args = parser.parse_args()
    levels = [int(value) for value in args.concurrency.split(",")]
    if args.repeat < 1 or not levels or min(levels) < 1 or max(levels) > 32:
        parser.error("repeat must be positive and concurrency bounded to 1..32")
    args.output.write_text(json.dumps(run(args.repeat, levels, args.datasets.split(","), args.http and not args.diagnostics_only, args.visible, args.diagnostics_only), indent=2) + "\n")
    print(args.output)
