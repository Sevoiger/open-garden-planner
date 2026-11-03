"""Complete offline botanical sheet, independent of canvas selection."""
from copy import deepcopy

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from open_garden_planner.services.botanical_calendar import calendar_for_species
from open_garden_planner.services.botanical_profile import trait
from open_garden_planner.services.garden_queries import garden_objects
from open_garden_planner.services.library_operations import (
    LibraryConflictError,
    LibraryOperationError,
)
from open_garden_planner.ui.widgets.botanical_calendar import BotanicalCalendarWidget
from open_garden_planner.ui.widgets.responsive_dialog import bounded_dialog_rect


class PlantSheetDialog(QDialog):
    species_saved = pyqtSignal(str)
    show_on_plan = pyqtSignal(object)

    def __init__(self, library, identity, *, project=None, scene=None, commands=None, parent=None):
        super().__init__(parent)
        self.library, self.identity = library, identity
        self.project, self.scene, self.commands = project, scene, commands
        self.resize(1050, 780)
        self.setWindowTitle(self.tr("Plant sheet"))
        layout = QVBoxLayout(self)
        # Each tab already owns scrolling; wrapping the entire tab widget in
        # another form scroll can hide the specimen action rows on macOS.
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.title = QLabel()
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        font = self.title.font()
        font.setPointSize(19)
        font.setBold(True)
        self.title.setFont(font)
        self.title.setWordWrap(True)
        layout.addWidget(self.title)
        self.tabs = QTabWidget()
        self.tabs.setMinimumSize(0, 0)
        layout.addWidget(self.tabs, 1)
        self.presentation = self._page()
        self.culture = self._page()
        self.sources = self._page()
        self.tabs.addTab(self.presentation, self.tr("Presentation"))
        self.tabs.addTab(self.culture, self.tr("Culture and calendar"))
        if project is not None and scene is not None and commands is not None:
            from open_garden_planner.ui.dialogs.plant_specimens_dialog import PlantSpecimensDialog

            plant = library.get_plant(identity)
            self.specimens = PlantSpecimensDialog(identity, project, scene, commands, self,
                                                  species=plant.to_dict() if plant else None, embedded=True)
            self.specimens.show_on_plan.connect(self._navigate)
            self.tabs.addTab(self.specimens, self.tr("My specimens"))
        else:
            self.specimens = None
            self.tabs.addTab(QLabel(self.tr("Open a project to manage personal specimens.")), self.tr("My specimens"))
        self.tabs.addTab(self._media_page(), self.tr("Photos and documents"))
        self.tabs.addTab(self.sources, self.tr("Sources"))
        self.tabs.currentChanged.connect(self._tab_changed)
        actions = QHBoxLayout()
        self.edit_button = QPushButton(self.tr("Edit botanical sheet"))
        self.edit_button.clicked.connect(self._edit)
        export = QPushButton(self.tr("Export PDF"))
        export.clicked.connect(self._export)
        close = QPushButton(self.tr("Close"))
        close.clicked.connect(self.accept)
        for button in (self.edit_button, export, close):
            actions.addWidget(button)
        layout.addLayout(actions)
        self._refresh()

    def showEvent(self, event):
        super().showEvent(event)
        screen = self.screen()
        if screen is not None:
            frame = self.frameGeometry()
            target = bounded_dialog_rect(frame, screen.availableGeometry())
            decoration = frame.size() - self.size()
            self.resize(target.size() - decoration)
            self.move(target.topLeft())

    def _page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        QVBoxLayout(content)
        scroll.setWidget(content)
        return scroll

    @staticmethod
    def _label(value):
        label = QLabel(str(value))
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        return label

    def _fill(self, page, rows, *, paragraphs=()):
        old = page.takeWidget()
        if old is not None:
            old.deleteLater()
        content = QWidget()
        layout = QVBoxLayout(content)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for label, value in rows:
            if value is not None and value != "":
                form.addRow(self._label(label), self._label(value))
        layout.addLayout(form)
        for paragraph in paragraphs:
            if paragraph:
                layout.addWidget(self._label(paragraph))
        layout.addStretch(1)
        page.setWidget(content)
        return layout

    def _refresh(self):
        plant = self.library.get_plant(self.identity)
        if plant is None:
            self.edit_button.setEnabled(False)
            return
        self.plant = deepcopy(plant)
        self.revision = self.library.revision
        self.title.setText(plant.common_name or plant.scientific_name)
        self._fill(self.presentation, [
            (self.tr("Summary"), trait(plant, "summary")),
            (self.tr("Botanical name"), plant.scientific_name),
            (self.tr("Cultivar"), trait(plant, "cultivar")),
            (self.tr("Commercial name"), trait(plant, "commercial_name")),
            (self.tr("Other common names"), trait(plant, "common_names")),
            (self.tr("Synonyms"), trait(plant, "synonyms")),
            (self.tr("Genus"), plant.genus), (self.tr("Species"), trait(plant, "species")),
            (self.tr("Family"), plant.family), (self.tr("Category"), trait(plant, "category")),
            (self.tr("Personal tags"), trait(plant, "tags")),
            (self.tr("Life cycle"), self._enum(plant.cycle)),
            (self.tr("Growth habit"), trait(plant, "habit")),
            (self.tr("Growth rate"), self._enum(plant.growth_rate)),
            (self.tr("Minimum adult height (cm)"), plant.min_height_cm),
            (self.tr("Maximum adult height (cm)"), plant.max_height_cm),
            (self.tr("Minimum adult spread (cm)"), plant.min_spread_cm),
            (self.tr("Maximum adult spread (cm)"), plant.max_spread_cm),
            (self.tr("Foliage persistence"), trait(plant, "foliage_persistence")),
            (self.tr("Foliage color"), plant.foliage_color),
            (self.tr("Foliage characteristics"), trait(plant, "foliage_details")),
            (self.tr("Flower color"), plant.flower_color),
            (self.tr("Flower characteristics"), trait(plant, "flower_details")),
            (self.tr("Fragrance"), trait(plant, "fragrance")),
            (self.tr("Fruiting"), trait(plant, "fruit")),
            (self.tr("Seasonal interest"), trait(plant, "seasonal_interest")),
        ], paragraphs=[plant.description])
        culture = self._fill(self.culture, [
            (self.tr("Exposure"), trait(plant, "exposures") or self._enum(plant.sun_requirement)),
            (self.tr("Soil type"), plant.soil_type), (self.tr("Drainage"), trait(plant, "drainage")),
            (self.tr("Soil richness"), trait(plant, "soil_richness")),
            (self.tr("Soil moisture"), trait(plant, "soil_moisture")),
            (self.tr("Soil pH"), trait(plant, "soil_ph")),
            (self.tr("Minimum soil pH"), plant.ph_min), (self.tr("Maximum soil pH"), plant.ph_max),
            (self.tr("Hardiness"), trait(plant, "hardiness")),
            (self.tr("Minimum hardiness zone"), plant.hardiness_zone_min),
            (self.tr("Maximum hardiness zone"), plant.hardiness_zone_max),
            (self.tr("Climate context"), trait(plant, "climate")),
            (self.tr("Water needs"), self._enum(plant.water_needs)),
            (self.tr("Drought tolerance"), trait(plant, "drought")),
            (self.tr("Sensitivity to excess water"), trait(plant, "excess_water")),
            (self.tr("Suitability for containers"), trait(plant, "pot_suitability")),
            (self.tr("Spacing or density with units"), trait(plant, "spacing")),
            (self.tr("Uses"), trait(plant, "uses")), (self.tr("Support requirements"), trait(plant, "support")),
            (self.tr("Planting advice"), trait(plant, "planting_advice")),
            (self.tr("Mulching"), trait(plant, "mulching")), (self.tr("Care"), trait(plant, "care")),
            (self.tr("Pruning advice"), trait(plant, "pruning_advice")),
            (self.tr("Pruning group and warnings"), trait(plant, "pruning_group")),
            (self.tr("Overwintering"), trait(plant, "overwintering")),
            (self.tr("Pollination"), trait(plant, "pollination")),
            (self.tr("Interest for pollinators"), trait(plant, "pollinators")),
            (self.tr("Propagation"), trait(plant, "propagation")),
            (self.tr("Documented edible uses"), trait(plant, "edibility")),
            (self.tr("Precautions"), trait(plant, "precautions")),
            (self.tr("Safety source and certainty"), trait(plant, "safety_source")),
        ])
        calendar = BotanicalCalendarWidget(editable=False, extended=True)
        calendar.set_calendar(calendar_for_species(plant).to_dict())
        culture.insertWidget(0, calendar)
        culture.insertWidget(0, self._label(self.tr("Monthly recommendations; an empty row means unknown.")))
        offsets = []
        for key, label in (
            ("indoor_sow", self.tr("Indoor sowing")), ("direct_sow", self.tr("Direct sowing")),
            ("transplant", self.tr("Transplanting")), ("harvest", self.tr("Harvest")),
        ):
            start, end = getattr(plant, key + "_start"), getattr(plant, key + "_end")
            if start is not None or end is not None:
                offsets.append(f"{label}: {start if start is not None else self.tr('Unknown')} / {end if end is not None else self.tr('Unknown')}")
        if offsets:
            culture.insertWidget(2, self._label(self.tr("Weeks relative to the last spring frost (start / end)") + "\n" + "\n".join(offsets)))
        source_rows = [(self.tr("Internal identifier"), self.identity)]
        source_rows.append((self.tr("Source file"), plant.raw_data.get("source_file_uri")))
        for provider, url_key in (("local_sheet", "url"), ("promesse_de_fleurs", "source_url")):
            source = plant.raw_data.get(provider)
            if isinstance(source, dict):
                provider_name = "Promesse de Fleurs" if provider == "promesse_de_fleurs" else self.tr("Local plant sheet")
                source_rows.extend(((self.tr("Data provider"), provider_name),
                                    (self.tr("Source URL"), source.get(url_key)),
                                    (self.tr("Source file"), source.get("source_file") or plant.raw_data.get("source_file"))))
                original_fields = source.get("characteristics" if provider == "local_sheet" else "fields", {})
                if isinstance(original_fields, dict):
                    for field, value in original_fields.items():
                        if isinstance(field, str):
                            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                                value = " · ".join(value)
                            if isinstance(value, str) and value:
                                source_rows.append((provider_name + " - " + field, value))
        source_rows.append((self.tr("Reference photo URL"), plant.image_url))
        state = plant.raw_data.get("catalog_import", {})
        if isinstance(state, dict):
            source_rows.append((self.tr("Retrieved on"), state.get("retrieved_at")))
            source_rows.append((self.tr("Last verified"), state.get("verified_at")))
            baseline = state.get("baseline", {})
            if isinstance(baseline, dict):
                names = {"common_name": self.tr("Common name"), "scientific_name": self.tr("Botanical name"),
                         "family": self.tr("Family"), "genus": self.tr("Genus"),
                         "description": self.tr("Description"), "max_height_cm": self.tr("Maximum adult height (cm)"),
                         "max_spread_cm": self.tr("Maximum adult spread (cm)"),
                         "sun_requirement": self.tr("Exposure"), "water_needs": self.tr("Water needs"),
                         "flower_color": self.tr("Flower color"), "foliage_color": self.tr("Foliage color")}
                manual_fields = state.get("manual_fields", [])
                if not isinstance(manual_fields, list):
                    manual_fields = []
                corrected = [names[key] for key, value in baseline.items() if key in names
                             and (plant.to_dict().get(key) != value or key in manual_fields)]
                source_rows.append((self.tr("Manually corrected fields"), ", ".join(corrected)))
        self._fill(self.sources, source_rows, paragraphs=[self.tr("Opening this sheet does not download or refresh its sources.")])
        if self.specimens is not None:
            self.specimens.species = plant.to_dict()

    def _tab_changed(self, index):
        if index == 3 and hasattr(self, "photo_chooser"):
            self._reload_media_chooser()

    def _reload_media_chooser(self):
        chooser = self.photo_chooser
        previous = chooser.currentData()
        chooser.blockSignals(True)
        chooser.clear()
        chooser.addItem(self.tr("Choose a specimen to view its personal photos"), None)
        for record in garden_objects(self.project.snapshot_dict(self.scene)):
            if record.get("metadata", {}).get("plant_instance", {}).get("library_plant_id") == self.identity:
                chooser.addItem(record.get("name", ""), record["item_id"])
        chooser.setCurrentIndex(max(0, chooser.findData(previous)))
        chooser.blockSignals(False)
        if previous is None or chooser.currentData() is None:
            self.photo_stack.setCurrentIndex(0)

    def _enum(self, value):
        labels = {"annual": self.tr("Annual"), "biennial": self.tr("Biennial"), "perennial": self.tr("Perennial"),
                  "slow": self.tr("Slow"), "medium": self.tr("Medium"), "fast": self.tr("Fast"),
                  "full_sun": self.tr("Full sun"), "partial_sun": self.tr("Partial sun"),
                  "partial_shade": self.tr("Partial shade"), "full_shade": self.tr("Full shade"),
                  "low": self.tr("Low"), "high": self.tr("High")}
        return labels.get(value.value, "")

    def _media_page(self):
        from open_garden_planner.ui.widgets.botanical_reference_gallery import (
            BotanicalReferenceGallery,
        )

        page = self._page()
        layout = page.widget().layout()
        layout.addWidget(self._label(self.tr("Reference photos belong to the botanical sheet. Personal garden photos belong to each specimen.")))
        self.reference_gallery = BotanicalReferenceGallery(self.library, self.identity, page)
        self.reference_gallery.changed.connect(self._reference_changed)
        layout.addWidget(self.reference_gallery, 1)
        if self.project is not None and self.scene is not None:
            chooser = QComboBox()
            self.photo_chooser = chooser
            self._personal_galleries = {}
            self.photo_stack = QStackedWidget()
            self.photo_stack.addWidget(QWidget())
            def selected(index):
                identity = chooser.itemData(index)
                if identity is None:
                    self.photo_stack.setCurrentIndex(0)
                    return
                from open_garden_planner.ui.widgets.plant_media_gallery import PlantMediaGallery

                try:
                    gallery = self._personal_galleries.get(identity)
                    if gallery is None:
                        gallery = PlantMediaGallery(self.project, self.scene, self.commands, identity, self.photo_stack)
                        self._personal_galleries[identity] = gallery
                        self.photo_stack.addWidget(gallery)
                    else:
                        gallery.refresh()
                    self.photo_stack.setCurrentWidget(gallery)
                except (ValueError, OSError) as error:
                    QMessageBox.warning(self, self.tr("Photos and documents"), str(error))
            chooser.currentIndexChanged.connect(selected)
            layout.addWidget(chooser)
            layout.addWidget(self.photo_stack, 1)
            self._reload_media_chooser()
        return page

    def _reference_changed(self):
        self._refresh()
        self.species_saved.emit(self.identity)

    def _navigate(self, item):
        self.show_on_plan.emit(item)
        self.accept()

    def _edit(self):
        from open_garden_planner.ui.views.plant_catalog_view import PlantSpeciesEditDialog

        editor = PlantSpeciesEditDialog(self.plant, parent=self)
        if not editor.exec() or editor.plant is None:
            return
        try:
            if not self.library.update_plant(self.identity, editor.plant, expected_revision=self.revision):
                raise LibraryConflictError(self.tr("This botanical sheet no longer exists."))
        except LibraryConflictError as error:
            self.library._load()
            self._refresh()
            QMessageBox.warning(self, self.tr("Could not save botanical sheet"), str(error))
            return
        except (LibraryOperationError, OSError) as error:
            QMessageBox.warning(self, self.tr("Could not save botanical sheet"), str(error))
            return
        self._refresh()
        self.species_saved.emit(self.identity)

    def _export(self):
        from open_garden_planner.ui.dialogs.plant_catalog_export_dialog import (
            PlantCatalogExportDialog,
        )

        PlantCatalogExportDialog([(self.identity, self.plant)], selected_ids=[self.identity], project=self.project,
                                  scene=self.scene, parent=self, library_path=self.library.path).exec()
