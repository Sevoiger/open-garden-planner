"""Plant v0 — one readable silhouette per sprite archetype (Phase 17 L1.7, #385).

``build_plant`` is the pipeline's builder for the plant kinds (``TREE``,
``SHRUB``, ``PERENNIAL``): a pure function of one ``Record`` that returns the
plant's meshes in the item-local frame. What it builds is decided by the SAME
recipe the 2D sprite is drawn from (``core/plant_art``, resolved by the same
rule), and how big it is by the plan:

- **archetype** — ``recipe["a"]``: canopy, conifer, mound, climber, ground, hedge,
  rosette, head, grass, allium, feathery, flower, palm. Every archetype of the
  tables has a builder here (``ARCHETYPE_BUILDERS``; pinned by a test, so a new
  2D archetype cannot silently fall back to another one's shape);
- **colours** — only the recipe's palette for foliage, in linear light; wood is
  the 2D BARK fill texture's mean colour (the plant tables hold no wood colour:
  a top-down sprite never shows a trunk);
- **size** — the record's ``height_cm`` and canopy radius. Every plant ends in
  ``_fit``: its bounding box IS the data (top = the height, widest span = the
  canopy diameter, base at z = 0). The builders give form, the fit gives truth.

v0 is deliberately cheap: a few soft volumes and a shell of large geometric
leaves, at most ``TRIANGLE_BUDGET`` triangles per plant — bounded per ARCHETYPE
and by construction (counts are clipped functions of recipe numbers, never of
the plant's size). It makes no seasonal claim: no fruit, no flowers, no flower
heads (the season gates arrive with Package L3), and a crown is never bare.
Species-accurate models are Package L3 ("Plant Forge", #387).

A plant the plan gives NO height (``height_cm is None``: no species data, no
measurement) is not guessed: it gets the placeholder — a low, smooth, neutral
dome over its canopy circle that casts no shadow, as it casts none in 2D.

Conventions the engine relies on (``ogp-3d-renderer`` §3): vertex colours are
linear; in the foliage part ``uv.x`` is the wind sway weight (0 at the ground,
1 at the plant's top) and ``uv.y`` the sway phase in [0, 1); wood does not sway.
Foliage normals are bent toward the crown's heart so a crown shades as one soft
volume (``ogp-lush-cinematic`` §2). Randomness is ``Generator.random`` only,
seeded from ``item_seed(record.params["seed"], "plant")``.

Qt-free: numpy, ``core.plant_art`` and the scene3d package.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Any, Final, TypeAlias

import numpy as np
from numpy.typing import NDArray

from open_garden_planner.core.plant_art import (
    FLOWERS,
    PALETTES,
    PLANT_KINDS,
    SpriteRef,
    has_recipe,
    palette_key,
    recipe_for,
    resolve_sprite,
)
from open_garden_planner.core.scene3d import (
    PARAM_PLANT_CATEGORY,
    PARAM_RADIUS_CM,
    PARAM_SEED,
    PARAM_SPECIES_NAME,
    BuilderRegistry,
    Mesh,
    MeshPart,
    Record,
    default_builder,
    item_seed,
    srgb_to_linear,
)

Floats: TypeAlias = NDArray[np.float64]
Ints: TypeAlias = NDArray[np.int64]

#: Triangles one plant may have — every archetype, every size (the story's gate).
TRIANGLE_BUDGET: Final = 2000

#: ``MeshPart.name`` values.
PART_WOOD: Final = "wood"
PART_FOLIAGE: Final = "foliage"
PART_PLACEHOLDER: Final = "placeholder"
#: ``plant_archetype`` of a plant the plan gives no height.
PLACEHOLDER: Final = "placeholder"

#: Wood. The 2D plant tables hold no bark colour, so v0 takes the mean colour of
#: the 2D BARK fill texture in linear light (``resources/textures/bark.png``,
#: hue 27.6°) — pinned against the PNG by ``tests/unit/test_scene3d_plants.py``.
BARK_SRGB: Final = "#5d432d"

#: The placeholder: a spherical cap this fraction of the canopy DIAMETER high,
#: never higher than ``PLACEHOLDER_MAX_HEIGHT_CM`` — a marker, not a plant.
PLACEHOLDER_HEIGHT_FRACTION: Final = 0.12
PLACEHOLDER_MAX_HEIGHT_CM: Final = 25.0
#: Its tone: the 2D white (``FLOWERS["white"]``, the petal colour) × this in
#: linear light, sRGB 171 / 168 / 159. The L0 spike's rule for a sun-lit white is
#: × 0.7 (``ogp-lush-cinematic`` §7, the birch trunk — an upright surface); a dome
#: faces the noon sun, and at × 0.7 it rendered at luma 232 through the spike's
#: engine (scratch look, 2026-10-11): unclipped, but the brightest thing in the
#: garden. A marker should not out-shine the plants.
PLACEHOLDER_SHADE: Final = 0.5
_PLACEHOLDER_SIDES: Final = 48

#: How much of its palette's TIP tone an archetype's foliage shows, on average
#: (0 = the base anchors b0/b1, 1 = the tip anchors t0/t1). Measured from the 2D
#: sprites, 2026-10-11: the tip weight whose mix has the hue of the sprite's
#: rendered foliage, median per archetype over all 123 sprites. It is what keeps
#: the 3D foliage within 10° of the sprite's hue (a palette spans up to 41°).
FOLIAGE_TIP_WEIGHT: Final[Mapping[str, float]] = MappingProxyType({
    "allium": 0.65,
    "canopy": 0.42,
    "climber": 0.36,
    "conifer": 0.12,
    "feathery": 0.36,
    "flower": 0.21,
    "grass": 0.40,
    "ground": 0.05,
    "head": 0.29,
    "hedge": 0.24,
    "mound": 0.38,
    "palm": 0.54,
    "rosette": 0.31,
})
#: Spread of the tip weight inside one plant: exposure 0 … 1 covers this much of
#: the palette around the archetype's mean.
_TONE_CONTRAST: Final = 0.9
#: Normal bending toward the crown's heart (``ogp-lush-cinematic`` §2: 0.55–0.75).
_SPHERIZE: Final = 0.6
_LEAF_SPHERIZE: Final = 0.7
#: The elements that form a plant's OUTLINE (a rosette's outer leaves, a palm's
#: fronds, an allium's outer blades, a grass tuft's longest blades) reach at
#: least this fraction of the canopy radius and sit within this fraction of an
#: even slot round the axis. A ragged outline cannot fill its canopy circle:
#: with blades of any reach at any angle, the fitted plant left the circle by up
#: to 39 % (feathery; allium 37 %, flower 23 %) — or, scaled back into it,
#: spanned only 72 % of the canopy diameter. Inner elements stay irregular.
_RIM_REACH: Final = 0.97
_RIM_JITTER: Final = 0.10
_TAU: Final = 2.0 * math.pi
_UP: Final[Floats] = np.array([0.0, 0.0, 1.0])


# ── the working mesh ──────────────────────────────────────────────────────────


@dataclass(slots=True)
class _Geo:
    """Geometry before the fit, in centimetres at the plant's real proportions.

    Per vertex: ``exposure`` (0 = deep inside or at the base, 1 = an outer tip —
    it becomes the palette's base → tip mix), ``shade`` (a hue-neutral depth
    darkening), ``tone`` (which of a palette's two anchor pairs: outer 0 … inner
    1) and the wind ``phase``.
    """

    pos: Floats
    nrm: Floats
    tri: Ints
    exposure: Floats
    shade: Floats
    tone: Floats
    phase: Floats


def _geo(pos: Floats, nrm: Floats, tri: Ints, exposure: Floats | float,
         shade: Floats | float, tone: Floats | float, phase: Floats | float) -> _Geo:
    count = len(pos)

    def full(value: Floats | float) -> Floats:
        return np.broadcast_to(np.asarray(value, np.float64), (count,)).copy()

    return _Geo(np.asarray(pos, np.float64).reshape(-1, 3),
                np.asarray(nrm, np.float64).reshape(-1, 3),
                np.asarray(tri, np.int64).reshape(-1, 3),
                full(exposure), full(shade), full(tone), full(phase))


def _join(parts: Sequence[_Geo]) -> _Geo:
    offsets = np.cumsum([0] + [len(part.pos) for part in parts[:-1]])
    return _Geo(
        np.concatenate([part.pos for part in parts]),
        np.concatenate([part.nrm for part in parts]),
        np.concatenate([part.tri + int(offset) for part, offset in zip(parts, offsets, strict=True)]),
        np.concatenate([part.exposure for part in parts]),
        np.concatenate([part.shade for part in parts]),
        np.concatenate([part.tone for part in parts]),
        np.concatenate([part.phase for part in parts]),
    )


class _Dice:
    """The plant's own randomness — uniform numbers only, from one seeded stream."""

    def __init__(self, seed: int) -> None:
        self._rng = np.random.default_rng(seed)

    def u(self, low: float, high: float, size: int | tuple[int, ...]) -> Floats:
        return low + (high - low) * self._rng.random(size)

    def uf(self, low: float, high: float) -> float:
        return low + (high - low) * float(self._rng.random())


