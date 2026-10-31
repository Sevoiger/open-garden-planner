"""Actual acquisitions and remaining quantities, independent of shopping lists."""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from open_garden_planner.core.personal_store import PersonalStoreError
from open_garden_planner.services.acquisition_inventory import AcquisitionInventory


class AcquisitionEditor(QDialog):
    def __init__(self, parent=None, record=None):
        super().__init__(parent)
        record = record or {}
        self.setWindowTitle(self.tr("Acquisition"))
        self.resize(500, 480)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.fields = {}
        for key, label in (
            ("name", self.tr("Name")), ("supplier", self.tr("Supplier")),
            ("purchase_date", self.tr("Purchase date (YYYY-MM-DD, optional)")),
            ("unit_price", self.tr("Unit price")), ("currency", self.tr("Currency")),
            ("purchased_quantity", self.tr("Quantity purchased")),
            ("remaining_quantity", self.tr("Quantity remaining")), ("unit", self.tr("Unit")),
            ("initial_condition", self.tr("Initial condition")), ("notes", self.tr("Notes")),
        ):
            edit = QLineEdit()
            value = record.get(key)
            edit.setText(str(value) if value is not None else "")
            self.fields[key] = edit
            form.addRow(label, edit)
            if key == "name":
                self.kind = QComboBox()
                self.kind.setEditable(True)
                self.kind.addItems(["", self.tr("Compost"), self.tr("Fertilizer"),
                                    self.tr("Soil amendment"), self.tr("Tool"), self.tr("Other")])
                self.kind.setCurrentText(record.get("kind", ""))
                form.addRow(self.tr("Type"), self.kind)
        layout.addLayout(form)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Save).setText(self.tr("Save"))
        box.button(QDialogButtonBox.StandardButton.Cancel).setText(self.tr("Cancel"))
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def values(self):
        result = {key: edit.text().strip() for key, edit in self.fields.items()}
        result["kind"] = self.kind.currentText().strip()
        for key in ("unit_price", "purchased_quantity", "remaining_quantity"):
            result[key] = float(result[key].replace(",", ".")) if result[key] else None
        return result


class AcquisitionInventoryDialog(QDialog):
    def __init__(self, store: AcquisitionInventory, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle(self.tr("Acquisitions and supplies"))
        self.resize(900, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(self.tr("Purchased lots and quantities remaining; separate from the shopping list.")))
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([self.tr("Name"), self.tr("Type"), self.tr("Supplier"),
            self.tr("Purchase date"), self.tr("Unit price"), self.tr("Purchased"), self.tr("Remaining")])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(self._edit)
        layout.addWidget(self.table)
        buttons = QHBoxLayout()
        for text, callback in ((self.tr("Add…"), self._add), (self.tr("Edit…"), self._edit),
                               (self.tr("Delete…"), self._delete), (self.tr("Reload"), self._reload)):
            button = QPushButton(text)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self._populate()

    def _populate(self):
        self._records = self.store.all()
        self.table.setRowCount(len(self._records))
        for row, record in enumerate(self._records):
            for col, key in enumerate(("name", "kind", "supplier", "purchase_date", "unit_price",
                                       "purchased_quantity", "remaining_quantity")):
                value = record.get(key)
                text = "" if value is None else str(value)
                if key in {"purchased_quantity", "remaining_quantity"} and text and record.get("unit"):
                    text += " " + record["unit"]
                if key == "unit_price" and text and record.get("currency"):
                    text += " " + record["currency"]
                self.table.setItem(row, col, QTableWidgetItem(text))

    def _selected(self):
        row = self.table.currentRow()
        return self._records[row] if 0 <= row < len(self._records) else None

    def _save(self, record=None):
        dialog = AcquisitionEditor(self, record)
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.store.put(dialog.values(), record["id"] if record else None)
            except (ValueError, OSError, PersonalStoreError) as exc:
                QMessageBox.warning(dialog, self.tr("Could not save acquisition"), str(exc))
                continue
            self._populate()
            return

    def _add(self):
        self._save()

    def _edit(self, *_args):
        record = self._selected()
        if record:
            self._save(record)

    def _delete(self):
        record = self._selected()
        if not record or QMessageBox.question(self, self.tr("Delete acquisition"),
                self.tr("Delete this acquisition history?")) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.delete(record["id"])
        except (ValueError, OSError, PersonalStoreError) as exc:
            QMessageBox.warning(self, self.tr("Could not save acquisition"), str(exc))
            return
        self._populate()

    def _reload(self):
        try:
            self.store.reload()
        except (OSError, PersonalStoreError) as exc:
            QMessageBox.warning(self, self.tr("Could not load acquisitions"), str(exc))
            return
        self._populate()
