"""Startup resumes saved projects without replacing active work or recovery."""

import json
from unittest.mock import Mock

import pytest
from PyQt6.QtWidgets import QMessageBox

from open_garden_planner.app.application import GardenPlannerApp
from open_garden_planner.app.settings import AppSettings, get_settings
from open_garden_planner.core.project import ProjectData, ProjectManager
from open_garden_planner.services.autosave_service import AutoSaveManager
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.canvas.items import RectangleItem


@pytest.fixture
def startup(qtbot, monkeypatch, tmp_path):
    run = GardenPlannerApp._startup_sequence
    monkeypatch.setattr(GardenPlannerApp, '_startup_sequence', lambda _self: None)
    monkeypatch.setattr(AutoSaveManager, 'find_recovery_files', lambda: [])
    original_path = AutoSaveManager._get_autosave_path
    monkeypatch.setattr(AutoSaveManager, '_get_autosave_path',
                        lambda manager: original_path(manager) if manager._current_project_path is not None
                        else tmp_path / '~autosave_untitled.ogp')
    win = GardenPlannerApp()
    qtbot.addWidget(win)
    win._confirm_discard_changes = lambda: True
    welcome = Mock()
    monkeypatch.setattr(win, '_show_welcome_dialog', welcome)
    return win, lambda: run(win), welcome


def save_plan(tmp_path, name='last.ogp'):
    folder = tmp_path / 'projects'
    folder.mkdir(exist_ok=True)
    scene = CanvasScene()
    item = RectangleItem(300, 400, 325, 600)
    scene.addItem(item)
    path = folder / name
    ProjectManager().save(scene, path)
    return path


def test_startup_reopens_saved_plan_and_uses_its_directory(startup, tmp_path):
    win, start, welcome = startup
    path = save_plan(tmp_path)
    assert AppSettings().last_saved_project == str(path)
    start()
    assert win._project_manager.current_file == path
    assert not win._project_manager.is_dirty
    assert win._default_dialog_dir() == path.parent
    assert len(win._project_manager.snapshot_dict(win.canvas_scene)['objects']) == 1
    welcome.assert_not_called()


def test_opening_another_file_does_not_change_last_saved_plan(startup, tmp_path):
    win, start, welcome = startup
    saved = save_plan(tmp_path)
    other = saved.parent / 'other.ogp'
    other.write_text(json.dumps(ProjectData().to_dict()))
    ProjectManager().load(CanvasScene(), other)
    start()
    assert win._project_manager.current_file == saved
    welcome.assert_not_called()


@pytest.mark.parametrize('state', ['open', 'dirty'])
def test_startup_keeps_explicit_open_or_early_edits(startup, tmp_path, monkeypatch, state):
    win, start, welcome = startup
    saved = save_plan(tmp_path)
    if state == 'open':
        win._load_project_file(str(saved))
    else:
        win.canvas_scene.addItem(RectangleItem(100, 100, 20, 30))
        win._project_manager.mark_dirty()
    before = win._project_manager.snapshot_dict(win.canvas_scene)['objects']
    check = Mock(return_value=False)
    monkeypatch.setattr(win, '_check_recovery_files', check)
    start()
    assert win._project_manager.snapshot_dict(win.canvas_scene)['objects'] == before
    check.assert_not_called()
    welcome.assert_not_called()


def test_missing_saved_plan_falls_back_to_welcome(startup, tmp_path):
    win, start, welcome = startup
    path = save_plan(tmp_path)
    path.unlink()
    start()
    assert win._project_manager.current_file is None
    welcome.assert_called_once()


def test_recovery_takes_priority_over_saved_plan(startup, tmp_path, monkeypatch):
    win, start, welcome = startup
    save_plan(tmp_path)
    def recover():
        win.canvas_scene.addItem(RectangleItem(100, 100, 70, 80))
        win._project_manager.mark_dirty()
        return True
    monkeypatch.setattr(win, '_check_recovery_files', recover)
    start()
    assert win._project_manager.current_file is None and win._project_manager.is_dirty
    welcome.assert_not_called()


@pytest.mark.parametrize('choice', [QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No,
                                   QMessageBox.StandardButton.Discard])
def test_saved_project_recovery_is_offered_and_never_silently_deleted(startup, tmp_path, monkeypatch, choice):
    win, start, welcome = startup
    saved = save_plan(tmp_path)
    recovery = saved.with_name('~autosave_' + saved.name)
    payload = ProjectData().to_dict()
    payload['autosave_metadata'] = {'timestamp': '2026-10-22T12:00:00+00:00', 'original_file': str(saved)}
    recovery.write_text(json.dumps(payload))
    before = recovery.read_bytes()
    question = Mock(return_value=choice)
    monkeypatch.setattr(QMessageBox, 'question', question)
    monkeypatch.setattr(QMessageBox, 'information', lambda *_args: None)
    start()
    question.assert_called_once()
    assert get_settings().last_saved_project == str(saved)
    if choice == QMessageBox.StandardButton.Discard:
        assert not recovery.exists()
        assert win._project_manager.current_file == saved
        welcome.assert_not_called()
    elif choice == QMessageBox.StandardButton.Yes:
        assert recovery.read_bytes() == before
        assert win._project_manager.current_file is None and win._project_manager.is_dirty
        welcome.assert_not_called()
    else:
        assert recovery.read_bytes() == before
        assert win._project_manager.current_file is None
        welcome.assert_called_once()


