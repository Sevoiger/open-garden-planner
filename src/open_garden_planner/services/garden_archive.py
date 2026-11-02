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
MAX_ARCHIVE_FILES = 20_000


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


def import_attachment(source: Path, project: Path, *, subdirectory: str | None = None,
                      expected_sha256: str | None = None, max_bytes: int | None = None) -> str:
    """Content-addressed names handle collisions; files outlive source/temp folders."""
    if not project.is_file():
        raise ValueError("Save the project before importing attachments")
    with source.open("rb") as stream:
        data = stream.read() if max_bytes is None else stream.read(max_bytes + 1)
    if max_bytes is not None and len(data) > max_bytes:
        raise ValueError("Attachment exceeds the file size limit")
    if expected_sha256 is not None and hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("Attachment changed during import; retry with the original file")
    relative = Path(subdirectory) if subdirectory is not None else Path(project.stem + ".assets")
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Attachment folder must be inside the project directory")
    folder = project.parent / relative
    folder.resolve().relative_to(project.parent.resolve())
    folder.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower()
    if not suffix.isascii() or len(suffix) > 12 or any(c not in ".abcdefghijklmnopqrstuvwxyz0123456789" for c in suffix):
        suffix = ".bin"
    destination = folder / (hashlib.sha256(data).hexdigest() + suffix)
    destination.resolve().relative_to(project.parent.resolve())
    if destination.is_symlink():
        raise ValueError("Attachment destination must not be a symbolic link")
    if destination.exists():
        if destination.read_bytes() != data:
            raise ValueError("Attachment hash collision")
    else:
        _atomic_bytes(destination, data)
    return destination.relative_to(project.parent).as_posix()


