"""The plan's ground as tiles of plain data (Phase 17 L1.5, #385, ADR-054 addendum).

In 3D everything flat — lawns, terraces, driveways, paths, in-ground beds — is
not geometry: it is PAINTED into the ground, as the 2D canvas paints it. This
module is the Qt-free half of that: the pixel grid over the plan rectangle, the
tile a sink receives, and the seam through which ``SceneSync`` asks for tiles
(``GroundBaking``). The painting itself needs ``QPainter`` and lives in
``ui/view3d/ground_bake.py``.

THE GRID. The plan rectangle runs from scene (0, 0) to (``width_cm`` east,
``depth_cm`` north). Over it lies ONE grid of square pixels, ``px_per_cm`` to
the centimetre, anchored at the south-west corner: pixel column ``i`` covers
east ``[i, i + 1) / px_per_cm``, pixel row ``j`` — counted from the SOUTH —
north ``[j, j + 1) / px_per_cm``. A tile is ``tile_px`` × ``tile_px`` of those
pixels (fewer in the last column and the last row), so every tile is a crop of
the same conceptual image and two neighbours meet without a seam by
construction: no pixel is painted twice and none is resampled.

Resolution. ``px_per_cm`` is the largest power of one half, at most 1, that
keeps the whole ground within ``max_pixels``. At 1 px/cm a fill texture is
painted texel for texel (the textures are drawn at 1 px = 1 cm); at 1/2 and
1/4 a texel block is averaged exactly. A scale in between would resample every
texture and bake the moiré into the ground.

A tile's ``pixels`` are RGBA8, opaque, row 0 on the NORTH edge — the image as a
map shows it. An engine that samples it bottom-up flips it once (Qt Quick 3D:
``flipV``).
"""

from __future__ import annotations

import math
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .record import Record
    from .scene_diff import SceneDiff

#: The side of a full tile in pixels. Measured (development box, ``QPainter`` on a
#: ``QImage``): a tile is re-baked in about 0.3 ms plus what its items cost, and four
#: of them are 4 MB to upload; at 1 px/cm it spans 5.12 m, so an item of up to 3 m
#: that is moved a little touches at most 2 × 2 tiles. 256 px made such a move nine
#: tiles; 1024 px made it 16 MB of upload.
TILE_PX: Final = 512
#: The most pixels a ground may have, all tiles together: 4096 × 4096. 64 MB of
#: RGBA8 — once in these tiles, once in the engine's textures, once (and a third
#: more, its mipmaps) on the graphics card. A plan of 24 × 16 m needs 3.8 million
#: at 1 px/cm; one of 50 × 30 m 15 million; beyond that the resolution halves.
MAX_GROUND_PIXELS: Final = 4096 * 4096
#: Albedo of plan ground no item covers, 8-bit sRGB: the ONE meadow albedo, shared
#: with the endless meadow around the plan (``quick3d.look.NEUTRAL_DAY``, pinned
#: equal by a test) — never the canvas's beige. A plan whose bare ground differs
#: from the meadow reads as an island, at night as a glowing one.
MEADOW_RGB: Final[tuple[int, int, int]] = (0x48, 0x7F, 0x34)

TileKey = tuple[int, int]
"""``(column, row)`` of a tile: columns count east from 0, rows NORTH from 0."""


@dataclass(frozen=True, slots=True)
class GroundTile:
    """One baked rectangle of the plan's ground — plain data, no Qt object.

    ``column`` / ``row``: its place in the grid (rows count north from the
    south edge). ``east_cm`` / ``north_cm``: its south-west corner in the scene;
    ``width_cm`` / ``depth_cm`` its extent east and north. ``pixels``:
    ``width_px`` × ``height_px`` RGBA8 values, row 0 on the tile's NORTH edge.

    Two tiles are equal when they are the same picture at the same place. A
    baker hands an unchanged tile out as the SAME object, so comparing a ground
    with its predecessor costs nothing for the tiles that were not re-baked.
    """

    column: int
    row: int
    east_cm: float
    north_cm: float
    width_cm: float
    depth_cm: float
    width_px: int
    height_px: int
    pixels: bytes = field(repr=False, hash=False)

    def __post_init__(self) -> None:
        if self.column < 0 or self.row < 0:
            raise ValueError(f"a tile's column and row count from 0, got {self.key}")
        if self.width_px < 1 or self.height_px < 1:
            raise ValueError(f"a tile has at least one pixel, got {self.width_px} x {self.height_px}")
        for name in ("east_cm", "north_cm", "width_cm", "depth_cm"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"GroundTile.{name} must be finite, got {getattr(self, name)!r}")
        if self.width_cm <= 0.0 or self.depth_cm <= 0.0:
            raise ValueError(f"a tile covers ground, got {self.width_cm!r} x {self.depth_cm!r} cm")
        if not isinstance(self.pixels, bytes):
            raise TypeError(f"GroundTile.pixels is bytes (RGBA8), got {type(self.pixels).__name__}")
        if len(self.pixels) != self.width_px * self.height_px * 4:
            raise ValueError(
                f"a tile of {self.width_px} x {self.height_px} px holds "
                f"{self.width_px * self.height_px * 4} bytes of RGBA8, got {len(self.pixels)}")

    @property
    def key(self) -> TileKey:
        return (self.column, self.row)


