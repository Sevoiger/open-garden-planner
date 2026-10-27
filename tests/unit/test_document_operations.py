"""Observable preview, transaction, concurrency and retry contracts."""

from copy import deepcopy

import pytest

from open_garden_planner.core.document_session import DocumentSession
from open_garden_planner.core.plan_io import read_plan, write_plan


def project(path):
    raw = {"version": "1.4", "canvas": {"width": 500, "height": 500},
           "objects": [], "future": {"kept": [1, 2]}}
    write_plan(path, raw)
    return raw


def addition(title="Example"):
    return {"operation": "add_manual_task", "fields": {"title": title}}


def test_preview_batch_one_undo_and_durable_replay(tmp_path):
    path = tmp_path / "project.ogp"
    original = project(path)
    operations = [addition(f"Task {i}") for i in range(30)]
    with DocumentSession(path) as host:
        preview = host.dispatch("prepare", {"operations": operations})
        assert read_plan(path) == original
        assert host.history.undo_depth == 0
        applied = host.dispatch("apply", {"preparation_id": preview["preparation_id"], "request_key": "batch-30"})
        assert applied["persisted"] and not applied["replayed"]
        assert len(read_plan(path)["manual_tasks"]) == 30
        assert host.history.undo_depth == 1
        host.dispatch("undo", {})
        assert not read_plan(path).get("manual_tasks")
        assert host.dispatch("apply", {"preparation_id": preview["preparation_id"], "request_key": "batch-30"})["replayed"]
        assert host.history.undo_depth == 0
        host.dispatch("redo", {})
        assert len(read_plan(path)["manual_tasks"]) == 30
    with DocumentSession(path) as host:
        preview = host.dispatch("prepare", {"operations": operations})
        assert host.dispatch("apply", {"preparation_id": preview["preparation_id"], "request_key": "batch-30"})["replayed"]
        assert host.history.undo_depth == 0
        assert read_plan(path)["future"] == original["future"]


def test_unchanged_legacy_manual_task_is_not_normalized_or_undoable(tmp_path):
    path = tmp_path / "legacy-task.ogp"
    original = project(path)
    original["manual_tasks"] = {
        "task-legacy": {"id": "task-legacy", "title": "Arroser"}
    }
    write_plan(path, original)
    operation = {
        "operation": "edit_manual_task",
        "target_id": "task-legacy",
        "fields": {"title": "Arroser"},
    }

    with DocumentSession(path) as host:
        preview = host.dispatch("prepare", {"operations": [operation]})
        assert preview["results"] == [{
            "row": 1,
            "operation": "edit_manual_task",
            "target_id": "task-legacy",
            "status": "unchanged",
        }]
        assert preview["changes"] == []
        applied = host.dispatch("apply", {
            "preparation_id": preview["preparation_id"],
            "request_key": "legacy-task-noop",
        })

        assert applied["status"] == "unchanged"
        assert host.history.undo_depth == 0
        assert host.snapshot()["manual_tasks"] == original["manual_tasks"]
        assert read_plan(path)["manual_tasks"] == original["manual_tasks"]


def test_invalid_row_and_failed_write_leave_document_and_history_unchanged(tmp_path, monkeypatch):
    path = tmp_path / "project.ogp"
    original = project(path)
    with DocumentSession(path) as host:
        with pytest.raises(ValueError):
            host.dispatch("prepare", {"operations": [addition(), addition("")]})
        assert read_plan(path) == original
        preview = host.dispatch("prepare", {"operations": [addition()]})

        def fail(*args):
            raise OSError("injected write failure")

        with monkeypatch.context() as scoped:
            scoped.setattr("open_garden_planner.core.document_session.write_plan", fail)
            with pytest.raises(OSError):
                host.dispatch("apply", {"preparation_id": preview["preparation_id"], "request_key": "failed"})
        assert host.history.undo_depth == 0
        assert host.snapshot() == original == read_plan(path)
        # A completed backup from the failed attempt must not block a safe retry.
        assert host.dispatch("apply", {"preparation_id": preview["preparation_id"], "request_key": "failed"})["persisted"]
        assert host.history.undo_depth == 1


