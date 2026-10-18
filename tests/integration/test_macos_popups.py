"""Owner-reported popup overflow, hover activation and squeezed welcome actions."""

import pytest
from PyQt6.QtCore import QEvent, QPoint, QRect, QSize, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QPushButton,
    QScrollArea,
    QStyle,
    QStyleOptionButton,
    QWidget,
)

from open_garden_planner.ui.dialogs.welcome_dialog import WelcomeDialog
from open_garden_planner.ui.theme import ThemeMode, apply_theme
from open_garden_planner.ui.widgets.category_dropdown import CategoryDropdown
from open_garden_planner.ui.widgets.gallery_data import build_toolbar_categories
from open_garden_planner.ui.widgets.global_search import GlobalSearchField
from open_garden_planner.ui.widgets.popup_geometry import popup_rect


@pytest.fixture
def themed_app():
    app = QApplication.instance()
    font = QFont(app.font())
    app.setFont(QFont("Helvetica", 13))
    yield app
    app.setFont(font)
    apply_theme(app, ThemeMode.LIGHT)


@pytest.mark.parametrize("origin", [QPoint(0, 0), QPoint(-1440, 0), QPoint(1920, -900)])
def test_popup_placement_uses_the_anchor_screen_including_negative_origins(origin):
    screen = QRect(origin, QSize(800, 600))
    anchor = QRect(screen.right() - 20, screen.bottom() - 30, 36, 36)
    for size in (QSize(300, 400), QSize(1200, 900)):
        result = popup_rect(anchor, screen, size)
        assert screen.contains(result)
    anchor.moveTopLeft(origin + QPoint(20, 20))
    result = popup_rect(anchor, screen, QSize(300, 200))
    assert result.top() == anchor.bottom() + 1


@pytest.mark.parametrize("mode", list(ThemeMode))
def test_every_category_stays_on_screen_and_last_item_is_reachable(qtbot, themed_app, mode):
    apply_theme(themed_app, mode)
    anchor = QWidget()
    qtbot.addWidget(anchor)
    screen = themed_app.primaryScreen().availableGeometry()
    anchor.resize(36, 36)
    anchor.move(screen.right() - 45, screen.bottom() - 50)
    anchor.show()
    themed_app.processEvents()
    for category in build_toolbar_categories():
        dropdown = CategoryDropdown(category)
        qtbot.addWidget(dropdown)
        dropdown.show_below(anchor)
        themed_app.processEvents()
        assert screen.contains(dropdown.frameGeometry()), (category.name, dropdown.frameGeometry())
        last = dropdown._buttons[-1]
        dropdown._scroll.ensureWidgetVisible(last)
        themed_app.processEvents()
        point = last.mapTo(dropdown._scroll.viewport(), last.rect().center())
        assert dropdown._scroll.viewport().rect().contains(point), category.name
        received = []
        dropdown.item_selected.connect(received.append)
        last.click()
        assert received == [last.item] and not dropdown.isVisible()


@pytest.mark.parametrize("mode", list(ThemeMode))
def test_search_popup_remains_inside_screen_and_field_keeps_focus(qtbot, themed_app, mode):
    apply_theme(themed_app, mode)
    field = GlobalSearchField(build_toolbar_categories())
    qtbot.addWidget(field)
    screen = themed_app.primaryScreen().availableGeometry()
    field.move(screen.right() - field.width() - 5, screen.bottom() - 50)
    field.show()
    field.setFocus()
    field.setText("tree")
    themed_app.processEvents()
    assert field._popup.isVisible()
    assert screen.contains(field._popup.frameGeometry())
    assert field._popup._list.count() > 0
    assert QApplication.focusWidget() is field
    field._popup.hide()


@pytest.mark.parametrize("mode", list(ThemeMode))
def test_welcome_action_contents_fit_real_font_at_small_dialog(qtbot, themed_app, mode):
    apply_theme(themed_app, mode)
    dialog = WelcomeDialog()
    qtbot.addWidget(dialog)
    dialog.resize(600, 500)
    dialog.show()
    themed_app.processEvents()
    for button in dialog.findChildren(QPushButton):
        option = QStyleOptionButton()
        option.initFrom(button)
        contents = button.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, button)
        assert contents.height() >= button.fontMetrics().height(), button.text()
        assert contents.width() >= button.fontMetrics().horizontalAdvance(button.text()), button.text()
        if button.property("buttonRole"):
            scroll = dialog.findChild(QScrollArea)
            scroll.ensureWidgetVisible(button)
            themed_app.processEvents()
            button_rect = QRect(button.mapTo(scroll.viewport(), QPoint()), button.size())
            assert scroll.viewport().rect().contains(button_rect), button.text()


def test_application_sidebar_opens_by_click_without_hover_or_reordering(qtbot, monkeypatch):
    from open_garden_planner.app.application import GardenPlannerApp
    from open_garden_planner.ui.widgets.panel_stack import PanelState

    monkeypatch.setattr(GardenPlannerApp, "_startup_sequence", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_start_update_check", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_maybe_start_agent_api", lambda _self: None)
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    controller = window._sidebar_controller
    header = controller.panel("layers").header
    order = controller.panels()
    # Dispatch locally to this offscreen widget; never move the system cursor.
    QApplication.sendEvent(header, QEvent(QEvent.Type.Enter))
    qtbot.wait(200)
    assert controller.state_of("layers") is PanelState.COLLAPSED
    qtbot.mouseClick(header, Qt.MouseButton.LeftButton)
    assert controller.state_of("layers") is PanelState.PINNED
    QApplication.sendEvent(header, QEvent(QEvent.Type.Leave))
    qtbot.wait(250)
    assert controller.state_of("layers") is PanelState.PINNED
    qtbot.mouseClick(header, Qt.MouseButton.LeftButton)
    assert controller.state_of("layers") is PanelState.COLLAPSED
    assert controller.panels() == order
    window._project_manager.mark_clean()
    window.close()


@pytest.mark.parametrize("mode", list(ThemeMode))
def test_category_filter_empty_state_clear_and_reopening(qtbot, themed_app, mode):
    apply_theme(themed_app, mode)
    anchor = QWidget()
    qtbot.addWidget(anchor)
    anchor.show()
    trees = next(c for c in build_toolbar_categories() if c.icon_name == "tree")
    dropdown = CategoryDropdown(trees)
    qtbot.addWidget(dropdown)
    dropdown.show_below(anchor)
    dropdown._search_box.setText("apple")
    assert [b.item.name for b in dropdown._buttons if not b.isHidden()] == ["Apple Tree"]
    dropdown._search_box.setText("zzzz-no-such-object")
    assert all(b.isHidden() for b in dropdown._buttons)
    assert dropdown._empty_label.isVisible()
    dropdown._clear_filters_button.click()
    assert all(not b.isHidden() for b in dropdown._buttons)
    assert not dropdown._empty_label.isVisible()
    dropdown.trait_filters.layer_buttons["ground_cover"].click()
    assert dropdown.trait_filters.layers == {"ground_cover"}
    dropdown.close()
    # Changing the theme while the same category instance exists must not
    # preserve a forgotten filter or lose the original object thumbnails.
    apply_theme(themed_app, ThemeMode.DARK if mode is ThemeMode.LIGHT else ThemeMode.LIGHT)
    dropdown.show_below(anchor)
    themed_app.processEvents()
    assert not dropdown.trait_filters.layers and not dropdown.trait_filters.functions
    assert all(b.isVisible() and not b._thumb_label.pixmap().isNull() for b in dropdown._buttons)
    assert anchor.screen().availableGeometry().contains(dropdown.frameGeometry())
    dropdown.close()
