"""Whole desktop control through real Qt controls, modal loops and MCP receipts."""

from __future__ import annotations

import threading
import time
import traceback
from uuid import uuid4

import pytest
from PyQt6 import sip
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QLineEdit,
    QMenu,
    QMessageBox,
    QSpinBox,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from open_garden_planner.agent_api.bridge import MainThreadBridge
from open_garden_planner.app.application import GardenPlannerApp
from open_garden_planner.core.object_types import ObjectType
from open_garden_planner.ui.agent_control import ApplicationUiController
from open_garden_planner.ui.canvas.items import RectangleItem


def snapshot(controller, **kwargs):
    return controller.dispatch("snapshot", kwargs)


def queue(controller, kind, **kwargs):
    return controller.dispatch(kind, {"request_key": str(uuid4()), **kwargs})


def complete(controller, qtbot, receipt):
    qtbot.waitUntil(lambda: controller.dispatch("status", {
        "operation_id": receipt["operation_id"],
    })["status"] in {"completed", "failed"}, timeout=10000)
    status = controller.dispatch("status", {"operation_id": receipt["operation_id"]})
    assert status["status"] == "completed", status.get("error", status)
    return status


def drive_ui(controller, qtbot, body):
    bridge = MainThreadBridge()
    outcome = {}

    def worker():
        def call(kind, **arguments):
            return bridge.run_on_main(lambda: controller.dispatch(kind, arguments))

        def wait(predicate):
            deadline = time.monotonic() + 8
            while not predicate():
                if time.monotonic() > deadline:
                    raise AssertionError("UI condition timed out")
                time.sleep(0.01)

        def run(kind, **arguments):
            receipt = call(kind, request_key=str(uuid4()), **arguments)
            wait(lambda: call("status", operation_id=receipt["operation_id"])["status"] in {"completed", "failed"})
            status = call("status", operation_id=receipt["operation_id"])
            assert status["status"] == "completed", status.get("error", status)
            return status

        try:
            body(call, wait, run)
        except BaseException:
            outcome["error"] = traceback.format_exc()
        finally:
            outcome["done"] = True
            # Close a failed test's nested loops so the test runner can unwind.
            if "error" in outcome:
                bridge.run_on_main(lambda: [w.close() for w in QApplication.topLevelWidgets()
                                           if isinstance(w, (QDialog, QMenu))])

    thread = threading.Thread(target=worker, daemon=True)
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(lambda: [w.close() for w in QApplication.topLevelWidgets()
                                      if isinstance(w, (QDialog, QMenu))])
    watchdog.start(12000)
    thread.start()
    qtbot.waitUntil(lambda: outcome.get("done", False), timeout=15000)
    watchdog.stop()
    assert "error" not in outcome, outcome.get("error")


def node(state, **values):
    matches = [n for n in state["nodes"] if all(n.get(k) == v for k, v in values.items())]
    assert len(matches) == 1, (values, [(n["class"], n.get("text"), n.get("value")) for n in matches])
    return matches[0]


def action(state, text):
    matches = [a for a in state["actions"] if a["text"].replace("&", "") == text]
    assert len(matches) == 1, (text, [a["text"] for a in state["actions"]])
    return matches[0]


