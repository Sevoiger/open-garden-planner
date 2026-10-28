"""Perennial garden cards and interventions, backed by the shared command service."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt6.QtCore import QCoreApplication, Qt, QUrl
from PyQt6.QtGui import QAction, QDesktopServices
from PyQt6.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from open_garden_planner.app.paths import default_dialog_dir
from open_garden_planner.core.garden_memory import GardenMemoryService
from open_garden_planner.models.garden_memory import optional_number, period
from open_garden_planner.ui.widgets.responsive_dialog import ResponsiveDialog


def event_type_choices() -> list[tuple[str, str]]:
    return [
        (QCoreApplication.translate("EventEditor", "Planting"), "planting"), (QCoreApplication.translate("EventEditor", "Relocation"), "relocation"),
        (QCoreApplication.translate("EventEditor", "Amendment"), "amendment"), (QCoreApplication.translate("EventEditor", "Fertilisation"), "fertilisation"),
        (QCoreApplication.translate("EventEditor", "Mulching"), "mulching"), (QCoreApplication.translate("EventEditor", "Sowing"), "sowing"),
        (QCoreApplication.translate("EventEditor", "Pruning"), "pruning"), (QCoreApplication.translate("EventEditor", "Occasional watering"), "watering"),
        (QCoreApplication.translate("EventEditor", "Observation"), "observation"), (QCoreApplication.translate("EventEditor", "Removal or loss"), "removal_loss"),
        ]


def _edited_period(text: str, original: dict[str, str]) -> dict[str, str]:
    """Keep structured precision/labels when an unrelated field is edited."""
    return period(original) if text == (original["label"] or original["value"]) else period(text)


class EventEditor(ResponsiveDialog):
    """A single intervention; an empty occurrence date explicitly means unknown."""

    def __init__(self, targets: list[dict[str, str]], event: dict[str, Any] | None = None,
                 parent: QWidget | None = None, choices: list[dict[str, str]] | None = None, project: Path | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Intervention"))
        self.resize(560, 520)
        self.targets = targets
        self.project = project
        self.value: dict[str, Any] | None = None
        event = event or {}
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.type_combo = QComboBox()
        self.type_choices = event_type_choices()
        for label, code in self.type_choices:
            self.type_combo.addItem(label, code)
        self.type_combo.setCurrentIndex(max(0, self.type_combo.findData(event.get("type", "observation"))))
        form.addRow(self.tr("Type:"), self.type_combo)
        self.status_combo = QComboBox()
        self.status_combo.addItem(self.tr("Planned"), "planned")
        self.status_combo.addItem(self.tr("Completed"), "done")
        self.status_combo.setCurrentIndex(max(0, self.status_combo.findData(event.get("status", "done"))))
        form.addRow(self.tr("Status:"), self.status_combo)
        self._original_period = period(event.get("period"))
        self.date_edit = QLineEdit(self._original_period["label"] or self._original_period["value"])
        self.date_edit.setToolTip(self._original_period["value"])
        self.date_edit.setPlaceholderText(self.tr("YYYY-MM-DD, YYYY-MM, autumn 2023, YYYY, or blank"))
        form.addRow(self.tr("Occurrence:"), self.date_edit)
        self.description = QPlainTextEdit(event.get("description", ""))
        self.description.setMaximumHeight(90)
        form.addRow(self.tr("Description:"), self.description)
        self.target_list = QListWidget()
        self.target_list.setMaximumHeight(90)
        selected_ids = {t["id"] for t in targets}
        available = {t["id"]: t for t in (choices or targets)}
        available.update({t["id"]: t for t in targets if t["id"] not in available})
        for target in available.values():
            row = QListWidgetItem(target["name"])
            row.setData(Qt.ItemDataRole.UserRole, target)
            row.setCheckState(Qt.CheckState.Checked if target["id"] in selected_ids else Qt.CheckState.Unchecked)
            self.target_list.addItem(row)
        form.addRow(self.tr("Targets:"), self.target_list)
        self.optional: dict[str, QLineEdit] = {}
        for key, label in [
            ("material", self.tr("Material or product:")), ("quantity", self.tr("Quantity (optional):")),
            ("unit", self.tr("Unit:")), ("application", self.tr("Application method:")),
            ("area_m2", self.tr("Treated area (m²):")), ("depth_cm", self.tr("Depth or thickness (cm):")),
        ]:
            raw = event.get(key)
            edit = QLineEdit("" if raw is None else str(raw))
            self.optional[key] = edit
            form.addRow(label, edit)
        form_widget = QWidget()
        form_widget.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_widget)
        layout.addWidget(scroll, 1)
        self.media = list(event.get("media", []))
        self.media_list = QListWidget()
        self.media_list.setMaximumHeight(70)
        self.media_list.addItems(self.media)
        layout.addWidget(self.media_list)
        media_actions = QHBoxLayout()
        attachment_menu = QMenu(self)
        attachment_button = QPushButton(self.tr("Attachments…"))
        attachment_button.setMenu(attachment_menu)
        media_actions.addWidget(attachment_button)
        for label, handler in [(self.tr("Attach photo or document…"), self._attach),
                               (self.tr("Open attachment"), self._open_attachment),
                               (self.tr("Remove attachment reference"), self._remove_attachment)]:
            action = QAction(label, attachment_menu)
            action.triggered.connect(handler)
            attachment_menu.addAction(action)
        layout.addLayout(media_actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _attach(self) -> None:
        if not self.project:
            QMessageBox.information(self, self.tr("Intervention"), self.tr("Save the project before attaching files."))
            return
        source, _ = QFileDialog.getOpenFileName(self, self.tr("Attach photo or document"),
                                               str(default_dialog_dir(self.project)))
        if source:
            try:
                from open_garden_planner.services.garden_archive import import_attachment

                reference = import_attachment(Path(source), self.project)
                if reference not in self.media:
                    self.media.append(reference)
                    self.media_list.addItem(reference)
            except Exception as exc:
                QMessageBox.warning(self, self.tr("Invalid garden data"), str(exc))

    def _open_attachment(self) -> None:
        row = self.media_list.currentItem()
        if row is not None and self.project is not None:
            try:
                path = (self.project.parent / row.text()).resolve()
                path.relative_to(self.project.parent.resolve())
                if not path.is_file():
                    raise ValueError(self.tr("Attachment is missing."))
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            except Exception as exc:
                QMessageBox.warning(self, self.tr("Invalid garden data"), str(exc))

    def _remove_attachment(self) -> None:
        index = self.media_list.currentRow()
        if index >= 0:
            self.media.pop(index)
            self.media_list.takeItem(index)

    def accept(self) -> None:
        try:
            self.targets = []
            for i in range(self.target_list.count()):
                target = self.target_list.item(i)
                if target is not None and target.checkState() == Qt.CheckState.Checked:
                    self.targets.append(target.data(Qt.ItemDataRole.UserRole))
            value: dict[str, Any] = {
                "type": self.type_combo.currentData(), "status": self.status_combo.currentData(),
                "period": _edited_period(self.date_edit.text(), self._original_period), "description": self.description.toPlainText(),
                "targets": self.targets, "media": self.media,
            }
            for key, edit in self.optional.items():
                value[key] = (optional_number(edit.text(), key) if key in {"quantity", "area_m2", "depth_cm"}
                              else edit.text())
            # Validate before closing; id/time are assigned by the shared service.
            from open_garden_planner.models.garden_memory import validate_event

            validate_event(value)
            self.value = value
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, self.tr("Invalid garden data"),
                                self.tr("Check the entered values: {error}").format(error=str(exc)))
            return
        super().accept()


class GardenMemoryDialog(ResponsiveDialog):
    """Compatibility dialog around the content also used by the garden data page."""

    def __init__(self, service: GardenMemoryService, target_id: str | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from open_garden_planner.ui.widgets.garden_memory_panel import GardenMemoryPanel

        self.setWindowTitle(self.tr("Garden records"))
        self.resize(980, 700)
        layout = QVBoxLayout(self)
        self.panel = GardenMemoryPanel(service, target_id, self)
        layout.addWidget(self.panel)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
