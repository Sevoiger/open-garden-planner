"""Real MCP transport with synthetic media larger than the connector's old limit."""

import asyncio
import hashlib
import json
import socket

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from open_garden_planner.agent_api.document_tools import DomainProviders
from open_garden_planner.agent_api.server import AgentApiServer
from open_garden_planner.core.document_session import DocumentSession
from open_garden_planner.core.plan_io import write_plan
from open_garden_planner.services.library_port import LibraryPort


def test_large_library_and_plan_read_over_http_without_mutation(tmp_path):
    plan, library_path = tmp_path / "synthetic.ogp", tmp_path / "library.json"
    structures = [
        {"type": "polygon", "item_id": f"structure-{index}", "object_type": kind,
         "name": f"Synthetic {kind}", "points": [[0, 0], [400, 0], [400, 300], [0, 300]],
         "metadata": {"personal_fill_pattern": {"png": str(index) * 1_000_000, "width_cm": 40},
                      "garden_tracking": {"notes": "Synthetic structure"}}}
        for index, kind in enumerate(["HOUSE", "PERGOLA", "HOUSE", "PERGOLA"], start=2)
    ]
    write_plan(plan, {"version": "1.4", "canvas": {"width": 500, "height": 500},
                      "objects": [{"type": "image", "item_id": "background", "image_data": "A" * 17_000_000},
                                  {"type": "rectangle", "item_id": "simple"}, *structures],
                      "manual_tasks": {"task": {"title": "Synthetic task"}}})
    library_path.write_text(json.dumps({"version": "1.0", "plants": {
        "alpha": {"source_id": "alpha", "common_name": "Alpha", "scientific_name": "Fictitia alpha",
                  "raw_data": {"personal_visual_png": "B" * 70_000_000, "extra_bytes": "XYZ"}},
        "beta": {"source_id": "beta", "common_name": "Beta", "scientific_name": "Fictitia beta"}}}))
    original_plan = hashlib.sha256(plan.read_bytes()).digest()
    original_library = hashlib.sha256(library_path.read_bytes()).digest()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with DocumentSession(plan) as host:
        library = LibraryPort(library_path)
        server = AgentApiServer(DomainProviders(host.dispatch, library.dispatch), port=port,
                                writes_enabled=True, write_token="synthetic-read-token")
        server.start()

        async def run():
            async with (streamablehttp_client(server.url, headers={"Authorization": "Bearer synthetic-read-token"})
                        as (reader, writer, _), ClientSession(reader, writer) as client):
                await client.initialize()

                async def call(name, **arguments):
                    result = await client.call_tool(name, arguments)
                    assert not result.isError, result.content
                    assert sum(len(content.text.encode()) for content in result.content) < 512_000
                    return json.loads(result.content[0].text)

                state = await call("get_document_state")
                assert "image_data" not in str(state["document"])
                assert state["media"]["total"] == 5
                fragment = await call("get_document_media", media_id=state["media"]["items"][0]["media_id"],
                                      expected_revision=state["revision"], max_bytes=17)
                assert fragment["data"] == "A" * 17
                for index in range(2, 6):
                    page = await call("get_document_state", domains=["objects"], offset=index, limit=1,
                                      expected_revision=state["revision"])
                    assert len(page["document"]["objects"]) == 1
                    item = page["document"]["objects"][0]
                    assert item["item_id"] == f"structure-{index}"
                    assert item["metadata"]["personal_fill_pattern"] == {"width_cm": 40}
                    assert item["metadata"]["garden_tracking"]["notes"] == "Synthetic structure"
                    assert page["media"]["total"] == 1
                    asset = page["media"]["items"][0]
                    assert asset["path"] == f"objects/{index}/metadata/personal_fill_pattern/png"
                    texture = await call("get_document_media", media_id=asset["media_id"],
                                         expected_revision=state["revision"], offset=23, max_bytes=17)
                    assert texture["data"] == str(index) * 17
                tasks = await call("get_document_state", domains=["tasks"], expected_revision=state["revision"])
                assert "objects" not in tasks["document"] and "task" in tasks["document"]["manual_tasks"]
                first = await call("get_custom_plant_library", limit=1)
                assert first["total"] == 2 and first["next_offset"] == 1
                assert "personal_visual_png" not in str(first["plants"])
                second = await call("get_custom_plant_library", offset=1, limit=1, expected_revision=first["revision"])
                assert list(second["plants"]) == ["beta"]
                searched = await call("get_custom_plant_library", query="Fictitia beta")
                assert searched["total"] == 1
                single = await call("get_custom_plant_library", plant_id="alpha", include_media=True,
                                    expected_revision=first["revision"], media_limit_bytes=19)
                assert single["media"][0]["data"] == "B" * 19
                continuation = await call("get_custom_plant_library", plant_id="alpha", include_media=True,
                                          expected_revision=first["revision"], media_offset=19, media_limit_bytes=19)
                assert continuation["media"][0]["data"] == "B" * 19
                assert continuation["media"][1]["finished"] and continuation["media"][1]["data"] == ""
                selected = await call("get_custom_plant_library", plant_id="alpha", include_media=True,
                                      media_id=single["media_manifest"][0]["media_id"], media_offset=19,
                                      expected_revision=first["revision"], media_limit_bytes=19)
                assert len(selected["media"]) == 1 and selected["media"][0]["data"] == "B" * 19
                invalid = await call("prepare_plant_library_import", records=[{
                    "source_id": "new", "scientific_name": "Fictitia invalid", "common_name": "Invalid",
                    "ph_min": 10, "ph_max": 2}])
                assert invalid["can_apply"] is False
                unknown = await call("prepare_plant_library_import", records=[{
                    "source_id": "new", "scientific_name": "Fictitia unknown", "common_name": "Unknown",
                    "invented_trait": True}])
                assert unknown["can_apply"] is False
        try:
            asyncio.run(run())
        finally:
            server.stop()
        assert host.history.undo_depth == 0
    assert hashlib.sha256(plan.read_bytes()).digest() == original_plan
    assert hashlib.sha256(library_path.read_bytes()).digest() == original_library
