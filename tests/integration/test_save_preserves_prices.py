"""Save failures and undo must not discard shopping-list prices."""

import pytest
from PyQt6.QtWidgets import QMessageBox

from open_garden_planner.agent_api.exports import save_plan_file
from open_garden_planner.app.application import GardenPlannerApp
from open_garden_planner.core.commands import DeleteItemsCommand
from open_garden_planner.core.object_types import ObjectType
from open_garden_planner.core.project import ProjectManager
from open_garden_planner.models.shopping_list import ShoppingListCategory
from open_garden_planner.services.shopping_list_service import ShoppingListService
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.canvas.items import CircleItem


@pytest.fixture
def garden(qtbot, monkeypatch):
    monkeypatch.setattr(GardenPlannerApp, "_startup_sequence", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_start_update_check", lambda _self: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Discard)
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    plant = CircleItem(100, 100, 30, object_type=ObjectType.TREE, name="Pommier")
    plant.metadata["plant_species"] = {"scientific_name": "Malus domestica", "common_name": "Pommier"}
    window.canvas_scene.addItem(plant)
    shopping = ShoppingListService(window.canvas_scene, window._soil_service, window._project_manager)
    item = next(item for item in shopping.build() if item.category is ShoppingListCategory.PLANTS)
    shopping.update_price(item, 12.5)
    window.canvas_view.command_manager.execute(DeleteItemsCommand(window.canvas_scene, [plant]))
    yield window, plant, item.id, shopping
    window.close()


@pytest.mark.parametrize("source", ["gui", "mcp"])
def test_save_then_undo_restores_price_and_roundtrips(garden, tmp_path, source):
    window, plant, item_id, shopping = garden
    manager = window._project_manager
    path = tmp_path / f"{source}.ogp"
    if source == "gui":
        window._save_to_file(path)
    else:
        save_plan_file(window.canvas_scene, manager, window._soil_service, str(path))
    assert manager.shopping_list_prices[item_id] == 12.5
    window.canvas_view.command_manager.undo()
    assert plant.scene() is window.canvas_scene
    restored = next(item for item in shopping.build() if item.id == item_id)
    assert restored.price_each == 12.5
    manager.save(window.canvas_scene, path)
    manager.release_document_ownership()
    reopened, scene = ProjectManager(), CanvasScene()
    reopened.load(scene, path)
    assert reopened.shopping_list_prices[item_id] == 12.5
    reopened.release_document_ownership()


@pytest.mark.parametrize("source", ["gui", "mcp"])
def test_failed_save_leaves_prices_in_memory(garden, monkeypatch, tmp_path, source):
    window, _plant, item_id, _shopping = garden
    manager = window._project_manager
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda _parent, title, message: errors.append((title, message)))

    def fail_save(*_args):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(manager, "save", fail_save)
    path = tmp_path / "garden.ogp"
    if source == "gui":
        window._save_to_file(path)
        assert errors
    else:
        with pytest.raises(OSError, match="synthetic disk failure"):
            save_plan_file(window.canvas_scene, manager, window._soil_service, str(path))
    assert manager.shopping_list_prices[item_id] == 12.5
    assert manager.is_dirty


def test_failed_save_cancels_close_and_preserves_prices(garden, monkeypatch, tmp_path):
    window, _plant, item_id, _shopping = garden
    manager = window._project_manager
    monkeypatch.setattr(QMessageBox, "critical", lambda *_args: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Save)
    monkeypatch.setattr(window, "_on_save", lambda: window._save_to_file(tmp_path / "garden.ogp"))

    def fail_save(*_args):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(manager, "save", fail_save)
    assert not window.close()
    assert manager.is_dirty
    assert manager.shopping_list_prices[item_id] == 12.5
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Discard)


def test_unknown_plants_have_distinct_prices_and_explicit_cleanup_is_undoable(garden, qtbot, monkeypatch, tmp_path):
    from open_garden_planner.ui.dialogs.shopping_list_dialog import ShoppingListDialog

    window, _plant, _item_id, shopping = garden
    manager = window._project_manager
    first = CircleItem(100, 100, 20, object_type=ObjectType.TREE, name="Arbre inconnu")
    window.canvas_scene.addItem(first)
    first_row = next(item for item in shopping.build() if item.id == f"plant:_unknown:{first.item_id}")
    shopping.update_price(first_row, 200)
    window.canvas_view.command_manager.execute(DeleteItemsCommand(window.canvas_scene, [first]))
    second = CircleItem(200, 100, 20, object_type=ObjectType.SHRUB, name="Arbuste inconnu")
    window.canvas_scene.addItem(second)
    second_row = next(item for item in shopping.build() if item.id == f"plant:_unknown:{second.item_id}")
    assert second_row.price_each is None
    manager.set_shopping_list_prices({**manager.shopping_list_prices, "plant:_unknown": 33})
    manager.set_excluded_shopping_items({"plant:_unknown"})
    path = tmp_path / "prices.ogp"
    manager.save(window.canvas_scene, path)
    assert manager.shopping_list_prices[first_row.id] == 200
    assert manager.shopping_list_prices["plant:_unknown"] == 33
    dialog = ShoppingListDialog(shopping)
    qtbot.addWidget(dialog)
    assert "33.00" in dialog._legacy_unknown_label.text()
    assert "previously marked as owned" in dialog._legacy_unknown_label.text()
    assert not manager.is_shopping_item_excluded(second_row.id)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.No)
    dialog._remove_unused_button.click()
    assert manager.shopping_list_prices[first_row.id] == 200
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_kw: QMessageBox.StandardButton.Yes)
    dialog._remove_unused_button.click()
    assert first_row.id not in manager.shopping_list_prices
    assert "plant:_unknown" not in manager.shopping_list_prices
    window.canvas_view.command_manager.undo()
    assert manager.shopping_list_prices[first_row.id] == 200
    assert manager.shopping_list_prices["plant:_unknown"] == 33
    assert "33.00" in dialog._legacy_unknown_label.text()
    assert dialog._remove_unused_button.isEnabled()
    dialog._clear_legacy_owned_button.click()
    assert not manager.is_shopping_item_excluded("plant:_unknown")
    assert not manager.is_shopping_item_excluded(second_row.id)
    window.canvas_view.command_manager.undo()
    assert manager.is_shopping_item_excluded("plant:_unknown")
    assert "previously marked as owned" in dialog._legacy_unknown_label.text()
