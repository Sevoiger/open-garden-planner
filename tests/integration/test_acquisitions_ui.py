"""A user can enter and reopen a lot without turning it into a shopping list."""
from PyQt6.QtWidgets import QDialog

from open_garden_planner.services.acquisition_inventory import AcquisitionInventory
from open_garden_planner.ui.dialogs.acquisition_inventory_dialog import (
    AcquisitionEditor,
    AcquisitionInventoryDialog,
)


def test_acquisition_dialog_persists_a_lot_and_quantity(qtbot, monkeypatch, tmp_path):
    store = AcquisitionInventory(tmp_path / "acquisitions.json")
    dialog = AcquisitionInventoryDialog(store)
    qtbot.addWidget(dialog)

    def enter(editor):
        editor.fields["name"].setText("Compost fictif")
        editor.kind.setCurrentText("Compost")
        editor.fields["supplier"].setText("Fournisseur fictif")
        editor.fields["unit_price"].setText("12,5")
        editor.fields["purchased_quantity"].setText("4")
        editor.fields["remaining_quantity"].setText("3")
        editor.fields["unit"].setText("sac")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AcquisitionEditor, "exec", enter)
    dialog._add()
    assert dialog.table.rowCount() == 1
    record = AcquisitionInventory(store.path).all()[0]
    assert record["unit_price"] == 12.5 and record["remaining_quantity"] == 3
    assert record["supplier"] == "Fournisseur fictif" and not record["purchase_date"]


def test_bad_backup_is_reported_without_closing_project(qtbot, monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QFileDialog, QMessageBox

    from open_garden_planner.app.application import GardenPlannerApp

    monkeypatch.setattr(GardenPlannerApp, "_startup_sequence", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_start_update_check", lambda _self: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Discard)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_a, **_kw: warnings.append(True))
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"not a zip")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_a, **_kw: (str(broken), ""))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_a, **_kw: str(tmp_path))
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    window._on_extract_global_backup()
    assert warnings and window.isVisible()
    assert not list(tmp_path.glob("garden-restored-*"))
    window.close()


def test_acquisitions_are_readable_from_live_authenticated_mcp(qtbot, monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QMessageBox

    import open_garden_planner.services.plant_library as plant_library
    from open_garden_planner.app.application import GardenPlannerApp
    from tests.integration.test_mcp_horticultural_cycle import run_cycle

    monkeypatch.setattr(GardenPlannerApp, "_startup_sequence", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_start_update_check", lambda _self: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Discard)
    isolated_app_data = tmp_path / "explicit-isolated-app-data"
    monkeypatch.setattr(plant_library, "_default_app_data_dir", lambda: isolated_app_data)
    store = AcquisitionInventory(isolated_app_data / "acquisitions.json")
    store.put({"name": "Compost fictif", "kind": "Compost", "remaining_quantity": 3})
    window = GardenPlannerApp()
    qtbot.addWidget(window)

    async def body(desktop):
        result = await desktop.call("get_acquisitions")
        assert result["read_only"] and result["scope"] == "global"
        assert result["records"][0]["remaining_quantity"] == 3

    run_cycle(window, qtbot, body)
    window.close()


def test_acquisitions_available_in_tab_and_refreshes_external_changes(qtbot, monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QMessageBox

    import open_garden_planner.services.plant_library as plant_library
    from open_garden_planner.app.application import GardenPlannerApp

    monkeypatch.setattr(GardenPlannerApp, "_startup_sequence", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_start_update_check", lambda _self: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Discard)
    data_dir = tmp_path / "isolated-data"
    monkeypatch.setattr(plant_library, "_default_app_data_dir", lambda: data_dir)
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    tab = window.acquisition_inventory_view
    assert window._tab_widget.indexOf(tab) == window._tab_widget.indexOf(window.seed_inventory_view) + 1
    assert tab._view.table.rowCount() == 0
    AcquisitionInventory(data_dir / "acquisitions.json").put({"name": "Synthetic", "kind": "Tool"})
    window._on_acquisitions()
    assert window._tab_widget.currentWidget() is tab
    assert tab._view.table.item(0, 0).text() == "Synthetic"
    from PyQt6.QtCore import Qt
    tab._view.table.setFocus()
    qtbot.keyClick(tab._view.table, Qt.Key.Key_Escape)
    assert tab._view.isVisible()
    window._tab_widget.setCurrentWidget(window.seed_inventory_view)
    window._on_acquisitions()
    assert tab._view.isVisible() and tab._view.table.item(0, 0).text() == "Synthetic"
    window.close()
