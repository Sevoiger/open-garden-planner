"""Garden data page sharing the calendar's weather source and frost pipeline."""

from __future__ import annotations

from typing import Any

from PyQt6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from open_garden_planner.ui.theme import set_text_role
from open_garden_planner.ui.widgets.weather_widget import WeatherWidget


class GardenDataView(QScrollArea):
    """Dedicated, scrollable page for location and weather data."""

    def __init__(self, project_manager: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_manager = project_manager
        self.setWidgetResizable(True)
        content = QWidget()
        content.setAutoFillBackground(True)
        layout = QVBoxLayout(content)
        title = QLabel(self.tr("Garden Data"))
        set_text_role(title, "h1")
        layout.addWidget(title)
        self.weather = WeatherWidget()
        layout.addWidget(self.weather)
        layout.addStretch()
        self.setWidget(content)

    def refresh(self) -> None:
        """Use the current project location, including when the calendar is hidden."""
        location = self._project_manager.location or {}
        lat, lon = location.get("latitude"), location.get("longitude")
        self.weather.set_location(lat, lon)
        if lat is not None and lon is not None:
            self.weather.refresh()
