"""Atomic, replayable operations for the personal plant library."""

import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest

from open_garden_planner.core.file_lock import FileLock
from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services import library_operations
from open_garden_planner.services.library_operations import (
    LibraryConflictError,
    LibraryFileError,
    LibraryOperations,
    LibraryPersistenceError,
    LibraryValidationError,
    PreparationExpiredError,
)
from open_garden_planner.services.plant_library import PlantLibrary


def _record(
    source_id: str,
    common_name: str,
    *,
    data_source: str = "custom",
) -> dict:
    return PlantSpeciesData(
        scientific_name=f"Botanica {source_id}",
        common_name=common_name,
        family="Rosaceae",
        source_id=source_id,
        data_source=data_source,
    ).to_dict()


def _write_document(path: Path, plants: dict, **extra) -> bytes:
    document = {"version": "1.0", "plants": deepcopy(plants), **deepcopy(extra)}
    raw = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def test_library_operations_import_and_prepare_without_qt_or_personal_path():
    project_root = Path(__file__).resolve().parents[2]
    script = r'''
import importlib.abc
import tempfile
import sys
from pathlib import Path

class BlockQt(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".")
               for name in ("PyQt6", "PySide", "PySide6")):
            raise AssertionError(f"Qt import attempted: {fullname}")
        return None

sys.meta_path.insert(0, BlockQt())
from open_garden_planner.services.library_operations import LibraryOperations

with tempfile.TemporaryDirectory() as directory:
    operations = LibraryOperations(Path(directory) / "custom_plants.json")
    preview = operations.prepare([{
        "source_id": "isolated-id",
        "scientific_name": "Rosa gallica",
        "common_name": "Rose",
    }])
    assert preview["can_apply"] is True
'''
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(project_root / "src"), env.get("PYTHONPATH")) if part
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_prepare_is_read_only_and_30_row_commit_roundtrips_after_restart(tmp_path):
    path = tmp_path / "custom_plants.json"
    operations = LibraryOperations(path)
    rows = [_record(f"source:{index}", f"Plante {index}") for index in range(30)]

    preview = operations.prepare(rows)

    assert preview["can_apply"] is True
    assert [row["status"] for row in preview["entries"]] == ["create"] * 30
    assert list(tmp_path.iterdir()) == []
    applied = operations.apply(preview["token"], " batch 30 ")

    assert applied["created"] == 30
    assert applied["updated"] == applied["unchanged"] == 0
    assert applied["persisted"] is True
    assert len(operations.snapshot()) == 30
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["_ogp_library_operations"]["requests"]["batch 30"]["result"] == applied
    assert document["_ogp_library_operations"]["transactions"][
        applied["transaction_id"]
    ]["version"] == 1

    restarted = LibraryOperations(path)
    assert restarted.result("batch 30")["transaction_state"] == "applied"
    replay_preview = restarted.prepare(rows)
    replay = restarted.apply(replay_preview["token"], "batch 30")
    assert replay["replayed"] is True
    assert replay["transaction_id"] == applied["transaction_id"]
    assert len(restarted.snapshot()) == 30


def test_prepare_marks_invalid_and_duplicate_rows_conflicts_without_partial_write(tmp_path):
    path = tmp_path / "custom_plants.json"
    existing_bytes = _write_document(path, {"old": _record("old", "Ancienne")})
    operations = LibraryOperations(path)
    rows = [
        _record("new", "Nouvelle"),
        {"scientific_name": "Sans ID", "common_name": "Refusée"},
        _record("duplicate", "A"),
        _record("duplicate", "B"),
    ]

    preview = operations.prepare(rows)

    assert preview["can_apply"] is False
    assert [row["status"] for row in preview["entries"]] == [
        "create", "conflict", "conflict", "conflict",
    ]
    with pytest.raises(LibraryValidationError):
        operations.apply(preview["token"], "invalid-batch")
    assert path.read_bytes() == existing_bytes
    assert not (tmp_path / "custom_plants.json.writer.lock").exists()


def test_preview_contains_detached_before_after_values_including_unknown_fields(tmp_path):
    path = tmp_path / "custom_plants.json"
    current = _record("rose", "Rosier")
    current["future_trait"] = {"grade": [1, 2]}
    _write_document(path, {"rose": current})
    incoming = deepcopy(current)
    incoming["common_name"] = "Rosier modifié"
    operations = LibraryOperations(path)

    preview = operations.prepare([incoming])

    row = preview["entries"][0]
    assert row["status"] == "update"
    assert row["before"]["common_name"] == "Rosier"
    assert row["before"]["future_trait"] == {"grade": [1, 2]}
    assert row["after"]["common_name"] == "Rosier modifié"
    assert row["after"]["future_trait"] == {"grade": [1, 2]}
    row["after"]["common_name"] = "Aperçu modifié par le client"

    operations.apply(preview["token"], "detached-preview")
    assert operations.snapshot()["rose"]["common_name"] == "Rosier modifié"


