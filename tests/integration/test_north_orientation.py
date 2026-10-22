"""North rotates the solar frame, never the plan; all storage stays isolated."""

import json
from datetime import UTC, date, datetime
from threading import Event
from unittest.mock import Mock

import numpy as np
import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QDialogButtonBox

from open_garden_planner.app.application import GardenPlannerApp
from open_garden_planner.core.commands import SetNorthAngleCommand
from open_garden_planner.core.object_types import ObjectType
from open_garden_planner.core.project import ProjectData
from open_garden_planner.core.solar import solar_position
from open_garden_planner.ui.canvas.items.rectangle_item import RectangleItem
from open_garden_planner.ui.canvas.sun_heatmap import HeatmapWorker
from open_garden_planner.ui.dialogs.north_orientation_dialog import NorthOrientationDialog

BERLIN = {"latitude": 52.52, "longitude": 13.405}
DAY = date(2026, 6, 21)


def window(qtbot):
    win = GardenPlannerApp()
    qtbot.addWidget(win)
    win._confirm_discard_changes = lambda: True
    return win


def test_roundtrip_undo_redo_new_and_legacy(qtbot, tmp_path):
    win = window(qtbot)
    project, scene = win._project_manager, win.canvas_scene
    rect = RectangleItem(400, 500, 800, 250, object_type=ObjectType.HOUSE)
    rect.setRotation(17)
    scene.addItem(rect)
    before = project.snapshot_dict(scene)["objects"]
    manager = win.canvas_view.command_manager
    manager.execute(SetNorthAngleCommand(project, 135))
    assert scene.north_angle_deg == project.north_angle_deg == 135
    assert project.snapshot_dict(scene)["objects"] == before
    path = tmp_path / "north.ogp"
    project.save(scene, path)
    assert not project.is_dirty
    manager.undo()
    assert scene.north_angle_deg == 0 and project.is_dirty
    manager.redo()
    assert scene.north_angle_deg == 135
    project.new_project()
    assert scene.north_angle_deg == 0
    project.load(scene, path)
    assert scene.north_angle_deg == project.north_angle_deg == 135
    assert project.snapshot_dict(scene)["objects"] == before
    assert not project.is_dirty
    # A legacy file still loads north-up, preserving unknown fields.
    raw = json.loads(path.read_text())
    raw.pop("north_angle_deg")
    raw["future_extension"] = {"keep": 42}
    path.write_text(json.dumps(raw))
    project.load(scene, path)
    assert scene.north_angle_deg == project.north_angle_deg == 0
    assert project.snapshot_dict(scene)["future_extension"] == {"keep": 42}


@pytest.mark.parametrize("invalid", [True, None, "bad", "NaN", "Infinity", {}, []])
def test_bad_orientation_load_preserves_current_document(qtbot, tmp_path, invalid):
    win = window(qtbot)
    project, scene = win._project_manager, win.canvas_scene
    win.canvas_view.command_manager.execute(SetNorthAngleCommand(project, 135))
    target = tmp_path / "invalid.ogp"
    raw = ProjectData().to_dict()
    raw["north_angle_deg"] = invalid
    target.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        project.load(scene, target)
    assert scene.north_angle_deg == project.north_angle_deg == 135
    assert project.current_file is None and project.is_dirty
    win.canvas_view.command_manager.undo()
    assert scene.north_angle_deg == 0


def test_dialog_preview_click_and_cancel(qtbot):
    dialog = NorthOrientationDialog(135)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.compass.angle_deg == 135
    qtbot.mouseClick(dialog.compass, Qt.MouseButton.LeftButton, pos=QPoint(180, 100))
    assert dialog.north_angle_deg == dialog.compass.angle_deg == 90
    dialog.angle_spin.setValue(230)
    assert dialog.compass.angle_deg == 230
    buttons = dialog.findChild(QDialogButtonBox)
    qtbot.mouseClick(buttons.button(QDialogButtonBox.StandardButton.Cancel), Qt.MouseButton.LeftButton)
    assert dialog.result() == dialog.DialogCode.Rejected


def test_menu_edit_is_one_command_and_cancel_is_noop(qtbot, monkeypatch):
    win = window(qtbot)
    def accept(dialog):
        dialog.angle_spin.setValue(135)
        return dialog.DialogCode.Accepted
    monkeypatch.setattr(NorthOrientationDialog, "exec", accept)
    action = next(a for a in win.menuBar().actions()[0].menu().actions()
                  if a.text() == "Set North Orientation...")
    action.trigger()
    assert win._project_manager.north_angle_deg == 135
    win.canvas_view.command_manager.undo()
    assert win._project_manager.north_angle_deg == 0
    monkeypatch.setattr(NorthOrientationDialog, "exec", lambda d: d.DialogCode.Rejected)
    action.trigger()
    assert win._project_manager.north_angle_deg == 0
    win.canvas_view.command_manager.redo()
    assert win._project_manager.north_angle_deg == 135


