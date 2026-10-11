"""The pipeline's gates over the committed edge-case plan (L1.1 fix round 1, #385).

``tests/fixtures/plans/scene3d_edge_cases.ogp`` (``scripts/make_scene3d_edge_plan.py``)
is the third committed plan. The two bench plans hold no stroke that closes on
itself, no turned house, no ellipse and no concave polygon — which is how a
closed fence could become an 80 m² slab with every gate green. Here the same
gates run over exactly those shapes, loaded through the real ``ProjectManager``:

- G8 — 100 snapshots of the untouched plan are identical;
- G7 — every mesh of the default builder validates, tops out at the resolved
  height, and its cap covers the footprint's AREA (the check the defect needed);
- G4 — ``transform ∘ local rings`` reproduces ``item_footprints``, holes included;
- the first ``SceneSync.apply`` adds every record once, the next says nothing.
"""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from open_garden_planner.core import ProjectManager
from open_garden_planner.core.object_types import ObjectType
from open_garden_planner.core.scene3d import (
    RecordingSink,
    SceneDiff,
    SceneSync,
    default_builder,
    diff,
    is_simple_ring,
    pose_ring,
    signed_area,
    verify_builder,
)
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.canvas.footprints import item_footprints
from open_garden_planner.ui.view3d.snapshot import Snapshot, snapshot_records
from tests.integration.test_scene3d_snapshot import (
    JUNE,
    _assert_walls_face_away_from_the_solid,
    _cap_area,
    _ring_deviation,
    _rings_of,
    _signed_area_sum,
    _strip_deviation,
)

REPO = Path(__file__).resolve().parents[2]
PLAN = REPO / "tests" / "fixtures" / "plans" / "scene3d_edge_cases.ogp"
BENCH_PLANS = ("bench_small.ogp", "bench_large.ogp")


@pytest.fixture
def plan(qtbot) -> tuple[CanvasScene, Snapshot, dict[str, Any]]:  # noqa: ARG001
    scene = CanvasScene()
    ProjectManager().load(scene, PLAN)
    items = {str(i.item_id): i for i in scene.items()
             if getattr(i, "object_type", None) is not None and hasattr(i, "item_id")}
    return scene, snapshot_records(scene, JUNE), items


def _by_name(snapshot: Snapshot, name: str):
    (record,) = [r for r in snapshot.values() if r.name == name]
    return record