@pytest.fixture
def ui_window(qtbot, monkeypatch):
    monkeypatch.setattr(GardenPlannerApp, "_startup_sequence", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_start_update_check", lambda _self: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Discard)
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    window.resize(1400, 900)
    window.show()
    window.activateWindow()
    qtbot.wait(20)
    controller = ApplicationUiController(window)
    try:
        yield window, controller
    finally:
        controller.close()
        window._project_manager.mark_clean()
        window._stop_agent_api()
        window.close()


def test_modal_action_remains_pilotable_and_retries_do_not_reopen(qtbot):
    owner = QWidget()
    qtbot.addWidget(owner)
    owner.show()
    dialog = QDialog(owner)
    layout = QFormLayout(dialog)
    name = QLineEdit()
    name.setObjectName("name")
    count = QSpinBox()
    count.setRange(1, 20)
    choice = QComboBox()
    choice.addItems(["First", "Second"])
    layout.addRow("Name", name)
    layout.addRow("Count", count)
    layout.addRow("Choice", choice)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    layout.addRow(buttons)
    buttons.accepted.connect(lambda: dialog.accept() if name.text() else None)
    buttons.rejected.connect(dialog.reject)
    opens = []
    edit = QAction("Edit modal", owner)
    edit.triggered.connect(lambda: (opens.append(True), dialog.exec()))
    owner.addAction(edit)
    controller = ApplicationUiController(owner)
    try:
        def body(call, wait, run):
            state = call("snapshot")
            args = {"action_id": action(state, "Edit modal")["id"], "request_key": "open-once"}
            receipt = call("action", **args)
            wait(lambda: call("snapshot")["modal_id"] is not None)
            assert call("status", operation_id=receipt["operation_id"])["status"] == "running"
            assert call("action", **args)["replayed"]
            state = call("snapshot")
            run("interact", widget_id=state["modal_id"], action="accept")
            assert call("snapshot")["modal_id"] == state["modal_id"]  # Button validation kept the empty form open.
            denied = call("action", action_id=args["action_id"], request_key="blocked")
            wait(lambda: call("status", operation_id=denied["operation_id"])["status"] == "failed")
            for cls, value in (("QLineEdit", "Érable du jardin 🌱"), ("QSpinBox", 7), ("QComboBox", "Second")):
                match = {"class": cls}
                if cls == "QLineEdit":
                    match["object_name"] = "name"
                field = node(call("snapshot", root_id=state["modal_id"]), **match)
                run("value", widget_id=field["id"], value=value)
            ok = node(call("snapshot", root_id=state["modal_id"]), **{"class": "QPushButton", "text": "OK"})
            run("interact", widget_id=ok["id"], action="click")
            wait(lambda: call("status", operation_id=receipt["operation_id"])["status"] == "completed")
            assert call("action", **args)["status"] == "completed"
            try:
                call("action", **{**args, "action_id": "another-handle"})
            except ValueError as exc:
                assert "conflict" in str(exc)
            else:
                raise AssertionError("Expected idempotence conflict")
        drive_ui(controller, qtbot, body)
        assert (name.text(), count.value(), choice.currentText()) == ("Érable du jardin 🌱", 7, "Second")
        assert opens == [True]
    finally:
        dialog.reject()
        controller.close()


def test_context_menu_exec_returns_the_chosen_action(qtbot):
    owner = QWidget()
    qtbot.addWidget(owner)
    owner.show()
    menu = QMenu(owner)
    chosen = menu.addAction("Choose this")
    results = []
    opener = QAction("Open context", owner)
    opener.triggered.connect(lambda: results.append(menu.exec(owner.mapToGlobal(owner.rect().center()))))
    owner.addAction(opener)
    controller = ApplicationUiController(owner)
    try:
        def body(call, wait, run):
            receipt = call("action", action_id=action(call("snapshot"), "Open context")["id"], request_key="context")
            wait(lambda: call("snapshot")["popup_id"] is not None)
            run("action", action_id=action(call("snapshot"), "Choose this")["id"])
            wait(lambda: call("status", operation_id=receipt["operation_id"])["status"] == "completed")
        drive_ui(controller, qtbot, body)
        assert results == [chosen]
    finally:
        menu.close()
        controller.close()


def test_real_properties_use_the_existing_undo_and_ogp_path(ui_window, qtbot, tmp_path):
    window, controller = ui_window
    scene, view = window.canvas_scene, window.canvas_view
    item = RectangleItem(100, 100, 200, 150, object_type=ObjectType.GARAGE_SHED,
                         name="Existing structure", layer_id=scene.active_layer.id)
    scene.addItem(item)
    state = snapshot(controller, max_nodes=10000)
    view_id = next(n["id"] for n in state["nodes"] if n["class"] == "CanvasView")
    complete(controller, qtbot, queue(controller, "interact", widget_id=view_id, action="select_scene",
                                      parameters={"item_ids": [str(item.item_id)]}))
    state = snapshot(controller, max_nodes=10000)
    header_label = node(state, **{"class": "QLabel", "text": "Properties", "visible": True})
    complete(controller, qtbot, queue(controller, "interact", widget_id=header_label["parent_id"], action="click"))
    qtbot.waitUntil(lambda: any(n.get("value") == "Existing structure" and n["visible"] for n in snapshot(controller, max_nodes=10000)["nodes"]))
    field = node(snapshot(controller, max_nodes=10000), **{"class": "QLineEdit", "value": "Existing structure", "visible": True})
    complete(controller, qtbot, queue(controller, "value", widget_id=field["id"], value="Abri rénové"))
    assert item.name == "Abri rénové"
    assert window._project_manager.is_dirty
    view.command_manager.undo()
    assert item.name == "Existing structure"
    view.command_manager.redo()
    assert item.name == "Abri rénové"
    path = tmp_path / "ui-control.ogp"
    window._project_manager.save(scene, path)
    identity = item.item_id
    window._project_manager.load(scene, path)
    assert scene.find_item_by_id(identity).name == "Abri rénové"


def test_models_pagination_tree_selection_and_editing(qtbot):
    view = QTreeView()
    qtbot.addWidget(view)
    model = QStandardItemModel()
    for row in range(70):
        parent = QStandardItem(f"Row {row}")
        parent.appendRow(QStandardItem(f"Child {row}"))
        model.appendRow(parent)
    view.setModel(model)
    view.show()
    controller = ApplicationUiController(view)
    try:
        identity = node(snapshot(controller), **{"class": "QTreeView"})["id"]
        page = controller.dispatch("model", {"widget_id": identity, "start": 60, "count": 10})
        assert page["row_count"] == 70 and page["rows"][0]["cells"][0]["text"] == "Row 60"
        children = controller.dispatch("model", {"widget_id": identity, "index_path": [60]})
        assert children["rows"][0]["cells"][0]["text"] == "Child 60"
        complete(controller, qtbot, queue(controller, "interact", widget_id=identity, action="expand",
                                          parameters={"index_path": [60]}))
        complete(controller, qtbot, queue(controller, "interact", widget_id=identity, action="edit_cell",
                                          parameters={"index_path": [60, 0], "value": "Edited child"}))
        assert model.item(60).child(0).text() == "Edited child"
        complete(controller, qtbot, queue(controller, "interact", widget_id=identity, action="select",
                                          parameters={"index_path": [60, 0]}))
        assert view.currentIndex().data() == "Edited child"
    finally:
        controller.close()


def test_file_dialog_is_discoverable_instead_of_native_and_accepts_a_path(ui_window, qtbot, tmp_path):
    window, controller = ui_window
    files = []
    opener = QAction("Test import file", window)
    opener.triggered.connect(lambda: files.append(QFileDialog.getOpenFileName(window, "Import test", str(tmp_path))[0]))
    window.addAction(opener)
    source = tmp_path / "sample.txt"
    source.write_text("fictional", encoding="utf-8")
    def body(call, wait, run):
        receipt = call("action", action_id=action(call("snapshot", max_nodes=10000), "Test import file")["id"],
                       request_key="open-file")
        wait(lambda: call("snapshot")["modal_id"] is not None)
        modal_id = call("snapshot")["modal_id"]
        state = call("snapshot", root_id=modal_id)
        edit = node(state, object_name="fileNameEdit")
        run("value", widget_id=edit["id"], value=str(source))
        run("interact", widget_id=modal_id, action="accept")
        wait(lambda: call("status", operation_id=receipt["operation_id"])["status"] == "completed")
    drive_ui(controller, qtbot, body)
    assert files == [str(source)]


def test_invalid_and_stale_controls_do_not_change_state(qtbot):
    owner = QWidget()
    qtbot.addWidget(owner)
    layout = QVBoxLayout(owner)
    spin = QDoubleSpinBox()
    spin.setRange(1, 10)
    spin.setValue(5)
    layout.addWidget(spin)
    password = QLineEdit("do-not-expose")
    password.setEchoMode(QLineEdit.EchoMode.Password)
    layout.addWidget(password)
    owner.show()
    controller = ApplicationUiController(owner)
    original = QApplication.testAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
    try:
        state = snapshot(controller)
        assert node(state, **{"class": "QLineEdit", "value": "<redacted>"})["value"] == "<redacted>"
        identity = node(state, **{"class": "QDoubleSpinBox"})["id"]
        for value in (100, True, "NaN"):
            receipt = queue(controller, "value", widget_id=identity, value=value)
            qtbot.waitUntil(lambda receipt=receipt: controller.dispatch("status", {"operation_id": receipt["operation_id"]})["status"] == "failed")
            assert spin.value() == 5
        spin.setEnabled(False)
        receipt = queue(controller, "value", widget_id=identity, value=7)
        qtbot.waitUntil(lambda receipt=receipt: controller.dispatch("status", {"operation_id": receipt["operation_id"]})["status"] == "failed")
        spin.deleteLater()
        qtbot.waitUntil(lambda: sip.isdeleted(spin))
        with pytest.raises(ValueError, match="stale"):
            queue(controller, "value", widget_id=identity, value=7)
    finally:
        controller.close()
    assert QApplication.testAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs) == original


