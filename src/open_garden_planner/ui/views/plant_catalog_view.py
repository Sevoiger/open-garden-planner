"""Standalone browser and editor for the user's custom plant species."""

import unicodedata
from copy import deepcopy
from difflib import SequenceMatcher
from pathlib import Path

from PyQt6.QtCore import QCoreApplication, QLocale, QSize, Qt, QThreadPool, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from open_garden_planner.models.plant_data import (
    GrowthRate,
    PlantCycle,
    PlantSpeciesData,
    SunRequirement,
    WaterNeeds,
    species_key,
)
from open_garden_planner.services import PlantLibrary, get_plant_library
from open_garden_planner.services.botanical_profile import trait, update_traits
from open_garden_planner.services.library_operations import (
    LibraryConflictError,
    LibraryOperationError,
)
from open_garden_planner.services.plant_catalog_filters import (
    CATALOG_RELATIONS,
    catalogue_relations,
    filter_catalogue,
    specimen_summaries,
    update_catalogue_relations,
)
from open_garden_planner.ui.theme import set_text_role
from open_garden_planner.ui.widgets.botanical_calendar import BotanicalCalendarWidget
from open_garden_planner.ui.widgets.responsive_dialog import ResponsiveDialog


class PlantSpeciesEditDialog(ResponsiveDialog):
    """Edit the recorded botanical fields for one custom species."""

    def __init__(
        self,
        plant: PlantSpeciesData | None = None,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(
            self.tr("Add Plant to Catalog") if plant is None
            else self.tr("Plant Details")
        )
        self.setMinimumWidth(440)

        self._original = deepcopy(plant) if plant is not None else None
        self.plant: PlantSpeciesData | None = None
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setProperty("error", True)
        self.error_label.hide()

        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.common_name_edit = QLineEdit()
        self.common_name_edit.setObjectName("plantCommonName")
        self.scientific_name_edit = QLineEdit()
        self.scientific_name_edit.setObjectName("plantScientificName")
        self.family_edit = QLineEdit()
        self.genus_edit = QLineEdit()
        form.addRow(self.tr("Common Name"), self.common_name_edit)
        form.addRow(self.tr("Scientific Name"), self.scientific_name_edit)
        form.addRow(self.tr("Family"), self.family_edit)
        form.addRow(self.tr("Genus"), self.genus_edit)

        self.cycle_combo = self._enum_combo(
            PlantCycle,
            {
                PlantCycle.UNKNOWN: self.tr("—"),
                PlantCycle.ANNUAL: self.tr("Annual"),
                PlantCycle.BIENNIAL: self.tr("Biennial"),
                PlantCycle.PERENNIAL: self.tr("Perennial"),
            },
        )
        self.growth_rate_combo = self._enum_combo(
            GrowthRate,
            {
                GrowthRate.UNKNOWN: self.tr("—"),
                GrowthRate.SLOW: self.tr("Slow"),
                GrowthRate.MEDIUM: self.tr("Medium"),
                GrowthRate.FAST: self.tr("Fast"),
            },
        )
        self.sun_combo = self._enum_combo(
            SunRequirement,
            {
                SunRequirement.UNKNOWN: self.tr("—"),
                SunRequirement.FULL_SUN: self.tr("Full Sun"),
                SunRequirement.PARTIAL_SUN: self.tr("Partial Sun"),
                SunRequirement.PARTIAL_SHADE: self.tr("Partial Shade"),
                SunRequirement.FULL_SHADE: self.tr("Full Shade"),
            },
        )
        self.water_combo = self._enum_combo(
            WaterNeeds,
            {
                WaterNeeds.UNKNOWN: self.tr("—"),
                WaterNeeds.LOW: self.tr("Low"),
                WaterNeeds.MEDIUM: self.tr("Medium"),
                WaterNeeds.HIGH: self.tr("High"),
            },
        )
        form.addRow(self.tr("Life Cycle"), self.cycle_combo)
        form.addRow(self.tr("Growth Rate"), self.growth_rate_combo)
        form.addRow(self.tr("Sun Requirement"), self.sun_combo)
        form.addRow(self.tr("Water Needs"), self.water_combo)

        self.max_height_spin = self._optional_number()
        self.max_spread_spin = self._optional_number()
        form.addRow(self.tr("Maximum Height (cm)"), self.max_height_spin)
        form.addRow(self.tr("Maximum Spread (cm)"), self.max_spread_spin)
        self.numeric_fields = {}
        for key, label in (
            ("min_height_cm", self.tr("Minimum adult height (cm)")),
            ("min_spread_cm", self.tr("Minimum adult spread (cm)")),
            ("ph_min", self.tr("Minimum soil pH")), ("ph_max", self.tr("Maximum soil pH")),
            ("hardiness_zone_min", self.tr("Minimum hardiness zone")),
            ("hardiness_zone_max", self.tr("Maximum hardiness zone")),
        ):
            value = getattr(plant, key) if plant is not None else None
            edit = QLineEdit("" if value is None else str(value))
            edit.setPlaceholderText(self.tr("Unknown"))
            self.numeric_fields[key] = (edit, edit.text())
            form.addRow(label, edit)

        self.soil_type_edit = QLineEdit()
        form.addRow(self.tr("Soil Type"), self.soil_type_edit)
        self.flower_color_edit = QLineEdit()
        self.foliage_color_edit = QLineEdit()
        form.addRow(self.tr("Flower Color"), self.flower_color_edit)
        form.addRow(self.tr("Foliage Color"), self.foliage_color_edit)
        self.description_edit = QPlainTextEdit()
        self.description_edit.setMaximumHeight(110)
        form.addRow(self.tr("Description"), self.description_edit)
        body_layout.addLayout(form)
        calendar_heading = QLabel(self.tr("Recommended periods"))
        set_text_role(calendar_heading, "h2")
        body_layout.addWidget(calendar_heading)
        calendar_help = QLabel(self.tr("Select months; an empty row means unknown."))
        calendar_help.setWordWrap(True)
        body_layout.addWidget(calendar_help)
        self.calendar = BotanicalCalendarWidget(editable=True, extended=True)
        body_layout.addWidget(self.calendar)
        relations = QGroupBox(self.tr("My catalogue relations"))
        relations_layout = QHBoxLayout(relations)
        self.relation_checks = {}
        self._relations_initial = catalogue_relations(plant) & set(CATALOG_RELATIONS) if plant else set()
        for key, label in _relation_choices():
            check = QCheckBox(label)
            check.setChecked(key in self._relations_initial)
            self.relation_checks[key] = check
            relations_layout.addWidget(check)
        relations.setToolTip(self.tr("Several relations can apply at once; importing a sheet does not mean you own a specimen."))
        body_layout.addWidget(relations)
        self.trait_edits = {}
        self._trait_initial = {}
        for title, fields in (
            (self.tr("Botanical identity"), (
                ("summary", self.tr("Summary")),
                ("species", self.tr("Species")), ("cultivar", self.tr("Cultivar")),
                ("commercial_name", self.tr("Commercial name")),
                ("common_names", self.tr("Other common names")),
                ("synonyms", self.tr("Synonyms")), ("category", self.tr("Category")),
                ("tags", self.tr("Personal tags")),
            )),
            (self.tr("Appearance and uses"), (
                ("habit", self.tr("Growth habit")),
                ("foliage_persistence", self.tr("Foliage persistence")),
                ("foliage_details", self.tr("Foliage characteristics")),
                ("flower_details", self.tr("Flower characteristics")),
                ("fragrance", self.tr("Fragrance")), ("fruit", self.tr("Fruiting")),
                ("seasonal_interest", self.tr("Seasonal interest")),
                ("spacing", self.tr("Spacing or density with units")),
                ("uses", self.tr("Uses")), ("support", self.tr("Support requirements")),
            )),
            (self.tr("Growing conditions and care"), (
                ("exposures", self.tr("Possible exposures")),
                ("drainage", self.tr("Drainage")), ("soil_richness", self.tr("Soil richness")),
                ("soil_moisture", self.tr("Soil moisture")),
                ("soil_ph", self.tr("Soil pH")), ("hardiness", self.tr("Hardiness")),
                ("climate", self.tr("Climate context")),
                ("drought", self.tr("Drought tolerance")),
                ("excess_water", self.tr("Sensitivity to excess water")),
                ("pot_suitability", self.tr("Suitability for containers")),
                ("planting_advice", self.tr("Planting advice")),
                ("mulching", self.tr("Mulching")), ("care", self.tr("Care")),
                ("pruning_advice", self.tr("Pruning advice")),
                ("pruning_group", self.tr("Pruning group and warnings")),
                ("overwintering", self.tr("Overwintering")),
            )),
            (self.tr("Propagation and precautions"), (
                ("pollination", self.tr("Pollination")),
                ("pollinators", self.tr("Interest for pollinators")),
                ("propagation", self.tr("Propagation")),
                ("edibility", self.tr("Documented edible uses")),
                ("precautions", self.tr("Precautions")),
                ("safety_source", self.tr("Safety source and certainty")),
            )),
        ):
            group = QGroupBox(title)
            group.setCheckable(True)
            group.setChecked(False)
            group_layout = QVBoxLayout(group)
            content = QWidget()
            fields_layout = QFormLayout(content)
            fields_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            for key, label in fields:
                value = trait(plant, key) if plant is not None else ""
                edit = QLineEdit(value)
                edit.setObjectName("botanical_" + key)
                self.trait_edits[key] = edit
                self._trait_initial[key] = value
                fields_layout.addRow(label, edit)
            group_layout.addWidget(content)
            content.setVisible(False)
            group.toggled.connect(content.setVisible)
            body_layout.addWidget(group)
        if plant is not None:
            source_section = QGroupBox(self.tr("Additional information from the source"))
            source_section.setCheckable(True)
            source_section.setChecked(False)
            source_layout = QVBoxLayout(source_section)
            source_content = QWidget()
            source_form = QFormLayout(source_content)
            source_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            for name, label in (
                ("Port", self.tr("Growth habit")),
                ("Persistance feuillage", self.tr("Foliage persistence")),
                ("Rusticité", self.tr("Hardiness")),
                ("pH du sol", self.tr("Soil pH")),
                ("Densité de plantation", self.tr("Planting density")),
                ("Type d'utilisation", self.tr("Uses")),
                ("Descriptif taille", self.tr("Pruning advice")),
            ):
                value = _source_characteristic(plant, name)
                if value:
                    text = QLabel(value)
                    text.setWordWrap(True)
                    source_form.addRow(label, text)
            source_layout.addWidget(source_content)
            source_content.setVisible(False)
            source_section.toggled.connect(source_content.setVisible)
            source_section.setVisible(source_form.rowCount() > 0)
            body_layout.addWidget(source_section)
        layout.addWidget(self.error_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_button = QPushButton(self.tr("Cancel"))
        self.cancel_button.clicked.connect(self.reject)
        self.save_button = QPushButton(self.tr("Save"))
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._save)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)

        self._populate()
        self._height_was_edited = False
        self._spread_was_edited = False
        self.max_height_spin.valueChanged.connect(
            lambda _value: setattr(self, "_height_was_edited", True)
        )
        self.max_spread_spin.valueChanged.connect(
            lambda _value: setattr(self, "_spread_was_edited", True)
        )

    @staticmethod
    def _enum_combo(enum_type: type, labels: dict) -> QComboBox:
        combo = QComboBox()
        for value in enum_type:
            combo.addItem(labels[value], value.value)
        return combo

    def _optional_number(self) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0, 100000)
        spin.setDecimals(1)
        spin.setSpecialValueText(self.tr("—"))
        spin.setSingleStep(1)
        return spin

    def _populate(self) -> None:
        plant = self._original
        if plant is None:
            return
        self.common_name_edit.setText(plant.common_name)
        self.scientific_name_edit.setText(plant.scientific_name)
        self.family_edit.setText(plant.family)
        self.genus_edit.setText(plant.genus)
        for combo, value in (
            (self.cycle_combo, plant.cycle.value),
            (self.growth_rate_combo, plant.growth_rate.value),
            (self.sun_combo, plant.sun_requirement.value),
            (self.water_combo, plant.water_needs.value),
        ):
            combo.setCurrentIndex(max(0, combo.findData(value)))
        self.max_height_spin.setValue(plant.max_height_cm or 0)
        self.max_spread_spin.setValue(plant.max_spread_cm or 0)
        self.soil_type_edit.setText(plant.soil_type)
        self.flower_color_edit.setText(plant.flower_color)
        self.foliage_color_edit.setText(plant.foliage_color)
        self.description_edit.setPlainText(plant.description)
        from open_garden_planner.services.botanical_calendar import calendar_for_species

        self.calendar.set_calendar(calendar_for_species(plant).to_dict())

    def _save(self) -> None:
        common_name = self.common_name_edit.text().strip()
        scientific_name = self.scientific_name_edit.text().strip()
        if not common_name and not scientific_name:
            self.error_label.setText(
                self.tr("Enter a common name or a scientific name.")
            )
            self.error_label.show()
            return

        stored_calendar = (self._original.raw_data.get("botanical_calendar")
                           if self._original is not None else None)
        invalid_calendar = self.calendar.edited_keys() and self._original is not None and (
            ("botanical_calendar" in self._original.raw_data
             and not isinstance(stored_calendar, dict))
            or (isinstance(stored_calendar, dict) and any(
                key in stored_calendar and not isinstance(stored_calendar[key], dict)
                for key in self.calendar.edited_keys()
            ))
        )
        if not invalid_calendar and isinstance(stored_calendar, dict):
            from open_garden_planner.models.botanical_calendar import MonthlyPeriod

            try:
                for key in self.calendar.edited_keys():
                    if key in stored_calendar:
                        MonthlyPeriod.from_dict(stored_calendar[key])
            except (TypeError, ValueError):
                invalid_calendar = True
        if invalid_calendar:
            self.error_label.setText(self.tr(
                "This period uses an unrecognized format and cannot be edited safely."
            ))
            self.error_label.show()
            return

        plant = deepcopy(self._original) if self._original is not None else PlantSpeciesData(
            scientific_name="",
            common_name="",
            data_source="custom",
        )
        plant.common_name = common_name
        plant.scientific_name = scientific_name
        plant.family = self.family_edit.text().strip()
        plant.genus = self.genus_edit.text().strip()
        plant.cycle = PlantCycle(self.cycle_combo.currentData())
        plant.growth_rate = GrowthRate(self.growth_rate_combo.currentData())
        plant.sun_requirement = SunRequirement(self.sun_combo.currentData())
        plant.water_needs = WaterNeeds(self.water_combo.currentData())
        if self._original is None or self._height_was_edited:
            plant.max_height_cm = self.max_height_spin.value() or None
        if self._original is None or self._spread_was_edited:
            plant.max_spread_cm = self.max_spread_spin.value() or None
        plant.soil_type = self.soil_type_edit.text().strip()
        plant.flower_color = self.flower_color_edit.text().strip()
        plant.foliage_color = self.foliage_color_edit.text().strip()
        plant.description = self.description_edit.toPlainText().strip()
        try:
            from open_garden_planner.models.garden_memory import optional_number

            for key, (edit, initial) in self.numeric_fields.items():
                if edit.text() == initial:
                    continue
                number = optional_number(edit.text(), key)
                if number is not None:
                    if key.startswith("ph_") and number > 14:
                        raise ValueError(self.tr("Soil pH must be between 0 and 14."))
                    if key.startswith("hardiness_zone"):
                        if not number.is_integer() or not 1 <= number <= 13:
                            raise ValueError(self.tr("Hardiness zones must be whole numbers from 1 to 13."))
                        number = int(number)
                    if key.startswith("min_") and number <= 0:
                        raise ValueError(self.tr("Adult dimensions must be positive or unknown."))
                setattr(plant, key, number)
        except (ValueError, TypeError) as error:
            self.error_label.setText(str(error))
            self.error_label.show()
            return
        selected_relations = {key for key, check in self.relation_checks.items() if check.isChecked()}
        if selected_relations != self._relations_initial:
            try:
                plant = update_catalogue_relations(plant, selected_relations)
            except ValueError as error:
                self.error_label.setText(str(error))
                self.error_label.show()
                return
        changes = {key: edit.text().strip() for key, edit in self.trait_edits.items()
                   if edit.text().strip() != self._trait_initial[key]}
        if changes:
            try:
                plant = update_traits(plant, changes)
            except ValueError as error:
                self.error_label.setText(str(error))
                self.error_label.show()
                return
        if self.calendar.edited_keys():
            from open_garden_planner.services.botanical_calendar import calendar_for_species

            stored_calendar = plant.raw_data.get("botanical_calendar")
            calendar = (deepcopy(stored_calendar) if isinstance(stored_calendar, dict)
                        else calendar_for_species(plant).to_dict())
            values = self.calendar.months()
            for key in self.calendar.edited_keys():
                period = calendar.get(key)
                if not isinstance(period, dict):
                    period = calendar_for_species(plant).to_dict()[key]
                    calendar[key] = period
                period["months"] = values[key]
                period["manually_edited"] = True
            plant.raw_data["botanical_calendar"] = calendar
        plant.data_source = "custom"
        self.plant = plant
        self.accept()


