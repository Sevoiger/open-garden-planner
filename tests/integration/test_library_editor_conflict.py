"""Concurrent library changes must not be erased by an open plant editor."""

from __future__ import annotations

from copy import deepcopy

from open_garden_planner.core.object_types import ObjectType
from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services.library_operations import (
    LibraryOperations,
    LibraryPersistenceError,
)
from open_garden_planner.services.plant_library import PlantLibrary
from open_garden_planner.ui.canvas.items.circle_item import CircleItem
from open_garden_planner.ui.panels import plant_database_panel as panel_module
from open_garden_planner.ui.panels.plant_database_panel import PlantDatabasePanel


def _open_editor(qtbot, monkeypatch, tmp_path):
    path = tmp_path / "custom_plants.json"
    library = PlantLibrary(path)
    original = PlantSpeciesData(
        common_name="Before MCP",
        scientific_name="Rosa demo",
        source_id="synthetic-rose",
        data_source="custom",
        max_height_cm=100,
    )
    library.add_plant(original)
    monkeypatch.setattr(panel_module, "get_plant_library", lambda: library)

    item = CircleItem(
        100,
        100,
        20,
        object_type=ObjectType.SHRUB,
        metadata={"plant_species": original.to_dict()},
    )
    panel = PlantDatabasePanel()
    qtbot.addWidget(panel)
    panel.set_selected_items([item])
    return path, library, original, item, panel, LibraryOperations(path)


def _apply_mcp_name_change(
    operations: LibraryOperations, *, max_height_cm: float = 100
) -> dict:
    preparation = operations.prepare([{
        "source_id": "synthetic-rose",
        "data_source": "custom",
        "common_name": "After MCP",
        "scientific_name": "Rosa demo",
        "max_height_cm": max_height_cm,
    }])
    return operations.apply(preparation["token"], "synthetic-mcp-write")


def test_same_item_refresh_keeps_the_old_editor_revision_and_refuses_mcp_overwrite(
    qtbot, monkeypatch, tmp_path
):
    _, library, original, item, panel, operations = _open_editor(
        qtbot, monkeypatch, tmp_path
    )
    captured_revision = panel._current_plant_revision
    original_metadata = deepcopy(item.metadata["plant_species"])

    result = _apply_mcp_name_change(operations)
    assert result["status"] == "applied"
    library._load()

    # This is the refresh performed by the app after apply/undo. The same item
    # still carries its older plan snapshot, so it must not replace the editor's
    # captured revision or data.
    panel.set_selected_items([item])
    assert panel._current_plant_revision == captured_revision
    assert panel._current_plant_data.common_name == original.common_name

    # A different field edit is handled by the real Qt signal connection.
    panel.max_height_spin.setValue(240)

    stored = operations.snapshot()["synthetic-rose"]
    assert stored["common_name"] == "After MCP"
    assert stored["max_height_cm"] == 100
    assert item.metadata["plant_species"] == original_metadata
    assert panel._current_plant_data.to_dict() == original.to_dict()
    assert panel.max_height_spin.value() == 240
    assert not panel._library_edit_status.isHidden()
    assert "changed since this record was opened" in panel._library_edit_status.text()


def test_stale_store_cas_conflict_is_reported_without_qt_signal_exception(
    qtbot, monkeypatch, tmp_path
):
    _, _, original, item, panel, operations = _open_editor(
        qtbot, monkeypatch, tmp_path
    )
    original_metadata = deepcopy(item.metadata["plant_species"])
    _apply_mcp_name_change(operations)

    # Do not reload the singleton: the disk CAS must still reject the stale
    # cached revision and the Qt signal handler must consume the conflict.
    panel.max_height_spin.setValue(240)

    stored = operations.snapshot()["synthetic-rose"]
    assert stored["common_name"] == "After MCP"
    assert item.metadata["plant_species"] == original_metadata
    assert panel._current_plant_data.to_dict() == original.to_dict()
    assert not panel._library_edit_status.isHidden()


def test_successful_sequential_edits_advance_the_editor_revision(
    qtbot, monkeypatch, tmp_path
):
    _, _, _, item, panel, operations = _open_editor(qtbot, monkeypatch, tmp_path)

    panel.max_height_spin.setValue(240)
    first_revision = panel._current_plant_revision
    assert first_revision is not None
    assert operations.snapshot()["synthetic-rose"]["max_height_cm"] == 240

    panel.common_edit.setText("Edited locally")
    panel._on_field_changed()

    stored = operations.snapshot()["synthetic-rose"]
    assert stored["common_name"] == "Edited locally"
    assert stored["max_height_cm"] == 240
    assert panel._current_plant_revision != first_revision
    assert item.metadata["plant_species"]["common_name"] == "Edited locally"
    assert not panel._library_edit_status.isVisible()


