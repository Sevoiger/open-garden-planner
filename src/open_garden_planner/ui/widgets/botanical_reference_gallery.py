"""Offline botanical reference gallery using the existing personal library."""
from pathlib import Path

from PyQt6 import sip
from PyQt6.QtCore import QSize, Qt, QThreadPool, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from open_garden_planner.services.botanical_media import (
    edit_reference_media,
    import_reference_media,
    reference_assets,
    resolve_reference_media,
)
from open_garden_planner.ui.widgets.plant_media_gallery import (
    _MediaTask,
    _read_image,
    _read_thumbnail,
)


class BotanicalReferenceGallery(QWidget):
    changed = pyqtSignal()

    def __init__(self, library, library_id, parent=None):
        super().__init__(parent)
        self.library, self.library_id = library, library_id
        self._assets, self._jobs, self._generation = [], {}, 0
        layout = QVBoxLayout(self)
        hint = QLabel(self.tr("Reference photos and plan illustrations are separate from personal garden photos. Files are imported only when you choose them."))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.gallery = QListWidget()
        self.gallery.setViewMode(QListWidget.ViewMode.IconMode)
        self.gallery.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.gallery.setIconSize(QSize(160, 120))
        self.gallery.itemDoubleClicked.connect(lambda _item: self._open())
        self.gallery.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.gallery, 1)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.kind = QComboBox()
        for key, label in (("reference", self.tr("Reference photo")),
                           ("illustration", self.tr("Plan illustration")), ("document", self.tr("Document"))):
            self.kind.addItem(label, key)
        self.caption, self.origin, self.credit, self.taken_at = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        self.taken_at.setPlaceholderText(self.tr("Unknown, or YYYY-MM-DD"))
        for label, widget in ((self.tr("Media category"), self.kind), (self.tr("Caption"), self.caption),
                              (self.tr("Source URL"), self.origin), (self.tr("Credit"), self.credit),
                              (self.tr("Date taken"), self.taken_at)):
            form.addRow(label, widget)
        layout.addLayout(form)
        actions = QGridLayout()
        self.import_button = QPushButton(self.tr("Add local reference file"))
        self.import_button.clicked.connect(self._import)
        actions.addWidget(self.import_button, 0, 0)
        self.download_button = QPushButton(self.tr("Download source reference photo"))
        self.download_button.clicked.connect(self._download_reference)
        actions.addWidget(self.download_button, 2, 0, 1, 3)
        for index, (label, handler) in enumerate(((self.tr("Open"), self._open),
                                                 (self.tr("Save media details"), self._save_details),
                                                 (self.tr("Use as main reference photo"), self._primary),
                                                 (self.tr("Remove association"), self._remove),
                                                 (self.tr("Open folder"), self._open_folder)), 1):
            button = QPushButton(label)
            button.clicked.connect(handler)
            actions.addWidget(button, index // 3, index % 3)
        layout.addLayout(actions)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.refresh()

    def _plant(self):
        plant = self.library.get_plant(self.library_id)
        if plant is None:
            raise ValueError(self.tr("The botanical record no longer exists."))
        return plant

    def _run(self, operation, callback):
        job = _MediaTask(operation)
        self._jobs[id(job)] = job, callback
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
        try:
            plant = self._plant()
            self._assets = reference_assets(plant)
        except (ValueError, OSError) as error:
            self._assets = []
            self.status.setText(str(error))
            return
        for asset in self._assets:
            title = asset.get("caption") or Path(asset["photo_path"]).name
            if asset["id"] == plant.raw_data.get("reference_primary_media_id"):
                title = "★ " + title
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, asset["id"])
            item.setToolTip(self.tr("Source: {source}\nCredit: {credit}\nDate taken: {taken}\nImported: {imported}").format(
                source=asset.get("source_url", ""), credit=asset.get("credit", ""),
                taken=asset.get("taken_at") or self.tr("Unknown"), imported=asset.get("imported_at", "")))
            self.gallery.addItem(item)
            if asset.get("thumbnail_path"):
                def loaded(image, error, item=item, generation=generation):
                    if generation != self._generation:
                        return
                    if error:
                        item.setText(self.tr("Missing or unreadable file"))
                        item.setToolTip(error)
                    else:
                        item.setIcon(QIcon(QPixmap.fromImage(image)))
                self._run(lambda asset=asset: _read_thumbnail(self.library.path, asset), loaded)
            else:
                try:
                    resolve_reference_media(self.library.path, asset["photo_path"])
                except (ValueError, OSError):
                    item.setText(self.tr("Missing or unreadable file"))

    def _selected(self):
        row = self.gallery.currentItem()
        identity = row.data(Qt.ItemDataRole.UserRole) if row else None
        return next((asset for asset in self._assets if asset["id"] == identity), None)

    def _selection_changed(self):
        asset = self._selected()
        if asset is None:
            return
        self.caption.setText(asset.get("caption", ""))
        self.origin.setText(asset.get("source_url", ""))
        self.credit.setText(asset.get("credit", ""))
        self.taken_at.setText(asset.get("taken_at") or "")
        index = self.kind.findData(asset.get("kind"))
        if index < 0:
            self.kind.addItem(self.tr("Unknown media category"), asset.get("kind"))
            index = self.kind.count() - 1
        self.kind.setCurrentIndex(index)
        self._detail_initial = {"kind": self.kind.currentData(), "caption": self.caption.text(),
                                "source_url": self.origin.text(), "credit": self.credit.text(),
                                "taken_at": self.taken_at.text() or None}
        self._detail_revision = self.library.revision

    def _commit(self, *, expected_revision=None, **changes):
        revision = self.library.revision if expected_revision is None else expected_revision
        plant = edit_reference_media(self._plant(), **changes)
        if not self.library.update_plant(self.library_id, plant, expected_revision=revision):
            raise ValueError(self.tr("The botanical record no longer exists."))
        self.refresh()
        self.changed.emit()

    def _import(self):
        source, _ = QFileDialog.getOpenFileName(self, self.tr("Add local reference file"), str(self.library.path.parent))
        if not source:
            return
        try:
            revision = self.library.revision
        except (ValueError, OSError, RuntimeError) as error:
            self.status.setText(str(error))
            return
        fields = {"kind": self.kind.currentData(), "caption": self.caption.text().strip(),
                  "source_url": self.origin.text().strip(), "credit": self.credit.text().strip(),
                  "taken_at": self.taken_at.text().strip() or None}
        self.import_button.setEnabled(False)
        def imported(asset, error):
            self.import_button.setEnabled(True)
            if error:
                self.status.setText(error)
                return
            try:
                self._commit(asset=asset, expected_revision=revision)
            except (ValueError, OSError, RuntimeError) as error:
                self.status.setText(str(error))
        self._run(lambda: import_reference_media(source, self.library.path, self.library_id, **fields), imported)

    def _primary(self):
        asset = self._selected()
        if asset:
            try:
                self._commit(primary_id=asset["id"])
            except (ValueError, OSError, RuntimeError) as error:
                self.status.setText(str(error))

    def _download_reference(self):
        try:
            plant = self._plant()
            raw = plant.raw_data if isinstance(plant.raw_data, dict) else {}
            provider = raw.get("promesse_de_fleurs", {})
            provider = provider if isinstance(provider, dict) else {}
            local_sheet = raw.get("local_sheet", {})
            local_sheet = local_sheet if isinstance(local_sheet, dict) else {}
            from open_garden_planner.services.plant_source_import import (
                validate_reference_photo_url,
                validate_source_url,
            )

            default_url = ""
            for candidate_url in (provider.get("image_url", ""), plant.image_url):
                if not candidate_url:
                    continue
                try:
                    validate_reference_photo_url(candidate_url)
                except (TypeError, ValueError):
                    continue
                default_url = candidate_url
                break
            page_url = provider.get("source_url", "") or local_sheet.get("url", "")
            try:
                validate_source_url(page_url)
                saved_page_is_provider = True
            except (TypeError, ValueError):
                saved_page_is_provider = False
            revision = self.library.revision
        except (ValueError, OSError, RuntimeError) as error:
            self.status.setText(str(error))
            return
        if saved_page_is_provider:
            from open_garden_planner.services.plant_source_import import (
                ReferencePhotoSelectionRequired,
                import_reference_photo_bytes,
            )
            from open_garden_planner.ui.dialogs.reference_photo_dialog import ReferencePhotoDialog
            from open_garden_planner.ui.widgets.plant_source_worker import reference_photo_request

            caption, credit = self.caption.text().strip(), self.credit.text().strip()
            use_primary = [False]
            self.download_button.setEnabled(False)
            self.status.clear()

            def imported(asset, error):
                self.download_button.setEnabled(True)
                if error:
                    self.status.setText(self.tr("The reference photo could not be downloaded. You can import a local file.") + "\n" + error)
                    return
                if isinstance(asset, ReferencePhotoSelectionRequired):
                    dialog = ReferencePhotoDialog(asset.candidates, self)
                    if not dialog.exec():
                        return
                    candidate, payload = dialog.selected_candidate, dialog.selected_payload
                    use_primary[0] = dialog.use_as_main
                    self.download_button.setEnabled(False)
                    self._run(lambda: import_reference_photo_bytes(
                        payload, self.library.path, self.library_id, source_url=candidate.url,
                        page_url=candidate.source_url, caption=caption or candidate.caption,
                        credit=credit or "Promesse de Fleurs"), imported)
                    return
                if asset is None:
                    self.status.setText(self.tr("The reference photo could not be downloaded. You can import a local file."))
                    return
                try:
                    self._commit(asset=asset, primary_id=asset["id"] if use_primary[0] else None,
                                 expected_revision=revision)
                except (ValueError, OSError, RuntimeError) as error:
                    self.status.setText(str(error))

            self._run(lambda: reference_photo_request(
                plant, self.library.path, self.library_id, caption=caption, credit=credit
            ), imported)
            return
        url, accepted = QInputDialog.getText(self, self.tr("Download source reference photo"),
            self.tr("HTTPS photo URL from Promesse de Fleurs. If retrieval is restricted, choose a local file."),
            text=default_url)
        if not accepted or not url.strip():
            return
        from open_garden_planner.services.plant_source_import import fetch_reference_photo

        caption, credit = self.caption.text().strip(), self.credit.text().strip()
        self.download_button.setEnabled(False)
        def imported(asset, error):
            self.download_button.setEnabled(True)
            if error:
                self.status.setText(self.tr("The reference photo could not be downloaded. You can import a local file.") + "\n" + error)
                return
            try:
                self._commit(asset=asset, expected_revision=revision)
            except (ValueError, OSError, RuntimeError) as error:
                self.status.setText(str(error))
        self._run(lambda: fetch_reference_photo(url.strip(), self.library.path, self.library_id,
                                                caption=caption, credit=credit), imported)

    def _remove(self):
        asset = self._selected()
        if asset:
            try:
                self._commit(remove_id=asset["id"])
            except (ValueError, OSError, RuntimeError) as error:
                self.status.setText(str(error))

    def _save_details(self):
        asset = self._selected()
        if asset:
            try:
                fields = {"kind": self.kind.currentData(),
                    "caption": self.caption.text().strip(), "source_url": self.origin.text().strip(),
                    "credit": self.credit.text().strip(), "taken_at": self.taken_at.text().strip() or None}
                changes = {key: value for key, value in fields.items() if value != self._detail_initial.get(key)}
                if changes:
                    self._commit(update_id=asset["id"], fields=changes, expected_revision=self._detail_revision)
            except (ValueError, OSError, RuntimeError) as error:
                self.status.setText(str(error))

    def _open_folder(self):
        asset = self._selected()
        if asset:
            try:
                path = resolve_reference_media(self.library.path, asset["photo_path"])
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
            except (ValueError, OSError) as error:
                self.status.setText(str(error))

    def _open(self):
        selected = self._selected()
        if selected is None:
            return
        if selected.get("kind") == "document":
            try:
                path = resolve_reference_media(self.library.path, selected["photo_path"])
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            except (ValueError, OSError) as error:
                self.status.setText(str(error))
            return
        assets = [asset for asset in self._assets if asset.get("kind") != "document"]
        position = [next(i for i, asset in enumerate(assets) if asset["id"] == selected["id"])]
        popup = QDialog(self)
        popup.setWindowTitle(self.tr("Reference photos"))
        popup.resize(1040, 740)
        layout = QVBoxLayout(popup)
        label = QLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label, 1)
        actions = QHBoxLayout()
        generation = [0]
        def navigate(delta):
            position[0] = (position[0] + delta) % len(assets)
            generation[0] += 1
            current = generation[0]
            label.clear()
            label.setText(self.tr("Loading…"))
            def loaded(image, error):
                if sip.isdeleted(label) or generation[0] != current:
                    return
                if error:
                    label.setText(self.tr("Missing or unreadable file"))
                else:
                    label.setPixmap(QPixmap.fromImage(image))
            asset = assets[position[0]]
            self._run(lambda: _read_image(self.library.path, asset["photo_path"], QSize(980, 640)), loaded)
        for text, delta in ((self.tr("Previous"), -1), (self.tr("Next"), 1)):
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, delta=delta: navigate(delta))
            actions.addWidget(button)
        layout.addLayout(actions)
        navigate(0)
        popup.exec()
        popup.deleteLater()
