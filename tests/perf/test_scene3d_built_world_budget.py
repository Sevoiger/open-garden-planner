"""Build budget of the built world (Phase 17 L1.6, #385, gate G7).

Every built-world item of the two bench plans — buildings, fences, walls, beds,
containers, hedges — through ``SceneSync`` (production registry) into a
``RecordingSink``: how long the builders take and how many triangles they make.

Measured on the dev box (Windows 11, Python 3.12.9, 2026-10-11, 30 runs each,
other test runs sharing the machine):

==================  =======  ==========  =================  ================
plan                items    triangles   median build (ms)  budget (ms)
==================  =======  ==========  =================  ================
bench_small.ogp     15       16,874      9.8 (max 10.5)     40
bench_large.ogp     28       22,482      18.7 (max 22.4)    60
==================  =======  ==========  =================  ================

Where the time goes: a hedge at its 6,000-leaf cap (12,010 triangles) costs
~4 ms, a 24 m picket fence ~1.5 ms, a raised bed ~0.4 ms (24 of them on
bench_large), a house ~1 ms. The budgets are generous on purpose (3-4x the
measured medians): this is a tripwire against a builder that starts looping in
Python, not a benchmark.
Same conventions as ``test_scene3d_snapshot_budget.py``: the ``perf`` marker, a
median, x3 on a CI runner, x10 under a line tracer (numpy-heavy code slows less
than that under coverage, so x10 is ample).

Triangles are deterministic, so they are asserted exactly per kind — a builder
that changes its triangle count changes this table knowingly.
"""

from __future__ import annotations

import collections
import statistics
import time
from datetime import date
from pathlib import Path

import pytest

from open_garden_planner.core import ProjectManager
from open_garden_planner.core.scene3d import Record, RecordingSink, SceneSync
from open_garden_planner.core.scene3d.builders import BUILT_WORLD_KINDS, production_registry
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.view3d.snapshot import snapshot_records
from tests.perf.test_scene3d_snapshot_budget import _IS_CI, budget_factor, traced

PLANS = Path(__file__).resolve().parents[1] / "fixtures" / "plans"
AT = date(2026, 6, 21)
RUNS = 30
WARMUP = 3

#: plan → (median build budget in ms, triangles per kind — exact)
EXPECTED: dict[str, tuple[float, dict[str, tuple[int, int]]]] = {
    "bench_small.ogp": (40.0, {
        "CONTAINER_ROUND": (3, 786), "FENCE": (2, 3690), "GARAGE_SHED": (1, 42),
        "GARDEN_BED": (3, 0), "HEDGE_POLYGON": (1, 12010), "HOUSE": (1, 42),
        "RAISED_BED": (3, 270), "WALL": (1, 34),
    }),
    "bench_large.ogp": (60.0, {
        "FENCE": (1, 8228), "GARAGE_SHED": (1, 42), "HEDGE_POLYGON": (1, 12010),
        "HOUSE": (1, 42), "RAISED_BED": (24, 2160),
    }),
}


def built_world(plan: str) -> dict[str, Record]:
    scene = CanvasScene()
    ProjectManager().load(scene, PLANS / plan)
    return {item_id: record for item_id, record in snapshot_records(scene, AT).items()
            if record.kind in BUILT_WORLD_KINDS}


@pytest.fixture(params=sorted(EXPECTED))
def plan(request: pytest.FixtureRequest, qtbot) -> tuple[str, dict[str, Record]]:  # noqa: ARG001
    return request.param, built_world(request.param)


def test_triangles_per_kind(plan: tuple[str, dict[str, Record]]) -> None:
    name, records = plan
    sink = RecordingSink()
    sync = SceneSync(sink, production_registry(), validate=True)
    sync.apply(records)
    assert sync.failures == {}
    counted: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    for item_id, record in records.items():
        entry = counted[record.kind]
        entry[0] += 1
        entry[1] += sum(part.mesh.triangle_count for part in sink.item(item_id).parts)
    assert {kind: (items, triangles) for kind, (items, triangles) in counted.items()} == EXPECTED[name][1]


@pytest.mark.perf
def test_build_time_median(plan: tuple[str, dict[str, Record]]) -> None:
    name, records = plan
    budget_ms = EXPECTED[name][0] * budget_factor(is_traced=traced(), is_ci=_IS_CI)
    registry = production_registry()

    def build_everything() -> None:
        sync = SceneSync(RecordingSink(), registry)
        sync.apply(records)
        assert sync.build_count == len(records) and not sync.failures

    for _ in range(WARMUP):
        build_everything()
    times = []
    for _ in range(RUNS):
        start = time.perf_counter()
        build_everything()
        times.append((time.perf_counter() - start) * 1000.0)
    median_ms = statistics.median(times)
    print(f"\n[G7] {name}: {len(records)} built-world items, median {median_ms:.2f} ms, "
          f"max {max(times):.2f} ms over {RUNS} runs (budget {budget_ms:.0f} ms)")
    assert median_ms <= budget_ms, (
        f"building {len(records)} built-world items of {name} took a median of {median_ms:.2f} ms "
        f"(max {max(times):.2f} ms), over the {budget_ms:.0f} ms budget"
    )


def test_the_budgets_are_what_the_gate_states() -> None:
    assert {plan: budget for plan, (budget, _kinds) in EXPECTED.items()} == {
        "bench_small.ogp": 40.0, "bench_large.ogp": 60.0}
