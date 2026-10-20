"""Object context locks reuse FIXED constraints, persist and undo normally."""

from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QGraphicsSceneContextMenuEvent, QMenu

from open_garden_planner.core.constraints import ConstraintType
from open_garden_planner.core.project import ProjectManager
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.canvas.canvas_view import CanvasView
from tests.integration.test_arrange_context_menu import (
    _make_circle,
    _make_ellipse,
    _make_polygon,
    _make_polyline,
    _make_rectangle,
    _make_text,
)


@pytest.mark.parametrize('factory', [_make_rectangle, _make_circle, _make_ellipse,
                                    _make_polygon, _make_polyline, _make_text])
def test_context_lock_unlock_save_and_undo(qtbot, monkeypatch, tmp_path, factory):
    scene = CanvasScene()
    view = CanvasView(scene)
    qtbot.addWidget(view)
    item = factory(layer_id=scene.active_layer.id)
    item.setPos(400, 500)
    scene.addItem(item)
    original = item.pos()

    def choose_lock(menu, *_args):
        action = next(a for a in menu.actions() if a.objectName() == 'toggle_position_lock')
        assert action.isEnabled()
        action.trigger()
        return action

    monkeypatch.setattr(QMenu, 'exec', choose_lock)
    event = MagicMock(spec=QGraphicsSceneContextMenuEvent)
    event.screenPos.return_value = QPoint()
    item.contextMenuEvent(event)
    fixed = list(scene.constraint_graph.constraints.values())
    assert len(fixed) == 1 and fixed[0].constraint_type == ConstraintType.FIXED
    assert (fixed[0].target_x, fixed[0].target_y) == (original.x(), original.y())
    assert view.command_manager.undo_depth == 1
    # A real Y-up viewport must show the shackle above the body.
    group = scene._dimension_line_manager._groups[fixed[0].constraint_id]
    body, shackle = group.items
    assert view.mapFromScene(shackle.sceneBoundingRect().center()).y() < view.mapFromScene(body.sceneBoundingRect().center()).y()
    path = tmp_path / 'locked.ogp'
    ProjectManager().save(scene, path)
    restored = CanvasScene()
    ProjectManager().load(restored, path)
    saved = list(restored.constraint_graph.constraints.values())
    assert len(saved) == 1 and saved[0].constraint_type == ConstraintType.FIXED
    assert saved[0].anchor_a.item_id == item.item_id
    view.command_manager.undo()
    assert not scene.constraint_graph.constraints
    view.command_manager.redo()
    assert len(scene.constraint_graph.constraints) == 1
    item.contextMenuEvent(event)
    assert not scene.constraint_graph.constraints
    assert item.pos() == original
    view.command_manager.undo()
    assert len(scene.constraint_graph.constraints) == 1
    view.command_manager.redo()
    assert not scene.constraint_graph.constraints


def test_lock_action_respects_locked_layer(qtbot):
    scene = CanvasScene()
    view = CanvasView(scene)
    qtbot.addWidget(view)
    item = _make_rectangle(layer_id=scene.active_layer.id)
    scene.addItem(item)
    scene.update_layer_lock(item.layer_id, True)
    menu = QMenu()
    item._build_arrange_menu(menu)
    action = next(a for a in menu.actions() if a.objectName() == 'toggle_position_lock')
    assert not action.isEnabled()
    item._toggle_position_lock()
    assert not scene.constraint_graph.constraints


def test_locked_position_cannot_be_changed_in_properties(qtbot):
    from PyQt6.QtWidgets import QDoubleSpinBox

    from open_garden_planner.ui.panels.properties_panel import PropertiesPanel

    scene = CanvasScene()
    view = CanvasView(scene)
    panel = PropertiesPanel()
    qtbot.addWidget(view)
    qtbot.addWidget(panel)
    panel.set_command_manager(view.command_manager)
    item = _make_rectangle(layer_id=scene.active_layer.id)
    item.setPos(400, 500)
    scene.addItem(item)
    item._toggle_position_lock()
    original = item.pos()
    corner = item.mapToScene(item.boundingRect().topLeft())
    x, y = QDoubleSpinBox(), QDoubleSpinBox()
    for spin in (x, y):
        qtbot.addWidget(spin)
        spin.setRange(-100000, 100000)
    x.setValue(corner.x() + 100)
    y.setValue(corner.y() + 100)
    depth = view.command_manager.undo_depth
    panel._on_position_changed(item, x, y)
    assert item.pos() == original
    assert x.value() == pytest.approx(corner.x(), abs=0.1)
    assert y.value() == pytest.approx(corner.y(), abs=0.1)
    assert view.command_manager.undo_depth == depth
    item._toggle_position_lock()
    x.setValue(corner.x() + 100)
    y.setValue(corner.y())
    panel._on_position_changed(item, x, y)
    assert item.pos().x() == original.x() + 100
    view.command_manager.undo()
    assert item.pos() == original
