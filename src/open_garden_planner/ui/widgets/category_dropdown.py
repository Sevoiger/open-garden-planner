"""Popup dropdown showing thumbnails of items in a single category.

Opens directly under a toolbar category button. Supports click-to-activate
and drag-to-canvas, plus an in-popup search field that filters thumbnails.
"""

from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import QCoreApplication, QMimeData, QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QContextMenuEvent,
    QDrag,
    QShowEvent,
)
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from open_garden_planner.core.tools import ToolType
from open_garden_planner.ui.theme import set_text_role
from open_garden_planner.ui.widgets.gallery_data import (
    THUMB_SIZE,
    GalleryCategory,
    GalleryItem,
)
from open_garden_planner.ui.widgets.popup_geometry import show_popup_below

if TYPE_CHECKING:
    from open_garden_planner.models.plant_data import PlantSpeciesData
    from open_garden_planner.models.plant_lists import PlantListStore

GRID_COLS = 3
DRAG_THRESHOLD = 10


class _ThumbnailButton(QToolButton):
    """Single thumbnail button inside the dropdown grid."""

    clicked_item = pyqtSignal(object)

    def __init__(self, item: GalleryItem, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._item = item
        self._drag_start_pos: QPoint | None = None

        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._list_store: PlantListStore | None = None
        if self._item.species:
            from open_garden_planner.models.plant_lists import (  # noqa: PLC0415
                get_plant_list_store,
            )

            self._list_store = get_plant_list_store()
            self._list_store.changed.connect(self._on_store_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(1)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        thumb_label = QLabel()
        thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb_label.setFixedSize(THUMB_SIZE, THUMB_SIZE)
        self._thumb_label = thumb_label
        if item.thumbnail and not item.thumbnail.isNull():
            self.update_thumbnail()
        else:
            thumb_label.setText("?")
            thumb_label.setStyleSheet("font-size: 20px;")
        layout.addWidget(thumb_label)

        star_badge = QLabel(thumb_label)
        from open_garden_planner.ui.icons import get_pixmap  # noqa: PLC0415

        pm = get_pixmap("star", size=14)
        if pm is not None:
            star_badge.setPixmap(pm)
        star_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        star_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        star_badge.setFixedSize(14, 14)
        star_badge.move(THUMB_SIZE - 15, 1)
        star_badge.setVisible(False)
        self._star_badge = star_badge

        name_label = QLabel(item.name)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label.setWordWrap(True)
        # Labels and tool buttons can have different fonts under the theme.
        # Measure the actual label, including translated compound words.
        metrics = name_label.fontMetrics()
        longest_word = max((metrics.horizontalAdvance(word) for word in item.name.split()), default=0)
        name_label.setMinimumWidth(max(THUMB_SIZE, longest_word + 4))
        self.setMinimumWidth(max(THUMB_SIZE + 32, longest_word + 12))
        self.setMinimumHeight(THUMB_SIZE + metrics.lineSpacing() * 3 + 14)
        required_text = name_label.fontMetrics().boundingRect(
            QRect(0, 0, name_label.minimumWidth(), 10000), Qt.TextFlag.TextWordWrap, item.name,
        )
        name_label.setMinimumHeight(max(name_label.fontMetrics().lineSpacing() * 3, required_text.height()) + 2)
        layout.addWidget(name_label)
        # QToolButton's own size hint does not account for a child form whose
        # wrapped title is taller than three lines. Contain the whole layout.
        self.setMinimumSize(self.minimumSize().expandedTo(layout.minimumSize()))

        self._update_favorite_state()

        # Styled by theme.py's #CategoryDropdown QToolButton rules — the old
        # palette()-based QSS tracked the OS palette, not our theme.
        self.clicked.connect(lambda: self.clicked_item.emit(self._item))

    def showEvent(self, event: QShowEvent | None) -> None:  # noqa: N802 — Qt override
        super().showEvent(event)
        self._update_favorite_state()

    def _on_store_changed(self) -> None:
        """Update favorite star badge and tooltip when store changes."""
        self._update_favorite_state()

    def _get_species_obj(self) -> "PlantSpeciesData":
        from open_garden_planner.models.plant_data import (  # noqa: PLC0415
            PlantSpeciesData,
        )
        from open_garden_planner.services.bundled_species_db import (  # noqa: PLC0415
            lookup_species,
        )

        meta = lookup_species(self._item.species) or lookup_species(self._item.name)
        if meta:
            return PlantSpeciesData.from_dict(meta)
        return PlantSpeciesData(
            scientific_name=self._item.species,
            common_name=self._item.name,
        )

    def _update_favorite_state(self) -> None:
        """Update button star badge and tooltip if item is favorited."""
        if not self._item.species:
            self.setToolTip(self._item.name)
            if hasattr(self, "_star_badge"):
                self._star_badge.setVisible(False)
            return
        from open_garden_planner.models.plant_lists import (  # noqa: PLC0415
            get_plant_list_store,
        )

        store = get_plant_list_store()
        obj = self._get_species_obj()
        is_fav = (
            store.is_favorite(obj)
            or store.is_favorite(self._item.species)
            or store.is_favorite(self._item.name)
        )
        if hasattr(self, "_star_badge"):
            self._star_badge.setVisible(is_fav)
        if is_fav:
            self.setToolTip(
                QCoreApplication.translate(
                    "CategoryDropdown", "{name} (Favorite)"
                ).format(name=self._item.name)
            )
        else:
            self.setToolTip(self._item.name)

    _update_tooltip = _update_favorite_state

    def contextMenuEvent(self, event: QContextMenuEvent | None) -> None:  # noqa: N802 — Qt override
        """Show context menu for plant gallery items (Favorites and List management)."""
        if not self._item.species or event is None:
            super().contextMenuEvent(event)
            return

        from open_garden_planner.models.plant_lists import (  # noqa: PLC0415
            get_plant_list_store,
        )

        store = get_plant_list_store()
        obj = self._get_species_obj()
        is_fav = store.is_favorite(obj) or store.is_favorite(self._item.species) or store.is_favorite(self._item.name)

        menu = QMenu(self)

        if is_fav:
            fav_action = menu.addAction(
                QCoreApplication.translate("CategoryDropdown", "Remove from Favorites")
            )
            if fav_action is not None:
                fav_action.triggered.connect(lambda: store.toggle_favorite(obj))
        else:
            fav_action = menu.addAction(
                QCoreApplication.translate("CategoryDropdown", "Add to Favorites")
            )
            if fav_action is not None:
                fav_action.triggered.connect(lambda: store.toggle_favorite(obj))

        custom_lists = [pl for pl in store.all_lists() if pl.id != store.FAVORITES_ID]
        if custom_lists:
            sub_menu = menu.addMenu(
                QCoreApplication.translate("CategoryDropdown", "Add to List")
            )
            if sub_menu is not None:
                for pl in custom_lists:
                    action = sub_menu.addAction(pl.name)
                    if action is not None:
                        action.triggered.connect(
                            lambda _checked=False, target_id=pl.id: store.add_entry(
                                target_id, self._get_species_obj()
                            )
                        )

        menu.exec(event.globalPos())

    @property
    def item(self) -> GalleryItem:
        return self._item

    def update_thumbnail(self) -> None:
        """Refresh the pixmap from the (possibly re-rendered) gallery item."""
        if self._item.thumbnail is None or self._item.thumbnail.isNull():
            return
        scaled = self._item.thumbnail.scaled(
            THUMB_SIZE - 4,
            THUMB_SIZE - 4,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._thumb_label.setPixmap(scaled)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not (event.buttons() & Qt.MouseButton.LeftButton) or self._drag_start_pos is None:
            return
        if (event.pos() - self._drag_start_pos).manhattanLength() < DRAG_THRESHOLD:
            return

        drag = QDrag(self)
        mime_data = QMimeData()
        drag_data = f"gallery:{self._item.tool_type.name}"
        if self._item.species:
            drag_data += f":species={self._item.species}"
        if self._item.plant_category:
            drag_data += f":category={self._item.plant_category.name}"
        mime_data.setText(drag_data)
        drag.setMimeData(mime_data)

        if self._item.thumbnail and not self._item.thumbnail.isNull():
            drag_pixmap = self._item.thumbnail.scaled(
                48, 48,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            drag.setPixmap(drag_pixmap)
            drag.setHotSpot(QPoint(24, 24))

        drag.exec(Qt.DropAction.CopyAction)


class CategoryDropdown(QWidget):
    """Popup panel listing all items of one category as a thumbnail grid.

    Constructed once per toolbar category button. Opens beneath the button
    when triggered, closes on click-outside or after an item is chosen.
    """

    tool_selected = pyqtSignal(ToolType)
    item_selected = pyqtSignal(object)
    database_search_requested = pyqtSignal(str, str)

    def __init__(self, category: GalleryCategory, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self._category = category
        self._buttons: list[_ThumbnailButton] = []
        self._catalog_records: dict[str, dict[str, Any]] | None = None
        self._catalog_offset = 0
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setObjectName("CategoryDropdown")
        # Styled by theme.py's #CategoryDropdown rule; a custom QWidget only
        # paints a stylesheet background with WA_StyledBackground set.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        header = QLabel(self._category.name)
        set_text_role(header, "h2")
        header.setStyleSheet("padding: 2px 4px;")
        layout.addWidget(header)

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText(self.tr("Filter…"))
        self._search_box.setClearButtonEnabled(True)
        self._search_box.textChanged.connect(self._apply_filter)
        search_row = QHBoxLayout()
        search_row.addWidget(self._search_box, stretch=1)
        self._clear_filters_button = QPushButton(self.tr("Clear filters"))
        self._clear_filters_button.clicked.connect(self.clear_filters)
        search_row.addWidget(self._clear_filters_button)
        layout.addLayout(search_row)
        self._content = QWidget()
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(4)
        self._scroll = QScrollArea()
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._content)
        layout.addWidget(self._scroll)
        from open_garden_planner.ui.widgets.trait_filters import TraitFilters

        self.trait_filters = TraitFilters(self)
        self.trait_filters.setVisible(any(item.species or item.plant_category for item in self._category.items))
        self.trait_filters.changed.connect(lambda: self._apply_filter(self._search_box.text()))
        content_layout.addWidget(self.trait_filters)

        from open_garden_planner.ui.widgets.catalog_palette import ICON_GROUPS, CatalogList

        self._catalog_group = ICON_GROUPS.get(self._category.icon_name)
        self._catalog_list = None
        self._illustrations_toggle = None
        if self._catalog_group:
            self._all_plants = QCheckBox(self.tr("All plants (including unclassified)"))
            self._all_plants.toggled.connect(lambda: self._apply_filter(self._search_box.text()))
            content_layout.addWidget(self._all_plants)
            self._catalog_count = QLabel()
            self._catalog_count.setWordWrap(True)
            content_layout.addWidget(self._catalog_count)
            self._catalog_list = CatalogList(self)
            self._catalog_list.item_chosen.connect(self._on_item_clicked)
            content_layout.addWidget(self._catalog_list)
            pages = QHBoxLayout()
            self._previous_page = QPushButton(self.tr("Previous plants"))
            self._next_page = QPushButton(self.tr("Next plants"))
            self._previous_page.clicked.connect(lambda: self._change_page(-1))
            self._next_page.clicked.connect(lambda: self._change_page(1))
            pages.addWidget(self._previous_page)
            pages.addWidget(self._next_page)
            content_layout.addLayout(pages)
            self._database_button = QPushButton(self.tr("Search plant databases…"))
            self._database_button.clicked.connect(self._search_databases)
            content_layout.addWidget(self._database_button)
            self._illustrations_toggle = QCheckBox(self.tr("Illustrations and generic shapes"))
            content_layout.addWidget(self._illustrations_toggle)

        grid_widget = QWidget()
        grid_layout = QGridLayout(grid_widget)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setSpacing(4)
        grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._grid_layout = grid_layout

        for i, item in enumerate(self._category.items):
            row = i // GRID_COLS
            col = i % GRID_COLS
            btn = _ThumbnailButton(item)
            btn.clicked_item.connect(self._on_item_clicked)
            grid_layout.addWidget(btn, row, col)
            self._buttons.append(btn)

        content_layout.addWidget(grid_widget)
        self._grid_widget = grid_widget
        if self._illustrations_toggle is not None:
            grid_widget.hide()
            self._illustrations_toggle.toggled.connect(grid_widget.setVisible)
            self._illustrations_toggle.toggled.connect(lambda: self._apply_filter(self._search_box.text()))
        self._empty_label = QLabel(self.tr("No matching objects"))
        self._empty_label.setWordWrap(True)
        content_layout.addWidget(self._empty_label)
        self._empty_label.hide()

        self.adjustSize()
        max_width = grid_widget.minimumSizeHint().width() + 32
        if not self.trait_filters.isHidden():
            max_width = max(max_width, self.trait_filters.minimumSizeHint().width() + 32)
        self._preferred_width = max_width
        self._header = header

    def _on_item_clicked(self, item: GalleryItem) -> None:
        self.tool_selected.emit(item.tool_type)
        self.item_selected.emit(item)
        self.close()

    def _search_databases(self) -> None:
        query = self._search_box.text()
        self.close()
        self.database_search_requested.emit(query, self._catalog_group or "flower")

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        while self._grid_layout.count():
            self._grid_layout.takeAt(0)
        position = 0
        for btn in self._buttons:
            from open_garden_planner.ui.widgets.gallery_data import gallery_matches

            visible = gallery_matches(btn.item, needle, layers=self.trait_filters.layers,
                                      functions=self.trait_filters.functions)
            btn.setVisible(visible)
            if visible:
                self._grid_layout.addWidget(btn, position // GRID_COLS, position % GRID_COLS)
                position += 1
        self._empty_label.setVisible(all(btn.isHidden() for btn in self._buttons))
        self._catalog_offset = 0
        self._populate_catalog(text)

    def clear_filters(self) -> None:
        """Start each category opening with all objects, never stale hidden chips."""
        self._search_box.clear()
        self.trait_filters.clear()
        if self._catalog_list is not None:
            self._all_plants.setChecked(False)
            self._illustrations_toggle.setChecked(False)
        self._apply_filter("")

    def _change_page(self, direction: int) -> None:
        from open_garden_planner.ui.widgets.catalog_palette import PAGE_SIZE

        self._catalog_offset = max(0, self._catalog_offset + direction * PAGE_SIZE)
        self._populate_catalog(self._search_box.text())

    def _populate_catalog(self, text: str) -> None:
        if self._catalog_list is None or self._catalog_records is None:
            return
        from open_garden_planner.ui.widgets.catalog_palette import PAGE_SIZE, matching_records

        matches = matching_records(
            self._catalog_records, text, group=None if self._all_plants.isChecked() else self._catalog_group,
            layers=self.trait_filters.layers, functions=self.trait_filters.functions,
        )
        page = matches[self._catalog_offset:self._catalog_offset + PAGE_SIZE]
        self._catalog_list.populate(page, self._catalog_group or "flower")
        self._catalog_count.setText(self.tr("{count} plants — {first}–{last}").format(
            count=len(matches), first=self._catalog_offset + 1 if page else 0,
            last=self._catalog_offset + len(page),
        ))
        self._previous_page.setEnabled(self._catalog_offset > 0)
        self._next_page.setEnabled(self._catalog_offset + PAGE_SIZE < len(matches))
        no_drawings = self._grid_widget.isHidden() or all(btn.isHidden() for btn in self._buttons)
        self._empty_label.setVisible(not matches and no_drawings)

    def refresh_thumbnails(self) -> None:
        """Re-pull each button's pixmap after a theme switch (see §8.21)."""
        for btn in self._buttons:
            btn.update_thumbnail()

    def show_below(self, anchor: QWidget) -> None:
        """Show the popup directly beneath the given anchor widget."""
        if self._catalog_group:
            from open_garden_planner.services.species_catalog import offline_catalog

            self._catalog_records = offline_catalog()
        self.clear_filters()
        for btn in self._buttons:
            btn._update_favorite_state()
        layout = self.layout()
        assert layout is not None
        margins = layout.contentsMargins()
        search_height = max(self._search_box.sizeHint().height(),
                            self._clear_filters_button.sizeHint().height())
        height = (self._header.sizeHint().height() + search_height
                  + self._content.sizeHint().height() + margins.top() + margins.bottom()
                  + 2 * layout.spacing())
        show_popup_below(self, anchor, QSize(self._preferred_width, height))
        for bar in (self._scroll.verticalScrollBar(), self._scroll.horizontalScrollBar()):
            if bar is not None:
                bar.setValue(0)
        self._search_box.setFocus()


# Backwards compatibility / descriptive alias
CategoryItemButton = _ThumbnailButton

