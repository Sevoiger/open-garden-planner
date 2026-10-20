"""Rows can grow after an accordion animation without overlapping footer controls."""

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QLabel, QStyle, QStyleOptionFrame

from open_garden_planner.models.layer import Layer
from open_garden_planner.ui.panels.layers_panel import LayersPanel
from open_garden_planner.ui.theme import ThemeMode, apply_theme
from open_garden_planner.ui.widgets.collapsible_panel import CollapsiblePanel
from open_garden_planner.ui.widgets.panel_stack import SidebarController


def test_standalone_panel_releases_content_floor_on_header_close(qtbot):
    content = QLabel("Available amendments")
    content.setMinimumHeight(250)
    panel = CollapsiblePanel("Amendments", content, expanded=False)
    qtbot.addWidget(panel)
    panel.show()
    for height in (250, 400):
        content.setMinimumHeight(height)
        qtbot.mouseClick(panel._header, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: panel.minimumHeight() == panel.sizeHint().height())
        assert panel.minimumHeight() >= height
        qtbot.mouseClick(panel._header, Qt.MouseButton.LeftButton)
        assert not panel.is_expanded()
        assert panel.minimumHeight() == 0
        assert not content.isVisible()
        panel.resize(panel.width(), panel.sizeHint().height())
        assert panel.height() < height


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
def test_layer_growth_and_renaming_remain_contained_after_other_panel_opens(qtbot, mode):
    app = QApplication.instance()
    original_font = QFont(app.font())
    app.setFont(QFont("Helvetica", 13))
    apply_theme(app, mode)
    try:
        content = LayersPanel()
        content.set_layers([Layer(name="Layer 1")])
        panel = CollapsiblePanel("Layers", content)
        detail = QLabel("Other content")
        detail.setMinimumHeight(350)
        controller = SidebarController()
        qtbot.addWidget(controller)
        controller.add_panel("details", CollapsiblePanel("Details", detail))
        controller.add_panel("layers", panel)
        controller.resize(450, 350)
        controller.show()
        controller._on_title_click("layers")
        qtbot.waitUntil(lambda: panel.minimumHeight() == panel.sizeHint().height())
        controller._on_title_click("details")
        for count in (4, 9, 2):
            content.set_layers([Layer(name=f"Layer {i}") for i in range(count)])
            app.processEvents()
            qtbot.waitUntil(lambda: panel.minimumHeight() == panel.sizeHint().height())
            qtbot.waitUntil(lambda: content.opacity_slider.y() > content.layer_list.geometry().bottom())
            qtbot.waitUntil(lambda: content.height() >= content.minimumSizeHint().height())
            # Fixed list, opacity and buttons must no longer compete for the same pixels.
            assert content.opacity_slider.y() > content.layer_list.geometry().bottom()
            assert content.food_forest_button.y() > content.opacity_slider.geometry().bottom()
            assert content.auto_assignment.y() > content.food_forest_button.geometry().bottom()
            row = content.layer_list.itemWidget(content.layer_list.item(0))
            row._start_editing()
            app.processEvents()
            assert row.name_edit.height() >= row.name_edit.sizeHint().height()
            options = QStyleOptionFrame()
            options.initFrom(row.name_edit)
            rect = row.name_edit.style().subElementRect(QStyle.SubElement.SE_LineEditContents, options, row.name_edit)
            assert rect.height() >= row.name_edit.fontMetrics().height()
            viewport = controller._scroll.viewport()
            controller._scroll.ensureWidgetVisible(content.food_forest_button)
            app.processEvents()
            assert viewport.rect().contains(content.food_forest_button.mapTo(viewport, QPoint(5, 5)))
            row.name_edit.setText("Renommage lisible")
            row.name_edit.editingFinished.emit()
    finally:
        app.setFont(original_font)
        apply_theme(app, ThemeMode.LIGHT)
