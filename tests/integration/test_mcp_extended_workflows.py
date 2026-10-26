"""Previously unverified desktop journeys, using only MCP for client actions."""

import anyio
from PyQt6.QtWidgets import QMessageBox

from tests.integration.test_agent_ui_control import ui_window  # noqa: F401
from tests.integration.test_mcp_horticultural_cycle import _QUESTION, run_cycle


async def modal_title(d, title):
    for _ in range(100):
        form = await d.modal()
        if any(n["title"] == title for n in form["nodes"]):
            return form
        await anyio.sleep(0.01)
    raise AssertionError((title, form))


async def confirmation(d):
    for _ in range(100):
        form = await d.modal()
        if any(n["class"] == "QMessageBox" for n in form["nodes"]):
            return form
        await anyio.sleep(0.01)
    raise AssertionError(form)


async def task_row(d, identity, button):
    for _ in range(150):
        state = await d.state()
        rows = [n for n in state["nodes"] if n.get("record", {}).get("id") == identity]
        if len(rows) == 1:
            # The dashboard rebuilds asynchronously. A handle can expire
            # between discovery and subtree read; rediscover that same UUID.
            result = await d.client.call_tool("get_application_ui", {"root_id": rows[0]["id"], "max_nodes": 10000})
            if result.isError:
                assert "stale" in str(result.content), result.content
                continue
            row = result.structuredContent
            if any(n.get("text") == button for n in row["nodes"]):
                return row
        await anyio.sleep(0.02)
    raise AssertionError((identity, button, rows))


def test_custom_library_create_assign_edit_delete_over_mcp(ui_window, qtbot, tmp_path, monkeypatch):  # noqa: F811
    window, _ = ui_window
    monkeypatch.setattr(QMessageBox, "question", _QUESTION)

    async def body(d):
        form, receipt = await d.open_action("Manage Custom Plants")
        await d.button(form, "Create New")
        table = d.one(await d.modal(), **{"class": "QTableWidget"})
        model = await d.call("read_ui_model", widget_id=table["id"])
        assert model["headers"] == ["Common Name", "Scientific Name", "Family", "Cycle", "ID"]
        identity = model["rows"][0]["cells"][4]["text"]
        await d.button(await d.modal(), "OK")
        await d.wait(receipt)
        plant = (await d.call("create_object", object_type="PERENNIAL", x=400, y=300))["item_id"]
        canvas = d.one(await d.state(), **{"class": "CanvasView"})
        await d.run("interact_ui", widget_id=canvas["id"], action="select_scene", parameters={"item_ids": [plant]})
        panel = d.one(await d.state(), **{"class": "PlantDatabasePanel"})
        load = await d.button(await d.state(panel["id"]), "Load", wait=False)
        form = await d.modal()
        table = d.one(form, **{"class": "QTableWidget"})
        await d.run("interact_ui", widget_id=table["id"], action="select", parameters={"index_path": [0]})
        await d.button(form, "OK")
        await d.wait(load)
        await d.field(await d.state(panel["id"]), "Common Name:", "Espèce personnelle synthétique", "QLineEdit")
        raw = await reopen(d, tmp_path / "custom.ogp")
        species = next(o for o in raw["objects"] if o.get("item_id") == plant)["metadata"]["plant_species"]
        assert species["source_id"] == identity and species["common_name"] == "Espèce personnelle synthétique"
        form, receipt = await d.open_action("Manage Custom Plants")
        table = d.one(form, **{"class": "QTableWidget"})
        model = await d.call("read_ui_model", widget_id=table["id"])
        assert model["rows"][0]["cells"][0]["text"] == species["common_name"]
        await d.run("interact_ui", widget_id=table["id"], action="select", parameters={"index_path": [0]})
        delete = await d.button(form, "Delete", wait=False)
        await d.button(await confirmation(d), "&Yes")
        await d.wait(delete)
        assert (await d.call("read_ui_model", widget_id=table["id"]))["row_count"] == 0
        await d.button(await d.modal(), "OK")
        await d.wait(receipt)
        assert next(o for o in (await reopen(d, tmp_path / "custom.ogp"))["objects"]
                    if o.get("item_id") == plant)["metadata"]["plant_species"] == species

    run_cycle(window, qtbot, body)