def test_mcp_ui_auth_modal_resize_and_screenshot(ui_window, qtbot):
    import anyio

    from open_garden_planner.agent_api import AgentApiServer
    from tests.integration.test_agent_api_writes import TOKEN, _free_port, _run
    from tests.integration.test_garden_memory_mcp import unpack

    window, _controller = ui_window
    server = AgentApiServer(window._build_agent_providers(), port=_free_port(),
                            write_token=TOKEN, writes_enabled=True)
    server.start()

    async def body(ctx):
        http_client, ClientSession, url = ctx
        async with http_client(url) as (r, w, _), ClientSession(r, w) as client:
            await client.initialize()
            for tool, args in (
                ("get_application_ui", {}),
                ("invoke_ui_action", {"action_id": "unknown", "request_key": "denied"}),
                ("set_ui_value", {"widget_id": "unknown", "value": "x", "request_key": "denied"}),
                ("interact_ui", {"widget_id": "unknown", "action": "close", "request_key": "denied"}),
                ("get_ui_operation", {"operation_id": "unknown"}),
                ("read_ui_model", {"widget_id": "unknown"}),
                ("render_ui_image", {}),
            ):
                denied = await client.call_tool(tool, args)
                assert denied.isError, tool
        async with (http_client(url, headers={"Authorization": f"Bearer {TOKEN}"}) as (r, w, _),
                    ClientSession(r, w) as client):
            await client.initialize()

            async def call(tool, **args):
                return unpack(await client.call_tool(tool, args))

            async def done(receipt):
                for _ in range(300):
                    state = await call("get_ui_operation", operation_id=receipt["operation_id"])
                    if state["status"] in {"completed", "failed"}:
                        assert state["status"] == "completed", state
                        return
                    await anyio.sleep(0.01)
                raise AssertionError("UI operation timed out")

            state = await call("get_application_ui", max_nodes=10000)
            opener = action(state, "Canvas Size...")
            receipt = await call("invoke_ui_action", action_id=opener["id"], request_key="resize-open")
            for _ in range(100):
                state = await call("get_application_ui")
                if state["modal_id"]:
                    break
                await anyio.sleep(0.01)
            assert state["modal_id"]
            assert (await call("get_ui_operation", operation_id=receipt["operation_id"]))["status"] == "running"
            modal = await call("get_application_ui", root_id=state["modal_id"])
            dimensions = [n for n in modal["nodes"] if n["class"] == "QDoubleSpinBox"]
            assert len(dimensions) == 2
            for i, value in enumerate((42.0, 21.0)):
                await done(await call("set_ui_value", widget_id=dimensions[i]["id"], value=value,
                                     request_key=f"dimension-{i}"))
            shot = await client.call_tool("render_ui_image", {"widget_id": state["modal_id"]})
            assert not shot.isError and any(block.type == "image" for block in shot.content)
            await done(await call("interact_ui", widget_id=state["modal_id"], action="accept", request_key="resize-accept"))
            await done(receipt)
            summary = await call("get_plan_summary")
            # Confirm the domain model changed, rather than just the spinboxes.
            assert "4200" in str(summary) and "2100" in str(summary)
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(lambda: QApplication.activeModalWidget().reject() if QApplication.activeModalWidget() else None)
    watchdog.start(15000)
    try:
        _run(server, body, qtbot)
    finally:
        watchdog.stop()
        server.stop()
    assert window.canvas_scene.width_cm == 4200
    assert window.canvas_scene.height_cm == 2100


