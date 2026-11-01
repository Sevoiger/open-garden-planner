"""Resolve seed photos after an imported workspace folder is moved."""
from __future__ import annotations

import json
from pathlib import Path


def seed_photo_relocation(app_data: Path) -> tuple[Path, Path] | None:
    """Read relocation metadata once per store or export."""
    root = Path(app_data)
    if root.name != "app-data":
        return None
    folder = root.parent.resolve()
    manifest = folder / "backup-manifest.json"
    if not manifest.exists():
        from open_garden_planner.services.plant_library import workspace_data_dir

        if workspace_data_dir() == root.resolve():
            raise ValueError("Imported workspace media manifest is missing")
        return None
    try:
        if not manifest.is_file() or manifest.stat().st_size > 8_000_000:
            raise ValueError("Invalid workspace media manifest")
        metadata = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise ValueError("Invalid workspace media manifest")
        previous = metadata.get("restored_media_base")
        if previous is None and not metadata.get("external_media"):
            return None
        if not isinstance(previous, str) or not Path(previous).is_absolute():
            raise ValueError("Missing workspace media location")
        return Path(previous), folder
    except (OSError, TypeError) as exc:
        raise ValueError("Cannot read workspace media manifest") from exc


def relocated_seed_photo(reference: str, relocation: tuple[Path, Path] | None) -> str:
    """Rebase only a known restored seed photo, without changing the store."""
    if not relocation or not reference:
        return reference
    previous, folder = relocation
    try:
        try:
            relative = Path(reference).relative_to(previous)
        except ValueError:
            return reference
        if len(relative.parts) != 2 or relative.parts[0] != "personal-media":
            return reference
        media = folder / "personal-media"
        candidate = media / relative.parts[1]
        if media.is_symlink() or candidate.is_symlink() or not candidate.is_file():
            raise ValueError("A seed photo is missing from the imported workspace")
        return str(candidate)
    except (OSError, TypeError) as exc:
        raise ValueError("Cannot access a seed photo in the imported workspace") from exc
