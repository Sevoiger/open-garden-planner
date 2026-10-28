"""Live library reads leave Qt free; commits retain Qt ordering and CAS."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from PyQt6.QtCore import QThread

from open_garden_planner.app.application import GardenPlannerApp
from open_garden_planner.services import library_operations, plant_library
from open_garden_planner.services.library_operations import LibraryConflictError


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(GardenPlannerApp, "_startup_sequence", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_start_update_check", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_confirm_discard_changes", lambda _self: True)
    instance = GardenPlannerApp()
    qtbot.addWidget(instance)
    yield instance
    instance.close()


def wait_result(future, qtbot):
    qtbot.waitUntil(future.done, timeout=10000)
    return future.result()


@pytest.mark.parametrize("action, arguments", [
    ("snapshot", {}), ("result", {"request_key": "unknown"}),
    ("prepare", {"records": [{"source_id": "new", "scientific_name": "Fictitia new", "common_name": "Synthetic"}]}),
])
def test_readonly_library_dispatch_runs_without_qt(window, qtbot, monkeypatch, action, arguments):
    main_thread = QThread.currentThread()
    original = library_operations.read_library_document
    calls = []

    def checked(path):
        calls.append(QThread.currentThread())
        assert QThread.currentThread() is not main_thread
        return original(path)

    monkeypatch.setattr(library_operations, "read_library_document", checked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = wait_result(pool.submit(window._agent_library_operation, action, arguments), qtbot)
    assert calls
    if action == "prepare":
        assert result["can_apply"]


def test_blocked_library_read_does_not_block_qt_or_share_mutable_data(window, qtbot, monkeypatch):
    entered, release = Event(), Event()
    main_thread = QThread.currentThread()
    original = library_operations.read_library_document

    with ThreadPoolExecutor(max_workers=1) as pool:
        preview = wait_result(pool.submit(window._agent_library_operation, "prepare", {"records": [{
            "source_id": "while-reading", "scientific_name": "Fictitia concurrent", "common_name": "Synthetic",
        }]}), qtbot)

    def blocked(path):
        if QThread.currentThread() is not main_thread:
            entered.set()
            assert release.wait(5)
        return original(path)

    monkeypatch.setattr(library_operations, "read_library_document", blocked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(window._agent_library_operation, "snapshot", {})
        try:
            qtbot.waitUntil(entered.is_set)
            # A second Qt hop finishes while the disk-read worker is held.
            qt_call = pool.submit(window._agent_bridge.run_on_main, lambda: QThread.currentThread())
            assert wait_result(qt_call, qtbot) is main_thread
            # A real library write also completes on Qt while reader I/O is
            # held, rather than blocking Qt on a worker-owned port mutex.
            write = pool.submit(window._agent_library_operation, "apply", {
                "preparation_id": preview["token"], "request_key": "while-reading",
            })
            assert wait_result(write, qtbot)["persisted"]
        finally:
            release.set()
        snapshot = wait_result(future, qtbot)
    assert "while-reading" in snapshot["plants"]
    snapshot["plants"]["invented"] = {"raw_data": {"extension": [1]}}
    with ThreadPoolExecutor(max_workers=1) as pool:
        current = wait_result(pool.submit(window._agent_library_operation, "snapshot", {}), qtbot)
    assert "invented" not in current["plants"]


def test_worker_preparation_revalidates_qt_edits_and_commits_on_qt(window, qtbot, monkeypatch):
    from open_garden_planner.models.plant_data import PlantSpeciesData

    refresh_threads = []
    main_thread = QThread.currentThread()
    monkeypatch.setattr(window, "_update_plant_database_panel", lambda: refresh_threads.append(QThread.currentThread()))
    arguments = {"records": [{"source_id": "new", "scientific_name": "Fictitia new", "common_name": "Synthetic"}]}
    with ThreadPoolExecutor(max_workers=1) as pool:
        preview = wait_result(pool.submit(window._agent_library_operation, "prepare", arguments), qtbot)
        # The normal Qt editor facade publishes a distinct record meanwhile.
        library = plant_library.get_plant_library()
        library.add_plant(PlantSpeciesData(scientific_name="Fictitia edited", common_name="Edited", source_id="edited"))
        stale = pool.submit(window._agent_library_operation, "apply", {
            "preparation_id": preview["token"], "request_key": "stale",
        })
        with pytest.raises(LibraryConflictError, match="changed after preparation"):
            wait_result(stale, qtbot)
        preview = wait_result(pool.submit(window._agent_library_operation, "prepare", arguments), qtbot)
        committed = wait_result(pool.submit(window._agent_library_operation, "apply", {
            "preparation_id": preview["token"], "request_key": "committed",
        }), qtbot)
        assert committed["persisted"]
        replay = wait_result(pool.submit(window._agent_library_operation, "apply", {
            "preparation_id": preview["token"], "request_key": "committed",
        }), qtbot)
        assert replay["replayed"]
        undone = wait_result(pool.submit(window._agent_library_operation, "undo", {
            "transaction_id": committed["transaction_id"],
        }), qtbot)
        assert undone["status"] == "undone"
    assert refresh_threads and all(thread is main_thread for thread in refresh_threads)
    assert plant_library.get_plant_library().get_plant("new") is None
