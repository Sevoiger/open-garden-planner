"""The garden data tab hosts the shared weather without opening a dialog."""
from unittest.mock import patch

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from tests.integration.test_icon_system import _make_app


def test_garden_data_tab_uses_shared_forecast_and_current_location(qtbot, monkeypatch):
    win = _make_app(qtbot, monkeypatch)
    page = win.garden_data_view
    assert page.weather is win.calendar_view._weather
    assert page.weather.parent() is page.widget()
    assert not win.calendar_view.isAncestorOf(page.weather)
    index = win._tab_widget.indexOf(page)
    assert win._tab_widget.tabText(index) == 'Garden Data'
    win.show()
    with patch.object(page.weather, 'refresh') as refresh:
        win._project_manager.set_location({'latitude': 48.8, 'longitude': 2.3})
        refresh.reset_mock()
        qtbot.mouseClick(win._tab_widget.tabBar(), Qt.MouseButton.LeftButton,
                         pos=win._tab_widget.tabBar().tabRect(index).center())
        assert win._tab_widget.currentWidget() is page
        refresh.assert_called_once()
        assert (page.weather._lat, page.weather._lon) == (48.8, 2.3)
        assert QApplication.activeModalWidget() is None
        win._project_manager.set_location(None)
        assert not page.weather._hourly_btn.isEnabled()
        assert page.weather._empty_lbl.isVisible()
    win.close()
