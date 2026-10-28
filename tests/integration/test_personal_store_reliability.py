"""Qt surfaces keep personal-store failures explicit and recoverable."""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

from open_garden_planner.core.object_types import ObjectType
from open_garden_planner.core.personal_store import PersonalStoreLoadState
from open_garden_planner.core.tools import ToolType
from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.models.plant_lists import PlantListStore
from open_garden_planner.models.seed_inventory import SeedInventoryStore, SeedPacket
from open_garden_planner.services.plant_api import PlantAPIManager
from open_garden_planner.ui.dialogs.plant_search_dialog import PlantSearchDialog
from open_garden_planner.ui.panels.plant_database_panel import PlantDatabasePanel
from open_garden_planner.ui.panels.plant_lists_panel import PlantListsPanel
from open_garden_planner.ui.views.seed_inventory_view import SeedInventoryView
from open_garden_planner.ui.widgets.category_dropdown import CategoryDropdown
from open_garden_planner.ui.widgets.gallery_data import GalleryCategory, GalleryItem


def test_plant_lists_panel_reports_load_error_and_retries_after_repair(
    qtbot, tmp_path: Path
) -> None:
    path = tmp_path / "plant_lists.json"
    original = b"{broken"
    path.write_bytes(original)
    store = PlantListStore(path)
    panel = PlantListsPanel(store=store)
    qtbot.addWidget(panel)
    panel.show()

    assert store.load_state == PersonalStoreLoadState.FAILED
    assert not panel._storage_status.isHidden()
    assert "could not be loaded" in panel._storage_status._message.text().lower()
    assert not panel._list_combo.isEnabled()
    assert path.read_bytes() == original

    path.write_text('{"version":"1.0","lists":[]}', encoding="utf-8")
    qtbot.mouseClick(panel._storage_status._retry, Qt.MouseButton.LeftButton)
    assert store.load_state == PersonalStoreLoadState.EMPTY
    assert panel._storage_status.isHidden()
    assert panel._list_combo.isEnabled()


def test_plant_list_save_failure_keeps_memory_until_confirmed_reload(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    from open_garden_planner.core import plan_io

    path = tmp_path / "plant_lists.json"
    store = PlantListStore(path)
    panel = PlantListsPanel(store=store)
    qtbot.addWidget(panel)
    real_replace = plan_io.os.replace

    def fail_replace(source, destination) -> None:
        if Path(destination) == path:
            raise OSError("permission denied")
        real_replace(source, destination)

    with monkeypatch.context() as scoped:
        scoped.setattr(plan_io.os, "replace", fail_replace)
        assert panel._run_store_action(lambda: store.create_list("Pending")) is None
        assert store.is_dirty
        assert "in memory but were not saved" in panel._storage_status._message.text()
        assert store.get_list(PlantListStore.FAVORITES_ID) is not None
        assert any(row.name == "Pending" for row in store.all_lists())
        assert not path.exists()

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
    )
    panel._reload_storage()
    assert store.is_dirty
    assert any(row.name == "Pending" for row in store.all_lists())

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    panel._reload_storage()
    assert not store.is_dirty
    assert not any(row.name == "Pending" for row in store.all_lists())


def test_seed_inventory_view_reports_failed_and_save_states(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    from open_garden_planner.core import plan_io
    from open_garden_planner.ui.views import seed_inventory_view

    path = tmp_path / "seed_inventory.json"
    path.write_text("NaN", encoding="utf-8")
    store = SeedInventoryStore(path)
    monkeypatch.setattr(seed_inventory_view, "get_seed_inventory", lambda: store)
    view = SeedInventoryView()
    qtbot.addWidget(view)
    view.show()

    assert store.load_state == PersonalStoreLoadState.FAILED
    assert not view._storage_status.isHidden()
    assert view._stats_lbl.text() == view.tr("Inventory unavailable")
    assert not view._add_btn.isEnabled()
    assert path.read_text(encoding="utf-8") == "NaN"

    path.write_text("[]", encoding="utf-8")
    qtbot.mouseClick(view._storage_status._retry, Qt.MouseButton.LeftButton)
    assert store.load_state == PersonalStoreLoadState.EMPTY
    assert view._storage_status.isHidden()
    assert view._add_btn.isEnabled()

    real_replace = plan_io.os.replace

    def fail_replace(source, destination) -> None:
        if Path(destination) == path:
            raise OSError("disk is full")
        real_replace(source, destination)

    with monkeypatch.context() as scoped:
        scoped.setattr(plan_io.os, "replace", fail_replace)
        view._mutate_and_save(
            lambda: store.add(SeedPacket(id="pending", species_name="Pending seed"))
        )
        assert store.is_dirty
        assert view._model.rowCount() == 1
        assert not view._storage_status.isHidden()
        assert "in memory but were not saved" in view._storage_status._message.text()
        assert not view._add_btn.isEnabled()
        assert path.read_text(encoding="utf-8") == "[]"

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
    )
    view._reload_storage()
    assert store.is_dirty
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    view._reload_storage()
    assert not store.is_dirty
    assert view._model.rowCount() == 0


