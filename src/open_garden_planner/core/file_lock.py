"""Process-wide file locking for cooperating local OGP writers.

The lock file is intentionally persistent: deleting a lock path after unlock
can split waiters across different inodes. Platforms without ``flock`` fail
closed instead of silently providing only an in-process lock.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
from types import TracebackType

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised on unsupported platforms
    fcntl = None  # type: ignore[assignment]


class FileLockUnavailableError(RuntimeError):
    """Raised when process-level file locking is unavailable."""


class FileLockBusyError(RuntimeError):
    """Raised when a nonblocking context-manager acquisition finds a lock held."""


class FileLock:
    """Exclusive advisory ``flock`` for a path chosen by the caller.

    ``acquire()`` is nonblocking by default and returns ``False`` when another
    process owns the lock. A successful lock can be held for a short operation
    or for a longer ownership lease, then released explicitly. Context-manager
    use also defaults to nonblocking and raises :class:`FileLockBusyError`.
    """

    def __init__(self, path: Path, *, blocking: bool = False) -> None:
        self.path = Path(path)
        self.blocking = blocking
        self._fd: int | None = None

    def acquire(self, *, blocking: bool | None = None) -> bool:
        """Acquire this lock, returning ``False`` if a nonblocking try is busy."""
        if self._fd is not None:
            return True
        if fcntl is None:
            raise FileLockUnavailableError(
                "This platform does not provide flock; refusing an unlocked write"
            )

        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            should_block = self.blocking if blocking is None else blocking
            flags = fcntl.LOCK_EX if should_block else fcntl.LOCK_EX | fcntl.LOCK_NB
            fcntl.flock(fd, flags)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                os.close(fd)
                return False
            os.close(fd)
            raise
        self._fd = fd
        return True

    def release(self) -> None:
        """Release the advisory lock without deleting its persistent path."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            if fcntl is None:  # defensive; it cannot normally change in-process
                raise FileLockUnavailableError("flock became unavailable while locked")
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self) -> FileLock:
        if not self.acquire():
            raise FileLockBusyError(f"File lock is already held: {self.path}")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.release()
        return False


class ProcessFileLock(FileLock):
    """Blocking context manager for short local storage transactions."""

    def __init__(self, path: Path) -> None:
        super().__init__(path, blocking=True)


__all__ = [
    "FileLock",
    "FileLockBusyError",
    "FileLockUnavailableError",
    "ProcessFileLock",
]
