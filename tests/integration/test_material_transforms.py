"""Material angle and repetition size are independent, portable, undoable shape style."""

from copy import deepcopy

import pytest
from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QComboBox, QDoubleSpinBox

from open_garden_planner.app import visual_paths
from open_garden_planner.core.fill_patterns import FillPattern, create_pattern_brush
from open_garden_planner.core.fill_transform import fill_transform_values
from open_garden_planner.core.item_look import copy_visual_style
from open_garden_planner.core.png_asset import import_png
from open_garden_planner.core.project import ProjectManager
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.canvas.items import CircleItem, EllipseItem, PolygonItem, RectangleItem
from open_garden_planner.ui.panels.properties_panel import ColorButton, PropertiesPanel
from tests.integration.test_personal_fill_pattern import _render_brush


@pytest.fixture
def png(qtbot, tmp_path):
    image = QImage(8, 4, QImage.Format.Format_ARGB32)
    image.fill(QColor('red'))
    for x in range(8):
        for y in (2, 3):
            image.setPixelColor(x, y, QColor('blue'))
    path = tmp_path / 'wood.png'
    assert image.save(str(path))
    return path


@pytest.mark.parametrize('shape', ['rectangle', 'polygon', 'circle', 'ellipse'])
@pytest.mark.parametrize('personal', [False, True])
def test_angle_scale_geometry_undo_colour_and_save(canvas, qtbot, tmp_path, png, shape, personal):
    item = {'rectangle': lambda: RectangleItem(100, 100, 400, 200),
            'polygon': lambda: PolygonItem([QPointF(100, 100), QPointF(500, 100),
                                            QPointF(500, 300), QPointF(100, 300)]),
            'circle': lambda: CircleItem(300, 200, 100),
            'ellipse': lambda: EllipseItem(100, 100, 400, 200)}[shape]()
    item.fill_pattern = FillPattern.WOOD
    if personal:
        item.metadata['personal_fill_pattern'] = {'png': import_png(png), 'width_cm': 80}
    item.metadata['unknown'] = {'keep': True}
    item._setup_styling()
    canvas.scene().addItem(item)
    before_geometry = item.sceneBoundingRect()
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    panel.set_command_manager(canvas.command_manager)
    panel.set_selected_items([item])
    angle = panel.findChild(QDoubleSpinBox, 'fill_pattern_angle')
    scale = panel.findChild(QDoubleSpinBox, 'fill_pattern_scale')
    angle.setValue(90)
    scale.setValue(200)
    assert canvas.command_manager.undo_depth == 2
    record = deepcopy(item.metadata['fill_transform'])
    assert record == {'angle_deg': 90, 'scale': 2}
    assert item.sceneBoundingRect() == before_geometry
    brush = item.brush()
    expected_scale = 20 if personal else 2
    assert brush.transform().m11() == pytest.approx(0, abs=1e-12)
    assert brush.transform().m12() == pytest.approx(expected_scale)
    color_button = ColorButton(QColor('green'))
    qtbot.addWidget(color_button)
    panel._on_color_changed(item, 'fill_color', color_button)
    assert item.brush().transform() == brush.transform()
    canvas.command_manager.undo()
    assert item.metadata['fill_transform'] == record
    # The transform changes repetition without modifying or tinting the embedded PNG.
    rendered = _render_brush(brush)
    canvas.command_manager.undo()
    assert fill_transform_values(item.metadata['fill_transform']) == (90, 1)
    panel._refresh_field_values()
    assert scale.value() == 100
    canvas.command_manager.undo()
    assert 'fill_transform' not in item.metadata
    canvas.command_manager.redo()
    canvas.command_manager.redo()
    assert item.metadata['fill_transform'] == record
    duplicate = RectangleItem(0, 0, 400, 200)
    copy_visual_style(item, duplicate)
    duplicate._setup_styling()
    assert duplicate.brush().transform() == brush.transform()
    assert duplicate.metadata['fill_transform'] is not item.metadata['fill_transform']
    path = tmp_path / 'pattern.ogp'
    ProjectManager().save(canvas.scene(), path)
    png.unlink()
    loaded = CanvasScene()
    ProjectManager().load(loaded, path)
    restored = next(i for i in loaded.items() if isinstance(i, type(item)))
    assert restored.metadata['unknown'] == {'keep': True}
    assert restored.metadata['fill_transform'] == record
    assert restored.brush().transform() == brush.transform()
    assert _render_brush(restored.brush()) == rendered
    # The shared tinted texture cache has no per-item angle/scale state.
    assert create_pattern_brush(FillPattern.WOOD, item.fill_color or QColor('white')).transform().isIdentity()


def test_material_picker_embeds_png_and_keeps_transform(canvas, qtbot, tmp_path, png, monkeypatch):
    monkeypatch.setattr(visual_paths, 'visuals_dir', lambda _kind: tmp_path)
    item = RectangleItem(100, 100, 400, 200)
    item.metadata['fill_transform'] = {'angle_deg': 45, 'scale': 0.5}
    canvas.scene().addItem(item)
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    panel.set_command_manager(canvas.command_manager)
    panel.set_selected_items([item])
    combo = panel.findChild(QComboBox, 'personal_materials')
    assert combo.count() == 2
    combo.activated.emit(1)
    assert item.metadata['personal_fill_pattern']['source_name'] == 'wood.png'
    assert item.metadata['fill_transform'] == {'angle_deg': 45, 'scale': 0.5}
    assert item.brush().textureImage().width() == 8
    png.unlink()
    item._setup_styling()
    assert item.brush().textureImage().width() == 8
    canvas.command_manager.undo()
    assert 'personal_fill_pattern' not in item.metadata
    canvas.command_manager.redo()
    assert item.brush().textureImage().width() == 8


def test_asset_directories_accept_old_flat_layout_and_new_subfolders(tmp_path, monkeypatch):
    monkeypatch.setattr(visual_paths, '__file__', str(tmp_path / 'src/pkg/app/visual_paths.py'))
    root = tmp_path / 'visuals'
    root.mkdir()
    assert visual_paths.visuals_dir('plants') == root
    assert visual_paths.visuals_dir('materials') == root
    (root / 'plants').mkdir()
    (root / 'materials').mkdir()
    assert visual_paths.visuals_dir('plants') == root / 'plants'
    assert visual_paths.visuals_dir('materials') == root / 'materials'


@pytest.mark.parametrize('record', [None, 42, {'scale': 0}, {'scale': True}, {'scale': float('inf')},
                                    {'angle_deg': float('nan')}, {'angle_deg': True}])
def test_invalid_transform_falls_back_safely(qtbot, record):
    assert fill_transform_values(record) == (0, 1)
