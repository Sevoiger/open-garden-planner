"""Build-time identity for a running server, independent of later checkout edits."""

import hashlib
import os
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from open_garden_planner.services.update_checker import get_current_version

API_CONTRACT_VERSION = "2"


def server_identity() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    # Source fingerprint is captured once when building the server, not recomputed
    # by get_capabilities after the working tree has changed. Frozen builds may
    # contain no .py; the application version remains available there.
    sources = sorted(root.rglob("*.py"))
    for path in sources:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return {
        "application_version": get_current_version(),
        "api_contract_version": API_CONTRACT_VERSION,
        "mcp_sdk_version": version("mcp"),
        "process_id": os.getpid(),
        "server_built_at": datetime.now(UTC).isoformat(),
        "source_sha256_at_server_build": digest.hexdigest() if sources else None,
        "source_file_count": len(sources),
    }