def test_failed_library_write_leaves_editor_model_and_canvas_metadata_unchanged(
    qtbot, monkeypatch, tmp_path
):
    _, library, original, item, panel, _ = _open_editor(
        qtbot, monkeypatch, tmp_path
    )
    original_metadata = deepcopy(item.metadata["plant_species"])
    original_revision = panel._current_plant_revision

    def fail_write(*_args, **_kwargs):
        raise LibraryPersistenceError("synthetic disk failure")

    monkeypatch.setattr(library, "update_plant", fail_write)
    panel.max_height_spin.setValue(240)

    assert library.get_plant("synthetic-rose").to_dict() == original.to_dict()
    assert item.metadata["plant_species"] == original_metadata
    assert panel._current_plant_data.to_dict() == original.to_dict()
    assert panel._current_plant_revision == original_revision
    assert not panel._library_edit_status.isHidden()
    assert "could not save changes" in panel._library_edit_status.text().lower()


def test_missing_read_revision_keeps_custom_record_write_disabled(
    qtbot, monkeypatch, tmp_path
):
    _, library, original, item, panel, _ = _open_editor(
        qtbot, monkeypatch, tmp_path
    )
    original_metadata = deepcopy(item.metadata["plant_species"])
    panel._current_plant_revision = None

    panel.max_height_spin.setValue(240)

    assert library.get_plant("synthetic-rose").to_dict() == original.to_dict()
    assert item.metadata["plant_species"] == original_metadata
    assert panel._current_plant_data.to_dict() == original.to_dict()
    assert not panel._library_edit_status.isHidden()
    assert "could not save changes" in panel._library_edit_status.text().lower()


def test_deselect_and_reselect_does_not_rearm_a_stale_canvas_snapshot(
    qtbot, monkeypatch, tmp_path
):
    _, library, original, item, panel, operations = _open_editor(
        qtbot, monkeypatch, tmp_path
    )
    original_metadata = deepcopy(item.metadata["plant_species"])
    _apply_mcp_name_change(operations, max_height_cm=180)
    library._load()

    # The app refreshes the same selected item after its import. Its project
    # metadata remains old, so this edit is refused and both stores stay intact.
    panel.set_selected_items([item])
    panel.max_height_spin.setValue(240)
    assert operations.snapshot()["synthetic-rose"]["common_name"] == "After MCP"
    assert operations.snapshot()["synthetic-rose"]["max_height_cm"] == 180
    assert item.metadata["plant_species"] == original_metadata

    # A later deselection/reselection is not permission to bless that old
    # embedded snapshot with the newer library revision.
    panel.set_selected_items([])
    panel.set_selected_items([item])
    assert panel._current_plant_revision is None
    assert panel._plant_revision_conflict
    panel.max_height_spin.setValue(300)

    stored = operations.snapshot()["synthetic-rose"]
    assert stored["common_name"] == "After MCP"
    assert stored["max_height_cm"] == 180
    assert item.metadata["plant_species"] == original_metadata
    assert panel._current_plant_data.to_dict() == original.to_dict()
    assert "changed since this record was opened" in panel._library_edit_status.text()


def test_explicit_load_of_current_library_species_rearms_editor(
    qtbot, monkeypatch, tmp_path
):
    _, library, original, item, panel, operations = _open_editor(
        qtbot, monkeypatch, tmp_path
    )
    original_metadata = deepcopy(item.metadata["plant_species"])
    _apply_mcp_name_change(operations, max_height_cm=180)
    library._load()

    panel.set_selected_items([item])
    panel.max_height_spin.setValue(240)
    assert operations.snapshot()["synthetic-rose"]["common_name"] == "After MCP"
    assert item.metadata["plant_species"] == original_metadata
    assert panel._current_plant_data.to_dict() == original.to_dict()

    from open_garden_planner.ui.dialogs import custom_plants_dialog

    class SelectedCurrentPlantDialog:
        def __init__(self, _parent):
            self.selected_plant = library.get_plant("synthetic-rose")

        def setWindowTitle(self, _title):
            pass

        def exec(self):
            return True

    monkeypatch.setattr(
        custom_plants_dialog, "CustomPlantsDialog", SelectedCurrentPlantDialog
    )
    panel._on_load_custom_plant()

    assert panel._current_plant_revision == library.revision
    assert not panel._plant_revision_conflict
    assert panel._current_plant_data.common_name == "After MCP"
    assert item.metadata["plant_species"]["common_name"] == "After MCP"
    assert operations.snapshot()["synthetic-rose"]["max_height_cm"] == 180

    panel.max_height_spin.setValue(240)

    stored = operations.snapshot()["synthetic-rose"]
    assert stored["common_name"] == "After MCP"
    assert stored["max_height_cm"] == 240
    assert item.metadata["plant_species"]["max_height_cm"] == 240
    assert panel._current_plant_data.max_height_cm == 240
