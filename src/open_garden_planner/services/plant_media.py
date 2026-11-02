"""Specimen media on top of the existing portable attachment mechanism."""
from contextlib import suppress
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import UUID

from PIL import ExifTags, Image, ImageOps

from open_garden_planner.services.garden_archive import _atomic_bytes, import_attachment

MEDIA_KINDS = ("personal", "reference", "illustration", "document")
MAX_MEDIA_BYTES = 40 * 1024 * 1024


def resolve_media(project: Path, reference: str) -> Path:
    relative = Path(reference)
    if not reference or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Media reference must be project-relative")
    path = project.parent / relative
    path.resolve().relative_to(project.parent.resolve())
    if not path.is_file():
        raise FileNotFoundError(f"Missing media: {reference}")
    return path


def import_specimen_media(source, project, specimen_id, *, folder=None, kind="personal",
                         caption="", source_url="", credit="", taken_at=None):
    """Import one explicit file; never scan a folder or delete an original.

    Thumbnail generation and EXIF reading run in the caller's worker. File
    timestamps are deliberately ignored. Returned references are portable.
    """
    source, project = Path(source), Path(project)
    specimen_id = str(UUID(specimen_id))
    if kind not in MEDIA_KINDS:
        raise ValueError("Unknown media kind")
    if not source.is_file() or source.stat().st_size > MAX_MEDIA_BYTES:
        raise ValueError("Media file is missing or exceeds 40 MiB")
    with source.open("rb") as stream:
        payload = stream.read(MAX_MEDIA_BYTES + 1)
    if len(payload) > MAX_MEDIA_BYTES:
        raise ValueError("Media file exceeds 40 MiB")
    identity = sha256(payload).hexdigest()
    thumbnail = None
    if kind != "document":
        with Image.open(BytesIO(payload)) as image:
            if image.width * image.height > 50_000_000:
                raise ValueError("Image exceeds the supported pixel limit")
            if taken_at is None:
                exif = image.getexif()
                # Standard camera JPEGs keep DateTimeOriginal in the Exif IFD;
                # retain the top-level fallback for previously supported files.
                exif_date = exif.get_ifd(ExifTags.IFD.Exif).get(36867) or exif.get(36867)
                if isinstance(exif_date, str):
                    with suppress(ValueError):
                        taken_at = datetime.strptime(exif_date, "%Y:%m:%d %H:%M:%S").isoformat()
            preview = ImageOps.exif_transpose(image).convert("RGB")
            preview.thumbnail((320, 240))
            stream = BytesIO()
            preview.save(stream, "JPEG", quality=85)
            thumbnail = stream.getvalue()
    if taken_at is not None:
        datetime.fromisoformat(taken_at)
    root = folder or (project.stem + ".assets/plants")
    relative = Path(root) / specimen_id
    reference = import_attachment(source, project, subdirectory=relative.as_posix(), expected_sha256=identity,
                                  max_bytes=MAX_MEDIA_BYTES)
    record = {"id": identity, "photo_path": reference, "kind": kind,
              "caption": caption, "source_url": source_url, "credit": credit,
              "taken_at": taken_at, "imported_at": datetime.now(UTC).isoformat()}
    if thumbnail is not None:
        thumb = Path(reference).with_name(identity + ".thumb.jpg")
        destination = project.parent / thumb
        if destination.is_symlink():
            raise ValueError("Thumbnail destination must not be a symbolic link")
        if not destination.exists():
            _atomic_bytes(destination, thumbnail)
        record["thumbnail_path"] = thumb.as_posix()
    return record


def edit_media_metadata(metadata, *, asset=None, remove_id=None, primary_id=None, update_id=None, fields=None):
    """Change associations only; removing a record never unlinks any file."""
    from open_garden_planner.models.garden_memory import validate_media

    result = deepcopy(metadata)
    instance = result.setdefault("plant_instance", {})
    if not isinstance(instance, dict):
        raise ValueError("Unrecognized specimen metadata")
    assets = instance.setdefault("media_assets", [])
    if not isinstance(assets, list) or any(not isinstance(record, dict) for record in assets):
        raise ValueError("Unrecognized media collection")
    identities = [record.get("id") for record in assets]
    if any(not isinstance(identity, str) for identity in identities) or len(set(identities)) != len(identities):
        raise ValueError("Media identities are absent or duplicated")
    if asset is not None:
        if not isinstance(asset, dict) or asset.get("kind") not in MEDIA_KINDS:
            raise ValueError("Invalid media record")
        identity = asset.get("id")
        if not isinstance(identity, str) or len(identity) != 64 or any(c not in "0123456789abcdef" for c in identity):
            raise ValueError("Invalid media identity")
        validate_media([asset.get("photo_path", "")])
        if asset.get("thumbnail_path"):
            validate_media([asset["thumbnail_path"]])
        if not any(record.get("id") == identity for record in assets):
            assets.append(deepcopy(asset))
    if remove_id is not None:
        assets[:] = [record for record in assets if record.get("id") != remove_id]
        if instance.get("primary_media_id") == remove_id:
            instance.pop("primary_media_id")
    if update_id is not None:
        target = next((record for record in assets if record.get("id") == update_id), None)
        if target is None or not isinstance(fields, dict):
            raise ValueError("Unknown media record")
        if set(fields) - {"kind", "caption", "taken_at", "source_url", "credit"}:
            raise ValueError("Only descriptive media fields can be changed")
        if "kind" in fields and fields["kind"] not in MEDIA_KINDS:
            raise ValueError("Invalid media kind")
        for key, value in fields.items():
            if key == "taken_at" and value is None:
                continue
            if not isinstance(value, str):
                raise ValueError("Media descriptions must be text")
            if key == "taken_at":
                datetime.fromisoformat(value)
        target.update(deepcopy(fields))
        if target.get("kind") != "personal" and instance.get("primary_media_id") == update_id:
            instance.pop("primary_media_id")
    if primary_id is not None:
        if not any(record.get("id") == primary_id and record.get("kind") == "personal" for record in assets):
            raise ValueError("Primary personal photo must belong to this specimen")
        instance["primary_media_id"] = primary_id
    return result
