"""Project paths reuse chosen folders without creating an application folder."""

from pathlib import Path

from PyQt6.QtCore import QStandardPaths

from open_garden_planner.app import paths
from open_garden_planner.app.settings import get_settings


def test_get_projects_dir_under_documents(monkeypatch, tmp_path):
    monkeypatch.setattr(QStandardPaths, 'writableLocation', lambda _loc: str(tmp_path))
    assert paths.get_projects_dir() == tmp_path


def test_get_projects_dir_does_not_create_subfolder(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, 'get_documents_dir', lambda: tmp_path)
    assert paths.get_projects_dir().is_dir()
    assert list(tmp_path.iterdir()) == []


def test_get_projects_dir_falls_back_to_home(monkeypatch, tmp_path):
    monkeypatch.setattr(QStandardPaths, 'writableLocation', lambda _loc: '')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    assert paths.get_documents_dir() == tmp_path / 'Documents'
    assert paths.get_projects_dir() == tmp_path
    assert not (tmp_path / 'Documents').exists()


def test_default_dialog_dir_without_project(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, 'get_documents_dir', lambda: tmp_path)
    assert paths.default_dialog_dir(None) == tmp_path


def test_default_dialog_dir_follows_current_file(tmp_path):
    project = tmp_path / 'garden' / 'plan.ogp'
    project.parent.mkdir()
    project.write_text('{}')
    assert paths.default_dialog_dir(project) == project.parent


def test_default_dialog_dir_ignores_missing_parent(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, 'get_documents_dir', lambda: tmp_path)
    assert paths.default_dialog_dir(tmp_path / 'deleted_dir' / 'plan.ogp') == tmp_path


def test_default_save_path_joins_filename(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, 'get_documents_dir', lambda: tmp_path)
    assert Path(paths.default_save_path('garden_2026.ogp')) == tmp_path / 'garden_2026.ogp'


def test_remembers_projects_folder_without_creating_sibling(monkeypatch, tmp_path):
    folder = tmp_path / 'projects'
    folder.mkdir()
    monkeypatch.setattr(paths, 'get_documents_dir', lambda: tmp_path)
    get_settings().last_saved_project = str(folder / 'garden.ogp')
    assert paths.get_projects_dir() == folder
    assert paths.default_dialog_dir() == folder
    assert not (tmp_path / 'Open Garden Planner').exists()


def test_recent_folder_migrates_until_first_save(tmp_path):
    folder = tmp_path / 'projects'
    folder.mkdir()
    path = folder / 'garden.ogp'
    path.write_text('{}')
    get_settings().add_recent_file(str(path))
    assert paths.get_last_project_path() == path
    assert paths.get_projects_dir() == folder


def test_clear_recent_files_forgets_startup_path(tmp_path):
    path = tmp_path / 'garden.ogp'
    path.write_text('{}')
    get_settings().last_saved_project = str(path)
    get_settings().add_recent_file(str(path))
    get_settings().clear_recent_files()
    assert paths.get_last_project_path() is None


def test_autosave_is_never_the_startup_candidate(tmp_path):
    autosave = tmp_path / '~autosave_garden.ogp'
    autosave.write_text('{}')
    get_settings().add_recent_file(str(autosave))
    assert paths.get_last_project_path() is None
