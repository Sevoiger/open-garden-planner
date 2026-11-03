"""Asynchronous thumbnail gallery for canonical specimen attachments."""
from copy import deepcopy
from pathlib import Path

from PyQt6 import sip
from PyQt6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QIcon, QImage, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from open_garden_planner.core.plant_specimens import replace_specimen_metadata
from open_garden_planner.services.plant_media import (
    edit_media_metadata,
    import_specimen_media,
    resolve_media,
)
from open_garden_planner.services.plant_specimens import specimen_record


class _MediaSignals(QObject):
    done = pyqtSignal(object, object, str)


class _MediaTask(QRunnable):
    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.signals = _MediaSignals()

    def run(self):
        try:
            result = self.operation()
        except Exception as error:
            self.signals.done.emit(id(self), None, str(error))
        else:
            self.signals.done.emit(id(self), result, "")


def _read_image(project, reference, size):
    image = QImage(str(resolve_media(project, reference)))
    if image.isNull():
        raise ValueError("The image could not be decoded")
    return image.scaled(size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


def _read_thumbnail(project, asset):
    resolve_media(project, asset["photo_path"])
    return _read_image(project, asset["thumbnail_path"], QSize(160, 120))


class PlantMediaGallery(QWidget):
    def __init__(self, project, scene, commands, identity, parent=None):
        super().__init__(parent)
        self.project, self.scene, self.commands, self.identity = project, scene, commands, identity
        self._assets = []
        self._generation = 0
        self._jobs = {}
        layout = QVBoxLayout(self)
        self.gallery = QListWidget()
        self.gallery.setViewMode(QListWidget.ViewMode.IconMode)
        self.gallery.setIconSize(QSize(160, 120))
        self.gallery.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.gallery.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.gallery.itemDoubleClicked.connect(lambda _item: self._open())
        self.gallery.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.gallery, 1)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.kind = QComboBox()
        for key, text in (("personal", self.tr("Personal garden photo")),
                          ("reference", self.tr("Reference photo")),
                          ("illustration", self.tr("Plan illustration")), ("document", self.tr("Document"))):
            self.kind.addItem(text, key)
        self.caption = QLineEdit()
        self.taken_at = QLineEdit()
        self.taken_at.setPlaceholderText(self.tr("Unknown, or YYYY-MM-DD"))
        self.origin = QLineEdit()
        self.credit = QLineEdit()
        form.addRow(self.tr("Media category"), self.kind)
        form.addRow(self.tr("Caption"), self.caption)
        form.addRow(self.tr("Date taken"), self.taken_at)
        form.addRow(self.tr("Source URL"), self.origin)
        form.addRow(self.tr("Credit"), self.credit)
        layout.addLayout(form)
        buttons = QGridLayout()
        self.import_button = QPushButton(self.tr("Add photo or document"))
        self.import_button.clicked.connect(self._import)
        buttons.addWidget(self.import_button, 0, 0)
        for index, (label, handler) in enumerate(((self.tr("Open / compare"), self._open),
                               (self.tr("Save media details"), self._save_details),
                               (self.tr("Use as main personal photo"), self._primary),
                               (self.tr("Remove association"), self._remove),
                               (self.tr("Open folder"), self._open_folder),
                               (self.tr("Choose photo folder"), self._choose_folder)), 1):
            button = QPushButton(label)
            button.clicked.connect(handler)
            buttons.addWidget(button, index // 4, index % 4)
        layout.addLayout(buttons)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.refresh()

    def _metadata(self):
        return deepcopy(specimen_record(self.project.snapshot_dict(self.scene), self.identity)["metadata"])

    def _run(self, operation, callback):
        job = _MediaTask(operation)
        self._jobs[id(job)] = (job, callback)
        job.signals.done.connect(self._task_done)
        QThreadPool.globalInstance().start(job)

    def _task_done(self, identity, result, error):
        entry = self._jobs.pop(identity, None)
        if entry:
            entry[1](result, error)

    def refresh(self):
        self._generation += 1
        generation = self._generation
        self.gallery.clear()
        instance = self._metadata().get("plant_instance", {})
        assets = instance.get("media_assets", [])
        if not isinstance(assets, list) or any(
            not isinstance(asset, dict) or not isinstance(asset.get("id"), str)
            or not isinstance(asset.get("photo_path"), str) for asset in assets
        ):
            self._assets = []
            self._error(self.tr("Unrecognized media collection; the original data was preserved."))
            return
        self._assets = deepcopy(assets)
        project = self.project.current_file
        for asset in self._assets:
            caption = asset.get("caption") or Path(asset["photo_path"]).name
            if instance.get("primary_media_id") == asset["id"]:
                caption = "★ " + caption
            item = QListWidgetItem(caption)
            item.setData(Qt.ItemDataRole.UserRole, asset["id"])
            item.setToolTip(self.tr("Category: {kind}\nDate taken: {taken}\nImported: {imported}\nSource: {source}\nCredit: {credit}").format(
                kind=self.kind.itemText(self.kind.findData(asset.get("kind"))),
                taken=asset.get("taken_at") or self.tr("Unknown"), imported=asset.get("imported_at", ""),
                source=asset.get("source_url", ""), credit=asset.get("credit", "")))
            self.gallery.addItem(item)
            reference = asset.get("thumbnail_path")
            if project is not None and reference:
                def loaded(image, error, item=item, generation=generation):
                    if generation != self._generation:
                        return
                    if error:
                        item.setText(self.tr("Missing or unreadable file"))
                        item.setToolTip(error)
                    else:
                        item.setIcon(QIcon(QPixmap.fromImage(image)))
                self._run(lambda asset=asset: _read_thumbnail(project, asset), loaded)
            elif project is not None:
                try:
                    resolve_media(project, asset["photo_path"])
                except (OSError, ValueError):
                    item.setText(self.tr("Missing or unreadable file"))

    def _selected(self):
        identities = {item.data(Qt.ItemDataRole.UserRole) for item in self.gallery.selectedItems()}
        return [asset for asset in self._assets if asset["id"] in identities]

    def _selection_changed(self):
        selected = self._selected()
        if len(selected) == 1:
            asset = selected[0]
            self._detail_raw_initial = deepcopy(asset)
            self.caption.setText(asset.get("caption", ""))
            self.taken_at.setText(asset.get("taken_at") or "")
            self.origin.setText(asset.get("source_url", ""))
            self.credit.setText(asset.get("credit", ""))
            index = self.kind.findData(asset.get("kind"))
            if index < 0:
                self.kind.addItem(self.tr("Unknown media category"), asset.get("kind"))
                index = self.kind.count() - 1
            self.kind.setCurrentIndex(index)
            self._detail_initial = {"kind": self.kind.currentData(), "caption": self.caption.text(),
                                    "taken_at": self.taken_at.text() or None,
                                    "source_url": self.origin.text(), "credit": self.credit.text()}

    def _save_details(self):
        selected = self._selected()
        if len(selected) == 1:
            try:
                fields = {
                    "kind": self.kind.currentData(), "caption": self.caption.text().strip(),
                    "taken_at": self.taken_at.text().strip() or None,
                    "source_url": self.origin.text().strip(), "credit": self.credit.text().strip(),
                }
                changes = {key: value for key, value in fields.items() if value != self._detail_initial.get(key)}
                if not changes:
                    return
                current = next((asset for asset in self._metadata().get("plant_instance", {}).get("media_assets", [])
                                if asset.get("id") == selected[0]["id"]), None)
                if current is None or any(current.get(key) != self._detail_raw_initial.get(key) for key in changes):
                    raise ValueError(self.tr("This photo changed while its details were open. Reload before saving."))
                self._commit(update_id=selected[0]["id"], fields=changes)
            except (OSError, ValueError) as error:
                self._error(error)

    def _commit(self, **changes):
        metadata = edit_media_metadata(self._metadata(), **changes)
        replace_specimen_metadata(self.project, self.scene, self.commands, self.identity, metadata)
        self.refresh()

    def _error(self, error):
        self.status.setText(str(error))

    def _import(self):
        project = self.project.current_file
        if project is None:
            self._error(self.tr("Save the project before attaching files."))
            return
        source, _ = QFileDialog.getOpenFileName(self, self.tr("Add photo or document"), str(project.parent))
        if not source:
            return
        instance = self._metadata().get("plant_instance", {})
        options = {"folder": instance.get("media_folder"), "kind": self.kind.currentData(),
                   "caption": self.caption.text().strip(), "source_url": self.origin.text().strip(),
                   "credit": self.credit.text().strip(), "taken_at": self.taken_at.text().strip() or None}
        self.import_button.setEnabled(False)
        def imported(asset, error):
            self.import_button.setEnabled(True)
            if error:
                self._error(error)
                return
            if self.project.current_file != project:
                self._error(self.tr("The project changed during import; no association was saved."))
                return
            try:
                self._commit(asset=asset)
            except (OSError, ValueError) as error:
                self._error(error)
        self._run(lambda: import_specimen_media(source, project, self.identity, **options), imported)

    def _remove(self):
        try:
            for asset in self._selected():
                self._commit(remove_id=asset["id"])
        except (OSError, ValueError) as error:
            self._error(error)

    def _primary(self):
        selected = self._selected()
        if len(selected) == 1:
            try:
                self._commit(primary_id=selected[0]["id"])
            except (OSError, ValueError) as error:
                self._error(error)

    def _open(self):
        selected = self._selected()[:2]
        project = self.project.current_file
        if not selected or project is None:
            return
        if len(selected) == 1 and selected[0].get("kind") == "document":
            try:
                path = resolve_media(project, selected[0]["photo_path"])
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            except (OSError, ValueError) as error:
                self._error(error)
            return
        popup = QDialog(self)
        popup.setWindowTitle(self.tr("Photos"))
        popup.resize(1100, 750)
        root = QVBoxLayout(popup)
        layout = QHBoxLayout()
        root.addLayout(layout, 1)
        labels = []
        for _asset in selected:
            label = QLabel(self.tr("Loading…"))
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label)
            labels.append(label)
        generation = [0]
        def show_assets(assets):
            generation[0] += 1
            current_generation = generation[0]
            for label, asset in zip(labels, assets, strict=True):
                label.clear()
                label.setText(self.tr("Loading…"))
                def loaded(image, error, label=label, current_generation=current_generation):
                    if sip.isdeleted(label) or generation[0] != current_generation:
                        return
                    if error:
                        label.setText(self.tr("Missing or unreadable file"))
                    else:
                        label.setPixmap(QPixmap.fromImage(image))
                self._run(lambda asset=asset: _read_image(project, asset["photo_path"], QSize(1000 // len(selected), 650)), loaded)
        if len(selected) == 1:
            photos = [asset for asset in self._assets if asset.get("kind") != "document"]
            position = [next(i for i, asset in enumerate(photos) if asset["id"] == selected[0]["id"])]
            actions = QHBoxLayout()
            def navigate(delta):
                if sip.isdeleted(popup):
                    return
                position[0] = (position[0] + delta) % len(photos)
                show_assets([photos[position[0]]])
            previous = QPushButton(self.tr("Previous"))
            previous.setShortcut("Left")
            previous.clicked.connect(lambda: navigate(-1))
            following = QPushButton(self.tr("Next"))
            following.setShortcut("Right")
            following.clicked.connect(lambda: navigate(1))
            actions.addWidget(previous)
            actions.addWidget(following)
            root.addLayout(actions)
        show_assets(selected)
        popup.exec()
        popup.deleteLater()

    def _open_folder(self):
        selected = self._selected()
        project = self.project.current_file
        if selected and project is not None:
            try:
                path = resolve_media(project, selected[0]["photo_path"])
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
            except (OSError, ValueError) as error:
                self._error(error)

    def _choose_folder(self):
        project = self.project.current_file
        if project is None:
            return
        folder = QFileDialog.getExistingDirectory(self, self.tr("Choose photo folder"), str(project.parent))
        if folder:
            try:
                relative = Path(folder).resolve().relative_to(project.parent.resolve())
                metadata = self._metadata()
                metadata.setdefault("plant_instance", {})["media_folder"] = relative.as_posix()
                replace_specimen_metadata(self.project, self.scene, self.commands, self.identity, metadata)
            except (OSError, ValueError) as error:
                self._error(error)