@dataclass(slots=True)
class _Plan:
    """What an archetype builder is given: the plant's real size, its recipe, its dice."""

    height: float
    radius: float
    recipe: Mapping[str, Any]
    dice: _Dice
    phase: float

    def number(self, key: str, default: float, low: float, high: float) -> float:
        """A recipe number, clipped — a recipe cannot push a count past its budget."""
        value = self.recipe.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            value = default
        return float(min(max(float(value), low), high))

    def flag(self, key: str, default: bool = False) -> bool:
        return bool(self.recipe.get(key, default))

    def leaf_scale(self) -> float:
        """The 2D table's per-species leaf size (0.55 thyme … 1.35 pumpkin)."""
        return self.number("leaf_scale", 1.0, 0.5, 1.5)

    def shell_leaves(self, base: int, default_n_out: float, low: int, high: int) -> int:
        """How many shell leaves: more for a fine-leaved recipe (small ``leaf_scale``,
        large ``n_out``), fewer and larger for a coarse one — coverage stays; clipped."""
        density = self.number("n_out", default_n_out, 1.0, 1000.0) / default_n_out
        count = base * min(max(density, 0.6), 1.5) / self.leaf_scale() ** 2
        return int(min(max(round(count), low), high))


_Shape: TypeAlias = tuple[_Geo | None, _Geo]  # (wood, foliage)
_ArchetypeBuilder: TypeAlias = Callable[[_Plan], _Shape]


# ── primitives (vectorised; no Python loop over vertices) ─────────────────────


def _unit(vectors: Floats) -> Floats:
    norm = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return np.asarray(vectors / np.maximum(norm, 1e-12), np.float64)


def _srgb_to_linear(srgb: Floats) -> Floats:
    """sRGB 0..1 → linear light, vectorised (the pipeline's transfer function,
    ``core.scene3d.srgb_channel_to_linear``; pinned equal by a test)."""
    return np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)


