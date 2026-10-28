"""Review and maintain observed plant sizes through the shared command service."""
from __future__ import annotations

from uuid import uuid4

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from open_garden_planner.services.plant_measurements import measurement_view
from open_garden_planner.ui.widgets.responsive_dialog import ResponsiveDialog


class PlantMeasurementsDialog(ResponsiveDialog):
    def __init__(self, service, item, parent=None):
        super().__init__(parent)
        self.service, self.item = service, item
        self.setWindowTitle(self.tr("Plant measurements"))
        self.resize(600, 510)
        layout = QVBoxLayout(self)
        hint = QLabel(self.tr("Select a record to correct it, or add a new dated measurement. Blank dimensions remain unknown."))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.history = QListWidget()
        self.history.currentItemChanged.connect(self._select)
        layout.addWidget(self.history)
        self.undated = QLabel()
        self.undated.setWordWrap(True)
        layout.addWidget(self.undated)
        form = QFormLayout()
        self.day = QLineEdit(QDate.currentDate().toString("yyyy-MM-dd"))
        self.day.setPlaceholderText("YYYY-MM-DD")
        form.addRow(self.tr("Measurement date:"), self.day)
        self.height = QLineEdit()
        self.spread = QLineEdit()
        for edit in (self.height, self.spread):
            edit.setPlaceholderText(self.tr("Unknown"))
        form.addRow(self.tr("Height (cm):"), self.height)
        form.addRow(self.tr("Spread (cm):"), self.spread)
        self.note = QLineEdit()
        form.addRow(self.tr("Note:"), self.note)
        layout.addLayout(form)
        actions = QHBoxLayout()
        for label, handler in [(self.tr("New measurement"), self._new),
                               (self.tr("Save measurement"), self._save),
                               (self.tr("Delete measurement"), self._delete)]:
            button = QPushButton(label)
            button.clicked.connect(handler)
            actions.addWidget(button)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh()

    def _refresh(self):
        view = measurement_view(self.item.metadata)
        self.history.clear()
        for record in reversed(view["measurements"]):
            height = str(record.get("height_cm", "—"))
            spread = str(record.get("spread_cm", "—"))
            row = QListWidgetItem(self.tr("{date} — height {height} cm; spread {spread} cm — {note}").format(
                date=record["date"], height=height, spread=spread, note=record["note"]))
            row.setData(Qt.ItemDataRole.UserRole, record)
            self.history.addItem(row)
        baseline = view["undated_size"] or {
            key: value["value"] for key, value in view["current_size"].items()
            if value["date"] is None and value["value"] is not None}
        height = baseline.get("current_height_cm", baseline.get("height_cm", "—"))
        spread = baseline.get("current_spread_cm", baseline.get("current_diameter_cm", baseline.get("spread_cm", "—")))
        self.undated.setText(self.tr("Previous undated values — height {height} cm; spread {spread} cm").format(
            height=height, spread=spread))
        self._new()

    def _new(self):
        self.history.setCurrentRow(-1)
        self.day.setText(QDate.currentDate().toString("yyyy-MM-dd"))
        self.height.clear()
        self.spread.clear()
        self.note.clear()

    def _select(self, row, _previous):
        if row is None:
            return
        record = row.data(Qt.ItemDataRole.UserRole)
        self.day.setText(record["date"])
        self.height.setText(str(record["height_cm"]) if "height_cm" in record else "")
        self.spread.setText(str(record["spread_cm"]) if "spread_cm" in record else "")
        self.note.setText(record["note"])

    def _write(self, operation, fields):
        try:
            self.service.plant_measurement(operation, str(self.item.item_id), fields, str(uuid4()))
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, self.tr("Plant measurements"), str(exc))
            return
        self._refresh()

    def _save(self):
        try:
            dimensions = {key: float(edit.text().strip().replace(",", ".")) if edit.text().strip() else None
                          for key, edit in (("height_cm", self.height), ("spread_cm", self.spread))}
        except ValueError:
            QMessageBox.warning(self, self.tr("Plant measurements"), self.tr("Enter a positive number of centimetres, or leave the dimension blank."))
            return
        fields = {"date": self.day.text().strip(),
                  **dimensions, "note": self.note.text()}
        row = self.history.currentItem()
        if row is not None:
            fields["measurement_id"] = row.data(Qt.ItemDataRole.UserRole)["id"]
        self._write("edit_plant_measurement" if row is not None else "add_plant_measurement", fields)

    def _delete(self):
        row = self.history.currentItem()
        if row is not None:
            self._write("delete_plant_measurement", {"measurement_id": row.data(Qt.ItemDataRole.UserRole)["id"]})