def changed_tiles(old: tuple[GroundTile, ...], new: tuple[GroundTile, ...]
                  ) -> tuple[tuple[GroundTile, ...], tuple[TileKey, ...]]:
    """``(to upload, to drop)`` when a ground goes from ``old`` to ``new``: the
    tiles of ``new`` that ``old`` does not hold as they are, and the keys only
    ``old`` has. What an engine sink does with a new ground, and what a test
    counts ("one edit re-bakes at most four tiles")."""
    held = {tile.key: tile for tile in old}
    upload = tuple(tile for tile in new
                   if (mine := held.get(tile.key)) is not tile and mine != tile)
    kept = {tile.key for tile in new}
    return upload, tuple(key for key in held if key not in kept)


@dataclass(frozen=True, slots=True)
class GroundGrid:
    """The pixel grid over one plan rectangle and its tiles (module docstring)."""

    width_cm: float
    depth_cm: float
    px_per_cm: float
    tile_px: int
    width_px: int
    height_px: int
    columns: int
    rows: int

    @classmethod
    def for_plan(cls, width_cm: float, depth_cm: float, *, tile_px: int = TILE_PX,
                 max_pixels: int = MAX_GROUND_PIXELS) -> GroundGrid:
        """The grid of a plan of ``width_cm`` × ``depth_cm``: the finest resolution
        of 1, 1/2, 1/4 … px/cm whose pixels, all tiles together, stay within
        ``max_pixels``."""
        for name, value in (("width_cm", width_cm), ("depth_cm", depth_cm)):
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"a plan's {name} must be a positive length, got {value!r}")
        if tile_px < 1 or max_pixels < 1:
            raise ValueError(f"tile_px and max_pixels must be positive, got {tile_px}, {max_pixels}")
        px_per_cm = 1.0
        while True:
            width_px = max(1, math.ceil(width_cm * px_per_cm - 1e-9))
            height_px = max(1, math.ceil(depth_cm * px_per_cm - 1e-9))
            if width_px * height_px <= max_pixels or (width_px == 1 and height_px == 1):
                break
            px_per_cm /= 2.0
        return cls(width_cm=width_cm, depth_cm=depth_cm, px_per_cm=px_per_cm, tile_px=tile_px,
                   width_px=width_px, height_px=height_px,
                   columns=-(-width_px // tile_px), rows=-(-height_px // tile_px))

    @property
    def tile_count(self) -> int:
        return self.columns * self.rows

    def tile_keys(self) -> Iterator[TileKey]:
        """Every tile, row by row from the south-west corner."""
        for row in range(self.rows):
            for column in range(self.columns):
                yield (column, row)

    def tile_px_rect(self, key: TileKey) -> tuple[int, int, int, int]:
        """``(x0, y0, width, height)`` of a tile in grid pixels; ``y0`` counts from
        the SOUTH edge of the plan."""
        column, row = key
        if not (0 <= column < self.columns and 0 <= row < self.rows):
            raise KeyError(f"no tile {key} in a grid of {self.columns} x {self.rows}")
        x0, y0 = column * self.tile_px, row * self.tile_px
        return (x0, y0, min(self.tile_px, self.width_px - x0), min(self.tile_px, self.height_px - y0))

    def tile_cm_rect(self, key: TileKey) -> tuple[float, float, float, float]:
        """``(east, north, width, depth)`` of a tile in scene centimetres — exactly
        its pixels' extent (the last column and row may reach past the plan by
        less than one pixel)."""
        x0, y0, width, height = self.tile_px_rect(key)
        scale = self.px_per_cm
        return (x0 / scale, y0 / scale, width / scale, height / scale)

    def tiles_touching(self, east0: float, north0: float, east1: float, north1: float
                       ) -> Iterator[TileKey]:
        """The tiles a scene-space box (cm) reaches into; nothing for a box that
        misses the grid. A box that only touches a tile's edge from outside does
        not reach into it."""
        if not (east0 <= east1 and north0 <= north1):  # also False for a NaN
            return
        scale, tile = self.px_per_cm, self.tile_px
        first_column = max(0, math.floor(east0 * scale / tile))
        last_column = min(self.columns - 1, math.ceil(east1 * scale / tile) - 1)
        first_row = max(0, math.floor(north0 * scale / tile))
        last_row = min(self.rows - 1, math.ceil(north1 * scale / tile) - 1)
        if east1 * scale <= 0.0 or north1 * scale <= 0.0:
            return
        for row in range(first_row, last_row + 1):
            for column in range(first_column, last_column + 1):
                yield (column, row)


@runtime_checkable
class GroundBaking(Protocol):
    """What ``SceneSync`` needs from a ground baker (``ui/view3d/ground_bake.GroundBaker``).

    Qt-free on purpose: the sync logic and its tests run without a painter, and
    a test hands in a double.
    """

    def bake(self, records: Mapping[str, Record], changes: SceneDiff | None,
             solid: Collection[str], width_cm: float, depth_cm: float
             ) -> tuple[GroundTile, ...]:
        """The ground of ``records`` (bottom to top) on a plan of ``width_cm`` ×
        ``depth_cm``: every tile, an unchanged one as the same object as last time.

        ``solid``: the ids the scene holds a solid for — those are not painted,
        whatever they carry. ``changes``: how ``records`` differ from the records
        of the PREVIOUS call (the diff the geometry path uses), or None when the
        caller cannot say; a baker may use it to look only at what changed, and
        must give the same tiles either way.
        """

    def clear(self) -> None:
        """Forget every tile: the next ``bake`` paints the whole ground anew."""

    def fresh(self) -> GroundBaking:
        """An empty baker with this one's settings (a from-scratch reference)."""
