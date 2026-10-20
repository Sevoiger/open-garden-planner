"""Personal fill PNGs repeat at a real size and survive undo and plan reload."""

from copy import deepcopy

import pytest
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QComboBox, QDoubleSpinBox, QFileDialog, QMessageBox, QPushButton

from open_garden_planner.core.fill_patterns import FillPattern, create_pattern_brush
from open_garden_planner.core.personal_fill_pattern import PATTERN_KEY
from open_garden_planner.core.project import ProjectManager
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.canvas.canvas_view import CanvasView
from open_garden_planner.ui.canvas.items import CircleItem, EllipseItem, PolygonItem, RectangleItem
from open_garden_planner.ui.panels.properties_panel import ColorButton, PropertiesPanel


@pytest.fixture
def png(tmp_path, qtbot):
    image = QImage(8, 4, QImage.Format.Format_ARGB32)
    image.fill(QColor("red"))
    for x in range(8):
        for y in (2, 3):
            image.setPixelColor(x, y, QColor("blue"))
    path = tmp_path / "tile.png"
    assert image.save(str(path), "PNG")
    return path


def _render_brush(brush):
    image = QImage(180, 100, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.translate(0, 100)
    painter.scale(1, -1)
    painter.fillRect(QRectF(0, 0, 180, 100), brush)
    painter.end()
    return image


@pytest.mark.parametrize("shape", ["rectangle", "polygon", "circle", "ellipse"])
def test_import_scale_undo_reload_and_builtin_switch(shape, qtbot, monkeypatch, png, tmp_path):
    scene = CanvasScene()
    view = CanvasView(scene)
    panel = PropertiesPanel()
    panel.set_command_manager(view.command_manager)
    qtbot.addWidget(view)
    qtbot.addWidget(panel)
    if shape == "rectangle":
        item = RectangleItem(0, 0, 400, 200)
    elif shape == "polygon":
        item = PolygonItem([QPointF(0, 0), QPointF(400, 0), QPointF(400, 200), QPointF(0, 200)])
    elif shape == "circle":
        item = CircleItem(200, 200, 100)
    else:
        item = EllipseItem(0, 0, 400, 200)
    item.metadata["future_field"] = {"keep": True}
    scene.addItem(item)
    panel.set_selected_items([item])
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(png), "PNG"))
    panel.findChild(QPushButton, "import_fill_png").click()
    assert PATTERN_KEY in item.metadata
    assert view.command_manager.undo_depth == 1
    width = panel.findChild(QDoubleSpinBox, "fill_tile_width")
    assert width.isEnabled()
    width.setValue(80)
    assert view.command_manager.undo_depth == 2
    record = deepcopy(item.metadata[PATTERN_KEY])
    assert record["width_cm"] == 80
    assert item.brush().transform().m11() == 10
    assert item.brush().textureImage().height() == 4
    # Real repeating brush through the same Y-up painter transform as the plan.
    rendered = _render_brush(item.brush())
    assert rendered.pixelColor(10, 85) == QColor("blue")
    assert rendered.pixelColor(90, 85) == QColor("blue")
    assert rendered.pixelColor(10, 65) == QColor("red")
    assert rendered.pixelColor(10, 45) == QColor("blue")
    view.command_manager.undo()
    assert item.metadata[PATTERN_KEY]["width_cm"] == 256
    view.command_manager.redo()
    assert item.metadata[PATTERN_KEY] == record
    # Changing the built-in color must preserve original imported colors, including undo.
    button = ColorButton(QColor("green"))
    qtbot.addWidget(button)
    panel._on_color_changed(item, "fill_color", button)
    assert item.brush().textureImage().pixelColor(0, 0) == QColor("blue")
    view.command_manager.undo()
    assert item.metadata[PATTERN_KEY] == record
    path = tmp_path / "personal-fill.ogp"
    ProjectManager().save(scene, path)
    png.unlink()
    restored_scene = CanvasScene()
    ProjectManager().load(restored_scene, path)
    restored = next(i for i in restored_scene.items() if isinstance(i, type(item)))
    assert restored.metadata[PATTERN_KEY] == record
    assert restored.metadata["future_field"] == {"keep": True}
    assert restored.brush().transform().m11() == 10
    assert _render_brush(restored.brush()) == rendered
    # Choosing a built-in texture removes the override in the same undo step.
    combo = next(c for c in panel.findChildren(QComboBox) if c.findData(FillPattern.GRASS) >= 0)
    combo.setCurrentIndex(combo.findData(FillPattern.GRASS))
    assert PATTERN_KEY not in item.metadata
    assert item.fill_pattern == FillPattern.GRASS
    view.command_manager.undo()
    assert item.metadata[PATTERN_KEY] == record
    assert _render_brush(item.brush()) == rendered
    panel.findChild(QPushButton, "remove_fill_png").click()
    assert PATTERN_KEY not in item.metadata
    view.command_manager.undo()
    assert item.metadata[PATTERN_KEY] == record


@pytest.mark.parametrize("record", [None, 42, {}, {"png": "broken", "width_cm": 80},
                                    {"png": "", "width_cm": float("nan")},
                                    {"png": "", "width_cm": True}])
def test_invalid_embedded_texture_uses_builtin(qtbot, record):
    expected = create_pattern_brush(FillPattern.SOIL, QColor("brown"))
    assert create_pattern_brush(FillPattern.SOIL, QColor("brown"), record) == expected


def test_invalid_import_leaves_plan_and_history_unchanged(qtbot, monkeypatch, tmp_path):
    scene = CanvasScene()
    view = CanvasView(scene)
    panel = PropertiesPanel()
    qtbot.addWidget(view)
    qtbot.addWidget(panel)
    panel.set_command_manager(view.command_manager)
    item = RectangleItem(0, 0, 200, 200)
    scene.addItem(item)
    panel.set_selected_items([item])
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not a png")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(bad), "PNG"))
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append(args))
    before = deepcopy(item.metadata)
    panel.findChild(QPushButton, "import_fill_png").click()
    assert messages
    assert item.metadata == before
    assert view.command_manager.undo_depth == 0
