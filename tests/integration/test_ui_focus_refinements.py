"""Observed category clutter, invisible active filters and translated button clipping."""

import gc

import pytest
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import QMessageBox, QPushButton, QStyle, QStyleOptionButton, QWidget

from open_garden_planner.core.i18n import load_translator
from open_garden_planner.ui.theme import ThemeMode, apply_theme, current_colors
from open_garden_planner.ui.widgets.category_dropdown import CategoryDropdown
from open_garden_planner.ui.widgets.gallery_data import build_toolbar_categories


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
def test_checked_plant_filter_has_a_visible_fill_inside_category_popup(qtbot, qapp, mode):
    apply_theme(qapp, mode)
    dropdown = CategoryDropdown(next(c for c in build_toolbar_categories() if c.icon_name == "tree"))
    qtbot.addWidget(dropdown)
    anchor = QWidget()
    qtbot.addWidget(anchor)
    anchor.show()
    dropdown.show_below(anchor)
    chip = dropdown.trait_filters.layer_buttons["canopy"]
    chip.click()
    qapp.processEvents()
    image = chip.grab().toImage()
    ratio = image.devicePixelRatio()
    assert image.pixelColor(int(6 * ratio), int(chip.height() / 2 * ratio)) == QColor(current_colors()["accent"])


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
@pytest.mark.parametrize("font_size", [13, 18])
def test_french_standard_message_buttons_fit_their_text(qtbot, qapp, mode, font_size):
    old_font = qapp.font()
    try:
        qapp.setFont(QFont("Helvetica", font_size))
        apply_theme(qapp, mode)
        assert load_translator(qapp, "fr")
        gc.collect()
        message = QMessageBox(QMessageBox.Icon.Warning, "Modifications non enregistrées",
                              "Enregistrer les modifications du plan fictif ?",
                              QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                              QMessageBox.StandardButton.Cancel)
        qtbot.addWidget(message)
        message.button(QMessageBox.StandardButton.Discard).setText("Ne pas enregistrer")
        message.show()
        qapp.processEvents()
        for button in message.findChildren(QPushButton):
            option = QStyleOptionButton()
            option.initFrom(button)
            contents = button.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, button)
            assert contents.width() >= button.fontMetrics().horizontalAdvance(button.text()), button.text()
            assert contents.height() >= button.fontMetrics().height(), button.text()
    finally:
        load_translator(qapp, "en")
        qapp.setFont(old_font)
        apply_theme(qapp, ThemeMode.DARK)


@pytest.mark.parametrize("font_size", [13, 18])
def test_french_palettes_share_one_row_after_restoring_two_rows(qtbot, qapp, monkeypatch, font_size):
    from open_garden_planner.app.application import GardenPlannerApp

    old_font = qapp.font()
    try:
        qapp.setFont(QFont("Helvetica", font_size))
        assert load_translator(qapp, "fr")
        apply_theme(qapp, ThemeMode.DARK)
        monkeypatch.setattr(GardenPlannerApp, '_startup_sequence', lambda *_: None)
        window = GardenPlannerApp()
        qtbot.addWidget(window)
        window.resize(800, 700)
        window.showNormal()
        # Reproduce the rejected two-row layout persisted by the previous patch.
        window.insertToolBarBreak(window.category_toolbar)
        window._save_ui_state()
        window._restore_ui_state()
        qapp.processEvents()
        toolbar = window.category_toolbar
        assert not window.toolBarBreak(toolbar)
        assert toolbar.y() == window.main_toolbar.y() == window.constraint_toolbar.y()
        assert [b.text() for b in toolbar._category_buttons] == ["Formes", "Plantes", "Aménagements"]
        for widget in toolbar._category_buttons:
            assert widget.isVisible()
            assert toolbar.rect().contains(widget.geometry()), (widget, widget.geometry(), toolbar.rect())
        window.close()
    finally:
        load_translator(qapp, "en")
        qapp.setFont(old_font)
        apply_theme(qapp, ThemeMode.DARK)
