"""Synthetic horticultural journeys through MCP and production Qt orchestration.

No direct widget invocation or project mutation in the client. The fixtures
redirect both global stores and project files before the window is constructed.
"""

import json
import threading
import traceback
from uuid import uuid4

import anyio
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox

from open_garden_planner import __version__
from open_garden_planner.agent_api import AgentApiServer
from tests.integration.test_agent_api_writes import TOKEN, _drive, _free_port, _run
from tests.integration.test_agent_ui_control import ui_window  # noqa: F401

_QUESTION = QMessageBox.question


class Desktop:
    def __init__(self, client):
        self.client = client

    async def call(self, tool, **args):
        result = await self.client.call_tool(tool, args)
        assert not result.isError, (tool, result.content)
        if result.structuredContent is not None:
            return result.structuredContent
        values = [json.loads(block.text) for block in result.content if block.type == "text"]
        return values[0] if len(values) == 1 else values

    async def raw(self):
        result = await self.client.read_resource("garden://plan/raw")
        return json.loads(result.contents[0].text)

    async def state(self, root=None, hidden=False):
        return await self.call("get_application_ui", root_id=root, max_nodes=10000, include_hidden=hidden)

    async def wait(self, receipt):
        for _ in range(300):
            state = await self.call("get_ui_operation", operation_id=receipt["operation_id"])
            if state["status"] in {"completed", "failed"}:
                assert state["status"] == "completed", state
                assert state["queued_at"] <= state["started_at"] <= state["finished_at"]
                return state
            await anyio.sleep(0.01)
        raise AssertionError(("Unfinished UI operation", receipt))

    async def run(self, tool, **args):
        receipt = await self.call(tool, request_key=str(uuid4()), **args)
        await self.wait(receipt)
        return receipt

    @staticmethod
    def one(state, **fields):
        matches = [n for n in state["nodes"] if all(n.get(k) == v for k, v in fields.items())]
        assert len(matches) == 1, (fields, [(n["class"], n.get("label"), n.get("text")) for n in matches])
        return matches[0]

    async def modal(self):
        for _ in range(100):
            state = await self.state()
            if state["modal_id"]:
                return await self.state(state["modal_id"])
            await anyio.sleep(0.01)
        raise AssertionError(("Expected an actual modal dialog", state["operation_statuses"],
                              [(n["class"], n.get("title")) for n in state["nodes"] if n["parent_id"] is None]))

    async def field(self, state, label, value, cls=None):
        fields = {"label": label}
        if cls:
            fields["class"] = cls
        target = self.one(state, **fields)
        await self.run("set_ui_value", widget_id=target["id"], value=value)

    async def button(self, state, text, wait=True):
        target = self.one(state, **{"class": "QPushButton", "text": text})
        receipt = await self.call("interact_ui", widget_id=target["id"], action="click", request_key=str(uuid4()))
        if wait:
            await self.wait(receipt)
        return receipt

    async def tab(self, text):
        state = await self.state()
        tabs = [n for n in state["nodes"] if n["class"] == "QTabWidget"
                and any(c["text"] == text for c in n.get("choices", []))]
        assert len(tabs) == 1, (text, [(n["class"], n.get("choices")) for n in state["nodes"] if "choices" in n])
        await self.run("set_ui_value", widget_id=tabs[0]["id"], value=text)

    async def open_action(self, text):
        state = await self.state()
        actions = [a for a in state["actions"] if text in a["text"].replace("&", "") and a["enabled"]]
        assert len(actions) == 1, (text, [a["text"] for a in actions])
        receipt = await self.call("invoke_ui_action", action_id=actions[0]["id"], request_key=str(uuid4()))
        return await self.modal(), receipt

    async def file_dialog(self, path, button):
        form = await self.modal()
        for _ in range(100):
            if any(n["class"] == "QFileDialog" for n in form["nodes"]):
                break
            await anyio.sleep(0.01)
            form = await self.modal()
        field = self.one(form, **{"class": "QLineEdit", "object_name": "fileNameEdit"})
        await self.run("set_ui_value", widget_id=field["id"], value=str(path))
        # File dialogs use accelerator markers in the displayed button text.
        targets = [n for n in form["nodes"] if n["class"] == "QPushButton" and n.get("text", "").replace("&", "") == button]
        assert len(targets) == 1, (button, targets)
        return await self.call("interact_ui", widget_id=targets[0]["id"], action="click", request_key=str(uuid4()))

    async def context_dialog(self, item_id, x, y, action_text):
        await self.tab("Garden Plan")
        state = await self.state()
        canvas = self.one(state, **{"class": "CanvasView"})["id"]
        await self.run("interact_ui", widget_id=canvas, action="select_scene", parameters={"item_ids": [item_id]})
        menu_receipt = await self.call("interact_ui", widget_id=canvas, action="context_menu",
                                       parameters={"x": x, "y": y, "coordinate_space": "scene"}, request_key=str(uuid4()))
        for _ in range(100):
            state = await self.state()
            if state["popup_id"]:
                break
            await anyio.sleep(0.01)
        actions = [a for a in state["actions"] if action_text in a["text"] and a["enabled"]]
        assert len(actions) == 1, (action_text, [a["text"] for a in state["actions"]])
        receipt = await self.call("invoke_ui_action", action_id=actions[0]["id"], request_key=str(uuid4()))
        return await self.modal(), [menu_receipt, receipt]