def test_scene_coordinates_drive_real_viewport_drag(ui_window, qtbot):
    from PyQt6.QtCore import QPoint, QPointF

    window, controller = ui_window
    view = window.canvas_view
    view.set_snap_enabled(False)
    view.set_object_snap_enabled(False)
    centre = view.mapToScene(view.viewport().rect().center())
    # Keep the interior distinct from the resize handles even at fit-to-plan zoom.
    item = RectangleItem(centre.x() - 500, centre.y() - 400, 1000, 800,
                         layer_id=window.canvas_scene.active_layer.id)
    window.canvas_scene.addItem(item)
    identity = next(n["id"] for n in snapshot(controller, max_nodes=10000)["nodes"] if n["class"] == "CanvasView")
    complete(controller, qtbot, queue(controller, "interact", widget_id=identity, action="select_scene",
                                      parameters={"item_ids": [str(item.item_id)]}))
    # Settle selection-driven sidebar layout before beginning a captured drag.
    qtbot.wait(200)
    before = QPointF(item.pos())
    # Cross Qt's pixel drag threshold even when the whole 50 m plan is fit.
    end = view.mapToScene(view.mapFromScene(centre) + QPoint(40, -30))
    expected = view.mapToScene(view.mapFromScene(end)) - view.mapToScene(view.mapFromScene(centre))
    for event, point, held in (
        ("mouse_press", centre, []),
        ("mouse_move", end, ["left"]),
        ("mouse_release", end, []),
    ):
        complete(controller, qtbot, queue(controller, "interact", widget_id=identity, action=event,
            parameters={"x": point.x(), "y": point.y(), "coordinate_space": "scene", "buttons": held}))
    delta = item.pos() - before
    # Qt's mouse positions are logical pixels; selection can also resize the
    # sidebar/viewport. Bound the physical rounding by one measured pixel.
    pixel = (view.mapToScene(QPoint(1, 1)) - view.mapToScene(QPoint(0, 0)))
    assert delta.x() == pytest.approx(expected.x(), abs=abs(pixel.x()))
    assert delta.y() == pytest.approx(expected.y(), abs=abs(pixel.y()))
    view.command_manager.undo()
    assert item.pos() == before