def test_duplicate_task_titles_have_stable_rows_and_status_roundtrip(ui_window, qtbot, tmp_path):  # noqa: F811
    from datetime import date, timedelta

    window, _ = ui_window

    async def body(d):
        first = await d.call("add_manual_task", title="Tâche identique")
        second = await d.call("add_manual_task", title="Tâche identique")
        await d.tab("Tasks")
        row = await task_row(d, second["task_id"], "Done")
        await d.button(row, "Done")
        raw = await d.raw()
        assert raw["task_states"][second["task_id"]]["status"] == "done"
        assert first["task_id"] not in raw["task_states"]
        await d.button(await task_row(d, second["task_id"], "Reopen"), "Reopen")
        assert second["task_id"] not in (await d.raw()).get("task_states", {})
        menu = await d.button(await task_row(d, second["task_id"], "Snooze"), "Snooze", wait=False)
        for _ in range(100):
            state = await d.state()
            if state["popup_id"]:
                break
            await anyio.sleep(0.01)
        action = next(a for a in state["actions"] if a["text"] == "1 week")
        await d.run("invoke_ui_action", action_id=action["id"])
        await d.wait(menu)
        until = (date.today() + timedelta(days=7)).isoformat()
        assert (await d.raw())["task_states"][second["task_id"]]["snooze_until"] == until
        raw = await reopen(d, tmp_path / "tasks.ogp")
        assert raw["task_states"][second["task_id"]]["snooze_until"] == until
        await d.tab("Tasks")
        await d.button(await task_row(d, second["task_id"], "Un-snooze"), "Un-snooze")
        assert second["task_id"] not in (await d.raw()).get("task_states", {})

    run_cycle(window, qtbot, body)


async def reopen(d, path):
    await d.call("save_plan", file_path=str(path))
    await d.call("new_plan")
    await d.call("open_plan", file_path=str(path))
    return await d.raw()


def test_canvas_dimensions_north_and_undo_roundtrip_over_mcp(ui_window, qtbot, tmp_path):  # noqa: F811
    window, _ = ui_window
    path = tmp_path / "orientation.ogp"

    async def body(d):
        await d.call("save_plan", file_path=str(path))
        before = await d.call("get_plan_summary")
        form, receipt = await d.open_action("Canvas Size")
        await d.field(form, "Width:", 37.5, "QDoubleSpinBox")
        await d.field(form, "Height:", 22, "QDoubleSpinBox")
        await d.button(form, "OK")
        await d.wait(receipt)
        state = await d.call("get_plan_summary")
        assert state["canvas_width_cm"] == 3750 and state["canvas_height_cm"] == 2200
        assert state["is_dirty"], "Resizing a saved plan must not silently remain clean"
        assert (await d.call("get_history"))["undo_depth"] == 1
        await d.call("undo")
        state = await d.call("get_plan_summary")
        assert state["canvas_width_cm"] == before["canvas_width_cm"]
        assert state["canvas_height_cm"] == before["canvas_height_cm"]
        await d.call("redo")
        await d.call("save_plan")
        history = await d.call("get_history")
        form, receipt = await d.open_action("Canvas Size")
        await d.button(form, "OK")  # Accepting unchanged dimensions is a no-op.
        await d.wait(receipt)
        assert await d.call("get_history") == history
        assert not (await d.call("get_plan_summary"))["is_dirty"]
        form, receipt = await d.open_action("Set North Orientation")
        await d.field(form, "Clockwise angle from the top:", 135, "QDoubleSpinBox")
        await d.button(form, "OK")
        await d.wait(receipt)
        assert (await d.raw())["north_angle_deg"] == 135
        await d.call("undo")
        assert (await d.raw()).get("north_angle_deg", 0) == 0
        await d.call("redo")
        raw = await reopen(d, path)
        assert raw["canvas"]["width"] == 3750 and raw["canvas"]["height"] == 2200
        assert raw["north_angle_deg"] == 135

    run_cycle(window, qtbot, body)


