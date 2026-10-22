"""An invisible background can be recovered without reimporting or losing undo."""

from pathlib import Path

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QContextMenuEvent, QPixmap
from PyQt6.QtWidgets import QInputDialog, QMenu

from open_garden_planner.app.application import GardenPlannerApp
from open_garden_planner.ui.canvas.items import BackgroundImageItem


def image(scene, tmp_path, name, opacity, locked):
    path = tmp_path / name
    pixmap = QPixmap(40, 30)
    pixmap.fill(Qt.GlobalColor.blue)
    assert pixmap.save(str(path))
    item = BackgroundImageItem(str(path))
    item.opacity, item.locked = opacity, locked
    scene.addItem(item)
    return item


@pytest.mark.parametrize("locked", [True, False])
def test_empty_click_restores_zero_opacity_with_undo_and_roundtrip(qtbot, monkeypatch, tmp_path, locked):
    win = GardenPlannerApp()
    qtbot.addWidget(win)
    win._confirm_discard_changes = lambda: True
    scene, view, project = win.canvas_scene, win.canvas_view, win._project_manager
    target = image(scene, tmp_path, 'permit.png', 0, locked)
    other = image(scene, tmp_path, 'other.png', 0.4, True)
    path = tmp_path / 'garden.ogp'
    project.save(scene, path)
    seen = []
    def execute(menu, _pos):
        submenu = next(a.menu() for a in menu.actions() if a.text() == 'Background Images')
        seen.extend(a.text() for a in submenu.actions())
        action = next(a for a in submenu.actions() if a.text().startswith('permit.png'))
        action.trigger()
        return action
    monkeypatch.setattr(QMenu, 'exec', execute)
    monkeypatch.setattr(QInputDialog, 'getInt', lambda *_args: (65, True))
    view.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(10, 10), QPoint(10, 10)))
    assert any('0%' in text and 'permit.png' in text for text in seen)
    assert len(seen) == 2
    assert target.opacity == 0.65 and target.locked == locked
    assert other.opacity == 0.4
    assert project.is_dirty
    view.command_manager.undo()
    assert target.opacity == 0 and other.opacity == 0.4
    view.command_manager.redo()
    assert target.opacity == 0.65
    project.save(scene, path)
    project.load(scene, path)
    restored = next(item for item in scene.items() if isinstance(item, BackgroundImageItem)
                    and Path(item.image_path).name == 'permit.png')
    assert restored.opacity == 0.65 and restored.locked == locked


@pytest.mark.parametrize(('value', 'accepted'), [(0, True), (85, False)])
def test_cancel_or_unchanged_opacity_does_not_create_command(canvas, qtbot, monkeypatch, tmp_path, value, accepted):
    target = image(canvas.scene(), tmp_path, 'permit.png', 0, True)
    monkeypatch.setattr(QInputDialog, 'getInt', lambda *_args: (value, accepted))
    target._show_opacity_dialog()
    assert target.opacity == 0
    assert canvas.command_manager.undo_depth == 0


def test_menu_without_background_still_imports(canvas, qtbot):
    menu = canvas._build_empty_canvas_menu()
    assert len(menu.actions()) == 1
    with qtbot.waitSignal(canvas.import_background_image_requested):
        menu.actions()[0].trigger()
