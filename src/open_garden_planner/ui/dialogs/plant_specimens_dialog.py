"""Catalogue access to project-owned specimens without a canvas selection."""

from PyQt6.QtCore import QPointF, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from open_garden_planner.core.plant_specimens import SpecimenEditCommand
from open_garden_planner.models.garden_memory import period
from open_garden_planner.services.plant_specimens import (
    add_specimen,
    prepare_personal_specimen_fields,
    specimen_record,
)
from open_garden_planner.ui.dialogs.copyable_error_dialog import show_copyable_error


class PlantSpecimensDialog(QDialog):
    show_on_plan = pyqtSignal(object)
    catalogue_saved = pyqtSignal()

    def __init__(self, library_id, project, scene, commands, parent=None, *, species=None, embedded=False, library=None):
        super().__init__(parent)
        self.embedded = embedded
        self.library = library
        self._revision = library.revision if library else None
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        self.setWindowTitle(self.tr("My specimens"))
        self.resize(760, 700)
        self.library_id, self.project, self.scene, self.commands = library_id, project, scene, commands
        self.species = species
        self._placed = {}
        self._identities = []
        self._initial_fields = {}
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setMaximumHeight(140)
        layout.addWidget(self.list)
        self.quantity = QSpinBox()
        self.quantity.setRange(0, 1000000)
        if library:
            self.setWindowTitle(self.tr("Quantity and personal details"))
            self.list.hide()
            quantity_form = QFormLayout()
            quantity_form.addRow(self.tr("Quantity"), self.quantity)
            layout.addLayout(quantity_form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.name = QLineEdit()
        self.location = QLineEdit()
        self.supplier = QLineEdit()
        self.relation = QComboBox()
        for key, label in (("study", self.tr("To study")), ("wanted", self.tr("Wanted")),
                           ("ordered", self.tr("Ordered")), ("owned", self.tr("Owned")),
                           ("archived", self.tr("Archived"))):
            self.relation.addItem(label, key)
        self.planting_period = QLineEdit()
        self.planting_period.setPlaceholderText(self.tr("Unknown, YYYY-MM-DD, YYYY-MM, season or year"))
        self.mode = QComboBox()
        for key, label in (("unknown", self.tr("Unknown")), ("ground", self.tr("In ground")),
                           ("pot", self.tr("Pot with bottom")), ("bottomless", self.tr("Bottomless container")),
                           ("other", self.tr("Other"))):
            self.mode.addItem(label, key)
        self.state = QComboBox()
        for key, label in (("planned", self.tr("Awaiting planting")), ("in_place", self.tr("In place")),
                           ("dormant", self.tr("Dormant")), ("uncertain", self.tr("Uncertain")),
                           ("confirmed_dead", self.tr("Confirmed dead")),
                           ("removed_moved", self.tr("Removed or given away"))):
            self.state.addItem(label, key)
        form.addRow(self.tr("Nickname"), self.name)
        form.addRow(self.tr("Catalogue relation"), self.relation)
        form.addRow(self.tr("Cultivation mode"), self.mode)
        form.addRow(self.tr("State"), self.state)
        form.addRow(self.tr("Location without plan"), self.location)
        form.addRow(self.tr("Actual planting date or period"), self.planting_period)
        content_layout.addLayout(form)
        self.text_fields = {"location": self.location, "acquisition_supplier": self.supplier}
        acquisition = QGroupBox(self.tr("Acquisition"))
        acquisition_form = QFormLayout(acquisition)
        acquisition_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.acquisition_period = QLineEdit()
        self.acquisition_period.setPlaceholderText(self.tr("Unknown, YYYY-MM-DD, YYYY-MM, season or year"))
        self.price = QLineEdit()
        self.price.setPlaceholderText(self.tr("Unknown"))
        self.currency = QLineEdit()
        self.receipt = QComboBox()
        acquisition_form.addRow(self.tr("Acquisition date or period"), self.acquisition_period)
        acquisition_form.addRow(self.tr("Acquisition supplier"), self.supplier)
        acquisition_form.addRow(self.tr("Price actually paid"), self.price)
        acquisition_form.addRow(self.tr("Currency"), self.currency)
        acquisition_form.addRow(self.tr("Receipt from attached documents"), self.receipt)
        receipt_button = QPushButton(self.tr("Attach or choose receipt…"))
        receipt_button.clicked.connect(lambda: self._photos(receipt=True))
        acquisition_form.addRow(receipt_button)
        content_layout.addWidget(acquisition)
        for title, fields in (
            (self.tr("Local growing conditions"), (("container", self.tr("Container")), ("substrate", self.tr("Substrate")))),
            (self.tr("External identifiers"), (("old_number", self.tr("Old number")), ("alias", self.tr("Alias")), ("nfc_tag", self.tr("NFC tag")))),
        ):
            group = QGroupBox(title)
            group_form = QFormLayout(group)
            group_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            for key, label in fields:
                edit = QLineEdit()
                self.text_fields[key] = edit
                group_form.addRow(label, edit)
            content_layout.addWidget(group)
        self.notes = QPlainTextEdit()
        self.notes.setMaximumHeight(90)
        content_layout.addWidget(QLabel(self.tr("Personal notes")))
        content_layout.addWidget(self.notes)
        self.status = QLabel()
        layout.addWidget(self.status)
        buttons = QGridLayout()
        add = QPushButton(self.tr("Add specimen"))
        add.clicked.connect(self._add)
        save = QPushButton(self.tr("Save specimen"))
        save.clicked.connect(self._save)
        place = QPushButton(self.tr("Place on plan"))
        place.clicked.connect(self._place)
        show = QPushButton(self.tr("Show on plan"))
        show.clicked.connect(self._show)
        photos = QPushButton(self.tr("Photos and documents"))
        photos.clicked.connect(self._photos)
        measurements = QPushButton(self.tr("Measurements"))
        measurements.clicked.connect(self._measurements)
        records = QPushButton(self.tr("Observations and interventions"))
        records.clicked.connect(self._records)
        repot = QPushButton(self.tr("Record repotting"))
        repot.clicked.connect(lambda: self._intervention("observation", self.tr("Repotting")))
        relocate = QPushButton(self.tr("Record relocation"))
        relocate.clicked.connect(lambda: self._intervention("relocation", ""))
        close = QPushButton(self.tr("Close"))
        close.clicked.connect(self.accept)
        actions = [add, save, place, show, photos, measurements, records, repot, relocate]
        if library:
            for button in actions:
                button.setVisible(button is save)
            save.setText(self.tr("Save"))
        if not embedded:
            actions.append(close)
        for index, button in enumerate(actions):
            buttons.addWidget(button, index // 4, index % 4)
        layout.addLayout(buttons)
        self.list.currentRowChanged.connect(self._select)
        self._reload()

    def _reload(self, selected_id=None):
        row = self.list.currentRow()
        if selected_id is None and 0 <= row < len(self._identities):
            selected_id = self._identities[row]
        self.list.clear()
        self._identities = []
        self._placed = {}
        if self.library:
            from open_garden_planner.services.plant_catalog_filters import catalogue_personal_record

            plant = self.library.get_plant(self.library_id)
            self._catalogue_record, quantity = catalogue_personal_record(plant, self.library_id)
            self._catalogue_documents()
            self.quantity.setValue(quantity)
            identity = self._catalogue_record["item_id"]
            self._identities = [identity]
            self.list.addItem(self._catalogue_record["name"])
            self.list.setCurrentRow(0)
            return
        for identity, record in self.project.plant_specimens.items():
            if record.get("metadata", {}).get("plant_instance", {}).get("library_plant_id") == self.library_id:
                self._identities.append(identity)
                self.list.addItem(record["name"])
        for item in self.scene.items():
            metadata = getattr(item, "metadata", {})
            if (metadata.get("plant_instance", {}).get("library_plant_id") == self.library_id
                    and hasattr(item, "item_id")):
                identity = str(item.item_id)
                if identity not in self._identities:
                    self._identities.append(identity)
                    self._placed[identity] = item
                    self.list.addItem(item.name + " — " + self.tr("On plan"))
        if self._identities:
            self.list.setCurrentRow(self._identities.index(selected_id) if selected_id in self._identities else 0)

    def _select(self, row):
        if row < 0 or row >= len(self._identities):
            return
        identity = self._identities[row]
        item = self._placed.get(identity)
        record = (self._catalogue_record if self.library else
                  {"name": item.name, "metadata": item.metadata} if item else self.project.plant_specimens[identity])
        instance = record["metadata"]["plant_instance"]
        self.name.setText(record["name"])
        for key, edit in self.text_fields.items():
            value = instance.get(key, "")
            edit.setText(value if isinstance(value, str) else "")
            edit.setEnabled(isinstance(value, str))
        self.notes.setPlainText(instance.get("notes", "") if isinstance(instance.get("notes", ""), str) else "")
        self.notes.setEnabled(isinstance(instance.get("notes", ""), str))
        for combo, value in ((self.mode, instance.get("cultivation_mode", "unknown")),
                             (self.relation, instance.get("relation", "owned")),
                             (self.state, record["metadata"].get("garden_tracking", {}).get("state", "uncertain"))):
            index = combo.findData(value)
            if index < 0:
                combo.addItem(self.tr("Unrecognized value (preserved)"), value)
                index = combo.count() - 1
            combo.setCurrentIndex(index)
        planting = record["metadata"].get("garden_tracking", {}).get("period", instance.get("planting_date"))
        acquisition = instance.get("acquisition_period", instance.get("acquisition_date"))
        for edit, value in ((self.planting_period, planting), (self.acquisition_period, acquisition)):
            try:
                occurrence = period(value)
                edit.setText(occurrence["label"] or occurrence["value"])
                edit.setEnabled(True)
            except (TypeError, ValueError):
                edit.clear()
                edit.setEnabled(False)
                self.status.setText(self.tr("An unrecognized period was preserved and cannot be edited here."))
        price = instance.get("acquisition_price")
        self.price.setText("" if price is None else str(price))
        self.price.setEnabled(price is None or isinstance(price, (str, int, float)))
        currency = instance.get("acquisition_currency", "")
        self.currency.setText(currency if isinstance(currency, str) else "")
        self.currency.setEnabled(isinstance(currency, str))
        self._refresh_receipts(instance)
        self._initial_fields = self._field_values()

    def _refresh_receipts(self, instance=None):
        preserve_selection = instance is None
        if instance is None:
            row = self.list.currentRow()
            if row < 0:
                return
            record = specimen_record(self.project.snapshot_dict(self.scene), self._identities[row])
            instance = record["metadata"]["plant_instance"]
        selected = (self.receipt.currentData() if preserve_selection and self.receipt.count()
                    else instance.get("acquisition_receipt_media_id"))
        self.receipt.clear()
        self.receipt.addItem(self.tr("No receipt selected"), None)
        for asset in instance.get("media_assets", []):
            if isinstance(asset, dict) and asset.get("kind") == "document":
                self.receipt.addItem(asset.get("caption") or asset.get("photo_path", ""), asset.get("id"))
        index = self.receipt.findData(selected)
        if selected is not None and index < 0:
            self.receipt.addItem(self.tr("Missing receipt association (preserved)"), selected)
            index = self.receipt.count() - 1
        self.receipt.setCurrentIndex(max(0, index))

    def _field_values(self):
        return {"name": self.name.text().strip(), "relation": self.relation.currentData(),
                "cultivation_mode": self.mode.currentData(), "state": self.state.currentData(),
                "planting_period": self.planting_period.text().strip(),
                "acquisition_period": self.acquisition_period.text().strip(),
                "acquisition_price": self.price.text().strip(), "acquisition_currency": self.currency.text().strip(),
                "acquisition_receipt_media_id": self.receipt.currentData(),
                "notes": self.notes.toPlainText(),
                **{key: edit.text().strip() for key, edit in self.text_fields.items()}}

    def _add(self):
        name, accepted = QInputDialog.getText(self, self.tr("Add specimen"), self.tr("Nickname"))
        if not accepted or not name.strip():
            return
        try:
            candidate, identity = add_specimen(self.project.snapshot_dict(self.scene), self.library_id, name.strip(),
                                        species_data=self.species)
            self.commands.execute(SpecimenEditCommand(self.project, candidate))
            self._reload(selected_id=identity)
        except (OSError, ValueError) as error:
            show_copyable_error(self, self.tr("Could not save specimen"), str(error))

    def _save(self):
        row = self.list.currentRow()
        if row < 0 or not self.name.text().strip():
            return
        identity = self._identities[row]
        item = self._placed.get(identity)
        values = self._field_values()
        changed = {key: value for key, value in values.items() if value != self._initial_fields.get(key)}
        if self.library:
            self._save_catalogue(changed)
            return
        if not changed:
            return
        try:
            candidate = prepare_personal_specimen_fields(self.project.snapshot_dict(self.scene), identity, changed)
            record = specimen_record(candidate, identity)
            if item is not None:
                from open_garden_planner.core.garden_memory import GardenEditCommand

                self.commands.execute(GardenEditCommand(self.project, candidate.get("garden_memory", self.project.garden_memory),
                                                        item, record["metadata"], record["name"]))
            else:
                self.commands.execute(SpecimenEditCommand(self.project, candidate))
            self._reload()
        except (OSError, ValueError) as error:
            show_copyable_error(self, self.tr("Could not save specimen"), str(error))

    def sync_catalogue(self):
        """Accept a sibling editor's committed revision, retaining pending fields."""
        from open_garden_planner.services.plant_catalog_filters import catalogue_personal_record

        plant = self.library.get_plant(self.library_id)
        record, _quantity = catalogue_personal_record(plant, self.library_id)
        record["item_id"] = self._identities[0]
        self._catalogue_record = record
        self._catalogue_documents()
        self._revision = self.library.revision
        # Reflect a sibling's relation edit unless this control has a pending edit.
        if self.relation.currentData() == self._initial_fields.get("relation"):
            relation = record["metadata"]["plant_instance"].get("relation", "owned")
            self.relation.setCurrentIndex(self.relation.findData(relation))
            self._initial_fields["relation"] = self.relation.currentData()

    def _catalogue_documents(self):
        from open_garden_planner.services.botanical_media import reference_assets

        instance = self._catalogue_record["metadata"]["plant_instance"]
        assets = instance.setdefault("media_assets", [])
        known = {asset.get("id") for asset in assets if isinstance(asset, dict)}
        assets.extend(asset for asset in reference_assets(self.library.get_plant(self.library_id))
                      if asset.get("kind") == "document" and asset.get("id") not in known)

    def _save_catalogue(self, changed):
        from copy import deepcopy

        from open_garden_planner.services.library_operations import LibraryOperationError
        from open_garden_planner.services.plant_catalog_filters import update_catalogue_relations

        try:
            identity = self._identities[0]
            candidate = prepare_personal_specimen_fields(
                {"plant_specimens": {identity: self._catalogue_record}}, identity, changed)
            record = candidate["plant_specimens"][identity]
            plant = deepcopy(self.library.get_plant(self.library_id))
            previous = self._catalogue_record["metadata"]["plant_instance"].get("relation", "owned")
            selected = record["metadata"]["plant_instance"].get("relation", "owned")
            if selected != previous:
                from open_garden_planner.services.plant_catalog_filters import (
                    CATALOG_RELATIONS,
                    catalogue_relations,
                )

                relations = (catalogue_relations(plant) & set(CATALOG_RELATIONS)) - {previous}
                plant = update_catalogue_relations(plant, relations | {selected})
            personal = deepcopy(plant.raw_data.get("catalog_personal", {}))
            personal.update(quantity=self.quantity.value(), record=record)
            plant.raw_data["catalog_personal"] = personal
            if not self.library.update_plant(self.library_id, plant, expected_revision=self._revision):
                raise ValueError("The botanical record no longer exists")
            self._revision = self.library.revision
            self._reload()
            self.catalogue_saved.emit()
        except (OSError, ValueError, LibraryOperationError) as error:
            show_copyable_error(self, self.tr("Could not save specimen"), str(error))

    def _place(self):
        row = self.list.currentRow()
        if row < 0 or self._identities[row] in self._placed:
            return
        x, accepted = QInputDialog.getDouble(self, self.tr("Place on plan"), self.tr("X (cm)"),
                                            0, -1000000, 1000000, 2)
        if not accepted:
            return
        y, accepted = QInputDialog.getDouble(self, self.tr("Place on plan"), self.tr("Y (cm)"),
                                            0, -1000000, 1000000, 2)
        if not accepted:
            return
        try:
            from open_garden_planner.ui.specimen_placement import place_specimen

            item = place_specimen(self.project, self.scene, self.commands, self._identities[row],
                                  QPointF(x, y), species=self.species)
            self._reload()
            self.show_on_plan.emit(item)
            if not self.embedded:
                self.accept()
        except (OSError, ValueError) as error:
            show_copyable_error(self, self.tr("Could not save specimen"), str(error))

    def _show(self):
        row = self.list.currentRow()
        if row >= 0:
            item = self._placed.get(self._identities[row])
            if item is not None:
                self.show_on_plan.emit(item)
                if not self.embedded:
                    self.accept()

    def _photos(self, _checked=False, *, receipt=False):
        row = self.list.currentRow()
        if row < 0:
            return
        if self.library:
            from open_garden_planner.ui.widgets.botanical_reference_gallery import (
                BotanicalReferenceGallery,
            )

            popup = QDialog(self)
            popup.setWindowTitle(self.tr("Photos and documents"))
            popup.resize(1000, 700)
            layout = QVBoxLayout(popup)
            gallery = BotanicalReferenceGallery(self.library, self.library_id, popup)
            gallery.changed.connect(self.catalogue_saved)
            if receipt:
                gallery.kind.setCurrentIndex(gallery.kind.findData("document"))
            layout.addWidget(gallery)
            popup.exec()
            popup.deleteLater()
            self._catalogue_documents()
            self._refresh_receipts(self._catalogue_record["metadata"]["plant_instance"])
            self._revision = self.library.revision
            return
        from open_garden_planner.ui.widgets.plant_media_gallery import PlantMediaGallery

        popup = QDialog(self)
        popup.setWindowTitle(self.tr("Photos and documents"))
        popup.resize(1100, 750)
        layout = QVBoxLayout(popup)
        gallery = PlantMediaGallery(self.project, self.scene, self.commands, self._identities[row], popup)
        if receipt:
            gallery.kind.setCurrentIndex(gallery.kind.findData("document"))
        layout.addWidget(gallery)
        popup.exec()
        popup.deleteLater()
        self._refresh_receipts()

    def _measurements(self):
        row = self.list.currentRow()
        if row < 0:
            return
        from open_garden_planner.core.garden_memory import GardenMemoryService
        from open_garden_planner.core.plant_specimens import ProjectSpecimenHandle
        from open_garden_planner.ui.dialogs.plant_measurements_dialog import PlantMeasurementsDialog

        identity = self._identities[row]
        item = self._placed.get(identity) or ProjectSpecimenHandle(self.project, identity)
        service = GardenMemoryService(self.project, self.commands, self.scene)
        PlantMeasurementsDialog(service, item, self).exec()

    def _records(self):
        row = self.list.currentRow()
        if row < 0:
            return
        from open_garden_planner.core.garden_memory import GardenMemoryService
        from open_garden_planner.ui.dialogs.garden_memory_dialog import GardenMemoryDialog

        service = GardenMemoryService(self.project, self.commands, self.scene)
        GardenMemoryDialog(service, self._identities[row], self).exec()

    def _intervention(self, event_type, description):
        row = self.list.currentRow()
        if row < 0:
            return
        from uuid import uuid4

        from open_garden_planner.core.garden_memory import GardenMemoryService
        from open_garden_planner.ui.dialogs.garden_memory_dialog import EventEditor

        identity = self._identities[row]
        record = specimen_record(self.project.snapshot_dict(self.scene), identity)
        editor = EventEditor([{"id": identity, "name": record["name"]}],
                             {"type": event_type, "description": description}, self,
                             project=self.project.current_file)
        if editor.exec() == QDialog.DialogCode.Accepted and editor.value:
            try:
                GardenMemoryService(self.project, self.commands, self.scene).add_event(editor.value, str(uuid4()))
            except (OSError, ValueError, TypeError) as error:
                show_copyable_error(self, self.tr("Could not save specimen"), str(error))