def test_conflicts_expiration_and_key_mismatch(tmp_path):
    path = tmp_path / "project.ogp"
    project(path)
    with DocumentSession(path) as host:
        first = host.dispatch("prepare", {"operations": [addition("one")]})
        stale = host.dispatch("prepare", {"operations": [addition("two")]})
        host.dispatch("apply", {"preparation_id": first["preparation_id"], "request_key": "one"})
        with pytest.raises(ValueError, match="changed after preparation"):
            host.dispatch("apply", {"preparation_id": stale["preparation_id"], "request_key": "two"})
        with pytest.raises(ValueError, match="Idempotence conflict"):
            host.dispatch("apply", {"preparation_id": stale["preparation_id"], "request_key": "one"})
        host.dispatch("undo", {})
        with pytest.raises(ValueError, match="changed after preparation"):
            host.dispatch("apply", {"preparation_id": stale["preparation_id"], "request_key": "two"})
        now = host.dispatch("prepare", {"operations": [addition()]})
        host.operations._clock = lambda: float("inf")
        with pytest.raises(ValueError, match="expired"):
            host.dispatch("apply", {"preparation_id": now["preparation_id"], "request_key": "expired"})


def test_explicit_invalidation_discards_session_preparations(tmp_path):
    path = tmp_path / "invalidate.ogp"
    project(path)
    with DocumentSession(path) as host:
        preview = host.dispatch("prepare", {"operations": [addition()]})
        host.operations.invalidate()
        with pytest.raises(ValueError, match="expired or belongs to another session"):
            host.dispatch("apply", {"preparation_id": preview["preparation_id"], "request_key": "invalidated"})
        assert host.history.undo_depth == 0
        assert read_plan(path).get("manual_tasks") is None


def test_ownership_disk_conflict_and_closed_session(tmp_path):
    path = tmp_path / "project.ogp"
    project(path)
    with DocumentSession(path) as host:
        with pytest.raises(ValueError, match="already open"):
            DocumentSession(path)
        preview = host.dispatch("prepare", {"operations": [addition()]})
        external = deepcopy(read_plan(path))
        external["future"] = "external update"
        write_plan(path, external)
        with pytest.raises(ValueError, match="changed on disk"):
            host.dispatch("apply", {"preparation_id": preview["preparation_id"], "request_key": "external"})
        assert read_plan(path) == external
        assert host.history.undo_depth == 0
    with pytest.raises(ValueError, match="closed"):
        host.dispatch("snapshot", {})


def test_garden_soil_batch_and_advice_are_distinct_from_completed_work(tmp_path):
    from uuid import uuid4

    from open_garden_planner.services.document_analysis import assess_document_soil

    path = tmp_path / "garden.ogp"
    plant, bed = str(uuid4()), str(uuid4())
    raw = project(path)
    raw["objects"] = [
        {"type": "circle", "item_id": plant, "object_type": "PERENNIAL", "name": "Synthetic plant",
         "center_x": 100, "center_y": 100, "radius": 20,
         "metadata": {"plant_instance": {"vendor": "retained"}}},
        {"type": "rectangle", "item_id": bed, "object_type": "GARDEN_BED", "name": "Synthetic bed",
         "x": 10, "y": 10, "width": 400, "height": 400},
    ]
    write_plan(path, raw)
    operations = [
        {"operation": "set_planting_date", "target_id": plant, "fields": {"planting_date": None}},
        {"operation": "add_garden_intervention", "fields": {
            "type": "amendment", "status": "planned", "description": "Synthetic plan",
            "targets": [{"id": bed, "name": "Synthetic bed"}], "period": "2026"}},
        {"operation": "record_soil_test", "target_id": bed,
         "fields": {"ph": 5.0, "n_level": 1, "test_date": "2026-01-01"}},
    ]
    with DocumentSession(path) as host:
        preview = host.dispatch("prepare", {"operations": operations})
        assert read_plan(path) == raw
        result = host.dispatch("apply", {"preparation_id": preview["preparation_id"], "request_key": "garden-batch"})
        assert result["effect"] == "present"
        before_advice = host.snapshot()
        advice = assess_document_soil(before_advice, bed, area_m2=16)
        assert advice["kind"] == "recommendation" and not advice["recorded_intervention"]
        assert host.snapshot() == before_advice
        assert len(before_advice["garden_memory"]["events"]) == 1
        assert next(iter(before_advice["garden_memory"]["events"].values()))["status"] == "planned"
        record = before_advice["objects"][0]
        assert record["metadata"]["plant_instance"] == {"vendor": "retained"}
        assert record["metadata"]["garden_tracking"]["period"]["precision"] == "unknown"
        host.dispatch("undo", {})
        assert not host.snapshot().get("soil_tests")
        assert host.dispatch("result", {"request_key": "garden-batch"})["effect"] == "reverted"
        host.dispatch("redo", {})
        assert len(read_plan(path)["garden_memory"]["events"]) == 1


