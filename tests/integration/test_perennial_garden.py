"""Real shape/command/persistence workflows for the personal perennial fork."""

from copy import deepcopy

import pytest

from open_garden_planner.core.commands import CommandManager
from open_garden_planner.core.garden_memory import GardenMemoryService
from open_garden_planner.core.object_types import ObjectType
from open_garden_planner.core.plan_io import read_plan, write_plan
from open_garden_planner.core.project import ProjectManager
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.canvas.items import CircleItem, RectangleItem


@pytest.fixture
def garden(qtbot):
    scene = CanvasScene(width_cm=2000, height_cm=1500)
    pm = ProjectManager()
    commands = CommandManager()
    commands.stack_changed.connect(pm.mark_dirty)
    bed = RectangleItem(100, 100, 500, 300, object_type=ObjectType.GARDEN_BED)
    plant = CircleItem(200, 200, 30, object_type=ObjectType.PERENNIAL)
    bed.name, plant.name = "Massif fictif", "bulbes à identifier"
    scene.addItem(bed)
    scene.addItem(plant)
    return scene, pm, commands, GardenMemoryService(pm, commands, scene), bed, plant


def fields(plant):
    return {"type": "planting", "status": "done", "description": "Groupe fictif",
            "targets": [{"id": str(plant.item_id), "name": plant.name}], "period": "Automne 2023"}


def test_group_partial_date_journal_undo_save_and_reopen(garden, tmp_path):
    scene, pm, commands, service, bed, plant = garden
    original_geometry = plant.sceneBoundingRect()
    result = service.set_card(str(plant.item_id), {
        "kind": "group", "state": "dormant", "period": "Automne 2023", "zone_id": str(bed.item_id),
    })
    assert "quantity" not in result["card"]
    assert plant.sceneBoundingRect() == original_geometry
    assert "planting_date" not in plant.metadata.get("plant_instance", {})
    event_id = service.add_event(fields(plant), "planting-1")["event_id"]
    path = tmp_path / "garden.ogp"
    pm.save(scene, path)
    assert not pm.is_dirty
    commands.undo()
    assert pm.is_dirty and not pm.garden_memory["events"]
    commands.redo()
    assert event_id in pm.garden_memory["events"]
    pm.save(scene, path)
    reopened, restored = CanvasScene(), ProjectManager()
    restored.load(reopened, path)
    found = next(i for i in reopened.items() if getattr(i, "item_id", None) == plant.item_id)
    assert found.name == "bulbes à identifier"
    assert found.metadata["garden_tracking"]["period"]["label"] == "Automne 2023"
    assert restored.garden_memory["events"][event_id]["period"]["precision"] == "season"


def test_idempotence_survives_save_reopen_delete_and_conflicts(garden, tmp_path):
    scene, pm, commands, service, bed, plant = garden
    body = fields(plant)
    first = service.add_event(body, "once")
    assert service.add_event(body, "once")["event_id"] == first["event_id"]
    with pytest.raises(ValueError, match="conflict"):
        service.add_event({**body, "description": "Other content"}, "once")
    service.edit_event(first["event_id"], None)
    assert service.add_event(body, "once")["deleted"]
    commands.undo()
    assert first["event_id"] in pm.garden_memory["events"]
    path = tmp_path / "garden.ogp"
    pm.save(scene, path)
    restored, other_scene = ProjectManager(), CanvasScene()
    restored.load(other_scene, path)
    other = GardenMemoryService(restored, CommandManager(), other_scene)
    assert other.add_event(body, "once")["replayed"]
    assert len(restored.garden_memory["events"]) == 1


def test_historical_area_name_and_events_survive_zone_change_and_new_year(garden, tmp_path):
    scene, pm, commands, service, bed, plant = garden
    body = {**fields(bed), "type": "amendment", "area_m2": 5, "quantity": None}
    identity = service.add_event(body, "amendment")["event_id"]
    before = deepcopy(pm.garden_memory)
    bed.name = "Massif renommé"
    bed.setRect(100, 100, 900, 700)
    new = tmp_path / "next-year.ogp"
    pm.create_new_season(scene, 2027, new, keep_plants=False)
    saved = read_plan(new)
    assert saved["garden_memory"] == before
    assert saved["garden_memory"]["events"][identity]["area_m2"] == 5
    assert {o["item_id"] for o in saved["objects"]} == {str(bed.item_id), str(plant.item_id)}


