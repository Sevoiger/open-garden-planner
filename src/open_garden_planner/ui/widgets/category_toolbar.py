"""Three direct object palettes sharing the drawing toolbar row."""

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QSizePolicy, QToolBar, QToolButton, QWidget

from open_garden_planner.core.tools import ToolType
from open_garden_planner.ui.icons import get_icon
from open_garden_planner.ui.theme import CATEGORY_ICON_TINTS, is_dark_theme
from open_garden_planner.ui.widgets.category_dropdown import CategoryDropdown
from open_garden_planner.ui.widgets.gallery_data import (
    GalleryCategory,
    build_toolbar_categories,
    refresh_icon_thumbnails,
)


class CategoryToolbar(QToolBar):
    """Direct access to merged shapes, plants and built-element palettes."""

    tool_selected = pyqtSignal(ToolType)
    item_selected = pyqtSignal(object)
    database_search_requested = pyqtSignal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("Categories"), parent)

        originals = build_toolbar_categories()
        groups = (
            (self.tr("Shapes"), "rectangle", {"rectangle", "garden_bed"}),
            (self.tr("Plants"), "tree", {"tree", "shrub", "flower", "vegetable"}),
            (self.tr("Built elements"), "house", {"house", "furniture", "fence", "infrastructure", "vertical_container"}),
        )
        self._categories = [
            GalleryCategory(name, [item for c in originals if c.icon_name in members for item in c.items], icon)
            for name, icon, members in groups
        ]
        self._category_buttons: list[QToolButton] = []
        self._group_icons: list[str] = []
        self._category_dropdowns: list[CategoryDropdown] = []

        self._setup_toolbar()

    def _load_icon(self, icon_name: str) -> QIcon | None:
        """Themed icon with this category's muted identity tint (ADR-039)."""
        if not icon_name:
            return None
        return get_icon(icon_name, color=self._category_tint(icon_name))

    @staticmethod
    def _category_tint(icon_name: str) -> str | None:
        tints = CATEGORY_ICON_TINTS.get(icon_name)
        if tints is None:
            return None
        return tints[1] if is_dark_theme() else tints[0]

    def _setup_toolbar(self) -> None:
        self.setObjectName("CategoryToolbar")  # required for QMainWindow.saveState
        self.setMovable(False)
        self.setOrientation(Qt.Orientation.Horizontal)
        self.setIconSize(QSize(24, 24))

        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.addWidget(spacer)

        for category in self._categories:
            dropdown = CategoryDropdown(category, self, unified_plants=category.icon_name == "tree")
            dropdown.tool_selected.connect(self.tool_selected)
            dropdown.item_selected.connect(self.item_selected)
            dropdown.database_search_requested.connect(self.database_search_requested)
            self._category_dropdowns.append(dropdown)

            self._add_palette_button(category, dropdown)

    def minimumSizeHint(self) -> QSize:
        """Reserve the three palette buttons before CAD tools overflow."""
        return self.sizeHint()

    def _add_palette_button(self, category: GalleryCategory, dropdown: CategoryDropdown) -> None:
        name, icon_name = category.name, category.icon_name
        button = QToolButton(self)
        button.setCheckable(False)
        button.setText(name.replace("&", "&&"))
        button.setToolTip(name)
        button.setMinimumHeight(36)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

        icon = self._load_icon(icon_name)
        if icon:
            button.setIcon(icon)
            button.setIconSize(QSize(24, 24))
        button.clicked.connect(lambda: dropdown.show_below(button))

        self.addWidget(button)
        self._category_buttons.append(button)
        self._group_icons.append(icon_name)

    def refresh_theme_icons(self) -> None:
        """Re-tint category buttons + dropdown thumbnails after a theme switch."""
        for button, icon_name in zip(
            self._category_buttons, self._group_icons, strict=True
        ):
            icon = self._load_icon(icon_name)
            if icon is not None:
                button.setIcon(icon)
        refresh_icon_thumbnails(self._categories)
        for dropdown in self._category_dropdowns:
            dropdown.refresh_thumbnails()
