"""Pure garden planners and the Qt command adapter share one rule set."""

from __future__ import annotations

import copy
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from open_garden_planner.services.garden_operations import prepare_garden_operation

ROOT = Path(__file__).resolve().parents[2]


def document(*, locked: bool = False) -> dict:
    plant_id, bed_id, layer_id = str(uuid4()), str(uuid4()), str(uuid4())
    return {
        "version": "1.4",
        "metadata": {"future_metadata": {"preserve": True}},
        "canvas": {"width": 800, "height": 600},
        "layers": [{"id": layer_id, "name": "Layer", "locked": locked}],
        "objects": [
            {
                "type": "circle",
                "item_id": plant_id,
                "object_type": "PERENNIAL",
                "layer_id": layer_id,
                "name": "Bulbe fictif",
                "metadata": {
                    "plant_instance": {
                        "planting_date": "2020-04-03",
                        "notes": "Note conservée",
                        "future_instance": {"x": 1},
                    },
                    "garden_tracking": {"state": "dormant"},
                    "future_metadata": {"source": "fixture"},
                },
                "future_object_field": ["kept"],
            },
            {
                "type": "rectangle",
                "item_id": bed_id,
                "object_type": "GARDEN_BED",
                "layer_id": layer_id,
                "name": "Carré fictif",
                "metadata": {"plant_instance": {}},
            },
        ],
        "garden_memory": {},
        "future_document_field": {"keep": [1, 2]},
    }


def object_record(snapshot: dict, item_id: str) -> dict:
    matches = []

    def walk(records):
        for record in records:
            if record.get("item_id", record.get("id")) == item_id:
                matches.append(record)
            walk(record.get("children", []))

    walk(snapshot["objects"])
    assert len(matches) == 1
    return matches[0]


def event_fields(item_id: str, description: str = "Observation fictive") -> dict:
    return {
        "type": "observation",
        "status": "done",
        "description": description,
        "period": None,
        "targets": [{"id": item_id, "name": "Nom fourni"}],
    }


def test_card_preparation_preserves_unknowns_and_date_precision() -> None:
    source = document()
    before = copy.deepcopy(source)
    plant_id = source["objects"][0]["item_id"]

    candidate, result = prepare_garden_operation(
        source, "set_planting_date", {"planting_date": "Automne 2023"}, plant_id,
    )

    assert source == before
    plant = object_record(candidate, plant_id)
    assert plant["metadata"]["garden_tracking"]["period"] == {
        "precision": "season", "value": "2023-autumn", "label": "Automne 2023",
    }
    assert plant["metadata"]["garden_tracking"]["state"] == "dormant"
    assert plant["metadata"]["plant_instance"]["notes"] == "Note conservée"
    assert plant["metadata"]["plant_instance"]["future_instance"] == {"x": 1}
    assert "planting_date" not in plant["metadata"]["plant_instance"]
    assert plant["metadata"]["future_metadata"] == {"source": "fixture"}
    assert plant["future_object_field"] == ["kept"]
    assert candidate["future_document_field"] == {"keep": [1, 2]}
    assert result["card"]["period"]["precision"] == "season"
    assert candidate["garden_memory"] == {"version": 1, "events": {}, "requests": {}}

    unknown, _ = prepare_garden_operation(
        source, "set_planting_date", {"planting_date": None}, plant_id,
    )
    assert object_record(unknown, plant_id)["metadata"]["garden_tracking"]["period"]["precision"] == "unknown"
    exact, _ = prepare_garden_operation(
        source, "set_planting_date", {"planting_date": "2024-02-29"}, plant_id,
    )
    assert object_record(exact, plant_id)["metadata"]["plant_instance"]["planting_date"] == "2024-02-29"