def test_original_is_backed_up_before_first_enrichment_and_bad_input_is_atomic(garden, tmp_path):
    scene, pm, commands, service, bed, plant = garden
    path = tmp_path / "legacy.ogp"
    pm.save(scene, path)
    original = path.read_bytes()
    service.set_card(str(plant.item_id), {"kind": "group", "period": None})
    pm.save(scene, path)
    assert (tmp_path / "legacy.pre-jardin.ogp").read_bytes() == original
    enriched = path.read_bytes()
    poisoned = read_plan(path)
    poisoned["garden_memory"]["version"] = 200
    with pytest.raises(ValueError):
        write_plan(path, poisoned)
    assert path.read_bytes() == enriched
    assert "planting_date" not in plant.metadata.get("plant_instance", {})


def test_visible_tracking_workflow_uses_existing_name_notes_and_undo(garden, qtbot, monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QDialog, QPushButton

    from open_garden_planner.ui.dialogs.garden_memory_dialog import EventEditor, GardenMemoryDialog

    scene, pm, commands, service, bed, plant = garden
    dialog = GardenMemoryDialog(service, str(plant.item_id))
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.name_edit.setText("Trente alliums fictifs")
    dialog.kind_combo.setCurrentIndex(dialog.kind_combo.findData("group"))
    dialog.state_combo.setCurrentIndex(dialog.state_combo.findData("dormant"))
    dialog.period_edit.setText("Automne 2023")
    dialog.notes_edit.setPlainText("Sachet inconnu — quantité non renseignée")
    dialog.zone_combo.setCurrentIndex(dialog.zone_combo.findData(str(bed.item_id)))
    save = next(b for b in dialog.findChildren(QPushButton) if b.text() == dialog.tr("Save card"))
    save.click()
    assert plant.name == "Trente alliums fictifs"
    assert plant.metadata["plant_instance"]["notes"].startswith("Sachet inconnu")
    assert "notes" not in plant.metadata["garden_tracking"]
    assert plant.metadata["garden_tracking"]["quantity"] is None

    def enter_event(editor):
        editor.description.setPlainText("Première observation fictive")
        editor.date_edit.clear()
        editor.accept()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(EventEditor, "exec", enter_event)
    add = next(b for b in dialog.findChildren(QPushButton) if b.text() == dialog.tr("Add intervention…"))
    add.click()
    assert dialog.events.count() == 1
    assert next(iter(pm.garden_memory["events"].values()))["period"]["precision"] == "unknown"
    undo = next(b for b in dialog.findChildren(QPushButton) if b.text() == dialog.tr("Undo"))
    undo.click()
    assert dialog.events.count() == 0
    commands.redo()
    assert dialog.events.count() == 1
    path = tmp_path / "dialog.ogp"
    pm.save(scene, path)
    assert read_plan(path)["objects"]
    dialog.close()


def test_unknown_extension_fields_are_preserved_by_fork(garden, tmp_path):
    scene, pm, commands, service, bed, plant = garden
    path = tmp_path / "extension.ogp"
    pm.save(scene, path)
    original = read_plan(path)
    original["unknown_extension"] = {"meaning": "must survive", "value": 2}
    original["metadata"]["external_provenance"] = "fictional"
    write_plan(path, original)
    pm.load(scene, path)
    pm.save(scene, path)
    saved = read_plan(path)
    assert saved["unknown_extension"] == original["unknown_extension"]
    assert saved["metadata"]["external_provenance"] == "fictional"


def test_portable_media_backup_restore_and_save_as(garden, tmp_path):
    from open_garden_planner.services.garden_archive import (
        export_csv,
        export_json,
        import_attachment,
        portable_backup,
        restore_backup,
    )

    scene, pm, commands, service, bed, plant = garden
    path = tmp_path / "garden.ogp"
    pm.save(scene, path)
    source = tmp_path / "sachet.txt"
    source.write_text("Étiquette fictive 1")
    first = import_attachment(source, path)
    source.write_text("Étiquette fictive 2")
    second = import_attachment(source, path)
    assert first != second  # Same basename, different contents, no overwrite.
    service.add_event({**fields(plant), "media": [first, second]}, "media-event")
    pm.save(scene, path)
    archive = tmp_path / "portable.zip"
    portable_backup(pm.snapshot_dict(scene), path, archive)
    source.unlink()
    restored_path = restore_backup(archive, tmp_path / "elsewhere")
    restored, restored_scene = ProjectManager(), CanvasScene()
    restored.load(restored_scene, restored_path)
    event = next(iter(restored.garden_memory["events"].values()))
    assert len(event["media"]) == 2
    assert (restored_path.parent / event["media"][0]).read_text() == "Étiquette fictive 1"
    copy_dir = tmp_path / "save-as"
    copy_dir.mkdir()
    pm.save(scene, copy_dir / "copy.ogp")
    assert (copy_dir / first).read_text() == "Étiquette fictive 1"
    export_json(restored.snapshot_dict(restored_scene), tmp_path / "tracking.json")
    export_csv(restored.snapshot_dict(restored_scene), tmp_path / "tracking.csv")
    assert read_plan(tmp_path / "tracking.json")["garden_memory"] == restored.garden_memory
    assert "Automne 2023" in (tmp_path / "tracking.csv").read_text(encoding="utf-8-sig")


def test_restore_rejects_path_traversal_without_partial_destination(tmp_path):
    import zipfile

    from open_garden_planner.services.garden_archive import restore_backup

    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("../escaped.txt", "bad")
    destination = tmp_path / "restored"
    with pytest.raises(ValueError, match="Unsafe"):
        restore_backup(archive, destination)
    assert not destination.exists() and not (tmp_path / "escaped.txt").exists()


def test_demo_cli_is_fictional_restorable_and_refuses_overwrite(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    target = tmp_path / "démonstration avec espaces.ogp"
    command = [sys.executable, str(root / "scripts/create_garden_demo.py"), "--output", str(target)]
    created = subprocess.run(command, capture_output=True, text=True, timeout=30,
                             env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    assert created.returncode == 0, created.stdout + created.stderr
    raw = read_plan(target)
    shapes = [o for o in raw["objects"] if o.get("object_type")]
    groups = [o for o in shapes if o["metadata"]["garden_tracking"]["kind"] == "group"]
    assert len(shapes) == 5 and len(groups) == 2
    assert groups[0]["item_id"] != groups[1]["item_id"]
    assert "location" not in raw
    assert any(o.get("image_data") for o in raw["objects"])
    assert len(raw["garden_memory"]["events"]) == 5
    before = target.read_bytes()
    refused = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert refused.returncode != 0 and target.read_bytes() == before
    scene, project = CanvasScene(), ProjectManager()
    project.load(scene, target)
    assert project.garden_memory["events"] == raw["garden_memory"]["events"]


def test_tracking_forms_fit_small_windows_and_attachment_buttons_are_readable(garden, qtbot):
    from PyQt6.QtGui import QFont
    from PyQt6.QtWidgets import QApplication, QPushButton

    from open_garden_planner.ui.dialogs.garden_memory_dialog import EventEditor, GardenMemoryDialog

    scene, pm, commands, service, bed, plant = garden
    application = QApplication.instance()
    old_font = application.font()
    try:
        application.setFont(QFont(old_font.family(), 13))
        dialog = GardenMemoryDialog(service, str(plant.item_id))
        qtbot.addWidget(dialog)
        dialog.show()
        dialog.resize(800, 600)
        qtbot.wait(10)
        assert dialog.width() == 800 and dialog.height() == 600
        editor = EventEditor([{"id": str(plant.item_id), "name": plant.name}])
        qtbot.addWidget(editor)
        editor.show()
        editor.resize(560, 520)
        qtbot.wait(10)
        assert editor.width() == 560 and editor.height() == 520
        for button in editor.findChildren(QPushButton):
            if button.isVisible():
                assert button.fontMetrics().horizontalAdvance(button.text()) + 16 <= button.width()
    finally:
        application.setFont(old_font)


def test_zone_events_and_plantings_remain_distinct_and_filterable(garden, qtbot):
    from open_garden_planner.services.garden_queries import garden_record, search_events
    from open_garden_planner.ui.dialogs.garden_memory_dialog import GardenMemoryDialog

    scene, pm, commands, service, bed, plant = garden
    service.set_card(str(plant.item_id), {"kind": "group", "zone_id": str(bed.item_id), "period": "Automne 2023"})
    own = service.add_event(fields(plant), "own")["event_id"]
    inherited = service.add_event({**fields(bed), "type": "mulching"}, "zone")["event_id"]
    snapshot = pm.snapshot_dict(scene)
    record = garden_record(snapshot, str(plant.item_id))
    assert {e["id"] for e in record["direct_events"]} == {own}
    assert {e["id"] for e in record["zone_events"]} == {inherited}
    zone = garden_record(snapshot, str(bed.item_id))
    assert zone["plantings"][0]["item_id"] == str(plant.item_id)
    assert len(search_events(snapshot, event_type="mulching", since="2023-10-15", until="2023-10-16")) == 1
    dialog = GardenMemoryDialog(service, str(bed.item_id))
    qtbot.addWidget(dialog)
    index = dialog.zone_members.findData(str(plant.item_id))
    dialog.zone_members.setCurrentIndex(index)
    dialog._select_zone_member()
    assert dialog.target_id == str(plant.item_id) and dialog.events.count() == 2


def test_locked_target_refuses_tracking_write_without_history_change(garden):
    scene, pm, commands, service, bed, plant = garden
    layer = scene.layers[0]
    plant.layer_id = layer.id
    layer.locked = True
    before = pm.garden_memory
    with pytest.raises(ValueError, match="locked"):
        service.add_event(fields(plant), "locked-event")
    assert pm.garden_memory == before and not commands.can_undo


def test_idempotence_receipt_survives_undo_and_refuses_late_resurrection(garden, tmp_path):
    scene, pm, commands, service, bed, plant = garden
    body = fields(plant)
    original = service.add_event(body, "durable-once")
    commands.undo()
    replay = service.add_event(body, "durable-once")
    assert replay["event_id"] == original["event_id"] and replay["deleted"]
    assert not pm.garden_memory["events"]
    with pytest.raises(ValueError, match="conflict"):
        service.add_event({**body, "description": "Different"}, "durable-once")
    path = tmp_path / "undone.ogp"
    pm.save(scene, path)
    fresh, other_scene = ProjectManager(), CanvasScene()
    fresh.load(other_scene, path)
    other = GardenMemoryService(fresh, CommandManager(), other_scene)
    assert other.add_event(body, "durable-once")["deleted"]


@pytest.mark.parametrize("label", ["", "Autour de la Toussaint, année incertaine"])
def test_unrelated_gui_edits_preserve_structured_periods(garden, qtbot, label):
    from open_garden_planner.ui.dialogs.garden_memory_dialog import EventEditor, GardenMemoryDialog

    scene, pm, commands, service, bed, plant = garden
    original = {"precision": "month", "value": "2023-10", "label": label}
    service.set_card(str(plant.item_id), {"kind": "group", "period": original})
    dialog = GardenMemoryDialog(service, str(plant.item_id))
    qtbot.addWidget(dialog)
    dialog.notes_edit.setPlainText("Note indépendante de la date")
    dialog._save_card()
    assert plant.metadata["garden_tracking"]["period"] == original
    body = {**fields(plant), "period": original}
    event_id = service.add_event(body, "structured-period")["event_id"]
    editor = EventEditor(body["targets"], pm.garden_memory["events"][event_id])
    qtbot.addWidget(editor)
    editor.description.setPlainText("Description indépendante de la date")
    editor.accept()
    assert editor.value["period"] == original
    service.edit_event(event_id, editor.value)
    assert pm.garden_memory["events"][event_id]["period"] == original
    # An intentional date change still updates the structured value.
    dialog.period_edit.setText("2024")
    dialog._save_card()
    assert plant.metadata["garden_tracking"]["period"]["precision"] == "year"
    editor.date_edit.clear()
    editor.accept()
    assert editor.value["period"]["precision"] == "unknown"


def test_locked_intervention_delete_shows_error_and_keeps_history(garden, qtbot, monkeypatch):
    from open_garden_planner.ui.dialogs.garden_memory_dialog import GardenMemoryDialog

    scene, pm, commands, service, bed, plant = garden
    service.add_event(fields(plant), "delete-locked")
    dialog = GardenMemoryDialog(service, str(plant.item_id))
    qtbot.addWidget(dialog)
    dialog.events.setCurrentRow(0)
    layer = scene.layers[0]
    plant.layer_id = layer.id
    layer.locked = True
    before, depth = pm.garden_memory, commands.undo_depth
    errors = []
    monkeypatch.setattr(dialog, "_error", lambda exc: errors.append(str(exc)))
    dialog._delete_event()
    assert errors and "locked" in errors[0]
    assert pm.garden_memory == before and commands.undo_depth == depth
