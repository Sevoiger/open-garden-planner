"""Run the document-only MCP host: python -m open_garden_planner.agent_api.autonomous."""

from __future__ import annotations

import argparse
import os
import signal
import threading
from pathlib import Path

from open_garden_planner.agent_api.document_tools import DomainProviders
from open_garden_planner.agent_api.server import AgentApiServer
from open_garden_planner.core.document_session import DocumentSession
from open_garden_planner.services.library_port import LibraryPort


def main() -> None:
    parser = argparse.ArgumentParser(description="OGP document MCP host without Qt")
    parser.add_argument("project", type=Path)
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--write", action="store_true", help="Enable authenticated data mutations")
    parser.add_argument("--token-env", default="OGP_DOMAIN_TOKEN", help="Environment variable holding the write token")
    parser.add_argument("--library", type=Path, help="Explicit optional botanical-library JSON path")
    args = parser.parse_args()
    token = os.environ.get(args.token_env)
    if args.write and (not token or not token.isascii()):
        parser.error("--write requires a nonempty ASCII token in the selected environment variable")
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_args: stopped.set())
    with DocumentSession(args.project) as document:
        library = LibraryPort(args.library) if args.library is not None else None
        server = AgentApiServer(DomainProviders(document.dispatch, library.dispatch if library else None), port=args.port,
                                write_token=token, writes_enabled=args.write)
        server.start()
        try:
            stopped.wait()
        finally:
            server.stop()


if __name__ == "__main__":
    main()