def test_canonical_update_omitting_legacy_extensions_preserves_stored_values(tmp_path):
    path = tmp_path / "custom_plants.json"
    legacy = _record("rose", "Rosier")
    legacy["future_trait"] = {"grade": [1, 2]}
    legacy["raw_data"] = {"provider_extension": {"source": "legacy"}}
    _write_document(path, {"rose": legacy})
    operations = LibraryOperations(path)

    canonical_update = _record("rose", "Rosier modifié")
    assert "future_trait" not in canonical_update
    preview = operations.prepare([canonical_update])

    assert preview["can_apply"] is True
    assert preview["entries"][0]["status"] == "update"
    assert preview["entries"][0]["after"]["future_trait"] == legacy["future_trait"]
    assert preview["entries"][0]["after"]["raw_data"] == legacy["raw_data"]
    operations.apply(preview["token"], "canonical-update")
    stored = operations.snapshot()["rose"]
    assert stored["common_name"] == "Rosier modifié"
    assert stored["future_trait"] == legacy["future_trait"]
    assert stored["raw_data"] == legacy["raw_data"]


def test_identity_is_exact_source_id_and_never_a_botanical_name_match(tmp_path):
    path = tmp_path / "custom_plants.json"
    original = _record("first-id", "Tomate", data_source="custom")
    _write_document(path, {"first-id": original})
    operations = LibraryOperations(path)
    incoming = _record("second-id", "Tomate", data_source="custom")

    preview = operations.prepare([incoming])

    assert {
        key: preview["entries"][0][key] for key in ("index", "id", "status")
    } == {"index": 0, "id": "second-id", "status": "create"}
    operations.apply(preview["token"], "second-source")
    snapshot = operations.snapshot()
    assert snapshot["first-id"] == original
    assert snapshot["second-id"]["common_name"] == "Tomate"


def test_source_collision_and_non_json_record_data_are_reported_as_conflicts(tmp_path):
    path = tmp_path / "custom_plants.json"
    _write_document(path, {"same": _record("same", "Tomate", data_source="custom")})
    operations = LibraryOperations(path)
    preview = operations.prepare([
        _record("same", "Tomate API", data_source="trefle"),
        {**_record("bad", "Bad"), "cycle": "not-a-valid-cycle"},
    ])
    assert [row["status"] for row in preview["entries"]] == ["conflict", "conflict"]
    assert preview["can_apply"] is False


@pytest.mark.parametrize(
    ("field_values", "expected_field"),
    [
        ({"ph_min": 10, "ph_max": 2}, "ph_min"),
        ({"min_height_cm": 200, "max_height_cm": 100}, "min_height_cm"),
        ({"min_spread_cm": 80, "max_spread_cm": 20}, "min_spread_cm"),
        ({"min_height_cm": -1}, "min_height_cm"),
        ({"ph_max": 14.1}, "ph_max"),
        ({"days_to_germination_min": 12, "days_to_germination_max": 5},
         "days_to_germination_min"),
        ({"prick_out_after_days": -1}, "prick_out_after_days"),
        ({"min_germination_temp_c": True}, "min_germination_temp_c"),
        ({"min_germination_temp_c": -274}, "min_germination_temp_c"),
        ({"max_spread_cm": "20"}, "max_spread_cm"),
        ({"days_to_maturity_min": True}, "days_to_maturity_min"),
        ({"edible": "false"}, "edible"),
        ({"edible_parts": "fruit"}, "edible_parts"),
    ],
)
def test_prepare_rejects_incoherent_or_mistyped_botanical_values(
    tmp_path, field_values, expected_field
):
    operations = LibraryOperations(tmp_path / "custom_plants.json")
    row = {**_record("invalid", "Invalidée"), **field_values}

    preview = operations.prepare([row])

    assert preview["can_apply"] is False
    assert preview["entries"][0]["status"] == "conflict"
    assert expected_field in preview["entries"][0]["error"]
    assert not (tmp_path / "custom_plants.json").exists()


def test_prepare_keeps_calendar_offsets_that_cross_the_frost_anchor(tmp_path):
    operations = LibraryOperations(tmp_path / "custom_plants.json")
    row = {
        **_record("wrap", "Calendrier"),
        "direct_sow_start": 10,
        "direct_sow_end": 2,
    }

    preview = operations.prepare([row])

    assert preview["can_apply"] is True
    assert preview["entries"][0]["after"]["direct_sow_start"] == 10
    assert preview["entries"][0]["after"]["direct_sow_end"] == 2


