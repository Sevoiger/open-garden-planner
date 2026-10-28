"""Reader I/O must not hold the lock needed by live Qt writers."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, get_ident

import pytest

from open_garden_planner.services import library_operations
from open_garden_planner.services.library_operations import LibraryConflictError
from open_garden_planner.services.library_port import LibraryPort


def test_writer_commits_while_reader_io_is_blocked(tmp_path, monkeypatch):
    port = LibraryPort(tmp_path / "library.json")
    preview = port.dispatch("prepare", {"records": [{
        "source_id": "synthetic", "scientific_name": "Fictitia example", "common_name": "Synthetic",
    }]})
    entered, release = Event(), Event()
    original = library_operations.read_library_document
    reader_ident = []

    def blocked(path):
        if get_ident() == reader_ident[0]:
            entered.set()
            assert release.wait(5)
        return original(path)

    def read():
        reader_ident.append(get_ident())
        return port.dispatch("snapshot", {})

    monkeypatch.setattr(library_operations, "read_library_document", blocked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(read)
        try:
            assert entered.wait(5)
            writer = pool.submit(port.dispatch, "apply", {
                "preparation_id": preview["token"], "request_key": "commit-during-read",
            })
            committed = writer.result(timeout=3)
            assert committed["persisted"]
        finally:
            release.set()
        assert "synthetic" in reader.result(timeout=5)["plants"]


def test_preparation_during_commit_is_stale_instead_of_overwriting(tmp_path, monkeypatch):
    port = LibraryPort(tmp_path / "library.json")
    first = port.dispatch("prepare", {"records": [{
        "source_id": "first", "scientific_name": "Fictitia first", "common_name": "First",
    }]})
    entered, release = Event(), Event()
    original = library_operations.commit_library_document

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(library_operations, "commit_library_document", blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        writer = pool.submit(port.dispatch, "apply", {
            "preparation_id": first["token"], "request_key": "first",
        })
        try:
            assert entered.wait(5)
            second = port.dispatch("prepare", {"records": [{
                "source_id": "second", "scientific_name": "Fictitia second", "common_name": "Second",
            }]})
            assert second["can_apply"]
        finally:
            release.set()
        assert writer.result(timeout=5)["persisted"]
    with pytest.raises(LibraryConflictError, match="changed after preparation"):
        port.dispatch("apply", {"preparation_id": second["token"], "request_key": "second"})
    assert set(port.dispatch("snapshot", {})["plants"]) == {"first"}