def test_card_notes_cultivar_and_zone_rules() -> None:
    source = document()
    plant_id = source["objects"][0]["item_id"]
    bed_id = source["objects"][1]["item_id"]
    candidate, result = prepare_garden_operation(
        source, "set_card",
        {"name": "Étiquette fictive", "kind": "group", "zone_id": bed_id,
         "period": "2024-02", "notes": "Note", "cultivar": "Cultivar"},
        plant_id,
    )
    plant = object_record(candidate, plant_id)
    assert plant["name"] == "Étiquette fictive"
    assert plant["metadata"]["garden_tracking"]["zone_id"] == bed_id
    assert plant["metadata"]["plant_instance"]["notes"] == "Note"
    assert plant["metadata"]["plant_instance"]["variety_cultivar"] == "Cultivar"
    assert "notes" not in result["card"]
    assert result["card"]["period"]["precision"] == "month"

    with pytest.raises(ValueError, match="another existing bed"):
        prepare_garden_operation(
            source, "set_card", {"name": "Bed", "kind": "zone", "zone_id": bed_id}, bed_id,
        )
    with pytest.raises(ValueError, match="existing bed"):
        prepare_garden_operation(
            source, "set_card", {"name": "Plant", "kind": "zone", "zone_id": plant_id}, bed_id,
        )


@pytest.mark.parametrize("case", ["locked", "grouped", "duplicate"])
def test_ambiguous_or_uneditable_targets_are_refused(case: str) -> None:
    source = document(locked=case == "locked")
    plant = source["objects"][0]
    plant_id = plant["item_id"]
    if case == "grouped":
        source["objects"] = [{"type": "group", "item_id": str(uuid4()), "children": [plant]}]
    elif case == "duplicate":
        source["objects"].append(copy.deepcopy(plant))
    message = {"locked": "layer is locked", "grouped": "top-level object", "duplicate": "ambiguous"}[case]
    with pytest.raises(ValueError, match=message):
        prepare_garden_operation(
            source, "set_card", {"name": "Étiquette fictive", "state": "planned"}, plant_id,
        )


def test_group_itself_cannot_own_a_nonserializable_garden_card() -> None:
    group_id = str(uuid4())
    source = {"objects": [{"item_id": group_id, "type": "group", "name": "Groupe",
                           "metadata": {}}], "layers": [], "garden_memory": {}}
    with pytest.raises(ValueError, match="typed garden shape"):
        prepare_garden_operation(
            source, "set_card", {"name": "Étiquette", "state": "planned"}, group_id,
        )


def test_media_validation_checks_only_new_portable_paths() -> None:
    source = document()
    plant_id = source["objects"][0]["item_id"]
    source["objects"][0]["metadata"]["garden_tracking"]["media"] = ["images/existing.jpg"]
    checks = []
    prepare_garden_operation(
        source, "set_card",
        {"name": "Bulbe fictif", "media": ["images/existing.jpg", "images/new.jpg"]},
        plant_id, media_check=checks.append,
    )
    assert checks == ["images/new.jpg"]
    with pytest.raises(ValueError):
        prepare_garden_operation(
            source, "set_card", {"name": "Bulbe fictif", "media": ["../escape.jpg"]},
            plant_id, media_check=lambda _path: pytest.fail("invalid path reached media callback"),
        )


def test_legacy_receipt_survives_replay_delete_and_edit_of_historical_target() -> None:
    source = document()
    plant_id = source["objects"][0]["item_id"]
    fields = event_fields(plant_id)
    candidate, first = prepare_garden_operation(
        source, "add_event", fields, request_key="garden-client-1",
    )
    event = candidate["garden_memory"]["events"][first["event_id"]]
    assert event["targets"][0] == {"id": plant_id, "name": "Bulbe fictif"}
    assert source["garden_memory"] == {}

    replay, result = prepare_garden_operation(
        candidate, "add_event", fields, request_key="garden-client-1",
    )
    assert replay == candidate
    assert result == {"event_id": first["event_id"], "replayed": True,
                      "deleted": False, "persisted": False}

    deleted = copy.deepcopy(candidate)
    deleted["garden_memory"]["events"].clear()
    replay, result = prepare_garden_operation(
        deleted, "add_event", fields, request_key="garden-client-1",
    )
    assert replay == deleted and result["deleted"] is True
    with pytest.raises(ValueError, match="different content"):
        prepare_garden_operation(
            candidate, "add_event", event_fields(plant_id, "Différent"),
            request_key="garden-client-1",
        )

    historical = copy.deepcopy(candidate)
    historical["objects"].clear()
    edited, result = prepare_garden_operation(
        historical, "edit_event", {"description": "Historique corrigé"}, first["event_id"],
    )
    assert result["deleted"] is False
    assert edited["garden_memory"]["events"][first["event_id"]]["targets"] == [
        {"id": plant_id, "name": "Bulbe fictif"},
    ]


