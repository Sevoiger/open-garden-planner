"""The French plant action row fits a narrow, actually resized sidebar."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QBoxLayout, QStyle, QStyleOptionButton

from open_garden_planner.core.i18n import load_translator
from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.ui.theme import ThemeMode, apply_theme
from tests.ui.test_plant_database_panel import _plant


def test_selected_plant_sidebar_reduces_and_actions_stay_readable(qtbot, qapp, monkeypatch):
    from open_garden_planner.app.application import GardenPlannerApp

    monkeypatch.setattr(GardenPlannerApp, '_startup_sequence', lambda _self: None)
    assert load_translator(qapp, 'fr')
    apply_theme(qapp, ThemeMode.LIGHT)
    try:
        window = GardenPlannerApp()
        qtbot.addWidget(window)
        window.show()
        item = _plant(PlantSpeciesData(scientific_name='Magnolia grandiflora', common_name='Magnolia'))
        item.layer_id = window.canvas_scene.active_layer.id
        window.canvas_scene.addItem(item)
        item.setSelected(True)
        qtbot.wait(200)
        window._main_splitter.setSizes([1100, 230])
        qapp.processEvents()
        assert window.sidebar.width() <= 240
        panel = window.plant_database_panel
        buttons = (panel.search_button, panel.load_custom_button, panel.create_custom_button)
        assert [b.text() for b in buttons] == ['Rechercher', 'Charger', 'Créer']
        assert panel._action_buttons_layout.direction() == QBoxLayout.Direction.TopToBottom
        for button in buttons:
            option = QStyleOptionButton()
            option.initFrom(button)
            contents = button.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, button)
            assert contents.width() >= button.fontMetrics().horizontalAdvance(button.text())
        outer = window._sidebar_controller.panel('plant_details')._body_scroll
        assert outer.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
        assert panel.width() < 240
        window._project_manager.mark_clean()
        window.close()
    finally:
        load_translator(qapp, 'en')
        apply_theme(qapp, ThemeMode.DARK)