def test_group_and_ungroup_use_native_keyboard_commands(ui_window, qtbot):
    from open_garden_planner.ui.canvas.items.group_item import GroupItem

    window, controller = ui_window
    items = [RectangleItem(100 + i * 200, 100, 100, 100,
                           layer_id=window.canvas_scene.active_layer.id) for i in range(2)]
    for item in items:
        window.canvas_scene.addItem(item)
    identity = next(n["id"] for n in snapshot(controller, max_nodes=10000)["nodes"] if n["class"] == "CanvasView")
    complete(controller, qtbot, queue(controller, "interact", widget_id=identity, action="select_scene",
                                      parameters={"item_ids": [str(i.item_id) for i in items]}))
    complete(controller, qtbot, queue(controller, "interact", widget_id=identity, action="key_sequence",
                                      parameters={"key": "Ctrl+G"}))
    groups = [i for i in window.canvas_scene.items() if isinstance(i, GroupItem)]
    assert len(groups) == 1
    complete(controller, qtbot, queue(controller, "interact", widget_id=identity, action="key_sequence",
                                      parameters={"key": "Ctrl+Shift+G"}))
    assert not [i for i in window.canvas_scene.items() if isinstance(i, GroupItem)]
    window.canvas_view.command_manager.undo()
    assert len([i for i in window.canvas_scene.items() if isinstance(i, GroupItem)]) == 1


