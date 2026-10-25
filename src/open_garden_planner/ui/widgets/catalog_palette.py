"""Bounded catalog rows shared by category palettes and global object search."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QCoreApplication, QMimeData, QModelIndex, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QDrag, QIcon, QPixmap
from PyQt6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem, QWidget

from open_garden_planner.core.object_types import ObjectType
from open_garden_planner.core.personal_plant_visual import personal_visual_pixmap
from open_garden_planner.core.plant_renderer import (
    _CATEGORIES_DIR,
    _CATEGORY_FILES,
    _SPECIES_DIR,
    PlantCategory,
)
from open_garden_planner.core.tools import ToolType
from open_garden_planner.services.plant_palette import (
    catalog_matches,
    catalog_name_rank,
    illustration_for,
    palette_group,
)
from open_garden_planner.ui.widgets.gallery_data import GalleryItem, render_svg_thumbnail

PAGE_SIZE = 48
PALETTE_TYPES = {
    "tree": (ToolType.TREE, ObjectType.TREE, PlantCategory.ROUND_DECIDUOUS),
    "shrub": (ToolType.SHRUB, ObjectType.SHRUB, PlantCategory.SPREADING_SHRUB),
    "flower": (ToolType.PERENNIAL, ObjectType.PERENNIAL, PlantCategory.FLOWERING_PERENNIAL),
    "vegetable": (ToolType.PERENNIAL, ObjectType.PERENNIAL, PlantCategory.VEGETABLE),
}
ICON_GROUPS = {"tree": "tree", "shrub": "shrub", "flower": "flower", "vegetable": "vegetable"}


@lru_cache(maxsize=256)
def _thumbnail(path: Path) -> QPixmap | None:
    return render_svg_thumbnail(path)


def matching_records(records: dict[str, dict[str, Any]], text: str, *, group: str | None = None,
                     layers: set[str] | None = None, functions: set[str] | None = None) -> list[dict[str, Any]]:
    return sorted((r for r in records.values() if catalog_matches(
        r, text, group=group, layers=layers, functions=functions,
    )), key=lambda r: (catalog_name_rank(r, text), str(r.get("common_name", "")).casefold(),
                      str(r.get("scientific_name", "")).casefold()))


def catalog_item(record: dict[str, Any], fallback_group: str = "flower") -> GalleryItem:
    group = palette_group(record) or fallback_group
    tool, object_type, category = PALETTE_TYPES[group]
    art = illustration_for(record)
    species = art["id"].replace("_", " ") if art else ""
    path = (_SPECIES_DIR / f"{art['id']}.svg" if art else
            _CATEGORIES_DIR / f"{_CATEGORY_FILES[category]}.svg")
    return GalleryItem(
        name=record.get("common_name") or record.get("scientific_name", ""),
        tool_type=tool, object_type=object_type, plant_category=category,
        species=species, thumbnail=personal_visual_pixmap(record) or _thumbnail(path), species_data=record,
    )


def gallery_mime(item: GalleryItem) -> QMimeData:
    mime = QMimeData()
    text = f"gallery:{item.tool_type.name}"
    if item.species:
        text += f":species={item.species}"
    if item.plant_category:
        text += f":category={item.plant_category.name}"
    mime.setText(text)
    if item.species_data is not None:
        mime.setData("application/x-ogp-plant-data", json.dumps(item.species_data).encode("utf-8"))
    return mime


class CatalogList(QListWidget):
    """Only the current page gets icons; no thousands of eagerly built widgets."""

    item_chosen = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setIconSize(QSize(48, 48))
        self.setWordWrap(True)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.setMinimumHeight(180)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.itemClicked.connect(self._choose)
        self.itemActivated.connect(self._choose)

    def populate(self, records: list[dict[str, Any]], group: str) -> None:
        self.clear()
        for record in records:
            item = catalog_item(record, group)
            scientific = record.get("scientific_name", "")
            label = item.name if scientific == item.name or not scientific else f"{item.name}\n{scientific}"
            row = QListWidgetItem(QIcon(item.thumbnail) if item.thumbnail is not None else QIcon(), label)
            row.setData(Qt.ItemDataRole.UserRole, item)
            row.setToolTip(QCoreApplication.translate(
                "CatalogPalette", "{name}\nStylized plan symbol; cultivar appearance may differ.\nChoose, then set center and edge on the plan. Uncheck size choice for one-click placement."
            ).format(name=label))
            self.addItem(row)

    def _choose(self, row: QListWidgetItem) -> None:
        self.item_chosen.emit(row.data(Qt.ItemDataRole.UserRole))

    def mime_for_index(self, index: QModelIndex) -> QMimeData:
        """The same MIME payload for native drag and authenticated UI control."""
        row = self.itemFromIndex(index)
        if row is None:
            raise ValueError("No catalogue item at this index")
        return gallery_mime(row.data(Qt.ItemDataRole.UserRole))

    def startDrag(self, _supported_actions: Qt.DropAction) -> None:  # noqa: N802
        row = self.currentItem()
        if row is None:
            return
        item = row.data(Qt.ItemDataRole.UserRole)
        drag = QDrag(self)
        drag.setMimeData(self.mime_for_index(self.indexFromItem(row)))
        if item.thumbnail is not None:
            drag.setPixmap(item.thumbnail)
        drag.exec(Qt.DropAction.CopyAction)
