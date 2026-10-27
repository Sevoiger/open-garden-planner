"""Reference points use the compatible circle record and shared object links."""

from typing import Any


def is_reference_point(item: Any) -> bool:
    """Whether a garden item is a surveying reference rather than a surface."""
    return getattr(item, "metadata", {}).get("reference_point") is True