def test_missing_succession_entries_cannot_delete_existing_plan(tmp_path):
    from uuid import uuid4

    path = tmp_path / "succession.ogp"
    bed = str(uuid4())
    raw = project(path)
    raw["objects"] = [{"type": "rectangle", "item_id": bed, "object_type": "GARDEN_BED"}]
    raw["succession_plans"] = {bed: {"bed_id": bed, "year": 2026, "entries": []}}
    write_plan(path, raw)
    with DocumentSession(path) as host:
        for fields in ({"year": 2026}, {"year": 2026, "entries": None}):
            with pytest.raises(ValueError, match="Provide entries"):
                host.dispatch("prepare", {"operations": [{
                    "operation": "set_succession_plan", "target_id": bed, "fields": fields}]})
            assert host.snapshot() == raw == read_plan(path)
            assert host.history.undo_depth == 0
        preview = host.dispatch("prepare", {"operations": [{
            "operation": "set_succession_plan", "target_id": bed,
            "fields": {"year": 2026, "entries": []}}]})
        host.dispatch("apply", {"preparation_id": preview["preparation_id"], "request_key": "explicit-delete"})
        assert not host.snapshot().get("succession_plans")
        host.dispatch("undo", {})
        assert host.snapshot()["succession_plans"] == raw["succession_plans"]


def test_compact_object_patch_checks_identity_before_undo():
    from open_garden_planner.services.document_operations import apply_patches, changes_between

    before = {"objects": [{"item_id": "one", "name": "old"}, {"item_id": "two", "name": "new"},
                          {"item_id": "background", "image": "x" * 3_000_000}]}
    after = deepcopy(before)
    after["objects"][0]["name"] = "new"
    patches = changes_between(before, after)
    assert len(str(patches)) < 1000  # The unchanged background is not retained.
    reordered = deepcopy(after)
    reordered["objects"][:2] = reversed(reordered["objects"][:2])
    with pytest.raises(ValueError, match="changed since preparation"):
        apply_patches(reordered, [p.inverse() for p in reversed(patches)])
    assert reordered["objects"][0]["item_id"] == "two"


def test_revision_cache_invalidates_writes_undo_redo_and_rejects_external_change(tmp_path, monkeypatch):
    import open_garden_planner.core.document_session as module

    path = tmp_path / "cached.ogp"
    project(path)
    calls = []
    original = module.document_fingerprint
    monkeypatch.setattr(module, "document_fingerprint", lambda data: (calls.append(1), original(data))[1])
    with DocumentSession(path) as host:
        first = host.dispatch("read", {"domains": ["tasks"]})["revision"]
        assert host.dispatch("read", {"domains": ["objects"]})["revision"] == first
        assert len(calls) == 1
        preview = host.dispatch("prepare", {"operations": [addition()], "expected_revision": first})
        host.dispatch("apply", {"preparation_id": preview["preparation_id"], "request_key": "cache"})
        after = host.dispatch("read", {})["revision"]
        assert after != first and len(calls) == 2
        host.dispatch("undo", {})
        undone = host.dispatch("read", {})["revision"]
        assert undone != after and len(calls) == 3
        host.dispatch("redo", {})
        assert host.dispatch("read", {})["revision"] != undone and len(calls) == 4
        changed = read_plan(path)
        changed["future"] = {"external": True}
        write_plan(path, changed)
        with pytest.raises(ValueError, match="changed on disk"):
            host.dispatch("read", {})
        with pytest.raises(ValueError, match="changed on disk"):
            host.dispatch("prepare", {"operations": [addition()]})


def test_document_fingerprint_preserves_old_bytes_without_copying_receipts():
    from open_garden_planner.services.document_operations import document_fingerprint, fingerprint

    class NoCopy(dict):
        def __deepcopy__(self, memo):
            raise AssertionError("Excluded receipts must not be copied")

    raw = {"metadata": {"modified": "now", "created": None}, "objects": [{"image_data": "AAA"}],
           "future": {"false": False, "zero": 0}, "operation_receipts": NoCopy({"old": {}}),
           "agent_meta": {"dirty": True}}
    expected = {"metadata": {"created": None}, "objects": raw["objects"], "future": raw["future"]}
    assert document_fingerprint(raw) == fingerprint(expected)
    assert raw["metadata"]["modified"] == "now"
