"""Personal lists are distinct from sheets; project seeds are distinct from global packets."""

import asyncio
import json

import pytest
from mcp.server.fastmcp import FastMCP

from open_garden_planner.agent_api.personal_catalog_tools import (
    page_records,
    register_personal_catalog_tools,
)


def test_catalogs_auth_projection_pagination_and_scope():
    async def run():
        mcp = FastMCP("synthetic")
        allowed = False
        calls = []

        def authorize():
            if not allowed:
                raise ValueError("Unauthorized")

        def seeds():
            calls.append("global")
            return [{"id": "global", "species_name": "Synthetic", "quantity": 3,
                     "notes": "x" * 100000, "photo_path": "data:" + "x" * 1000000}]

        lists = [{"id": "list", "name": "To plant", "entries": [
            {"id": str(i), "common_name": f"Synthetic {i}", "species_key": f"custom:{i}",
             "image": "x" * 1000000} for i in range(30)]}]
        register_personal_catalog_tools(
            mcp, snapshot=lambda: {"seed_inventory": [{"id": "project", "quantity": 2}]},
            plant_lists=lambda: lists, global_seeds=seeds, writes_active=True, authorize=authorize)

        async def call(name, **arguments):
            return json.loads((await mcp.call_tool(name, arguments))[0].text)

        with pytest.raises(Exception, match="Unauthorized"):
            await call("get_seed_inventory", scope="global")
        with pytest.raises(Exception, match="Unauthorized"):
            await call("get_plant_lists")
        assert not calls
        assert (await call("get_seed_inventory"))["records"] == [{"id": "project", "quantity": 2}]
        allowed = True
        summary = await call("get_plant_lists")
        assert summary["records"][0]["entry_count"] == 30
        assert "entries" not in summary["records"][0]
        entries = await call("get_plant_list", list_id="list", offset=25)
        assert entries["total"] == 30 and len(entries["records"]) == 5 and entries["next_offset"] is None
        assert len(json.dumps(entries)) < 2000
        assert (await call("get_plant_list", list_id="list", query="custom:29"))["total"] == 1
        global_page = await call("get_seed_inventory", scope="global", packet_id="global")
        assert global_page["records"][0]["quantity"] == 3
        assert len(json.dumps(global_page)) < 3000 and "photo_path" not in str(global_page)
        assert calls == ["global"]

    asyncio.run(run())


def test_autonomous_never_resolves_global_storage():
    async def run():
        mcp = FastMCP("synthetic")
        register_personal_catalog_tools(mcp, snapshot=lambda: {}, writes_active=False, authorize=lambda: None)
        assert [t.name for t in await mcp.list_tools()] == ["get_seed_inventory"]
        with pytest.raises(Exception, match="unavailable"):
            await mcp.call_tool("get_seed_inventory", {"scope": "global"})
    asyncio.run(run())


def test_personal_pages_detect_insertions_and_edits_before_continuation():
    async def run():
        mcp = FastMCP("synthetic")
        seeds = [{"id": "first", "quantity": 1}, {"id": "second", "quantity": 2}]
        lists = [{"id": "one", "name": "One", "entries": [
            {"id": "entry-a", "common_name": "A"}, {"id": "entry-b", "common_name": "B"}]}]
        register_personal_catalog_tools(mcp, snapshot=lambda: {"seed_inventory": seeds},
                                        plant_lists=lambda: lists, global_seeds=lambda: seeds,
                                        writes_active=True, authorize=lambda: None)

        async def call(name, **arguments):
            return json.loads((await mcp.call_tool(name, arguments))[0].text)

        summary = await call("get_plant_lists", limit=1)
        entries = await call("get_plant_list", list_id="one", limit=1)
        project = await call("get_seed_inventory", limit=1)
        global_page = await call("get_seed_inventory", scope="global", limit=1)
        assert project["revision"] != global_page["revision"]
        assert (await call("get_seed_inventory", limit=1, offset=1,
                           expected_revision=project["revision"]))["records"][0]["id"] == "second"
        lists.insert(0, {"id": "zero", "name": "Zero", "entries": []})
        lists[1]["entries"].insert(0, {"id": "new", "common_name": "New"})
        seeds.insert(0, {"id": "zero", "quantity": 9})
        for name, arguments, revision in [
            ("get_plant_lists", {}, summary["revision"]),
            ("get_plant_list", {"list_id": "one"}, entries["revision"]),
            ("get_seed_inventory", {}, project["revision"]),
            ("get_seed_inventory", {"scope": "global"}, global_page["revision"]),
        ]:
            with pytest.raises(Exception, match="revision changed"):
                await call(name, offset=1, limit=1, expected_revision=revision, **arguments)
        # A fresh first page is the explicit recovery, with the inserted record.
        restarted = await call("get_seed_inventory", limit=1)
        assert restarted["records"][0]["id"] == "zero"
        assert restarted["revision"] != project["revision"]
    asyncio.run(run())


@pytest.mark.parametrize("offset,limit", [(-1, 25), (False, 25), (0, 0), (0, 101), (0, True)])
def test_invalid_page_bounds(offset, limit):
    with pytest.raises(ValueError):
        page_records([], offset=offset, limit=limit)