def run_cycle(window, qtbot, body):
    server = AgentApiServer(window._build_agent_providers(), port=_free_port(), write_token=TOKEN, writes_enabled=True)
    server.start()

    async def client(ctx):
        http_client, ClientSession, url = ctx
        async with http_client(url, headers={"Authorization": f"Bearer {TOKEN}"}) as (r, w, _), ClientSession(r, w) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.version == __version__
            try:
                await body(Desktop(session))
            except Exception as exc:
                print(traceback.format_exc())
                # Unwind child-first while the HTTP session can still deliver
                # input; closing a parent first can leave its QFileDialog alive.
                desktop = Desktop(session)
                for _ in range(8):
                    state = await desktop.state()
                    if not state["modal_id"] and not state["popup_id"]:
                        break
                    root = state["modal_id"] or state["popup_id"]
                    form = await desktop.state(root)
                    cancel = next((n for n in form["nodes"] if n["class"] == "QPushButton"
                                   and n.get("text", "").replace("&", "") in {"No", "Cancel"}), None)
                    await desktop.call("interact_ui", widget_id=cancel["id"] if cancel else root,
                                       action="click" if cancel else "close", request_key=str(uuid4()))
                    await anyio.sleep(0.02)
                raise AssertionError(traceback.format_exc()) from exc

    # Unwind nested event loops even when a remote assertion fails.
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(lambda: [w.close() for w in QApplication.topLevelWidgets() if isinstance(w, (QDialog, QMenu))])
    watchdog.start(19000)
    try:
        _run(server, client, qtbot)
    finally:
        watchdog.stop()
        server.stop()
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, (QDialog, QMenu)):
                widget.close()