def test_generator_reproduces_the_committed_fixture(qtbot, tmp_path) -> None:  # noqa: ARG001
    """Regenerating writes the same plan (modulo the save timestamp) — and leaves
    the two bench plans exactly as they are.

    "The same plan" is ``same_plan``: this fixture has turned items, so a few of
    its numbers went through ``cos`` / ``sin`` and may differ in their last bit
    on another platform (the committed file was generated on Windows). On the
    generating platform the comparison is exact as well."""
    import open_garden_planner.app.settings as app_settings

    spec = importlib.util.spec_from_file_location(
        "make_scene3d_edge_plan", REPO / "scripts" / "make_scene3d_edge_plan.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    store = (app_settings.ORGANIZATION_NAME, app_settings.APPLICATION_NAME)
    spec.loader.exec_module(module)
    after = (app_settings.ORGANIZATION_NAME, app_settings.APPLICATION_NAME)
    assert after == store  # importing the generator must not retarget this process's settings
    fresh = tmp_path / module.NAME
    module.bench._save(module.build_edge_cases(), fresh)
    regenerated, committed = module.bench._normalised(fresh), module.bench._normalised(PLAN)
    assert module.same_plan(regenerated, committed), (
        "scene3d_edge_cases.ogp is stale — rerun scripts/make_scene3d_edge_plan.py")
    if sys.platform == "win32":  # where the committed file was generated: bit for bit
        assert regenerated == committed
    for name, build in zip(BENCH_PLANS, (module.bench.build_small, module.bench.build_large),
                           strict=True):
        again = tmp_path / name
        module.bench._save(build(), again)
        assert module.bench._normalised(again) == module.bench._normalised(PLAN.parent / name), name


def test_same_plan_forgives_the_last_bit_and_nothing_else(qtbot) -> None:  # noqa: ARG001
    """The comparison the regeneration test stands on, turned against itself: a
    float moved by one bit is the same plan; a float moved by a micrometre, a
    changed string, a missing key, a reordered list, ``True`` for ``1`` are not."""
    import json
    import math

    spec = importlib.util.spec_from_file_location(
        "make_scene3d_edge_plan", REPO / "scripts" / "make_scene3d_edge_plan.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    committed = json.loads(PLAN.read_text(encoding="utf-8"))
    assert module.same_plan(committed, copy.deepcopy(committed))

    def first_float(node, path=()):
        if isinstance(node, float):
            return path
        children = node.items() if isinstance(node, dict) else enumerate(node) if isinstance(node, list) else ()
        for key, child in children:
            found = first_float(child, (*path, key))
            if found is not None:
                return found
        return None

    def changed(path, change):
        plan = copy.deepcopy(committed)
        node = plan
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = change(node[path[-1]])
        return plan

    where = first_float(committed["objects"])
    assert where is not None
    where = ("objects", *where)
    assert module.same_plan(changed(where, lambda x: math.nextafter(x, math.inf)), committed)
    assert not module.same_plan(changed(where, lambda x: x + 1e-4), committed)  # a micrometre
    assert not module.same_plan(changed(("objects", 0, "object_type"), lambda _x: "SHED"), committed)
    assert not module.same_plan(changed(("objects",), lambda items: items[1:]), committed)
    assert not module.same_plan(changed(("objects",), lambda items: items[::-1]), committed)
    assert not module.same_plan({"a": True}, {"a": 1}) and not module.same_plan({"a": 1}, {"b": 1})
    assert module.same_plan({"a": 2}, {"a": 2.0})  # 2 and 2.0 are one number in JSON


def test_what_the_plan_contains(plan) -> None:
    """The cases, by name — so a regenerated plan cannot quietly lose one."""
    _scene, snapshot, items = plan
    assert snapshot.skipped == ()
    assert len(items) == 12 and len(snapshot) == 11  # every item but the roof ridge
    kinds = sorted(record.kind for record in snapshot.values())
    assert kinds == ["CONTAINER_ROUND", "FENCE", "FENCE", "FENCE", "HEDGE_POLYGON", "HOUSE",
                     "PERENNIAL", "PERENNIAL", "PERENNIAL", "POND_POOL", "RAISED_BED"]
    assert sum(1 for i in items.values() if i.object_type is ObjectType.ROOF_RIDGE) == 1

    house = _by_name(snapshot, "House")
    assert house.transform.rotation_deg == 25.0 and house.shape == "polygon"
    (start, end) = house.params["ridge"]  # type: ignore[misc]
    assert start == pytest.approx((-450.0, 0.0), abs=1e-6) and end == pytest.approx((450.0, 0.0), abs=1e-6)

    pond = _by_name(snapshot, "Pond")
    assert pond.shape == "ellipse" and pond.transform.rotation_deg == 40.0

    closed, nearly, open_u = (_by_name(snapshot, name) for name in
                              ("Closed fence", "Nearly closed fence", "Open U fence"))
    assert [len(r.footprints) for r in (closed, nearly, open_u)] == [1, 1, 1]
    assert [sum(len(g) for g in r.holes) for r in (closed, nearly, open_u)] == [1, 1, 0]
    assert closed.path_width_cm == nearly.path_width_cm == open_u.path_width_cm == 10.0
    # 36 m, 35.98 m and 26 m of fence, 10 cm wide — not the 80 m² they enclose
    assert closed.plan_area_cm2 == pytest.approx(36000.0, rel=5e-3)
    assert nearly.plan_area_cm2 == pytest.approx(35980.0, rel=5e-3)
    assert open_u.plan_area_cm2 == pytest.approx(26000.0, rel=5e-3)

    bed = _by_name(snapshot, "Turned raised bed")
    assert bed.transform.rotation_deg == 30.0
    children = [r for r in snapshot.values() if r.parent_id == bed.item_id]
    assert sorted(str(r.params["species_name"]) for r in children) == ["Basil", "Tomato"]
    pot = _by_name(snapshot, "Pot")
    (herb,) = [r for r in snapshot.values() if r.parent_id == pot.item_id]
    assert herb.params["species_name"] == "Rosemary" and pot.params["radius_cm"] == 30.0

    hedge = _by_name(snapshot, "L-shaped hedge")
    assert len(hedge.footprints[0]) == 6 and hedge.holes == ()
    assert hedge.plan_area_cm2 == pytest.approx(900.0 * 300.0 + 400.0 * 400.0)  # concave: not its hull


def test_g8_the_untouched_plan_snapshots_identically_100_times(plan) -> None:
    scene, first, _items = plan
    for _ in range(100):
        again = snapshot_records(scene, JUNE)
        assert again == first and list(again) == list(first) and again.skipped == ()
        assert diff(first, again).is_empty and diff(again, first).is_empty


def test_g7_every_mesh_validates_reaches_its_height_and_covers_its_footprint(plan) -> None:
    _scene, snapshot, _items = plan
    solids = 0
    for record in snapshot.values():
        assert all(is_simple_ring(ring) and signed_area(ring) > 0.0 for ring in record.footprints)
        parts = verify_builder(default_builder, record, plan_margin_cm=0.0)  # nothing outside at all
        assert bool(parts) is (record.height_cm is not None), record.name
        for part in parts:
            part.validate()
            assert float(part.mesh.bounds()[1][2]) == pytest.approx(record.height_cm, rel=1e-6)
            assert _cap_area(part.mesh) == pytest.approx(record.plan_area_cm2, rel=1e-6), record.name
            _assert_walls_face_away_from_the_solid(part.mesh, record)
            solids += 1
    assert solids >= 8  # house, three fences, bed, pot, hedge, plants with a height


def test_g4_transform_and_local_rings_reproduce_the_2d_footprints(plan) -> None:
    _scene, snapshot, items = plan
    worst_exact = worst_strip = 0.0
    for item_id, record in snapshot.items():
        expected = item_footprints(items[item_id], JUNE)
        posed = _rings_of(record)
        assert len(posed) == len(expected), record.name  # every ring: outers AND holes
        if record.shape == "polyline":
            worst_strip = max(worst_strip, _strip_deviation(posed, expected))
            centre_line = [(p.x(), p.y()) for p in
                           (items[item_id].mapToScene(q) for q in items[item_id].points)]
            worst_exact = max(worst_exact, _ring_deviation(
                pose_ring(record.transform, record.path), centre_line))
            # the stroke's area, read even-odd from the 2D rings, is the record's
            assert record.plan_area_cm2 == pytest.approx(_signed_area_sum(expected), rel=5e-4)
        else:
            worst_exact = max(worst_exact, max(
                _ring_deviation(ring, scene_ring)
                for ring, scene_ring in zip(posed, expected, strict=True)))
    assert worst_exact <= 1e-6, worst_exact
    assert worst_strip <= 3e-3, worst_strip


def test_the_pipeline_adds_every_record_once_then_says_nothing(plan) -> None:
    scene, snapshot, _items = plan
    sink = RecordingSink()
    sync = SceneSync(sink, validate=True)
    result = sync.apply(snapshot)
    assert result == SceneDiff(added=tuple(snapshot)) and sync.failures == {}
    assert sink.ops() == [("begin", None), *[("add", i) for i in snapshot], ("commit", None)]
    sink.clear_calls()
    for _ in range(3):
        assert sync.apply(snapshot_records(scene, JUNE)).is_empty
    assert sink.calls == [] and sink.state() == sync.expected_state()


def test_turning_the_house_in_the_loaded_plan_is_one_transform_entry(plan) -> None:
    """The loaded house and its loaded ridge item, turned by the app's own rule."""
    scene, before, items = plan
    house = next(i for i in items.values() if i.object_type is ObjectType.HOUSE)
    house._apply_rotation(205.0)
    after = snapshot_records(scene, JUNE)
    assert diff(before, after) == SceneDiff(transform=(str(house.item_id),))
    assert after[str(house.item_id)].transform.rotation_deg == 205.0
