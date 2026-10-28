"""Small serialized port shared by the live and autonomous library tools."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from open_garden_planner.services.library_operations import LibraryOperations


class LibraryPort:
    def __init__(self, path: Path) -> None:
        self.operations = LibraryOperations(path)
        self._mutex = threading.RLock()
        # Bound concurrent reader allocations without making a Qt writer wait
        # for a worker's disk read. Atomic files and commit CAS coordinate the
        # two lanes; LibraryOperations protects its short preparation cache.
        self._read_mutex = threading.RLock()

    def dispatch(self, action: str, arguments: dict[str, Any]) -> dict[str, Any]:
        readonly = action in {"snapshot", "prepare", "result"}
        with self._read_mutex if readonly else self._mutex:
            if action == "snapshot":
                return {"plants": self.operations.snapshot(), "scope": "library"}
            if action == "prepare":
                return {**self.operations.prepare(arguments["records"]), "scope": "library", "persisted": False}
            if action == "apply":
                return self.operations.apply(arguments["preparation_id"], arguments["request_key"])
            if action == "result":
                return self.operations.result(arguments["request_key"]) or {"status": "unknown"}
            if action == "undo":
                return self.operations.undo(arguments["transaction_id"])
            raise ValueError(f"Unknown library action: {action}")
