"""Edit a dated point measurement without interpreting laboratory results."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from open_garden_planner.services.soil_samples import (
    MAX_REPORT_TEXT,
    STANDARD_PARAMETER_CODES,
    positions_from_record,
)


class SoilSampleDialog(QDialog):
    """Optional pH and a faithful, open-ended laboratory report."""

    def __init__(self, parent=None, *, full=False, record=None, positions=None):
        super().__init__(parent)
        record = record or {}
        report = record.get("laboratory_report") or {}
        self._report_extra = {k: deepcopy(v) for k, v in report.items()
                              if k not in {"laboratory", "reference", "sample_depth", "source_text", "readings"}}
        self._full = full or bool(report)
        self.setWindowTitle(self.tr("Soil analysis") if self._full else self.tr("pH point"))
        self.resize(780 if self._full else 360, 580 if self._full else 160)
        outer = QVBoxLayout(self)
        if self._full:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            content = QWidget()
            scroll.setWidget(content)
            outer.addWidget(scroll)
            layout = QVBoxLayout(content)
        else:
            layout = outer
        form = QFormLayout()
        self.date = QDateEdit(QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date.setMaximumDate(QDate.currentDate())
        parsed = QDate.fromString(record.get("date", ""), "yyyy-MM-dd")
        if parsed.isValid():
            self.date.setDate(parsed)
        form.addRow(self.tr("Sample date"), self.date)
        self.ph_enabled = QCheckBox(self.tr("pH measured"))
        self.ph = QLineEdit()
        self.ph.setPlaceholderText("0 – 14")
        self.ph_enabled.setChecked(record.get("ph") is not None or not self._full)
        self.ph.setText(str(record["ph"]) if record.get("ph") is not None else "")
        self.ph.setEnabled(self.ph_enabled.isChecked())
        self.ph_enabled.toggled.connect(self.ph.setEnabled)
        ph_row = QHBoxLayout()
        ph_row.addWidget(self.ph_enabled)
        ph_row.addWidget(self.ph)
        form.addRow(ph_row)
        self.lab = QLineEdit(report.get("laboratory", ""))
        self.reference = QLineEdit(report.get("reference", ""))
        self.depth = QLineEdit(report.get("sample_depth", ""))
        if self._full:
            form.addRow(self.tr("Laboratory"), self.lab)
            form.addRow(self.tr("Report reference"), self.reference)
            form.addRow(self.tr("Sampling depth"), self.depth)
        layout.addLayout(form)
        self.positions = QTableWidget(0, 2)
        self.positions.setHorizontalHeaderLabels([self.tr("X (cm)"), self.tr("Y (cm)")])
        initial_positions = positions if positions is not None else positions_from_record(record)
        self._initial_positions = deepcopy(initial_positions)
        self._opaque_positions = ("sample_positions" in record and record["sample_positions"] != initial_positions)
        if self._full:
            for point in initial_positions:
                row = self.positions.rowCount()
                self.positions.insertRow(row)
                for col, key in enumerate(("x_cm", "y_cm")):
                    self.positions.setItem(row, col, QTableWidgetItem(str(point[key])))
            self.positions.setMaximumHeight(80)
            layout.addWidget(QLabel(self.tr("Sampling locations (up to 15)")))
            layout.addWidget(self.positions)
            position_buttons = QHBoxLayout()
            add_position = QPushButton(self.tr("Add location"))
            add_position.clicked.connect(self._add_position)
            remove_position = QPushButton(self.tr("Remove selected location"))
            remove_position.clicked.connect(lambda: self.positions.removeRow(self.positions.currentRow()) if self.positions.currentRow() >= 0 else None)
            position_buttons.addWidget(add_position)
            position_buttons.addWidget(remove_position)
            position_buttons.addStretch()
            layout.addLayout(position_buttons)
        self.table = QTableWidget(0, 6)
        if self._full:
            self.table.setMinimumHeight(150)
        self.table.setHorizontalHeaderLabels([
            self.tr("Parameter"), self.tr("Value"), self.tr("Unit"),
            self.tr("Method"), self.tr("Reference range"), self.tr("Interpretation"),
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for column in range(self.table.columnCount()):
            label = self.table.horizontalHeaderItem(column).text()
            self.table.setColumnWidth(column, max(90, self.table.fontMetrics().horizontalAdvance(label) + 25))
        self._original_row_count = len(report.get("readings", []))
        for original_index, row in enumerate(report.get("readings", [])):
            self._add_row(row, original_index)
        if self._full:
            existing_labels = {str(row.get("parameter", "")).casefold() for row in report.get("readings", [])}
            existing_codes = {row.get("parameter_code") for row in report.get("readings", [])
                              if isinstance(row.get("parameter_code"), str) and row.get("parameter_code") in STANDARD_PARAMETER_CODES}
            presets = (("nitrogen_total", self.tr("Total nitrogen")), ("phosphorus", self.tr("Phosphorus")),
                       ("potassium", self.tr("Potassium")), ("sand", self.tr("Sand")),
                       ("silt", self.tr("Silt")), ("clay", self.tr("Clay")),
                       ("organic_matter", self.tr("Organic matter")), ("organic_carbon", self.tr("Organic carbon")),
                       ("limestone_total", self.tr("Total limestone")), ("limestone_active", self.tr("Active limestone")),
                       ("cec", self.tr("Cation exchange capacity")), ("calcium", self.tr("Calcium")),
                       ("magnesium", self.tr("Magnesium")), ("sodium", self.tr("Sodium")))
            for code, label in presets:
                if code not in existing_codes and label.casefold() not in existing_labels:
                    self._add_row({"parameter": label, "parameter_code": code}, preset=True)
        self.source = QPlainTextEdit(report.get("source_text", ""))
        if self._full:
            layout.addWidget(QLabel(self.tr("Reported results (keep the original units and qualifiers)")))
            layout.addWidget(self.table, 1)
            buttons = QHBoxLayout()
            add = QPushButton(self.tr("Add result"))
            add.clicked.connect(lambda: self._add_row())
            remove = QPushButton(self.tr("Remove result"))
            remove.clicked.connect(self._remove_row)
            buttons.addWidget(add)
            buttons.addWidget(remove)
            buttons.addStretch()
            layout.addLayout(buttons)
            layout.addWidget(QLabel(self.tr("Complete source report")))
            layout.addWidget(self.source, 1)
            load = QPushButton(self.tr("Import text or Markdown…"))
            load.clicked.connect(self._import_text)
            layout.addWidget(load)
        self.notes = QLineEdit(record.get("notes", ""))
        form_notes = QFormLayout()
        form_notes.addRow(self.tr("Notes"), self.notes)
        layout.addLayout(form_notes)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Save).setText(self.tr("Save"))
        box.button(QDialogButtonBox.StandardButton.Cancel).setText(self.tr("Cancel"))
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        outer.addWidget(box)

    def _add_row(self, row=None, original_index=None, preset=False):
        row = row or {}
        index = self.table.rowCount()
        self.table.insertRow(index)
        for column, key in enumerate(("parameter", "value", "unit", "method", "reference", "interpretation")):
            item = QTableWidgetItem(row.get(key, ""))
            if column == 0:
                item.setData(Qt.ItemDataRole.UserRole, {k: deepcopy(v) for k, v in row.items()
                    if k not in {"parameter", "value", "unit", "method", "reference", "interpretation"}
                    and (k != "parameter_code" or not isinstance(v, str) or v not in STANDARD_PARAMETER_CODES)})
                item.setData(Qt.ItemDataRole.UserRole + 1, original_index)
                item.setData(Qt.ItemDataRole.UserRole + 2, preset)
                code = row.get("parameter_code")
                recognized = isinstance(code, str) and code in STANDARD_PARAMETER_CODES
                item.setData(Qt.ItemDataRole.UserRole + 3, code if recognized else None)
                if preset or recognized:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(index, column, item)

    def _add_position(self):
        if self.positions.rowCount() >= 15:
            return
        row = self.positions.rowCount()
        self.positions.insertRow(row)
        for column in range(2):
            self.positions.setItem(row, column, QTableWidgetItem(""))
        self.positions.setCurrentCell(row, 0)

    def _remove_row(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def _import_text(self):
        path, _ = QFileDialog.getOpenFileName(self, self.tr("Import report"), "", self.tr("Text files (*.txt *.md)"))
        if not path:
            return
        try:
            with Path(path).open(encoding="utf-8-sig") as file:
                data = file.read(MAX_REPORT_TEXT + 1)
            if len(data) > MAX_REPORT_TEXT:
                raise ValueError("Report is too long")
        except (OSError, UnicodeError, ValueError):
            QMessageBox.warning(self, self.tr("Import report"), self.tr("Could not read this UTF-8 text file or it is too large."))
            return
        self.source.setPlainText(data)

    def fields(self):
        fields = {"test_date": self.date.date().toString("yyyy-MM-dd"),
                  "ph": float(self.ph.text().strip().replace(",", "."))
                  if self.ph_enabled.isChecked() and self.ph.text().strip() else None,
                  "notes": self.notes.text()}
        if self._full:
            current_positions = [
                {key: float(self.positions.item(row, col).text().strip().replace(",", "."))
                 for col, key in enumerate(("x_cm", "y_cm"))}
                for row in range(self.positions.rowCount())
            ]
            if self._opaque_positions:
                if current_positions != self._initial_positions:
                    raise ValueError("Existing sampling-position extension cannot be replaced")
            else:
                fields["positions"] = current_positions
            keys = ("parameter", "value", "unit", "method", "reference", "interpretation")
            readings = []
            used_indices = set()
            for row in range(self.table.rowCount()):
                values = {key: self.table.item(row, col).text() if self.table.item(row, col) else ""
                          for col, key in enumerate(keys)}
                first = self.table.item(row, 0)
                preset = first.data(Qt.ItemDataRole.UserRole + 2) if first else False
                if any(values.values()) and (not preset or any(values[key] for key in keys[1:])):
                    extras = first.data(Qt.ItemDataRole.UserRole) if first else None
                    reading = {**(extras or {}), **values}
                    code = first.data(Qt.ItemDataRole.UserRole + 3) if first else None
                    if code:
                        reading["parameter_code"] = code
                    previous_index = first.data(Qt.ItemDataRole.UserRole + 1) if first else None
                    if previous_index is not None:
                        used_indices.add(previous_index)
                        reading["previous_index"] = previous_index
                    readings.append(reading)
            fields["report"] = {**self._report_extra, "laboratory": self.lab.text(), "reference": self.reference.text(),
                                "sample_depth": self.depth.text(), "source_text": self.source.toPlainText(),
                                "readings": readings,
                                "removed_reading_indices": sorted(set(range(self._original_row_count)) - used_indices)}
        return fields
