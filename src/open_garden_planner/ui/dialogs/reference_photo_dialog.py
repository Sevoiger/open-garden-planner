"""Preview one explicitly requested provider photo before keeping it."""
from PyQt6 import sip
from PyQt6.QtCore import QSize, Qt, QThreadPool
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QVBoxLayout,
)

from open_garden_planner.ui.widgets.plant_media_gallery import _MediaTask


class ReferencePhotoDialog(QDialog):
    """The original stays in memory until accepted; no library write on cancel."""

    def __init__(self, candidates, parent=None):
        super().__init__(parent)
        self.candidates = tuple(candidates)
        self.selected_candidate = None
        self.selected_payload = None
        self.keep_all = False
        self._job = None
        self.setWindowTitle(self.tr("Choose a reference photo"))
        self.resize(900, 640)
        layout = QVBoxLayout(self)
        hint = QLabel(self.tr("Several photos were found. Preview them and choose the one to keep. Botanical data is unchanged."))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        body = QHBoxLayout()
        self.options = QListWidget()
        self.options.setMinimumWidth(230)
        for candidate in self.candidates:
            item = QListWidgetItem(candidate.caption or candidate.url)
            item.setToolTip(candidate.url)
            self.options.addItem(item)
        self.options.currentRowChanged.connect(self._preview)
        body.addWidget(self.options, 1)
        self.preview = QLabel()
        self.preview.setMinimumSize(280, 220)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setWordWrap(True)
        body.addWidget(self.preview, 2)
        layout.addLayout(body, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(100)
        layout.addWidget(self.details)
        self.main_photo = QCheckBox(self.tr("Use as the main reference photo"))
        self.main_photo.setChecked(True)
        layout.addWidget(self.main_photo)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.all_button = self.buttons.addButton(self.tr("Download all photos"), QDialogButtonBox.ButtonRole.ActionRole)
        self.all_button.setToolTip(self.tr("Add all source photos to the gallery without changing the main photo."))
        self.all_button.clicked.connect(self._accept_all)
        layout.addWidget(self.buttons)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        if self.candidates:
            self.options.setCurrentRow(0)

    @property
    def use_as_main(self):
        return self.main_photo.isChecked()

    def _accept_all(self):
        if self.candidates:
            self.keep_all = True
            super().accept()

    def _preview(self, index):
        if self._job is not None or not 0 <= index < len(self.candidates):
            return
        candidate = self.candidates[index]
        self.selected_candidate, self.selected_payload = None, None
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.preview.clear()
        self.preview.setText(self.tr("Loading preview…"))
        self.details.setPlainText(candidate.caption + "\n" + candidate.url + "\n" + candidate.source_url)
        self.options.setEnabled(False)
        decode_error = self.tr("The reference image could not be decoded")
        from open_garden_planner.services.plant_source_import import retrieve_reference_photo

        def retrieve():
            payload = retrieve_reference_photo(candidate.url)
            image = QImage.fromData(payload)
            if image.isNull():
                raise ValueError(decode_error)
            return payload, image.scaled(QSize(620, 390), Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation)
        job = _MediaTask(retrieve)
        self._job = job, candidate
        job.signals.done.connect(self._ready)
        QThreadPool.globalInstance().start(job)

    def _ready(self, _identity, result, error):
        entry, self._job = self._job, None
        if sip.isdeleted(self):
            return
        self.options.setEnabled(True)
        if error:
            self.preview.setText(self.tr("Preview unavailable"))
            # Keep the exact technical diagnostic copyable, beneath localized
            # user-facing context rather than turning it into application prose.
            self.details.appendPlainText(self.tr("Preview unavailable") + ":\n" + error)
            return
        self.selected_candidate = entry[1]
        self.selected_payload, image = result
        self.preview.setPixmap(QPixmap.fromImage(image))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)

    def accept(self):
        if self.selected_candidate is not None and self.selected_payload is not None:
            super().accept()