def test_seed_lot_crud_via_mcp_and_global_storage(ui_window, qtbot, monkeypatch):  # noqa: F811
    window, _ = ui_window
    monkeypatch.setattr(QMessageBox, "question", _QUESTION)

    async def body(d):
        await d.tab("Seed Inventory")
        receipt = await d.button(await d.state(), "+ Add Packet", wait=False)
        form = await d.modal()
        unknown = d.one(form, **{"class": "QDoubleSpinBox", "object_name": "germination_temp_min_c"})
        assert unknown["value"] is None and unknown["display_text"] == "—"
        await d.run("set_ui_value", widget_id=unknown["id"], value=10)
        await d.run("set_ui_value", widget_id=unknown["id"], value=None)
        await d.field(form, "Species name:", "Synthetic tomato", "QLineEdit")
        await d.field(form, "Variety / cultivar:", "Audit F1", "QLineEdit")
        await d.field(form, "Purchase / harvest year:", 2026, "QSpinBox")
        await d.field(form, "Quantity:", 24, "QDoubleSpinBox")
        await d.field(form, "Batch / lot number:", "AUDIT-2026", "QLineEdit")
        await d.button(form, "OK")
        await d.wait(receipt)
        # Retry the completed opening input: no second packet or dialog.
        replay = await d.call("get_ui_operation", operation_id=receipt["operation_id"])
        assert replay["status"] == "completed"
        state = await d.state()
        table = d.one(state, **{"class": "QTableView"})
        data = await d.call("read_ui_model", widget_id=table["id"])
        assert data["headers"][:4] == ["Species", "Variety", "Year", "Quantity"]
        assert data["row_count"] == 1
        packet_id = data["rows"][0]["cells"][0]["user_data"]
        assert packet_id and "Synthetic tomato" in str(data)
        await d.run("interact_ui", widget_id=table["id"], action="select", parameters={"index_path": [0]})
        receipt = await d.button(await d.state(), "Edit", wait=False)
        form = await d.modal()
        await d.field(form, "Quantity:", 12, "QDoubleSpinBox")
        await d.button(form, "OK")
        await d.wait(receipt)
        # Switching away and back reloads the actual persisted global inventory.
        await d.tab("Garden Plan")
        await d.tab("Seed Inventory")
        table = d.one(await d.state(), **{"class": "QTableView"})
        data = await d.call("read_ui_model", widget_id=table["id"])
        assert data["rows"][0]["cells"][0]["user_data"] == packet_id
        assert "12" in data["rows"][0]["cells"][3]["text"]
        await d.run("interact_ui", widget_id=table["id"], action="select", parameters={"index_path": [0]})
        receipt = await d.button(await d.state(), "Mark as Used", wait=False)
        await d.button(await d.modal(), "&Yes")
        await d.wait(receipt)
        data = await d.call("read_ui_model", widget_id=table["id"])
        assert data["rows"][0]["cells"][3]["text"].startswith("0")
        await d.run("interact_ui", widget_id=table["id"], action="select", parameters={"index_path": [0]})
        receipt = await d.button(await d.state(), "Delete Selected", wait=False)
        await d.button(await d.modal(), "&Yes")
        await d.wait(receipt)
        assert (await d.call("read_ui_model", widget_id=table["id"]))["row_count"] == 0
    run_cycle(window, qtbot, body)
    # Independent store reload, rather than an assertion on the view's cache.
    from open_garden_planner.models.seed_inventory import SeedInventoryStore, get_seed_inventory
    store = get_seed_inventory()
    restored = SeedInventoryStore(store._path)
    assert restored.all() == []


def test_discovery_and_explicit_refusals_over_mcp(ui_window, qtbot, tmp_path):  # noqa: F811
    window, _ = ui_window

    async def body(d):
        capabilities = await d.call("get_capabilities")
        assert capabilities["identity"]["application_version"] == __version__
        assert capabilities["identity"]["mcp_sdk_version"] != ""
        assert capabilities["identity"]["source_sha256_at_server_build"]
        assert set(capabilities["retry_policy"]["persistent_request_key"]) == {
            "add_garden_intervention", "apply_document_operations", "apply_plant_library_import",
        }
        assert capabilities["retry_policy"]["persistent_receipt_lookup"] == {
            "apply_document_operations": "get_document_operation",
            "apply_plant_library_import": "get_plant_library_operation",
        }
        assert "move_object" in capabilities["retry_policy"]["read_before_retry"]
        tools = {t.name: t for t in (await d.client.list_tools()).tools}
        assert all(tool.inputSchema.get("additionalProperties") is False for tool in tools.values())
        schema = tools["add_garden_intervention"].inputSchema
        assert schema["properties"]["event_type"]["enum"] == [
            "planting", "relocation", "amendment", "fertilisation", "mulching", "sowing",
            "pruning", "watering", "observation", "removal_loss",
        ]
        assert schema["$defs"]["InterventionDetails"]["additionalProperties"] is False
        bad_inputs = [
            ("get_tasks", {"since": "2026-01-01"}),  # Wrong name must not silently use the default window.
            ("get_object", {"item_id": "not-a-uuid"}),
            ("get_object", {"item_id": str(uuid4())}),
            ("get_garden_record", {"item_id": "missing"}),
            ("save_plan", {}),
            ("save_plan", {"file_path": str(tmp_path / "absent" / "test.ogp")}),
            ("open_plan", {"file_path": str(tmp_path / "absent.ogp")}),
            ("move_object", {"item_id": "missing", "dx": 10, "dy": 0}),
            ("add_garden_intervention", {"target_ids": [], "event_type": "harvest", "description": "invalid", "request_key": "bad-type"}),
            ("add_garden_intervention", {"target_ids": ["missing"], "event_type": "sowing", "description": "invalid", "request_key": "bad-target"}),
            ("add_garden_intervention", {"target_ids": ["missing"], "event_type": "sowing", "description": "invalid", "request_key": "bad-field", "details": {"unrecognized": 2}}),
        ]
        for tool, arguments in bad_inputs:
            result = await d.client.call_tool(tool, arguments)
            assert result.isError and any(c.type == "text" and c.text.strip() for c in result.content), (tool, arguments, result)
        assert (await d.call("get_history"))["undo_depth"] == 0
    run_cycle(window, qtbot, body)