class LocalPlantImportPreviewDialog(ResponsiveDialog):
    """Review local plant records before adding any of them to the library."""

    def __init__(
        self,
        plants: list[PlantSpeciesData],
        duplicate_indexes: set[int],
        possible_match_indexes: set[int] | None = None,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Preview Local Plant Import"))
        self.setMinimumSize(620, 420)
        self.plants = deepcopy(plants)
        self.duplicate_indexes = set(duplicate_indexes)
        self.possible_match_indexes = set(possible_match_indexes or ())

        layout = QVBoxLayout(self)
        introduction = QLabel(self.tr(
            "Review the species read from this folder. Existing records will be "
            "skipped only when the source ID matches. Similar names are flagged "
            "for comparison. Edit preview records before importing if needed."
        ))
        introduction.setWordWrap(True)
        layout.addWidget(introduction)

        self.table = QTableWidget(len(self.plants), 4)
        self.table.setHorizontalHeaderLabels([
            self.tr("Common Name"),
            self.tr("Scientific Name"),
            self.tr("Family"),
            self.tr("Import Status"),
        ])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        for row, plant in enumerate(self.plants):
            for column, value in enumerate((
                plant.common_name,
                plant.scientific_name,
                plant.family,
                self._row_status(row),
            )):
                self.table.setItem(row, column, QTableWidgetItem(value))
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        actions.addStretch()
        self.edit_button = QPushButton(self.tr("Edit Preview"))
        self.edit_button.setEnabled(False)
        self.edit_button.clicked.connect(self._edit_selected)
        actions.addWidget(self.edit_button)
        layout.addLayout(actions)
        self.table.itemSelectionChanged.connect(self._update_edit_button)
        self.table.itemDoubleClicked.connect(lambda _item: self._edit_selected())

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel_button = QPushButton(self.tr("Cancel"))
        cancel_button.clicked.connect(self.reject)
        self.import_button = QPushButton()
        self.import_button.setDefault(True)
        self.import_button.clicked.connect(self.accept)
        buttons.addWidget(cancel_button)
        buttons.addWidget(self.import_button)
        layout.addLayout(buttons)
        self._refresh_summary()

    @property
    def pending_plants(self) -> list[PlantSpeciesData]:
        return [
            plant for index, plant in enumerate(self.plants)
            if index not in self.duplicate_indexes
        ]

    def _row_status(self, row: int) -> str:
        if row in self.duplicate_indexes:
            return self.tr("Duplicate source ID — will be skipped")
        if row in self.possible_match_indexes:
            return self.tr("Possible name match — compare")
        return self.tr("Ready to import")

    def _update_edit_button(self) -> None:
        row = self.table.currentRow()
        self.edit_button.setEnabled(row >= 0 and row not in self.duplicate_indexes)

    def _edit_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row in self.duplicate_indexes:
            return
        dialog = PlantSpeciesEditDialog(self.plants[row], parent=self)
        if not dialog.exec() or dialog.plant is None:
            return
        self.plants[row] = dialog.plant
        self.table.item(row, 0).setText(dialog.plant.common_name)
        self.table.item(row, 1).setText(dialog.plant.scientific_name)
        self.table.item(row, 2).setText(dialog.plant.family)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        possible_count = len(self.possible_match_indexes - self.duplicate_indexes)
        self.status_label.setText(self.tr(
            "{new_count} records can be imported; {duplicate_count} duplicate source ID(s) "
            "will be skipped; {possible_count} possible name match(es) are flagged."
        ).format(
            new_count=len(self.pending_plants),
            duplicate_count=len(self.duplicate_indexes),
            possible_count=possible_count,
        ))
        self.import_button.setText(self.tr("Import {count} New Species").format(
            count=len(self.pending_plants)
        ))
        self.import_button.setEnabled(bool(self.pending_plants))


class _ActivatingListWidget(QListWidget):
    """Emit the same activation signal for both Enter keys and mouse activation."""

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        if event is not None and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            item = self.currentItem()
            if item is not None:
                self.itemActivated.emit(item)
                event.accept()
                return
        super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent | None) -> None:
        if event is not None:
            item = self.itemAt(event.position().toPoint())
            if item is not None:
                self.setCurrentItem(item)
                self.itemActivated.emit(item)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)


