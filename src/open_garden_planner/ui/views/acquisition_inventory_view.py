"""Tab wrapper for the global acquisition inventory and its recovery state."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from open_garden_planner.core.personal_store import PersonalStoreError
from open_garden_planner.services.acquisition_inventory import AcquisitionInventory
from open_garden_planner.services.plant_library import get_app_data_dir
from open_garden_planner.ui.dialogs.acquisition_inventory_dialog import AcquisitionInventoryDialog


class AcquisitionInventoryView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._error = QLabel()
        self._error.setWordWrap(True)
        self._retry = QPushButton(self.tr("Retry loading"))
        self._retry.clicked.connect(self._load)
        self._layout.addWidget(self._error)
        self._layout.addWidget(self._retry)
        self._view = None
        self._load()

    def _load(self):
        try:
            store = AcquisitionInventory(get_app_data_dir() / AcquisitionInventory.FILENAME)
        except (OSError, PersonalStoreError) as exc:
            self._error.setText(self.tr("Could not load acquisitions: {error}").format(error=exc))
            self._error.show()
            self._retry.show()
            return
        if self._view is not None:
            self._layout.removeWidget(self._view)
            self._view.deleteLater()
        self._view = AcquisitionInventoryDialog(store, self, embedded=True)
        self._view.setWindowFlags(Qt.WindowType.Widget)
        self._layout.addWidget(self._view)
        self._error.hide()
        self._retry.hide()

    def refresh(self):
        if self._view is None:
            self._load()
        else:
            self._view._reload()
