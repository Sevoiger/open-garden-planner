"""Protect cached paths when an imported data directory is replaced."""

from __future__ import annotations

import os
import stat
from pathlib import Path

_root: Path | None = None
_identity: tuple[int, int] | None = None
_container_identity: tuple[int, int] | None = None


def pin_workspace(root: Path | None) -> None:
    global _root, _identity, _container_identity
    if root is None:
        _root, _identity, _container_identity = None, None, None
    else:
        info = root.stat()
        container = root.parent.stat()
        _root, _identity = root, (info.st_dev, info.st_ino)
        _container_identity = (container.st_dev, container.st_ino)


def check_workspace_path(path: Path) -> None:
    """Fail closed if a cached path now traverses a replaced workspace.

    This catches ordinary moves and substitutions before filesystem operations;
    it is not a defense against an adversary racing individual system calls.
    """
    if _root is None:
        return
    candidate = Path(os.path.abspath(path))
    if not candidate.is_relative_to(_root.parent):
        return
    try:
        container = _root.parent.lstat()
        if not stat.S_ISDIR(container.st_mode) or (container.st_dev, container.st_ino) != _container_identity:
            raise OSError("Imported workspace folder was replaced")
        info = _root.lstat()
        if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != _identity:
            raise OSError("Imported personal data folder was replaced")
        current = _root.parent
        for part in candidate.relative_to(_root.parent).parts:
            current = current / part
            try:
                if stat.S_ISLNK(current.lstat().st_mode):
                    raise OSError("Imported personal data path contains a symbolic link")
            except FileNotFoundError:
                break
    except FileNotFoundError as exc:
        raise OSError("Imported personal data folder is unavailable") from exc