def _octasphere(subdivisions: int) -> tuple[Floats, Ints]:
    """A unit sphere from a subdivided octahedron: 32 triangles at 1, 128 at 2."""
    vertices: list[tuple[float, float, float]] = [
        (1.0, 0.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
        (0.0, -1.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, -1.0),
    ]
    faces = [(0, 2, 4), (2, 1, 4), (1, 3, 4), (3, 0, 4), (2, 0, 5), (1, 2, 5), (3, 1, 5), (0, 3, 5)]
    for _ in range(subdivisions):
        middles: dict[tuple[int, int], int] = {}

        def middle(i: int, j: int, cache: dict[tuple[int, int], int] = middles) -> int:
            key = (min(i, j), max(i, j))
            if key not in cache:
                x, y, z = (vertices[i][axis] + vertices[j][axis] for axis in range(3))
                length = math.sqrt(x * x + y * y + z * z)
                vertices.append((x / length, y / length, z / length))
                cache[key] = len(vertices) - 1
            return cache[key]

        split = []
        for a, b, c in faces:
            ab, bc, ca = middle(a, b), middle(b, c), middle(c, a)
            split += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        faces = split
    return np.array(vertices, np.float64), np.array(faces, np.int64)


_SPHERE_COARSE: Final = _octasphere(1)   # 18 vertices, 32 triangles
_SPHERE: Final = _octasphere(2)          # 66 vertices, 128 triangles


def _lobes(centers: Floats, radii: Floats, heart: Floats, dice: _Dice, phase: float,
           sphere: tuple[Floats, Ints] = _SPHERE) -> _Geo:
    """Soft ellipsoid volumes — the mass of a crown, a shrub or a cushion.

    Vertices below the ground are pressed onto it; normals are the ellipsoid's,
    bent toward the direction away from ``heart``; the further from the heart
    and the more upward a vertex looks, the lighter it is.
    """
    unit_vertices, faces = sphere
    count = len(centers)
    pos = unit_vertices[None, :, :] * radii[:, None, :] + centers[:, None, :]
    own = _unit(unit_vertices[None, :, :] / radii[:, None, :])
    pos[:, :, 2] = np.maximum(pos[:, :, 2], 0.0)
    away = pos - heart[None, None, :]
    distance = np.linalg.norm(away, axis=2)
    depth = np.clip(distance / max(float(distance.max()), 1e-12), 0.0, 1.0)
    nrm = _unit(own * (1.0 - _SPHERIZE) + _unit(away) * _SPHERIZE)
    exposure = 0.10 + 0.40 * depth + 0.25 * (0.5 + 0.5 * own[:, :, 2])
    tone = np.repeat(_stratified(dice, count), len(unit_vertices))
    tri = faces[None, :, :] + (np.arange(count) * len(unit_vertices))[:, None, None]
    return _geo(pos.reshape(-1, 3), nrm.reshape(-1, 3), tri.reshape(-1, 3),
                exposure.reshape(-1), (0.62 + 0.38 * depth).reshape(-1), tone, phase)


def _stratified(dice: _Dice, count: int) -> Floats:
    """``count`` tones spread evenly over 0 … 1 in a random order: a handful of
    lobes then still averages the palette's two anchor pairs."""
    order = np.argsort(dice.u(0.0, 1.0, count))
    tones = np.empty(count)
    tones[order] = (np.arange(count) + 0.5) / count
    return tones


def _leaves(dice: _Dice, base: Floats, outward: Floats, length: Floats, aspect: float,
            heart: Floats, phase: float, droop: float = 0.15) -> _Geo:
    """Large geometric leaves (a diamond: 4 vertices, 2 triangles) facing ``outward``.

    The shell that breaks a volume's outline. Each leaf lies across its facing
    direction, hangs by ``droop``, and is lightest at its tip.
    """
    count = len(base)
    facing = _unit(outward + dice.u(-0.35, 0.35, (count, 3)))
    spin = dice.u(-1.0, 1.0, (count, 3))
    along = _unit(spin - (spin * facing).sum(axis=1, keepdims=True) * facing)
    along = _unit(along - np.array([0.0, 0.0, droop]))
    side = np.cross(along, facing)
    reach = length[:, None]
    half_width = reach * aspect * 0.5
    pos = np.stack([
        base,
        base + along * reach * 0.45 + side * half_width,
        base + along * reach,
        base + along * reach * 0.45 - side * half_width,
    ], axis=1).reshape(-1, 3)
    nrm = _unit(facing * (1.0 - _LEAF_SPHERIZE) + _unit(base - heart[None, :]) * _LEAF_SPHERIZE)
    tri = np.array([[0, 1, 2], [0, 2, 3]], np.int64)[None, :, :] + (
        np.arange(count) * 4)[:, None, None]
    return _geo(pos, np.repeat(nrm, 4, axis=0), tri.reshape(-1, 3),
                np.tile([0.55, 0.80, 1.00, 0.80], count),
                np.repeat(dice.u(0.88, 1.0, count), 4),
                np.repeat(dice.u(0.0, 1.0, count), 4),
                (phase + np.repeat(dice.u(-0.1, 0.1, count), 4)) % 1.0)


def _lobe_surface(origin: Floats, reach: Floats, centers: Floats, radii: Floats) -> Floats:
    """Where each ray ``origin + t · reach`` leaves the outermost lobe: t per ray, 0 for
    a ray that meets no lobe. Shell leaves are set on that surface — on an envelope
    instead they were buried where a lobe reached it (bald crown tops in the first
    look through an engine) and hovered in the gaps between lobes."""
    step = reach[:, None, :] / radii[None, :, :]                       # (rays, lobes, 3)
    start = ((origin[None, :] - centers) / radii)[None, :, :]
    a = (step * step).sum(axis=2)
    b = 2.0 * (step * start).sum(axis=2)
    c = (start * start).sum(axis=2) - 1.0
    root = b * b - 4.0 * a * c
    far = np.where(root >= 0.0, (-b + np.sqrt(np.maximum(root, 0.0))) / (2.0 * np.maximum(a, 1e-300)), 0.0)
    return np.asarray(np.maximum(far.max(axis=1), 0.0), np.float64)


def _on_lobes(dice: _Dice, origin: Floats, reach: Floats, centers: Floats, radii: Floats) -> Floats:
    """Points on the lobes' outer surface along the rays (a little in or out, so the
    shell is not a film); a ray that meets no lobe keeps its own reach."""
    distance = _lobe_surface(origin, reach, centers, radii)
    distance = np.where(distance > 0.0, distance, 1.0) * dice.u(0.96, 1.05, len(reach))
    return np.asarray(origin[None, :] + reach * distance[:, None], np.float64)


def _shell_points(dice: _Dice, count: int, lowest: float) -> Floats:
    """``count`` unit directions on the part of a sphere above ``lowest`` (z)."""
    z = dice.u(lowest, 1.0, count)
    angle = dice.u(0.0, _TAU, count)
    flat = np.sqrt(np.maximum(1.0 - z * z, 0.0))
    return np.stack([flat * np.cos(angle), flat * np.sin(angle), z], axis=1)


def _ribbons(base: Floats, direction: Floats, reach: Floats, height: Floats, width: Floats,
             segments: int, tone: Floats, phase: Floats, arch: float = 0.0) -> _Geo:
    """Blades: each rises ``height`` from ``base`` and leans ``reach`` along its
    (horizontal) ``direction``, tapering to the tip; ``arch`` lets the tip nod."""
    count = len(base)
    f = np.linspace(0.0, 1.0, segments + 1)
    along = np.concatenate([direction, np.zeros((count, 1))], axis=1)
    rise = f - arch * f ** 3
    centre = (base[:, None, :]
              + along[:, None, :] * (reach[:, None] * f[None, :] ** 2)[:, :, None]
              + _UP[None, None, :] * (height[:, None] * rise[None, :])[:, :, None])
    tangent = (along[:, None, :] * (reach[:, None] * 2.0 * f[None, :])[:, :, None]
               + _UP[None, None, :] * (height[:, None] * (1.0 - 3.0 * arch * f[None, :] ** 2))[:, :, None])
    side = np.stack([-direction[:, 1], direction[:, 0], np.zeros(count)], axis=1)  # up × direction
    half = (width[:, None] * (1.0 - 0.85 * f[None, :]) * 0.5)[:, :, None]
    left = centre + side[:, None, :] * half
    right = centre - side[:, None, :] * half
    face = _unit(np.cross(np.broadcast_to(side[:, None, :], tangent.shape), tangent))
    nrm = _unit(face * 0.7 + _UP[None, None, :] * 0.3)
    pos = np.stack([left, right], axis=2).reshape(count, -1, 3)       # per blade: L0 R0 L1 R1 …
    step = np.arange(segments) * 2
    quad = np.stack([step + 1, step, step + 2, step + 1, step + 2, step + 3], axis=1).reshape(-1, 3)
    tri = quad[None, :, :] + (np.arange(count) * (segments + 1) * 2)[:, None, None]
    per_vertex = (segments + 1) * 2
    return _geo(pos.reshape(-1, 3), np.repeat(nrm, 2, axis=1).reshape(-1, 3), tri.reshape(-1, 3),
                np.tile(np.repeat(f, 2), count),
                np.tile(np.repeat(0.72 + 0.28 * f, 2), count),
                np.repeat(tone, per_vertex), np.repeat(phase, per_vertex))


#: A leaf's outline: its width at evenly spaced cross-sections from base to tip, as
#: fractions of its full width. Broad = a lettuce or cabbage leaf, round at the tip;
#: lance = a frond or a strap leaf, widest low down and pointed.
_BROAD_LEAF: Final[Floats] = np.array([0.18, 0.80, 1.00, 0.74, 0.18])
_LANCE_LEAF: Final[Floats] = np.array([0.25, 0.98, 0.86, 0.58, 0.28, 0.06])
_FROND: Final[Floats] = np.array([0.25, 0.60, 0.98, 0.50, 0.80, 0.35, 0.06])  # notched


def _fronds(base: Floats, tip: Floats, width: Floats, bow: Floats, outline: Floats,
            tone: Floats, phase: Floats, fold: float = 0.18, ripple: float = 0.0) -> _Geo:
    """Leaves with a folded midrib: three vertices across (edge, rib, edge).

    Each runs from ``base`` to ``tip`` with the given ``outline`` and bows up by
    ``bow`` × its length; ``ripple`` waves the edges (a frilly leaf).
    """
    count = len(base)
    segments = len(outline) - 1
    f = np.linspace(0.0, 1.0, segments + 1)
    chord = tip - base
    length = np.linalg.norm(chord, axis=1)
    lift = (bow * length)[:, None]
    centre = base[:, None, :] + chord[:, None, :] * f[None, :, None]
    centre[:, :, 2] += lift * np.sin(np.pi * f)[None, :]
    tangent = np.broadcast_to(chord[:, None, :], centre.shape).copy()
    tangent[:, :, 2] += lift * np.pi * np.cos(np.pi * f)[None, :]
    side = np.stack([-chord[:, 1], chord[:, 0], np.zeros(count)], axis=1)          # up × chord
    flat = np.linalg.norm(side, axis=1) < 1e-9 * np.maximum(length, 1e-12)
    side[flat] = [1.0, 0.0, 0.0]
    side = _unit(side)[:, None, :]
    face = _unit(np.cross(tangent, np.broadcast_to(side, tangent.shape)))          # the upper side
    half = (width[:, None] * outline[None, :] * 0.5)[:, :, None]
    grown = np.minimum(1.0, 3.0 * f)   # a leaf leaves its stem flat: ripple and fold grow in
    wave = (ripple * grown[None, :] * half[:, :, 0]
            * np.sin(3.0 * np.pi * f[None, :] + _TAU * tone[:, None]))[:, :, None]
    left = centre + side * half + _UP[None, None, :] * wave
    right = centre - side * half - _UP[None, None, :] * wave
    rib = centre - face * (2.0 * fold) * half * grown[None, :, None]
    pos = np.stack([left, rib, right], axis=2).reshape(count, -1, 3)   # per leaf: L0 M0 R0 L1 …
    lean = 2.0 * fold
    nrm = np.stack([_unit(face - side * lean), face, _unit(face + side * lean)], axis=2)
    step = np.arange(segments) * 3
    quad = np.stack([
        step + 1, step + 3, step,          # rib, next edge, edge
        step + 1, step + 4, step + 3,      # rib, next rib, next edge
        step + 2, step + 4, step + 1,      # other edge, next rib, rib
        step + 2, step + 5, step + 4,      # other edge, next other edge, next rib
    ], axis=1).reshape(-1, 3)
    tri = quad[None, :, :] + (np.arange(count) * (segments + 1) * 3)[:, None, None]
    exposure = np.stack([0.35 + 0.65 * f, 0.20 + 0.65 * f, 0.35 + 0.65 * f], axis=1).reshape(-1)
    per_vertex = (segments + 1) * 3
    return _geo(pos.reshape(-1, 3), nrm.reshape(-1, 3), tri.reshape(-1, 3),
                np.tile(exposure, count), np.tile(np.repeat(0.78 + 0.22 * f, 3), count),
                np.repeat(tone, per_vertex), np.repeat(phase, per_vertex))


def _revolve(radius: Floats, z: Floats, sides: int, apex: float | None = None,
             underside: float | None = None) -> tuple[Floats, Ints]:
    """A surface of revolution about the plant's axis: rings bottom to top.

    ``radius`` and ``z`` are per ring (m,) or per ring and side (m, sides) — a
    star rim is a ring whose radius alternates. ``apex`` closes the top with a
    point at that height, ``underside`` the bottom with one (facing down).
    Vertices: the rings (ring-major), then the apex, then the underside point.
    """
    rings = len(radius)
    angle = _TAU * np.arange(sides) / sides
    r = np.broadcast_to(np.asarray(radius, np.float64).reshape(rings, -1), (rings, sides))
    height = np.broadcast_to(np.asarray(z, np.float64).reshape(rings, -1), (rings, sides))
    pos = np.stack([r * np.cos(angle)[None, :], r * np.sin(angle)[None, :], height], axis=2)
    vertices = [pos.reshape(-1, 3)]
    j = np.arange(sides)
    k = (j + 1) % sides
    i = np.arange(rings - 1)[:, None] * sides
    a, b, c, e = i + j[None, :], i + k[None, :], i + sides + k[None, :], i + sides + j[None, :]
    faces = [np.stack([a, b, c], axis=2).reshape(-1, 3), np.stack([a, c, e], axis=2).reshape(-1, 3)]
    extra = rings * sides
    if apex is not None:
        top = (rings - 1) * sides
        vertices.append(np.array([[0.0, 0.0, apex]]))
        faces.append(np.stack([top + j, top + k, np.full(sides, extra)], axis=1))
        extra += 1
    if underside is not None:
        vertices.append(np.array([[0.0, 0.0, underside]]))
        faces.append(np.stack([k, j, np.full(sides, extra)], axis=1))
    return np.concatenate(vertices), np.concatenate(faces).astype(np.int64)


def _smooth_normals(pos: Floats, tri: Ints) -> Floats:
    """Area-weighted vertex normals of a triangle soup."""
    cross = np.cross(pos[tri[:, 1]] - pos[tri[:, 0]], pos[tri[:, 2]] - pos[tri[:, 0]])
    flat = tri.reshape(-1)
    summed = np.stack([
        np.bincount(flat, weights=np.repeat(cross[:, axis], 3), minlength=len(pos))
        for axis in range(3)
    ], axis=1)
    return _unit(np.asarray(summed, np.float64))


def _vertex_areas(pos: Floats, tri: Ints) -> Floats:
    """The surface area each vertex stands for (a third of its triangles')."""
    cross = np.cross(pos[tri[:, 1]] - pos[tri[:, 0]], pos[tri[:, 2]] - pos[tri[:, 0]])
    area = 0.5 * np.linalg.norm(cross, axis=1)
    return np.asarray(np.bincount(tri.reshape(-1), weights=np.repeat(area / 3.0, 3),
                                  minlength=len(pos)), np.float64)


def _trunk(top: float, radius: float, taper: tuple[float, float, float, float],
           sides: int = 8) -> _Geo:
    """A tapered trunk with a root flare, open at the top (it ends inside the crown).

    ``taper``: radius at the ground, above the flare, half way up and at the top,
    as fractions of ``radius``.
    """
    z = np.array([0.0, 0.06, 0.5, 1.0]) * top
    pos, tri = _revolve(np.array(taper) * radius, z, sides)
    return _geo(pos, _smooth_normals(pos, tri), tri, 0.0, 1.0, 0.0, 0.0)


def _lumps(dice: _Dice, amount: float) -> Callable[[Floats], Floats]:
    """A smooth closed curve round the axis, between ``1 - amount`` and 1: two low
    harmonics with a random weight and random phases. Returns curve(angle)."""
    weight, first, second = dice.uf(0.3, 0.7), dice.uf(0.0, _TAU), dice.uf(0.0, _TAU)

    def at(angle: Floats) -> Floats:
        wave = weight * np.sin(2.0 * angle + first) + (1.0 - weight) * np.sin(3.0 * angle + second)
        return np.asarray(1.0 - amount * (0.5 + 0.5 * wave), np.float64)

    return at


def _even_angles(dice: _Dice, count: int, jitter: float) -> Floats:
    """``count`` directions round the axis, one per equal slot, each off its slot's
    middle by at most ``jitter`` slots; the whole ring turned at random."""
    return dice.uf(0.0, _TAU) + _TAU * (np.arange(count) + dice.u(-jitter, jitter, count)) / count


# ── the archetypes ────────────────────────────────────────────────────────────


def _canopy(p: _Plan) -> _Shape:
    """A deciduous tree: a tapered trunk and a crown of seven soft lobes in a leaf shell."""
    h, r, d = p.height, p.radius, p.dice
    base = h * d.uf(0.21, 0.33)          # where the crown starts: no two trees alike
    half = (h - base) / 2.0
    heart = np.array([0.0, 0.0, base + half])
    # five lobes round the crown, each reaching the canopy circle on its own side:
    # together they are the crown's outline (the leaf shell sits on them)
    ring = _even_angles(d, 5, _RIM_JITTER)
    out = r * d.u(0.30, 0.40, 5)
    wide = r * d.u(_RIM_REACH, 1.0, 5) - out
    centers = np.concatenate([
        np.stack([out * np.cos(ring), out * np.sin(ring),
                  heart[2] + half * d.u(-0.38, 0.16, 5)], axis=1),
        np.array([[r * d.uf(-0.08, 0.08), r * d.uf(-0.08, 0.08), heart[2] + half * d.uf(0.36, 0.50)],
                  [0.0, 0.0, heart[2] - 0.30 * half]]),
    ])
    top = r * d.uf(0.56, 0.66)
    radii = np.concatenate([
        np.stack([wide, wide, half * d.u(0.55, 0.72, 5)], axis=1),
        np.array([[top, top, 0.52 * half], [0.62 * r, 0.62 * r, 0.62 * half]]),
    ])
    lobes = _lobes(centers, radii, heart, d, p.phase)
    count = p.shell_leaves(300, 18.0, 160, 480)
    direction = _shell_points(d, count, -0.55)
    shell = _on_lobes(d, heart, direction * np.array([r, r, half])[None, :], centers, radii)
    size = 0.265 * p.leaf_scale() * math.sqrt(r * max(r, half))
    leaves = _leaves(d, shell, direction + np.array([0.0, 0.0, 0.25]),
                     size * d.u(0.8, 1.25, count), 0.75 if p.flag("frilly") else 0.6,
                     heart, p.phase)
    trunk = _trunk(base + 0.35 * half, min(0.035 * h, 0.22 * r), (1.45, 1.0, 0.86, 0.62))
    return trunk, _join([lobes, leaves])


def _conifer(p: _Plan) -> _Shape:
    """A conifer: a short trunk and a stack of star-rimmed cone tiers, narrowing to a leader.

    The 2D sprite is four interleaved whorls of 14, 12, 10 and 8 serrated sprays
    seen from above; here the whorls are tiers, three to six by the tree's proportions.
    """
    h, r, d = p.height, p.radius, p.dice
    tiers = int(min(max(round(2.0 + 1.6 * h / (2.0 * r)), 3), 6))
    foot = 0.10 * h
    step = (h - foot) / tiers
    parts: list[_Geo] = []
    for tier in range(tiers):
        points = max(14 - 2 * tier, 8)
        sides = 2 * points
        # no two conifers alike: each whorl sits a little higher or lower, reaches a
        # little less far, is cut a little deeper and hangs a little more — except
        # the lowest, which is the outline
        z0 = foot + step * (tier + (0.0 if tier == 0 else d.uf(-0.18, 0.18)))
        reach = r * (1.0 - 0.84 * tier / tiers) ** 0.9 * (1.0 if tier == 0 else d.uf(0.84, 1.0))
        tip = np.arange(sides) % 2 == 0
        last = tier == tiers - 1
        tier_height = (h - z0) if last else 1.55 * step
        rim = np.where(tip, reach, d.uf(0.52, 0.70) * reach)
        hang = d.uf(0.05, 0.18)
        rings_r = np.stack([rim, np.where(tip, 0.60, 0.50) * reach, np.full(sides, 0.20 * reach)])
        rings_z = np.stack([
            z0 + np.where(tip, -hang, 0.10) * step,
            np.full(sides, z0 + 0.42 * tier_height),
            np.full(sides, z0 + (0.80 if last else 1.0) * tier_height),
        ])
        pos, tri = _revolve(rings_r, rings_z, sides, apex=h if last else None,
                            underside=z0 + 0.22 * step)
        turn = d.uf(0.0, _TAU)
        cos, sin = math.cos(turn), math.sin(turn)
        pos[:, :2] = pos[:, :2] @ np.array([[cos, sin], [-sin, cos]])
        flat = pos[:, :2]
        outward = np.concatenate([flat, 0.45 * np.linalg.norm(flat, axis=1, keepdims=True)], axis=1)
        nrm = _unit(_smooth_normals(pos, tri) * 0.5 + _unit(outward + [0.0, 0.0, 1e-9]) * 0.5)
        nrm[-1] = [0.0, 0.0, -1.0]   # the underside's centre looks down
        exposure = np.concatenate([np.where(tip, 1.0, 0.60), np.full(sides, 0.50),
                                   np.full(sides, 0.25), [0.9] if last else [], [0.0]])
        shade = np.concatenate([np.full(sides, 1.0), np.full(sides, 0.90), np.full(sides, 0.74),
                                [1.0] if last else [], [0.62]])
        parts.append(_geo(pos, nrm, tri, exposure, shade, (tier + 0.5) / tiers, p.phase))
    # the trunk ends inside the top whorls, tapered to a twig: a thicker one poked
    # out above the leader as a bare stub (the L0 spike's spruce)
    trunk = _trunk(0.88 * h, min(0.02 * h, 0.2 * r), (1.4, 1.0, 0.55, 0.12))
    return trunk, _join(parts)


def _dome(p: _Plan, centers: Floats, radii: Floats, sphere: tuple[Floats, Ints], leaf_base: int,
          default_n_out: float, most: int, heart: Floats, envelope: Floats, lowest: float,
          size: float) -> _Geo:
    """Lobes sitting on the ground plus a leaf shell on their surface, cast from
    ``heart`` in the directions of the ellipsoid ``envelope`` above ``lowest``."""
    d = p.dice
    lobes = _lobes(centers, radii, heart, d, p.phase, sphere)
    count = p.shell_leaves(leaf_base, default_n_out, 150, most)
    direction = _shell_points(d, count, lowest)
    shell = _on_lobes(d, heart, direction * envelope[None, :], centers, radii)
    shell[:, 2] = np.maximum(shell[:, 2], 0.0)
    narrow, frilly = p.flag("narrow"), p.flag("frilly")
    length = size * p.leaf_scale() * (1.25 if narrow else 1.0) * d.u(0.8, 1.25, count)
    leaves = _leaves(d, shell, direction + np.array([0.0, 0.0, 0.35]), length,
                     0.30 if narrow else 0.80 if frilly else 0.60, heart, p.phase,
                     droop=-0.25 if narrow else 0.15)
    return _join([lobes, leaves])


def _mound(p: _Plan) -> _Shape:
    """A shrub, a bushy perennial, a herb: six lobes sitting on the ground in a leaf shell.

    It leans: the lobes on one side — a different side per plant — are up to 40 %
    taller, and the top sits off-centre toward them. Without the lean two cushions
    of one size were 97 % alike from the side.
    """
    h, r, d = p.height, p.radius, p.dice
    heart = np.array([0.0, 0.0, 0.30 * h])
    # five lobes round the plant, each reaching the canopy circle on its own side
    ring = _even_angles(d, 5, _RIM_JITTER)
    out = r * d.u(0.28, 0.38, 5)
    wide = r * d.u(_RIM_REACH, 1.0, 5) - out
    toward = d.uf(0.0, _TAU)
    taller = 1.0 + d.uf(0.15, 0.40) * np.cos(ring - toward)
    off = r * d.uf(0.05, 0.18)
    centers = np.concatenate([
        np.stack([out * np.cos(ring), out * np.sin(ring), h * d.u(0.16, 0.36, 5) * taller], axis=1),
        np.array([[off * math.cos(toward), off * math.sin(toward), h * d.uf(0.42, 0.50)]]),
    ])
    core = r * d.uf(0.52, 0.78)
    radii = np.concatenate([
        np.stack([wide, wide, h * d.u(0.28, 0.50, 5) * taller], axis=1),
        np.array([[core, core, h * d.uf(0.48, 0.56)]]),
    ])
    size = 0.27 * math.sqrt(r * max(r, 0.62 * h))
    return None, _dome(p, centers, radii, _SPHERE, 300, 16.0, 420, heart,
                       np.array([r, r, 0.70 * h]), -0.30, size)


def _climber(p: _Plan) -> _Shape:
    """A climber without its support: six lobes on a helix — a leafy pillar when the
    plan makes it tall, a sprawling mat when it makes it wide — with tendril curls.

    The helix makes one turn over the plant's height and every lobe reaches the
    canopy circle on its own side: together the six are the outline.
    """
    h, r, d = p.height, p.radius, p.dice
    index = np.arange(6)
    angle = d.uf(0.0, _TAU) + _TAU * (index + d.u(-_RIM_JITTER, _RIM_JITTER, 6)) / 6.0
    out = r * d.u(0.26, 0.38, 6)
    wide = r * d.u(_RIM_REACH, 1.0, 6) - out
    centers = np.stack([out * np.cos(angle), out * np.sin(angle),
                        h * (0.14 + 0.72 * index / 5.0)], axis=1)
    radii = np.stack([wide, wide, np.full(6, 0.20 * h)], axis=1)
    heart = np.array([0.0, 0.0, 0.5 * h])
    lobes = _lobes(centers, radii, heart, d, p.phase)
    count = p.shell_leaves(280, 16.0, 140, 400)
    z = h * d.u(0.02, 0.98, count)
    turn = d.u(0.0, _TAU, count)
    radial = np.stack([np.cos(turn), np.sin(turn), np.zeros(count)], axis=1)
    # each leaf where a horizontal ray from the axis at its height leaves the lobes
    step = radial[:, None, :2] / radii[None, :, :2]
    start = (-centers[None, :, :2]) / radii[None, :, :2]
    a = (step * step).sum(axis=2)
    b = 2.0 * (step * start).sum(axis=2)
    c = (start * start).sum(axis=2) + ((z[:, None] - centers[None, :, 2]) / radii[None, :, 2]) ** 2 - 1.0
    root = b * b - 4.0 * a * c
    far = np.where(root >= 0.0, (-b + np.sqrt(np.maximum(root, 0.0))) / (2.0 * a), 0.0).max(axis=1)
    reach = np.where(far > 0.0, far, 0.3 * r) * d.u(0.96, 1.05, count)
    shell = radial * reach[:, None] + _UP[None, :] * z[:, None]
    size = 0.27 * p.leaf_scale() * math.sqrt(r * max(r, 0.5 * h))
    leaves = _leaves(d, shell, radial + np.array([0.0, 0.0, 0.25]), size * d.u(0.8, 1.25, count),
                     0.80 if p.flag("frilly") else 0.60, heart, p.phase)
    curl = d.u(0.0, _TAU, 5)
    start = np.stack([0.3 * r * np.cos(curl), 0.3 * r * np.sin(curl), h * d.u(0.72, 0.90, 5)], axis=1)
    tendrils = _ribbons(start, np.stack([np.cos(curl + 1.3), np.sin(curl + 1.3)], axis=1),
                        r * d.u(0.25, 0.45, 5), h * d.u(0.06, 0.10, 5), np.full(5, 0.05 * r), 4,
                        np.full(5, 0.5), np.full(5, p.phase))
    return None, _join([lobes, leaves, tendrils])


def _ground(p: _Plan) -> _Shape:
    """A ground cover: a low mat of nine small bumps under a dense shell of leaves.

    The eight outer bumps each reach the canopy circle: they are the mat's outline.
    """
    h, r, d = p.height, p.radius, p.dice
    ring = _even_angles(d, 8, 0.2)
    out = np.concatenate([r * d.u(0.56, 0.64, 8), [0.0]])
    angle = np.concatenate([ring, [0.0]])
    wide = np.concatenate([r * d.u(_RIM_REACH, 1.0, 8) - out[:8], [r * d.uf(0.34, 0.42)]])
    centers = np.stack([out * np.cos(angle), out * np.sin(angle), h * d.u(0.12, 0.25, 9)], axis=1)
    radii = np.stack([wide, wide, h * d.u(0.55, 0.85, 9)], axis=1)
    heart = np.array([0.0, 0.0, 0.2 * h])
    return None, _dome(p, centers, radii, _SPHERE_COARSE, 420, 16.0, 500, heart,
                       np.array([r, r, 0.8 * h]), -0.15, 0.20 * r)


def _hedge(p: _Plan) -> _Shape:
    """A clipped hedge section: a drum with a bevelled, nearly flat top in a fine leaf shell.

    A drum, not a box: the record is a circle and a circle has no heading, so a
    box's orientation would be invented. It is cut by hand, not turned on a
    lathe — narrower at the top than at the foot (the batter a hedge is cut
    with), its side bulging and its top dipping a little, differently on every
    section: two perfect drums were the same drum (side silhouettes 99.9 % alike).
    """
    h, r, d = p.height, p.radius, p.dice
    sides = 20
    theta = _TAU * np.arange(sides) / sides
    bulge, dip = _lumps(d, 0.10), _lumps(d, 0.14)
    batter = d.uf(0.80, 0.98)            # radius at the shoulder / radius at the foot
    side, top = bulge(theta), dip(theta)
    crown = float(top.mean())
    pos, tri = _revolve(np.stack([side, batter * side, 0.94 * batter * side, 0.82 * batter * side]) * r,
                        np.stack([np.zeros(sides), 0.86 * top, 0.96 * top, top]) * h,
                        sides, apex=crown * h)
    heart = np.array([0.0, 0.0, 0.45 * h])
    nrm = _unit(_smooth_normals(pos, tri) * (1.0 - _SPHERIZE) + _unit(pos - heart[None, :]) * _SPHERIZE)
    ring_exposure = np.repeat([0.05, 0.40, 0.55, 0.60], sides)
    drum = _geo(pos, nrm, tri, np.concatenate([ring_exposure, [0.60]]),
                np.concatenate([np.repeat([0.66, 0.90, 1.0, 1.0], sides), [1.0]]), 0.5, p.phase)
    count = p.shell_leaves(380, 16.0, 150, 460)
    on_top = d.u(0.0, 1.0, count) < 0.28
    turn = d.u(0.0, _TAU, count)
    radial = np.stack([np.cos(turn), np.sin(turn), np.zeros(count)], axis=1)
    inward = np.where(on_top, np.sqrt(d.u(0.0, 1.0, count)), 1.0)     # 1 = at the rim
    up_side = d.u(0.05, 1.0, count)                                     # 1 = at the shoulder
    reach = r * bulge(turn) * np.where(on_top, 0.82 * batter * inward,
                                       1.0 - (1.0 - batter) * up_side)
    z = h * np.where(on_top, crown + (dip(turn) - crown) * inward,
                     0.86 * dip(turn) * up_side)
    shell = radial * reach[:, None] + _UP[None, :] * z[:, None]
    outward = np.where(on_top[:, None], _UP[None, :], radial)
    size = 0.16 * p.leaf_scale() * math.sqrt(r * max(r, 0.5 * h))
    leaves = _leaves(d, shell, outward, size * d.u(0.8, 1.2, count), 0.6, heart, p.phase, droop=0.05)
    return None, _join([drum, leaves])


def _whorl(p: _Plan, count: int, tip_radius: float, tip_height: float, width: float, bow: float,
           outline: Floats = _BROAD_LEAF, base_radius: float = 0.06, base_height: float = 0.03,
           ripple: float = 0.0, fold: float = 0.18, rim: bool = False) -> _Geo:
    """One ring of ``count`` folded leaves rising from the plant's centre to tips
    at ``tip_radius`` × the radius and ``tip_height`` × the height.

    ``rim``: this ring is the plant's outline (``_RIM_REACH``, ``_RIM_JITTER``)."""
    h, r, d = p.height, p.radius, p.dice
    angle = _even_angles(d, count, _RIM_JITTER if rim else 0.25)
    radial = np.stack([np.cos(angle), np.sin(angle)], axis=1)
    out = r * tip_radius * d.u(_RIM_REACH if rim else 0.86, 1.0, count)
    base = np.concatenate([radial * (base_radius * r), np.full((count, 1), base_height * h)], axis=1)
    tip = np.concatenate([radial * out[:, None],
                          (h * tip_height * d.u(0.85, 1.0, count))[:, None]], axis=1)
    return _fronds(base, tip, np.full(count, width) * d.u(0.9, 1.1, count),
                   np.full(count, bow), outline, d.u(0.0, 1.0, count),
                   (p.phase + d.u(-0.1, 0.1, count)) % 1.0, fold, ripple)


def _rosette(p: _Plan) -> _Shape:
    """A leafy vegetable: shingled rings of folded leaves — flat outside, upright inside —
    around a heart of young leaves (unless the recipe has none)."""
    r, h, d = p.radius, p.height, p.dice
    outer = p.number("n_out", 10.0, 6.0, 16.0)
    width = 0.80 * r * p.number("leaf_scale", 1.0, 0.6, 1.4)
    rings = [
        _whorl(p, round(outer), 1.00, 0.42, width, 0.20, ripple=0.3, rim=True),
        _whorl(p, max(round(0.78 * outer), 5), 0.80, 0.70, 0.86 * width, 0.15, ripple=0.3),
        _whorl(p, max(round(0.61 * outer), 4), 0.56, 0.92, 0.72 * width, 0.10, ripple=0.25),
    ]
    if p.flag("heart", True):
        heart = np.array([[0.0, 0.0, 0.52 * h]])
        rings.append(_lobes(heart, np.array([[0.36 * r, 0.36 * r, 0.44 * h]]),
                            np.array([0.0, 0.0, 0.2 * h]), d, p.phase))
        rings[-1].exposure += 0.45   # the heart is the youngest, lightest growth
    else:
        rings.append(_whorl(p, max(round(0.47 * outer), 4), 0.30, 1.00, 0.55 * width, 0.05))
    return None, _join(rings)


def _head(p: _Plan) -> _Shape:
    """A brassica or an artichoke: two rings of large leaves — the upper ring is the
    plant's height — around a low bud of young leaves.

    The head itself — a curd, a flower bud — is a flower head, and a flower is a
    seasonal claim: v0 builds the plant that carries it, not the head.
    """
    width = 0.70 * p.radius
    ripple = 0.3 if p.flag("frilly") else 0.15
    return None, _join([
        _whorl(p, 11, 1.00, 0.70, width, 0.22, ripple=ripple, rim=True),
        _whorl(p, 8, 0.74, 1.00, 0.85 * width, 0.14, ripple=ripple),
        _whorl(p, 6, 0.24, 0.50, 0.42 * width, 0.05),
    ])


def _blade_tuft(p: _Plan, count: int, base: Floats, direction: Floats, reach: Floats,
                height: Floats, width: float, segments: int, arch: float) -> _Geo:
    d = p.dice
    return _ribbons(base, direction, reach, height, width * d.u(0.8, 1.2, count), segments,
                    d.u(0.0, 1.0, count), (p.phase + d.u(-0.1, 0.1, count)) % 1.0, arch)


def _grass(p: _Plan) -> _Shape:
    """A grass or a bulb's foliage: seven tufts of thin arching blades — one in the
    middle, six around it, each fanning away from the plant's centre.

    The outer tufts' blades are placed by where their TIPS end: evenly round the
    axis, every fourth one on the canopy circle (the outline), the rest a little
    short of it; each blade grows from the tuft nearest to its tip.
    """
    h, r, d = p.height, p.radius, p.dice
    per_tuft = round(p.number("n_out", 11.0, 8.0, 20.0))
    outer = 6 * per_tuft
    count = outer + per_tuft
    tuft_angle = _even_angles(d, 6, _RIM_JITTER)
    tuft_out = 0.38 * r * d.u(0.75, 1.1, 6)
    centre = np.concatenate([
        np.stack([tuft_out * np.cos(tuft_angle), tuft_out * np.sin(tuft_angle)], axis=1),
        np.zeros((1, 2)),
    ])
    # the outer blades: tip direction and tip distance from the axis
    toward = _even_angles(d, outer, 0.3)
    far = r * np.where(np.arange(outer) % 4 == 0, d.u(_RIM_REACH, 1.0, outer), d.u(0.80, 1.0, outer))
    tip = far[:, None] * np.stack([np.cos(toward), np.sin(toward)], axis=1)
    apart = np.abs((toward[:, None] - tuft_angle[None, :] + np.pi) % _TAU - np.pi)
    tuft = np.concatenate([apart.argmin(axis=1), np.full(per_tuft, 6)])
    foot = centre[tuft] + r * d.u(-0.03, 0.03, (count, 2))
    # the middle tuft fans all round and stays inside
    middle = d.u(0.0, _TAU, per_tuft)
    inner_tip = (r * d.u(0.25, 0.70, per_tuft))[:, None] * np.stack(
        [np.cos(middle), np.sin(middle)], axis=1)
    lean = np.concatenate([tip, inner_tip]) - foot
    reach = np.linalg.norm(lean, axis=1)
    direction = lean / np.maximum(reach, 1e-12)[:, None]
    base = np.concatenate([foot, np.zeros((count, 1))], axis=1)
    width = 0.10 * r * p.leaf_scale()
    return None, _blade_tuft(p, count, base, direction, reach,
                             h * d.u(0.70, 1.0, count), width, 4, 0.12)


def _allium(p: _Plan) -> _Shape:
    """An onion, a leek, sweet corn: ONE clump, two layers of broad blades — the outer
    arching out, the inner upright."""
    h, r, d = p.height, p.radius, p.dice
    outer = round(p.number("n_out", 16.0, 12.0, 26.0)) + 4
    inner = max(round(0.8 * outer), 10)
    count = outer + inner
    is_outer = np.arange(count) < outer
    angle = np.concatenate([_even_angles(d, outer, _RIM_JITTER), d.u(0.0, _TAU, inner)])
    base = np.concatenate([r * d.u(-0.02, 0.02, (count, 2)), np.zeros((count, 1))], axis=1)
    reach = np.where(is_outer, r * d.u(_RIM_REACH, 1.0, count), r * d.u(0.15, 0.50, count))
    height = np.where(is_outer, h * d.u(0.62, 0.84, count), h * d.u(0.85, 1.0, count))
    width = 0.24 * r * p.leaf_scale()
    return None, _blade_tuft(p, count, base, np.stack([np.cos(angle), np.sin(angle)], axis=1),
                             reach, height, width, 5, 0.10)


def _feathery(p: _Plan) -> _Shape:
    """Fern-like foliage (carrot, dill, fennel, cosmos): twelve thin arching stems
    carrying narrow leaflets — airy, with no solid volume."""
    h, r, d = p.height, p.radius, p.dice
    stems = 12
    angle = _even_angles(d, stems, _RIM_JITTER)
    direction = np.stack([np.cos(angle), np.sin(angle)], axis=1)
    reach = r * d.u(_RIM_REACH, 1.0, stems)
    height = h * d.u(0.62, 0.98, stems)
    base = np.concatenate([r * d.u(-0.02, 0.02, (stems, 2)), np.zeros((stems, 1))], axis=1)
    stalks = _ribbons(base, direction, reach, height, np.full(stems, 0.035 * r), 4,
                      np.full(stems, 0.5), np.full(stems, p.phase))
    per_stem = round(p.number("n_out", 13.0, 8.0, 20.0) * 2.25)
    count = stems * per_stem
    stem = np.repeat(np.arange(stems), per_stem)
    f = d.u(0.22, 1.0, count)
    along = np.concatenate([direction, np.zeros((stems, 1))], axis=1)[stem]
    on_stem = base[stem] + along * (reach[stem] * f ** 2)[:, None] + _UP[None, :] * (height[stem] * f)[:, None]
    outward = _unit(along * 0.6 + d.u(-1.0, 1.0, (count, 3)) + np.array([0.0, 0.0, 0.5]))
    size = 0.20 * p.leaf_scale() * math.sqrt(r * max(r, 0.6 * h))
    leaflets = _leaves(d, on_stem, outward, size * d.u(0.7, 1.2, count), 0.28,
                       np.array([0.0, 0.0, 0.3 * h]), p.phase, droop=-0.1)
    return None, _join([stalks, leaflets])


def _flower(p: _Plan) -> _Shape:
    """A sunflower before it flowers: one stalk, broad leaves up its length, a leaf bud.

    The 2D sprite is one dominant flower head over a leaf ring; a flower is a
    seasonal claim, so v0 builds the plant that will carry it.
    """
    h, r, d = p.height, p.radius, p.dice
    stalk_radius = min(0.012 * h, 0.10 * r)
    pos, tri = _revolve(np.array([1.2, 1.0, 0.7]) * stalk_radius, np.array([0.0, 0.5, 0.94]) * h, 6)
    stalk = _geo(pos, _smooth_normals(pos, tri), tri, 0.15, 0.85, 0.5, p.phase)
    count = 12
    index = np.arange(count)
    # a spiral up the stalk that still puts one leaf into every twelfth of the circle
    angle = d.uf(0.0, _TAU) + _TAU * (
        (index * 5) % count + d.u(-_RIM_JITTER, _RIM_JITTER, count)) / count
    radial = np.stack([np.cos(angle), np.sin(angle)], axis=1)
    z = h * (0.14 + 0.70 * index / (count - 1))
    out = r * d.u(_RIM_REACH, 1.0, count)
    base = np.concatenate([radial * stalk_radius, z[:, None]], axis=1)
    tip = np.concatenate([radial * out[:, None], (z + h * d.u(-0.03, 0.05, count))[:, None]], axis=1)
    leaves = _fronds(base, tip, np.full(count, 0.72 * r), np.full(count, 0.10), _BROAD_LEAF,
                     d.u(0.0, 1.0, count), (p.phase + d.u(-0.1, 0.1, count)) % 1.0)
    bud = _whorl(p, 5, 0.24, 1.0, 0.30 * r, 0.03, _LANCE_LEAF, base_radius=0.02, base_height=0.90)
    return None, _join([stalk, leaves, bud])


def _palm(p: _Plan) -> _Shape:
    """A palm: a slender ringed trunk and a crown of arching, notched fronds."""
    h, r, d = p.height, p.radius, p.dice
    crown = 0.78 * h
    trunk_radius = min(0.03 * h, 0.18 * r)
    rings = np.array([1.35, 1.0, 1.08, 0.95, 1.03, 0.90]) * trunk_radius
    pos, tri = _revolve(rings, np.array([0.0, 0.08, 0.30, 0.52, 0.76, 1.0]) * crown, 8)
    trunk = _geo(pos, _smooth_normals(pos, tri), tri, 0.0, 1.0, 0.0, 0.0)
    count = round(p.number("n_out", 9.0, 6.0, 14.0))
    angle = _even_angles(d, count, _RIM_JITTER)
    radial = np.stack([np.cos(angle), np.sin(angle)], axis=1)
    base = np.concatenate([radial * trunk_radius, np.full((count, 1), crown)], axis=1)
    out = r * d.u(_RIM_REACH, 1.0, count)
    tip = np.concatenate([radial * out[:, None], (h * d.u(0.50, 0.66, count))[:, None]], axis=1)
    length = np.linalg.norm(tip - base, axis=1)
    bow = (h - crown) * d.u(1.25, 1.6, count) / np.maximum(length, 1e-12)
    fronds = _fronds(base, tip, np.full(count, 0.46 * r), bow, _FROND, d.u(0.0, 1.0, count),
                     (p.phase + d.u(-0.1, 0.1, count)) % 1.0, fold=0.22)
    spear = _whorl(p, 3, 0.22, 1.0, 0.16 * r, 0.02, _LANCE_LEAF, base_radius=0.01, base_height=0.78)
    return trunk, _join([fronds, spear])


#: Archetype (``recipe["a"]``) → its silhouette. Every archetype of the 2D tables
#: is here: ``tests/unit/test_scene3d_plants.py`` pins this map to
#: ``plant_art.ARCHETYPES``, so a new 2D archetype fails a test instead of
#: borrowing another one's shape.
ARCHETYPE_BUILDERS: Final[Mapping[str, _ArchetypeBuilder]] = MappingProxyType({
    "allium": _allium,
    "canopy": _canopy,
    "climber": _climber,
    "conifer": _conifer,
    "feathery": _feathery,
    "flower": _flower,
    "grass": _grass,
    "ground": _ground,
    "head": _head,
    "hedge": _hedge,
    "mound": _mound,
    "palm": _palm,
    "rosette": _rosette,
})


# ── truth and colour ──────────────────────────────────────────────────────────


def _fit(wood: _Geo | None, foliage: _Geo, height: float, radius: float) -> None:
    """Make the bounding box the data, in place.

    Builders work at the plant's real size, so the canopy circle is already there:
    foliage that strays beyond it (a leaf of the shell pointing outward) is pulled
    back onto it. The foliage is then centred on the local origin (the canopy
    circle's centre) — the wood stays where it stands: a trunk's foot is the plan
    position. Whatever dips below the plant's ground — the tip of a hanging leaf —
    is pressed onto it; a plant whose lowest point is above the ground is set
    down. Last, everything is scaled about the origin: xy so the widest span is
    the canopy diameter, z so the top is the height. Normals take the inverse of
    the scale.

    Never lift a plant to make its lowest vertex zero: one leaf dipping 9 cm
    under a lavender's base lifted the whole cushion 9 cm into the air, and
    "min z = 0" still held (a sanity render found it, not a gate).

    Why not scale the farthest vertex onto the circle instead: the bounding box
    then spans as little as 72 % of the canopy diameter for a sparse plant. With
    the bounding box exact, the regular outlines (``_RIM_REACH``) and the pull
    above keep every vertex within a few percent of the circle (measured per
    archetype in ``tests/unit/test_scene3d_plants.py``).
    """
    distance = np.hypot(foliage.pos[:, 0], foliage.pos[:, 1])
    stray = distance > radius
    foliage.pos[stray, :2] *= (radius / distance[stray])[:, None]
    low, high = foliage.pos[:, :2].min(axis=0), foliage.pos[:, :2].max(axis=0)
    foliage.pos[:, :2] -= (low + high) / 2.0
    parts = [foliage] if wood is None else [wood, foliage]
    for part in parts:
        part.pos[:, 2] = np.maximum(part.pos[:, 2], 0.0)
    reach = max(float(np.abs(part.pos[:, :2]).max()) for part in parts)
    bottom = min(float(part.pos[:, 2].min()) for part in parts)   # >= 0: only ever set down
    top = max(float(part.pos[:, 2].max()) for part in parts)
    scale = np.array([radius / max(reach, 1e-12), radius / max(reach, 1e-12),
                      height / max(top - bottom, 1e-12)])
    for part in parts:
        part.pos[:, 2] -= bottom
        part.pos *= scale[None, :]
        nrm = part.nrm / scale[None, :]
        length = np.linalg.norm(nrm, axis=1, keepdims=True)
        part.nrm = np.where(length > 1e-12, nrm / np.maximum(length, 1e-12), _UP[None, :])


@lru_cache(maxsize=32)
def _palette(name: str) -> Floats:
    """A palette's four leaf anchors (t0, t1, b0, b1) as sRGB 0..1, shape (4, 3)."""
    entry = PALETTES[name]
    rows = [[int(entry[key][i:i + 2], 16) / 255.0 for i in (1, 3, 5)]
            for key in ("t0", "t1", "b0", "b1")]
    anchors = np.array(rows, np.float64)
    anchors.setflags(write=False)
    return anchors


def _foliage_colors(geo: _Geo, palette: Floats, tip_weight: float) -> Floats:
    """Linear RGBA per vertex from the palette alone.

    The mix between the base anchors and the tip anchors follows each vertex's
    exposure, shifted so that the plant's area-weighted mean is ``tip_weight`` —
    the archetype's measured share of tip tone in the 2D sprite. The mix is made
    in sRGB, as the 2D gradients are; the shade is applied in linear light and
    does not move the hue.
    """
    weight = _vertex_areas(geo.pos, geo.tri) * geo.shade ** (1.0 / 2.4)
    total = max(float(weight.sum()), 1e-12)
    tip = tip_weight + _TONE_CONTRAST * (geo.exposure - float(weight @ geo.exposure) / total)
    tip = np.clip(tip, 0.0, 1.0)
    tip = np.clip(tip + (tip_weight - float(weight @ tip) / total), 0.0, 1.0)
    tone = geo.tone[:, None]
    base_colour = palette[2][None, :] + (palette[3] - palette[2])[None, :] * tone
    tip_colour = palette[0][None, :] + (palette[1] - palette[0])[None, :] * tone
    srgb = base_colour + (tip_colour - base_colour) * tip[:, None]
    rgba = np.ones((len(geo.pos), 4))
    rgba[:, :3] = np.clip(_srgb_to_linear(srgb) * geo.shade[:, None], 0.0, 1.0)
    return rgba


def _solid(pos: Floats, colour: NDArray[np.float32]) -> Floats:
    rgba = np.ones((len(pos), 4))
    rgba[:, :3] = colour[None, :]
    return rgba


def build_silhouette(recipe: Mapping[str, Any], seed: int, height_cm: float,
                     radius_cm: float) -> tuple[MeshPart, ...]:
    """The silhouette of ``recipe`` at ``height_cm`` × a canopy of ``radius_cm``.

    Pure and deterministic in its arguments. ``ValueError`` for an archetype
    without a builder — never another archetype's shape.
    """
    archetype = str(recipe.get("a"))
    builder = ARCHETYPE_BUILDERS.get(archetype)
    if builder is None:
        raise ValueError(f"no 3D builder for archetype {archetype!r}")
    dice = _Dice(seed)
    wood, foliage = builder(_Plan(height_cm, radius_cm, recipe, dice, dice.uf(0.0, 1.0)))
    _fit(wood, foliage, height_cm, radius_cm)
    colours = _foliage_colors(foliage, _palette(palette_key(recipe)), FOLIAGE_TIP_WEIGHT[archetype])
    sway = np.stack([np.clip(foliage.pos[:, 2] / height_cm, 0.0, 1.0),
                     np.minimum(foliage.phase % 1.0, 0.999999)], axis=1)
    parts = [MeshPart(Mesh.from_arrays(foliage.pos, foliage.nrm, colours, sway, foliage.tri),
                      "foliage", casts_shadow=True, name=PART_FOLIAGE)]
    if wood is not None:
        bark = _solid(wood.pos, srgb_to_linear(BARK_SRGB))
        parts.insert(0, MeshPart(
            Mesh.from_arrays(wood.pos, wood.nrm, bark, np.zeros((len(wood.pos), 2)), wood.tri),
            "vc", casts_shadow=True, name=PART_WOOD))
    return tuple(parts)


@lru_cache(maxsize=64)
def _placeholder(radius_cm: float) -> tuple[MeshPart, ...]:
    height = min(PLACEHOLDER_HEIGHT_FRACTION * 2.0 * radius_cm, PLACEHOLDER_MAX_HEIGHT_CM)
    sphere = (radius_cm * radius_cm + height * height) / (2.0 * height)
    rings = radius_cm * np.array([1.0, 0.86, 0.68, 0.46, 0.23])
    z = np.sqrt(sphere * sphere - rings * rings) - (sphere - height)
    z[0] = 0.0
    pos, tri = _revolve(rings, z, _PLACEHOLDER_SIDES, apex=height)
    nrm = _unit(pos + np.array([0.0, 0.0, sphere - height])[None, :])  # the cap's own sphere
    tone = srgb_to_linear(FLOWERS["white"][0]) * np.float32(PLACEHOLDER_SHADE)
    mesh = Mesh.from_arrays(pos, nrm, _solid(pos, tone), np.zeros((len(pos), 2)), tri)
    return (MeshPart(mesh, "vc", casts_shadow=False, name=PART_PLACEHOLDER),)


def placeholder_parts(radius_cm: float) -> tuple[MeshPart, ...]:
    """The honest marker of a plant whose height the plan does not know.

    One part: a smooth spherical cap over the canopy circle,
    ``min(PLACEHOLDER_HEIGHT_FRACTION × diameter, PLACEHOLDER_MAX_HEIGHT_CM)``
    high, in one neutral tone, casting no shadow (the 2D shadow model gives a
    plant without a height none either). The same for every item: a marker has
    no individuality, and it reads nothing but the radius.
    """
    return _placeholder(float(radius_cm))


# ── the pipeline's builder ────────────────────────────────────────────────────


@lru_cache(maxsize=1024)
def _resolve(kind: str, species: str, category: str | None) -> SpriteRef | None:
    return resolve_sprite(kind, species, category, available=has_recipe)


def plant_sprite(record: Record) -> SpriteRef | None:
    """The sprite — and so the recipe — of a plant record, or None when the record
    is not a plant.

    The 2D rule (``plant_art.resolve_sprite``) on the record's ``species_name``
    and ``plant_category``. Note what the record carries: ``species_name`` is the
    species' common name when the item has species data, else the item's own
    species string. The 2D renderer reads the item's species string only — the
    two agree for every plant placed from the gallery or a plant list, and can
    differ after a species was assigned from the plant database (ADR-054
    addendum, "plant art v0").
    """
    if record.kind not in PLANT_KINDS:
        return None
    species = record.params.get(PARAM_SPECIES_NAME)
    category = record.params.get(PARAM_PLANT_CATEGORY)
    return _resolve(record.kind, species if isinstance(species, str) else "",
                    category if isinstance(category, str) else None)


def plant_archetype(record: Record) -> str | None:
    """What ``build_plant`` builds for a record: an archetype name, ``PLACEHOLDER``
    for a plant without a height, None for a record that is not a plant."""
    sprite = plant_sprite(record)
    if sprite is None:
        return None
    if record.height_cm is None:
        return PLACEHOLDER
    return str(recipe_for(sprite)["a"])


def _canopy_radius(record: Record) -> float | None:
    radius = record.params.get(PARAM_RADIUS_CM)
    if isinstance(radius, bool) or not isinstance(radius, (int, float)):
        return None
    return float(radius) if math.isfinite(radius) and radius > 0.0 else None


def build_plant(record: Record) -> tuple[MeshPart, ...]:
    """The builder of the plant kinds (``register``).

    - a plant with a height: its recipe's silhouette, fitted to ``height_cm`` and
      the canopy radius ``params["radius_cm"]``, seeded from ``params["seed"]``;
    - a plant without a height: ``placeholder_parts``;
    - anything else (not a plant kind, or no canopy circle): ``default_builder``.

    Reads only the record's geometry part (``core.scene3d.build``).
    """
    sprite = plant_sprite(record)
    radius = _canopy_radius(record)
    if sprite is None or radius is None:
        return default_builder(record)
    if record.height_cm is None:
        return placeholder_parts(radius)
    seed = record.params.get(PARAM_SEED)
    return build_silhouette(recipe_for(sprite), item_seed(str(seed) if seed is not None else "", "plant"),
                            record.height_cm, radius)


def register(registry: BuilderRegistry) -> None:
    """Register ``build_plant`` for every plant kind (``plant_art.PLANT_KINDS`` —
    the table ``plant_renderer.is_plant_type`` answers from)."""
    registry.register_many(sorted(PLANT_KINDS), build_plant)
