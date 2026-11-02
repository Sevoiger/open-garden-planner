"""Review species and personal photos before printing catalogue sheets."""
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from open_garden_planner.app.paths import default_dialog_dir
from open_garden_planner.services.garden_queries import garden_objects
from open_garden_planner.services.library_operations import LibraryOperationError
from open_garden_planner.services.pdf_report_service import PdfReportService


class PlantCatalogExportDialog(QDialog):
    def __init__(self, entries, *, selected_ids=(), project=None, scene=None, parent=None, library_path=None):
        super().__init__(parent)
        self.entries, self.project, self.scene = list(entries), project, scene
        self.library_path = library_path
        self.exported = None
        self.setWindowTitle(self.tr("Export plant sheets"))
        self.resize(660, 570)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(self.tr("Choose the botanical sheets to export.")))
        self.species_list = QListWidget()
        for identity, plant in self.entries:
            item = QListWidgetItem(plant.common_name or plant.scientific_name)
            item.setData(Qt.ItemDataRole.UserRole, identity)
            item.setCheckState(Qt.CheckState.Checked if identity in selected_ids else Qt.CheckState.Unchecked)
            self.species_list.addItem(item)
        layout.addWidget(self.species_list, 1)
        self.personal = QCheckBox(self.tr("Include my specimens, measurements and observations"))
        self.personal.setEnabled(project is not None and scene is not None)
        layout.addWidget(self.personal)
        layout.addWidget(QLabel(self.tr("Include only the personal photos checked below.")))
        self.photos = QListWidget()
        self.photos.setEnabled(False)
        layout.addWidget(self.photos, 1)
        self.personal.toggled.connect(self.photos.setEnabled)
        self.species_list.itemChanged.connect(self._refresh_photos)
        actions = QHBoxLayout()
        cancel = QPushButton(self.tr("Cancel"))
        cancel.clicked.connect(self.reject)
        export = QPushButton(self.tr("Export PDF"))
        export.clicked.connect(self._export)
        actions.addWidget(cancel)
        actions.addWidget(export)
        layout.addLayout(actions)
        self._refresh_photos()

    def _selected_ids(self):
        return {self.species_list.item(row).data(Qt.ItemDataRole.UserRole)
                for row in range(self.species_list.count())
                if self.species_list.item(row).checkState() == Qt.CheckState.Checked}

    def _refresh_photos(self, _item=None):
        previously = {self.photos.item(row).data(Qt.ItemDataRole.UserRole) for row in range(self.photos.count())
                      if self.photos.item(row).checkState() == Qt.CheckState.Checked}
        self.photos.clear()
        if self.project is None or self.scene is None:
            return
        selected = self._selected_ids()
        for record in garden_objects(self.project.snapshot_dict(self.scene)):
            instance = record.get("metadata", {}).get("plant_instance", {})
            if instance.get("library_plant_id") not in selected:
                continue
            for asset in instance.get("media_assets", []):
                if asset.get("kind") == "personal":
                    caption = asset.get("caption") or Path(asset.get("photo_path", "")).name
                    item = QListWidgetItem(record.get("name", "") + " - " + caption)
                    selection = (record["item_id"], asset["id"])
                    item.setData(Qt.ItemDataRole.UserRole, selection)
                    item.setCheckState(Qt.CheckState.Checked if selection in previously else Qt.CheckState.Unchecked)
                    self.photos.addItem(item)

    def _export(self):
        selected = self._selected_ids()
        entries = [(identity, plant) for identity, plant in self.entries if identity in selected]
        if not entries:
            QMessageBox.information(self, self.tr("Export plant sheets"), self.tr("Select at least one sheet."))
            return
        project_file = self.project.current_file if self.project is not None else None
        destination, _ = QFileDialog.getSaveFileName(self, self.tr("Export PDF"),
                                                     str(default_dialog_dir(project_file) / "plant-sheets.pdf"),
                                                     self.tr("PDF files (*.pdf)"))
        if not destination:
            return
        try:
            snapshot = self.project.snapshot_dict(self.scene) if self.project is not None and self.scene is not None else None
            photos = {self.photos.item(row).data(Qt.ItemDataRole.UserRole) for row in range(self.photos.count())
                      if self.photos.item(row).checkState() == Qt.CheckState.Checked}
            PdfReportService.export_botanical_catalog(entries, destination, snapshot=snapshot,
                                                       project_file=project_file, selected_photos=photos,
                                                       include_personal=self.personal.isChecked(), library_path=self.library_path)
            self.exported = Path(destination)
            self.accept()
        except (OSError, ValueError, LibraryOperationError) as error:
            QMessageBox.warning(self, self.tr("Could not export plant sheets"), str(error))
