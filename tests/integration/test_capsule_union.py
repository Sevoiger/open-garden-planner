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
