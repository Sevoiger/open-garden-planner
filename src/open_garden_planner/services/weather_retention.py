"""Prune only collector-owned runs after publication, preserving live references."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

RUN_NAME = re.compile(r"\d{8}T\d{12}Z")
MARKER = ".ogp-weather-run"


def read_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if (
        not isinstance(data, dict)
        or data.get("schema_version") not in {1, 2}
        or not isinstance(data.get("sources"), dict)
    ):
        raise ValueError(f"Unrecognized weather manifest: {path.name}")
    if not re.fullmatch(r"[0-9a-f]{64}", data.get("config_sha256", "")):
        raise ValueError("Missing weather configuration fingerprint")
    timestamp = datetime.fromisoformat(data["attempted_at"])
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Weather manifest timestamp needs a timezone")
    if any(not isinstance(source, dict) for source in data["sources"].values()):
        raise ValueError("Invalid weather source structure")
    # Validate every historical manifest before any deletion, not only the
    # subset reachable from the two retained runs.
    referenced_runs(data)
    return data


def referenced_runs(data: Any) -> set[str]:
    """Malformed file references stop pruning, rather than risking data loss."""
    result: set[str] = set()
    if isinstance(data, dict):
        for key, value in data.items():
            if key == "path":
                if not isinstance(value, str):
                    raise ValueError("Invalid archive reference")
                parts = Path(value).parts
                if (
                    len(parts) < 3
                    or parts[0] != "runs"
                    or not RUN_NAME.fullmatch(parts[1])
                    or ".." in parts
                ):
                    raise ValueError("Unsafe archive reference")
                result.add(parts[1])
            else:
                result.update(referenced_runs(value))
    elif isinstance(data, list):
        for value in data:
            result.update(referenced_runs(value))
    return result


def prune_archives(output: Path, current: Path) -> dict[str, Any]:
    """Caller holds collection lock. Current latest must already be published."""
    root = output / "runs"
    if root.is_symlink() or current.parent != root or current.is_symlink():
        raise ValueError("Unsafe archive directory")
    latest = read_manifest(output / "latest.json")
    if latest["attempted_at"] != read_manifest(current / "manifest.json")["attempted_at"]:
        raise ValueError("Current run has not been published")
    owned, completed = {}, {}
    for entry in root.iterdir():
        if entry.is_symlink() or not entry.is_dir() or not RUN_NAME.fullmatch(entry.name):
            continue
        manifest = entry / "manifest.json"
        if manifest.is_symlink():
            continue
        if manifest.exists():
            # A malformed historical manifest may contain live references: fail closed.
            data = read_manifest(manifest)
            owned[entry.name] = entry
            completed[entry.name] = data
        elif (entry / MARKER).is_file() and not (entry / MARKER).is_symlink():
            owned[entry.name] = entry
    previous = sorted((name for name in completed if name != current.name), reverse=True)[:1]
    retained = {current.name, *previous}
    protected = set(retained)
    # Also protect references of fallback directories transitively, because their
    # manifests remain readable and can themselves refer to still older sources.
    pending = [latest, *(completed[name] for name in retained)]
    scanned = set(retained)
    while pending:
        references = referenced_runs(pending.pop())
        protected.update(references)
        for name in references - scanned:
            scanned.add(name)
            if name in completed:
                pending.append(completed[name])
    removed, errors = [], []
    for name, path in sorted(owned.items()):
        if name in protected or name >= current.name:
            continue
        try:
            shutil.rmtree(path)
            removed.append(name)
        except OSError as exc:
            errors.append(f"{name}: {exc}")
    return {
        "keep_recent_runs": 2,
        "current_run": current.name,
        "retained_runs": sorted(retained),
        "protected_runs": sorted(protected),
        "removed_runs": removed,
        "errors": errors,
    }