def test_prepare_rejects_new_top_level_fields_but_allows_raw_data_extensions(tmp_path):
    operations = LibraryOperations(tmp_path / "custom_plants.json")
    invented = {**_record("invented", "Inventée"), "leaf_shape_guess": "heart"}

    preview = operations.prepare([invented])

    assert preview["can_apply"] is False
    assert preview["entries"][0]["status"] == "conflict"
    assert "leaf_shape_guess" in preview["entries"][0]["error"]

    extension = {
        **_record("extension", "Étendue"),
        "raw_data": {"provider_extension": {"leaf_shape": "heart"}},
    }
    accepted = operations.prepare([extension])
    assert accepted["can_apply"] is True
    assert accepted["entries"][0]["after"]["raw_data"] == extension["raw_data"]


def test_new_record_keeps_absent_edible_and_flowering_unknown(tmp_path):
    operations = LibraryOperations(tmp_path / "custom_plants.json")
    row = _record("sparse", "Inconnue")

    preview = operations.prepare([row])

    assert preview["can_apply"] is True
    after = preview["entries"][0]["after"]
    assert after["edible"] is None
    assert after["flowering"] is None
    operations.apply(preview["token"], "sparse-create")
    stored = operations.snapshot()["sparse"]
    assert stored["edible"] is None
    assert stored["flowering"] is None


def test_batch_size_is_bounded(tmp_path):
    operations = LibraryOperations(tmp_path / "custom_plants.json")
    with pytest.raises(LibraryValidationError, match="limited to 100"):
        operations.prepare([_record(str(i), str(i)) for i in range(101)])


def test_request_payload_size_is_bounded(tmp_path):
    operations = LibraryOperations(tmp_path / "custom_plants.json")
    oversized = _record("large", "Grande")
    oversized["raw_data"] = {"payload": "x" * 1_000_100}
    with pytest.raises(LibraryValidationError, match="JSON bytes"):
        operations.prepare([oversized])


def test_preview_token_expires_after_announced_ttl_without_writing(tmp_path, monkeypatch):
    now = {"value": 100.0}
    monkeypatch.setattr(library_operations.time, "monotonic", lambda: now["value"])
    path = tmp_path / "custom_plants.json"
    operations = LibraryOperations(path)
    preview = operations.prepare([_record("expires", "Éphémère")])
    assert preview["expires_in_seconds"] == 300
    now["value"] += 300

    with pytest.raises(PreparationExpiredError, match="expired"):
        operations.apply(preview["token"], "expired-request")

    assert list(tmp_path.iterdir()) == []


def test_operation_history_limits_fail_closed_without_purging(tmp_path):
    path = tmp_path / "custom_plants.json"
    document = {
        "version": "1.0",
        "plants": {},
        "_ogp_library_operations": {
            "version": 1,
            "requests": {f"old-{i}": {} for i in range(10_001)},
            "transactions": {},
            "record_versions": {},
        },
    }
    raw = json.dumps(document).encode()
    path.write_bytes(raw)

    with pytest.raises(LibraryFileError, match="row limit"):
        LibraryOperations(path).snapshot()

    assert path.read_bytes() == raw


def test_history_capacity_refuses_new_receipt_without_purging_old_ones(tmp_path, monkeypatch):
    path = tmp_path / "custom_plants.json"
    monkeypatch.setattr(library_operations, "_MAX_RECEIPTS", 1)
    operations = LibraryOperations(path)
    first = operations.prepare([_record("one", "Une")])
    operations.apply(first["token"], "first-key")
    before = path.read_bytes()
    second = operations.prepare([_record("two", "Deux")])

    with pytest.raises(LibraryValidationError, match="capacity reached"):
        operations.apply(second["token"], "second-key")

    assert path.read_bytes() == before
    assert operations.result("first-key") is not None
    assert operations.result("second-key") is None


def test_same_request_key_with_different_content_conflicts_before_cas(tmp_path):
    path = tmp_path / "custom_plants.json"
    operations = LibraryOperations(path)
    first = operations.prepare([_record("one", "Une")])
    operations.apply(first["token"], "same-key")
    second = operations.prepare([_record("two", "Deux")])

    with pytest.raises(LibraryConflictError, match="different library content"):
        operations.apply(second["token"], "same-key")

    assert set(operations.snapshot()) == {"one"}


