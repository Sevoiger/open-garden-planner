"""Regression: native theme surfaces and crowded status instruments on macOS."""

import sys

import pytest
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

from open_garden_planner.app.application import GardenPlannerApp
from open_garden_planner.ui.theme import ThemeMode, apply_theme


@pytest.fixture
def window(qtbot, monkeypatch):
    for method in ("_startup_sequence", "_start_update_check", "_maybe_start_agent_api"):
        monkeypatch.setattr(GardenPlannerApp, method, lambda _self: None)
    app = QApplication.instance()
    old_font = app.font()
    old_style = app.style().objectName()
    old_sheet = app.styleSheet()
    app.setFont(QFont(old_font.family(), 13))
    apply_theme(app, ThemeMode.LIGHT)
    win = GardenPlannerApp()
    qtbot.addWidget(win)
    win.showNormal()
    yield win
    win._project_manager.mark_clean()
    win.close()
    app.setStyleSheet("")
    app.setStyle(old_style)
    app.setFont(old_font)
    app.setStyleSheet(old_sheet)


@pytest.mark.parametrize("width", [800, 1200, 1800])
def test_status_fields_fit_and_restore_with_window_width(window, qtbot, width):
    window.resize(width, 750)
    qtbot.wait(30)
    bar = window.statusBar()
    bar.fit_segments()
    qtbot.wait(10)
    fields = [window.coord_label, window.coordinate_input_field, window.zoom_label]
    fields += [label for _, label in bar._segments if label.isVisible()]
    for field in fields:
        assert field.isVisible()
        assert field.geometry().left() >= 0
        assert field.geometry().right() <= bar.width()
    for left, right in zip(fields, fields[1:], strict=False):
        assert left.geometry().right() < right.geometry().left()
    if width == 800:
        assert not window.location_label.isVisible()
    if width == 1800:
        assert window.location_label.isVisible()
        assert window.season_label.isVisible()


@pytest.mark.skipif(sys.platform != "darwin", reason="Aqua/QSS conflict is macOS-specific")
def test_theme_replaces_aqua_and_retains_readable_controls(window):
    app = QApplication.instance()
    app.setStyleSheet("")
    app.setStyle("macOS")
    apply_theme(app, ThemeMode.LIGHT)
    app.setStyleSheet("")  # Inspect the base style beneath Qt's QSS proxy.
    assert app.style().objectName().lower() == "fusion"
    apply_theme(app, ThemeMode.DARK)
    app.setStyleSheet("")
    assert app.style().objectName().lower() == "fusion"


def test_hidden_status_segment_reappears_when_text_shortens(qtbot):
    from PyQt6.QtWidgets import QLabel

    from open_garden_planner.ui.widgets.garden_status_bar import GardenStatusBar, StatusLabel

    bar = GardenStatusBar()
    qtbot.addWidget(bar)
    core = QLabel("Permanent input")
    core.setFixedWidth(500)
    bar.addPermanentWidget(core)
    icon = QLabel("+")
    detail = StatusLabel("Selection measurement " * 30)
    bar.addPermanentWidget(icon)
    bar.addPermanentWidget(detail)
    bar.add_optional_segment(icon, detail)
    bar.resize(800, 40)
    bar.show()
    qtbot.waitUntil(lambda: detail.isHidden())
    # No resize, no explicit fit call: a hidden label must notify its owner.
    detail.setText("No selection")
    qtbot.waitUntil(lambda: detail.isVisible())
    assert icon.isVisible()
    assert detail.geometry().right() <= bar.width()