class PlantCatalogView(QWidget):
    """Search, inspect and edit species in the custom plant library."""

    species_saved = pyqtSignal(str)
    show_on_plan = pyqtSignal(object)

    def __init__(
        self,
        library: PlantLibrary | None = None,
        parent: QWidget | None = None,
        *, project=None, scene=None, commands=None,
    ) -> None:
        super().__init__(parent)
        self._library = library or get_plant_library()
        self._project, self._scene, self._commands = project, scene, commands
        self._entries: dict[str, PlantSpeciesData] = {}
        self._thumbnail_generation = 0
        self._thumbnail_jobs = {}
        self._thumbnail_requested = set()
        self.setObjectName("plantCatalogView")

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        header = QHBoxLayout()
        heading = QLabel(self.tr("Botanical Catalog"))
        set_text_role(heading, "h1")
        header.addWidget(heading)
        header.addStretch(1)
        layout.addLayout(header)

        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("plantCatalogSearch")
        self.search_edit.setPlaceholderText(
            self.tr("Search by common name, scientific name, or family")
        )
        self.search_edit.textChanged.connect(self._populate_list)
        search_row.addWidget(self.search_edit)
        self.month_filter = QComboBox()
        self.month_filter.setObjectName("plantCatalogMonthFilter")
        self.month_filter.addItem(self.tr("Any month"), None)
        for month in range(1, 13):
            self.month_filter.addItem(QLocale().monthName(month), month)
        self.add_button = QPushButton(self.tr("Create manually"))
        self.add_button.clicked.connect(self._add_plant)
        header.addWidget(self.add_button)
        self.import_button = QToolButton()
        self.import_button.setText(self.tr("Import file"))
        self.import_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.import_button.clicked.connect(self._import_local_source_file)
        import_menu = QMenu(self.import_button)
        import_file_action = import_menu.addAction(self.tr("Import JSON/HTML File"))
        import_file_action.triggered.connect(self._import_local_source_file)
        self.import_folder_action = import_menu.addAction(self.tr("Import Source Folder"))
        self.import_folder_action.triggered.connect(self._import_local_sources)
        self.import_button.setMenu(import_menu)
        header.addWidget(self.import_button)
        self.url_button = QPushButton(self.tr("Import from URL"))
        self.url_button.clicked.connect(lambda: self._retrieve_source(False))
        header.addWidget(self.url_button)
        self.refresh_source_button = QPushButton(self.tr("Enrich from source"))
        self.refresh_source_button.clicked.connect(lambda: self._retrieve_source(True))
        header.addWidget(self.refresh_source_button)
        self.filters_button = QToolButton()
        self.filters_button.setText(self.tr("Filters"))
        self.filters_button.setCheckable(True)
        self.filters_button.setArrowType(Qt.ArrowType.DownArrow)
        self.filters_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        search_row.addWidget(self.filters_button)
        layout.addLayout(search_row)

        self.filters_widget = QWidget()
        filters = QGridLayout(self.filters_widget)
        filters.setContentsMargins(0, 4, 0, 4)
        filters.setSpacing(10)
        self.category_filter = QComboBox()
        self.category_filter.addItem(self.tr("All categories"), "")
        self.relation_filter = QComboBox()
        self.relation_filter.addItem(self.tr("All relations"), "")
        for code, label in _relation_choices():
            self.relation_filter.addItem(label, code)
        self.relation_filter.addItem(self.tr("Present"), "present")
        self.mode_filter = QComboBox()
        for code, label in (("", self.tr("All cultivation modes")), ("ground", self.tr("In ground")),
                            ("pot", self.tr("Pot with bottom")), ("bottomless", self.tr("Bottomless container")),
                            ("other", self.tr("Other")), ("unknown", self.tr("Unknown"))):
            self.mode_filter.addItem(label, code)
        self.location_filter = QLineEdit()
        self.location_filter.setPlaceholderText(self.tr("Location or bed"))
        self.activity_filter = QComboBox()
        for code, label in (("", self.tr("All calendar activities")), ("flowering", self.tr("Flowering")),
                            ("planting", self.tr("Planting")), ("pruning", self.tr("Pruning")),
                            ("planting_optimal", self.tr("Optimal planting")), ("harvest", self.tr("Harvest and fruiting")),
                            ("sowing", self.tr("Sowing")), ("propagation", self.tr("Propagation"))):
            self.activity_filter.addItem(label, code)
        self.sort_combo = QComboBox()
        for code, label in (("name", self.tr("Sort by common name")), ("botanical", self.tr("Sort by botanical name")),
                            ("category", self.tr("Sort by category")), ("count", self.tr("Sort by specimen count"))):
            self.sort_combo.addItem(label, code)
        self.incomplete_filter = QCheckBox(self.tr("Incomplete sheets"))
        self.incomplete_filter.setToolTip(self.tr("Missing scientific name, adult dimensions, exposure or a known calendar period."))
        self.without_photo_filter = QCheckBox(self.tr("Without personal photo"))
        search_row.addWidget(self.sort_combo)
        for index, widget in enumerate((self.category_filter, self.relation_filter, self.mode_filter,
                                        self.location_filter, self.activity_filter, self.month_filter,
                                        self.incomplete_filter, self.without_photo_filter)):
            filters.addWidget(widget, index // 4, index % 4)
            if isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self._populate_list)
            elif isinstance(widget, QLineEdit):
                widget.textChanged.connect(self._populate_list)
            else:
                widget.toggled.connect(self._populate_list)
        self.sort_combo.currentIndexChanged.connect(self._populate_list)
        self.filters_widget.setVisible(False)
        self.filters_button.toggled.connect(self.filters_widget.setVisible)
        layout.addWidget(self.filters_widget)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.results_list = _ActivatingListWidget()
        from open_garden_planner.ui.widgets.plant_catalog_cards import PlantCatalogCardDelegate

        self.card_delegate = PlantCatalogCardDelegate(self.results_list)
        self.card_delegate.thumbnail_needed.connect(self._request_thumbnail)
        self.results_list.setItemDelegate(self.card_delegate)
        self.results_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results_list.setObjectName("plantCatalogResults")
        self.results_list.setMinimumWidth(340)
        self.results_list.setUniformItemSizes(True)
        self.results_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.results_list.itemSelectionChanged.connect(self._update_preview)
        self.results_list.itemActivated.connect(self._open_selected)
        splitter.addWidget(self.results_list)

        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        preview = QWidget()
        preview.setMinimumWidth(390)
        self.preview_scroll.setWidget(preview)
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(18, 8, 14, 12)
        preview_layout.setSpacing(16)
        identity_row = QHBoxLayout()
        self.preview_photo = QLabel(self.tr("Reference photo"))
        self.preview_photo.setFixedSize(150, 120)
        self.preview_photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_photo.setWordWrap(True)
        self.preview_photo.setProperty("textRole", "hint")
        photo_column = QVBoxLayout()
        photo_column.addWidget(self.preview_photo)
        self.photo_button = QPushButton(self.tr("Get reference photo"))
        self.photo_button.clicked.connect(lambda: self._download_reference_photo(self._selected_id()))
        photo_column.addWidget(self.photo_button)
        identity_row.addLayout(photo_column)
        self._photo_jobs = {}
        identity_text = QVBoxLayout()
        self.preview_name = QLabel(self.tr("Select a species to view its details."))
        self.preview_name.setWordWrap(True)
        set_text_role(self.preview_name, "h2")
        self.preview_name.setTextFormat(Qt.TextFormat.PlainText)
        identity_text.addWidget(self.preview_name)
        self.preview_scientific_name = QLabel()
        self.preview_scientific_name.setWordWrap(True)
        scientific_font = self.preview_scientific_name.font()
        scientific_font.setItalic(True)
        self.preview_scientific_name.setFont(scientific_font)
        self.preview_scientific_name.setTextFormat(Qt.TextFormat.PlainText)
        identity_text.addWidget(self.preview_scientific_name)
        identity_text.addStretch(1)
        identity_row.addLayout(identity_text, 1)
        preview_layout.addLayout(identity_row)
        self.preview_form = QFormLayout()
        self.preview_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.preview_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.preview_form.setVerticalSpacing(10)
        self.preview_form.setHorizontalSpacing(18)
        preview_layout.addLayout(self.preview_form)
        calendar_heading = QLabel(self.tr("Recommended periods"))
        set_text_role(calendar_heading, "h2")
        preview_layout.addWidget(calendar_heading)
        self.preview_calendar = BotanicalCalendarWidget(editable=False, quick=True)
        preview_layout.addWidget(self.preview_calendar)
        self.preview_description = QLabel()
        self.preview_description.setWordWrap(True)
        self.preview_description.setTextFormat(Qt.TextFormat.PlainText)
        self.preview_description.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.preview_description_label = QLabel(self.tr("Description"))
        set_text_role(self.preview_description_label, "hint")
        preview_layout.addWidget(self.preview_description_label)
        preview_layout.addWidget(self.preview_description)
        preview_layout.addStretch(1)
        splitter.addWidget(self.preview_scroll)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([440, 660])
        layout.addWidget(splitter, 1)

        footer = QHBoxLayout()
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        set_text_role(self.status_label, "secondary")
        footer.addWidget(self.status_label, 1)
        self.specimens_button = QPushButton(self.tr("My specimens"))
        self.specimens_button.setEnabled(project is not None)
        self.specimens_button.clicked.connect(self._open_specimens)
        footer.addWidget(self.specimens_button)
        self.export_button = QPushButton(self.tr("Export PDF"))
        self.export_button.clicked.connect(self._export_pdf)
        footer.addWidget(self.export_button)
        self.open_button = QPushButton(self.tr("Open plant sheet"))
        self.open_button.setObjectName("plantCatalogOpen")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open_selected)
        footer.addWidget(self.open_button)
        layout.addLayout(footer)

        self._preview_rows: list[tuple[QLabel, QLabel]] = []
        self._set_preview_rows()
        self._populate_list()

    def _set_preview_rows(self) -> None:
        for label, value in self._preview_rows:
            self.preview_form.removeRow(label)
            label.deleteLater()
            value.deleteLater()
        self._preview_rows.clear()
        self._preview_rows = []
        self._preview_field_labels = [
            self.tr("Cultivar"),
            self.tr("Family"),
            self.tr("Genus"),
            self.tr("Life Cycle"),
            self.tr("Growth Rate"),
            self.tr("Maximum Height (cm)"),
            self.tr("Maximum Spread (cm)"),
            self.tr("Sun Requirement"),
            self.tr("Water Needs"),
            self.tr("Soil Type"),
            self.tr("Flower Color"),
            self.tr("Foliage Color"),
        ]
        for field_label in self._preview_field_labels:
            label = QLabel(field_label)
            value = QLabel()
            label.setContentsMargins(0, 3, 0, 3)
            value.setContentsMargins(0, 3, 0, 3)
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setWordWrap(True)
            self.preview_form.addRow(label, value)
            self._preview_rows.append((label, value))

    def refresh(self) -> None:
        """Reload the library so external changes appear in this view."""
        self._library._load()
        self._populate_list()

    def _populate_list(self, _text: str | None = None) -> None:
        current_id = self._selected_id()
        query = self.search_edit.text().strip() if hasattr(self, "search_edit") else ""
        all_entries = self._library.get_all_plants()
        category = self.category_filter.currentData()
        self.category_filter.blockSignals(True)
        self.category_filter.clear()
        self.category_filter.addItem(self.tr("All categories"), "")
        for value in sorted({trait(plant, "category") for _, plant in all_entries if trait(plant, "category")}, key=str.casefold):
            self.category_filter.addItem(value, value)
        self.category_filter.setCurrentIndex(max(0, self.category_filter.findData(category)))
        self.category_filter.blockSignals(False)
        snapshot = self._project.snapshot_dict(self._scene) if self._project is not None and self._scene is not None else None
        self._summaries = specimen_summaries(all_entries, snapshot)
        if query:
            matches = {id(plant) for plant in self._library.search_plants(query)}
            entries = [
                (plant_id, plant)
                for plant_id, plant in all_entries
                if id(plant) in matches
            ]
        else:
            entries = all_entries
        entries = filter_catalogue(
            entries, self._summaries, category=self.category_filter.currentData(),
            relation=self.relation_filter.currentData(), mode=self.mode_filter.currentData(),
            location=self.location_filter.text(), month=self.month_filter.currentData(),
            activity=self.activity_filter.currentData(), incomplete=self.incomplete_filter.isChecked(),
            without_personal_photo=self.without_photo_filter.isChecked(), sort=self.sort_combo.currentData(),
        )

        self.results_list.blockSignals(True)
        self._thumbnail_generation += 1
        self._thumbnail_requested.clear()
        self.results_list.clear()
        self._entries = {}
        for plant_id, plant in entries:
            if not plant_id:
                continue
            name = plant.common_name or plant.scientific_name or self.tr("Unnamed Plant")
            scientific = plant.scientific_name
            label = f"{name}  —  {scientific}" if scientific and scientific != name else name
            cultivar = trait(plant, "cultivar")
            if cultivar and cultivar.casefold() not in label.casefold():
                label += f" ‘{cultivar}’"
            summary = self._summaries[plant_id]
            badge_labels = dict(_relation_choices())
            badge_labels["present"] = self.tr("Present")
            badges = [badge_labels[key] for key in (*CATALOG_RELATIONS, "present") if key in summary["relations"]]
            label += "\n" + self.tr("{count} specimen(s)").format(count=summary["count"])
            if badges:
                label += "  ·  " + " · ".join(badges)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, plant_id)
            from open_garden_planner.services.botanical_calendar import calendar_for_species
            from open_garden_planner.services.botanical_media import primary_reference
            from open_garden_planner.ui.widgets.plant_catalog_cards import CARD_ROLE

            try:
                reference = primary_reference(plant)
            except ValueError:
                reference = None
            exposure = trait(plant, "exposures") or self._enum_label(plant.sun_requirement)
            dimensions = self.tr("Adult H {height} × W {spread} cm").format(
                height=_optional_text(plant.max_height_cm), spread=_optional_text(plant.max_spread_cm))
            botanical = scientific + (f" ‘{cultivar}’" if cultivar and cultivar.casefold() not in scientific.casefold() else "")
            item.setData(CARD_ROLE, {"identity": plant_id, "name": name, "botanical": botanical,
                                     "badges": label.split("\n")[-1], "conditions": exposure + " · " + dimensions,
                                     "calendar": calendar_for_species(plant).to_dict(), "thumbnail": reference})
            item.setToolTip(label + "\n" + exposure + "\n" + dimensions)
            self.results_list.addItem(item)
            self._entries[plant_id] = plant
            if plant_id == current_id:
                self.results_list.setCurrentItem(item)
        self.results_list.blockSignals(False)
        if self.results_list.currentItem() is None and self.results_list.count():
            self.results_list.setCurrentRow(0)
        self._update_preview()

        count = len(self._entries)
        self.status_label.setText(
            self.tr("{count} species in the custom library").format(count=count)
            if count
            else self.tr("No species found. Add a plant to start your catalog.")
        )

    def _selected_id(self) -> str | None:
        item = self.results_list.currentItem() if hasattr(self, "results_list") else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _request_thumbnail(self, identity):
        if identity in self._thumbnail_requested:
            return
        self._thumbnail_requested.add(identity)
        from open_garden_planner.services.botanical_media import primary_reference
        from open_garden_planner.ui.widgets.plant_media_gallery import _MediaTask, _read_thumbnail

        plant = self._entries.get(identity)
        if plant is None:
            return
        try:
            asset = primary_reference(plant)
        except ValueError:
            return
        if asset is None or not asset.get("thumbnail_path"):
            return
        job = _MediaTask(lambda: _read_thumbnail(self._library.path, asset))
        self._thumbnail_jobs[id(job)] = (job, self._thumbnail_generation, identity)
        job.signals.done.connect(self._thumbnail_ready)
        QThreadPool.globalInstance().start(job)

    def _thumbnail_ready(self, job_id, image, error):
        pending = self._thumbnail_jobs.pop(job_id, None)
        if pending is None or pending[1] != self._thumbnail_generation:
            return
        from PyQt6.QtGui import QIcon, QPixmap

        for row in range(self.results_list.count()):
            item = self.results_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == pending[2]:
                if error:
                    item.setToolTip(item.toolTip() + "\n" + self.tr("Reference photo is missing or unreadable."))
                else:
                    item.setIcon(QIcon(QPixmap.fromImage(image)))
                    if self._selected_id() == pending[2]:
                        self._show_reference_image(image)
                break

    def _show_reference_image(self, image):
        from PyQt6.QtGui import QPixmap

        ratio = self.devicePixelRatioF()
        pixmap = QPixmap.fromImage(image).scaled(
            QSize(int(150 * ratio), int(120 * ratio)), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        pixmap.setDevicePixelRatio(ratio)
        self.preview_photo.setPixmap(pixmap)

    def _download_reference_photo(self, identity):
        """Explicit network action; association is committed on the Qt thread."""
        if not identity or self._photo_jobs:
            return
        try:
            plant = self._library.get_plant(identity)
            if plant is None:
                return
            revision = self._library.revision
        except (OSError, ValueError, LibraryOperationError) as error:
            self._show_save_error(error)
            return
        from open_garden_planner.services.plant_source_import import fetch_reference_photo_for_plant
        from open_garden_planner.ui.widgets.plant_media_gallery import _MediaTask

        path = self._library.path
        job = _MediaTask(lambda: fetch_reference_photo_for_plant(plant, path, identity))
        self._photo_jobs[id(job)] = (job, identity, revision)
        job.signals.done.connect(self._reference_photo_ready)
        self.photo_button.setEnabled(False)
        self.status_label.setText(self.tr("Downloading the reference photo…"))
        QThreadPool.globalInstance().start(job)

    def _reference_photo_ready(self, job_id, asset, error):
        pending = self._photo_jobs.pop(job_id, None)
        if pending is None:
            return
        self.photo_button.setEnabled(self._selected_id() is not None)
        if error:
            self.status_label.setText(self.tr("Reference photo unavailable. Use a local file. {error}").format(error=error))
            return
        if asset is None:
            self.status_label.setText(self.tr("No reference photo was found in this source."))
            return
        from open_garden_planner.services.botanical_media import edit_reference_media

        try:
            plant = self._library.get_plant(pending[1])
            if plant is None:
                raise LibraryConflictError(self.tr("The botanical record no longer exists."))
            updated = edit_reference_media(plant, asset=asset)
            self._library.update_plant(pending[1], updated, expected_revision=pending[2])
            self._populate_list()
        except (OSError, ValueError, LibraryOperationError) as error:
            self.status_label.setText(self.tr("The photo could not be associated. {error}").format(error=error))

    def _open_specimens(self):
        identity = self._selected_id()
        if identity and self._project is not None:
            from open_garden_planner.ui.dialogs.plant_specimens_dialog import PlantSpecimensDialog

            plant = self._library.get_plant(identity)
            dialog = PlantSpecimensDialog(identity, self._project, self._scene, self._commands, self,
                                           species=plant.to_dict() if plant else None)
            dialog.show_on_plan.connect(self.show_on_plan)
            dialog.exec()
            self._populate_list()

    def _export_pdf(self):
        from open_garden_planner.ui.dialogs.plant_catalog_export_dialog import (
            PlantCatalogExportDialog,
        )

        selected = [item.data(Qt.ItemDataRole.UserRole) for item in self.results_list.selectedItems()]
        PlantCatalogExportDialog(self._library.get_all_plants(), selected_ids=selected,
                                  project=self._project, scene=self._scene, parent=self,
                                  library_path=self._library.path).exec()

    def _update_preview(self) -> None:
        plant_id = self._selected_id()
        plant = self._entries.get(plant_id) if plant_id else None
        enabled = plant is not None
        self.open_button.setEnabled(enabled)
        self.refresh_source_button.setEnabled(enabled and getattr(self, "_source_job", None) is None)
        self.photo_button.setEnabled(enabled and not self._photo_jobs)
        current_item = self.results_list.currentItem()
        card = current_item.data(int(Qt.ItemDataRole.UserRole) + 1) if current_item else None
        has_reference = isinstance(card, dict) and bool(card.get("thumbnail"))
        self.photo_button.setVisible(enabled and not has_reference)
        self.preview_photo.clear()
        self.preview_photo.setText(self.tr("Reference photo"))
        self.preview_description_label.setVisible(enabled)
        self.preview_description.setVisible(enabled)
        if plant is None:
            self.preview_name.setText(self.tr("Select a species to view its details."))
            self.preview_scientific_name.clear()
            for label, value in self._preview_rows:
                value.clear()
                self.preview_form.setRowVisible(label, False)
            self.preview_description.clear()
            self.preview_calendar.set_calendar({})
            return

        self.preview_name.setText(plant.common_name or plant.scientific_name)
        self.preview_scientific_name.setText(plant.scientific_name)
        values = (
            trait(plant, "cultivar"),
            plant.family,
            plant.genus,
            self._enum_label(plant.cycle),
            self._enum_label(plant.growth_rate),
            _optional_text(plant.max_height_cm),
            _optional_text(plant.max_spread_cm),
            (trait(plant, "exposures") or self.tr("—"))
            if plant.sun_requirement == SunRequirement.UNKNOWN
            else self._enum_label(plant.sun_requirement),
            (trait(plant, "soil_moisture") or self.tr("—"))
            if plant.water_needs == WaterNeeds.UNKNOWN
            else self._enum_label(plant.water_needs),
            plant.soil_type,
            plant.flower_color,
            plant.foliage_color,
        )
        quick_fields = {0, 1, 5, 6, 7, 8, 9, 10}
        for index, ((label, value), text) in enumerate(zip(self._preview_rows, values, strict=True)):
            value.setText(text)
            self.preview_form.setRowVisible(label, index in quick_fields and bool(text) and text != self.tr("—"))
        summary = trait(plant, "summary") or plant.description.split("\n\n", 1)[0]
        if len(summary) > 420:
            summary = summary[:420].rsplit(" ", 1)[0] + "…"
        self.preview_description.setText(summary)
        from open_garden_planner.services.botanical_calendar import calendar_for_species

        self.preview_calendar.set_calendar(calendar_for_species(plant).to_dict())
        self.preview_description_label.setVisible(bool(summary))
        self.preview_description.setVisible(bool(summary))
        item = self.results_list.currentItem()
        if item and not item.icon().isNull():
            self.preview_photo.setPixmap(item.icon().pixmap(QSize(150, 120)))
        self._request_thumbnail(plant_id)

    def _enum_label(self, value) -> str:
        if value.value == "unknown":
            return self.tr("—")
        labels = {
            "annual": self.tr("Annual"),
            "biennial": self.tr("Biennial"),
            "perennial": self.tr("Perennial"),
            "slow": self.tr("Slow"),
            "medium": self.tr("Medium"),
            "fast": self.tr("Fast"),
            "full_sun": self.tr("Full Sun"),
            "partial_sun": self.tr("Partial Sun"),
            "partial_shade": self.tr("Partial Shade"),
            "full_shade": self.tr("Full Shade"),
            "low": self.tr("Low"),
            "high": self.tr("High"),
        }
        return labels.get(value.value, value.value.replace("_", " ").title())

    def _source_value(self, plant: PlantSpeciesData, name: str) -> str:
        value = _source_characteristic(plant, name)
        return self.tr("Source: {text}").format(text=value) if value else ""

    def _add_plant(self) -> None:
        dialog = PlantSpeciesEditDialog(parent=self)
        if not dialog.exec() or dialog.plant is None:
            return
        try:
            plant_id = self._library.add_plant(dialog.plant)
        except (LibraryOperationError, OSError) as error:
            self._show_save_error(error)
            return
        self.search_edit.clear()
        self._populate_list()
        self._select_id(plant_id)
        self.species_saved.emit(plant_id)

    def _import_local_sources(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            self.tr("Select Plant Source Folder"),
        )
        if not directory:
            return
        self._read_local_sources(Path(directory))

    def _import_local_source_file(self) -> None:
        filename, _selected_filter = QFileDialog.getOpenFileName(
            self,
            self.tr("Select JSON/HTML Plant Source"),
            "",
            self.tr("Plant sources (*.json *.html *.htm);;All files (*)"),
        )
        if filename:
            self._read_local_sources(Path(filename))

    def _read_local_sources(self, source: Path) -> None:
        from open_garden_planner.services.local_plant_sources import (
            read_local_plant_sources,
        )

        try:
            plants = read_local_plant_sources(source)
        except (OSError, ValueError) as error:
            QMessageBox.warning(
                self,
                self.tr("Could Not Read Plant Sources"),
                self.tr("No records were imported.\n\n{error}").format(error=str(error)),
            )
            return

        self._preview_import(plants)

    def _preview_import(self, plants):
        from open_garden_planner.services.plant_source_import import mark_import_baseline

        plants = [mark_import_baseline(plant) if "catalog_import" not in plant.raw_data else plant
                  for plant in plants]
        duplicate_indexes = self._local_import_duplicate_indexes(plants)
        possible_matches = self._local_import_possible_match_indexes(
            plants, duplicate_indexes
        )
        preview = LocalPlantImportPreviewDialog(
            plants, duplicate_indexes, possible_matches, parent=self
        )
        if not preview.exec():
            return

        # Recheck after the preview. A second application window may have added
        # one of these exact sources while the modal review was open.
        plants = preview.plants
        duplicate_indexes = self._local_import_duplicate_indexes(plants)
        pending = [
            plant for index, plant in enumerate(plants)
            if index not in duplicate_indexes
        ]
        if not pending:
            self.refresh()
            QMessageBox.information(
                self,
                self.tr("No New Species"),
                self.tr("Every selected species is already in the catalog."),
            )
            return

        records = {
            plant.source_id or _species_identity(plant): plant.to_dict()
            for plant in pending
        }
        try:
            imported = self._library.import_from_dict(records)
        except LibraryConflictError as error:
            self.refresh()
            QMessageBox.warning(
                self,
                self.tr("Catalog Changed"),
                self.tr(
                    "The library changed while the import was being prepared. "
                    "No imported records were saved. Refresh the catalog and retry.\n\n{error}"
                ).format(error=str(error)),
            )
            return
        except (LibraryOperationError, OSError) as error:
            QMessageBox.warning(
                self,
                self.tr("Could Not Save Plant"),
                self.tr("No records were imported.\n\n{error}").format(error=str(error)),
            )
            return

        self.search_edit.clear()
        self.refresh()
        QMessageBox.information(
            self,
            self.tr("Plant Sources Imported"),
            self.tr("Imported {count} new species. Existing records were not changed.")
            .format(count=imported),
        )
        return [identity for identity in records if self._library.get_plant(identity) is not None]

    def _retrieve_source(self, refreshing):
        if getattr(self, "_source_job", None) is not None:
            return
        identity = self._selected_id() if refreshing else None
        existing = self._library.get_plant(identity) if identity else None
        default_url = ""
        if existing:
            for key, url_key in (("promesse_de_fleurs", "source_url"), ("local_sheet", "url")):
                source = existing.raw_data.get(key, {})
                if isinstance(source, dict):
                    default_url = source.get(url_key) or default_url
        url, accepted = QInputDialog.getText(self, self.tr("Import from URL"),
                                              self.tr("Source URL"), text=default_url)
        if not accepted or not url.strip() or refreshing and existing is None:
            return
        try:
            from open_garden_planner.services.plant_source_import import validate_source_url
            from open_garden_planner.ui.widgets.plant_source_worker import PlantSourceWorker

            validate_source_url(url.strip())
            self._source_context = (identity, deepcopy(existing), self._library.revision)
            job = PlantSourceWorker(url.strip())
            self._source_job = job
            job.signals.ready.connect(self._source_ready)
            self.url_button.setEnabled(False)
            self.refresh_source_button.setEnabled(False)
            QThreadPool.globalInstance().start(job)
        except (ValueError, OSError, LibraryOperationError) as error:
            self._show_save_error(error)

    def _source_ready(self, plant, error):
        self._source_job = None
        self.url_button.setEnabled(True)
        self.refresh_source_button.setEnabled(True)
        if error:
            QMessageBox.warning(self, self.tr("Could Not Read Plant Sources"),
                                self.tr("Use a local file or create the plant manually.\n\n{error}").format(error=error))
            return
        identity, existing, revision = self._source_context
        if existing is None:
            imported = self._preview_import([plant])
            if imported:
                self._download_reference_photo(imported[0])
            return
        try:
            from open_garden_planner.services.plant_source_import import plan_source_update

            candidate, changes = plan_source_update(existing, plant)
            review = ResponsiveDialog(self)
            review.setWindowTitle(self.tr("Review source changes"))
            review.resize(760, 440)
            layout = QVBoxLayout(review)
            table = QTableWidget(len(changes), 4)
            table.setHorizontalHeaderLabels([self.tr("Field"), self.tr("Current value"),
                                             self.tr("Source value"), self.tr("Manual value protected")])
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            for row, change in enumerate(changes):
                for column, value in enumerate((self._source_field_label(change["field"]),
                                                 self._source_diff_value(change["current"]),
                                                 self._source_diff_value(change["source"]),
                                                 self.tr("Yes") if change["protected"] else "")):
                    table.setItem(row, column, QTableWidgetItem(str(value)))
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            layout.addWidget(table)
            proceed = QPushButton(self.tr("Review and edit before saving"))
            proceed.clicked.connect(review.accept)
            layout.addWidget(proceed)
            if not review.exec():
                return
            editor = PlantSpeciesEditDialog(candidate, parent=self)
            if not editor.exec() or editor.plant is None:
                return
            if not self._library.update_plant(identity, editor.plant, expected_revision=revision):
                raise LibraryConflictError("The plant no longer exists")
            self.refresh()
            self._select_id(identity)
            self._download_reference_photo(identity)
        except (ValueError, OSError, LibraryOperationError) as error:
            self._show_save_error(error)

    def _source_field_label(self, key):
        labels = {"common_name": self.tr("Common Name"), "scientific_name": self.tr("Scientific Name"),
                  "description": self.tr("Description"), "family": self.tr("Family"), "genus": self.tr("Genus"),
                  "max_height_cm": self.tr("Maximum Height (cm)"), "max_spread_cm": self.tr("Maximum Spread (cm)"),
                  "sun_requirement": self.tr("Sun Requirement"), "water_needs": self.tr("Water Needs"),
                  "growth_rate": self.tr("Growth Rate"), "soil_type": self.tr("Soil Type"),
                  "flower_color": self.tr("Flower Color"), "foliage_color": self.tr("Foliage Color"),
                  "calendar.planting": self.tr("Planting"), "calendar.flowering": self.tr("Flowering"),
                  "calendar.pruning": self.tr("Pruning"), "calendar": self.tr("Recommended periods"),
                  "calendar.planting_optimal": self.tr("Optimal planting"),
                  "calendar.harvest": self.tr("Harvest and fruiting"), "calendar.sowing": self.tr("Sowing"),
                  "calendar.propagation": self.tr("Propagation")}
        if key.startswith("profile."):
            from open_garden_planner.services.botanical_profile import SOURCE_FIELDS

            return SOURCE_FIELDS.get(key.partition(".")[2], key.partition(".")[2].replace("_", " "))
        return labels.get(key, key.replace("_", " "))

    def _source_diff_value(self, value):
        if value is None or value == "" or value == "unknown":
            return self.tr("Unknown")
        if isinstance(value, dict):
            if "months" not in value:
                return self.tr("Unrecognized format (preserved)")
            months = value.get("months")
            selected = (", ".join(QLocale().monthName(month) for month in months)
                        if isinstance(months, list) and all(type(month) is int and 1 <= month <= 12 for month in months)
                        else self.tr("Unknown"))
            return selected + (" — " + value["source_text"] if isinstance(value.get("source_text"), str) and value["source_text"] else "")
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        return str(value)

    def _local_import_duplicate_indexes(
        self, plants: list[PlantSpeciesData]
    ) -> set[int]:
        from open_garden_planner.services.plant_source_import import source_identity

        def external_identity(plant):
            try:
                return source_identity(plant)
            except ValueError:
                # Unknown provenance is never sufficient evidence to merge.
                return None

        existing = self._library.get_all_plants()
        known_ids = {
            plant.source_id or plant_id
            for plant_id, plant in existing
        }
        known_sources = {identity for _, plant in existing if (identity := external_identity(plant)) is not None}
        duplicates: set[int] = set()
        for index, plant in enumerate(plants):
            source_id = plant.source_id.strip()
            provider_id = external_identity(plant)
            if source_id and source_id in known_ids or provider_id is not None and provider_id in known_sources:
                duplicates.add(index)
                continue
            if source_id:
                known_ids.add(source_id)
            if provider_id is not None:
                known_sources.add(provider_id)
        return duplicates

    def _local_import_possible_match_indexes(
        self,
        plants: list[PlantSpeciesData],
        duplicate_indexes: set[int],
    ) -> set[int]:
        known_names = [
            name
            for _, plant in self._library.get_all_plants()
            for name in (
                _normalized_name(plant.common_name),
                _normalized_name(plant.scientific_name),
            )
            if len(name) >= 5
        ]
        possible: set[int] = set()
        for index, plant in enumerate(plants):
            if index in duplicate_indexes:
                continue
            names = {
                name for name in (
                    _normalized_name(plant.common_name),
                    _normalized_name(plant.scientific_name),
                ) if len(name) >= 5
            }
            if any(
                SequenceMatcher(None, name, known).ratio() >= 0.86
                for name in names
                for known in known_names
            ):
                possible.add(index)
            known_names.extend(names)
        return possible

    def _open_selected(self, *_args) -> None:
        plant_id = self._selected_id()
        plant = self._entries.get(plant_id) if plant_id else None
        if plant_id is None or plant is None:
            return
        try:
            from open_garden_planner.ui.dialogs.plant_sheet_dialog import PlantSheetDialog

            dialog = PlantSheetDialog(self._library, plant_id, project=self._project, scene=self._scene,
                                       commands=self._commands, parent=self)
            dialog.show_on_plan.connect(self.show_on_plan)
            dialog.species_saved.connect(self.species_saved)
            dialog.exec()
        except (LibraryOperationError, OSError, ValueError) as error:
            self._show_save_error(error)
            return
        self._populate_list()
        self._select_id(plant_id)

    def _select_id(self, plant_id: str) -> None:
        for row in range(self.results_list.count()):
            item = self.results_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == plant_id:
                self.results_list.setCurrentItem(item)
                self.results_list.scrollToItem(item)
                return

    def _show_save_error(self, error: Exception) -> None:
        QMessageBox.warning(
            self,
            self.tr("Could Not Save Plant"),
            self.tr("The catalog could not be saved. Your changes were not applied.\n\n{error}")
            .format(error=str(error)),
        )


def _optional_text(value: float | int | None) -> str:
    if value is None:
        return "—"
    return f"{value:g}"


def _relation_choices():
    return (
        ("study", QCoreApplication.translate("PlantCatalogRelations", "To study")),
        ("wanted", QCoreApplication.translate("PlantCatalogRelations", "Wanted")),
        ("ordered", QCoreApplication.translate("PlantCatalogRelations", "Ordered")),
        ("owned", QCoreApplication.translate("PlantCatalogRelations", "Owned")),
        ("archived", QCoreApplication.translate("PlantCatalogRelations", "Archived")),
    )


def _source_characteristic(plant: PlantSpeciesData, name: str) -> str:
    """Show explicit source values without collapsing multiple choices."""
    raw = plant.raw_data
    sheet = raw.get("local_sheet")
    if isinstance(sheet, dict) and isinstance(sheet.get("characteristics"), dict):
        value = sheet["characteristics"].get(name)
        if isinstance(value, str):
            return value
    source = raw.get("promesse_de_fleurs")
    if isinstance(source, dict) and isinstance(source.get("fields"), dict):
        values = source["fields"].get(name)
        if isinstance(values, list) and all(isinstance(value, str) for value in values):
            return " · ".join(values)
    return ""


def _species_identity(plant: PlantSpeciesData) -> str:
    return species_key(plant.to_dict())


def _normalized_name(value: str) -> str:
    folded = unicodedata.normalize("NFD", value.casefold())
    without_marks = "".join(
        char for char in folded if unicodedata.category(char) != "Mn"
    )
    return " ".join(
        "".join(char if char.isalnum() else " " for char in without_marks).split()
    )
