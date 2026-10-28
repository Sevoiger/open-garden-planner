"""Integration tests for the weather forecast widget (US-12.1).

Tests cover:
- Empty state when no location is set
- Rendering a forecast into the 7-day strip
- Expandable table show/hide
- Offline cache age display
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from open_garden_planner.services.weather_service import DayForecast, WeatherForecast
from open_garden_planner.ui.widgets.weather_widget import WeatherWidget


@pytest.mark.parametrize('language,conditions,weekday,month', [
    ('fr', ['Ciel dégagé', 'Partiellement nuageux', 'Brumeux', 'Pluvieux', 'Neigeux',
            'Averses', 'Averses de neige', 'Orage', 'Inconnu'], 'sam.', 'févr.'),
    ('de', ['Klarer Himmel', 'Teilweise bewölkt', 'Neblig', 'Regnerisch', 'Schneefall',
            'Schauer', 'Schneeschauer', 'Gewitter', 'Unbekannt'], 'Sa.', 'Feb.'),
])
def test_forecast_uses_app_language_despite_english_system(qtbot, qapp, language, conditions, weekday, month):
    from pathlib import Path

    from PyQt6.QtCore import QLocale, QTranslator
    from PyQt6.QtGui import QColor, QPixmap
    from PyQt6.QtWidgets import QDateEdit, QLabel

    from open_garden_planner.app.settings import get_settings
    from open_garden_planner.ui.dialogs.calibration_dialog import CalibrationDialog
    from open_garden_planner.ui.views.planting_calendar_view import (
        _DashboardPanel,
        _DashboardTask,
        _DetailPanel,
        _month_abbr,
    )

    translator = QTranslator()
    catalogs = Path(__file__).resolve().parents[2] / 'src/open_garden_planner/resources/translations'
    assert translator.load(str(catalogs / f'open_garden_planner_{language}.qm'))
    previous_locale = QLocale()
    QLocale.setDefault(QLocale('en_US'))
    qapp.installTranslator(translator)
    get_settings().language = language
    try:
        widget = WeatherWidget()
        qtbot.addWidget(widget)
        forecast = _make_forecast(9)
        for day, code in zip(forecast.days, (0, 2, 45, 61, 71, 80, 85, 95, 999), strict=True):
            day.weathercode = code
        widget._on_fetch_success(forecast)
        assert [widget._table.item(row, 1).text() for row in range(9)] == conditions
        assert widget._day_cells[0]._day_lbl.text() == weekday
        assert widget._day_cells[0].toolTip() == conditions[0]
        assert _month_abbr(2) == month
        dashboard = _DashboardPanel()
        qtbot.addWidget(dashboard)
        date = datetime(2024, 2, 21).date()
        task = _DashboardTask('task', 'harvest', 'Plant', date, date, 'today', 'plant')
        row = dashboard._make_task_row(task, {}, QColor('green'))
        qtbot.addWidget(row)
        assert f'21 {month}' in [label.text() for label in row.findChildren(QLabel)]
        detail = _DetailPanel()
        qtbot.addWidget(detail)
        assert all(field.locale().name().startswith(language) for field in detail.findChildren(QDateEdit))
        calibration = CalibrationDialog(QPixmap(20, 20))
        qtbot.addWidget(calibration)
        assert 'e.g.' not in calibration._distance_input.placeholderText()
    finally:
        qapp.removeTranslator(translator)
        QLocale.setDefault(previous_locale)


def _make_forecast(days: int = 16) -> WeatherForecast:
    """Create a dummy forecast with the requested number of days."""
    base_date = datetime(2024, 6, 1, tzinfo=UTC)
    forecast_days = []
    for i in range(days):
        d = base_date + timedelta(days=i)
        forecast_days.append(
            DayForecast(
                date=d.strftime("%Y-%m-%d"),
                max_c=20.0 + (i % 5),
                min_c=10.0 + (i % 3),
                precipitation_mm=float(i % 3),
                weathercode=i % 100,
            )
        )
    return WeatherForecast(
        days=forecast_days,
        fetched_at=datetime.now(UTC).isoformat(),
    )


class TestWeatherWidgetEmptyState:
    def test_no_location_shows_empty_label(self, qtbot: object) -> None:
        widget = WeatherWidget()
        qtbot.addWidget(widget)
        widget.show()

        widget.set_location(None, None)

        # Empty label should be visible; strip/table hidden
        assert widget._empty_lbl.isVisible()
        assert not widget._strip.isVisible()
        assert not widget._table.isVisible()

    def test_empty_label_text(self, qtbot: object) -> None:
        widget = WeatherWidget()
        qtbot.addWidget(widget)
        widget.show()

        widget.set_location(None, None)

        assert "Set a location" in widget._empty_lbl.text()


class TestWeatherWidgetForecast:
    def test_set_location_then_inject_forecast_renders_strip(self, qtbot: object) -> None:
        widget = WeatherWidget()
        qtbot.addWidget(widget)
        widget.show()

        widget.set_location(52.5, 13.4)
        forecast = _make_forecast(days=7)
        widget._on_fetch_success(forecast)

        # Strip should be visible with 7 cells
        assert widget._strip.isVisible()
        assert widget._day_cells[0].isVisible()
        assert widget._day_cells[6].isVisible()

    def test_day_cell_shows_correct_data(self, qtbot: object) -> None:
        widget = WeatherWidget()
        qtbot.addWidget(widget)
        widget.show()

        widget.set_location(52.5, 13.4)
        forecast = _make_forecast(days=1)
        widget._on_fetch_success(forecast)

        cell = widget._day_cells[0]
        day_text = cell._day_lbl.text()
        # 2024-06-01 is a Saturday
        assert "Sat" in day_text
        assert cell._temp_lbl.text() == "20° / 10°"

    def test_expandable_table_hidden_by_default(self, qtbot: object) -> None:
        widget = WeatherWidget()
        qtbot.addWidget(widget)
        widget.show()

        widget.set_location(52.5, 13.4)
        forecast = _make_forecast(days=16)
        widget._on_fetch_success(forecast)

        assert not widget._table.isVisible()
        assert not widget._expanded
        assert not widget._expand_btn.icon().isNull()  # themed chevron, not a "▾" glyph (#310)

    def test_expand_button_shows_table(self, qtbot: object) -> None:
        widget = WeatherWidget()
        qtbot.addWidget(widget)
        widget.show()

        widget.set_location(52.5, 13.4)
        forecast = _make_forecast(days=16)
        widget._on_fetch_success(forecast)

        # Simulate expand button click
        widget._on_expand_clicked()

        assert widget._table.isVisible()
        assert widget._expanded
        assert not widget._expand_btn.icon().isNull()
        assert widget._table.rowCount() == 16
        # condition column carries a themed icon + description (no emoji)
        item = widget._table.item(0, 1)
        assert item is not None and not item.icon().isNull()
        assert not any(ord(ch) > 0x2000 for ch in item.text()), item.text()

    def test_table_contains_correct_headers(self, qtbot: object) -> None:
        widget = WeatherWidget()
        qtbot.addWidget(widget)
        widget.show()

        widget.set_location(52.5, 13.4)
        forecast = _make_forecast(days=3)
        widget._on_fetch_success(forecast)
        widget._on_expand_clicked()

        headers = [widget._table.horizontalHeaderItem(i).text() for i in range(5)]
        assert "Date" in headers
        assert "Weather" in headers
        assert "Max °C" in headers
        assert "Min °C" in headers
        assert "Rain mm" in headers


class TestWeatherWidgetOffline:
    def test_cache_age_label_shown_for_old_data(self, qtbot: object) -> None:
        widget = WeatherWidget()
        qtbot.addWidget(widget)
        widget.show()

        widget.set_location(52.5, 13.4)

        # Create a forecast fetched 2 hours ago
        old_time = datetime.now(UTC) - timedelta(hours=2)
        forecast = WeatherForecast(
            days=[DayForecast(date="2024-06-01", max_c=20.0, min_c=10.0, precipitation_mm=0.0, weathercode=0)],
            fetched_at=old_time.isoformat(),
        )
        widget._on_fetch_success(forecast)

        assert widget._age_lbl.isVisible()
        assert "2" in widget._age_lbl.text()
        assert "h ago" in widget._age_lbl.text()

    def test_cache_age_hidden_for_fresh_data(self, qtbot: object) -> None:
        widget = WeatherWidget()
        qtbot.addWidget(widget)
        widget.show()

        widget.set_location(52.5, 13.4)
        forecast = _make_forecast(days=1)
        widget._on_fetch_success(forecast)

        # Fresh data (< 1 min) should hide age label
        assert not widget._age_lbl.isVisible()


class TestWeatherWidgetFetchTrigger:
    def test_refresh_starts_worker_when_location_set(self, qtbot: object) -> None:
        widget = WeatherWidget()
        qtbot.addWidget(widget)

        with (patch.object(widget, "_show_loading"),
              patch("open_garden_planner.ui.widgets.weather_widget._WeatherFetchWorker") as MockWorker):
            instance = MagicMock()
            MockWorker.return_value = instance

            widget.set_location(52.5, 13.4)
            widget.refresh()

            MockWorker.assert_called_once_with(52.5, 13.4)
            instance.start.assert_called_once()

    def test_refresh_shows_empty_when_no_location(self, qtbot: object) -> None:
        widget = WeatherWidget()
        qtbot.addWidget(widget)
        widget.show()

        widget.set_location(None, None)
        widget.refresh()
        assert widget._empty_lbl.isVisible()
        assert not widget._strip.isVisible()


@pytest.mark.real_weather_worker
class TestWeatherWidgetLifecycle:
    def test_worker_cancel_suppresses_signals(self, monkeypatch, qtbot) -> None:  # noqa: ARG002
        import time

        from open_garden_planner.ui.widgets.weather_widget import _WeatherFetchWorker

        def slow_fetch(*_args, **_kwargs):
            time.sleep(0.05)
            return None

        import open_garden_planner.services.weather_service as ws

        service = ws.get_weather_service()
        monkeypatch.setattr(service, "fetch_forecast", slow_fetch)

        worker = _WeatherFetchWorker(52.5, 13.4)
        successes, errors = [], []
        worker.success.connect(successes.append)
        worker.error.connect(errors.append)

        worker.cancel()
        worker.start()
        worker.wait(500)

        assert successes == []
        assert errors == []

    def test_shutdown_cancels_and_cleans_up_worker(self, monkeypatch, qtbot) -> None:
        import time

        from PyQt6 import sip

        def slow_fetch(*_args, **_kwargs):
            time.sleep(0.3)
            return None

        import open_garden_planner.services.weather_service as ws
        service = ws.get_weather_service()
        monkeypatch.setattr(service, "fetch_forecast", slow_fetch)

        widget = WeatherWidget()
        widget.set_location(52.5, 13.4)
        widget.refresh()

        worker = widget._worker
        assert worker is not None
        assert worker.isRunning()

        # Shutdown widget and delete it
        widget.shutdown(timeout_ms=50)
        assert widget._worker is None
        sip.delete(widget)

        # Dropping worker stack reference and forcing GC must not abort while running
        import gc
        del worker
        gc.collect()

        time.sleep(0.3)
        gc.collect()

    def test_repeated_refresh_shuts_down_previous_worker(self, monkeypatch, qtbot) -> None:
        import time

        def slow_fetch(*_args, **_kwargs):
            time.sleep(0.3)
            return None

        import open_garden_planner.services.weather_service as ws
        service = ws.get_weather_service()
        monkeypatch.setattr(service, "fetch_forecast", slow_fetch)

        widget = WeatherWidget()
        qtbot.addWidget(widget)
        widget.set_location(52.5, 13.4)
        widget.refresh()

        first_worker = widget._worker
        assert first_worker is not None

        # Trigger refresh with different coordinates
        widget.set_location(48.8, 2.3)
        widget.refresh()

        second_worker = widget._worker
        assert second_worker is not None
        assert second_worker is not first_worker
        assert first_worker._is_cancelled

        # Dropping first_worker stack reference must not cause GC crash while running
        import gc
        del first_worker
        gc.collect()

        widget.shutdown(timeout_ms=50)
        time.sleep(0.3)
        gc.collect()




def test_hourly_is_inline_and_water_values_are_explicit(qtbot):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication, QDialog

    from open_garden_planner.services.weather_service import HourForecast

    widget = WeatherWidget()
    qtbot.addWidget(widget)
    widget.show()
    forecast = _make_forecast(2)
    forecast.days[0].et0_mm = 2.7
    forecast.hours = [HourForecast('2026-10-28T10:00', 15.3, 0, 20),
                      HourForecast('2026-10-28T11:00')]
    forecast.timezone = 'Europe/Paris'
    widget._on_fetch_success(forecast)
    assert widget._day_cells[0]._rain_lbl.text() == 'Rain 0.0 mm'
    assert widget._day_cells[0]._et0_lbl.text() == 'ET₀ 2.7 mm'
    assert widget._table.item(1, 5).text() == '—'
    qtbot.mouseClick(widget._hourly_btn, Qt.MouseButton.LeftButton)
    assert widget._hourly_panel.isVisible()
    assert widget._hourly_table.rowCount() == 2
    assert widget._hourly_table.item(0, 2).text() == '0.0'
    assert widget._hourly_table.item(1, 2).text() == '—'
    assert 'Europe/Paris' in widget._hourly_title.text()
    assert not isinstance(QApplication.activeModalWidget(), QDialog)
    widget.refresh_theme_icons()
    assert widget._hourly_panel.isVisible()
    qtbot.mouseClick(widget._hourly_btn, Qt.MouseButton.LeftButton)
    assert not widget._hourly_panel.isVisible()
    widget.set_location(None, None)
    assert not widget._hourly_btn.isEnabled()


def test_hourly_day_navigation_keeps_values_and_selection_on_theme_change(qtbot):
    from PyQt6.QtCore import Qt

    from open_garden_planner.services.weather_service import HourForecast

    widget = WeatherWidget()
    qtbot.addWidget(widget)
    widget.show()
    forecast = _make_forecast(2)
    forecast.hours = [HourForecast('2024-06-01T23:00', 12, 0.2, 30),
                      HourForecast('2024-06-02T00:00', None, None, None)]
    widget._on_fetch_success(forecast)
    qtbot.mouseClick(widget._hourly_btn, Qt.MouseButton.LeftButton)
    assert widget._hourly_day.count() == 2
    assert widget._hourly_table.rowCount() == 1
    assert widget._hourly_table.item(0, 0).text() == '23:00'
    assert widget._hourly_table.item(0, 2).text() == '0.2'
    assert widget._hourly_table.item(0, 3).text() == '30'
    widget._hourly_day.setCurrentIndex(1)
    assert widget._hourly_table.item(0, 0).text() == '00:00'
    assert widget._hourly_table.item(0, 2).text() == '—'
    widget.refresh_theme_icons()
    assert widget._hourly_day.currentData() == '2024-06-02'
    assert widget._hourly_panel.isVisible()
    qtbot.mouseClick(widget._expand_btn, Qt.MouseButton.LeftButton)
    assert widget._table.isVisible()
    assert not widget._hourly_panel.isVisible()
