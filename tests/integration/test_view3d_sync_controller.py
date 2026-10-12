"""The 3D sync controller against a real canvas and a ``RecordingSink`` (L1.3, ADR-057).

``ui/view3d/sync_controller.Scene3DController`` is the wiring between the plan and
the 3D engine: change signals in, at most one pipeline pass per event-loop turn
out. Everything here runs without a GPU — the sink is the recording double — and
through the REAL signals: ``QGraphicsScene.changed``, the undo stack's
``stack_changed`` and the sim clock's two channels. Gates of the story pinned here:
G4 (paused while hidden; one catch-up pass that equals a from-scratch apply) and
G9 (time → light only; date → the dated plants regrow, nothing else; a destroyed
subscriber is never called).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time
from typing import Any

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QColor

from open_garden_planner.core.commands import (
    CreateItemCommand,
    DeleteItemsCommand,
    MoveItemsCommand,
    ResizeItemCommand,
)
from open_garden_planner.core.object_height import METADATA_KEY
from open_garden_planner.core.object_types import ObjectType
from open_garden_planner.core.scene3d import (
    GroundSpec,
    Record,
    RecordingSink,
    SceneSync,
    SinkProtocolError,
    SunState,
)
from open_garden_planner.core.scene3d.builders import plants, production_registry
from open_garden_planner.core.scene3d.camera import Bounds
from open_garden_planner.core.sim_clock import SimClock
from open_garden_planner.core.solar import solar_position
from open_garden_planner.ui.canvas.canvas_view import CanvasView
from open_garden_planner.ui.canvas.geometry_apply import (
    apply_rect_like_geometry,
    build_rect_resize,
)
from open_garden_planner.ui.canvas.items.circle_item import CircleItem
from open_garden_planner.ui.canvas.items.rectangle_item import RectangleItem
from open_garden_planner.ui.view3d.snapshot import SkipReason, snapshot_records
from open_garden_planner.ui.view3d.sync_controller import (
    NO_LOCATION_SUN,
    Scene3DController,
    TickReport,
    plain_ground,
)
from tests.integration.test_scene3d_plants import drop
from tests.integration.test_scene3d_snapshot import _recolour as recolour_fill

BERLIN = {"latitude": 52.52, "longitude": 13.405}
NOON = datetime(2026, 6, 21, 12, 0)
BEGIN, COMMIT = ("begin", None), ("commit", None)
GROUND, SUN = ("set_ground", None), ("set_sun", None)


class Rig:
    """A canvas, a clock, a recording sink and the controller between them — wired
    the way the workspace wires them."""

    def __init__(self, view: CanvasView, qtbot: Any, *, location: dict[str, float] | None = None,
                 sink: Any = None, registry: Any = None) -> None:
        self.view, self.qtbot = view, qtbot
        self.scene = view.scene()
        self.commands = view.command_manager
        self.clock = SimClock(NOON)
        self.location = location
        self.sink = sink if sink is not None else RecordingSink()
        self.controller = Scene3DController(
            self.scene, self.clock, lambda: self.location, self.sink, registry)
        self.commands.stack_changed.connect(self.controller.mark_dirty)
        self.reports: list[TickReport] = []
        self.controller.applied.connect(self.reports.append)

    def settle(self) -> list[tuple[str, str | None]]:
        """Let the event loop run until the pipeline is quiet; the sink calls since
        the last ``settle``."""
        self.qtbot.wait(70)
        calls = self.recorder.ops()
        self.recorder.clear_calls()
        return calls

    @property
    def recorder(self) -> RecordingSink:
        return self.sink if isinstance(self.sink, RecordingSink) else self.sink.inner

    def bed(self, x: float = 400.0, y: float = 300.0) -> RectangleItem:
        item = RectangleItem(x, y, 300.0, 200.0, object_type=ObjectType.RAISED_BED,
                             layer_id=self.scene.active_layer.id)
        self.commands.execute(CreateItemCommand(self.scene, item, "bed"))
        return item

    def assert_equals_a_from_scratch_apply(self) -> None:
        """Incremental == full rebuild, for what the controller sent."""
        sync = self.controller.sync
        fresh_sink = RecordingSink()
        fresh = SceneSync(fresh_sink, sync.registry)
        fresh.apply(snapshot_records(self.scene, self.clock.plan_date),
                    sun=sync.sun, ground=sync.ground)
        assert self.recorder.state() == fresh_sink.state() == sync.expected_state()


@pytest.fixture()
def rig(canvas: CanvasView, qtbot) -> Rig:
    return Rig(canvas, qtbot)


# ── G4: paused while hidden, one catch-up pass ───────────────────────────────


def test_nothing_happens_before_3d_is_ever_shown(rig: Rig) -> None:
    rig.bed()
    assert rig.settle() == []
    assert rig.controller.snapshot_count == 0 and rig.controller.apply_count == 0
    assert rig.controller.dirty is True


def test_the_first_show_is_one_pass_with_sun_and_ground_in_the_same_transaction(rig: Rig) -> None:
    bed = rig.bed()
    rig.controller.set_active(True)
    assert rig.controller.snapshot_count == 0, "the pass waits for the event loop: the pane paints first"
    assert rig.settle() == [BEGIN, ("add", str(bed.item_id)), GROUND, SUN, COMMIT]
    assert (rig.controller.snapshot_count, rig.controller.apply_count) == (1, 1)
    assert rig.recorder.ground == GroundSpec(5000.0, 3000.0)
    assert rig.recorder.sun == NO_LOCATION_SUN
    rig.assert_equals_a_from_scratch_apply()
    report = rig.reports[-1]
    assert report.snapshot and report.records == 1 and report.changes.added == (str(bed.item_id),)


def test_hidden_edits_cause_no_snapshot_and_no_sink_call_and_one_catch_up_pass(rig: Rig) -> None:
    """Gate G4. Every kind of change while 3D is hidden leaves a flag and nothing else."""
    bed = rig.bed()
    rig.controller.set_active(True)
    rig.settle()
    rig.controller.set_active(False)
    before = (rig.controller.snapshot_count, rig.controller.apply_count, rig.controller.light_count)

    tree = CircleItem(1500.0, 900.0, 120.0, object_type=ObjectType.TREE,
                      layer_id=rig.scene.active_layer.id)
    edits = 0
    rig.commands.execute(CreateItemCommand(rig.scene, tree, "tree"))
    rig.commands.execute(MoveItemsCommand([bed], QPointF(137.25, -42.5)))
    rig.commands.execute(recolour_fill(bed, QColor(200, 30, 30)))
    old, new = build_rect_resize(bed, 500.0, 120.0, keep_center=True)
    rig.commands.execute(ResizeItemCommand(bed, old, new, apply_rect_like_geometry))
    rig.commands.undo()
    rig.commands.redo()
    rig.commands.execute(DeleteItemsCommand(rig.scene, [tree]))
    rig.commands.undo()
    edits += 8
    for index in range(20):
        rig.commands.execute(MoveItemsCommand([bed], QPointF(1.0 + index, 0.5)))
        edits += 1
    rig.clock.set_date(date(2026, 8, 1))
    rig.clock.set_time_of_day(time(17, 30))
    bed.metadata[METADATA_KEY] = 75.0
    rig.controller.mark_dirty()
    rig.controller.refresh_sun()
    rig.controller.document_changed()
    assert edits == 28

    assert rig.settle() == []
    assert (rig.controller.snapshot_count, rig.controller.apply_count,
            rig.controller.light_count) == before
    assert rig.controller.dirty is True

    rig.controller.set_active(True)
    calls = rig.settle()
    assert calls[0] == BEGIN and calls[-1] == COMMIT and calls.count(BEGIN) == 1
    assert (rig.controller.snapshot_count, rig.controller.apply_count) == (before[0] + 1, before[1] + 1)
    assert rig.controller.dirty is False
    rig.assert_equals_a_from_scratch_apply()


def test_coming_back_with_nothing_changed_costs_nothing(rig: Rig) -> None:
    rig.bed()
    rig.controller.set_active(True)
    rig.settle()
    counts = (rig.controller.snapshot_count, rig.controller.apply_count)
    for _ in range(5):
        rig.controller.set_active(False)
        rig.controller.set_active(True)
    assert rig.settle() == []
    assert (rig.controller.snapshot_count, rig.controller.apply_count) == counts


def test_hiding_cancels_a_pass_that_was_only_scheduled(rig: Rig) -> None:
    bed = rig.bed()
    rig.controller.set_active(True)
    rig.settle()
    counts = rig.controller.snapshot_count
    rig.commands.execute(MoveItemsCommand([bed], QPointF(10.0, 0.0)))
    rig.controller.set_active(False)   # before the event loop ran
    assert rig.settle() == []
    assert rig.controller.snapshot_count == counts and rig.controller.dirty is True
    rig.controller.flush()             # "run what is pending" is not a way round the pause
    assert rig.recorder.ops() == [] and rig.controller.snapshot_count == counts


# ── the edit session ─────────────────────────────────────────────────────────


def test_an_edit_session_sends_the_minimal_calls_one_pass_per_turn(rig: Rig) -> None:
    rig.controller.set_active(True)
    assert rig.settle() == [BEGIN, GROUND, SUN, COMMIT]  # an empty plan: its ground and light

    bed = rig.bed()
    bed_id = str(bed.item_id)
    assert rig.settle() == [BEGIN, ("add", bed_id), COMMIT]

    rig.commands.execute(MoveItemsCommand([bed], QPointF(137.25, -42.5)))
    assert rig.settle() == [BEGIN, ("update_transform", bed_id), COMMIT]

    rig.commands.execute(recolour_fill(bed, QColor(200, 30, 30)))
    assert rig.settle() == [BEGIN, ("update_material", bed_id), COMMIT]

    old, new = build_rect_resize(bed, 500.0, 120.0, keep_center=True)
    rig.commands.execute(ResizeItemCommand(bed, old, new, apply_rect_like_geometry))
    assert rig.settle() == [BEGIN, ("replace_geometry", bed_id), COMMIT]

    rig.commands.execute(DeleteItemsCommand(rig.scene, [bed]))
    assert rig.settle() == [BEGIN, ("remove", bed_id), COMMIT]
    rig.commands.undo()
    assert rig.settle() == [BEGIN, ("add", bed_id), COMMIT]

    bed.setSelected(True)     # a selection repaints the scene: a pass, and nothing to send
    bed.setSelected(False)
    assert rig.settle() == []
    rig.assert_equals_a_from_scratch_apply()
    assert rig.controller.recovery_count == 0 and rig.controller.last_error is None


def test_many_edits_in_one_turn_of_the_event_loop_are_one_snapshot(rig: Rig) -> None:
    bed = rig.bed()
    rig.controller.set_active(True)
    rig.settle()
    snapshots = rig.controller.snapshot_count
    for index in range(40):
        rig.commands.execute(MoveItemsCommand([bed], QPointF(1.0 + index, 0.0)))
    rig.controller.flush()
    assert rig.controller.snapshot_count == snapshots + 1
    assert rig.recorder.ops() == [BEGIN, ("update_transform", str(bed.item_id)), COMMIT]


def test_a_height_typed_into_the_metadata_reaches_3d_through_the_undo_stack_signal(rig: Rig) -> None:
    """A metadata-only edit repaints nothing, so ``scene.changed`` stays silent: the
    undo stack's ``stack_changed`` is the second trigger (the shadow overlay's rule)."""
    bed = rig.bed()
    rig.controller.set_active(True)
    rig.settle()
    bed.metadata[METADATA_KEY] = 75.0
    rig.commands.stack_changed.emit()
    assert rig.settle() == [BEGIN, ("replace_geometry", str(bed.item_id)), COMMIT]


def test_an_edit_reaches_3d_while_the_canvas_itself_is_hidden(rig: Rig) -> None:
    """3D mode hides the canvas view. ``QGraphicsScene.changed`` does not need a view."""
    bed = rig.bed()
    rig.view.hide()
    rig.controller.set_active(True)
    rig.settle()
    bed.setPos(bed.pos() + QPointF(50.0, 0.0))   # no command, no stack signal: the scene alone
    assert rig.settle() == [BEGIN, ("update_transform", str(bed.item_id)), COMMIT]


def test_a_resized_canvas_moves_the_ground(rig: Rig) -> None:
    rig.controller.set_active(True)
    rig.settle()
    rig.scene.resize_canvas(8000.0, 6000.0)
    assert rig.settle() == [BEGIN, GROUND, COMMIT]
    assert rig.recorder.ground == GroundSpec(8000.0, 6000.0)
    assert rig.controller.plan_bounds() == Bounds.of_plan(8000.0, 6000.0, 0.0)


# ── G9: the sim clock ────────────────────────────────────────────────────────


def test_a_time_change_moves_the_light_and_takes_no_snapshot(canvas: CanvasView, qtbot) -> None:
    rig = Rig(canvas, qtbot, location=dict(BERLIN))
    rig.bed()
    rig.controller.set_active(True)
    rig.settle()
    snapshots, applies = rig.controller.snapshot_count, rig.controller.apply_count

    rig.clock.set_time_of_day(time(17, 30))
    assert rig.settle() == [BEGIN, SUN, COMMIT]
    assert (rig.controller.snapshot_count, rig.controller.apply_count) == (snapshots, applies)
    assert rig.controller.light_count == 1
    expected = solar_position(BERLIN["latitude"], BERLIN["longitude"], rig.clock.utc)
    assert rig.recorder.sun == SunState.from_solar_position(expected)
    assert rig.reports[-1].snapshot is False


def test_time_changes_faster_than_passes_are_one_light_update(canvas: CanvasView, qtbot) -> None:
    rig = Rig(canvas, qtbot, location=dict(BERLIN))
    rig.controller.set_active(True)
    rig.settle()
    for minute in range(30):
        rig.clock.set_time_of_day(time(13, minute))
    assert rig.settle() == [BEGIN, SUN, COMMIT]
    assert rig.controller.light_count == 1


def test_without_a_location_the_documented_default_sun_is_sent_once(rig: Rig) -> None:
    rig.controller.set_active(True)
    rig.settle()
    assert rig.recorder.sun == NO_LOCATION_SUN == SunState(50.0, 180.0)   # FR-SUN-06
    rig.clock.set_time_of_day(time(18, 0))
    assert rig.settle() == [], "the same sun again is not sent"
    rig.location = dict(BERLIN)
    rig.controller.refresh_sun()
    assert rig.settle() == [BEGIN, SUN, COMMIT]


def test_a_date_change_regrows_the_dated_plants_and_nothing_else(canvas: CanvasView, qtbot) -> None:
    """Gate G9. An apple tree measured on its planting day grows with the plan date; a
    tomato planted next May has not been planted yet and a bed never grows."""
    rig = Rig(canvas, qtbot, location=dict(BERLIN))
    bed = rig.bed(2000.0, 1500.0)
    apple = drop(canvas, "TREE:species=apple tree", QPointF(900.0, 700.0))
    apple.metadata["plant_instance"] = {
        "planting_date": "2024-04-01", "current_height_cm": 180.0, "current_spread_cm": 120.0}
    spruce = drop(canvas, "TREE:species=spruce", QPointF(3000.0, 700.0))   # no planting date
    rig.controller.set_active(True)
    rig.settle()
    snapshots = rig.controller.snapshot_count

    rig.clock.set_date(date(2027, 6, 21))
    calls = rig.settle()
    assert calls == [BEGIN, ("replace_geometry", str(apple.item_id)), SUN, COMMIT]
    assert rig.controller.snapshot_count == snapshots + 1
    assert {str(bed.item_id), str(spruce.item_id)} <= set(rig.recorder.item_ids())
    rig.assert_equals_a_from_scratch_apply()


def test_an_animated_date_never_queues_more_than_one_regrow(canvas: CanvasView, qtbot) -> None:
    rig = Rig(canvas, qtbot)
    apple = drop(canvas, "TREE:species=apple tree", QPointF(900.0, 700.0))
    apple.metadata["plant_instance"] = {
        "planting_date": "2024-04-01", "current_height_cm": 180.0, "current_spread_cm": 120.0}
    rig.controller.set_active(True)
    rig.settle()
    snapshots = rig.controller.snapshot_count
    for day in range(1, 29):
        rig.clock.set_date(date(2027, 2, day))
    calls = rig.settle()
    assert rig.controller.snapshot_count == snapshots + 1
    assert calls == [BEGIN, ("replace_geometry", str(apple.item_id)), COMMIT]


def test_a_destroyed_controller_is_never_called_again(canvas: CanvasView, qtbot) -> None:
    """Gate G9, third part — through the real clock and the real scene."""
    rig = Rig(canvas, qtbot, location=dict(BERLIN))
    bed = rig.bed()
    rig.controller.set_active(True)
    rig.settle()
    sip.delete(rig.controller)
    rig.clock.set_date(date(2027, 1, 1))          # would raise from inside the dead slot
    rig.clock.set_time_of_day(time(9, 0))
    bed.setPos(bed.pos() + QPointF(5.0, 0.0))
    rig.qtbot.wait(50)
    assert rig.recorder.ops() == []
    assert rig.clock.date_changed.receiver_count() == 0
    assert rig.clock.instant_changed.receiver_count() == 0


# ── a new document ───────────────────────────────────────────────────────────


def test_a_document_change_is_a_plain_apply_and_announces_the_document_once(
        rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    cleared: list[int] = []
    monkeypatch.setattr(plants, "clear_shape_cache", lambda: cleared.append(1))
    shown: list[int] = []
    rig.controller.document_shown.connect(lambda: shown.append(1))
    old = rig.bed()
    rig.controller.set_active(True)
    rig.settle()
    assert shown == [1], "the first pass shows a document"

    rig.scene.clear()                      # what "new plan" and "open" do
    rig.scene.resize_canvas(2000.0, 1000.0)
    new = rig.bed(100.0, 100.0)
    rig.controller.document_changed()
    calls = rig.settle()
    assert cleared == [1]
    assert ("clear", None) not in calls, "a document change is not a reset"
    assert calls == [BEGIN, ("remove", str(old.item_id)), ("add", str(new.item_id)), GROUND, COMMIT]
    assert shown == [1, 1]
    rig.commands.execute(MoveItemsCommand([new], QPointF(10.0, 0.0)))
    rig.settle()
    assert shown == [1, 1], "an edit never announces a document"
    assert rig.controller.plan_bounds() == Bounds.of_plan(2000.0, 1000.0, 40.0)


def test_a_document_changed_while_hidden_is_announced_when_3d_comes_back(rig: Rig) -> None:
    shown: list[int] = []
    rig.controller.document_shown.connect(lambda: shown.append(1))
    rig.controller.set_active(True)
    rig.settle()
    rig.controller.set_active(False)
    rig.controller.document_changed()
    rig.controller.document_changed()      # opened twice while hidden: still one document
    rig.settle()
    assert shown == [1]
    rig.controller.set_active(True)
    rig.settle()
    assert shown == [1, 1]


# ── failures: contained, logged, recovered ───────────────────────────────────


class FailingSink:
    """A sink that raises on a chosen call — the engine failing in mid-transaction."""

    def __init__(self) -> None:
        self.inner = RecordingSink()
        self.fail_on: str | None = None
        self.failures_left = 0

    def __getattr__(self, name: str) -> Any:
        target = getattr(self.inner, name)
        if not callable(target) or name != self.fail_on:
            return target

        def call(*arguments: Any, **options: Any) -> Any:
            if self.failures_left > 0:
                self.failures_left -= 1
                raise RuntimeError(f"the engine refused {name}()")
            return target(*arguments, **options)

        return call


def test_a_sink_that_fails_is_reset_and_reapplied_and_nothing_reaches_the_event_loop(
        canvas: CanvasView, qtbot, caplog: pytest.LogCaptureFixture) -> None:
    sink = FailingSink()
    rig = Rig(canvas, qtbot, sink=sink)
    bed = rig.bed()
    rig.controller.set_active(True)
    rig.settle()

    sink.fail_on, sink.failures_left = "update_transform", 1
    with caplog.at_level(logging.ERROR):
        rig.commands.execute(MoveItemsCommand([bed], QPointF(50.0, 0.0)))
        calls = rig.settle()
    assert rig.controller.recovery_count == 1
    assert ("clear", None) in calls                       # the recovery goes through the protocol
    assert calls[-4:] == [("add", str(bed.item_id)), GROUND, SUN, COMMIT]
    assert rig.reports[-1].recovered is True
    assert rig.controller.last_error is None and rig.controller.sync.poisoned is False
    assert any("resetting the 3D scene" in record.message for record in caplog.records)
    rig.assert_equals_a_from_scratch_apply()


def test_a_sink_that_keeps_failing_is_given_up_for_this_pass_and_tried_again_later(
        canvas: CanvasView, qtbot) -> None:
    sink = FailingSink()
    rig = Rig(canvas, qtbot, sink=sink)
    errors: list[str] = []
    rig.controller.failed.connect(errors.append)
    bed = rig.bed()
    rig.controller.set_active(True)
    rig.settle()

    sink.fail_on, sink.failures_left = "begin", 2          # the pass and its recovery
    rig.commands.execute(MoveItemsCommand([bed], QPointF(50.0, 0.0)))
    rig.settle()
    assert len(errors) == 1 and "could not be rebuilt" in errors[0]
    assert rig.controller.last_error == errors[0]
    assert rig.controller.dirty is True, "nothing was lost: the next change tries again"

    rig.commands.execute(MoveItemsCommand([bed], QPointF(1.0, 0.0)))   # the engine is back
    rig.settle()
    assert rig.controller.last_error is None
    rig.assert_equals_a_from_scratch_apply()


def test_a_poisoned_sync_is_recovered_by_the_next_pass(rig: Rig) -> None:
    bed = rig.bed()
    rig.controller.set_active(True)
    rig.settle()
    with pytest.raises(SinkProtocolError):                 # somebody broke the protocol …
        rig.controller.sync._transact(lambda: rig.recorder.remove("nobody"))
    assert rig.controller.sync.poisoned is True
    rig.recorder.clear_calls()
    rig.commands.execute(MoveItemsCommand([bed], QPointF(5.0, 0.0)))
    calls = rig.settle()
    assert rig.controller.recovery_count == 1 and calls[0] == ("clear", None)
    assert rig.controller.sync.poisoned is False
    rig.assert_equals_a_from_scratch_apply()


def test_reset_after_the_engine_scene_was_rebuilt_sends_everything_again(rig: Rig) -> None:
    bed = rig.bed()
    rig.controller.set_active(True)
    rig.settle()
    rig.controller.reset()
    calls = rig.settle()
    assert calls == [("clear", None), BEGIN, ("add", str(bed.item_id)), GROUND, SUN, COMMIT]


# ── what 3D could not build ──────────────────────────────────────────────────


def test_an_item_the_record_layer_refuses_is_reported_and_fixing_it_clears_the_report(
        rig: Rig) -> None:
    changed: list[int] = []
    rig.controller.problems_changed.connect(lambda: changed.append(rig.controller.problem_count))
    good, bad = rig.bed(), rig.bed(1500.0, 1500.0)
    bad.metadata[METADATA_KEY] = float("inf")              # a height nobody can build
    rig.controller.set_active(True)
    rig.settle()
    assert [(s.item_id, s.kind, s.reason) for s in rig.controller.skipped] == [
        (str(bad.item_id), "RAISED_BED", SkipReason.OUT_OF_RANGE)]
    assert rig.controller.problem_count == 1 and changed == [1]
    assert rig.recorder.item_ids() == (str(good.item_id),), "the rest of the plan is shown"

    rig.commands.execute(MoveItemsCommand([good], QPointF(5.0, 0.0)))
    rig.settle()
    assert changed == [1], "the same report again is not news"

    bad.metadata[METADATA_KEY] = 80.0                      # the user fixes it
    rig.commands.stack_changed.emit()
    rig.settle()
    assert rig.controller.skipped == () and changed == [1, 0]
    assert set(rig.recorder.item_ids()) == {str(good.item_id), str(bad.item_id)}


def test_a_builder_that_fails_is_reported_too(canvas: CanvasView, qtbot) -> None:
    registry = production_registry()

    def broken(record: Record) -> tuple:
        raise ValueError(f"cannot build {record.kind}")

    registry.register("RAISED_BED", broken, replace=True)
    rig = Rig(canvas, qtbot, registry=registry)
    bed = rig.bed()
    rig.controller.set_active(True)
    rig.settle()
    failure = rig.controller.failures[str(bed.item_id)]
    assert (failure.kind, failure.error_type) == ("RAISED_BED", "ValueError")
    assert rig.controller.problem_count == 1
    assert rig.recorder.item(str(bed.item_id)).parts == ()   # shown without a solid


# ── the ground seam (L1.5) ───────────────────────────────────────────────────


def test_the_ground_stage_sees_both_snapshots_and_its_ground_rides_the_transaction(
        rig: Rig) -> None:
    seen: list[tuple[list[str], list[str], float, float]] = []

    def stage(previous: Any, records: Any, width: float, depth: float) -> GroundSpec:
        seen.append((list(previous), list(records), width, depth))
        return GroundSpec(width / 2.0, depth / 2.0)        # anything a baker decides

    bed = rig.bed()
    rig.controller.set_active(True)
    rig.settle()
    rig.controller.set_ground_stage(stage)
    assert rig.settle() == [BEGIN, GROUND, COMMIT]
    assert seen == [([str(bed.item_id)], [str(bed.item_id)], 5000.0, 3000.0)]
    assert rig.recorder.ground == GroundSpec(2500.0, 1500.0)

    second = rig.bed(2000.0, 2000.0)
    assert rig.settle() == [BEGIN, ("add", str(second.item_id)), COMMIT]
    # the pass that added it was handed "what the engine holds" and "what it will hold"
    both = [str(bed.item_id), str(second.item_id)]
    assert ([str(bed.item_id)], both, 5000.0, 3000.0) in seen
    assert seen[-1][1] == both

    rig.controller.set_ground_stage(None)
    assert rig.settle() == [BEGIN, GROUND, COMMIT]
    assert rig.recorder.ground == GroundSpec(5000.0, 3000.0)
    assert plain_ground({}, {}, 12.0, 34.0) == GroundSpec(12.0, 34.0)


def test_a_ground_stage_that_raises_does_not_blank_the_garden(
        rig: Rig, caplog: pytest.LogCaptureFixture) -> None:
    def broken(*_arguments: Any) -> GroundSpec:
        raise RuntimeError("the baker fell over")

    bed = rig.bed()
    rig.controller.set_ground_stage(broken)
    rig.controller.set_active(True)
    with caplog.at_level(logging.ERROR):
        calls = rig.settle()
    assert calls == [BEGIN, ("add", str(bed.item_id)), GROUND, SUN, COMMIT]
    assert rig.recorder.ground == GroundSpec(5000.0, 3000.0)
    assert any("ground stage failed" in record.message for record in caplog.records)
    assert rig.controller.recovery_count == 0


def test_after_a_reset_the_ground_stage_is_told_that_nothing_is_in_the_engine(
        canvas: CanvasView, qtbot) -> None:
    sink = FailingSink()
    rig = Rig(canvas, qtbot, sink=sink)
    previous_seen: list[list[str]] = []

    def stage(previous: Any, _records: Any, width: float, depth: float) -> GroundSpec:
        previous_seen.append(list(previous))
        return GroundSpec(width, depth)

    bed = rig.bed()
    rig.controller.set_ground_stage(stage)
    rig.controller.set_active(True)
    rig.settle()
    sink.fail_on, sink.failures_left = "update_transform", 1
    rig.commands.execute(MoveItemsCommand([bed], QPointF(50.0, 0.0)))
    rig.settle()
    assert previous_seen[-2:] == [[str(bed.item_id)], []]


def test_shutdown_stops_everything(rig: Rig) -> None:
    bed = rig.bed()
    rig.controller.set_active(True)
    rig.settle()
    rig.controller.shutdown()
    rig.commands.execute(MoveItemsCommand([bed], QPointF(50.0, 0.0)))
    rig.clock.set_date(date(2027, 3, 3))
    assert rig.settle() == []
    assert rig.controller.active is False