def test_other_plant_ui_surfaces_show_store_failures(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    import open_garden_planner.models.plant_lists as plant_lists_module
    import open_garden_planner.models.seed_inventory as seed_inventory_module

    lists_path = tmp_path / "plant_lists.json"
    lists_path.write_text("{broken", encoding="utf-8")
    list_store = PlantListStore(lists_path)
    monkeypatch.setattr(plant_lists_module, "get_plant_list_store", lambda: list_store)
    monkeypatch.setattr(PlantSearchDialog, "_perform_search", lambda *_args, **_kwargs: None)

    dialog = PlantSearchDialog(PlantAPIManager())
    qtbot.addWidget(dialog)
    dialog._selected_plant = PlantSpeciesData("Mystery plant", "Mystery plant")
    dialog._update_favorite_button()
    assert not dialog.favorite_btn.isEnabled()
    assert not dialog._list_store_status.isHidden()
    dialog.close()

    category = GalleryCategory("Plants", [
        GalleryItem("Mystery plant", ToolType.TREE, ObjectType.TREE, species="mystery"),
    ])
    dropdown = CategoryDropdown(category)
    qtbot.addWidget(dropdown)
    thumbnail = dropdown._buttons[0]
    assert "personal plant lists unavailable" in thumbnail.toolTip().lower()

    seed_path = tmp_path / "seed_inventory.json"
    seed_path.write_text("{broken", encoding="utf-8")
    seed_store = SeedInventoryStore(seed_path)
    monkeypatch.setattr(seed_inventory_module, "get_seed_inventory", lambda: seed_store)
    database_panel = PlantDatabasePanel()
    qtbot.addWidget(database_panel)
    database_panel._populate_seed_packet_combo(
        PlantSpeciesData("Mystery plant", "Mystery plant"), {}
    )
    assert database_panel.seed_packet_combo.itemText(1) == database_panel.tr(
        "Seed inventory unavailable"
    )
    assert not database_panel._seed_store_status.isHidden()


def test_favorite_store_retry_clears_load_status_without_a_selection(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    import open_garden_planner.models.plant_lists as plant_lists_module

    search_path = tmp_path / "search_lists.json"
    search_path.write_text("{broken", encoding="utf-8")
    current_store = {"value": PlantListStore(search_path)}
    monkeypatch.setattr(
        plant_lists_module, "get_plant_list_store", lambda: current_store["value"]
    )
    monkeypatch.setattr(PlantSearchDialog, "_perform_search", lambda *_a, **_k: None)

    dialog = PlantSearchDialog(PlantAPIManager())
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._selected_plant = PlantSpeciesData("Mystery", "Mystery plant")
    dialog._update_favorite_button()
    assert not dialog._list_store_status.isHidden()
    dialog._selected_plant = None
    search_path.write_text('{"version":"1.0","lists":[]}', encoding="utf-8")

    qtbot.mouseClick(dialog._list_store_status._retry, Qt.MouseButton.LeftButton)

    assert dialog._list_store_status.isHidden()
    dialog.close()

    panel_path = tmp_path / "database_lists.json"
    panel_path.write_text("{broken", encoding="utf-8")
    current_store["value"] = PlantListStore(panel_path)
    panel = PlantDatabasePanel()
    qtbot.addWidget(panel)
    panel.show()
    panel._current_plant_data = PlantSpeciesData("Mystery", "Mystery plant")
    panel._update_favorite_button()
    assert not panel._list_store_status.isHidden()
    panel._current_plant_data = None
    panel_path.write_text('{"version":"1.0","lists":[]}', encoding="utf-8")

    qtbot.mouseClick(panel._list_store_status._retry, Qt.MouseButton.LeftButton)

    assert panel._list_store_status.isHidden()


def test_full_app_opens_with_both_stores_corrupt_and_mcp_reads_fail(
    qtbot, tmp_path: Path
) -> None:
    from open_garden_planner.app.application import GardenPlannerApp
    from open_garden_planner.app.settings import get_settings
    from open_garden_planner.core.personal_store import PersonalStoreLoadError
    from open_garden_planner.models.plant_lists import get_plant_list_store
    from open_garden_planner.models.seed_inventory import get_seed_inventory
    from open_garden_planner.services.plant_library import get_app_data_dir

    get_settings().show_welcome_on_startup = False
    app_data = get_app_data_dir()
    app_data.mkdir(parents=True, exist_ok=True)
    list_path = app_data / "plant_lists.json"
    seed_path = app_data / "seed_inventory.json"
    list_bytes = b'{"lists":[broken]}'
    seed_bytes = b'{broken'
    list_path.write_bytes(list_bytes)
    seed_path.write_bytes(seed_bytes)

    window = GardenPlannerApp()
    qtbot.addWidget(window)
    try:
        list_store = get_plant_list_store()
        seed_store = get_seed_inventory()
        assert list_store.load_state == PersonalStoreLoadState.FAILED
        assert seed_store.load_state == PersonalStoreLoadState.FAILED
        assert not window.plant_lists_panel._storage_status.isHidden()
        assert not window.seed_inventory_view._storage_status.isHidden()
        assert not window.calendar_view._seed_store_status.isHidden()
        window.plant_database_panel._populate_seed_packet_combo(
            PlantSpeciesData("Mystery plant", "Mystery plant"), {}
        )
        assert not window.plant_database_panel._seed_store_status.isHidden()
        with pytest.raises(PersonalStoreLoadError):
            window._agent_get_plant_lists()
        with pytest.raises(PersonalStoreLoadError):
            window._agent_get_seed_inventory()
        assert list_path.read_bytes() == list_bytes
        assert seed_path.read_bytes() == seed_bytes
    finally:
        window._stop_agent_api()
