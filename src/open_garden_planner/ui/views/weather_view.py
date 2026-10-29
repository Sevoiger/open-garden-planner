"""Weather page: local daily cards and a structured, on-demand AI bulletin."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from PyQt6.QtCore import QDate, QLocale, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from open_garden_planner.app.settings import active_language
from open_garden_planner.core.worker_lifecycle import retain_worker_until_finished
from open_garden_planner.services.weather_service import DayForecast, WeatherForecast
from open_garden_planner.services.weather_workspace import WeatherWorkspace
from open_garden_planner.ui.icons import get_icon
from open_garden_planner.ui.theme import set_text_role


class _WeatherJob(QThread):
    success = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, workspace: WeatherWorkspace, analysis: bool, language: str):
        super().__init__()
        self.workspace, self.analysis, self.language = workspace, analysis, language

    def run(self) -> None:
        try:
            result = (
                self.workspace.generate(self.language)
                if self.analysis
                else self.workspace.refresh()
            )
            if not self.isInterruptionRequested():
                self.success.emit(result)
        except Exception as exc:  # noqa: BLE001 — worker errors never reach Qt.
            if not self.isInterruptionRequested():
                self.error.emit(str(exc) if isinstance(exc, RuntimeError) else "WEATHER_ERROR")


class WeatherView(QWidget):
    forecast_ready = pyqtSignal(object)

    def __init__(self, workspace: WeatherWorkspace | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.workspace = workspace if workspace is not None else WeatherWorkspace()
        self._worker: _WeatherJob | None = None
        self._loaded = False
        self._forecast: WeatherForecast | None = None
        self._report: dict[str, Any] | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(28, 24, 28, 24)
        self._body.setSpacing(20)
        heading = QHBoxLayout()
        titles = QVBoxLayout()
        self.title = self._label(self.tr("Weather"), "heading")
        titles.addWidget(self.title)
        self.place = self._label(self.workspace.config["location"]["name"], "subheading")
        titles.addWidget(self.place)
        heading.addLayout(titles)
        heading.addStretch()
        self.refresh_button = QPushButton(self.tr("Update forecasts"))
        icon = get_icon("refresh")
        if icon is not None:
            self.refresh_button.setIcon(icon)
        self.refresh_button.clicked.connect(lambda: self._start(False))
        heading.addWidget(self.refresh_button)
        self._body.addLayout(heading)
        self.status = self._label(self.tr("Forecasts update at most once a day."), "caption")
        self._body.addWidget(self.status)
        self._body.addWidget(self._label(self.tr("The days ahead"), "subheading"))
        strip = QScrollArea()
        strip.setWidgetResizable(True)
        strip.setFrameShape(QFrame.Shape.NoFrame)
        strip.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        strip.setFixedHeight(190)
        days = QWidget()
        self.days_layout = QHBoxLayout(days)
        self.days_layout.setContentsMargins(0, 0, 0, 4)
        self.days_layout.setSpacing(10)
        strip.setWidget(days)
        self._body.addWidget(strip)
        ai_header = QHBoxLayout()
        ai_header.addWidget(self._label(self.tr("Your garden weather bulletin"), "heading"), 1)
        self.generate_button = QPushButton(self.tr("Generate bulletin"))
        icon = get_icon("sun")
        if icon is not None:
            self.generate_button.setIcon(icon)
        self.generate_button.clicked.connect(lambda: self._start(True))
        ai_header.addWidget(self.generate_button)
        self._body.addLayout(ai_header)
        self.bulletin_status = self._label(
            self.tr("On demand · GPT-6.1 Sol · high reasoning"), "caption"
        )
        self._body.addWidget(self.bulletin_status)
        self.bulletin = QVBoxLayout()
        self.bulletin.setSpacing(14)
        self._body.addLayout(self.bulletin)
        self._body.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll)
        self._load_cache()

    @staticmethod
    def _label(text: str, role: str | None = None) -> QLabel:
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if role:
            set_text_role(
                label, {"heading": "h1", "subheading": "h2", "caption": "hint"}.get(role, role)
            )
        return label

    @staticmethod
    def _clear(layout: Any) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                WeatherView._clear(item.layout())

    def _card(self) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setProperty("weatherCard", True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        return card, layout

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        self._load_cache()
        if not self._loaded or self.refresh_button.isEnabled():
            self._loaded = True
            QTimer.singleShot(0, lambda: self._start(False))

    def _load_cache(self) -> None:
        report = self.workspace.load()
        if report:
            self.render_forecast(report)
        record = self.workspace.load_bulletin()
        if record:
            self.render_bulletin(record)
        self._buttons()

    def _buttons(self) -> None:
        busy = self._worker is not None
        self.generate_button.setEnabled(not busy and not self.workspace.attempted_today())
        fresh = bool(
            self._report
            and datetime.fromisoformat(self._report["attempted_at"])
            .astimezone(ZoneInfo(self.workspace.config["location"]["timezone"]))
            .date()
            .isoformat()
            == self.workspace._today()
        )
        # Final throttle is enforced by the service, including DST and restarts.
        self.refresh_button.setEnabled(not busy and not fresh)

    def _start(self, analysis: bool) -> None:
        if self._worker is not None:
            return
        if analysis and self.workspace.attempted_today():
            self._load_cache()
            return
        self.status.setText(
            self.tr("Collecting maps and preparing your bulletin…")
            if analysis
            else self.tr("Updating forecasts…")
        )
        job = _WeatherJob(self.workspace, analysis, active_language())
        self._worker = job
        job.success.connect(self._success)
        job.error.connect(self._error)
        job.finished.connect(self._finished)
        self._buttons()
        job.start()

    def _success(self, result: dict[str, Any]) -> None:
        job = self._worker
        if job and job.analysis:
            self.render_bulletin(result)
            report = self.workspace.load()
            if report:
                self.render_forecast(report)
        else:
            self.render_forecast(result)

    def _error(self, code: str) -> None:
        if code == "MISSING_API_KEY" or code == "API_HTTP_401":
            message = self.tr("Check your OpenAI API key to generate a bulletin.")
        elif code == "DAILY_LIMIT":
            message = self.tr("Today’s update has already been requested. Try again tomorrow.")
        else:
            message = self.tr("Weather update unavailable. The last saved data remains displayed.")
        self.status.setText(message)

    def _finished(self) -> None:
        if self.sender() is self._worker:
            self._worker = None
            self._buttons()

    def render_forecast(self, report: dict[str, Any]) -> None:
        self._report = report
        self._clear(self.days_layout)
        rows = self.workspace.daily(report)
        for day in rows:
            card, box = self._card()
            card.setFixedWidth(155)
            date = QDate.fromString(day["date"], "yyyy-MM-dd")
            box.addWidget(
                self._label(QLocale(active_language()).toString(date, "ddd d MMM"), "caption")
            )
            temps = f"{day['temperature_min_c']:.0f}°  /  {day['temperature_max_c']:.0f}°"
            box.addWidget(self._label(temps, "heading"))
            rain = "—" if day["precipitation_mm"] is None else f"{day['precipitation_mm']:.1f} mm"
            box.addWidget(self._label(self.tr("Rain: %1").replace("%1", rain)))
            gust = day.get("max_gust_sample_kmh")
            value = "—" if gust is None else f"{gust:.0f} km/h"
            box.addWidget(self._label(self.tr("Gusts: %1").replace("%1", value)))
            foot = self.tr("Partial day") if day["partial"] else self.tr("Daily forecast")
            if day.get("ensemble"):
                foot = self.tr("Ensemble trend")
            if day["stale"]:
                foot = self.tr("Older forecast")
            box.addWidget(self._label(foot, "caption"))
            card.setToolTip(str(day["model"]))
            self.days_layout.addWidget(card)
        self.days_layout.addStretch()
        if rows and all(day["stale"] for day in rows):
            self.status.setText(self.tr("Showing saved forecasts; update unavailable."))
        elif rows:
            stamp = str(report.get("attempted_at", ""))[:10]
            self.status.setText(self.tr("Forecasts updated %1 · once per day").replace("%1", stamp))
        else:
            self.status.setText(self.tr("No forecast available yet."))
        # Preserve the calendar/tasks frost pipeline without its old weather UI.
        forecasts = [
            DayForecast(
                date=d["date"],
                max_c=d["temperature_max_c"],
                min_c=d["temperature_min_c"],
                precipitation_mm=d["precipitation_mm"] or 0,
                weathercode=0,
                et0_mm=d.get("et0_mm"),
            )
            for d in rows
            if not d["stale"] and not d["partial"]
        ]
        self._forecast = WeatherForecast(forecasts, report["attempted_at"])
        self.forecast_ready.emit(self._forecast)
        self._buttons()

    def render_bulletin(self, record: dict[str, Any]) -> None:
        self._clear(self.bulletin)
        data = record["content"]
        hero, box = self._card()
        hero.setProperty("weatherHero", True)
        box.addWidget(self._label(data["title"], "heading"))
        box.addWidget(self._label(data["summary"], "subheading"))
        self.bulletin.addWidget(hero)
        grid = QGridLayout()
        for i, highlight in enumerate(data["highlights"]):
            card, box = self._card()
            card.setProperty("weatherHighlight", highlight["kind"])
            box.addWidget(self._label(highlight["label"], "caption"))
            box.addWidget(self._label(highlight["value"], "heading"))
            box.addWidget(self._label(highlight["detail"]))
            grid.addWidget(card, i // 2, i % 2)
        self.bulletin.addLayout(grid)
        for period in data["periods"]:
            card, box = self._card()
            box.addWidget(self._label(period["timeframe"], "caption"))
            box.addWidget(self._label(period["title"], "subheading"))
            box.addWidget(self._label(period["forecast"]))
            if period["garden"].strip():
                box.addWidget(self._label(self.tr("In the garden"), "caption"))
                box.addWidget(self._label(period["garden"]))
            self.bulletin.addWidget(card)
        self.bulletin_status.setText(
            self.tr("Bulletin from %1 · GPT-6.1 Sol").replace("%1", record["date"])
        )

    def closeEvent(self, event: Any) -> None:
        self.shutdown()
        super().closeEvent(event)

    def forecast(self) -> WeatherForecast | None:
        return self._forecast

    def shutdown(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.requestInterruption()
            worker.success.disconnect(self._success)
            worker.error.disconnect(self._error)
            retain_worker_until_finished(worker)
            worker.wait(100)