def test_pest_disease_photo_crud_roundtrip_over_mcp(ui_window, qtbot, tmp_path, monkeypatch):  # noqa: F811
    from PIL import Image

    window, _ = ui_window
    monkeypatch.setattr(QMessageBox, "question", _QUESTION)
    photo = tmp_path / "synthetic.png"
    Image.new("RGB", (20, 20), "green").save(photo)
    path = tmp_path / "pest.ogp"

    async def body(d):
        plant = (await d.call("create_object", object_type="PERENNIAL", x=400, y=300,
                              species="Tomato", name="Tomate synthétique"))["item_id"]
        await d.call("save_plan", file_path=str(path))
        form, receipts = await d.context_dialog(plant, 400, 300, "Log Pest/Disease")
        await d.field(form, "Date", "2026-06-10", "QDateEdit")
        await d.field(form, "Type", "Disease", "QComboBox")
        await d.field(form, "Name", "Diagnostic synthétique", "QLineEdit")
        await d.field(form, "Severity", "Medium", "QComboBox")
        await d.field(form, "Treatment", "Surveillance synthétique", "QLineEdit")
        await d.field(form, "Notes", "Observation sans diagnostic inféré", "QPlainTextEdit")
        attach = await d.button(form, "Attach Photo…", wait=False)
        await d.wait(await d.file_dialog(photo, "Open"))
        await d.wait(attach)
        await d.button(await d.modal(), "OK")
        for receipt in receipts:
            await d.wait(receipt)
        record = (await d.raw())["pest_disease_logs"][plant]["records"][0]
        assert record["entry_type"] == "disease" and record["severity"] == "medium"
        assert record["notes"] == "Observation sans diagnostic inféré"
        assert (tmp_path / record["photo_path"]).read_bytes() == photo.read_bytes()
        await d.call("undo")
        assert plant not in (await d.raw()).get("pest_disease_logs", {})
        await d.call("redo")
        assert (await reopen(d, path))["pest_disease_logs"][plant]["records"][0] == record
        form, receipts = await d.context_dialog(plant, 400, 300, "Log Pest/Disease")
        await d.run("set_ui_value", widget_id=d.one(form, **{"class": "QTabWidget"})["id"], value="History")
        history = await d.modal()
        row = d.one(history, record={"kind": "pest_disease", "id": record["id"], "target_id": plant})
        edit = await d.button(await d.state(row["id"]), "Edit", wait=False)
        form = await modal_title(d, "Edit Pest/Disease Log")
        await d.field(form, "Name", "Diagnostic corrigé", "QLineEdit")
        resolved = d.one(form, **{"class": "QCheckBox", "text": "Resolved"})
        await d.run("set_ui_value", widget_id=resolved["id"], value=True)
        await d.button(form, "Remove Photo")
        await d.button(form, "OK")
        await d.wait(edit)
        edited = (await d.raw())["pest_disease_logs"][plant]["records"][0]
        assert edited["id"] == record["id"] and edited["resolved"] and edited.get("photo_path") is None
        delete = await d.button(await d.modal(), "Delete", wait=False)
        await d.button(await confirmation(d), "&Yes")
        await d.wait(delete)
        assert not (await d.raw())["pest_disease_logs"][plant]["records"]
        await d.button(await d.modal(), "Cancel")
        for receipt in receipts:
            await d.wait(receipt)
        await d.call("undo")
        assert (await d.raw())["pest_disease_logs"][plant]["records"][0] == edited
        await d.call("undo")
        assert (await reopen(d, path))["pest_disease_logs"][plant]["records"][0] == record

    run_cycle(window, qtbot, body)


def test_link_seed_packet_and_global_preferences_over_mcp(ui_window, qtbot, tmp_path):  # noqa: F811
    window, _ = ui_window
    path = tmp_path / "seed-link.ogp"

    async def body(d):
        await d.tab("Seed Inventory")
        add = await d.button(await d.state(), "+ Add Packet", wait=False)
        form = await d.modal()
        await d.field(form, "Species name:", "Tomato", "QLineEdit")
        await d.field(form, "Variety / cultivar:", "Synthétique", "QLineEdit")
        await d.field(form, "Quantity:", 20, "QDoubleSpinBox")
        await d.button(form, "OK")
        await d.wait(add)
        table = d.one(await d.state(), **{"class": "QTableView"})
        packet = (await d.call("read_ui_model", widget_id=table["id"]))["rows"][0]["cells"][0]["user_data"]
        await d.tab("Garden Plan")
        plant = (await d.call("create_object", object_type="PERENNIAL", x=400, y=300,
                              species="Tomato", name="Tomate liée"))["item_id"]
        canvas = d.one(await d.state(), **{"class": "CanvasView"})
        await d.run("interact_ui", widget_id=canvas["id"], action="select_scene", parameters={"item_ids": [plant]})
        state = await d.state(hidden=True)
        link = d.one(state, **{"class": "QComboBox", "object_name": "seed_packet_id"})
        # Expand the actual panel through its header, if collapsed.
        if not link["visible"]:
            header = d.one(await d.state(), **{"class": "QToolButton", "text": "Plant Details"})
            await d.run("interact_ui", widget_id=header["id"], action="click")
            link = d.one(await d.state(), **{"class": "QComboBox", "object_name": "seed_packet_id"})
        choice = next(c for c in link["choices"] if c["user_data"] == packet)
        await d.run("set_ui_value", widget_id=link["id"], value={"index": choice["index"]})

        def instance(raw):
            return next(o for o in raw["objects"] if o.get("item_id") == plant)["metadata"]["plant_instance"]

        assert instance(await d.raw())["seed_packet_id"] == packet
        await d.call("undo")
        assert instance(await d.raw()).get("seed_packet_id") is None
        await d.call("redo")
        assert instance(await reopen(d, path))["seed_packet_id"] == packet
        form, receipt = await d.open_action("Preferences")
        await d.field(form, "Orange warning threshold (°C):", -2.5, "QDoubleSpinBox")
        await d.field(form, "Red alert threshold (°C):", -5, "QDoubleSpinBox")
        await d.button(form, "Save")
        await d.wait(receipt)
        form, receipt = await d.open_action("Preferences")
        assert d.one(form, **{"label": "Orange warning threshold (°C):", "class": "QDoubleSpinBox"})["value"] == -2.5
        assert d.one(form, **{"label": "Red alert threshold (°C):", "class": "QDoubleSpinBox"})["value"] == -5
        await d.button(form, "Cancel")
        await d.wait(receipt)

    run_cycle(window, qtbot, body)