def test_corrupt_saved_project_reports_error_and_returns_to_welcome(startup, tmp_path, monkeypatch):
    win, start, welcome = startup
    saved = save_plan(tmp_path)
    saved.write_text('{broken')
    error = Mock()
    monkeypatch.setattr(QMessageBox, 'critical', error)
    start()
    error.assert_called_once()
    assert win._project_manager.current_file is None
    welcome.assert_called_once()


def test_failed_save_keeps_previous_startup_choice(startup, tmp_path, monkeypatch):
    win, _start, _welcome = startup
    saved = save_plan(tmp_path)
    def fail(*_args):
        raise OSError('write refused')
    monkeypatch.setattr('open_garden_planner.core.project.write_plan', fail)
    with pytest.raises(OSError):
        win._project_manager.save(win.canvas_scene, saved.parent / 'failed.ogp')
    assert get_settings().last_saved_project == str(saved)
    assert win._project_manager.current_file is None


def test_first_recovery_is_not_replaced_by_another_recovery(startup, tmp_path, monkeypatch):
    win, start, welcome = startup
    saved = save_plan(tmp_path)
    saved_recovery = saved.with_name('~autosave_' + saved.name)
    payload = ProjectData().to_dict()
    payload['autosave_metadata'] = {'timestamp': '2026-10-22T12:00:00+00:00', 'original_file': str(saved)}
    saved_recovery.write_text(json.dumps(payload))
    untitled = tmp_path / '~autosave_untitled.ogp'
    metadata = {'timestamp': '2026-10-22T13:00:00+00:00', 'original_file': None}
    payload['autosave_metadata'] = metadata
    untitled.write_text(json.dumps(payload))
    monkeypatch.setattr(AutoSaveManager, 'find_recovery_files', lambda: [(untitled, metadata)])
    question = Mock(return_value=QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, 'question', question)
    monkeypatch.setattr(QMessageBox, 'information', lambda *_args: None)
    start()
    question.assert_called_once()
    assert win._project_manager.current_file is None and win._project_manager.is_dirty
    assert saved_recovery.exists() and untitled.exists()
    welcome.assert_not_called()


def test_autosave_does_not_change_the_startup_choice(startup, tmp_path):
    win, _start, _welcome = startup
    saved = save_plan(tmp_path)
    win._autosave_manager.set_project_path(saved)
    assert win._autosave_manager.perform_autosave()
    assert get_settings().last_saved_project == str(saved)


def test_missing_original_still_offers_its_recovery(startup, tmp_path, monkeypatch):
    win, start, welcome = startup
    saved = save_plan(tmp_path)
    recovery = saved.with_name('~autosave_' + saved.name)
    payload = ProjectData().to_dict()
    payload['autosave_metadata'] = {'timestamp': '2026-10-22T12:00:00+00:00', 'original_file': str(saved)}
    recovery.write_text(json.dumps(payload))
    saved.unlink()
    question = Mock(return_value=QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, 'question', question)
    monkeypatch.setattr(QMessageBox, 'information', lambda *_args: None)
    start()
    question.assert_called_once()
    assert win._project_manager.current_file is None and win._project_manager.is_dirty
    assert recovery.exists()
    welcome.assert_not_called()


def test_failed_first_recovery_still_offers_next_valid_copy(startup, tmp_path, monkeypatch):
    win, start, welcome = startup
    saved = save_plan(tmp_path)
    valid = saved.with_name('~autosave_' + saved.name)
    payload = ProjectData().to_dict()
    payload['autosave_metadata'] = {'timestamp': '2026-10-22T12:00:00+00:00', 'original_file': str(saved)}
    valid.write_text(json.dumps(payload))
    unsupported = tmp_path / '~autosave_untitled.ogp'
    metadata = {'timestamp': '2026-10-22T13:00:00+00:00', 'original_file': None}
    payload['version'] = '99.0'
    payload['autosave_metadata'] = metadata
    unsupported.write_text(json.dumps(payload))
    monkeypatch.setattr(AutoSaveManager, 'find_recovery_files', lambda: [(unsupported, metadata)])
    question = Mock(return_value=QMessageBox.StandardButton.Yes)
    error = Mock()
    monkeypatch.setattr(QMessageBox, 'question', question)
    monkeypatch.setattr(QMessageBox, 'critical', error)
    monkeypatch.setattr(QMessageBox, 'information', lambda *_args: None)
    start()
    assert question.call_count == 2
    error.assert_called_once()
    assert win._project_manager.current_file is None and win._project_manager.is_dirty
    assert unsupported.exists() and valid.exists()
    welcome.assert_not_called()
