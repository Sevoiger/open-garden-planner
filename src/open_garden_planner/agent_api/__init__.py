"""Agent API: an opt-in, loopback-only MCP server embedded in the app.

Lets AI agents (Claude, Cursor, any MCP client) read and, behind the explicit
write gate, edit the garden plan currently open in Open Garden Planner. See
Package D / epic #237. The package exposes the D1 read surface plus the D2
command-backed write and global history providers.

``AgentApiServer``/``build_server`` are imported lazily so merely importing this
package never pulls in ``mcp``/``uvicorn``.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "AgentApiServer",
    "AgentProviders",
    "CurveGeometry",
    "Diagnostic",
    "ExportResult",
    "GeometryAnchor",
    "GeometryConstraint",
    "GeometryPoint",
    "GeometryResult",
    "LeaderGeometry",
    "MainThreadBridge",
    "Measurement",
    "ObjectDetail",
    "ObjectRef",
    "PlanSummary",
    "PortInUseError",
    "RenderMeta",
    "build_server",
    "diagnostics_from_records",
    "plan_summary_from_snapshot",
]

_EXPORT_MODULES = {
    "AgentApiServer": "server",
    "AgentProviders": "providers",
    "CurveGeometry": "schema",
    "Diagnostic": "schema",
    "ExportResult": "schema",
    "GeometryAnchor": "schema",
    "GeometryConstraint": "schema",
    "GeometryPoint": "schema",
    "GeometryResult": "schema",
    "LeaderGeometry": "schema",
    "MainThreadBridge": "bridge",
    "Measurement": "schema",
    "ObjectDetail": "schema",
    "ObjectRef": "schema",
    "PlanSummary": "schema",
    "PortInUseError": "server",
    "RenderMeta": "schema",
    "build_server": "server",
    "diagnostics_from_records": "diagnostics",
    "plan_summary_from_snapshot": "mapping",
}


def __getattr__(name: str) -> Any:
    """Resolve the old package surface without importing unrelated adapters."""
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{module_name}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