def test_timed_out_mcp_write_is_cancelled_before_qt_resumes(ui_window, qtbot, monkeypatch):  # noqa: F811
    window, _ = ui_window
    hop = window._agent_bridge.run_on_main
    monkeypatch.setattr(window._agent_bridge, "run_on_main", lambda fn: hop(fn, timeout=0.03))
    server = AgentApiServer(window._build_agent_providers(), port=_free_port(), write_token=TOKEN, writes_enabled=True)
    server.start()
    outcome = {}

    async def body(ctx):
        http_client, ClientSession, url = ctx
        async with http_client(url, headers={"Authorization": f"Bearer {TOKEN}"}) as (r, w, _), ClientSession(r, w) as session:
            await session.initialize()
            result = await session.call_tool("create_object", {"object_type": "TREE", "x": 400, "y": 300})
            assert result.isError and "cancelled before execution" in str(result.content)

    thread = threading.Thread(target=_drive, args=(server, body, outcome), daemon=True)
    try:
        thread.start()
        thread.join(timeout=3)  # Deliberately block Qt while the HTTP client times out.
        assert not thread.is_alive() and outcome.get("error") is None, outcome
        qtbot.wait(20)
        assert window.canvas_view.command_manager.undo_depth == 0
        assert not any(getattr(item, "item_id", None) for item in window.canvas_scene.items())
    finally:
        server.stop()


def test_custom_layer_and_constraint_rows_expose_names_and_ids_via_mcp(ui_window, qtbot):  # noqa: F811
    from open_garden_planner.core.constraints import AnchorRef, ConstraintType
    from open_garden_planner.core.measure_snapper import AnchorType
    from open_garden_planner.ui.canvas.items import RectangleItem

    window, _ = ui_window
    scene = window.canvas_scene
    item = RectangleItem(300, 300, 200, 100, layer_id=scene.active_layer.id, name="Rectangle synthétique")
    scene.addItem(item)
    constraint = scene.constraint_graph.add_constraint(
        AnchorRef(item.item_id, AnchorType.CORNER, 0),
        AnchorRef(item.item_id, AnchorType.CORNER, 1), 200,
        constraint_type=ConstraintType.EDGE_LENGTH,
    )
    window.constraints_panel.refresh()

    async def body(d):
        layer = await d.call("create_layer", name="Calque synthétique")
        state = await d.state(hidden=True)
        by_id = {n["id"]: n for n in state["nodes"]}
        models = {}
        for target in state["nodes"]:
            if target["class"] != "QListWidget":
                continue
            parent = target.get("parent_id")
            while parent in by_id:
                cls = by_id[parent]["class"]
                if cls in {"LayersPanel", "ConstraintsPanel"}:
                    models[cls] = await d.call("read_ui_model", widget_id=target["id"])
                    break
                parent = by_id[parent].get("parent_id")
        assert set(models) == {"LayersPanel", "ConstraintsPanel"}
        for model in models.values():
            assert all(row["cells"][0]["text"] and row["cells"][0]["user_data"]
                       for row in model["rows"])
        assert str(constraint.constraint_id) in str(models["ConstraintsPanel"])
        assert layer["layer_id"] in str(models["LayersPanel"])
    run_cycle(window, qtbot, body)


