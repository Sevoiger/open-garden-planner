"""The existing two-shape Union workflow makes a dimensionally correct capsule."""

import math

import pytest
from PyQt6.QtCore import QRectF
from PyQt6.QtWidgets import QMessageBox

from open_garden_planner.core.project import ProjectManager
from open_garden_planner.core.shape_boolean import _polygon_area
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.canvas.canvas_view import CanvasView
from open_garden_planner.ui.canvas.items import CircleItem, PolygonItem, RectangleItem


def test_rectangle_with_round_ends_union_undo_save_reload(qtbot, monkeypatch, tmp_path):
    scene = CanvasScene()
    view = CanvasView(scene)
    qtbot.addWidget(view)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Ok)
    rect = RectangleItem(100, 100, 300, 200)
    left = CircleItem(100, 200, 100)
    right = CircleItem(400, 200, 100)
    for item in (rect, left, right):
        scene.addItem(item)
    rect.setSelected(True)
    left.setSelected(True)
    view.boolean_operation("union")
    first = next(i for i in scene.items() if isinstance(i, PolygonItem))
    right.setSelected(True)
    view.boolean_operation("union")
    result = next(i for i in scene.items() if isinstance(i, PolygonItem))
    assert result is not first
    assert result.polygon().boundingRect() == QRectF(0, 100, 500, 200)
    # Curved boundaries are polygonized by Qt's established boolean engine.
    assert _polygon_area(result.polygon()) == pytest.approx(60000 + math.pi * 100**2, rel=0.005)
    assert view.command_manager.undo_depth == 2
    view.command_manager.undo()
    assert first.scene() is scene and right.scene() is scene
    view.command_manager.undo()
    assert all(i.scene() is scene for i in (rect, left, right))
    view.command_manager.redo()
    view.command_manager.redo()
    path = tmp_path / "capsule.ogp"
    ProjectManager().save(scene, path)
    restored = CanvasScene()
    ProjectManager().load(restored, path)
    polygon = next(i for i in restored.items() if isinstance(i, PolygonItem))
    assert polygon.polygon() == result.polygon()


def test_merge_three_shapes_from_context_menu_is_one_undo_step(qtbot, monkeypatch, tmp_path):
    from unittest.mock import MagicMock

    from PyQt6.QtWidgets import QGraphicsSceneContextMenuEvent, QMenu

    scene = CanvasScene()
    view = CanvasView(scene)
    qtbot.addWidget(view)
    monkeypatch.setattr(QMessageBox, 'question', lambda *_args: QMessageBox.StandardButton.Ok)
    originals = [RectangleItem(100, 100, 300, 200), CircleItem(100, 200, 100), CircleItem(400, 200, 100)]
    for item in originals:
        scene.addItem(item)
        item.setSelected(True)
    originals[0].metadata['future_field'] = {'keep': True}

    def choose_union(menu, *_args):
        submenu = next(a.menu() for a in menu.actions() if a.menu() and a.text() == 'Boolean')
        assert [a.text() for a in submenu.actions()] == ['Union']
        return submenu.actions()[0]

    monkeypatch.setattr(QMenu, 'exec', choose_union)
    event = MagicMock(spec=QGraphicsSceneContextMenuEvent)
    originals[0].contextMenuEvent(event)
    result = next(i for i in scene.items() if isinstance(i, PolygonItem))
    assert all(i.scene() is None for i in originals)
    assert result.metadata["future_field"] == {"keep": True}
    assert result.polygon().boundingRect() == QRectF(0, 100, 500, 200)
    assert _polygon_area(result.polygon()) == pytest.approx(60000 + math.pi * 100**2, rel=0.005)
    assert view.command_manager.undo_depth == 1
    view.command_manager.undo()
    assert all(i.scene() is scene for i in originals)
    assert originals[0].metadata['future_field'] == {'keep': True}
    view.command_manager.redo()
    path = tmp_path / 'three-shape-capsule.ogp'
    ProjectManager().save(scene, path)
    restored = CanvasScene()
    ProjectManager().load(restored, path)
    polygon = next(i for i in restored.items() if isinstance(i, PolygonItem))
    assert polygon.polygon() == result.polygon()
    assert polygon.metadata['future_field'] == {'keep': True}


@pytest.mark.parametrize('case', ['gap', 'cancel', 'plant', 'fixed', 'different_data'])
def test_merge_refusal_or_cancel_leaves_scene_unchanged(qtbot, monkeypatch, case):
    from open_garden_planner.core.object_types import ObjectType

    scene = CanvasScene()
    view = CanvasView(scene)
    qtbot.addWidget(view)
    originals = [RectangleItem(100, 100, 300, 200), CircleItem(100, 200, 100), CircleItem(400, 200, 100)]
    for item in originals:
        item.layer_id = scene.active_layer.id
        scene.addItem(item)
        item.setSelected(True)
    if case == 'gap':
        originals[2].setPos(2000, 2000)
    elif case == 'plant':
        originals[2].object_type = ObjectType.TREE
    elif case == 'fixed':
        originals[0]._toggle_position_lock()
    elif case == 'different_data':
        originals[0].metadata['future_field'] = 'a'
        originals[1].metadata['future_field'] = 'b'

    def confirm(*_args):
        assert case == 'cancel', 'Invalid selections must be refused before confirmation'
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, 'question', confirm)
    depth = view.command_manager.undo_depth
    before = [ProjectManager._serialize_item(item) for item in originals]
    view.boolean_operation('union')
    assert all(item.scene() is scene for item in originals)
    assert [ProjectManager._serialize_item(item) for item in originals] == before
    assert view.command_manager.undo_depth == depth
    assert not any(isinstance(item, PolygonItem) for item in scene.items())


def test_touching_rotated_ellipse_and_rectangles_merge(qtbot, monkeypatch):
    from open_garden_planner.ui.canvas.items import EllipseItem

    scene = CanvasScene()
    view = CanvasView(scene)
    qtbot.addWidget(view)
    monkeypatch.setattr(QMessageBox, 'question', lambda *_args: QMessageBox.StandardButton.Ok)
    ellipse = EllipseItem(0, 0, 100, 50)
    ellipse.rotation_angle = 37
    # Two rectangles share an edge; the ellipse intersects the first.
    for item in (ellipse, RectangleItem(0, 0, 100, 100), RectangleItem(100, 0, 100, 100)):
        scene.addItem(item)
        item.setSelected(True)
    view.boolean_operation('union')
    assert len([i for i in scene.items() if isinstance(i, PolygonItem)]) == 1
    assert view.command_manager.undo_depth == 1