def _media_references(value: Any, _path: tuple[str | int, ...] = ()) -> Iterator[tuple[Any, str | int, str]]:
    """Yield mutable containers and their media-reference keys without scanning disk."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "reference_media" and _path[-2:] in {
                ("plant_species", "raw_data"), ("species_data", "raw_data"),
            }:
                # Embedded botanical data is an immutable library snapshot.
                # These paths belong to the botanical library, not the plan.
                # Its declared originals are archived through app-data below.
                continue
            if key in {"photo_path", "thumbnail_path"} and isinstance(child, str) and child:
                yield value, key, child
            elif key == "media" and isinstance(child, list):
                for index, reference in enumerate(child):
                    yield child, index, reference
            else:
                yield from _media_references(child, (*_path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _media_references(child, (*_path, index))


def _seed_photo_references(records: Any) -> Iterator[tuple[dict[str, Any], str]]:
    """Recognize seed photos only; arbitrary extension fields stay opaque."""
    if not isinstance(records, list):
        raise ValueError("Invalid seed inventory")
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Invalid seed inventory record")
        photo = record.get("photo_path", "")
        if not isinstance(photo, str):
            raise ValueError("Invalid seed photo reference")
        if photo:
            yield record, photo


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


def _file_digest(path: Path) -> tuple[str, int]:
    """Hash a file with bounded memory, refusing oversized inputs as they grow."""
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Portable archive exceeds supported size")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_ARCHIVE_BYTES:
                raise ValueError("Portable archive exceeds supported size")
            digest.update(chunk)
    return digest.hexdigest(), size


def portable_backup(snapshot: dict[str, Any], project: Path, destination: Path, *,
                    app_data: Path | None = None, weather_config: Path | None = None) -> None:
    """Stream one plan, attachments and optional saved personal stores to a ZIP."""
    if destination.resolve() == project.resolve() or destination.suffix.lower() == ".ogp":
        raise ValueError("A backup cannot overwrite a garden project")
    candidate = deepcopy(snapshot)
    from open_garden_planner.services.botanical_media import snapshot_reference_paths

    snapshot_botanical_media = set(snapshot_reference_paths(candidate))
    if snapshot_botanical_media and app_data is None:
        raise ValueError("Use the global portable backup with the botanical library to preserve reference photos")
    files: dict[str, bytes] = {}
    sources: dict[str, Path] = {}
    fingerprints: dict[str, tuple[str, int]] = {}

    def add_source(name: str, path: Path, fingerprint: tuple[str, int] | None = None) -> None:
        if path.resolve() == destination.resolve():
            raise ValueError("A backup cannot overwrite an attached document or personal data")
        digest = fingerprint or _file_digest(path)
        if name in fingerprints and fingerprints[name] != digest:
            raise ValueError("Archive name collision")
        sources[name] = path
        fingerprints[name] = digest

    for container, key, reference in _media_references(candidate):
        source = project.parent / reference
        digest = _file_digest(source)
        suffix = Path(reference).suffix.lower()
        if not suffix.isascii() or len(suffix) > 12 or any(c not in ".abcdefghijklmnopqrstuvwxyz0123456789" for c in suffix):
            suffix = ".bin"
        name = "media/" + digest[0] + suffix
        add_source(name, source, digest)
        container[key] = name
    validate_plan_data(candidate)
    files["garden.ogp"] = json.dumps(candidate, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
    personal_files = None
    originals: list[Path] = []
    external_media: dict[str, str] = {}
    if app_data is not None:
        root = Path(app_data)
        allowed = {"custom_plants.json", "plant_lists.json", "seed_inventory.json",
                   "acquisitions.json", "custom_companion_rules.json"}
        directories = {"weather", "guilds", "smart_symbols"}

        def personal_files():
            if not root.exists():
                return []
            paths = [path for path in root.iterdir() if path.name in allowed
                     or (path.name.startswith("weather_cache_") and path.suffix == ".json")]
            for name in directories:
                folder = root / name
                if folder.is_symlink():
                    raise ValueError("A personal data directory is a symbolic link")
                if folder.exists():
                    for path in folder.rglob("*"):
                        if path.is_symlink():
                            raise ValueError("A personal data path is a symbolic link")
                        if path.is_file():
                            paths.append(path)
            return sorted(p for p in paths if (not p.name.startswith(".") or p.name == ".ogp-weather-run")
                          and not p.name.endswith((".lock", ".tmp", ".env")))

        originals = personal_files()
        if len(originals) + len(sources) + len(files) + 1 > MAX_ARCHIVE_FILES:
            raise ValueError("Portable archive exceeds supported size")
        if (sum(p.stat().st_size for p in originals) + sum(size for _, size in fingerprints.values())
                + sum(map(len, files.values())) > MAX_ARCHIVE_BYTES):
            raise ValueError("Portable archive exceeds supported size")
        for path in originals:
            if path.is_symlink() or not path.is_file():
                raise ValueError("A personal data file is not a regular file")
            path.resolve().relative_to(root.resolve())
            add_source("app-data/" + path.relative_to(root).as_posix(), path)
        botanical_path = sources.get("app-data/custom_plants.json")
        for reference in snapshot_botanical_media:
            path = root / reference
            path.resolve().relative_to(root.resolve())
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Referenced botanical snapshot media is unavailable: {reference}")
            add_source("app-data/" + Path(reference).as_posix(), path)
        if botanical_path is not None:
            from open_garden_planner.core.personal_store import loads_json
            from open_garden_planner.services.botanical_media import library_reference_paths

            if botanical_path.stat().st_size > 64_000_000:
                raise ValueError("Botanical library exceeds supported size")
            with botanical_path.open("rb") as stream:
                botanical_bytes = stream.read(64_000_001)
            if len(botanical_bytes) > 64_000_000:
                raise ValueError("Botanical library exceeds supported size")
            observed = hashlib.sha256(botanical_bytes).hexdigest(), len(botanical_bytes)
            if observed != fingerprints["app-data/custom_plants.json"]:
                raise ValueError("Botanical library changed during backup; retry")
            botanical = loads_json(botanical_bytes.decode("utf-8"))
            for reference in library_reference_paths(botanical):
                path = root / reference
                path.resolve().relative_to(root.resolve())
                if path.is_symlink() or not path.is_file():
                    raise ValueError(f"Referenced botanical media is unavailable: {reference}")
                # Keep references relative to the restored library, whose JSON
                # is copied byte-for-byte. Only declared originals/thumbnails
                # enter the archive; unrelated files are never scanned.
                add_source("app-data/" + Path(reference).as_posix(), path)
        seed_path = sources.get("app-data/seed_inventory.json")
        if seed_path is not None:
            from open_garden_planner.core.personal_store import loads_json
            if seed_path.stat().st_size > 64_000_000:
                raise ValueError("Seed inventory exceeds supported size")
            with seed_path.open("rb") as seed_stream:
                seed_bytes = seed_stream.read(64_000_001)
            if len(seed_bytes) > 64_000_000:
                raise ValueError("Seed inventory exceeds supported size")
            if (hashlib.sha256(seed_bytes).hexdigest(), len(seed_bytes)) != fingerprints["app-data/seed_inventory.json"]:
                raise ValueError("Seed inventory changed during backup; retry")
            seeds = loads_json(seed_bytes.decode("utf-8"))
            from open_garden_planner.services.workspace_media import (
                relocated_seed_photo,
                seed_photo_relocation,
            )

            relocation = seed_photo_relocation(root)
            for _, reference in _seed_photo_references(seeds):
                path = Path(relocated_seed_photo(reference, relocation)).expanduser()
                if not path.is_absolute():
                    path = project.parent / path
                if not path.is_file():
                    raise ValueError(f"Referenced seed photo is unavailable: {reference}")
                digest = _file_digest(path)
                suffix = path.suffix.lower()
                if not suffix.isascii() or len(suffix) > 12 or any(c not in ".abcdefghijklmnopqrstuvwxyz0123456789" for c in suffix):
                    suffix = ".bin"
                name = "personal-media/" + digest[0] + suffix
                add_source(name, path, digest)
                external_media[reference] = name
        if weather_config is not None and weather_config.exists():
            if weather_config.is_symlink() or not weather_config.is_file():
                raise ValueError("Weather configuration is not a regular file")
            add_source("configuration/weather-config.json", weather_config)
        manifest = {"version": 1, "external_media": external_media,
                    "files": {**{name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
                              **{name: digest for name, (digest, _size) in fingerprints.items()}}}
        files["backup-manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        if len(files["backup-manifest.json"]) > 8_000_000:
            raise ValueError("Backup manifest exceeds supported size")
    if (len(files) + len(sources) > MAX_ARCHIVE_FILES or
            sum(map(len, files.values())) + sum(size for _, size in fingerprints.values()) > MAX_ARCHIVE_BYTES):
        raise ValueError("Portable archive exceeds supported size")

    fd, temporary = tempfile.mkstemp(prefix=".garden-backup-", dir=destination.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
            for name, path in sources.items():
                digest = hashlib.sha256()
                size = 0
                with path.open("rb") as source, archive.open(name, "w", force_zip64=True) as target:
                    while chunk := source.read(1024 * 1024):
                        size += len(chunk)
                        if size > fingerprints[name][1]:
                            raise ValueError("Backup source changed during backup; retry")
                        digest.update(chunk)
                        target.write(chunk)
                if (digest.hexdigest(), size) != fingerprints[name]:
                    raise ValueError("Backup source changed during backup; retry")
        if (personal_files is not None and personal_files() != originals) or any(
                _file_digest(path) != fingerprints[name] for name, path in sources.items()):
            raise ValueError("Backup source changed during backup; retry")
        with open(temporary, "rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def restore_backup(archive_path: Path, destination: Path) -> Path:
    """Restore into a new directory. Reject traversal, duplicate paths and oversized archives."""
    if destination.exists():
        raise FileExistsError("Restore requires a new destination directory")
    with tempfile.TemporaryDirectory(prefix=".garden-restore-", dir=destination.parent) as temporary:
        staging = Path(temporary) / "restored"
        staging.mkdir(mode=0o700)
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
                target = staging.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("xb") as output:
                    while chunk := source.read(min(1024 * 1024, remaining + 1)):
                        remaining -= len(chunk)
                        if remaining < 0:
                            raise ValueError("Portable archive exceeds supported size")
                        output.write(chunk)
        manifest_path = staging / "backup-manifest.json"
        if manifest_path.exists():
            if manifest_path.stat().st_size > 8_000_000:
                raise ValueError("Backup manifest exceeds supported size")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or manifest.get("version") != 1 or not isinstance(manifest.get("files"), dict):
                raise ValueError("Invalid backup manifest")
            expected = manifest["files"]
            actual = {p.relative_to(staging).as_posix() for p in staging.rglob("*") if p.is_file()}
            if set(expected) != actual - {"backup-manifest.json"}:
                raise ValueError("Backup contents do not match the manifest")
            for name, digest in expected.items():
                if not isinstance(digest, str) or _file_digest(staging / name)[0] != digest:
                    raise ValueError("Backup integrity check failed")
        # Rebase external seed photographs to the restored copy, not the old machine.
        if manifest_path.exists():
            media_map = manifest.get("external_media", {})
            if not isinstance(media_map, dict):
                raise ValueError("Invalid backup media mapping")
            seed_path = staging / "app-data" / "seed_inventory.json"
            if media_map:
                from open_garden_planner.core.personal_store import loads_json
                if not seed_path.is_file():
                    raise ValueError("Missing seed inventory for archived media")
                if seed_path.stat().st_size > 64_000_000:
                    raise ValueError("Seed inventory exceeds supported size")
                seeds = loads_json(seed_path.read_text(encoding="utf-8"))
                for container, reference in _seed_photo_references(seeds):
                    if reference in media_map:
                        name = media_map[reference]
                        if (not isinstance(name, str) or not name.startswith("personal-media/")
                                or name not in expected or not (staging / name).is_file()):
                            raise ValueError("Invalid archived seed photo")
                        container["photo_path"] = str(destination.resolve() / name)
                seed_path.write_text(json.dumps(seeds, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                manifest["files"]["app-data/seed_inventory.json"] = _file_digest(seed_path)[0]
                manifest["restored_media_base"] = str(destination.resolve())
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        payload = read_plan(staging / "garden.ogp")
        for _, _, reference in _media_references(payload):
            path = (staging / reference).resolve()
            path.relative_to(staging.resolve())
            if not path.is_file():
                raise ValueError("Missing archived media")
        botanical_path = staging / "app-data" / "custom_plants.json"
        from open_garden_planner.services.botanical_media import snapshot_reference_paths

        for reference in snapshot_reference_paths(payload):
            path = (staging / "app-data" / reference).resolve()
            path.relative_to((staging / "app-data").resolve())
            if not path.is_file():
                raise ValueError("Missing archived botanical snapshot media")
        if botanical_path.is_file():
            from open_garden_planner.core.personal_store import loads_json
            from open_garden_planner.services.botanical_media import library_reference_paths

            if botanical_path.stat().st_size > 64_000_000:
                raise ValueError("Botanical library exceeds supported size")
            botanical = loads_json(botanical_path.read_text(encoding="utf-8"))
            for reference in library_reference_paths(botanical):
                path = (botanical_path.parent / reference).resolve()
                path.relative_to(botanical_path.parent.resolve())
                if not path.is_file():
                    raise ValueError("Missing archived botanical media")
        staging.rename(destination)
    return destination / "garden.ogp"
