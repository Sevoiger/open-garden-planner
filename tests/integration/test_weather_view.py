"""Dedicated page interaction and rendering, with no personal data or networking."""

from datetime import UTC, datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel

from open_garden_planner.services.weather_collection import normalize
from open_garden_planner.services.weather_workspace import WeatherWorkspace
from open_garden_planner.ui.views.weather_view import WeatherView
from tests.unit.test_weather_collection import config, raw
from tests.unit.test_weather_workspace import content


class Workspace(WeatherWorkspace):
    def __init__(self, tmp_path):
        super().__init__(tmp_path, config(), tmp_path / "no-key.env")
        self.calls = 0
        self.done = False
        self.report = {
            "attempted_at": datetime.now(UTC).isoformat(),
            "checked_at": datetime.now(UTC).isoformat(),
            "location": self.config["location"],
            "garden_context": "sand",
            "sources": {
                "meteofrance_arome_france": {
                    "model": "AROME",
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "stale": False,
                    **normalize(raw(), self.config["location"]["timezone"]),
                }
            },
        }
        self.record = {
            "date": self._today(),
            "location": self.config["location"],
            "content": content(),
        }

    def load(self):
        return self.report

    def load_bulletin(self):
        return self.record if self.done else None

    def attempted_today(self):
        return self.done

    def refresh(self):
        return self.report

    def generate(self, language="fr"):
        self.calls += 1
        self.done = True
        return self.record


def test_button_generates_once_and_renders_structured_cards(qtbot, tmp_path):
    service = Workspace(tmp_path)
    page = WeatherView(service)
    qtbot.addWidget(page)
    page.resize(1000, 800)
    page.show()
    qtbot.waitUntil(lambda: page._worker is None)
    qtbot.mouseClick(page.generate_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: service.done and page._worker is None)
    assert service.calls == 1
    assert not page.generate_button.isEnabled()
    labels = [w.text() for w in page.findChildren(QLabel)]
    assert "Sec puis plus frais." in labels
    assert "15 °C." in labels
    assert any("—" in text for text in labels)  # missing rain remains absent
    assert all(w.textFormat() == Qt.TextFormat.PlainText for w in page.findChildren(QLabel))
    page._start(True)
    assert service.calls == 1


def test_saved_bulletin_survives_error_and_external_html_is_plain(qtbot, tmp_path):
    service = Workspace(tmp_path)
    service.done = True
    service.record["content"]["summary"] = "<b>Weather</b>"
    page = WeatherView(service)
    qtbot.addWidget(page)
    assert "<b>Weather</b>" in [w.text() for w in page.findChildren(QLabel)]
    page._error("API_HTTP_401")
    assert "<b>Weather</b>" in [w.text() for w in page.findChildren(QLabel)]
    assert "API key" in page.status.text()


def test_calendar_recomputes_frost_from_cached_weather_for_changed_plants_and_plan(
    qtbot, monkeypatch
):
    from open_garden_planner.services.weather_service import DayForecast, WeatherForecast
    from tests.integration.test_icon_system import _make_app

    win = _make_app(qtbot, monkeypatch)
    point = win.weather_view.workspace.config["location"]
    win._project_manager.set_location(dict(point))
    forecast = WeatherForecast(
        [DayForecast(datetime.now().date().isoformat(), 8, -2, 0, 0)], datetime.now(UTC).isoformat()
    )
    plants = []
    monkeypatch.setattr(win.calendar_view, "_collect_plant_info", lambda: list(plants))
    win.weather_view._forecast = forecast
    win._on_local_weather_forecast(forecast)
    assert win.calendar_view.current_frost_alerts == []
    plants.append({"id": "test", "frost_protection_needed": True, "frost_tolerance": "tender"})
    win.calendar_view.refresh()
    assert win.calendar_view.current_frost_alerts[0].affected_plant_ids == ["test"]
    win._project_manager.set_location(dict(point))
    assert win.calendar_view.current_frost_alerts[0].affected_plant_ids == ["test"]
    plants.clear()
    win.calendar_view.refresh()
    assert win.calendar_view.current_frost_alerts == []
    win._project_manager.set_location({"latitude": 0, "longitude": 0})
    assert win.calendar_view._weather_forecast is None
    win.close()


def test_close_retains_running_worker_until_finished(qtbot, tmp_path):
    import threading

    service = Workspace(tmp_path)
    started = threading.Event()
    release = threading.Event()

    def refresh():
        started.set()
        release.wait(3)
        return service.report

    service.refresh = refresh
    page = WeatherView(service)
    qtbot.addWidget(page)
    page.show()
    qtbot.waitUntil(started.is_set)
    worker = page._worker
    try:
        page.close()
        assert page._worker is None and worker.isRunning()
    finally:
        release.set()
        qtbot.waitUntil(lambda: not worker.isRunning())