def test_planner_import_and_execution_are_qt_free() -> None:
    script = r'''
import builtins
import importlib.abc
import sys
from uuid import uuid4

blocked = ("PyQt6", "PySide")
original_import = builtins.__import__

class BlockQt(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == root or fullname.startswith(root + ".") for root in blocked):
            raise AssertionError(f"Qt import attempted: {fullname}")
        return None

sys.meta_path.insert(0, BlockQt())
def block_import(name, *args, **kwargs):
    if any(name == root or name.startswith(root + ".") for root in blocked):
        raise AssertionError(f"Qt import attempted: {name}")
    return original_import(name, *args, **kwargs)
builtins.__import__ = block_import

from open_garden_planner.services.garden_operations import prepare_garden_operation
plant_id = str(uuid4())
document = {
    "objects": [{"item_id": plant_id, "object_type": "PERENNIAL", "name": "Plant", "metadata": {}}],
    "layers": [],
    "garden_memory": {},
}
candidate, result = prepare_garden_operation(
    document, "set_planting_date", {"planting_date": "2022"}, plant_id,
)
assert result["card"]["period"]["precision"] == "year"
assert candidate["objects"][0]["metadata"]["garden_tracking"]["period"]["value"] == "2022"
event, added = prepare_garden_operation(
    candidate, "add_event", {
        "type": "observation", "status": "done", "description": "Observation",
        "targets": [{"id": plant_id, "name": "Plant"}],
    }, request_key="domain:fixture",
)
assert event["garden_memory"]["requests"]
assert not any(name == root or name.startswith(root + ".") for name in sys.modules
               for root in blocked)
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, env=os.environ.copy(),
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_live_service_matches_pure_candidate_and_keeps_one_undo_step(qtbot) -> None:
    from open_garden_planner.core.commands import CommandManager
    from open_garden_planner.core.garden_memory import GardenMemoryService
    from open_garden_planner.core.object_types import ObjectType
    from open_garden_planner.core.project import ProjectManager
    from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
    from open_garden_planner.ui.canvas.items import CircleItem, RectangleItem

    scene = CanvasScene()
    plant = CircleItem(
        300, 250, 30, object_type=ObjectType.PERENNIAL, name="Bulbe fictif",
        metadata={"plant_instance": {"notes": "Note conservée"},
                  "garden_tracking": {"state": "dormant"}},
    )
    bed = RectangleItem(100, 100, 600, 300, object_type=ObjectType.GARDEN_BED, name="Carré fictif")
    scene.addItem(plant)
    scene.addItem(bed)
    project, commands = ProjectManager(), CommandManager()
    service = GardenMemoryService(project, commands, scene)
    before = project.snapshot_dict(scene)
    candidate, expected = prepare_garden_operation(
        before, "set_planting_date", {"planting_date": "2024-03"}, str(plant.item_id),
    )

    actual = service.set_planting_date(str(plant.item_id), "2024-03")
    after = project.snapshot_dict(scene)

    assert actual == expected
    assert object_record(after, str(plant.item_id))["metadata"] == object_record(candidate, str(plant.item_id))["metadata"]
    assert after["garden_memory"] == candidate["garden_memory"]
    assert commands.undo_depth == 1
    commands.undo()
    assert plant.metadata["garden_tracking"] == {"state": "dormant"}
    assert "garden_memory" not in project.snapshot_dict(scene)

    event_fields = {
        "type": "observation", "status": "done", "description": "Observation fictive",
        "targets": [{"id": str(plant.item_id), "name": "Bulbe fictif"}],
    }
    first = service.add_event(event_fields, "legacy-retry-after-undo")
    commands.undo()
    retry = service.add_event(event_fields, "legacy-retry-after-undo")
    assert retry["event_id"] == first["event_id"]
    assert retry["replayed"] is True and retry["deleted"] is True
    assert commands.undo_depth == 0
