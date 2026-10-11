"""Build budget of the plant silhouettes (Phase 17 L1.7, #385, gate G7).

Every plant of the two bench plans, at two dates, goes through the real pipeline
— ``snapshot_records`` → ``SceneSync`` → ``RecordingSink`` with the plant
builder registered — and three things are pinned:

- **time**: building all plants of a plan from scratch. Measured on the dev box
  (Windows 11, Python 3.12.9, 2026-10-11), on a quiet machine and one commit
  before the builder was frozen: ``bench_small`` (61 plants) median 66–68 ms,
  ``bench_large`` (320 plants) median 329–333 ms — about 1.0 ms a plant, sink
  bookkeeping included. The frozen builder (its shell leaves are cast onto the
  lobes) costs 1.0–1.35 times that — compared with it in one process,
  interleaved, three times: 1.01–1.08 by the median, 1.12–1.35 by the minimum —
  and this test read 196–208 ms and 750–958 ms while three other full test runs
  shared the machine. No quiet reading of the frozen builder exists. The bounds
  below are a tripwire against a builder that starts looping in Python, not a
  frame budget (when builds run, and off which thread, is L1.3's);
- **triangles**: no plant over ``TRIANGLE_BUDGET``, and the plan totals
  (measured: 70,802 for ``bench_small``, 300,212 / 300,292 for ``bench_large``
  in June / December — a conifer's tier count follows its proportions);
- **a date change regrows only what grew**: scrubbing June → December reaches
  the sink as one ``replace_geometry`` per plant whose height or canopy changed
  and nothing else — a plant that did not grow is not rebuilt.

Timing conventions are those of ``test_scene3d_snapshot_budget.py`` (a median;
× 3 on CI runners; × 10 under a line tracer, where a timing measures the tracer).
"""

from __future__ import annotations

import os
import statistics
import time
from datetime import date
from pathlib import Path

import pytest

from open_garden_planner.core import ProjectManager
from open_garden_planner.core.plant_art import PLANT_KINDS
from open_garden_planner.core.scene3d import BuilderRegistry, Record, RecordingSink, SceneSync, diff
from open_garden_planner.core.scene3d.builders.plants import (
    PART_PLACEHOLDER,
    TRIANGLE_BUDGET,
    register,
)
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.view3d.snapshot import snapshot_records
from tests.perf.test_scene3d_snapshot_budget import budget_factor, traced

PLANS = Path(__file__).resolve().parents[1] / "fixtures" / "plans"
JUNE, DECEMBER = date(2026, 6, 21), date(2026, 12, 21)
_IS_CI = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"
#: plan → (plants, plants that grow June → December, build budget in ms, runs)
BUDGETS = {"bench_small.ogp": (61, 29, 400.0, 9), "bench_large.ogp": (320, 80, 2000.0, 5)}


def plant_registry() -> BuilderRegistry:
    registry = BuilderRegistry()
    register(registry)
    return registry


def plants_of(records: dict[str, Record]) -> dict[str, Record]:
    return {item_id: record for item_id, record in records.items() if record.kind in PLANT_KINDS}


def triangle_count(sink: RecordingSink) -> tuple[int, int]:
    """(all triangles, the largest plant's) of what the sink holds."""
    counts = [sum(part.mesh.triangle_count for part in sink.item(item_id).parts)
              for item_id in sink.item_ids()]
    return sum(counts), max(counts)


@pytest.fixture(params=sorted(BUDGETS))
def plan(request: pytest.FixtureRequest, qtbot) -> tuple[CanvasScene, str]:  # noqa: ARG001
    scene = CanvasScene()
    ProjectManager().load(scene, PLANS / request.param)
    return scene, request.param


@pytest.mark.perf
class TestPlantBuildBudget:
    def test_building_every_plant_of_a_plan(self, plan: tuple[CanvasScene, str]) -> None:
        scene, name = plan
        plants, _grow, budget_ms, runs = BUDGETS[name]
        budget_ms *= budget_factor(is_traced=traced(), is_ci=_IS_CI)
        for at in (JUNE, DECEMBER):
            records = plants_of(snapshot_records(scene, at))
            assert len(records) == plants
            times, sink = [], RecordingSink()
            for _ in range(runs + 1):  # the first run warms the caches
                sink = RecordingSink()
                sync = SceneSync(sink, plant_registry())
                start = time.perf_counter()
                sync.apply(records)
                times.append((time.perf_counter() - start) * 1000.0)
                assert sync.build_count == plants and not sync.failures
            median_ms = statistics.median(times[1:])
            total, largest = triangle_count(sink)
            print(f"\n{name} at {at}: {plants} plants, {total} triangles (largest {largest}), "
                  f"build median {median_ms:.0f} ms (max {max(times[1:]):.0f} ms)")
            assert largest <= TRIANGLE_BUDGET and total <= plants * TRIANGLE_BUDGET
            assert median_ms <= budget_ms, (
                f"{name}: building {plants} plants took {median_ms:.0f} ms (median of {runs}), "
                f"budget {budget_ms:.0f} ms")

    def test_every_bench_plant_is_a_valid_real_plant(self, plan: tuple[CanvasScene, str]) -> None:
        """With ``validate=True`` no builder fails, and no bench plant is a
        placeholder: every one has a species and so a height."""
        scene, name = plan
        for at in (JUNE, DECEMBER):
            sink = RecordingSink()
            sync = SceneSync(sink, plant_registry(), validate=True)
            sync.apply(plants_of(snapshot_records(scene, at)))
            assert not sync.failures, {k: str(v.error) for k, v in sync.failures.items()}
            assert len(sink.item_ids()) == BUDGETS[name][0]
            for item_id in sink.item_ids():
                held = sink.item(item_id)
                assert held.parts and all(p.name != PART_PLACEHOLDER for p in held.parts)
                top = max(float(p.mesh.positions[:, 2].max()) for p in held.parts)
                assert top == pytest.approx(sync.records[item_id].height_cm, rel=1e-5)

    def test_a_date_change_regrows_only_the_plants_that_grew(
        self, plan: tuple[CanvasScene, str]
    ) -> None:
        scene, name = plan
        plants, grow, _budget, _runs = BUDGETS[name]
        june = plants_of(snapshot_records(scene, JUNE))
        december = plants_of(snapshot_records(scene, DECEMBER))
        grown = diff(june, december)
        assert len(grown.geometry) == grow
        assert not (grown.added or grown.removed or grown.transform or grown.material)
        sink = RecordingSink()
        sync = SceneSync(sink, plant_registry(), validate=True)
        sync.apply(june)
        assert sync.build_count == plants
        before = {item_id: sink.item(item_id).parts for item_id in sink.item_ids()}
        sink.clear_calls()
        sync.apply(december)
        assert sink.ops() == [("begin", None),
                              *(("replace_geometry", item_id) for item_id in grown.geometry),
                              ("commit", None)]
        assert sync.build_count == plants + grow          # one build per grown plant, no more
        for item_id in sink.item_ids():
            if item_id in grown.geometry:
                assert sink.item(item_id).parts != before[item_id]
                assert december[item_id].height_cm != june[item_id].height_cm or (
                    december[item_id].params["radius_cm"] != june[item_id].params["radius_cm"])
            else:
                assert sink.item(item_id).parts is before[item_id]   # not rebuilt, not re-sent
        sink.clear_calls()
        sync.apply(december)                                # the same date again: silence
        assert sink.ops() == [] and sync.build_count == plants + grow