def test_journal_photo_create_edit_delete_roundtrip_over_mcp(ui_window, qtbot, tmp_path, monkeypatch):  # noqa: F811
    from PIL import Image

    window, _ = ui_window
    monkeypatch.setattr(QMessageBox, "question", _QUESTION)
    photo = tmp_path / "synthetic.png"
    Image.new("RGB", (20, 20), "blue").save(photo)
    path = tmp_path / "journal.ogp"

    async def body(d):
        await d.call("save_plan", file_path=str(path))
        state = await d.state()
        tool = d.one(state, **{"class": "QToolButton", "tooltip": "Drop a garden-journal note pin (J)"})
        await d.run("interact_ui", widget_id=tool["id"], action="click")
        canvas = d.one(await d.state(), **{"class": "CanvasView"})
        placement = await d.call("interact_ui", widget_id=canvas["id"], action="click",
                                 parameters={"x": 400, "y": 300, "coordinate_space": "scene"}, request_key="place-note")
        form = await d.modal()
        await d.field(form, "Date", "2026-06-01", "QDateEdit")
        await d.field(form, "Note", "Note synthétique\nMétéo observée", "QPlainTextEdit")
        attach = await d.button(form, "Attach Photo…", wait=False)
        await d.wait(await d.file_dialog(photo, "Open"))
        await d.wait(attach)
        await d.button(await d.modal(), "OK")
        await d.wait(placement)
        notes = (await d.raw())["garden_journal_notes"]
        assert len(notes) == 1
        identity, note = next(iter(notes.items()))
        assert note["date"] == "2026-06-01" and note["text"] == "Note synthétique\nMétéo observée"
        assert (tmp_path / note["photo_path"]).read_bytes() == photo.read_bytes()
        await d.call("undo")
        assert not (await d.raw()).get("garden_journal_notes")
        await d.call("redo")
        assert (await reopen(d, path))["garden_journal_notes"][identity] == note
        # Escape switches back to selection. A real double click edits the pin.
        canvas = d.one(await d.state(), **{"class": "CanvasView"})
        await d.run("interact_ui", widget_id=canvas["id"], action="key_sequence", parameters={"key": "Escape"})
        # Pins ignore the view transform: their body is 12 screen pixels above
        # the saved scene anchor (the tip is on the shape's boundary).
        a, b, c, e, tx, ty = canvas["scene_to_viewport"]
        edit = await d.call("interact_ui", widget_id=canvas["id"], action="double_click",
                            parameters={"x": a * note["scene_x"] + c * note["scene_y"] + tx,
                                        "y": b * note["scene_x"] + e * note["scene_y"] + ty - 12}, request_key="edit-note")
        form = await modal_title(d, "Edit Journal Note")
        await d.field(form, "Note", "Note corrigée", "QPlainTextEdit")
        await d.button(form, "Remove Photo")
        await d.button(form, "OK")
        await d.wait(edit)
        edited = (await d.raw())["garden_journal_notes"][identity]
        assert edited["text"] == "Note corrigée" and edited.get("photo_path") is None
        # Delete via the selected pin and normal canvas shortcut.
        pin = next(o for o in (await d.raw())["objects"] if o["type"] == "journal_pin")
        await d.run("interact_ui", widget_id=canvas["id"], action="select_scene", parameters={"item_ids": [pin["item_id"]]})
        delete = await d.call("interact_ui", widget_id=canvas["id"], action="key_sequence",
                              parameters={"key": "Delete"}, request_key="delete-note")
        await d.wait(delete)
        assert not (await d.raw()).get("garden_journal_notes")
        await d.call("undo")
        assert (await d.raw())["garden_journal_notes"][identity] == edited
        await d.call("undo")
        assert (await reopen(d, path))["garden_journal_notes"][identity] == note

    run_cycle(window, qtbot, body)
