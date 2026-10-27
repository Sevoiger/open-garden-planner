"""Bounded read tools for personal lists and explicitly scoped seed inventory."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any, Literal

import anyio


def page_records(records: list[dict[str, Any]], *, offset: int, limit: int,
                 query: str = "", record_id: str | None = None,
                 expected_revision: str | None = None, revision_context: Any = None) -> dict[str, Any]:
    """Project known small fields before filtering, never expose media blobs."""
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if len(query) > 200:
        raise ValueError("query must be at most 200 characters")
    revision = hashlib.sha256(json.dumps([revision_context, records], sort_keys=True,
                                        allow_nan=False, separators=(",", ":")).encode()).hexdigest()
    if expected_revision is not None and expected_revision != revision:
        raise ValueError("Catalog revision changed; restart pagination")
    selected = [row for row in records if (record_id is None or row.get("id") == record_id)
                and (not query or query.casefold() in str(row).casefold())]
    page = selected[offset:offset + limit]
    return {"records": page, "total": len(selected), "offset": offset,
            "limit": limit, "next_offset": offset + len(page) if offset + len(page) < len(selected) else None,
            "read_only": True, "revision": revision}


def _small(record: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    # Only scalar business fields; old files may contain arbitrary extensions.
    result = {}
    for key in keys:
        value = record.get(key)
        if value is None or isinstance(value, (bool, int, float)):
            if key in record:
                result[key] = value
        elif isinstance(value, str):
            result[key] = value[:2000] if not value.startswith("data:") else "[media omitted]"
    return result


def register_personal_catalog_tools(
    mcp: Any, *, snapshot: Callable[[], dict[str, Any]],
    plant_lists: Callable[[], list[dict[str, Any]]] | None = None,
    global_seeds: Callable[[], list[dict[str, Any]]] | None = None,
    writes_active: bool, authorize: Callable[[], None],
) -> None:
    """Global stores require auth; autonomous hosts expose only project seeds."""
    if plant_lists is not None and writes_active:
        @mcp.tool(structured_output=False)
        async def get_plant_lists(query: str = "", offset: int = 0, limit: int = 25,
                                  expected_revision: str | None = None) -> dict[str, Any]:
            """Read personal planting-list summaries, distinct from botanical sheets.

            Authenticated read only. Use get_plant_list for a list's entries.
            Query matches summaries; limit 1..100. Long text is capped at 2000 characters.
            Pin following pages with expected_revision from the first page.
            """
            authorize()
            def read():
                rows = [{**_small(row, ("id", "name", "description")), "entry_count": len(row.get("entries", []))}
                        for row in plant_lists()]
                return {"scope": "global", **page_records(rows, offset=offset, limit=limit, query=query,
                                                          expected_revision=expected_revision,
                                                          revision_context="global:list-index")}
            return await anyio.to_thread.run_sync(read)

        @mcp.tool(structured_output=False)
        async def get_plant_list(list_id: str, query: str = "", offset: int = 0, limit: int = 25,
                                 expected_revision: str | None = None) -> dict[str, Any]:
            """Read paginated personal-list entries, without placing plants or media.

            Species keys refer to botanical identities; entry IDs refer to this list.
            Authenticated read only. Long text is capped at 2000 characters.
            This list has its own revision; pin following entry pages with it.
            """
            authorize()
            def read():
                row = next((row for row in plant_lists() if row.get("id") == list_id), None)
                if row is None:
                    raise ValueError("Unknown plant list")
                entries = [_small(entry, ("id", "species_key", "common_name", "scientific_name", "note", "added_at"))
                           for entry in row.get("entries", [])]
                summary = _small(row, ("id", "name", "description"))
                return {"scope": "global", "list": summary,
                        **page_records(entries, offset=offset, limit=limit, query=query,
                                       expected_revision=expected_revision,
                                       revision_context=["global:list", summary])}
            return await anyio.to_thread.run_sync(read)

    @mcp.tool(structured_output=False)
    async def get_seed_inventory(scope: Literal["project", "global"] = "project", packet_id: str | None = None,
                                 query: str = "", offset: int = 0, limit: int = 25,
                                 expected_revision: str | None = None) -> dict[str, Any]:
        """Read seed packets by scope, ID or search, with bounded pagination.

        Project packets are saved in .ogp; global packets belong to the separate
        Seed Inventory tab and require authentication and a live host. No inferred
        viability or botanical traits are added. Media omitted; text capped at 2000 chars.
        Pin subsequent pages with expected_revision; each scope has its own revision.
        """
        if scope == "global":
            if not writes_active or global_seeds is None:
                raise ValueError("Global seed inventory unavailable on this host")
            authorize()
        def read():
            raw = global_seeds() if scope == "global" else snapshot().get("seed_inventory", [])
            fields = ("id", "species_id", "species_name", "variety", "purchase_year", "manufacturer", "batch_number",
                      "quantity", "quantity_unit", "germination_temp_min_c", "germination_temp_opt_c",
                      "germination_temp_max_c", "germination_days_min", "germination_days_max", "light_germinator",
                      "cold_stratification", "stratification_days", "pre_treatment", "notes", "created_date",
                      "viability_shelf_life_override")
            rows = [_small(row, fields) for row in raw if isinstance(row, dict)]
            return {"scope": scope, "media_omitted": True,
                    **page_records(rows, offset=offset, limit=limit, query=query, record_id=packet_id,
                                   expected_revision=expected_revision, revision_context=["seeds", scope])}
        return await anyio.to_thread.run_sync(read)