def test_propagation_chart_override_is_discoverable_and_persisted(ui_window, qtbot, tmp_path):  # noqa: F811
    window, _ = ui_window
    path = tmp_path / "propagation.ogp"

    async def body(d):
        await d.call("create_object", object_type="PERENNIAL", x=400, y=300, species="Tomato", name="Tomate")
        form, receipt = await d.open_action("Set Garden Location")
        await d.field(form, "Last spring frost:", "04-15", "QLineEdit")
        await d.field(form, "First fall frost:", "10-15", "QLineEdit")
        await d.button(form, "OK")
        await d.wait(receipt)
        await d.tab("Planting Calendar")
        state = await d.state()
        toggle = d.one(state, **{"class": "QCheckBox", "text": "Show propagation steps"})
        await d.run("set_ui_value", widget_id=toggle["id"], value=True)
        state = await d.state()
        chart = d.one(state, **{"class": "_GanttWidget"})
        rows = chart["custom_state"]["rows"]
        assert len(rows) == 1 and rows[0]["species_key"] and rows[0]["name"]
        bounds = rows[0]["bounds"]
        await d.run("interact_ui", widget_id=chart["id"], action="click", parameters={"x": bounds["x"] + 20, "y": bounds["y"] + 10})
        state = await d.state()
        field = d.one(state, **{"class": "QDateEdit", "object_name": "propagation_prick_out_start"})
        # Keep the date in the calculated sequence to exercise the real editor's validation.
        chosen = field["value"]
        from datetime import date, timedelta
        chosen = (date.fromisoformat(chosen) + timedelta(days=1)).isoformat()
        await d.run("set_ui_value", widget_id=field["id"], value=chosen)
        for _ in range(100):
            raw = await d.raw()
            if raw.get("propagation_overrides"):
                break
            await anyio.sleep(0.01)  # The existing editor coalesces edits for 600 ms.
        assert raw["propagation_overrides"][rows[0]["species_key"]]["prick_out"]["start"] == chosen
        await d.call("save_plan", file_path=str(path))
        await d.call("open_plan", file_path=str(path))
        assert (await d.raw())["propagation_overrides"] == raw["propagation_overrides"]
        tasks = await d.call("get_tasks", from_date=chosen[:4] + "-01-01", to_date=chosen[:4] + "-12-31")
        assert chosen in str(tasks)
    run_cycle(window, qtbot, body)


def test_season_creation_and_switch_via_completed_qt_dialogs(ui_window, qtbot, tmp_path, monkeypatch):  # noqa: F811
    window, _ = ui_window
    monkeypatch.setattr(QMessageBox, "question", _QUESTION)
    original = tmp_path / "saison_2026.ogp"
    next_season = tmp_path / "saison_2027.ogp"

    async def body(d):
        plant = await d.call("create_object", object_type="TREE", x=400, y=300, name="Arbre synthétique")
        await d.call("save_plan", file_path=str(tmp_path / "base.ogp"))
        for season_year, season_path in ((2026, original), (2027, next_season)):
            form, receipt = await d.open_action("Manage Seasons")
            year = d.one(form, **{"class": "QSpinBox"})
            await d.run("set_ui_value", widget_id=year["id"], value=season_year)
            keep = d.one(form, **{"class": "QRadioButton", "text": "Keep all plants (carry over — for perennials/trees)"})
            await d.run("set_ui_value", widget_id=keep["id"], value=True)
            create_receipt = await d.button(form, "Save as New Season…", wait=False)
            save_receipt = await d.file_dialog(season_path, "Save")
            await d.wait(save_receipt)
            await d.wait(create_receipt)
            confirmation = await d.modal()
            assert any(n["class"] == "QMessageBox" for n in confirmation["nodes"])
            await d.button(confirmation, "&No")
            await d.wait(receipt)
            await d.call("save_plan")
            await d.call("open_plan", file_path=str(season_path))
            if season_year == 2026:
                await d.call("add_garden_intervention", target_ids=[plant["item_id"]], event_type="observation", description="Observation conservée entre saisons", occurrence=None, request_key="perennial-season")
        raw = await d.raw()
        assert raw["season_year"] == 2027
        assert plant["item_id"] in str(raw["objects"])
        form, receipt = await d.open_action("Manage Seasons")
        view = d.one(form, **{"class": "QListWidget"})
        data = await d.call("read_ui_model", widget_id=view["id"])
        matches = [r for r in data["rows"] if "2026" in r["cells"][0]["text"]]
        assert len(matches) == 1, data
        row = matches[0]
        assert row["cells"][0]["user_data"]["resolved"] == str(original)
        await d.run("interact_ui", widget_id=view["id"], action="select", parameters={"index_path": row["index_path"]})
        await d.button(await d.modal(), "Open Selected Season")
        await d.wait(receipt)
        assert (await d.call("get_plan_summary"))["file_name"] == original.name
    run_cycle(window, qtbot, body)


