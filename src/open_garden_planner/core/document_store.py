"""Ownership and optimistic disk checks shared by Qt and autonomous hosts."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any

from open_garden_planner.core.file_lock import FileLock

_owners: dict[Path, tuple[FileLock, int, bool]] = {}
_mutex = threading.RLock()


def disk_version(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


class DocumentOwnership:
    """A writer lease; GUI documents may share within their serialized process.

    Each GUI document still has its own disk-version precondition. Autonomous
    hosts always request exclusive ownership, including within this process.
    """

    def __init__(self, path: Path, *, gui: bool = False) -> None:
        self.path = path.resolve()
        self._closed = True
        with _mutex:
            current = _owners.get(self.path)
            if current is not None:
                if not gui or not current[2]:
                    raise ValueError("Project already open for writing; use its live MCP server")
                lock, count, _gui = current
                _owners[self.path] = lock, count + 1, True
            else:
                lock = FileLock(self.path.with_name(self.path.name + ".writer.lock"))
                if not lock.acquire():
                    raise ValueError("Project owned by another process; use its live MCP server or close it")
                _owners[self.path] = lock, 1, gui
            self._closed = False

    def close(self) -> None:
        with _mutex:
            if getattr(self, "_closed", True):
                return
            self._closed = True
            lock, count, gui = _owners[self.path]
            if count == 1:
                del _owners[self.path]
                lock.release()
            else:
                _owners[self.path] = lock, count - 1, gui

    def __del__(self) -> None:
        self.close()

    def __enter__(self) -> DocumentOwnership:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


def check_disk_version(path: Path, expected: str | None) -> None:
    if disk_version(path) != expected:
        raise ValueError("Project changed on disk; reload it before saving to avoid overwriting another writer")