def test_concurrent_same_key_requests_commit_once(tmp_path):
    path = tmp_path / "custom_plants.json"
    row = _record("concurrent", "Concurrente")
    barrier = threading.Barrier(2)

    def prepare_and_apply():
        operations = LibraryOperations(path)
        preview = operations.prepare([row])
        barrier.wait(timeout=5)
        return operations.apply(preview["token"], "shared-key")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: prepare_and_apply(), range(2)))

    assert sorted(result["replayed"] for result in results) == [False, True]
    assert results[0]["transaction_id"] == results[1]["transaction_id"]
    assert len(LibraryOperations(path).snapshot()) == 1


def test_undo_is_versioned_and_replay_after_undo_does_not_recreate(tmp_path):
    path = tmp_path / "custom_plants.json"
    operations = LibraryOperations(path)
    row = _record("to-remove", "À supprimer")
    prepared = operations.prepare([row])
    applied = operations.apply(prepared["token"], "create-once")
    transaction_id = applied["transaction_id"]
    transaction = json.loads(path.read_text(encoding="utf-8"))[
        "_ogp_library_operations"]["transactions"][transaction_id]
    assert transaction["version"] == 1

    undo_result = operations.undo(transaction_id)
    assert undo_result["status"] == "undone"
    assert "to-remove" not in operations.snapshot()
    assert operations.result("create-once")["transaction_state"] == "undone"

    retry = operations.prepare([row])
    replay = operations.apply(retry["token"], "create-once")
    assert replay["replayed"] is True
    assert replay["transaction_id"] == transaction_id
    assert "to-remove" not in operations.snapshot()


def test_replay_after_legacy_suppression_does_not_restore_deleted_record(tmp_path):
    path = tmp_path / "custom_plants.json"
    operations = LibraryOperations(path)
    row = _record("legacy-delete", "À effacer")
    prepared = operations.prepare([row])
    first = operations.apply(prepared["token"], "old-request")

    legacy = PlantLibrary(path)
    assert legacy.remove_plant("legacy-delete") is True
    before_retry = path.read_bytes()
    restarted = LibraryOperations(path)
    replay_prepare = restarted.prepare([row])
    replay = restarted.apply(replay_prepare["token"], "old-request")

    assert replay["replayed"] is True
    assert replay["transaction_id"] == first["transaction_id"]
    assert replay["transaction_state"] == "superseded"
    assert path.read_bytes() == before_retry
    assert "legacy-delete" not in LibraryOperations(path).snapshot()


def test_undo_refuses_if_a_changed_record_no_longer_matches(tmp_path):
    path = tmp_path / "custom_plants.json"
    operations = LibraryOperations(path)
    prepared = operations.prepare([_record("changed", "Avant")])
    applied = operations.apply(prepared["token"], "create")
    library = PlantLibrary(path)
    edited = library.get_plant("changed")
    assert edited is not None
    edited.common_name = "Édition concurrente"
    library.update_plant("changed", edited)
    restored = library.get_plant("changed")
    assert restored is not None
    restored.common_name = "Avant"
    library.update_plant("changed", restored)
    before_undo = path.read_bytes()

    with pytest.raises(LibraryConflictError, match="changed after this transaction"):
        operations.undo(applied["transaction_id"])

    assert path.read_bytes() == before_undo
    assert LibraryOperations(path).snapshot()["changed"]["common_name"] == "Avant"


