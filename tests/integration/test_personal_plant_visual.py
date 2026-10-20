"""Owner PNG → plan/undo/reload and explicit reusable library preference."""

import base64
import json
from copy import deepcopy

import pytest
from PyQt6.QtCore import QPointF, QRect, Qt
from PyQt6.QtGui import QColor, QImage, QPainter

from open_garden_planner.core.object_types import ObjectType
from open_garden_planner.core.personal_plant_visual import (
    VISUAL_KEY,
    import_png,
    personal_visual_pixmap,
)
from open_garden_planner.core.project import ProjectManager
from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services.personal_plant_visual import remember_visual
from open_garden_planner.services.plant_library import get_plant_library
from open_garden_planner.services.species_catalog import offline_catalog
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.canvas.canvas_view import CanvasView
from open_garden_planner.ui.canvas.items import CircleItem
from open_garden_planner.ui.panels.plant_database_panel import PlantDatabasePanel
from open_garden_planner.ui.widgets.catalog_palette import catalog_item


@pytest.fixture
def png(qtbot, tmp_path):
    image = QImage(40, 20, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.fillRect(QRect(10, 0, 20, 10), QColor("red"))
    painter.fillRect(QRect(10, 10, 20, 10), QColor("blue"))
    painter.end()
    path = tmp_path / "rose.png"
    assert image.save(str(path), "PNG")
    return path


def record():
    return PlantSpeciesData(common_name="Rosier Ketchup & Mustard",
                            scientific_name="Rosa Wekzazette", source_id="my-rose",
                            data_source="custom", max_spread_cm=120,
                            raw_data={"cultivar": "Ketchup & Mustard"}).to_dict()


def test_png_keeps_alpha_aspect_and_orientation(qtbot, png):
    snapshot = record()
    encoded = import_png(png)
    assert base64.b64decode(encoded) == png.read_bytes()
    snapshot["raw_data"][VISUAL_KEY] = encoded
    icon = personal_visual_pixmap(snapshot).toImage()
    assert icon.pixelColor(512, 300) == QColor("red")
    assert icon.pixelColor(512, 700) == QColor("blue")
    assert icon.pixelColor(512, 100).alpha() == 0
    assert icon.pixelColor(100, 512).alpha() == 0
    canvas = personal_visual_pixmap(snapshot, canvas=True).toImage()
    assert canvas.pixelColor(512, 300) == QColor("blue")
    assert catalog_item(snapshot).thumbnail.toImage() == icon


@pytest.mark.parametrize("value", [None, 42, {}, "not base64", base64.b64encode(b"not a PNG").decode()])
def test_bad_embedded_art_falls_back_without_error(qtbot, value):
    snapshot = record()
    snapshot["raw_data"][VISUAL_KEY] = value
    assert personal_visual_pixmap(snapshot) is None
    assert catalog_item(snapshot).thumbnail is not None


def test_import_rejects_corrupt_and_oversize_png(qtbot, tmp_path):
    path = tmp_path / "bad.png"
    for data in (b"broken", b"x" * (4 * 1024 * 1024 + 1)):
        path.write_bytes(data)
        with pytest.raises(ValueError):
            import_png(path)


def test_import_button_undo_redo_portable_save_and_render(qtbot, monkeypatch, png, tmp_path):
    scene = CanvasScene()
    view = CanvasView(scene)
    panel = PlantDatabasePanel()
    qtbot.addWidget(view)
    qtbot.addWidget(panel)
    original = record()
    item = CircleItem(300, 300, 60, object_type=ObjectType.SHRUB,
                      metadata={"plant_species": deepcopy(original)})
    scene.addItem(item)
    panel.set_selected_items([item])
    from PyQt6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(png), "PNG"))
    panel.import_visual_button.click()
    changed = deepcopy(item.metadata["plant_species"])
    assert changed["raw_data"][VISUAL_KEY] == import_png(png)
    assert item.radius == 60 and item.center == QPointF(300, 300)
    assert view.command_manager.undo_depth == 1
    view.command_manager.undo()
    assert item.metadata["plant_species"] == original
    view.command_manager.redo()
    assert item.metadata["plant_species"] == changed
    path = tmp_path / "portable.ogp"
    ProjectManager().save(scene, path)
    png.unlink()
    restored = CanvasScene()
    ProjectManager().load(restored, path)
    plant = next(i for i in restored.items() if isinstance(i, CircleItem))
    assert plant.metadata["plant_species"] == changed
    # Exercise the actual item painter under the canvas Y-up transform.
    image = QImage(240, 240, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.translate(120, 120)
    painter.scale(1, -1)
    painter.translate(-plant.rect().center())
    plant.paint(painter, None)
    painter.end()
    assert image.pixelColor(120, 105).red() > 200
    assert image.pixelColor(120, 135).blue() > 200
    panel.remove_visual_button.click()
    assert VISUAL_KEY not in item.metadata["plant_species"]["raw_data"]
    view.command_manager.undo()
    assert item.metadata["plant_species"] == changed


def test_remember_patches_only_art_and_preserves_unknown_data(qtbot, png):
    library = get_plant_library()
    library.add_plant(PlantSpeciesData.from_dict(record()))
    path = library._library_path
    before = json.loads(path.read_bytes())
    before["private_extra"] = "keep"
    before["plants"]["my-rose"]["future_field"] = {"keep": True}
    path.write_text(json.dumps(before))
    original = path.read_bytes()
    snapshot = record()
    snapshot["common_name"] = "Do not overwrite library name"
    snapshot["raw_data"][VISUAL_KEY] = import_png(png)
    assert remember_visual(library, snapshot) == "my-rose"
    after = json.loads(path.read_bytes())
    expected = deepcopy(before)
    expected["plants"]["my-rose"]["raw_data"][VISUAL_KEY] = snapshot["raw_data"][VISUAL_KEY]
    assert after == expected
    assert next(path.parent.glob("*.before-visual-*.json")).read_bytes() == original
    assert offline_catalog()["my-rose"]["raw_data"][VISUAL_KEY] == snapshot["raw_data"][VISUAL_KEY]
    snapshot["raw_data"].pop(VISUAL_KEY)
    remember_visual(library, snapshot)
    assert json.loads(path.read_bytes()) == before


def test_remember_new_api_plant_preserves_source_identity(qtbot, png):
    snapshot = record()
    snapshot.update(source_id="trefle:123", data_source="trefle")
    snapshot["raw_data"][VISUAL_KEY] = import_png(png)
    remember_visual(get_plant_library(), snapshot)
    assert offline_catalog()["trefle:123"] == snapshot
