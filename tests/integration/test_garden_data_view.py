"""Garden records live in a page; weather lives in a dedicated tab."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QPushButton

from open_garden_planner.core.object_types import ObjectType
from open_garden_planner.ui.canvas.items import CircleItem
from open_garden_planner.ui.widgets.weather_widget import WeatherWidget
from tests.integration.test_icon_system import _make_app


def test_properties_open_records_page_on_selected_plant(qtbot, monkeypatch, tmp_path):
    win = _make_app(qtbot, monkeypatch)
    plant = CircleItem(200, 200, 30, object_type=ObjectType.PERENNIAL)
    plant.name = 'Plantation fictive'
    win.canvas_scene.addItem(plant)
    plant.setSelected(True)
    win._update_properties_panel()
    win.show()
    page = win.garden_data_view
    assert not win.calendar_view.findChildren(WeatherWidget)
    assert win._tab_widget.indexOf(win.weather_view) >= 0
    assert not page.findChildren(WeatherWidget)
    button = next(b for b in win.properties_panel.findChildren(QPushButton)
                  if b.text() == 'Garden records…')
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert win._tab_widget.currentWidget() is page
    assert QApplication.activeModalWidget() is None
    assert page.target_id == str(plant.item_id)
    assert page.name_edit.text() == 'Plantation fictive'
    page.name_edit.setText('Alliums fictifs')
    qtbot.mouseClick(page.save_card_button, Qt.MouseButton.LeftButton)
    assert plant.name == 'Alliums fictifs'
    win.canvas_view.command_manager.undo()
    qtbot.waitUntil(lambda: page.name_edit.text() == 'Plantation fictive')
    win.canvas_view.command_manager.redo()
    qtbot.waitUntil(lambda: page.name_edit.text() == 'Alliums fictifs')
    path = tmp_path / 'garden.ogp'
    win._project_manager.save(win.canvas_scene, path)
    win._project_manager.load(win.canvas_scene, path)
    page.refresh()
    page.select_target(str(plant.item_id))
    assert page.name_edit.text() == 'Alliums fictifs'
    win._on_garden_records()
    assert page.target_id is None
    assert win._tab_widget.currentWidget() is page
    win.close()


def test_records_page_reloads_when_activated_and_clears_deleted_target(qtbot, monkeypatch):
    win = _make_app(qtbot, monkeypatch)
    page = win.garden_data_view
    plant = CircleItem(200, 200, 30, object_type=ObjectType.PERENNIAL)
    plant.name = 'Fictif'
    win.canvas_scene.addItem(plant)
    win.show()
    index = win._tab_widget.indexOf(page)
    qtbot.mouseClick(win._tab_widget.tabBar(), Qt.MouseButton.LeftButton,
                     pos=win._tab_widget.tabBar().tabRect(index).center())
    assert any(page.targets.item(i).data(Qt.ItemDataRole.UserRole) == str(plant.item_id)
               for i in range(page.targets.count()))
    page.select_target(str(plant.item_id))
    win.canvas_scene.clear()
    assert page.target_id is None
    qtbot.waitUntil(lambda: page.targets.count() == 1)
    assert not page.save_card_button.isVisible()
    win.close()


def test_unsaved_card_survives_tab_navigation_and_unrelated_intervention(qtbot, monkeypatch):
    win = _make_app(qtbot, monkeypatch)
    page = win.garden_data_view
    plant = CircleItem(200, 200, 30, object_type=ObjectType.PERENNIAL)
    plant.name = 'Fictif'
    win.canvas_scene.addItem(plant)
    win.show()
    win._on_garden_records(str(plant.item_id))
    page.name_edit.setText('Nom en cours')
    page.notes_edit.setPlainText('Observation en cours')
    page.period_edit.setText('Automne 2023')
    win._tab_widget.setCurrentIndex(0)
    win._tab_widget.setCurrentWidget(page)
    assert page.name_edit.text() == 'Nom en cours'
    assert page.notes_edit.toPlainText() == 'Observation en cours'
    page.service.add_event({'type': 'observation', 'status': 'done', 'description': 'Exemple',
                            'targets': [{'id': str(plant.item_id), 'name': plant.name}]}, 'draft-test')
    qtbot.waitUntil(lambda: page.events.count() == 1)
    assert page.notes_edit.toPlainText() == 'Observation en cours'
    assert page.period_edit.text() == 'Automne 2023'
    assert plant.name == 'Fictif'
    page.service.set_card(str(plant.item_id), {'name': 'Nom corrigé depuis Propriétés'})
    qtbot.waitUntil(lambda: page.name_edit.text() == 'Nom corrigé depuis Propriétés')
    assert page.notes_edit.toPlainText() == 'Observation en cours'
    assert page.period_edit.text() == 'Automne 2023'
    other = CircleItem(300, 300, 30, object_type=ObjectType.PERENNIAL)
    other.name = 'Autre plantation'
    win.canvas_scene.addItem(other)
    page.select_target(str(other.item_id))
    assert page.name_edit.text() == 'Autre plantation'
    assert page.notes_edit.toPlainText() == ''
    assert page.period_edit.text() == ''
    win.close()