def test_complete_culture_observation_intervention_harvest_task_reopen(ui_window, qtbot, tmp_path, monkeypatch):  # noqa: F811
    window, _ = ui_window
    monkeypatch.setattr(QMessageBox, "question", _QUESTION)
    target = tmp_path / "cycle-synthetique.ogp"

    async def body(d):
        await d.call("new_plan", width_cm=1200, height_cm=800)
        bed = (await d.call("create_object", object_type="GARDEN_BED", x=500, y=400, width=800, height=600, name="Massif synthétique"))["item_id"]
        plant = (await d.call("create_object", object_type="PERENNIAL", x=400, y=300, species="Tomato", name="Tomate synthétique"))["item_id"]
        plant2 = (await d.call("create_object", object_type="PERENNIAL", x=600, y=300, species="Tomato", name="Tomate témoin"))["item_id"]
        assert (await d.call("get_garden_record", item_id=plant))["card"]["period"]["precision"] == "unknown"
        for identity in (plant, plant2):
            await d.call("set_garden_card", item_id=identity, fields={"kind": "individual", "state": "planned", "zone_id": bed, "period": None})
        assert len(await d.call("list_zone_plantings", zone_id=bed)) == 2
        await d.call("set_planting_date", item_id=plant, planting_date="2026-05")
        assert (await d.call("get_garden_record", item_id=plant))["card"]["period"]["precision"] == "month"
        await d.call("set_planting_date", item_id=plant, planting_date=None)
        assert (await d.call("get_garden_record", item_id=plant))["card"]["period"]["precision"] == "unknown"
        await d.call("set_planting_date", item_id=plant, planting_date="2026-05-18")
        planned = await d.call("add_garden_intervention", target_ids=[plant], event_type="sowing", description="Semis prévu", status="planned", occurrence="2026-03", request_key="sow-plan")
        sow_args = {"target_ids": [plant, plant2], "event_type": "sowing", "description": "Semis réalisé synthétique", "status": "done", "occurrence": "2026-03-15", "request_key": "sow-done"}
        sow = await d.call("add_garden_intervention", **sow_args)
        assert (await d.call("add_garden_intervention", **sow_args))["event_id"] == sow["event_id"]
        assert sow["event_id"] != planned["event_id"]
        observation = await d.call("add_garden_intervention", target_ids=[plant], event_type="observation", description="Germination observée", occurrence=None, request_key="germinated")
        await d.call("edit_garden_intervention", event_id=observation["event_id"], fields={"description": "Germination corrigée", "period": "2026-03-22"})
        await d.call("add_garden_intervention", target_ids=[plant], event_type="planting", description="Repiquage réalisé", occurrence="2026-04-12", request_key="prick-out")
        await d.call("add_garden_intervention", target_ids=[plant, bed], event_type="planting", description="Plantation au massif", occurrence="2026-05-18", request_key="planting")
        water = await d.call("add_garden_intervention", target_ids=[plant, plant2], event_type="watering", description="Arrosage", occurrence="2026-05-19", details={"quantity": None, "unit": "L"}, request_key="water")
        await d.call("edit_garden_intervention", event_id=water["event_id"], fields={"quantity": 0})
        await d.call("undo")
        events = (await d.call("get_garden_record", item_id=plant))["direct_events"]
        assert next(e for e in events if e["id"] == water["event_id"])["quantity"] is None
        await d.call("redo")
        # A real context menu and completed harvest dialog; no test-side signal.
        form, receipts = await d.context_dialog(plant, 400, 300, "Log Harvest")
        await d.field(form, "Date", "2026-08-20", "QDateEdit")
        await d.field(form, "Quantity", 2.5, "QDoubleSpinBox")
        await d.field(form, "Unit", "kg", "QComboBox")
        await d.button(form, "OK")
        for receipt in receipts:
            await d.wait(receipt)
        raw = await d.raw()
        harvest = raw["harvest_logs"][plant]["records"][0]
        assert harvest["date"] == "2026-08-20" and harvest["quantity"] == 2.5
        assert harvest["journal_note_id"] in raw["garden_journal_notes"]
        await d.call("undo")
        assert plant not in (await d.raw()).get("harvest_logs", {})
        await d.call("redo")
        form, receipts = await d.context_dialog(plant, 400, 300, "Log Harvest")
        tabs = d.one(form, **{"class": "QTabWidget"})
        await d.run("set_ui_value", widget_id=tabs["id"], value="History")
        d.one(await d.modal(), record={"kind": "harvest", "id": harvest["id"], "target_id": plant})
        edit_receipt = await d.button(await d.modal(), "Edit", wait=False)
        edit_form = await d.modal()
        # The parent dialog remains visible too: wait for the edit form itself.
        for _ in range(100):
            if any(n["title"] == "Edit Harvest Entry" for n in edit_form["nodes"]):
                break
            await anyio.sleep(0.01)
            edit_form = await d.modal()
        await d.field(edit_form, "Quantity", 3, "QDoubleSpinBox")
        await d.field(edit_form, "Unit", "basket", "QComboBox")
        await d.button(edit_form, "OK")
        await d.wait(edit_receipt)
        edited = (await d.raw())["harvest_logs"][plant]["records"][0]
        assert edited["id"] == harvest["id"] and edited["unit"] == "basket" and edited["quantity"] == 3
        delete_receipt = await d.button(await d.modal(), "Delete", wait=False)
        confirmation = await d.modal()
        for _ in range(100):
            if any(n["class"] == "QMessageBox" for n in confirmation["nodes"]):
                break
            await anyio.sleep(0.01)
            confirmation = await d.modal()
        await d.button(confirmation, "&Yes")
        await d.wait(delete_receipt)
        assert plant not in (await d.raw()).get("harvest_logs", {})
        # Close the history dialog without adding an entry, then undo deletion
        # and edit: linked journal note and quantity return together.
        await d.button(await d.modal(), "Cancel")
        for receipt in receipts:
            await d.wait(receipt)
        await d.call("undo")
        assert (await d.raw())["harvest_logs"][plant]["records"][0] == edited
        await d.call("undo")
        assert (await d.raw())["harvest_logs"][plant]["records"][0] == harvest
        await d.tab("Harvest")
        table = d.one(await d.state(), **{"class": "QTableWidget"})
        totals = await d.call("read_ui_model", widget_id=table["id"])
        assert totals["headers"] == ["Species", "Year", "Total", "Unit", "Entries"]
        assert totals["rows"][0]["cells"][2]["text"] == "2.5"
        task = await d.call("add_manual_task", title="Contrôler les tuteurs synthétiques", date="2026-05-20", bed_id=bed)
        await d.call("edit_manual_task", task_id=task["task_id"], title="Contrôler les tuteurs corrigés", date="2026-05-21", bed_id=bed)
        await d.tab("Tasks")
        state = await d.state()
        labels = [n for n in state["nodes"] if "Contrôler les tuteurs corrigés" in n.get("text", "")]
        assert len(labels) == 1
        row = await d.state(labels[0]["parent_id"])
        await d.button(row, "Done")
        tasks = await d.call("get_tasks", today="2026-05-21")
        assert "done" in str(tasks) and task["task_id"] in str(tasks)
        await d.call("save_plan", file_path=str(target))
        await d.call("new_plan")
        await d.call("open_plan", file_path=str(target))
        raw = await d.raw()
        assert raw["harvest_logs"][plant]["records"][0] == harvest
        assert next(e for e in raw["garden_memory"]["events"].values() if e["id"] == water["event_id"])["quantity"] == 0
        assert (await d.call("get_garden_record", item_id=plant))["card"]["period"]["value"] == "2026-05-18"
        assert (await d.call("add_garden_intervention", **sow_args))["replayed"]
        assert task["task_id"] in str(await d.call("get_tasks", today="2026-05-21"))
        await d.call("delete_manual_task", task_id=task["task_id"])
        await d.call("undo")
        assert task["task_id"] in str(await d.call("get_tasks", today="2026-05-21"))
        # Rename/delete a target without losing historical names or receipts.
        await d.call("set_garden_card", item_id=plant2, fields={"name": "Témoin renommé", "zone_id": bed})
        await d.call("delete_object", item_id=plant2)
        events = (await d.raw())["garden_memory"]["events"]
        assert next(t for t in events[sow["event_id"]]["targets"] if t["id"] == plant2)["name"] == "Tomate témoin"
        assert (await d.call("add_garden_intervention", **sow_args))["replayed"]
        await d.call("save_plan")
        await d.call("open_plan", file_path=str(target))
        assert len((await d.raw())["garden_memory"]["events"]) == 6
    run_cycle(window, qtbot, body)
