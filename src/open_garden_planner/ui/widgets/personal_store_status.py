"""Inline load/save recovery controls for cross-project personal stores."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QWidget

from open_garden_planner.core.personal_store import PersonalStoreConflictError
from open_garden_planner.ui.theme import set_text_role


class PersonalStoreStatus(QWidget):
    """Show a persistent, actionable storage error beside a personal catalog."""

    retry_requested = pyqtSignal()
    reload_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(8)

        self._message = QLabel()
        self._message.setWordWrap(True)
        set_text_role(self._message, color_role="error")
        self._message.setStyleSheet("font-weight: 600;")
        layout.addWidget(self._message, 1)

        self._retry = QPushButton()
        self._retry.clicked.connect(lambda: self.retry_requested.emit())
        layout.addWidget(self._retry)

        self._reload = QPushButton(self.tr("Reload from Disk…"))
        self._reload.clicked.connect(lambda: self.reload_requested.emit())
        layout.addWidget(self._reload)
        self.hide()

    def show_load_error(self, error: BaseException) -> None:
        """Explain that data could not be loaded and allow an explicit retry."""
        self._message.setText(
            self.tr(
                "Personal data could not be loaded: {error}. The file was left "
                "unchanged. Repair it, then retry loading."
            ).format(error=str(error))
        )
        self._retry.setText(self.tr("Retry Loading"))
        self._reload.hide()
        self._retry.show()
        self.show()

    def show_save_error(self, error: BaseException) -> None:
        """Explain whether memory remains pending after a failed save."""
        if isinstance(error, PersonalStoreConflictError):
            message = self.tr(
                "Another OGP writer changed this file. Your edits remain in "
                "memory: {error}. Retry after resolving the conflict, or reload."
            )
        else:
            message = self.tr(
                "Changes are in memory but were not saved: {error}. Retry "
                "saving or reload from disk."
            )
        self._message.setText(message.format(error=str(error)))
        self._retry.setText(self.tr("Retry Saving"))
        self._retry.show()
        self._reload.show()
        self.show()

    def clear(self) -> None:
        """Hide the recovery controls after a successful load or save."""
        self._message.clear()
        self.hide()

    def confirm_reload(self, parent: QWidget) -> bool:
        """Ask before discarding edits that remain only in memory."""
        answer = QMessageBox.question(
            parent,
            self.tr("Reload Personal Data"),
            self.tr("Reloading will discard the current in-memory changes. Continue?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes


__all__ = ["PersonalStoreStatus"]
