"""External services (file I/O, plant API, etc.).

Exports are resolved lazily so importing one focused service does not also load
the Qt autosave manager and settings stack.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "AutoSaveManager",
    "PlantAPIClient",
    "PlantAPIError",
    "PlantAPIManager",
    "PlantDetailUnavailableError",
    "PlantLibrary",
    "get_plant_library",
]

_EXPORT_MODULES = {
    "AutoSaveManager": "autosave_service",
    "PlantAPIClient": "plant_api",
    "PlantAPIError": "plant_api",
    "PlantAPIManager": "plant_api",
    "PlantDetailUnavailableError": "plant_api",
    "PlantLibrary": "plant_library",
    "get_plant_library": "plant_library",
}


def __getattr__(name: str) -> Any:
    """Load historical package exports only when a caller requests them."""
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{module_name}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