def test_2d_and_3d_sun_turn_clockwise_without_moving_geometry(qtbot):
    win = window(qtbot)
    project, scene = win._project_manager, win.canvas_scene
    project.set_location(BERLIN)
    win._sim_clock.set_datetime(datetime(2026, 6, 21, 11, tzinfo=UTC))
    house = RectangleItem(1200, 1200, 100, 100, object_type=ObjectType.HOUSE)
    house.set_metadata("object_height_cm", 300)
    scene.addItem(house)
    original_geometry = house.mapRectToScene(house.rect())
    sun = win._sun_controller
    sun.set_enabled(True)
    path0 = sun._overlay.path().boundingRect()
    # At Berlin noon, a south sun casts a north/up shadow.
    assert path0.top() >= 1190 and path0.bottom() > 1300
    fake3d = Mock()
    win._view3d_window = fake3d
    try:
        win.canvas_view.command_manager.execute(SetNorthAngleCommand(project, 90))
        path90 = sun._overlay.path().boundingRect()
        # North is now right: shadow is to the right, not down or left.
        assert path90.left() >= 1190 and path90.right() > 1300
        position = solar_position(52.52, 13.405, win._sim_clock.utc)
        fake3d.set_sun.assert_called_with(position.elevation_deg, (position.azimuth_deg + 90) % 360)
        assert house.mapRectToScene(house.rect()) == original_geometry
    finally:
        win._view3d_window = None


def test_orientation_discards_inflight_heatmap(qtbot, monkeypatch):
    win = window(qtbot)
    win._project_manager.set_location(BERLIN)
    release, entered = Event(), Event()
    def controlled_run(worker):
        entered.set()
        release.wait(5)
        worker.success.emit(np.ones((worker._grid.rows, worker._grid.cols), np.float32))
    monkeypatch.setattr(HeatmapWorker, "run", controlled_run)
    heatmap = win._sun_heatmap
    try:
        assert heatmap.run_for_day(DAY, cell_cm=100)
        assert entered.wait(5)
        win.canvas_view.command_manager.execute(SetNorthAngleCommand(win._project_manager, 135))
        release.set()
        qtbot.waitUntil(lambda: not heatmap.result_pending)
        assert not heatmap.heatmap_visible()
        assert heatmap.last_minutes is None
        assert heatmap.receiver_minutes == {}
        assert not win._sun_toolbar._heatmap_button.isChecked()
    finally:
        release.set()
        heatmap.shutdown()


def test_worker_rotates_real_daily_heatmap(qtbot):
    win = window(qtbot)
    win._project_manager.set_location(BERLIN)
    scene = win.canvas_scene
    scene.resize_canvas(1000, 1000)
    # Across the entire canvas: winter south sun shades only the north side.
    house = RectangleItem(0, 450, 1000, 100, object_type=ObjectType.HOUSE)
    house.set_metadata("object_height_cm", 500)
    scene.addItem(house)
    day = date(2026, 12, 21)
    win._sim_clock.set_date(day)
    heatmap = win._sun_heatmap
    with qtbot.waitSignal(heatmap.finished, timeout=5000):
        assert heatmap.run_for_day(day, cell_cm=50)
    north, south = heatmap.last_grid.cell_at(500, 650), heatmap.last_grid.cell_at(500, 350)
    assert heatmap.last_minutes[north] < heatmap.last_minutes[south]
    win.canvas_view.command_manager.execute(SetNorthAngleCommand(win._project_manager, 180))
    assert not heatmap.heatmap_visible()
    with qtbot.waitSignal(heatmap.finished, timeout=5000):
        assert heatmap.run_for_day(day, cell_cm=50)
    assert heatmap.last_minutes[south] < heatmap.last_minutes[north]
    assert heatmap.snapshot.north_angle_deg == 180


@pytest.mark.parametrize("keep_plants", [False, True])
def test_season_carries_orientation(qtbot, tmp_path, keep_plants):
    win = window(qtbot)
    project = win._project_manager
    project.set_north_angle(135)
    target = tmp_path / "next-season.ogp"
    project.create_new_season(win.canvas_scene, 2027, target, keep_plants=keep_plants)
    assert json.loads(target.read_text())["north_angle_deg"] == 135
