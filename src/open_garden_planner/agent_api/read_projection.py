"""Bounded, Qt-free read projections for shared document and plant data."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Literal

MAX_DOCUMENT_STATE_BYTES = 512_000
MAX_DOCUMENT_PAGE_SIZE = 1_000
MAX_MEDIA_PAGE_SIZE = 100
MAX_MEDIA_CHUNK_BYTES = 64 * 1024
MAX_LIBRARY_PAGE_SIZE = 100
MAX_LIBRARY_RESPONSE_BYTES = 512_000
MAX_LIBRARY_RECORD_BYTES = 32_000

DOCUMENT_DOMAINS = (
    "plan",
    "objects",
    "tasks",
    "soil",
    "harvest",
    "pests",
    "journal",
    "succession",
    "inventory",
    "shopping",
    "garden_memory",
)

_DOMAIN_FIELDS: dict[str, tuple[str, ...]] = {
    "plan": ("canvas", "location", "north_angle_deg", "layers"),
    "objects": ("objects",),
    "tasks": ("manual_tasks", "task_states", "task_completions"),
    "soil": ("soil_tests", "enabled_amendments", "prefer_organic"),
    "harvest": ("harvest_logs",),
    "pests": ("pest_disease_logs",),
    "journal": ("garden_journal_notes",),
    "succession": ("succession_plans", "auto_assign_permaculture_layers"),
    "inventory": (
        "seed_inventory",
        "propagation_overrides",
        "crop_rotation",
        "season_year",
        "linked_seasons",
    ),
    "shopping": ("shopping_list_prices", "excluded_shopping_items"),
    "garden_memory": ("garden_memory",),
}

_MEDIA_KEYS = frozenset({"image_data", "personal_visual_png", "thumbnail_data", "png_data"})
_OBJECT_GEOMETRY_KEYS = frozenset(
    {
        "x", "y", "width", "height", "center_x", "center_y", "radius",
        "semi_x", "semi_y", "rotation", "angle", "x1", "y1", "x2", "y2",
        "points", "anchors", "handles_in", "handles_out", "position",
    }
)
_SEARCH_FIELDS = (
    "scientific_name", "common_name", "family", "genus", "data_source",
    "source_id", "slug",
)
_PLANT_SUMMARY_FIELDS = (
    "scientific_name", "common_name", "family", "genus", "data_source",
    "source_id", "slug",
)
_PAGED_FIELDS = frozenset(
    {
        "layers", "objects", "manual_tasks", "task_states", "task_completions",
        "soil_tests", "enabled_amendments", "harvest_logs", "pest_disease_logs",
        "garden_journal_notes", "succession_plans", "seed_inventory",
        "propagation_overrides", "crop_rotation", "linked_seasons",
        "shopping_list_prices", "excluded_shopping_items",
    }
)


def _json_size(value: Any) -> int:
    # Match json.dumps' default wire separators and ASCII escaping, which is a
    # conservative proxy for FastMCP's text content encoding.
    return len(json.dumps(value, allow_nan=False).encode("utf-8"))


def _plants_revision(plants: dict[str, Any]) -> str:
    digest = hashlib.sha256()
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    for chunk in encoder.iterencode(plants):
        digest.update(chunk.encode("utf-8"))
    return digest.hexdigest()


def _encoded_size(value: str) -> int:
    # CPython keeps the ASCII flag on strings; base64 length needs no allocation.
    return len(value) if value.isascii() else len(value.encode("utf-8"))


def _slice_text_by_bytes(value: str, *, offset: int, size: int, label: str) -> tuple[str, int]:
    if value.isascii():
        if offset > len(value):
            raise ValueError(f"{label} exceeds media size")
        chunk = value[offset : offset + size]
        return chunk, len(chunk)
    encoded = value.encode("utf-8")
    if offset > len(encoded):
        raise ValueError(f"{label} exceeds media size")
    try:
        encoded[:offset].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} must align to a UTF-8 byte boundary") from exc
    end_offset = min(offset + size, len(encoded))
    while end_offset > offset:
        try:
            chunk = encoded[offset:end_offset].decode("utf-8")
            return chunk, len(chunk.encode("utf-8"))
        except UnicodeDecodeError:
            end_offset -= 1
    raise ValueError(f"{label} byte limit is too small to include the next UTF-8 character")


def _is_media_key(key: object, *, parent_path: tuple[str | int, ...] = ()) -> bool:
    if not isinstance(key, str):
        return False
    normalized = key.casefold()
    parent = parent_path[-1].casefold() if parent_path and isinstance(parent_path[-1], str) else ""
    # Personal fill patterns are stored as metadata records with a plain `png`
    # field rather than a generic image/media key. Restrict this exception to
    # the owning record so unrelated business fields named `png` are retained.
    return (
        normalized in _MEDIA_KEYS
        or normalized.endswith(("_base64", "_bytes"))
        or (normalized == "png" and parent == "personal_fill_pattern")
    )


def _is_data_uri(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value[:5].casefold() == "data:"
        and ";base64," in value[:128].casefold()
    )


def _media_id(scope: str, path: tuple[str | int, ...]) -> str:
    encoded_path = json.dumps(path, ensure_ascii=False, separators=(",", ":"))
    return f"{scope}:{hashlib.sha256(encoded_path.encode('utf-8')).hexdigest()[:24]}"


def _walk_media(value: Any, *, scope: str, path: tuple[str | int, ...] = ()) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = (*path, str(key))
            if isinstance(child, str) and (
                _is_media_key(key, parent_path=path) or _is_data_uri(child)
            ):
                assets.append(
                    {
                        "media_id": _media_id(scope, child_path),
                        "path": "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in child_path),
                        "path_parts": child_path,
                        "field": str(key),
                        "encoding": "data-uri" if _is_data_uri(child) else (
                            "base64" if _is_base64_media_key(key, parent_path=path)
                            else "opaque"
                        ),
                        "encoded_bytes": _encoded_size(child),
                    }
                )
            else:
                assets.extend(_walk_media(child, scope=scope, path=child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assets.extend(_walk_media(child, scope=scope, path=(*path, index)))
    return assets


def _is_base64_media_key(
    key: object, *, parent_path: tuple[str | int, ...] = ()
) -> bool:
    if not isinstance(key, str):
        return False
    normalized = key.casefold()
    return (
        normalized in _MEDIA_KEYS
        or normalized.endswith("_base64")
        or (
            normalized == "png"
            and bool(parent_path)
            and isinstance(parent_path[-1], str)
            and parent_path[-1].casefold() == "personal_fill_pattern"
        )
    )


def _strip_media(value: Any, *, path: tuple[str | int, ...] = ()) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _strip_media(child, path=(*path, str(key)))
            for key, child in value.items()
            if not _is_media_key(key, parent_path=path) and not _is_data_uri(child)
        }
    if isinstance(value, list):
        return [
            _strip_media(child, path=(*path, index))
            for index, child in enumerate(value)
            if not _is_data_uri(child)
        ]
    if _is_data_uri(value):
        return None
    return deepcopy(value)


def _object_summary(item: Any) -> Any:
    if isinstance(item, list):
        return [_object_summary(child) for child in item]
    if not isinstance(item, Mapping):
        return deepcopy(item)
    result: dict[str, Any] = {}
    for key, value in item.items():
        name = str(key)
        if name in _OBJECT_GEOMETRY_KEYS or _is_media_key(name) or _is_data_uri(value):
            continue
        if name == "children":
            result[name] = _object_summary(value)
        else:
            result[name] = _strip_media(value, path=(name,))
    return result


def _collection_page(value: Any, *, offset: int, limit: int) -> tuple[Any, dict[str, Any] | None]:
    if isinstance(value, list):
        total = len(value)
        return value[offset : offset + limit], {
            "total": total,
            "offset": offset,
            "limit": limit,
            "next_offset": min(offset + limit, total) if offset + limit < total else None,
        }
    if isinstance(value, Mapping):
        keys = sorted(value, key=lambda item: str(item))
        total = len(keys)
        selected = keys[offset : offset + limit]
        return {str(key): value[key] for key in selected}, {
            "total": total,
            "offset": offset,
            "limit": limit,
            "next_offset": min(offset + limit, total) if offset + limit < total else None,
        }
    return value, None


def _project_field_page(
    snapshot: dict[str, Any], field: str, *, offset: int, limit: int
) -> tuple[Any, dict[str, Any] | None]:
    value = snapshot[field]
    if field == "garden_memory" and isinstance(value, Mapping):
        events, page = _collection_page(value.get("events", {}), offset=offset, limit=limit)
        return {"version": value.get("version", 1), "events": events}, page
    if field in _PAGED_FIELDS:
        return _collection_page(value, offset=offset, limit=limit)
    return value, None


def project_document_state(
    snapshot: dict[str, Any],
    *,
    revision: str,
    mode: str,
    domains: list[str] | None = None,
    offset: int = 0,
    limit: int = 100,
    media_offset: int = 0,
    media_limit: int = 100,
    object_fields: list[str] | None = None,
    expected_revision: str | None = None,
) -> dict[str, Any]:
    """Return a paged business-data view without canvas geometry or image blobs."""
    selected = list(DOCUMENT_DOMAINS) if domains is None else domains
    if not isinstance(selected, list) or any(not isinstance(name, str) for name in selected):
        raise ValueError("domains must be a list of domain names")
    if len(set(selected)) != len(selected) or set(selected) - set(DOCUMENT_DOMAINS):
        unknown = sorted(set(selected) - set(DOCUMENT_DOMAINS))
        raise ValueError(f"Unknown or repeated document domain: {', '.join(unknown) or 'duplicate'}")
    for name, value, maximum in (
        ("offset", offset, 10_000_000),
        ("limit", limit, MAX_DOCUMENT_PAGE_SIZE),
        ("media_offset", media_offset, 10_000_000),
        ("media_limit", media_limit, MAX_MEDIA_PAGE_SIZE),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
            raise ValueError(f"{name} must be an integer between 0 and {maximum}")
    if limit == 0 or media_limit == 0:
        raise ValueError("limit and media_limit must be at least 1")
    if expected_revision is not None and expected_revision != revision:
        raise ValueError("Document revision changed; restart pagination at the returned revision")

    if object_fields is not None and (not isinstance(object_fields, list) or len(object_fields) > 32
                                      or any(not isinstance(field, str) or not field or len(field) > 100
                                             for field in object_fields)
                                      or len(set(object_fields)) != len(object_fields)):
        raise ValueError("object_fields must contain at most 32 distinct top-level field names")
    omitted_object_fields: dict[str, list[str]] = {}
    fields: list[str] = []
    for domain in selected:
        for field in _DOMAIN_FIELDS[domain]:
            if field not in fields:
                fields.append(field)
    document: dict[str, Any] = {"version": snapshot.get("version", "1.0")}
    pagination: dict[str, Any] = {}
    if "objects" in fields:
        objects, page = _collection_page(snapshot.get("objects", []), offset=offset, limit=limit)
        if object_fields is not None and isinstance(objects, list):
            requested = set(object_fields)
            filtered = []
            for index, item in enumerate(objects, start=offset):
                if isinstance(item, Mapping):
                    omitted_object_fields[str(item.get("item_id", index))] = sorted(str(key) for key in item if key not in requested)
                    item = {key: value for key, value in item.items() if key in requested}
                filtered.append(item)
            objects = filtered
        document["objects"] = _object_summary(objects)
        if page is not None:
            pagination["objects"] = page
        fields.remove("objects")
    for field in fields:
        if field not in snapshot:
            continue
        value, page = _project_field_page(snapshot, field, offset=offset, limit=limit)
        document[field] = _strip_media(value)
        if page is not None:
            pagination[field] = page

    # Media manifest entries refer only to records present in this page. The
    # media bytes are fetched separately with the same revision.
    assets = []
    for field in (field for domain in selected for field in _DOMAIN_FIELDS[domain]):
        if field not in snapshot:
            continue
        value = snapshot[field]
        path: tuple[str | int, ...] = (field,)
        if field == "garden_memory" and isinstance(value, Mapping):
            value = value.get("events", {})
            path = (field, "events")
        if field in _PAGED_FIELDS or field == "garden_memory":
            if isinstance(value, list):
                for index in range(offset, min(offset + limit, len(value))):
                    assets.extend(_walk_media(value[index], scope="document", path=(*path, index)))
                continue
            if isinstance(value, Mapping):
                for key in sorted(value, key=lambda item: str(item))[offset : offset + limit]:
                    assets.extend(_walk_media(value[key], scope="document", path=(*path, str(key))))
                continue
        assets.extend(_walk_media({field: value}, scope="document"))
    media_page = assets[media_offset : media_offset + media_limit]
    public_media = [{key: value for key, value in asset.items() if key != "path_parts"} for asset in media_page]

    result = {
        "revision": revision,
        "mode": mode,
        "domains": selected,
        "document": document,
        "pagination": pagination,
        "media": {
            "items": public_media,
            "total": len(assets),
            "offset": media_offset,
            "limit": media_limit,
            "next_offset": min(media_offset + media_limit, len(assets))
            if media_offset + media_limit < len(assets)
            else None,
        },
        "omitted": ["object_geometry", "embedded_media_bytes", "unknown_top_level_fields"],
    }
    if object_fields is not None:
        result["omitted_object_fields"] = omitted_object_fields
    if _json_size(result) > MAX_DOCUMENT_STATE_BYTES:
        raise ValueError(
            f"Document state exceeds {MAX_DOCUMENT_STATE_BYTES} JSON bytes; "
            "request fewer domains or a smaller limit, then follow next_offset. "
            "For one oversized object select object_fields=['item_id','name','type']."
        )
    return result


def read_document_media(
    snapshot: dict[str, Any],
    *,
    media_id: str,
    revision: str,
    expected_revision: str,
    offset: int = 0,
    max_bytes: int = MAX_MEDIA_CHUNK_BYTES,
) -> dict[str, Any]:
    """Read one embedded media string in bounded UTF-8 chunks at a pinned revision."""
    if revision != expected_revision:
        raise ValueError("Document revision changed; call get_document_state again before reading media")
    if not isinstance(media_id, str) or not media_id:
        raise ValueError("media_id must be a nonempty string from get_document_state")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= MAX_MEDIA_CHUNK_BYTES:
        raise ValueError(f"max_bytes must be between 1 and {MAX_MEDIA_CHUNK_BYTES}")
    assets = _walk_media(snapshot, scope="document")
    match = next((asset for asset in assets if asset["media_id"] == media_id), None)
    if match is None:
        raise ValueError("Unknown media_id; request the manifest again")
    path_parts = match["path_parts"]
    value: Any = snapshot
    for part in path_parts:
        value = value[int(part)] if isinstance(value, list) else value[str(part)]
    encoded_bytes = _encoded_size(value)
    chunk, consumed = _slice_text_by_bytes(value, offset=offset, size=max_bytes, label="offset")
    next_offset = offset + consumed
    return {
        "media_id": media_id,
        "revision": revision,
        "encoding": match["encoding"],
        "field": match["field"],
        "offset": offset,
        "offset_unit": "UTF-8 bytes in the encoded text; base64 content is ASCII",
        "encoded_bytes": encoded_bytes,
        "data": chunk,
        "next_offset": next_offset if next_offset < encoded_bytes else None,
    }


def project_library_page(
    plants: dict[str, Any],
    *,
    revision: str,
    query: str | None = None,
    offset: int = 0,
    limit: int = 100,
    plant_id: str | None = None,
    include_media: bool = False,
    media_id: str | None = None,
    media_offset: int = 0,
    media_limit_bytes: int = MAX_MEDIA_CHUNK_BYTES,
    expected_revision: str | None = None,
    view: Literal["summary", "detail"] = "detail",
    fields: list[str] | None = None,
    include_source: bool = False,
) -> dict[str, Any]:
    """Search/page personal plants while separating and bounding embedded art."""
    if expected_revision is not None and expected_revision != revision:
        raise ValueError("Plant library changed; restart pagination at the returned revision")
    if query is not None and (not isinstance(query, str) or len(query) > 200):
        raise ValueError("query must be at most 200 characters")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIBRARY_PAGE_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_LIBRARY_PAGE_SIZE}")
    if plant_id is not None and (not isinstance(plant_id, str) or not plant_id):
        raise ValueError("plant_id must be a nonempty library record ID")
    if plant_id is not None and offset != 0:
        raise ValueError("offset must be 0 when reading a plant_id")
    if media_id is not None and (not isinstance(media_id, str) or not media_id):
        raise ValueError("media_id must be a nonempty ID from media_manifest")
    if isinstance(media_offset, bool) or not isinstance(media_offset, int) or media_offset < 0:
        raise ValueError("media_offset must be a nonnegative integer")
    if (
        isinstance(media_limit_bytes, bool)
        or not isinstance(media_limit_bytes, int)
        or not 1 <= media_limit_bytes <= MAX_MEDIA_CHUNK_BYTES
    ):
        raise ValueError(f"media_limit_bytes must be between 1 and {MAX_MEDIA_CHUNK_BYTES}")
    if include_media and plant_id is None:
        raise ValueError("include_media requires plant_id so the media read stays bounded")
    if media_id is not None and (not include_media or plant_id is None):
        raise ValueError("media_id requires include_media=true and a plant_id")

    if view not in {"summary", "detail"}:
        raise ValueError("view must be summary or detail")
    if fields is not None and (not isinstance(fields, list) or len(fields) > 32
                               or any(not isinstance(field, str) or not field or len(field) > 100
                                      for field in fields) or len(set(fields)) != len(fields)):
        raise ValueError("fields must contain at most 32 distinct top-level field names")
    if fields is not None and not include_source and set(fields) & {"raw_data", "provider_data", "html"}:
        raise ValueError("Source fields require include_source=true")

    normalized_query = query.strip().casefold() if query else ""
    if plant_id is not None:
        matching = [plant_id] if plant_id in plants else []
    else:
        matching = []
        for identity, record in plants.items():
            if not normalized_query:
                matching.append(identity)
                continue
            parts = [str(identity)[:512]]
            if isinstance(record, Mapping):
                parts.extend(
                    value[:2_048]
                    for field in _SEARCH_FIELDS
                    if isinstance((value := record.get(field)), str) and not _is_data_uri(value)
                )
            searchable = " ".join(parts).casefold()
            if normalized_query in searchable:
                matching.append(identity)
    matching.sort()
    total = len(matching)
    selected_ids = matching[offset : offset + limit]
    projected: dict[str, Any] = {}
    truncated: list[str] = []
    omitted_fields: dict[str, list[str]] = {}
    for identity in selected_ids:
        record = plants[identity]
        selected_record = record
        if isinstance(record, Mapping):
            selected_fields = set(fields) if fields is not None else (
                set(_PLANT_SUMMARY_FIELDS) if view == "summary" else set(record)
            )
            if not include_source:
                selected_fields -= {"raw_data", "provider_data", "html"}
            selected_record = {key: value for key, value in record.items() if key in selected_fields}
            omitted_fields[str(identity)] = sorted(str(key) for key in record if key not in selected_record)
        clean = _strip_media(selected_record)
        if not isinstance(clean, dict):
            clean = {"value": clean[:2_048] if isinstance(clean, str) else clean}
        if _json_size(clean) > MAX_LIBRARY_RECORD_BYTES:
            clean = _strip_media(
                {
                    field: record[field][:2_048] if isinstance(record[field], str) else deepcopy(record[field])
                    for field in _PLANT_SUMMARY_FIELDS
                    if (fields is None or field in fields)
                    and isinstance(record, Mapping)
                    and field in record
                    and not _is_data_uri(record[field])
                }
            )
            truncated.append(identity)
            if isinstance(record, Mapping):
                omitted_fields[str(identity)] = sorted(str(key) for key in record if key not in clean)
        projected[str(identity)] = clean
    result: dict[str, Any] = {
        "scope": "library",
        "revision": revision,
        "plants": projected,
        "total": total,
        "offset": offset,
        "limit": limit,
        "next_offset": min(offset + len(projected), total) if offset + len(projected) < total else None,
        "truncated_records": truncated,
        "omitted_fields": omitted_fields,
        "view": view,
    }
    if plant_id is not None:
        result["found"] = bool(selected_ids)
    record_assets_by_id: dict[str, tuple[str, dict[str, Any]]] = {}
    media_manifest: list[dict[str, Any]] = []
    for identity in selected_ids:
        record_assets = _walk_media(plants[identity], scope=f"library:{identity}")
        for asset in record_assets:
            record_assets_by_id[asset["media_id"]] = (str(identity), asset)
            media_manifest.append(
                {
                    "plant_id": str(identity),
                    "media_id": asset["media_id"],
                    "field": asset["field"],
                    "encoding": asset["encoding"],
                    "encoded_bytes": asset["encoded_bytes"],
                }
            )
    result["media_manifest"] = media_manifest
    if media_id is not None and not selected_ids:
        raise ValueError("Unknown plant_id or media_id; read media_manifest first")
    if include_media and selected_ids:
        if media_id is not None:
            selected_asset = record_assets_by_id.get(media_id)
            if selected_asset is None or selected_asset[0] != str(selected_ids[0]):
                raise ValueError("Unknown media_id for this plant; read media_manifest first")
            record_assets = [selected_asset[1]]
        else:
            record_assets = _walk_media(plants[selected_ids[0]], scope=f"library:{selected_ids[0]}")
        media_parts: list[dict[str, Any]] = []
        for asset in record_assets:
            # Plant visuals are expected to be PNG. Each call returns one
            # bounded chunk per asset. media_id selects one when records carry
            # multiple images; the multi-image compatibility form marks assets
            # already complete at this offset instead of failing the whole read.
            identity = selected_ids[0]
            value: Any = plants[identity]
            for part in asset["path_parts"]:
                value = value[int(part)] if isinstance(value, list) else value[str(part)]
            encoded_size = _encoded_size(value)
            finished = media_offset >= encoded_size
            if finished:
                chunk, end = "", encoded_size
            else:
                chunk, consumed = _slice_text_by_bytes(
                    value,
                    offset=media_offset,
                    size=media_limit_bytes,
                    label="media_offset",
                )
                end = media_offset + consumed
            media_parts.append({
                "media_id": asset["media_id"],
                "field": asset["field"],
                "encoding": asset["encoding"],
                "offset": media_offset,
                "offset_unit": "UTF-8 bytes in the encoded text; base64 content is ASCII",
                "encoded_bytes": encoded_size,
                "data": chunk,
                "finished": finished or end >= encoded_size,
                "next_offset": end if end < encoded_size else None,
            })
        result["media"] = media_parts
    if _json_size(result) > MAX_LIBRARY_RESPONSE_BYTES:
        raise ValueError(
            f"Plant-library page exceeds {MAX_LIBRARY_RESPONSE_BYTES} JSON bytes; "
            "request a smaller limit or a higher offset."
        )
    return result


def project_library_snapshot(snapshot: dict[str, Any], **arguments: Any) -> dict[str, Any]:
    """Project a LibraryPort snapshot while pinning pages to its current data."""
    plants = snapshot.get("plants")
    if not isinstance(plants, dict):
        raise ValueError("Plant-library snapshot must contain a plants object")
    return project_library_page(plants, revision=_plants_revision(plants), **arguments)