def test_native_3d_window_input_uses_the_real_focus_target(ui_window, qtbot, tmp_path):
    if QApplication.platformName() != "cocoa":
        pytest.skip("Native Qt3D input/capture needs the macOS window system")
    window, controller = ui_window
    window.canvas_scene.addItem(RectangleItem(100, 100, 500, 400, object_type=ObjectType.GARAGE_SHED))
    receipt = queue(controller, "action", action_id=action(snapshot(controller, max_nodes=10000), "3D View…")["id"])
    complete(controller, qtbot, receipt)
    qtbot.waitUntil(lambda: window._view3d_window is not None)
    view3d = window._view3d_window
    try:
        qtbot.wait(200)
        state = snapshot(controller, max_nodes=10000)
        native = next(n for n in state["nodes"] if n["class"] == "Qt3DWindow")
        walk = action(state, "Walk")
        complete(controller, qtbot, queue(controller, "action", action_id=walk["id"]))
        assert view3d.adapter.camera_mode == "walk"
        camera = view3d.adapter.window_handle().camera()
        before = camera.position()
        complete(controller, qtbot, queue(controller, "interact", widget_id=native["id"], action="key_press",
                                          parameters={"key": "W"}))
        qtbot.wait(150)
        complete(controller, qtbot, queue(controller, "interact", widget_id=native["id"], action="key_release",
                                          parameters={"key": "W"}))
        assert (camera.position() - before).length() > 0
        rendered = controller.dispatch("render", {"widget_id": native["id"]})
        assert rendered["png_bytes"].startswith(b"\x89PNG")
        from PyQt6.QtGui import QImage

        image = QImage.fromData(rendered["png_bytes"])
        colours = {image.pixelColor(x, y).rgba() for x in range(0, image.width(), 20)
                   for y in range(0, image.height(), 20)}
        assert len(colours) > 5, "The captured native 3D window is blank"
        (tmp_path / "native-mcp-3d.png").write_bytes(rendered["png_bytes"])
        complete(controller, qtbot, queue(controller, "interact", widget_id=native["id"], action="key_sequence",
                                          parameters={"key": "Escape"}))
        assert view3d.adapter.camera_mode == "orbit"
    finally:
        view3d.close()
        qtbot.wait(50)


def test_double_click_delivers_the_complete_qt_sequence(qtbot):
    from PyQt6.QtWidgets import QListWidget

    view = QListWidget()
    qtbot.addWidget(view)
    view.addItems(["First", "Second"])
    view.show()
    activated = []
    view.itemDoubleClicked.connect(lambda item: activated.append(item.text()))
    controller = ApplicationUiController(view)
    try:
        state = snapshot(controller)
        viewport = next(n for n in state["nodes"] if n["object_name"] == "qt_scrollarea_viewport")
        point = view.visualItemRect(view.item(1)).center()
        complete(controller, qtbot, queue(controller, "interact", widget_id=viewport["id"], action="double_click",
                                          parameters={"x": point.x(), "y": point.y()}))
        assert activated == ["Second"]
    finally:
        controller.close()


def test_layer_row_reorder_reuses_the_real_command_and_undo(ui_window, qtbot):
    window, controller = ui_window
    window._do_agent_create_layer("Second")
    window._do_agent_create_layer("Third")
    original = [layer.id for layer in window.canvas_scene.layers]
    state = snapshot(controller, max_nodes=10000)
    label = node(state, **{"class": "QLabel", "text": "Layers", "visible": True})
    complete(controller, qtbot, queue(controller, "interact", widget_id=label["parent_id"], action="click"))
    field = node(snapshot(controller, max_nodes=10000), object_name="LayerList")
    complete(controller, qtbot, queue(controller, "interact", widget_id=field["id"], action="move_rows",
                                      parameters={"row": 0, "count": 1, "destination": 3}))
    assert [layer.id for layer in window.canvas_scene.layers] == [*original[1:], original[0]]
    assert window._project_manager.is_dirty
    window.canvas_view.command_manager.undo()
    assert [layer.id for layer in window.canvas_scene.layers] == original
    window.canvas_view.command_manager.redo()
    assert [layer.id for layer in window.canvas_scene.layers] == [*original[1:], original[0]]


