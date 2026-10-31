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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from open_garden_planner.services.soil_samples import MAX_REPORT_TEXT


class SoilSampleDialog(QDialog):
    """Optional pH and a faithful, open-ended laboratory report."""

    def __init__(self, parent=None, *, full=False, record=None):
        super().__init__(parent)
        record = record or {}
        report = record.get("laboratory_report") or {}
        self._report_extra = {k: deepcopy(v) for k, v in report.items()
                              if k not in {"laboratory", "reference", "sample_depth", "source_text", "readings"}}
        self._full = full or bool(report)
        self.setWindowTitle(self.tr("Soil analysis") if self._full else self.tr("pH point"))
        self.resize(780 if self._full else 360, 650 if self._full else 160)
        layout = QVBoxLayout(self)
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
        self.table = QTableWidget(0, 6)
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
        layout.addWidget(box)

    def _add_row(self, row=None, original_index=None):
        row = row or {}
        index = self.table.rowCount()
        self.table.insertRow(index)
        for column, key in enumerate(("parameter", "value", "unit", "method", "reference", "interpretation")):
            item = QTableWidgetItem(row.get(key, ""))
            if column == 0:
                item.setData(Qt.ItemDataRole.UserRole, {k: deepcopy(v) for k, v in row.items()
                    if k not in {"parameter", "value", "unit", "method", "reference", "interpretation"}})
                item.setData(Qt.ItemDataRole.UserRole + 1, original_index)
            self.table.setItem(index, column, item)

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
            keys = ("parameter", "value", "unit", "method", "reference", "interpretation")
            readings = []
            used_indices = set()
            for row in range(self.table.rowCount()):
                values = {key: self.table.item(row, col).text() if self.table.item(row, col) else ""
                          for col, key in enumerate(keys)}
                if any(values.values()):
                    first = self.table.item(row, 0)
                    extras = first.data(Qt.ItemDataRole.UserRole) if first else None
                    reading = {**(extras or {}), **values}
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
