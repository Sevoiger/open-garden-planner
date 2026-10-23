"""Qt-free portable garden archives, attachment import and complete data exports."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
import zipfile
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any

from open_garden_planner.core.plan_io import read_plan, validate_plan_data, write_plan
from open_garden_planner.services.garden_queries import garden_cards, search_events

MAX_ARCHIVE_BYTES = 1_000_000_000
MAX_ARCHIVE_FILES = 2000


def _atomic_bytes(path: Path, data: bytes) -> None:
    """Replace a file only after all bytes were successfully produced and flushed."""
    fd, temporary = tempfile.mkstemp(prefix=".garden-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def import_attachment(source: Path, project: Path) -> str:
    """Content-addressed names handle collisions; files outlive source/temp folders."""
    if not project.is_file():
        raise ValueError("Save the project before importing attachments")
    data = source.read_bytes()
    folder = project.parent / (project.stem + ".assets")
    folder.mkdir(exist_ok=True)
    suffix = source.suffix.lower()
    if not suffix.isascii() or len(suffix) > 12 or any(c not in ".abcdefghijklmnopqrstuvwxyz0123456789" for c in suffix):
        suffix = ".bin"
    destination = folder / (hashlib.sha256(data).hexdigest() + suffix)
    if destination.exists():
        if destination.read_bytes() != data:
            raise ValueError("Attachment hash collision")
    else:
        _atomic_bytes(destination, data)
    return destination.relative_to(project.parent).as_posix()


def _media_references(value: Any) -> Iterator[tuple[Any, str | int, str]]:
    """Yield mutable containers and their media-reference keys without scanning disk."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "photo_path" and isinstance(child, str) and child:
                yield value, key, child
            elif key == "media" and isinstance(child, list):
                for index, reference in enumerate(child):
                    yield child, index, reference
            else:
                yield from _media_references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _media_references(child)


def copy_relative_media(snapshot: dict[str, Any], source_dir: Path, destination_dir: Path) -> None:
    """Keep relative references usable after Save As or a perennial season copy."""
    if source_dir.resolve() == destination_dir.resolve():
        return
    pending = []
    for _, _, reference in _media_references(snapshot):
        relative = Path(reference)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Use a portable backup to relocate legacy external media")
        source, target = source_dir / relative, destination_dir / relative
        # Resolve containment as well as lexical traversal (existing symlinks).
        target.resolve().relative_to(destination_dir.resolve())
        data = source.read_bytes()
        if target.exists() and target.read_bytes() != data:
            raise FileExistsError(f"A different media file already exists: {target}")
        pending.append((target, data))
    for target, data in pending:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            _atomic_bytes(target, data)


def export_json(snapshot: dict[str, Any], destination: Path) -> None:
    """Complete canonical plan JSON, including all tracking and legacy journal data."""
    candidate = deepcopy(snapshot)
    candidate["garden_export_version"] = 1
    write_plan(destination, candidate)


def export_csv(snapshot: dict[str, Any], destination: Path) -> None:
    """One row per card or event; target IDs remain explicit and dates retain labels."""
    buffer = io.StringIO(newline="")
    columns = ["record", "id", "name", "kind", "state", "date_precision", "date_value", "date_label",
               "event_type", "status", "description", "target_ids", "zone_id", "quantity", "unit",
               "material", "application", "area_m2", "depth_cm", "media"]
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    for card in garden_cards(snapshot):
        p = card["period"]
        writer.writerow({"record": "card", "id": card["item_id"], "name": card["name"],
                         "kind": card["kind"], "state": card["state"], "date_precision": p["precision"],
                         "date_value": p["value"], "date_label": p["label"], "description": card["notes"],
                         "zone_id": card.get("zone_id", ""), "quantity": card.get("quantity"),
                         "depth_cm": card.get("depth_cm"), "media": json.dumps(card.get("media", []))})
    for event in search_events(snapshot):
        p = event["period"]
        writer.writerow({"record": "event", "id": event["id"], "event_type": event["type"],
                         "date_precision": p["precision"], "date_value": p["value"], "date_label": p["label"],
                         "target_ids": json.dumps(event["targets"], ensure_ascii=False),
                         **{key: event.get(key) for key in ["status", "description", "quantity", "unit", "material",
                                                          "application", "area_m2", "depth_cm"]},
                         "media": json.dumps(event["media"], ensure_ascii=False)})
    _atomic_bytes(destination, buffer.getvalue().encode("utf-8-sig"))


def portable_backup(snapshot: dict[str, Any], project: Path, destination: Path) -> None:
    """Bundle one canonical plan and every referenced photo/document; backgrounds embed PNG."""
    if destination.resolve() == project.resolve():
        raise ValueError("A backup cannot overwrite the open project")
    candidate = deepcopy(snapshot)
    files: dict[str, bytes] = {}
    for container, key, reference in _media_references(candidate):
        data = (project.parent / reference).read_bytes()
        suffix = Path(reference).suffix.lower()
        if not suffix.isascii() or len(suffix) > 12 or any(c not in ".abcdefghijklmnopqrstuvwxyz0123456789" for c in suffix):
            suffix = ".bin"
        name = "media/" + hashlib.sha256(data).hexdigest() + suffix
        files[name] = data
        container[key] = name
    validate_plan_data(candidate)
    files["garden.ogp"] = json.dumps(candidate, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
    if len(files) > MAX_ARCHIVE_FILES or sum(len(data) for data in files.values()) > MAX_ARCHIVE_BYTES:
        raise ValueError("Portable archive exceeds supported size")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    _atomic_bytes(destination, buffer.getvalue())


def restore_backup(archive_path: Path, destination: Path) -> Path:
    """Restore into a new directory. Reject traversal, duplicate paths and oversized archives."""
    if destination.exists():
        raise FileExistsError("Restore requires a new destination directory")
    with tempfile.TemporaryDirectory(prefix=".garden-restore-", dir=destination.parent) as temporary:
        staging = Path(temporary) / "restored"
        staging.mkdir()
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_FILES or sum(info.file_size for info in infos) > MAX_ARCHIVE_BYTES:
                raise ValueError("Portable archive exceeds supported size")
            names = set()
            remaining = MAX_ARCHIVE_BYTES
            for info in infos:
                relative = PurePosixPath(info.filename)
                if (relative.is_absolute() or ".." in relative.parts or "\\" in info.filename
                        or ":" in info.filename or relative.as_posix() in names or not info.filename
                        or info.is_dir() or (info.external_attr >> 16) & 0o170000 == 0o120000):
                    raise ValueError("Unsafe archive member")
                names.add(relative.as_posix())
                with archive.open(info) as source:
                    data = source.read(remaining + 1)
                remaining -= len(data)
                if remaining < 0:
                    raise ValueError("Portable archive exceeds supported size")
                target = staging.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        payload = read_plan(staging / "garden.ogp")
        for _, _, reference in _media_references(payload):
            path = (staging / reference).resolve()
            path.relative_to(staging.resolve())
            if not path.is_file():
                raise ValueError("Missing archived media")
        staging.rename(destination)
    return destination / "garden.ogp"
