"""Garden records as a persistent page, using the existing command service."""

from typing import Any

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QWidget

from open_garden_planner.core.garden_memory import GardenMemoryService
from open_garden_planner.services.garden_queries import card_for
from open_garden_planner.ui.widgets.garden_memory_panel import GardenMemoryPanel


class GardenDataView(GardenMemoryPanel):
    """Plant and zone cards, interventions, filters and exports in a main tab."""

    def __init__(self, service: GardenMemoryService, parent: QWidget | None = None) -> None:
        self._loaded_card: dict[str, Any] | None = None
        super().__init__(service, parent=parent)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(100)
        self._refresh_timer.timeout.connect(self._refresh_if_visible)
        service.project.garden_memory_changed.disconnect(self._refresh_events)
        service.project.garden_memory_changed.connect(self.schedule_refresh)
        service.commands.stack_changed.connect(self.schedule_refresh)
        self.scene.layers_changed.connect(self.schedule_refresh)
        self.scene.document_replaced.connect(self._document_replaced)

    def schedule_refresh(self) -> None:
        self._refresh_timer.start()

    def _refresh_if_visible(self) -> None:
        if self.isVisible():
            self.refresh()

    def _document_replaced(self) -> None:
        self.target_id = None
        # The scene announces replacement before Qt destroys its items.
        self.schedule_refresh()

    def _target_changed(self) -> None:
        super()._target_changed()
        self._loaded_card = card_for(self._snapshot(), self.target_id) if self.target_id else None

    def refresh(self) -> None:
        self._refresh_timer.stop()
        previous_target, previous_card = self.target_id, self._loaded_card
        text_fields = (self.name_edit, self.period_edit, self.quantity_edit)
        text_keys = ("name", "period", "quantity")
        combo_fields = (self.kind_combo, self.state_combo, self.zone_combo)
        combo_keys = ("kind", "state", "zone_id")
        texts = [field.text() for field in text_fields]
        choices = [field.currentData() for field in combo_fields]
        notes = self.notes_edit.toPlainText()
        self._reload_targets()
        # Preserve each unfinished field while its model value is unchanged.
        # An external rename must not erase a note being drafted in this page.
        if (self.target_id and self.target_id == previous_target and previous_card and self._loaded_card
                and previous_card["item_id"] == self._loaded_card["item_id"]):
            for field, key, text in zip(text_fields, text_keys, texts, strict=True):
                if self._loaded_card.get(key) == previous_card.get(key):
                    field.setText(text)
            for combo, key, value in zip(combo_fields, combo_keys, choices, strict=True):
                if self._loaded_card.get(key) == previous_card.get(key):
                    index = combo.findData(value)
                    if index >= 0:
                        combo.setCurrentIndex(index)
            if self._loaded_card.get("notes") == previous_card.get("notes"):
                self.notes_edit.setPlainText(notes)

    def select_target(self, target_id: str | None) -> None:
        self.target_id = target_id
        self.refresh()
