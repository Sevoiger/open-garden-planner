"""Durable JSON I/O for small cross-project personal stores.

These files are separate from the open garden plan. Writers cooperate through
the same persistent advisory lock path and compare the exact bytes they loaded
before publishing an atomic replacement.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from open_garden_planner.core.file_lock import ProcessFileLock
from open_garden_planner.core.plan_io import write_bytes_atomically


class PersonalStoreLoadState(StrEnum):
    """What was observed when a personal JSON store was last read."""

    ABSENT = "absent"
    EMPTY = "empty"
    LOADED = "loaded"
    FAILED = "failed"


class PersonalStoreError(RuntimeError):
    """Base class for personal-store failures."""


class PersonalStoreLoadError(PersonalStoreError):
    """The on-disk personal store could not be read or validated."""


class PersonalStoreConflictError(PersonalStoreError):
    """The file changed after this store instance loaded it."""


class PersonalStorePersistenceError(PersonalStoreError, OSError):
    """An in-memory change remains unsaved after the disk write failed."""


@dataclass(frozen=True)
class LoadedJSON:
    """One consistent read of a personal JSON file."""

    state: PersonalStoreLoadState
    value: Any = None
    fingerprint: str | None = None


def _fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> LoadedJSON:
    """Read JSON bytes without treating corruption or I/O errors as emptiness."""
    path = Path(path)
    try:
        payload = path.read_bytes()
    except FileNotFoundError:
        return LoadedJSON(PersonalStoreLoadState.ABSENT)
    except OSError as exc:
        raise PersonalStoreLoadError(f"Cannot read {path}: {exc}") from exc

    try:
        value = loads_json(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PersonalStoreLoadError(f"Invalid JSON in {path}: {exc}") from exc
    return LoadedJSON(PersonalStoreLoadState.EMPTY, value, _fingerprint(payload))


def loads_json(text: str) -> Any:
    """Decode strict JSON, rejecting duplicate keys and every non-finite number."""
    try:
        return json.loads(
            text,
            parse_constant=_reject_constant,
            parse_float=_parse_finite_float,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except RecursionError as exc:
        raise ValueError("JSON nesting exceeds the supported depth") from exc


def disk_fingerprint(path: Path) -> str | None:
    """Return the current exact-byte fingerprint, or ``None`` when absent."""
    try:
        payload = Path(path).read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PersonalStorePersistenceError(f"Cannot inspect {path}: {exc}") from exc
    return _fingerprint(payload)


def _reject_constant(token: str) -> Any:
    raise ValueError(f"Non-standard JSON constant is not allowed: {token}")


def _parse_finite_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise ValueError(f"Non-finite JSON number is not allowed: {token}")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result


def write_json(path: Path, value: Any, *, expected_fingerprint: str | None) -> str:
    """Atomically publish JSON only when the loaded file version is current.

    OGP writers use a persistent ``<filename>.writer.lock`` file. The lock is
    cooperative; the fingerprint check also detects uncooperative edits that
    happened before the lock was acquired.
    """
    path = Path(path)
    lock_path = path.with_name(path.name + ".writer.lock")
    payload = (
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)

    with ProcessFileLock(lock_path):
        actual_fingerprint = disk_fingerprint(path)
        if actual_fingerprint != expected_fingerprint:
            raise PersonalStoreConflictError(
                f"{path.name} changed on disk since it was loaded; reload before saving"
            )

        write_bytes_atomically(path, payload)

    return _fingerprint(payload)


__all__ = [
    "LoadedJSON",
    "PersonalStoreConflictError",
    "PersonalStoreError",
    "PersonalStoreLoadError",
    "PersonalStoreLoadState",
    "PersonalStorePersistenceError",
    "disk_fingerprint",
    "loads_json",
    "read_json",
    "write_json",
]
