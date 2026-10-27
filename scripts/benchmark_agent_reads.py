"""Reproducible synthetic MCP read profile; never opens user storage.

Run from the repository: .venv/bin/python scripts/benchmark_agent_reads.py --output /tmp/ogp-profile.json
Stage samples are exclusive cProfile CPU wall timings (instrumented once), separate
from uninstrumented latency samples. HTTP timings include local SDK/client overhead.
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import cProfile
import importlib.util
import json
import math
import os
import platform
import resource
import socket
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Any

sys.path.insert(
    0, os.environ.get("OGP_BENCH_SOURCE_ROOT", str(Path(__file__).resolve().parents[1] / "src"))
)

from open_garden_planner.agent_api.document_tools import DomainProviders
from open_garden_planner.agent_api.read_projection import project_library_snapshot
from open_garden_planner.agent_api.server import AgentApiServer
from open_garden_planner.core.document_session import DocumentSession
from open_garden_planner.core.plan_io import write_plan
from open_garden_planner.services.library_port import LibraryPort


def size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def tokens(value: Any) -> dict[str, int]:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if importlib.util.find_spec("tiktoken"):
        import tiktoken

        return {"tokens_cl100k_base": len(tiktoken.get_encoding("cl100k_base").encode(text))}
    return {"estimated_tokens_utf8_div_4": math.ceil(len(text.encode()) / 4)}


def stats(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "median_ms": statistics.median(values),
        "p95_ms": ordered[math.ceil(0.95 * len(ordered)) - 1],
    }


def measure(fn, repeat):
    start = time.perf_counter()
    first = fn()
    initial = (time.perf_counter() - start) * 1000
    times = []
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        times.append((time.perf_counter() - start) * 1000)
    tracemalloc.start()
    fn()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    profiler = cProfile.Profile()
    profiler.runcall(fn)
    phases: dict[str, float] = {}
    # Exclusive times avoid double counting nested operations.
    import pstats

    for (filename, _, name), (_, _, own, _, _) in pstats.Stats(profiler).stats.items():
        phase = "other"
        if "deepcopy" in name or name == "_deepcopy_dict":
            phase = "copy"
        elif "fingerprint" in name or "_plants_revision" in name or "hashlib" in filename:
            phase = "revision"
        elif "snapshot" in name:
            phase = "snapshot"
        elif "read" in name and ("io" in filename or "library_operations" in filename):
            phase = "disk_read_validation"
        elif name in {"_strip_media", "_object_summary", "_is_media_key", "_is_data_uri"}:
            phase = "filter"
        elif "project_" in name or "_walk_media" in name or "_collection_page" in name:
            phase = "projection_manifest"
        elif "json" in filename or "encode" in name or "decode" in name:
            phase = "serialization_encoding"
        elif name == "dispatch":
            phase = "dispatch"
        phases[phase] = phases.get(phase, 0) + own * 1000
    return {
        "initial_ms": initial,
        **stats(times),
        "utf8_bytes": size(first),
        **tokens(first),
        "peak_allocated_bytes": peak,
        "exclusive_profile_ms": phases,
    }


async def instrumented_http_profile(host, library, repeat):
    """Instrument only this subprocess, leaving production tracing disabled."""
    from mcp.server.fastmcp.tools.tool_manager import ToolManager
    from mcp.server.fastmcp.utilities.func_metadata import FuncMetadata

    import open_garden_planner.agent_api.server as server_module

    calls = []
    current = contextvars.ContextVar("benchmark_request", default=None)
    original_middleware = server_module._bearer_token_middleware
    original_call = ToolManager.call_tool
    original_convert = FuncMetadata.convert_result

    async def call(manager, name, *args, **kwargs):
        record = current.get()
        started = time.perf_counter()
        if record is not None:
            record["tool"] = name
        try:
            return await original_call(manager, name, *args, **kwargs)
        finally:
            if record is not None:
                record["tool_dispatch_ms"] = (time.perf_counter() - started) * 1000

    def convert(metadata, value):
        started = time.perf_counter()
        try:
            return original_convert(metadata, value)
        finally:
            record = current.get()
            if record is not None:
                record["sdk_result_conversion_ms"] = (time.perf_counter() - started) * 1000

    def middleware(app):
        wrapped = original_middleware(app)

        async def measured(scope, receive, send):
            if scope["type"] != "http":
                return await wrapped(scope, receive, send)
            record = {"asgi_send_ms": 0.0, "response_body_bytes": 0}
            token = current.set(record)
            started = time.perf_counter()

            async def measured_send(message):
                before = time.perf_counter()
                await send(message)
                record["asgi_send_ms"] += (time.perf_counter() - before) * 1000
                record["response_body_bytes"] += len(message.get("body", b""))

            try:
                await wrapped(scope, receive, measured_send)
            finally:
                record["server_request_ms"] = (time.perf_counter() - started) * 1000
                calls.append(record)
                current.reset(token)

        return measured

    server_module._bearer_token_middleware = middleware
    ToolManager.call_tool = call
    FuncMetadata.convert_result = convert
    try:
        result = await http_profile(host, library, repeat)
    finally:
        server_module._bearer_token_middleware = original_middleware
        ToolManager.call_tool = original_call
        FuncMetadata.convert_result = original_convert
    result["server_spans"] = {
        name: {
            field: stats([row[field] for row in calls if row.get("tool") == name and field in row])
            for field in (
                "server_request_ms",
                "tool_dispatch_ms",
                "sdk_result_conversion_ms",
                "asgi_send_ms",
            )
        }
        for name in sorted({row["tool"] for row in calls if "tool" in row})
    }
    return result


async def http_profile(host, library, repeat):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = AgentApiServer(
        DomainProviders(host.dispatch, library.dispatch),
        port=port,
        writes_enabled=True,
        write_token="synthetic-profile-only",
    )
    server.start()
    results = {}
    try:
        async with (
            streamablehttp_client(
                server.url, headers={"Authorization": "Bearer synthetic-profile-only"}
            ) as (reader, writer, _),
            ClientSession(reader, writer) as client,
        ):
            await client.initialize()
            for name, args in [
                ("get_document_state", {"domains": ["tasks"]}),
                ("get_custom_plant_library", {"limit": 2}),
                ("get_capabilities", {}),
            ]:
                samples = []
                payload = None
                for _ in range(repeat):
                    start = time.perf_counter()
                    payload = await client.call_tool(name, args)
                    if payload.isError:
                        raise RuntimeError(str(payload.content))
                    samples.append((time.perf_counter() - start) * 1000)
                results[name] = {
                    **stats(samples),
                    "envelope_utf8_bytes": len(payload.model_dump_json().encode()),
                    "text_utf8_bytes": sum(
                        len(c.text.encode()) for c in payload.content if c.type == "text"
                    ),
                }
            # Capability arguments are additive; old baselines reject this call.
            compact = await client.call_tool("get_capabilities", {"detail": "summary"})
            if not compact.isError:
                results["capabilities_summary"] = {
                    "envelope_utf8_bytes": len(compact.model_dump_json().encode())
                }
            # One observed end-to-end agent path on this disposable document.
            # No claim of fewer calls: compact discovery adds one cold schema fetch.
            workflow_start = time.perf_counter()
            workflow_calls = []

            async def step(name, arguments):
                value = await client.call_tool(name, arguments)
                if value.isError:
                    raise RuntimeError(str(value.content))
                workflow_calls.append(
                    {"tool": name, "envelope_utf8_bytes": len(value.model_dump_json().encode())}
                )
                return json.loads(value.content[0].text)

            await step("get_custom_plant_library", {"query": "Fictitia 1", "limit": 2})
            before = await step("get_document_state", {"domains": ["tasks"]})
            prepared = await step(
                "prepare_document_operations",
                {
                    "operations": [
                        {
                            "operation": "add_manual_task",
                            "fields": {"title": f"Synthetic batch {index}"},
                        }
                        for index in range(10)
                    ],
                    "expected_revision": before["revision"],
                },
            )
            await step(
                "apply_document_operations",
                {
                    "preparation_id": prepared["preparation_id"],
                    "request_key": "synthetic-benchmark-batch",
                },
            )
            await step("get_document_operation", {"request_key": "synthetic-benchmark-batch"})
            results["workflow_search_prepare_apply_verify"] = {
                "calls": workflow_calls,
                "call_count": len(workflow_calls),
                "total_ms": (time.perf_counter() - workflow_start) * 1000,
            }
            samples = []

            async def read():
                start = time.perf_counter()
                result = await client.call_tool("get_document_state", {"domains": ["tasks"]})
                if result.isError:
                    raise RuntimeError(str(result.content))
                return (time.perf_counter() - start) * 1000

            for _ in range(max(2, repeat // 4)):
                samples.extend(await asyncio.gather(*(read() for _ in range(4))))
            results["concurrent_tasks_4"] = stats(samples)
    finally:
        server.stop()
    return results


def run(repeat):
    result = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "repeat": repeat,
        "tokenizer": "unavailable; estimate = ceil(compact JSON UTF-8 bytes / 4), not billed tokens",
        "datasets": {},
    }
    if importlib.util.find_spec("tiktoken"):
        import tiktoken

        result["tokenizer"] = f"tiktoken {getattr(tiktoken, '__version__', 'unknown')} cl100k_base"
    with tempfile.TemporaryDirectory(prefix="ogp-agent-profile-") as root:
        for name, count, media_bytes, receipts in [
            ("small", 12, 0, 0),
            ("many", 2000, 0, 0),
            ("media", 12, 8_000_000, 0),
            ("history", 12, 0, 10000),
        ]:
            folder = Path(root) / name
            folder.mkdir()
            plan, library_path = folder / "plan.ogp", folder / "library.json"
            objects = [
                {
                    "type": "rectangle",
                    "item_id": str(i),
                    "name": f"Object {i}",
                    "metadata": {"flag": False},
                }
                for i in range(count)
            ]
            if media_bytes:
                objects[0]["metadata"]["extension"] = {"image_data": "A" * media_bytes}
            document = {
                "version": "1.4",
                "canvas": {"width": 500, "height": 500},
                "objects": objects,
                "manual_tasks": {"task": {"title": "Synthetic"}},
                "operation_receipts": {
                    str(i): {"status": "applied", "fingerprint": str(i), "results": [{"value": 0}]}
                    for i in range(receipts)
                },
            }
            plants = {
                str(i): {
                    "source_id": str(i),
                    "common_name": f"Plant {i}",
                    "scientific_name": f"Fictitia {i}",
                    "raw_data": {"html": "Source " * 100},
                }
                for i in range(count)
            }
            if media_bytes:
                plants["0"]["raw_data"]["personal_visual_png"] = "B" * media_bytes
            write_plan(plan, document)
            library_path.write_text(json.dumps({"version": "1.0", "plants": plants}))
            with DocumentSession(plan) as host:
                library = LibraryPort(library_path)
                result["datasets"][name] = {
                    "objects": count,
                    "media_bytes": media_bytes,
                    "receipts": receipts,
                    "tasks": measure(lambda: host.dispatch("read", {"domains": ["tasks"]}), repeat),
                    "objects_page": measure(
                        lambda: host.dispatch("read", {"domains": ["objects"], "limit": 2}), repeat
                    ),
                    "library_page": measure(
                        lambda library=library: project_library_snapshot(
                            library.dispatch("snapshot", {}), limit=2
                        ),
                        repeat,
                    ),
                    "library_search": measure(
                        lambda library=library: project_library_snapshot(
                            library.dispatch("snapshot", {}), query="Fictitia 1", limit=2
                        ),
                        repeat,
                    ),
                }
                if name in {"small", "media"}:
                    result["datasets"][name]["http_local_sdk"] = asyncio.run(
                        instrumented_http_profile(host, library, repeat)
                    )
    result["process_peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (
        1 if sys.platform == "darwin" else 1024
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=12)
    args = parser.parse_args()
    if args.repeat < 2:
        parser.error("repeat must be >= 2")
    report = run(args.repeat)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(args.output)