def test_compare_and_swap_refuses_external_change_after_preview(tmp_path):
    path = tmp_path / "custom_plants.json"
    initial = _write_document(path, {"old": _record("old", "Ancienne")})
    operations = LibraryOperations(path)
    prepared = operations.prepare([_record("new", "Nouvelle")])
    concurrent = json.loads(initial)
    concurrent["future_revision"] = {"writer": "external"}
    path.write_text(json.dumps(concurrent), encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(LibraryConflictError, match="changed after preparation"):
        operations.apply(prepared["token"], "stale-preview")

    assert path.read_bytes() == before
    assert "new" not in LibraryOperations(path).snapshot()


def test_atomic_write_failure_keeps_main_file_and_receipt_absent(tmp_path, monkeypatch):
    path = tmp_path / "custom_plants.json"
    original = _write_document(path, {"old": _record("old", "Ancienne")})
    operations = LibraryOperations(path)
    prepared = operations.prepare([_record("new", "Nouvelle")])
    real_replace = library_operations.os.replace

    def fail_main_replace(source, destination):
        if Path(destination) == path:
            raise OSError("disk full")
        return real_replace(source, destination)

    with monkeypatch.context() as context:
        context.setattr(library_operations.os, "replace", fail_main_replace)
        with pytest.raises(LibraryPersistenceError, match="Could not save"):
            operations.apply(prepared["token"], "retry-after-failure")

    assert path.read_bytes() == original
    assert (tmp_path / "custom_plants.json.bak").read_bytes() == original
    assert operations.result("retry-after-failure") is None

    succeeded = operations.apply(prepared["token"], "retry-after-failure")
    assert succeeded["created"] == 1
    assert "new" in operations.snapshot()


def test_library_facade_preserves_unknown_document_and_record_fields(tmp_path, monkeypatch):
    path = tmp_path / "isolated" / "custom_plants.json"
    original_record = _record("rose", "Rosier")
    original_record.update(future_trait={"grade": [1, 2]}, raw_data={"source_extra": {"x": 1}})
    original = _write_document(path, {"rose": original_record}, future_document={"v": 2})
    monkeypatch.setattr(
        "open_garden_planner.services.plant_library.get_app_data_dir",
        lambda: pytest.fail("explicit path must not resolve application data"),
    )

    library = PlantLibrary(path)
    edited = library.get_plant("rose")
    assert edited is not None
    edited.common_name = "Rosier modifié"
    assert library.update_plant("rose", edited) is True

    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["future_document"] == {"v": 2}
    assert after["plants"]["rose"]["future_trait"] == {"grade": [1, 2]}
    assert after["plants"]["rose"]["raw_data"]["source_extra"] == {"x": 1}
    assert (tmp_path / "isolated" / "custom_plants.json.bak").read_bytes() == original

    operations = LibraryOperations(path)
    changed = deepcopy(after["plants"]["rose"])
    changed["common_name"] = "Rosier via lot"
    preview = operations.prepare([changed])
    assert preview["entries"][0]["status"] == "update"
    operations.apply(preview["token"], "preserve-extra")
    final = json.loads(path.read_text(encoding="utf-8"))
    assert final["future_document"] == {"v": 2}
    assert final["plants"]["rose"]["future_trait"] == {"grade": [1, 2]}
    assert final["plants"]["rose"]["raw_data"]["source_extra"] == {"x": 1}


def test_legacy_library_save_reports_io_failure_without_changing_memory_or_file(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "custom_plants.json"
    original = _write_document(path, {"old": _record("old", "Ancienne")})
    library = PlantLibrary(path)
    incoming = PlantSpeciesData("Rosa", "Rose")
    real_replace = library_operations.os.replace

    def fail_main_replace(source, destination):
        if Path(destination) == path:
            raise OSError("read-only file system")
        return real_replace(source, destination)

    with monkeypatch.context() as context:
        context.setattr(library_operations.os, "replace", fail_main_replace)
        with pytest.raises(LibraryPersistenceError, match="Could not save"):
            library.add_plant(incoming)

    assert path.read_bytes() == original
    assert library.count == 1
    assert incoming.source_id == ""
    assert incoming.data_source == ""


def test_plant_library_legacy_signatures_keep_add_update_remove_and_import(tmp_path):
    library = PlantLibrary(tmp_path / "custom_plants.json")
    plant = PlantSpeciesData("Rosa gallica", "Rose", source_id="rose")
    assert library.add_plant(plant) == "rose"
    assert plant.data_source == "custom"
    assert library.update_plant("rose", PlantSpeciesData("Rosa gallica", "Rosier")) is True
    assert library.get_plant("rose").common_name == "Rosier"

    incoming = _record("imported", "Importée")
    assert library.import_from_dict({"imported": incoming}) == 1
    assert library.import_from_dict({"imported": incoming}) == 0
    assert library.remove_plant("imported") is True
    assert library.remove_plant("imported") is False
    assert library.count == 1


def test_unreadable_existing_file_cannot_be_replaced_by_facade_or_operations(tmp_path):
    path = tmp_path / "custom_plants.json"
    path.write_bytes(b'{"plants":')
    before = path.read_bytes()
    library = PlantLibrary(path)
    with pytest.raises(LibraryFileError, match="refusing to replace"):
        library.add_plant(PlantSpeciesData("Rosa", "Rose"))
    operations = LibraryOperations(path)
    with pytest.raises(LibraryFileError):
        operations.prepare([_record("new", "Nouvelle")])
    assert path.read_bytes() == before


def test_file_lock_is_nonblocking_and_keeps_its_lock_path(tmp_path):
    lock_path = tmp_path / "project.ogp.writer.lock"
    first = FileLock(lock_path)
    second = FileLock(lock_path)
    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.release()
    assert second.acquire() is True
    second.release()
    assert lock_path.exists()
