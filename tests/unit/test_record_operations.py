"""Pure preparation tests for harvest, pest and journal records."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import uuid
from copy import deepcopy
from pathlib import Path

import pytest

from open_garden_planner.services.record_operations import prepare_record_operation

_ROOT = Path(__file__).resolve().parents[2]


def _id() -> str:
    return str(uuid.uuid4())


def _document(*, object_type: str = "TREE", locked: bool = False) -> tuple[dict, str]:
    item_id = _id()
    layer_id = _id()
    return ({
        "version": "1.4",
        "layers": [{"id": layer_id, "locked": locked}],
        "objects": [{
            "item_id": item_id,
            "object_type": object_type,
            "layer_id": layer_id,
            "metadata": {},
        }],
        "future_project_key": {"preserve": True},
    }, item_id)


def test_module_imports_with_qt_imports_blocked() -> None:
    source = textwrap.dedent(
        """
        import importlib.abc
        import sys

        class BlockQt(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "PyQt6" or fullname.startswith("PyQt6."):
                    raise AssertionError(f"Qt import attempted: {fullname}")
                if fullname == "PySide6" or fullname.startswith("PySide6."):
                    raise AssertionError(f"Qt import attempted: {fullname}")
                return None

        sys.meta_path.insert(0, BlockQt())
        from open_garden_planner.services.record_operations import prepare_record_operation
        from open_garden_planner.models.harvest_log import HarvestRecord
        assert prepare_record_operation and HarvestRecord
        """
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(_ROOT / "src"), env.get("PYTHONPATH", "")])
    )
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_record_models_round_trip_future_fields() -> None:
    from open_garden_planner.models.harvest_log import HarvestHistory, HarvestRecord
    from open_garden_planner.models.journal_note import JournalNote
    from open_garden_planner.models.pest_log import PestLogHistory, PestLogRecord

    harvest = HarvestHistory.from_dict({
        "target_id": _id(),
        "records": [{"id": _id(), "date": "", "quantity": 0,
                     "unit": "kg", "future_record": 1}],
        "future_history": 2,
    }).to_dict()
    assert harvest["future_history"] == 2
    assert harvest["records"][0]["future_record"] == 1

    pest = PestLogHistory.from_dict({
        "target_id": _id(),
        "records": [{"id": _id(), "date": "", "future_record": 3}],
        "future_history": 4,
    }).to_dict()
    assert pest["future_history"] == 4
    assert pest["records"][0]["future_record"] == 3
    assert PestLogRecord.from_dict(pest["records"][0]).to_dict()["future_record"] == 3

    note = JournalNote.from_dict({"id": _id(), "date": "", "text": "x", "future": 5})
    assert note.to_dict()["future"] == 5
    assert HarvestRecord.from_dict(harvest["records"][0]).to_dict()["future_record"] == 1


def test_harvest_add_keeps_zero_unknown_date_and_future_fields() -> None:
    document, target = _document()
    before = deepcopy(document)
    record_id = _id()
    note_id = _id()

    candidate, result = prepare_record_operation(
        document,
        "add_harvest_record",
        {
            "id": record_id,
            "date": "",
            "quantity": 0,
            "unit": "kg",
            "photo_path": None,
            "journal_note_id": note_id,
            "future_record_field": {"retained": True},
            "species_key": "tomato",
            "species_name": "Tomato",
        },
        target,
    )

    assert document == before
    assert result == {
        "operation": "add_harvest_record",
        "target_id": target,
        "record_id": record_id,
        "journal_note_id": note_id,
        "status": "prepared",
    }
    raw_history = candidate["harvest_logs"][target]
    raw_record = raw_history["records"][0]
    assert raw_record["quantity"] == 0.0
    assert raw_record["date"] == ""
    assert raw_record["future_record_field"] == {"retained": True}
    assert raw_history["species_name"] == "Tomato"
    assert candidate["garden_journal_notes"][note_id]["tags"] == ["harvest"]
    assert candidate["future_project_key"] == {"preserve": True}


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"date": "", "unit": "kg"}, id="missing"),
        pytest.param({"date": "", "unit": "kg", "quantity": None}, id="unknown"),
        pytest.param({"date": "", "unit": "kg", "quantity": ""}, id="invalid"),
    ],
)
def test_harvest_unknown_quantity_is_not_coerced_to_zero(fields: dict) -> None:
    document, target = _document()
    with pytest.raises(ValueError, match="quantity"):
        prepare_record_operation(document, "add_harvest_record", fields, target)


def test_harvest_edit_preserves_store_extras_and_checks_only_new_media() -> None:
    document, target = _document()
    record_id = _id()
    note_id = _id()
    document["harvest_logs"] = {
        target: {
            "target_id": target,
            "records": [{
                "id": record_id,
                "date": "2026-06-01",
                "quantity": 1,
                "unit": "kg",
                "photo_path": "photos/tomato.jpg",
                "journal_note_id": note_id,
                "future_record": "keep",
            }],
            "species_name": "Tomato",
            "future_history": [1, 2],
        }
    }
    document["garden_journal_notes"] = {
        note_id: {
            "id": note_id, "date": "2026-06-01", "text": "old",
            "tags": ["harvest"], "scene_x": 0, "scene_y": 0,
            "future_note": 3,
        }
    }
    checked: list[str] = []

    candidate, _ = prepare_record_operation(
        document,
        "edit_harvest_record",
        {"id": record_id, "quantity": 2.5},
        target,
        media_check=checked.append,
    )
    edited = candidate["harvest_logs"][target]
    assert edited["future_history"] == [1, 2]
    assert edited["records"][0]["future_record"] == "keep"
    assert edited["records"][0]["date"] == "2026-06-01"
    assert edited["records"][0]["quantity"] == 2.5
    assert candidate["garden_journal_notes"][note_id]["future_note"] == 3
    assert checked == []  # unchanged reference is not reopened by the host


def test_editing_other_harvest_fields_preserves_unknown_quantity() -> None:
    document, target = _document()
    record_id = _id()
    document["harvest_logs"] = {
        target: {
            "target_id": target,
            "records": [{"id": record_id, "date": "", "unit": "kg"}],
        }
    }

    candidate, _ = prepare_record_operation(
        document,
        "edit_harvest_record",
        {"id": record_id, "notes": "weighed later"},
        target,
    )
    updated = candidate["harvest_logs"][target]["records"][0]
    assert "quantity" not in updated
    assert updated["notes"] == "weighed later"


def test_harvest_delete_removes_link_and_preserves_future_history_fields() -> None:
    document, target = _document()
    record_id = _id()
    note_id = _id()
    document["harvest_logs"] = {
        target: {
            "target_id": target,
            "records": [{"id": record_id, "date": "", "quantity": 0, "unit": "kg",
                         "journal_note_id": note_id}],
            "future_history": "keep",
        }
    }
    document["garden_journal_notes"] = {
        note_id: {"id": note_id, "date": "", "text": "summary", "tags": ["harvest"]}
    }

    candidate, _ = prepare_record_operation(
        document, "delete_harvest_record", {"record_id": record_id}, target
    )
    assert candidate["harvest_logs"][target]["records"] == []
    assert note_id not in candidate["garden_journal_notes"]
    assert candidate["harvest_logs"][target]["future_history"] == "keep"
    # Source snapshot is unchanged.
    assert document["garden_journal_notes"][note_id]["text"] == "summary"


def test_pest_edit_delete_round_trip_keeps_unknown_fields() -> None:
    document, target = _document(object_type="GARDEN_BED")
    record_id = _id()
    document["pest_disease_logs"] = {
        target: {
            "target_id": target,
            "records": [{
                "id": record_id, "date": "", "entry_type": "pest",
                "name": "Aphids", "severity": "low", "resolved": False,
                "future_record": {"source": "newer-client"},
            }],
            "future_history": True,
        }
    }
    edited, _ = prepare_record_operation(
        document,
        "edit_pest_log",
        {"id": record_id, "severity": "high", "resolved": True},
        target,
    )
    raw = edited["pest_disease_logs"][target]
    assert raw["records"][0]["severity"] == "high"
    assert raw["records"][0]["resolved"] is True
    assert raw["records"][0]["date"] == ""
    assert raw["records"][0]["future_record"] == {"source": "newer-client"}
    assert raw["future_history"] is True

    deleted, _ = prepare_record_operation(
        edited, "delete_pest_log", {"record_id": record_id}, target
    )
    assert deleted["pest_disease_logs"][target]["records"] == []
    assert document["pest_disease_logs"][target]["records"][0]["severity"] == "low"


def test_target_uuid_layer_and_group_checks() -> None:
    document, target = _document(locked=True)
    with pytest.raises(ValueError, match="locked"):
        prepare_record_operation(
            document, "add_pest_log", {"name": "aphids"}, target
        )

    unlocked, valid_id = _document()
    unlocked["objects"][0]["children"] = [{
        "item_id": _id(), "object_type": "TREE", "metadata": {},
    }]
    child_id = unlocked["objects"][0]["children"][0]["item_id"]
    with pytest.raises(ValueError, match="top-level"):
        prepare_record_operation(
            unlocked, "add_harvest_record", {"quantity": 1}, child_id
        )
    with pytest.raises(ValueError, match="UUID"):
        prepare_record_operation(
            unlocked, "add_harvest_record", {"quantity": 1}, "bed-1"
        )
    with pytest.raises(ValueError, match="Unknown harvest/pest target"):
        prepare_record_operation(
            unlocked, "add_harvest_record", {"quantity": 1}, _id()
        )
    assert valid_id != child_id


def test_journal_operations_are_body_only_and_refuse_pinned_delete() -> None:
    document, _ = _document()
    note_id = _id()
    added, _ = prepare_record_operation(
        document,
        "add_journal_note",
        {"id": note_id, "date": "", "text": "A note", "future": "keep"},
    )
    assert added["objects"] == document["objects"]
    assert added["garden_journal_notes"][note_id]["scene_x"] == 0.0
    assert added["garden_journal_notes"][note_id]["future"] == "keep"
    with pytest.raises(ValueError, match="position"):
        prepare_record_operation(
            document,
            "add_journal_note",
            {"id": _id(), "text": "Pin", "scene_x": 12},
        )

    pinned_document = deepcopy(added)
    pinned_document["objects"].append({
        "object_type": "GARDEN_JOURNAL_PIN", "note_id": note_id,
    })
    with pytest.raises(ValueError, match="Qt canvas command"):
        prepare_record_operation(
            pinned_document, "delete_journal_note", {}, note_id
        )
    body_edit, _ = prepare_record_operation(
        added, "edit_journal_note", {"text": "Updated"}, note_id
    )
    assert body_edit["garden_journal_notes"][note_id]["text"] == "Updated"
    assert body_edit["garden_journal_notes"][note_id]["future"] == "keep"
    deleted, _ = prepare_record_operation(
        added, "delete_journal_note", {}, note_id
    )
    assert note_id not in deleted["garden_journal_notes"]


def test_media_callback_checks_only_new_relative_reference() -> None:
    document, target = _document()
    checked: list[str] = []
    prepare_record_operation(
        document,
        "add_pest_log",
        {"name": "aphids", "photo_path": "pest_photos/aphids.jpg"},
        target,
        media_check=checked.append,
    )
    assert checked == ["pest_photos/aphids.jpg"]

    with pytest.raises(ValueError, match="project directory"):
        prepare_record_operation(
            document,
            "add_pest_log",
            {"name": "aphids", "photo_path": "../outside.jpg"},
            target,
        )


def test_pest_command_applies_the_same_candidate_and_undoes_exactly() -> None:
    from open_garden_planner.core.commands import AddPestLogCommand
    from open_garden_planner.models.pest_log import PestLogRecord

    class ProjectStub:
        def __init__(self) -> None:
            self._pest: dict[str, dict] = {}
            self._harvest: dict[str, dict] = {}
            self._notes: dict[str, dict] = {}

        @property
        def pest_logs(self) -> dict[str, dict]:
            return deepcopy(self._pest)

        @property
        def harvest_logs(self) -> dict[str, dict]:
            return deepcopy(self._harvest)

        @property
        def garden_journal_notes(self) -> dict[str, dict]:
            return deepcopy(self._notes)

        def restore_pest_log_history(self, target_id: str, history: dict | None) -> None:
            if history is None:
                self._pest.pop(target_id, None)
            else:
                self._pest[target_id] = deepcopy(history)

    document, target = _document(object_type="GARDEN_BED")
    record = PestLogRecord(date="", name="Aphids", severity="medium")
    expected, _ = prepare_record_operation(
        document, "add_pest_log", record.to_dict(), target
    )
    pm = ProjectStub()
    command = AddPestLogCommand(pm, target, record)

    command.execute()
    assert pm.pest_logs == expected["pest_disease_logs"]
    command.undo()
    assert pm.pest_logs == {}
