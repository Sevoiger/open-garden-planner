"""Filesystem path helpers for user-facing data.

User projects (`.ogp` files) and exports must never default into the install
directory: on Windows the installer wipes that directory on upgrade/uninstall,
which destroyed user data (issue #199). These helpers resolve a safe, writable
location explicitly chosen by the user, or the existing OS Documents folder.
"""

from pathlib import Path

from PyQt6.QtCore import QStandardPaths


def get_documents_dir() -> Path:
    """Return the OS Documents directory, falling back to ``~/Documents``."""
    docs = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    return Path(docs) if docs else Path.home() / "Documents"


def _remembered_projects() -> list[Path]:
    from open_garden_planner.app.settings import get_settings

    settings = get_settings()
    saved = settings.last_saved_project
    # Migrate older sessions using their existing recent list. Once an explicit
    # save is known, a later Open must not replace it as the startup choice.
    names = [saved] if saved else settings.recent_files
    return [Path(name) for name in names
            if Path(name).suffix.lower() == ".ogp" and not Path(name).name.startswith("~autosave_")]


def get_last_project_path() -> Path | None:
    """Remembered startup path, even if missing so its recovery can be found."""
    projects = _remembered_projects()
    return projects[0] if projects else None


def get_projects_dir() -> Path:
    """Reuse the last project's folder; never create a default project folder."""
    for project in _remembered_projects():
        if project.parent.is_dir():
            return project.parent
    documents = get_documents_dir()
    return documents if documents.is_dir() else Path.home()


def default_dialog_dir(current_file: Path | str | None = None) -> Path:
    """Resolve a safe directory for a save/open dialog to start in.

    Returns the folder of ``current_file`` when one is supplied and exists (so
    users stay where they last worked), otherwise ``get_projects_dir()``. This
    is the single chokepoint every file dialog must use — it never returns the
    process working directory as an implicit fallback (issue #199). It reuses
    a remembered project folder before falling back to existing Documents.
    """
    if current_file is not None:
        parent = Path(current_file).parent
        if parent.is_dir():
            return parent
    return get_projects_dir()


def default_save_path(filename: str, current_file: Path | str | None = None) -> str:
    """Build a full default path (safe dir + ``filename``) for a save dialog."""
    return str(default_dialog_dir(current_file) / filename)