def test_plant_list_drop_uses_its_native_mime_and_canvas_creation(ui_window, qtbot):
    import json

    from PyQt6.QtWidgets import QListWidgetItem

    from open_garden_planner.models.plant_data import PlantSpeciesData
    from open_garden_planner.ui.panels.plant_lists_panel import _DraggablePlantListWidget

    window, controller = ui_window
    source = _DraggablePlantListWidget()
    source.setObjectName("RemoteDropSource")
    qtbot.addWidget(source)
    row = QListWidgetItem("Fictional garden plant")
    row.setData(Qt.ItemDataRole.UserRole, "fictional-entry")
    species = PlantSpeciesData(scientific_name="Fictional species", common_name="Fictional plant")
    row.setData(Qt.ItemDataRole.UserRole + 1, json.dumps(species.to_dict()))
    source.addItem(row)
    source.show()
    state = snapshot(controller, max_nodes=10000)
    source_id = node(state, object_name="RemoteDropSource")["id"]
    view = window.canvas_view
    point = view.mapToScene(view.viewport().rect().center())
    target_id = next(n["id"] for n in state["nodes"] if n["class"] == "CanvasView")
    before = {i.item_id for i in window.canvas_scene.items() if hasattr(i, "item_id")}
    complete(controller, qtbot, queue(controller, "interact", widget_id=target_id, action="drop",
        parameters={"source_id": source_id, "index_path": [0], "x": point.x(), "y": point.y(), "coordinate_space": "scene"}))
    created = [i for i in window.canvas_scene.items() if hasattr(i, "item_id") and i.item_id not in before]
    assert len(created) == 1
    assert created[0].metadata["plant_species"]["scientific_name"] == "Fictional species"
    window.canvas_view.command_manager.undo()
    assert not [i for i in window.canvas_scene.items() if hasattr(i, "item_id") and i.item_id not in before]


def test_cell_delegate_limits_are_not_bypassed(qtbot):
    from PyQt6.QtWidgets import QStyledItemDelegate

    class BoundedDelegate(QStyledItemDelegate):
        def createEditor(self, parent, _option, _index):
            editor = QSpinBox(parent)
            editor.setRange(1, 10)
            return editor

    view = QTreeView()
    qtbot.addWidget(view)
    model = QStandardItemModel()
    row = QStandardItem()
    row.setData(5, Qt.ItemDataRole.EditRole)
    model.appendRow(row)
    view.setModel(model)
    delegate = BoundedDelegate(view)
    view.setItemDelegate(delegate)
    view.show()
    view.activateWindow()
    qtbot.wait(10)
    controller = ApplicationUiController(view)
    try:
        identity = node(snapshot(controller), **{"class": "QTreeView"})["id"]
        receipt = queue(controller, "interact", widget_id=identity, action="edit_cell",
                        parameters={"index_path": [0], "value": 99})
        qtbot.waitUntil(lambda: controller.dispatch("status", {"operation_id": receipt["operation_id"]})["status"] == "failed")
        assert model.index(0, 0).data(Qt.ItemDataRole.EditRole) == 5
        complete(controller, qtbot, queue(controller, "interact", widget_id=identity, action="edit_cell",
                                          parameters={"index_path": [0], "value": 7}))
        assert model.index(0, 0).data(Qt.ItemDataRole.EditRole) == 7
    finally:
        controller.close()


def test_layer_slider_commits_one_normal_undo_step(ui_window, qtbot):
    window, controller = ui_window
    layer = window.canvas_scene.active_layer
    old = layer.opacity
    state = snapshot(controller, max_nodes=10000)
    label = node(state, **{"class": "QLabel", "text": "Layers", "visible": True})
    complete(controller, qtbot, queue(controller, "interact", widget_id=label["parent_id"], action="click"))
    slider = node(snapshot(controller, max_nodes=10000), **{"class": "QSlider", "visible": True})
    complete(controller, qtbot, queue(controller, "value", widget_id=slider["id"], value=40))
    assert layer.opacity == 0.4
    window.canvas_view.command_manager.undo()
    assert layer.opacity == old
    assert not window.canvas_view.command_manager.can_undo
