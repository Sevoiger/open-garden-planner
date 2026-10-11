"""What every built-world builder shares (Phase 17 L1.6).

The truth rules of the built world live here, once:

- **The top is the height.** A builder shapes its item for a height and its
  top is that height by construction. ``fit_parts`` is the safety net behind it
  (a z-scale about the ground, as the spike's ``fit_height``); a builder whose
  ``fit_scale`` is not 1 is a bug the tests catch, not a look the fit hides.
- **No height, no shadow.** An item the plan gives no height is built at its
  kind's nominal height as decoration and none of its parts casts a shadow —
  exactly as in 2D, where it casts none. A kind without a nominal height gets
  no solid at all.

``BuiltBuilder`` wraps a *shape function* ``(record, height_cm) -> parts`` into
the ``Builder`` the registry wants and applies both rules.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence
from typing import Final, TypeAlias

import numpy as np

from ...container_model import container_height_cm
from ...object_height import DEFAULT_HEIGHTS_CM
from ..mesh import Mesh, MeshPart
from ..record import Record

ShapeFunction: TypeAlias = Callable[[Record, float], tuple[MeshPart, ...]]

#: A fit scale this close to 1 is float32 rounding of an exact construction,
#: and the mesh is left untouched.
FIT_IDENTITY: Final = 1e-6

#: Kinds whose resolver always has a height, but through the container fill
#: height rather than the per-type table (``object_height._CONTAINER_TYPE_NAMES``).
_CONTAINER_KINDS: Final = frozenset({"CONTAINER", "CONTAINER_ROUND", "WALL_PLANTER"})


def nominal_height_cm(kind: str) -> float | None:
    """The height an item of ``kind`` is drawn at when the plan gives it none:
    its default from the 2D resolver's own tables, or None when it has none."""
    if kind in _CONTAINER_KINDS:
        return container_height_cm(None)
    return DEFAULT_HEIGHTS_CM.get(kind)


def top_cm(parts: Sequence[MeshPart]) -> float | None:
    """The highest vertex of the union of ``parts`` (None when they hold none)."""
    tops = [float(part.mesh.positions[:, 2].max()) for part in parts if part.mesh.vertex_count]
    return max(tops) if tops else None


def fit_scale(parts: Sequence[MeshPart], height_cm: float) -> float:
    """``height_cm`` / the top of ``parts``: 1.0 when they were built to the height
    (and for no parts). The number gate G1 holds every builder to."""
    top = top_cm(parts)
    if top is None or top <= 0.0:
        return 1.0
    return height_cm / top


def _scaled_z(mesh: Mesh, scale: float) -> Mesh:
    positions = mesh.positions.astype(np.float64)
    positions[:, 2] *= scale
    normals = mesh.normals.astype(np.float64) / np.array([1.0, 1.0, scale])  # inverse transpose
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    return Mesh.from_arrays(positions, normals, mesh.colors, mesh.uvs, mesh.indices)


def fit_parts(parts: tuple[MeshPart, ...], height_cm: float) -> tuple[MeshPart, ...]:
    """``parts`` scaled in z about the ground, with ONE scale, so the top of their
    union is ``height_cm``. The identity for parts built to the height."""
    scale = fit_scale(parts, height_cm)
    if abs(scale - 1.0) <= FIT_IDENTITY:
        return parts
    return tuple(dataclasses.replace(part, mesh=_scaled_z(part.mesh, scale)) for part in parts)


class BuiltBuilder:
    """A ``Builder`` made of a shape function plus the two truth rules.

    ``shape(record, height_cm)`` returns the parts for a height; it reads only
    the record's geometry part. ``raw(record)`` gives its output before the fit
    (the tests measure ``fit_scale`` on it); calling the instance gives the parts
    the engine gets.
    """

    def __init__(self, shape: ShapeFunction, name: str) -> None:
        self._shape = shape
        self.name = name

    def __repr__(self) -> str:
        return f"<built-world builder {self.name}>"

    def height_for(self, record: Record) -> float | None:
        """The height the item is built at: the plan's, else the kind's nominal one."""
        if record.height_cm is not None:
            return record.height_cm
        return nominal_height_cm(record.kind)

    def raw(self, record: Record) -> tuple[MeshPart, ...]:
        """The shape function's parts, unfitted (``()`` when there is no height at all)."""
        height = self.height_for(record)
        if height is None:
            return ()
        return tuple(part for part in self._shape(record, height) if part.mesh.vertex_count)

    def fit_scale(self, record: Record) -> float:
        """How far the shape function missed the height (1.0 = built to it)."""
        height = self.height_for(record)
        return 1.0 if height is None else fit_scale(self.raw(record), height)

    def __call__(self, record: Record) -> tuple[MeshPart, ...]:
        height = self.height_for(record)
        if height is None:
            return ()
        parts = fit_parts(self.raw(record), height)
        if record.height_cm is None:  # decoration: casts no shadow in 2D either
            parts = tuple(dataclasses.replace(part, casts_shadow=False) for part in parts)
        return parts


def footprint_of(record: Record) -> tuple[tuple[float, float], ...]:
    """The item's outer footprint ring (item-local), or ``()``. For a polygon,
    rectangle, ellipse or circle that is its one ring. NEVER build a polyline
    from this: its stroke polygon can come back as an outer ring plus a hole
    (a fence round a garden) — use ``record.path``."""
    return record.footprints[0] if record.footprints else ()
