"""Toolbar with object-category dropdowns and the global search field.

Three labelled menus expose every object subcategory, with global search on
the same row. The drawing and CAD tools occupy a separate row.
"""

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import QMenu, QSizePolicy, QToolBar, QToolButton, QWidget

from open_garden_planner.core.tools import ToolType
from open_garden_planner.ui.icons import get_icon
from open_garden_planner.ui.theme import CATEGORY_ICON_TINTS, is_dark_theme
from open_garden_planner.ui.widgets.category_dropdown import CategoryDropdown
from open_garden_planner.ui.widgets.gallery_data import (
    GalleryCategory,
    build_toolbar_categories,
    refresh_icon_thumbnails,
)
from open_garden_planner.ui.widgets.global_search import GlobalSearchField


class CategoryToolbar(QToolBar):
    """Category-buttons + global search, positioned right of constraints."""

    tool_selected = pyqtSignal(ToolType)
    item_selected = pyqtSignal(object)
    database_search_requested = pyqtSignal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("Categories"), parent)

        self._categories: list[GalleryCategory] = build_toolbar_categories()
        self._category_buttons: list[QToolButton] = []
        self._group_icons: list[str] = []
        self._category_dropdowns: list[CategoryDropdown] = []
        self._search_field: GlobalSearchField | None = None
        self._focus_shortcut: QShortcut | None = None

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

        for category in self._categories:
            dropdown = CategoryDropdown(category, self)
            dropdown.tool_selected.connect(self.tool_selected)
            dropdown.item_selected.connect(self.item_selected)
            dropdown.database_search_requested.connect(self.database_search_requested)
            self._category_dropdowns.append(dropdown)

        for name, icon, categories in (
            (self.tr("Shapes"), "rectangle", {"rectangle", "garden_bed"}),
            (self.tr("Plants"), "tree", {"tree", "shrub", "flower", "vegetable"}),
            (self.tr("Built elements"), "house", {"house", "furniture", "fence", "infrastructure", "vertical_container"}),
        ):
            self._add_group_button(name, icon, categories)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.addWidget(spacer)

        self._search_field = GlobalSearchField(self._categories, self)
        self._search_field.tool_selected.connect(self.tool_selected)
        self._search_field.item_selected.connect(self.item_selected)
        self.addWidget(self._search_field)

        self._focus_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        self._focus_shortcut.activated.connect(self._focus_search)

    def _add_group_button(self, name: str, icon_name: str, categories: set[str]) -> None:
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
        menu = QMenu(button)
        for category, dropdown in zip(self._categories, self._category_dropdowns, strict=True):
            if category.icon_name not in categories:
                continue
            action = menu.addAction(self._load_icon(category.icon_name) or QIcon(), category.name.replace("&", "&&"))
            assert action is not None
            action.setData(category.icon_name)
            # Let the menu close before opening another Qt popup.
            action.triggered.connect(lambda _checked=False, d=dropdown, b=button:
                                     QTimer.singleShot(0, lambda: d.show_below(b)))
        button.setMenu(menu)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        self.addWidget(button)
        self._category_buttons.append(button)
        self._group_icons.append(icon_name)

    def _focus_search(self) -> None:
        if self._search_field is not None:
            self._search_field.setFocus()
            self._search_field.selectAll()

    def refresh_theme_icons(self) -> None:
        """Re-tint category buttons + dropdown thumbnails after a theme switch."""
        for button, icon_name in zip(
            self._category_buttons, self._group_icons, strict=True
        ):
            icon = self._load_icon(icon_name)
            if icon is not None:
                button.setIcon(icon)
            menu = button.menu()
            if menu is not None:
                for action in menu.actions():
                    icon = self._load_icon(action.data())
                    if icon is not None:
                        action.setIcon(icon)
        refresh_icon_thumbnails(self._categories)
        for dropdown in self._category_dropdowns:
            dropdown.refresh_thumbnails()
