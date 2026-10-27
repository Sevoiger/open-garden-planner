"""An explicit-path garden document session requiring neither Qt nor UI."""

from __future__ import annotations

import os
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from open_garden_planner.core.command_history import CommandHistory
from open_garden_planner.core.document_store import (
    DocumentOwnership,
    check_disk_version,
    disk_version,
)
from open_garden_planner.core.plan_io import read_plan, write_plan
from open_garden_planner.core.project_data import FILE_VERSION, _is_newer_file_version
from open_garden_planner.services.document_operations import OperationSession, document_fingerprint


class DocumentSession:
    """Own one .ogp file. Every successful mutation commits data and receipt.

    This host supports data operations only. It preserves raw geometry and
    unknown fields, never constructs graphical items or imports a library.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path).resolve(strict=True)
        self._mutex = threading.RLock()
        self._ownership = DocumentOwnership(self.path)
        self._closed = False
        try:
            self._document = read_plan(self.path)
            if _is_newer_file_version(str(self._document.get("version", "1.0")), FILE_VERSION):
                raise ValueError("Project format is newer than this application")
            self._disk_version = disk_version(self.path)
        except BaseException:
            self._ownership.close()
            raise
        self.history = CommandHistory()
        self._epoch, self._sequence = str(uuid4()), 0
        self._fingerprint: str | None = None
        self._backup_created = False
        self.operations = OperationSession(self.snapshot, self._write, self.history.execute,
                                           self.revision, persisted=True, media_check=self.check_media)

    def check_media(self, reference: str) -> None:
        root = self.path.parent.resolve()
        path = (root / reference).resolve()
        path.relative_to(root)
        if not path.is_file():
            raise ValueError(f"Missing project attachment: {reference}")

    def snapshot(self) -> dict[str, Any]:
        with self._mutex:
            self._require_open()
            return deepcopy(self._document)

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError("Document session is closed")

    def revision(self) -> str:
        with self._mutex:
            self._require_open()
            if self._fingerprint is None:
                self._fingerprint = document_fingerprint(self._document)
            return f"{self._epoch}:{self._sequence}:{self._fingerprint}"

    def _write(self, candidate: dict[str, Any]) -> None:
        self._require_open()
        check_disk_version(self.path, self._disk_version)
        # Keep a pre-mutation backup once per session, before first publication.
        if not self._backup_created:
            backup = self.path.with_name(f"{self.path.stem}.pre-domain-{self._epoch}.ogp")
            stream = backup.open("xb")
            try:
                with stream:
                    stream.write(self.path.read_bytes())
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                backup.unlink(missing_ok=True)
                raise
            self._backup_created = True
        write_plan(self.path, candidate)
        self._disk_version = disk_version(self.path)
        self._document = deepcopy(candidate)
        self._sequence += 1
        self._fingerprint = None

    def dispatch(self, action: str, arguments: dict[str, Any]) -> dict[str, Any]:
        with self._mutex:
            self._require_open()
            check_disk_version(self.path, self._disk_version)
            if action in {"read", "media"}:
                from open_garden_planner.agent_api.read_projection import (
                    project_document_state,
                    read_document_media,
                )

                revision = self.revision()
                if action == "read":
                    return project_document_state(
                        self._document,
                        revision=revision,
                        mode="autonomous",
                        **arguments,
                    )
                return read_document_media(
                    self._document,
                    media_id=arguments["media_id"],
                    revision=revision,
                    expected_revision=arguments["expected_revision"],
                    offset=arguments.get("offset", 0),
                    max_bytes=arguments.get("max_bytes", 65536),
                )
            if action == "snapshot":
                return {"revision": self.revision(), "document": self.snapshot(), "mode": "autonomous"}
            if action == "prepare":
                return self.operations.prepare(arguments["operations"], arguments.get("expected_revision"))
            if action == "apply":
                return self.operations.apply(arguments["preparation_id"], arguments["request_key"])
            if action == "result":
                return self.operations.result(arguments["request_key"])
            if action in {"undo", "redo"}:
                method = getattr(self.history, action)
                if not getattr(self.history, f"can_{action}"):
                    raise ValueError(f"Nothing to {action}")
                method()
                return {"status": action, "persisted": True, "undo_depth": self.history.undo_depth,
                        "redo_depth": self.history.redo_depth}
            raise ValueError(f"Unsupported autonomous action: {action}")

    def close(self) -> None:
        with self._mutex:
            if not self._closed:
                self._closed = True
                self._ownership.close()

    def __enter__(self) -> DocumentSession:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()
