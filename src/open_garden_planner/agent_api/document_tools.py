"""Register the small shared document port on either MCP host."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import anyio

from open_garden_planner.agent_api.read_projection import project_library_snapshot


@dataclass
class DomainProviders:
    """Only the ports available to a document host without Qt."""

    document_operation: Callable[[str, dict[str, Any]], dict[str, Any]]
    library_operation: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None


def register_document_tools(mcp: Any, provider: Callable[..., dict[str, Any]], *,
                            writes_active: bool, authorize: Callable[[], None]) -> None:
    @mcp.tool(structured_output=False)
    async def get_document_state(domains: list[str] | None = None, offset: int = 0,
                                 limit: int = 100, media_offset: int = 0,
                                 media_limit: int = 100,
                                 object_fields: list[str] | None = None,
                                 expected_revision: str | None = None) -> dict[str, Any]:
        """Read a bounded page of business state, excluding geometry and embedded image bytes.

        Domains: plan, objects, tasks, soil, harvest, pests, journal, succession,
        inventory, shopping and garden_memory. Collections use offset/limit;
        media is a separate manifest page. Use object_fields=["item_id","name","type"]
        for compact object summaries, including oversized records. Continue all pages with the returned
        revision as expected_revision so one response cannot mix document states.
        """
        arguments = {"domains": domains, "offset": offset, "limit": limit,
                     "media_offset": media_offset, "media_limit": media_limit,
                     "object_fields": object_fields,
                     "expected_revision": expected_revision}
        return await anyio.to_thread.run_sync(lambda: provider("read", arguments))

    @mcp.tool(structured_output=False)
    async def get_document_media(media_id: str, expected_revision: str, offset: int = 0,
                                 max_bytes: int = 65536) -> dict[str, Any]:
        """Read one embedded media string in a bounded UTF-8 byte chunk.

        Use a media_id and revision from get_document_state. Base64 content is
        ASCII, so its encoded-text byte offsets equal character offsets.
        """
        arguments = {"media_id": media_id, "expected_revision": expected_revision,
                     "offset": offset, "max_bytes": max_bytes}
        return await anyio.to_thread.run_sync(lambda: provider("media", arguments))

    @mcp.tool(structured_output=False)
    async def prepare_document_operations(operations: list[dict[str, Any]],
                                          expected_revision: str | None = None) -> dict[str, Any]:
        """Preview a bounded atomic project batch, without writing or changing undo.

        Operations use operation, target_id and fields. Discover supported
        names in get_capabilities. The result
        includes every before/after value and a session preparation_id expiring
        after five minutes. Inspect it, then apply using a stable request_key.
        """
        return await anyio.to_thread.run_sync(
            lambda: provider("prepare", {"operations": operations, "expected_revision": expected_revision}))

    @mcp.tool(structured_output=False)
    async def get_document_operation(request_key: str) -> dict[str, Any]:
        """Look up an application receipt after a lost response or restart.

        A receipt records original acceptance, not a claim that a later undo
        has not changed the document. Live receipts survive restart after save.
        """
        return await anyio.to_thread.run_sync(lambda: provider("result", {"request_key": request_key}))

    @mcp.tool(structured_output=False)
    async def assess_document_soil(target_id: str = "global", area_m2: float = 0) -> dict[str, Any]:
        """Read health and amendment advice, without recording an intervention.

        Area is explicit in square metres. Kit categories remain separate from
        ppm; unknown readings remain unknown. With area zero, no dosage is given.
        """
        from open_garden_planner.services.document_analysis import assess_document_soil as assess

        snapshot = await anyio.to_thread.run_sync(lambda: provider("snapshot", {}))
        return await anyio.to_thread.run_sync(lambda: assess(snapshot["document"], target_id, area_m2=area_m2))

    if writes_active:
        @mcp.tool(structured_output=False)
        async def apply_document_operations(preparation_id: str, request_key: str) -> dict[str, Any]:
            """Apply one prepared batch, rechecking revision; one shared undo step.

            Same key and intent replays without another mutation. A conflict,
            expired preparation or invalid batch applies nothing. Live success
            remains in memory until save_plan; inspect persisted in the receipt.
            """
            authorize()
            return await anyio.to_thread.run_sync(
                lambda: provider("apply", {"preparation_id": preparation_id, "request_key": request_key}))


def register_autonomous_history(mcp: Any, provider: Callable[..., dict[str, Any]], *,
                                authorize: Callable[[], None]) -> None:
    @mcp.tool(structured_output=False)
    async def undo() -> dict[str, Any]:
        """Undo one data operation and atomically persist it. Session history only."""
        authorize()
        return await anyio.to_thread.run_sync(lambda: provider("undo", {}))

    @mcp.tool(structured_output=False)
    async def redo() -> dict[str, Any]:
        """Redo one data operation and atomically persist it. Session history only."""
        authorize()
        return await anyio.to_thread.run_sync(lambda: provider("redo", {}))


def register_library_tools(mcp: Any, provider: Callable[..., dict[str, Any]], *,
                           writes_active: bool, authorize: Callable[[], None]) -> None:
    """Global-library access is explicit and authenticated, including previews."""
    if not writes_active:
        return

    @mcp.tool(structured_output=False)
    async def get_custom_plant_library(query: str | None = None, offset: int = 0,
                                       limit: int = 100, plant_id: str | None = None,
                                       include_media: bool = False, media_id: str | None = None,
                                       media_offset: int = 0,
                                       media_limit_bytes: int = 65536,
                                       expected_revision: str | None = None,
                                       view: Literal["summary", "detail"] = "detail",
                                       fields: list[str] | None = None,
                                       include_source: bool = False) -> dict[str, Any]:
        """Search/page personal plants or read one by exact ID, without inline media.

        view="summary" returns identity; fields selects top-level fields.
        Source raw_data/provider_data/html require include_source=true; media
        remains separate. Omitted fields are listed explicitly.
        Pages contain at most 100 plants. Search covers record ID and botanical
        identity fields. Pin subsequent pages with expected_revision. Embedded
        manifest IDs are returned without the bytes. Media is opt-in, requires
        plant_id, and is returned as separate bounded chunks. Select one asset
        with media_id; offsets count UTF-8 bytes in encoded text (base64 is ASCII).
        """
        authorize()
        arguments = {"query": query, "offset": offset, "limit": limit,
                     "plant_id": plant_id, "include_media": include_media,
                     "view": view, "fields": fields, "include_source": include_source,
                     "media_id": media_id,
                     "media_offset": media_offset,
                     "media_limit_bytes": media_limit_bytes,
                     "expected_revision": expected_revision}
        return await anyio.to_thread.run_sync(
            lambda: project_library_snapshot(provider("snapshot", {}), **arguments)
        )

    @mcp.tool(structured_output=False)
    async def prepare_plant_library_import(records: list[dict[str, Any]]) -> dict[str, Any]:
        """Preview up to 100 sheets, matching explicit source_id, never common name.

        Does not write. Conflicting rows refuse the entire batch on application.
        This changes the global library only, not snapshots in garden projects.
        """
        authorize()
        return await anyio.to_thread.run_sync(lambda: provider("prepare", {"records": records}))

    @mcp.tool(structured_output=False)
    async def apply_plant_library_import(preparation_id: str, request_key: str) -> dict[str, Any]:
        """Commit one atomic library transaction, with durable replay protection.

        Use token returned by preparation as preparation_id. Undo is a separate
        library transaction, independent of the document/canvas history.
        """
        authorize()
        return await anyio.to_thread.run_sync(
            lambda: provider("apply", {"preparation_id": preparation_id, "request_key": request_key}))

    @mcp.tool(structured_output=False)
    async def get_plant_library_operation(request_key: str) -> dict[str, Any]:
        """Read a durable library receipt after a timeout or restart."""
        authorize()
        return await anyio.to_thread.run_sync(lambda: provider("result", {"request_key": request_key}))

    @mcp.tool(structured_output=False)
    async def undo_plant_library_import(transaction_id: str) -> dict[str, Any]:
        """Undo an import if affected records have not changed since its commit."""
        authorize()
        return await anyio.to_thread.run_sync(lambda: provider("undo", {"transaction_id": transaction_id}))
