"""Plant v0 — archetype silhouettes from the shared recipes (Phase 17 L1.7, #385).

``core/scene3d/builders/plants.py`` turns one plant record into a readable,
cheap silhouette: the archetype and the colours come from the recipe the 2D
sprite is drawn from, the size from the plan. The look may be stylised; the
numbers may not lie. Gates (the story's G2 – G6):

- G2 truth and budget — every recipe of both tables × six sizes (three along its
  own proportion, the bundled species' small size, one far wider than tall, one
  far taller than wide) × five seeds: top = the plan's height (± 3 %), widest
  span = the canopy diameter (± 10 %), base at 0, a valid mesh, at most 2,000
  triangles — and the plant is BUILT to those numbers, not scaled into them:
  the builder's own top is the height before the fit touches it, the foliage's
  mass reaches the top (area-weighted height quantiles), next to nothing is
  pulled back onto the canopy circle or pressed onto the ground, and no foliage
  triangle lies flat on the ground;
- G3 colour — the foliage hue is within 10° of the foliage of the 2D sprite, as
  QSvgRenderer draws it, for all 123 sprites;
- G4 coverage — every archetype of the tables has a builder, and a record is
  resolved by the 2D rule;
- G5 determinism (≤ 1e-6) and no clones (side silhouettes of two ids differ);
- G6 the placeholder for a plant without a height.

No GPU, and no Qt except for G3's reference (the sprite renderer).
"""

from __future__ import annotations

import colorsys
import dataclasses
import importlib.util
import json
import math
from functools import cache
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import numpy as np
import pytest

from open_garden_planner.core.plant_art import (
    ARCHETYPES,
    CATEGORIES,
    CATEGORY_SPRITES,
    FEATURE_KEYS,
    FLOWERS,
    PALETTES,
    PLANT_KINDS,
    SPECIES,
    Recipe,
    SpriteRef,
    has_recipe,
    palette_key,
    resolve_recipe,
    resolve_sprite,
)
from open_garden_planner.core.scene3d import (
    PARAM_PLANT_CATEGORY,
    PARAM_RADIUS_CM,
    PARAM_SEED,
    PARAM_SPECIES_NAME,
    BuilderContractError,
    BuilderRegistry,
    Material,
    Mesh,
    MeshPart,
    Params,
    Record,
    RecordingSink,
    SceneSync,
    Transform,
    default_builder,
    item_seed,
    normal_vs_winding,
    srgb_channel_to_linear,
    srgb_to_linear,
    verify_builder,
)
from open_garden_planner.core.scene3d.builders import plants
from open_garden_planner.core.scene3d.builders.plants import (
    ARCHETYPE_BUILDERS,
    BARK_SRGB,
    FOLIAGE_TIP_WEIGHT,
    PART_FOLIAGE,
    PART_PLACEHOLDER,
    PART_WOOD,
    PLACEHOLDER,
    PLACEHOLDER_HEIGHT_FRACTION,
    PLACEHOLDER_MAX_HEIGHT_CM,
    PLACEHOLDER_SHADE,
    PROPORTION_STEP,
    SHAPE_CACHE_SIZE,
    TRIANGLE_BUDGET,
    FitReport,
    build_plant,
    build_silhouette,
    clear_shape_cache,
    fit_report,
    plant_archetype,
    plant_sprite,
    register,
    shape_bytes,
    shape_cache_info,
)
from open_garden_planner.core.shadow_geometry import circle_footprint

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "generate_plant_sprites.py"
PLANTS_DIR = REPO / "src" / "open_garden_planner" / "resources" / "plants"
BUNDLED = json.loads(
    (REPO / "src/open_garden_planner/resources/data/plant_species.json").read_text("utf-8")
)["plants"]
GOLDEN = json.loads(
    (REPO / "tests/fixtures/plant_art/sprite_resolution_golden.json").read_text("utf-8")
)
BARK_TEXTURE = REPO / "src/open_garden_planner/resources/textures/bark.png"

CATEGORY_OF_SPRITE = {stem: name for name, stem in CATEGORY_SPRITES.items()}
ALL_SPRITES: list[SpriteRef] = [SpriteRef("species", name) for name in SPECIES] + [
    SpriteRef("categories", name) for name in CATEGORIES
]
SEEDS = tuple(f"plant-{n}" for n in range(5))
#: Sizes along a recipe's own proportion, as fractions of its mature size.
GROWTH = {"seedling": 0.08, "mid": 0.5, "mature": 1.0}
#: Off-proportion sizes every recipe is ALSO built at (height × spread in cm): a
#: plant far wider than tall and one far taller than wide. Scaled along its own
#: proportion a recipe is seen at one proportion only — and a builder that builds
#: too tall and is squashed back passes at the proportion it was tuned at (a
#: squash 60 × 360 was built 1.6 times too tall; at 15 × 400 nine archetypes
#: were 5 to 9 times too tall, and every gate was green).
FLAT, TALL = (30.0, 240.0), (300.0, 60.0)
#: (height, spread) in cm for a recipe no bundled species reaches — synthetic.
ARCHETYPE_SIZE = {
    "allium": (50.0, 15.0), "canopy": (600.0, 500.0), "climber": (250.0, 80.0),
    "conifer": (800.0, 300.0), "feathery": (60.0, 40.0), "flower": (200.0, 60.0),
    "grass": (80.0, 60.0), "ground": (15.0, 80.0), "head": (60.0, 60.0),
    "hedge": (150.0, 100.0), "mound": (100.0, 100.0), "palm": (500.0, 350.0),
    "rosette": (30.0, 30.0),
}


def recipe_of(sprite: SpriteRef) -> Recipe:
    return (SPECIES if sprite.group == "species" else CATEGORIES)[sprite.name]


def record_for(sprite: SpriteRef, height_cm: float | None, radius_cm: float,
               seed: str = "plant-0", **extra: object) -> Record:
    """A plant record as the snapshot writes it, resolving to ``sprite``: a species
    by the name the gallery sets, a category by its ``PlantCategory`` name."""
    params: dict[str, object] = {PARAM_SEED: seed, PARAM_RADIUS_CM: radius_cm}
    if sprite.group == "species":
        params[PARAM_SPECIES_NAME] = sprite.name.replace("_", " ")
        kind = "PERENNIAL"
    else:
        params[PARAM_PLANT_CATEGORY] = CATEGORY_OF_SPRITE[sprite.name]
        kind = "TREE"
    params.update(extra)
    ring = tuple((round(x, 7), round(y, 7)) for x, y in circle_footprint(0.0, 0.0, radius_cm))
    return Record(
        item_id=f"item-{seed}", kind=kind, shape="circle", footprints=(ring,),
        transform=Transform(120.0, -40.0), material=Material(kind),
        height_cm=height_cm, params=Params(params),  # type: ignore[arg-type]
    )


def _bundled_sizes() -> tuple[dict[SpriteRef, tuple[float, float]],
                              dict[SpriteRef, tuple[float, float]]]:
    """(mature, small) height × spread per sprite from the bundled species data:
    the largest species that reaches the sprite, at its ``max_*`` and its
    ``min_*`` size (their proportions differ: Winter Squash is 60 × 360 at its
    largest and 30 × 180 at its smallest, a tomato 200 × 90 and 60 × 45) — else
    the archetype's synthetic size and half of it."""
    mature: dict[SpriteRef, tuple[float, float]] = {}
    small: dict[SpriteRef, tuple[float, float]] = {}
    for species in BUNDLED:
        resolved = resolve_recipe("PERENNIAL", species["common_name"], None)
        assert resolved is not None
        size = (float(species["max_height_cm"]), float(species["max_spread_cm"]))
        if resolved[0] not in mature or size[0] * size[1] > np.prod(mature[resolved[0]]):
            mature[resolved[0]] = size
            small[resolved[0]] = (float(species["min_height_cm"]), float(species["min_spread_cm"]))
    for sprite in ALL_SPRITES:
        height, spread = ARCHETYPE_SIZE[recipe_of(sprite)["a"]]
        mature.setdefault(sprite, (height, spread))
        small.setdefault(sprite, (0.5 * height, 0.5 * spread))
    return mature, small


MATURE, SMALL = _bundled_sizes()


def sizes_of(sprite: SpriteRef) -> dict[str, tuple[float, float]]:
    """label → (height, canopy RADIUS) in cm: the six sizes a recipe is built at."""
    height, spread = MATURE[sprite]
    sizes = {label: (height * factor, spread * factor / 2.0) for label, factor in GROWTH.items()}
    sizes["small"] = (SMALL[sprite][0], SMALL[sprite][1] / 2.0)
    sizes["flat"] = (FLAT[0], FLAT[1] / 2.0)
    sizes["tall"] = (TALL[0], TALL[1] / 2.0)
    return sizes


def plant_seed(seed: str) -> int:
    """The number ``build_plant`` seeds a record's plant with."""
    return item_seed(seed, "plant")


def bbox(parts: tuple[MeshPart, ...]) -> tuple[np.ndarray, np.ndarray]:
    points = np.concatenate([part.mesh.positions for part in parts])
    return points.min(axis=0), points.max(axis=0)


def triangles(parts: tuple[MeshPart, ...]) -> int:
    return sum(part.mesh.triangle_count for part in parts)


def part(parts: tuple[MeshPart, ...], name: str) -> MeshPart:
    return next(p for p in parts if p.name == name)


def linear_to_srgb(linear: np.ndarray) -> np.ndarray:
    linear = np.clip(linear, 0.0, 1.0)
    return np.where(linear <= 0.0031308, linear * 12.92, 1.055 * np.power(linear, 1 / 2.4) - 0.055)


def hue_deg(rgb: np.ndarray) -> float:
    r, g, b = (float(c) for c in rgb)
    top = max(r, g, b, 1e-12)
    return colorsys.rgb_to_hsv(r / top, g / top, b / top)[0] * 360.0


def saturation(rgb: np.ndarray) -> float:
    r, g, b = (float(c) for c in rgb)
    top = max(r, g, b, 1e-12)
    return colorsys.rgb_to_hsv(r / top, g / top, b / top)[1]


def hue_difference(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def mean_srgb(mesh: Mesh) -> np.ndarray:
    """Area-weighted mean of a mesh's vertex colours, converted to sRGB."""
    srgb = linear_to_srgb(mesh.colors[:, :3].astype(np.float64))
    tri = mesh.indices.astype(np.int64)
    p = mesh.positions.astype(np.float64)
    area = 0.5 * np.linalg.norm(
        np.cross(p[tri[:, 1]] - p[tri[:, 0]], p[tri[:, 2]] - p[tri[:, 0]]), axis=1)
    return (srgb[tri].mean(axis=1) * area[:, None]).sum(axis=0) / area.sum()


def triangle_areas(mesh: Mesh) -> tuple[np.ndarray, np.ndarray]:
    """(area, the three vertices' heights) per triangle of a mesh."""
    tri = mesh.indices.astype(np.int64)
    p = mesh.positions.astype(np.float64)
    area = 0.5 * np.linalg.norm(
        np.cross(p[tri[:, 1]] - p[tri[:, 0]], p[tri[:, 2]] - p[tri[:, 0]]), axis=1)
    return area, p[tri][:, :, 2]


def height_quantiles(mesh: Mesh, shares: tuple[float, ...]) -> tuple[float, ...]:
    """The heights below which the given shares of a mesh's SURFACE AREA lie
    (triangles by their centroid). Where a plant's mass is: a canopy squashed
    into the lower two thirds of its bounding box has its 99 % quantile at 0.63
    of the height, whatever the box says."""
    area, z = triangle_areas(mesh)
    centre = z.mean(axis=1)
    order = np.argsort(centre)
    cumulative = np.cumsum(area[order]) / float(area.sum())
    return tuple(float(centre[order][min(int(np.searchsorted(cumulative, share)), len(order) - 1)])
                 for share in shares)


def ground_flat_triangles(mesh: Mesh, height: float) -> int:
    """Triangles lying wholly within 0.9 % of the height above the ground: coplanar
    with it for every practical purpose (they z-fight with the ground texture)."""
    _area, z = triangle_areas(mesh)
    return int((z.max(axis=1) <= 0.009 * height).sum())


def side_silhouette(parts: tuple[MeshPart, ...], height: float, radius: float,
                    cells: int = 32) -> np.ndarray:
    """Occupancy of the plant seen from the south (the x–z projection), ``cells``²
    over [-radius, radius] × [0, height].

    Every triangle is sampled on a barycentric lattice finer than half a cell, so
    a large triangle (a hedge's side) fills its cells as surely as a small leaf.
    """
    grid = np.zeros((cells, cells), bool)
    for item in parts:
        tri = item.mesh.indices.astype(np.int64)
        flat = item.mesh.positions.astype(np.float64)[:, [0, 2]]
        flat = (flat + [radius, 0.0]) / [2.0 * radius, height] * cells      # in cells
        corners = flat[tri]                                                  # (m, 3, 2)
        extent = (corners.max(axis=1) - corners.min(axis=1)).max(axis=1)
        steps = np.minimum(np.ceil(extent * 2.0).astype(int) + 1, 128)
        for count in np.unique(steps):
            lattice = np.array([(i, j, count - i - j) for i in range(count + 1)
                                for j in range(count + 1 - i)], float) / count
            points = np.einsum("sk,mkc->msc", lattice, corners[steps == count]).reshape(-1, 2)
            col = np.clip(points[:, 0].astype(int), 0, cells - 1)
            row = np.clip(points[:, 1].astype(int), 0, cells - 1)
            grid[row, col] = True
    return grid


def silhouette_iou(a: np.ndarray, b: np.ndarray) -> float:
    return float((a & b).sum() / max((a | b).sum(), 1))


# ── G2: truth and budget ──────────────────────────────────────────────────────

#: BUILT to the data. Tolerance of the builder's own top against the height,
#: before the fit (the orchestrator's ruling allows ± 15 %). Measured over every
#: recipe × six sizes × five ids (3,690 builds) and the extremes below (702):
#: 1.0000 everywhere — each archetype places its highest point AT the height.
BUILT_TOP_TOLERANCE = 0.02
#: What the fit may still repair — measured maxima in brackets, over the same
#: 4,392 builds, 2026-10-11 (``scripts/measure_plant_art_3d.py truth`` prints
#: them): the share of foliage vertices pulled back onto the canopy circle
#: [1.23 %, a mound 300 × 60; 0.57 % at the bundled sizes], how far beyond the
#: circle the farthest of them was [1.068 radii], the xy scale [1.073, a rosette
#: of six leaves], the centring shift [0.041 radii]. Nothing is pressed onto the
#: ground and no plant is set down: both exactly 0.
CLAMPED_SHARE_MAX = 0.02
CLAMPED_REACH_MAX = 1.08
XY_SCALE_MAX = 1.09
CENTRE_SHIFT_MAX = 0.05
#: Where the foliage's mass is, per archetype: (z95, z99) — at least 5 % / 1 % of
#: the foliage's surface area lies above this fraction of the height. Stated
#: about 0.03 under the measured minima (in brackets, same 4,392 builds). They
#: differ by nature: a hedge is a drum, a conifer a cone (95 % of a cone's
#: surface lies below 0.78 of its height), a rosette flat leaves round a small
#: heart. Before the builders built to the data a squash 60 × 360 measured
#: 0.70 / 0.76 and a mound 15 × 800 measured 0.15 / 0.35.
MASS_REACHES = {
    "allium": (0.68, 0.82),     # [0.710, 0.849]
    "canopy": (0.82, 0.90),     # [0.854, 0.931]
    "climber": (0.83, 0.93),    # [0.863, 0.965]
    "conifer": (0.70, 0.84),    # [0.739, 0.870]
    "feathery": (0.75, 0.88),   # [0.784, 0.910]
    "flower": (0.77, 0.78),     # [0.807, 0.818]
    "grass": (0.69, 0.80),      # [0.727, 0.835]
    "ground": (0.64, 0.80),     # [0.677, 0.832]
    "head": (0.73, 0.80),       # [0.762, 0.833]
    "hedge": (0.87, 0.91),      # [0.902, 0.940]
    "mound": (0.73, 0.86),      # [0.764, 0.897]
    "palm": (0.78, 0.79),       # [0.819, 0.828]
    "rosette": (0.62, 0.72),    # [0.655, 0.757]
}


def assert_built_to_the_data(recipe: Recipe, seed: int, height: float, radius: float,
                             parts: tuple[MeshPart, ...]) -> FitReport:
    """The plant is built to the plan's numbers — the fit found next to nothing
    to repair — and its foliage is where its bounding box says it is."""
    report = fit_report(recipe, seed, height, radius)
    where = f"{recipe['a']} {height:g} x {2 * radius:g}, seed {seed}: {report}"
    assert abs(report.built_top - 1.0) <= BUILT_TOP_TOLERANCE, where
    assert abs(report.z_scale - 1.0) <= BUILT_TOP_TOLERANCE, where
    assert report.pressed_share == 0.0 and report.set_down <= 1e-9, where
    assert report.clamped_share <= CLAMPED_SHARE_MAX, where
    assert 1.0 <= report.clamped_reach <= CLAMPED_REACH_MAX, where
    assert 1.0 - 1e-9 <= report.xy_scale <= XY_SCALE_MAX, where
    assert report.centre_shift <= CENTRE_SHIFT_MAX, where
    # the remembered shape was built within half a proportion step of this plant's
    assert abs(math.log(report.stretch)) <= 0.5 * math.log(PROPORTION_STEP) + 1e-9, where
    foliage = part(parts, PART_FOLIAGE).mesh
    z95, z99 = height_quantiles(foliage, (0.95, 0.99))
    floor95, floor99 = MASS_REACHES[recipe["a"]]
    assert z95 >= floor95 * height and z99 >= floor99 * height, (z95 / height, z99 / height, where)
    assert ground_flat_triangles(foliage, height) == 0, where
    return report


@pytest.mark.parametrize("sprite", ALL_SPRITES, ids=lambda s: f"{s.group}/{s.name}")
def test_every_recipe_fits_the_plan_s_numbers(sprite: SpriteRef) -> None:
    """Every recipe × six sizes × five seeds: the bounding box IS the data, the
    plant was BUILT to it, the mesh is sound, and it stays inside the budget."""
    recipe = recipe_of(sprite)
    for height, radius in sizes_of(sprite).values():
        for seed in SEEDS:
            record = record_for(sprite, height, radius, seed)
            assert plant_sprite(record) == sprite
            assert plant_archetype(record) == recipe["a"]
            parts = (verify_builder(build_plant, record, height_tolerance=0.03)
                     if seed == SEEDS[0] else build_plant(record))
            assert parts and {p.name for p in parts} <= {PART_WOOD, PART_FOLIAGE}
            low, high = bbox(parts)
            assert high[2] == pytest.approx(height, rel=1e-5)             # gate: ± 3 %
            assert low[2] == pytest.approx(0.0, abs=1e-4 * height)        # stands on the ground
            # ... on more than the tip of one hanging leaf (a lavender once floated 9 cm
            # above the ground with a single vertex touching it, and min z was 0)
            grounded = sum(int((p.mesh.positions[:, 2] <= 0.005 * height).sum()) for p in parts)
            assert grounded >= 6, f"only {grounded} vertices touch the ground: it floats"
            span = max(high[0] - low[0], high[1] - low[1])
            assert span == pytest.approx(2.0 * radius, rel=1e-5)          # gate: ± 10 %
            # centred on the plan position, and nothing leaves the canopy circle by more
            # than 10 % (measured over this matrix: at most 5 %)
            assert abs(high[0] + low[0]) <= 1e-4 * radius + 1e-5
            assert abs(high[1] + low[1]) <= 1e-4 * radius + 1e-5
            reach = max(float(np.hypot(p.mesh.positions[:, 0], p.mesh.positions[:, 1]).max())
                        for p in parts)
            assert reach <= 1.10 * radius
            for item in parts:
                item.validate()
                assert item.casts_shadow and not item.tinted
                assert item.material_kind == ("vc" if item.name == PART_WOOD else "foliage")
            assert 0 < triangles(parts) <= TRIANGLE_BUDGET == 2000
            assert_built_to_the_data(recipe, plant_seed(seed), height, radius, parts)


#: The largest plant of each archetype over the matrix above (every recipe × six
#: sizes × five seeds), as documented in §8.26.7 — a ceiling this test holds the
#: builders to, well under the 2,000 of the gate.
MEASURED_MAX_TRIANGLES = {
    "allium": 470, "canopy": 1904, "climber": 1352, "conifer": 664, "feathery": 792,
    "flower": 316, "grass": 1120, "ground": 1092, "head": 400, "hedge": 900, "mound": 1556,
    "palm": 356, "rosette": 560,
}


def test_the_largest_plant_of_each_archetype() -> None:
    """Budget by ARCHETYPE: the measured maximum per archetype is what the docs
    state, and none comes near the gate. Builds what it needs itself."""
    largest: dict[str, int] = dict.fromkeys(sorted(ARCHETYPES), 0)
    for sprite in ALL_SPRITES:
        recipe = recipe_of(sprite)
        for height, radius in sizes_of(sprite).values():
            for seed in SEEDS:
                count = triangles(build_silhouette(recipe, plant_seed(seed), height, radius))
                largest[recipe["a"]] = max(largest[recipe["a"]], count)
    print("\nmax triangles per archetype:", largest)
    assert set(largest) == set(MEASURED_MAX_TRIANGLES) == ARCHETYPES
    for archetype, count in largest.items():
        assert 0 < count <= MEASURED_MAX_TRIANGLES[archetype] <= TRIANGLE_BUDGET, (archetype, count)


@pytest.mark.parametrize("species", BUNDLED, ids=lambda s: s["common_name"])
def test_every_bundled_species_builds_at_its_mature_size(species: dict) -> None:
    for kind in sorted(PLANT_KINDS):
        height, radius = float(species["max_height_cm"]), float(species["max_spread_cm"]) / 2.0
        record = dataclasses.replace(
            record_for(SpriteRef("species", "tomato"), height, radius),
            kind=kind, params=Params({PARAM_SEED: "bundled", PARAM_RADIUS_CM: radius,
                                      PARAM_SPECIES_NAME: species["common_name"]}))
        parts = verify_builder(build_plant, record, height_tolerance=0.03)
        expected = resolve_recipe(kind, species["common_name"], None)
        assert expected is not None and plant_sprite(record) == expected[0]
        assert 0 < triangles(parts) <= TRIANGLE_BUDGET
        assert_built_to_the_data(expected[1], plant_seed("bundled"), height, radius, parts)


@pytest.mark.parametrize("archetype", sorted(ARCHETYPES))
def test_the_plant_stays_on_its_canopy_circle(archetype: str) -> None:
    """The bounding box is exact by the fit; this is the other half of the spread
    truth: seen from above, no vertex is further than 1.10 × the canopy radius
    from the axis, for 40 ids × three proportions × a coarse and a fine recipe.

    Measured over 1,000 ids × three proportions × three recipes per archetype
    (scratch, 2026-10-11): at most 1.081 (hedge), 1.078 (palm), 1.077 (feathery),
    1.071 (canopy); every other archetype under 1.06.
    With outlines of any reach at any angle it was 1.39 (feathery), 1.37
    (allium), 1.23 (flower) — and scaling the farthest vertex onto the circle
    instead left a bounding box of 72 % of the canopy diameter."""
    worst = 0.0
    for extra in ({"leaf_scale": 1.35, "n_out": 10, "frilly": True},
                  {"leaf_scale": 0.55, "n_out": 22, "narrow": True, "heart": False}):
        for height, radius in ((100.0, 50.0), (300.0, 40.0), (30.0, 120.0)):
            for seed in range(40):
                parts = build_silhouette({"a": archetype, **extra}, seed * 7919 + 13, height, radius)
                reach = max(float(np.hypot(p.mesh.positions[:, 0], p.mesh.positions[:, 1]).max())
                            for p in parts)
                worst = max(worst, reach / radius)
    assert worst <= 1.10, f"{archetype}: a vertex {worst:.3f} canopy radii from the axis"


EXTREMES = [(1.0, 0.5), (0.4, 3.0), (5000.0, 1500.0), (15.0, 400.0), (800.0, 10.0), (2.0, 2.0),
            (15.0, 200.0), (30.0, 120.0), (300.0, 30.0)]
EXTREME_RECIPES = [{}, {"leaf_scale": 1.35, "n_out": 10, "frilly": True},
                   {"leaf_scale": 0.55, "n_out": 22, "narrow": True, "heart": False}]


@pytest.mark.parametrize("archetype", sorted(ARCHETYPES))
@pytest.mark.parametrize(("height", "radius"), EXTREMES)
def test_extreme_sizes_and_proportions(archetype: str, height: float, radius: float) -> None:
    """Sizes a plan can hold: a seedling of a centimetre, a 50 m tree, a plant
    27 times wider than tall and one 40 times taller than wide."""
    for extra in EXTREME_RECIPES:
        for seed in (11, 12):
            recipe = {"a": archetype, **extra}
            parts = build_silhouette(recipe, seed, height, radius)
            low, high = bbox(parts)
            assert high[2] == pytest.approx(height, rel=1e-5)
            assert low[2] == pytest.approx(0.0, abs=1e-4 * height)
            assert max(high[0] - low[0], high[1] - low[1]) == pytest.approx(2 * radius, rel=1e-5)
            for item in parts:
                item.validate()
            assert triangles(parts) <= TRIANGLE_BUDGET
            assert_built_to_the_data(recipe, seed, height, radius, parts)


@pytest.mark.parametrize("archetype", sorted(ARCHETYPES))
def test_the_budget_holds_by_construction(archetype: str) -> None:
    """Triangle counts are clipped functions of recipe numbers — never of the
    plant's size, and not of a number someone types into a recipe."""
    base = triangles(build_silhouette({"a": archetype}, 3, 100.0, 50.0))
    for size in ((1.0, 0.5), (4000.0, 2000.0)):   # the same proportions, 100 times smaller / 40 larger
        assert triangles(build_silhouette({"a": archetype}, 3, *size)) == base
    for recipe in ({"n_out": 100_000, "leaf_scale": 0.001}, {"n_out": 1, "leaf_scale": 50.0},
                   {"n_out": 100_000, "leaf_scale": 0.001, "narrow": True, "frilly": True,
                    "heart": True}):
        count = triangles(build_silhouette({"a": archetype, **recipe}, 3, 100.0, 50.0))
        assert 0 < count <= TRIANGLE_BUDGET


# ── G4: every archetype has a builder, and 3D resolves as 2D does ─────────────


class TestCoverage:
    def test_every_archetype_of_the_tables_has_a_builder(self) -> None:
        assert set(ARCHETYPE_BUILDERS) == ARCHETYPES
        assert set(FOLIAGE_TIP_WEIGHT) == ARCHETYPES
        assert len(ARCHETYPES) == 13

    def test_a_new_archetype_cannot_fall_back_silently(self) -> None:
        with pytest.raises(ValueError, match="no 3D builder for archetype 'bonsai'"):
            build_silhouette({"a": "bonsai", "pal": "fresh"}, 1, 100.0, 50.0)

    def test_a_record_is_resolved_by_the_2d_rule(self) -> None:
        """Every answer of the pre-extraction 2D resolver for a plant kind — the
        builder resolves the record's params to the same sprite."""
        checked = 0
        for species, kind, category, expected in GOLDEN:
            if kind not in PLANT_KINDS:
                continue
            params: dict[str, object] = {PARAM_SEED: "x", PARAM_RADIUS_CM: 30.0}
            if species:
                params[PARAM_SPECIES_NAME] = species
            if category:
                params[PARAM_PLANT_CATEGORY] = category
            record = dataclasses.replace(
                record_for(SpriteRef("species", "tomato"), 50.0, 30.0),
                kind=kind, params=Params(params))  # type: ignore[arg-type]
            sprite = plant_sprite(record)
            assert sprite is not None and f"{sprite.group}/{sprite.name}" == expected
            assert sprite == resolve_sprite(kind, species, category, available=has_recipe)
            checked += 1
        assert checked > 1400

    def test_a_category_fallback_gets_the_category_s_recipe(self) -> None:
        """Echinacea has no sprite of its own: 2D draws the flowering perennial,
        so 3D builds the flowering perennial's recipe."""
        record = record_for(SpriteRef("species", "tomato"), 90.0, 25.0,
                            **{PARAM_SPECIES_NAME: "Echinacea"})
        assert plant_sprite(record) == SpriteRef("categories", "flowering_perennial")
        assert plant_archetype(record) == "mound"

    def test_register_takes_the_plant_kinds(self) -> None:
        registry = BuilderRegistry()
        register(registry)
        assert registry.kinds() == PLANT_KINDS == frozenset({"TREE", "SHRUB", "PERENNIAL"})
        for kind in PLANT_KINDS:
            assert registry.builder_for(kind) is build_plant
        assert registry.builder_for("HOUSE") is default_builder
        with pytest.raises(ValueError):
            register(registry)  # two packages claiming the plants is a bug

    def test_a_record_that_is_not_a_plant_circle_gets_the_default_prism(self) -> None:
        tomato = record_for(SpriteRef("species", "tomato"), 120.0, 40.0)
        house = dataclasses.replace(tomato, kind="HOUSE")
        assert build_plant(house) == default_builder(house)
        assert plant_archetype(house) is None and plant_sprite(house) is None

    @pytest.mark.parametrize("radius", [None, 0.0, -5.0, True])
    def test_a_plant_without_a_canopy_circle_has_one_answer(self, radius: object) -> None:
        """``plant_archetype`` says what ``build_plant`` builds — for a plant record
        with no usable radius too: the default builder, so None (it said "mound"
        while the builder returned the prism)."""
        params: dict[str, object] = {PARAM_SEED: "x", PARAM_SPECIES_NAME: "tomato"}
        if radius is not None:
            params[PARAM_RADIUS_CM] = radius
        tomato = record_for(SpriteRef("species", "tomato"), 120.0, 40.0)
        for height in (120.0, None):
            record = dataclasses.replace(tomato, height_cm=height, params=Params(params))  # type: ignore[arg-type]
            assert plant_sprite(record) == SpriteRef("species", "tomato")   # it IS a plant
            assert plant_archetype(record) is None
            assert build_plant(record) == default_builder(record)

    def test_a_failing_archetype_is_contained_by_the_sync(self) -> None:
        registry = BuilderRegistry()
        register(registry)
        sync = SceneSync(RecordingSink(), registry, validate=True)
        good = record_for(SpriteRef("species", "tomato"), 120.0, 40.0, "good")
        bad = record_for(SpriteRef("species", "lettuce"), 20.0, 12.0, "bad")
        with patch.dict(SPECIES, {"lettuce": {"a": "bonsai"}}):
            sync.apply({good.item_id: good, bad.item_id: bad})
        assert list(sync.failures) == [bad.item_id]
        assert "bonsai" in str(sync.failures[bad.item_id].error)
        assert isinstance(sync.sink, RecordingSink)
        assert sync.sink.item(bad.item_id).parts == ()
        assert triangles(sync.sink.item(good.item_id).parts) > 0


# ── G5: determinism and no clones ─────────────────────────────────────────────


@pytest.mark.parametrize("sprite", ALL_SPRITES[::6], ids=lambda s: s.name)
def test_same_record_same_mesh(sprite: SpriteRef) -> None:
    height, spread = MATURE[sprite]
    record = record_for(sprite, height, spread / 2.0, "same")
    first, second = build_plant(record), build_plant(record)
    assert [p.name for p in first] == [p.name for p in second]
    for a, b in zip(first, second, strict=True):
        assert a.mesh.vertex_count == b.mesh.vertex_count
        for name in ("positions", "normals", "colors", "uvs"):
            assert float(np.abs(getattr(a.mesh, name) - getattr(b.mesh, name)).max()) <= 1e-6
        assert np.array_equal(a.mesh.indices, b.mesh.indices)


def test_the_seed_is_the_record_s_seed_param() -> None:
    sprite = SpriteRef("species", "apple_tree")
    a = build_plant(record_for(sprite, 400.0, 150.0, "one"))
    again = build_plant(dataclasses.replace(record_for(sprite, 400.0, 150.0, "one"),
                                            item_id="another-id"))
    b = build_plant(record_for(sprite, 400.0, 150.0, "two"))
    assert a == again and a != b
    assert item_seed("one", "plant") != item_seed("two", "plant")


#: No clones. The metric: IoU of two plants' side silhouettes (x–z projection, a
#: 32 × 32 occupancy grid over the shared bounding box). A clone — or a copy
#: turned about its axis, for a round shape — scores 1.0; two perfect hedge drums
#: scored 0.999 before the hedge was cut by hand (the instrument bites).
#:
#: Twelve ids per recipe at its mature size: 66 pairs a recipe, 8,118 in all.
#: Measured 2026-10-11 (``scripts/measure_plant_art_3d.py clones`` prints the same
#: table for any number of ids; over 30 ids — 53,505 pairs — the maximum was
#: 0.975, a hedge, and 99 % of all pairs were below 0.952):
#:
#:   archetype  pairs  max    p95    median  share above 0.97
#:   allium       330  0.831  0.801  0.746   0.00 %
#:   canopy      1254  0.952  0.930  0.881   0.00 %
#:   climber      462  0.941  0.923  0.778   0.00 %
#:   conifer      264  0.936  0.928  0.900   0.00 %
#:   feathery     396  0.762  0.743  0.689   0.00 %
#:   flower        66  0.760  0.681  0.387   0.00 %
#:   grass        462  0.916  0.890  0.839   0.00 %
#:   ground        66  0.953  0.936  0.887   0.00 %
#:   head         198  0.917  0.906  0.875   0.00 %
#:   hedge         66  0.966  0.957  0.926   0.00 %
#:   mound       3432  0.966  0.941  0.895   0.00 %
#:   palm          66  0.894  0.856  0.722   0.00 %
#:   rosette     1056  0.939  0.922  0.893   0.00 %
#:
#: Five fixed ids with 0.005 of headroom said less than that: over other ids a
#: hedge section reached 0.984 (review round 1). A clipped hedge and a flat mat
#: are the two shapes that repeat by nature; the bounds below are theirs.
CLONE_IDS = tuple(f"clone-{n}" for n in range(12))
#: No pair of any recipe is more alike than this,
CLONE_IOU_MAX = 0.985
#: 95 % of an archetype's pairs are less alike than this,
CLONE_P95_MAX = 0.97
#: and at most this share of an archetype's pairs is more than 97 % alike.
CLONE_ABOVE_97_MAX = 0.03


@cache
def clone_pairs(sprite: SpriteRef) -> tuple[float, ...]:
    """Side-silhouette IoU of every pair of ``CLONE_IDS`` of one recipe at its mature size."""
    height, spread = MATURE[sprite]
    radius = spread / 2.0
    shapes = np.array([
        side_silhouette(build_silhouette(recipe_of(sprite), plant_seed(seed), height, radius),
                        height, radius).reshape(-1) for seed in CLONE_IDS], dtype=np.float64)
    shared = shapes @ shapes.T
    area = shapes.sum(axis=1)
    iou = shared / (area[:, None] + area[None, :] - shared)
    assert np.allclose(np.diag(iou), 1.0)                # a plant is its own clone
    return tuple(float(value) for value in iou[np.triu_indices(len(CLONE_IDS), 1)])


@pytest.mark.parametrize("sprite", ALL_SPRITES, ids=lambda s: f"{s.group}/{s.name}")
def test_two_ids_are_not_clones(sprite: SpriteRef) -> None:
    worst = max(clone_pairs(sprite))
    assert worst <= CLONE_IOU_MAX, f"{sprite.name}: two ids share {worst:.3f} of their silhouette"


def test_how_alike_two_ids_are_per_archetype() -> None:
    """The distribution the texts state — per archetype, over twelve ids a recipe."""
    pairs: dict[str, list[float]] = {archetype: [] for archetype in sorted(ARCHETYPES)}
    for sprite in ALL_SPRITES:
        pairs[recipe_of(sprite)["a"]].extend(clone_pairs(sprite))
    print("\narchetype  pairs  max    p95    median  share above 0.97")
    for archetype, values in pairs.items():
        alike = np.array(values)
        above = float((alike > 0.97).mean())
        print(f"{archetype:9s} {len(alike):6d}  {alike.max():.3f}  {np.percentile(alike, 95):.3f}  "
              f"{np.median(alike):.3f}   {100 * above:.2f} %")
        assert float(alike.max()) <= CLONE_IOU_MAX, archetype
        assert float(np.percentile(alike, 95)) <= CLONE_P95_MAX, archetype
        assert above <= CLONE_ABOVE_97_MAX, archetype
    everything = np.concatenate([np.array(values) for values in pairs.values()])
    assert len(everything) == 66 * len(ALL_SPRITES)
    assert float((everything > 0.97).mean()) <= 0.002      # measured: 0.00 %


# ── G6: the honest placeholder ────────────────────────────────────────────────


class TestPlaceholder:
    @pytest.mark.parametrize("radius", [4.0, 30.0, 100.0, 104.2, 400.0, 1500.0])
    def test_no_height_gives_the_placeholder(self, radius: float) -> None:
        record = record_for(SpriteRef("categories", "round_deciduous"), None, radius)
        assert plant_archetype(record) == PLACEHOLDER
        parts = verify_builder(build_plant, record, height_tolerance=0.03)
        assert len(parts) == 1
        marker = parts[0]
        assert (marker.name, marker.material_kind) == (PART_PLACEHOLDER, "vc")
        assert marker.casts_shadow is False and marker.tinted is False
        low, high = marker.mesh.bounds()
        assert low[2] == 0.0
        assert high[0] - low[0] == pytest.approx(2 * radius, rel=1e-6)   # the canopy footprint
        assert high[1] - low[1] == pytest.approx(2 * radius, rel=1e-6)
        expected = min(PLACEHOLDER_HEIGHT_FRACTION * 2 * radius, PLACEHOLDER_MAX_HEIGHT_CM)
        assert high[2] == pytest.approx(expected, rel=1e-6)
        assert high[2] <= 25.0 and high[2] / (2 * radius) <= 0.12 + 1e-9  # low, always
        assert (PLACEHOLDER_HEIGHT_FRACTION, PLACEHOLDER_MAX_HEIGHT_CM) == (0.12, 25.0)
        assert float(np.hypot(marker.mesh.positions[:, 0], marker.mesh.positions[:, 1]).max()) \
            == pytest.approx(radius, rel=1e-6)                           # round, inside the canopy
        assert marker.mesh.triangle_count <= TRIANGLE_BUDGET

    def test_it_is_a_smooth_dome_facing_out(self) -> None:
        marker = build_plant(record_for(SpriteRef("species", "oak"), None, 80.0))[0]
        dots, flat = normal_vs_winding(marker.mesh)
        assert float(dots.min()) > 0.9 and not flat.any()
        # z falls monotonically with the distance from the axis: one smooth cap
        r = np.hypot(marker.mesh.positions[:, 0], marker.mesh.positions[:, 1])
        order = np.argsort(r)
        assert (np.diff(marker.mesh.positions[order, 2]) <= 1e-4).all()

    @pytest.mark.parametrize("radius", [4.0, 80.0, 1500.0])
    def test_its_rim_is_the_canopy_circle(self, radius: float) -> None:
        """Seen from above the marker IS the canopy circle: the polygon of its rim
        covers the circle to within 0.5 % (48 sides: 99.7 %). The first cut had 24
        sides — 98.9 % — and its rim read as a polygon at close range through the
        spike's engine; nothing measured said so."""
        marker = build_plant(record_for(SpriteRef("species", "oak"), None, radius))[0]
        points = marker.mesh.positions.astype(np.float64)
        rim = points[points[:, 2] <= 1e-9 * radius][:, :2]
        rim = rim[np.argsort(np.arctan2(rim[:, 1], rim[:, 0]))]
        x, y = rim[:, 0], rim[:, 1]
        area = 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))
        assert 0.995 * np.pi * radius**2 <= area <= np.pi * radius**2

    def test_its_colour_is_one_neutral_tone_no_plant_has(self) -> None:
        marker = build_plant(record_for(SpriteRef("species", "oak"), None, 80.0))[0]
        colours = marker.mesh.colors[:, :3]
        assert float(np.abs(colours - colours[0]).max()) == 0.0          # uniform
        tone = srgb_to_linear(FLOWERS["white"][0]) * PLACEHOLDER_SHADE   # the 2D white, in shade
        np.testing.assert_allclose(colours[0], tone, atol=1e-6)
        assert PLACEHOLDER_SHADE == 0.5   # x 0.7 rendered at luma 232 at noon (spike engine)
        srgb = linear_to_srgb(colours[0].astype(np.float64))
        assert float(srgb.min() * 255) >= 40 and float(srgb.max() * 255) <= 240   # albedo range
        palest = min(saturation(mean_srgb(part(build_plant(
            record_for(sprite, *_size(sprite))), PART_FOLIAGE).mesh)) for sprite in ALL_SPRITES)
        assert saturation(srgb) < 0.10
        assert saturation(srgb) < 0.5 * palest, (saturation(srgb), palest)

    def test_it_has_no_individuality_and_no_plant_parts(self) -> None:
        """A marker is not a plant: two ids give the same dome, whatever species
        or category the record names — only the canopy radius is read."""
        a = build_plant(record_for(SpriteRef("species", "oak"), None, 80.0, "one"))
        b = build_plant(record_for(SpriteRef("categories", "palm"), None, 80.0, "two"))
        assert a == b
        real = build_plant(record_for(SpriteRef("species", "oak"), 900.0, 80.0, "one"))
        assert {p.name for p in real} == {PART_WOOD, PART_FOLIAGE}
        assert {p.name for p in a} == {PART_PLACEHOLDER}

    def test_a_height_turns_the_placeholder_into_the_plant(self) -> None:
        registry = BuilderRegistry()
        register(registry)
        sink = RecordingSink()
        sync = SceneSync(sink, registry, validate=True)
        blank = record_for(SpriteRef("categories", "conifer"), None, 90.0, "it")
        sync.apply({blank.item_id: blank})
        assert [p.name for p in sink.item(blank.item_id).parts] == [PART_PLACEHOLDER]
        sink.clear_calls()
        sync.apply({blank.item_id: dataclasses.replace(blank, height_cm=600.0)})
        assert sink.ops() == [("begin", None), ("replace_geometry", blank.item_id),
                              ("commit", None)]
        assert {p.name for p in sink.item(blank.item_id).parts} == {PART_WOOD, PART_FOLIAGE}


def _size(sprite: SpriteRef) -> tuple[float, float]:
    height, spread = MATURE[sprite]
    return height, spread / 2.0


# ── no seasonal claim, colours from the tables ────────────────────────────────


class TestColoursAndSeason:
    @pytest.mark.parametrize("sprite", ALL_SPRITES, ids=lambda s: f"{s.group}/{s.name}")
    def test_fruit_and_flowers_are_not_built(self, sprite: SpriteRef) -> None:
        """v0 makes no seasonal claim: a recipe without its feature keys (fruit,
        flowers, pods, umbels, bulbs …) and without the flower head of the
        ``head`` archetype builds the identical mesh."""
        recipe = recipe_of(sprite)
        bare = {k: v for k, v in recipe.items()
                if k not in FEATURE_KEYS | {"head", "head_scale", "rib_color", "rib_width",
                                            "shadow_col", "r"}}
        height, radius = _size(sprite)
        assert build_silhouette(recipe, 5, height, radius) == build_silhouette(
            bare, 5, height, radius)

    @pytest.mark.parametrize("sprite", ALL_SPRITES[::4], ids=lambda s: s.name)
    def test_foliage_colours_stay_inside_the_recipe_s_palette(self, sprite: SpriteRef) -> None:
        """Every foliage vertex is a mix of the palette's four leaf anchors, darkened
        by a hue-neutral shade: its hue lies between the anchors' hues."""
        palette = PALETTES[palette_key(recipe_of(sprite))]
        anchors = [hue_deg(linear_to_srgb(srgb_to_linear(palette[k]).astype(np.float64)))
                   for k in ("t0", "t1", "b0", "b1")]
        foliage = part(build_plant(record_for(sprite, *_size(sprite))), PART_FOLIAGE).mesh
        srgb = linear_to_srgb(foliage.colors[:, :3].astype(np.float64))
        hues = np.array([hue_deg(c) for c in srgb[:: max(1, len(srgb) // 300)]])
        centre = anchors[0]
        offsets = (hues - centre + 180.0) % 360.0 - 180.0
        anchor_offsets = [(h - centre + 180.0) % 360.0 - 180.0 for h in anchors]
        assert offsets.min() >= min(anchor_offsets) - 1.5
        assert offsets.max() <= max(anchor_offsets) + 1.5
        assert float(foliage.colors[:, 3].min()) == 1.0

    def test_foliage_albedo_is_neither_black_nor_white(self) -> None:
        darkest, lightest = 255.0, 0.0
        for sprite in ALL_SPRITES:
            foliage = part(build_plant(record_for(sprite, *_size(sprite))), PART_FOLIAGE).mesh
            luma = linear_to_srgb(foliage.colors[:, :3].astype(np.float64)) @ [
                0.2126, 0.7152, 0.0722] * 255.0
            darkest, lightest = min(darkest, float(luma.min())), max(lightest, float(luma.max()))
        # the style table's albedo range (sRGB ~40-240); measured 43 .. 227 over 5 seeds
        assert darkest >= 40.0 and lightest <= 240.0, (darkest, lightest)

    def test_wood_is_the_2d_bark(self) -> None:
        """The plant tables hold no wood colour (a top-down sprite shows no trunk);
        v0 takes the mean colour of the 2D BARK fill texture, in linear light."""
        from PIL import Image

        pixels = np.asarray(Image.open(BARK_TEXTURE).convert("RGB"), np.float64) / 255.0
        linear = np.vectorize(srgb_channel_to_linear)(pixels).reshape(-1, 3).mean(axis=0)
        mean_hex = "#" + "".join(f"{round(float(c) * 255):02x}" for c in linear_to_srgb(linear))
        assert mean_hex == BARK_SRGB == "#5d432d"
        for name in ("oak", "pine"):
            wood = part(build_plant(record_for(SpriteRef("species", name), 900.0, 300.0)),
                        PART_WOOD).mesh
            np.testing.assert_allclose(wood.colors[:, :3], np.broadcast_to(
                srgb_to_linear(BARK_SRGB), wood.colors[:, :3].shape), atol=1e-6)

    def test_only_trees_have_wood(self) -> None:
        with_wood = {recipe_of(s)["a"] for s in ALL_SPRITES
                     if any(p.name == PART_WOOD for p in build_plant(
                         record_for(s, *_size(s))))}
        assert with_wood == {"canopy", "conifer", "palm"}

    def test_the_vectorised_transfer_function_is_the_pipeline_s(self) -> None:
        grid = np.linspace(0.0, 1.0, 257)
        expected = np.array([srgb_channel_to_linear(float(v)) for v in grid])
        np.testing.assert_allclose(plants._srgb_to_linear(grid), expected, rtol=0, atol=1e-12)


# ── conventions the engine relies on ──────────────────────────────────────────


class TestConventions:
    @pytest.mark.parametrize("sprite", ALL_SPRITES[::5], ids=lambda s: s.name)
    def test_wind_weight_rises_from_the_ground_to_the_top(self, sprite: SpriteRef) -> None:
        """uv.x = sway weight: 0 at the plant's base, 1 at its top (L1.4's wind
        shader); uv.y = phase in [0, 1). Wood does not sway."""
        height, radius = _size(sprite)
        for item in build_plant(record_for(sprite, height, radius)):
            uv, z = item.mesh.uvs, item.mesh.positions[:, 2]
            if item.name == PART_WOOD:
                assert float(np.abs(uv).max()) == 0.0
                continue
            np.testing.assert_allclose(uv[:, 0], np.clip(z / height, 0.0, 1.0), atol=1e-5)
            assert float(uv[:, 0].max()) == pytest.approx(1.0, abs=1e-5)
            assert float(uv[:, 1].min()) >= 0.0 and float(uv[:, 1].max()) < 1.0

    @pytest.mark.parametrize("name", ["oak", "spruce"])
    def test_wood_faces_outward(self, name: str) -> None:
        wood = part(build_plant(record_for(SpriteRef("species", name), 900.0, 300.0)),
                    PART_WOOD).mesh
        dots, _flat = normal_vs_winding(wood)
        assert float(dots.min()) > 0.5
        radial = wood.positions[:, :2] / np.maximum(
            np.linalg.norm(wood.positions[:, :2], axis=1, keepdims=True), 1e-9)
        assert float((radial * wood.normals[:, :2]).sum(axis=1).min()) > 0.0

    def test_the_trunk_stands_on_the_plan_position(self) -> None:
        """The crown is centred on the canopy circle; the trunk is not dragged
        along with a lopsided crown — its foot is the item's position."""
        for seed in SEEDS:
            wood = part(build_plant(record_for(SpriteRef("species", "oak"), 900.0, 300.0, seed)),
                        PART_WOOD).mesh
            foot = wood.positions[wood.positions[:, 2] < 1e-3]
            assert len(foot) >= 6
            np.testing.assert_allclose(foot[:, :2].mean(axis=0), [0.0, 0.0], atol=1e-3)

    def test_foliage_normals_are_bent_toward_the_crown(self) -> None:
        """A crown shades as one soft volume: its normals lean away from the
        crown's heart, not along each facet."""
        foliage = part(build_plant(record_for(SpriteRef("species", "oak"), 900.0, 300.0)),
                       PART_FOLIAGE).mesh
        heart = np.array([0.0, 0.0, 600.0])
        away = foliage.positions - heart
        away /= np.maximum(np.linalg.norm(away, axis=1, keepdims=True), 1e-9)
        assert float(((away * foliage.normals).sum(axis=1) > 0.2).mean()) > 0.9

    def test_shell_leaves_are_set_on_the_lobes(self) -> None:
        """A leaf of the shell starts on the outer surface of the lobes — not inside
        one (the first look through an engine showed bald crown tops: the leaves
        were there, buried) and not in the air between two (nothing floats) — and
        it faces the way the SURFACE faces there, not the way the ray came."""
        dice = plants._Dice(7)
        centers = np.array([[60.0, 0.0, 100.0], [-50.0, 30.0, 110.0], [0.0, -10.0, 150.0]])
        radii = np.array([[70.0, 70.0, 60.0], [65.0, 65.0, 55.0], [60.0, 60.0, 50.0]])
        origin = np.array([0.0, 0.0, 110.0])
        reach = plants._shell_points(dice, 400, -0.5) * np.array([120.0, 120.0, 90.0])
        points, normals = plants._on_lobes(dice, origin, reach, centers, radii)
        # per lobe: 1 on its surface, < 1 inside it, > 1 outside it
        level = np.sqrt((((points[:, None, :] - centers[None]) / radii[None]) ** 2).sum(axis=2))
        outermost = level.min(axis=1)
        assert outermost.min() >= 0.90 and outermost.max() <= 1.12, (
            outermost.min(), outermost.max())
        np.testing.assert_allclose(np.linalg.norm(normals, axis=1), 1.0, atol=1e-9)
        missed, _lobe = plants._lobe_surface(origin + [0.0, 0.0, 1000.0], reach, centers, radii)
        assert float(missed.max()) == 0.0      # a ray that meets no lobe says so
        # one flat lobe — a cushion 10 times wider than tall: its surface looks UP
        # nearly everywhere, though the rays from its heart leave nearly level
        flat_centre, flat_radii = np.array([[0.0, 0.0, 5.0]]), np.array([[100.0, 100.0, 10.0]])
        level_rays = plants._shell_points(dice, 400, 0.0) * np.array([100.0, 100.0, 10.0])
        _points, flat_normals = plants._on_lobes(dice, np.array([0.0, 0.0, 5.0]), level_rays,
                                                 flat_centre, flat_radii)
        assert float(np.median(flat_normals[:, 2])) > 0.95
        assert float(np.median(plants._unit(level_rays)[:, 2])) < 0.20

    def test_a_builder_reads_only_the_geometry_part(self) -> None:
        record = record_for(SpriteRef("species", "tomato"), 120.0, 40.0)
        verify_builder(build_plant, record, height_tolerance=0.03)   # transform, material, ids
        with pytest.raises(BuilderContractError):                     # the instrument bites
            verify_builder(lambda r: build_plant(dataclasses.replace(
                r, height_cm=(r.height_cm or 0) + r.transform.base_cm)), record,
                height_tolerance=0.03)


# ── G3: the foliage hue is the 2D sprite's ────────────────────────────────────


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("generate_plant_sprites_for_hue", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render(svg: str, size: int = 96) -> np.ndarray:
    """A sprite as the canvas draws it (QSvgRenderer), premultiplied RGBA floats."""
    from PyQt6.QtCore import QByteArray, QRectF
    from PyQt6.QtGui import QImage, QPainter
    from PyQt6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    assert renderer.isValid()
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    bits = image.constBits()
    bits.setsize(size * size * 4)
    bgra = np.frombuffer(bits, np.uint8).reshape(size, size, 4).astype(np.float64)
    return bgra[:, :, [2, 1, 0, 3]].copy()


def sprite_foliage_rgb(generator: ModuleType, sprite: SpriteRef) -> np.ndarray:
    """The summed FOLIAGE ink of a sprite as rendered — no hand-made mask.

    The shipped SVG is rendered, then the same sprite with its palette replaced
    by black. Source-over compositing is linear in each layer's colour, so the
    difference is exactly the ink that depends on the palette (leaves, base
    cloud, occlusion, heart); fruit, flowers, bulbs, heads and the ground shadow
    cancel. HSV hue is invariant to scale and to a grey offset, so the hue of
    the sum is the foliage's hue.
    """
    recipe = recipe_of(sprite)
    shipped = (PLANTS_DIR / sprite.group / f"{sprite.name}.svg").read_text(encoding="utf-8")
    name = palette_key(recipe)
    black = dict.fromkeys(PALETTES[name], "#000000")
    with patch.dict(PALETTES, {name: black}):
        blacked = (generator.build_hedge(sprite.name) if recipe["a"] == "hedge"
                   else generator.build_sprite(sprite.name, recipe))
    real, dark = _render(shipped), _render(blacked)
    assert np.array_equal(real[:, :, 3], dark[:, :, 3])     # the same shapes, other colours
    ink = (real - dark)[:, :, :3]
    assert ink.min() > -1.5 and (ink.sum(axis=2) > 3.0).mean() > 0.10
    return ink.sum(axis=(0, 1))


def test_foliage_hue_is_the_2d_sprite_s(qtbot) -> None:
    """Gate G3 over all 123 sprites: the area-weighted mean of the foliage vertex
    colours (in sRGB) has the hue of the sprite's rendered foliage, ± 10°.

    The reference is the RENDERED sprite, not the palette entries: the palette is
    what the builder mixes from, so comparing against it would test the mixing
    against itself. A palette spans up to 41° between its anchors (``dark``:
    105° … 148°); where in that span a sprite's foliage sits is decided by its
    drawing code, and only the render knows."""
    generator = _load_generator()
    worst: tuple[float, str] = (0.0, "")
    for sprite in ALL_SPRITES:
        reference = hue_deg(sprite_foliage_rgb(generator, sprite))
        for seed in SEEDS[:2]:
            foliage = part(build_plant(record_for(sprite, *_size(sprite), seed)),
                           PART_FOLIAGE).mesh
            error = hue_difference(hue_deg(mean_srgb(foliage)), reference)
            worst = max(worst, (error, sprite.name))
            assert error <= 10.0, (
                f"{sprite.name}: foliage hue {hue_deg(mean_srgb(foliage)):.1f}° vs the sprite's "
                f"{reference:.1f}°")
    print(f"\nworst foliage hue error: {worst[0]:.2f}° ({worst[1]})")
    assert worst[0] <= 10.0


def test_the_hue_instrument_tells_palettes_apart(qtbot) -> None:
    """Positive control: built in another palette, a plant fails the gate."""
    generator = _load_generator()
    reference = hue_deg(sprite_foliage_rgb(generator, SpriteRef("species", "tomato")))  # fresh
    for other in ("yellow", "teal", "autumn"):
        wrong = part(build_silhouette({"a": "mound", "pal": other}, 1, 150.0, 45.0),
                     PART_FOLIAGE).mesh
        assert hue_difference(hue_deg(mean_srgb(wrong)), reference) > 10.0
    right = part(build_silhouette({"a": "mound", "pal": "fresh"}, 1, 150.0, 45.0),
                 PART_FOLIAGE).mesh
    assert hue_difference(hue_deg(mean_srgb(right)), reference) <= 10.0


# ── built inside the plant's size: leaves, lobes, dice ────────────────────────


def _leaf_quads(geo: object) -> np.ndarray:
    return geo.pos.reshape(-1, 4, 3)   # type: ignore[attr-defined]  # base, edge, tip, edge


class TestLeavesStayInside:
    """``_leaves`` builds a leaf inside the plant's cylinder by TURNING it — its
    length and width are what was asked for, its place is what the plant allows."""

    def test_a_leaf_with_no_headroom_lies_down_and_keeps_its_size(self) -> None:
        dice = plants._Dice(3)
        count, radius, height = 600, 100.0, 10.0          # a mat 10 cm high, 2 m wide
        base = np.column_stack([dice.u(-40.0, 40.0, count), dice.u(-40.0, 40.0, count),
                                dice.u(0.0, height, count)])
        level = dice.u(0.0, 2.0 * np.pi, count)           # facing sideways: the leaves stand on edge
        outward = np.column_stack([np.cos(level), np.sin(level), np.zeros(count)])
        leaves = plants._leaves(dice, base, outward, np.full(count, 30.0), 0.6,
                                np.array([0.0, 0.0, 5.0]), 0.0, radius, height)
        quads = _leaf_quads(leaves)
        assert float(quads[..., 2].min()) >= plants._LEAF_FLOOR * height - 1e-9
        assert float(quads[..., 2].max()) <= height + 1e-9
        assert float(quads[:, 0, 2].min()) >= plants._LEAF_LIFT * height - 1e-9   # the bases
        # three times as long as the plant is tall — and still 30 cm long, 18 cm wide
        np.testing.assert_allclose(np.linalg.norm(quads[:, 2] - quads[:, 0], axis=1), 30.0, rtol=1e-9)
        np.testing.assert_allclose(np.linalg.norm(quads[:, 1] - quads[:, 3], axis=1), 18.0, rtol=1e-9)
        # a plant with room is left alone: the same leaves on a plant 10 m tall stand
        tall = plants._leaves(plants._Dice(3), base + [0.0, 0.0, 500.0], outward,
                              np.full(count, 30.0), 0.6, np.array([0.0, 0.0, 5.0]), 0.0, radius, 1000.0)
        assert float(np.ptp(_leaf_quads(tall)[..., 2], axis=1).mean()) > 3.0 * float(
            np.ptp(quads[..., 2], axis=1).mean())

    def test_a_leaf_at_the_canopy_s_edge_turns_in_its_plane(self) -> None:
        dice = plants._Dice(4)
        count, radius, height = 600, 100.0, 300.0
        turn = dice.u(0.0, 2.0 * np.pi, count)
        base = np.column_stack([radius * np.cos(turn), radius * np.sin(turn),     # ON the circle
                                dice.u(60.0, 240.0, count)])
        flat = np.tile([0.0, 0.0, 1.0], (count, 1))                                # lying level
        leaves = plants._leaves(dice, base, flat, np.full(count, 40.0), 0.6,
                                np.array([0.0, 0.0, 150.0]), 0.0, radius, height)
        quads = _leaf_quads(leaves)
        beyond = np.hypot(quads[..., 0], quads[..., 1]).max(axis=1) / radius
        # level leaves, 0.4 radii long: one of the four headings always lies inside
        assert float(beyond.max()) <= 1.0 + 1e-9
        np.testing.assert_allclose(np.linalg.norm(quads[:, 2] - quads[:, 0], axis=1), 40.0, rtol=1e-9)

    def test_no_leaf_is_longer_than_the_plant_is_wide(self) -> None:
        dice = plants._Dice(5)
        base = np.zeros((50, 3)) + [0.0, 0.0, 400.0]
        leaves = plants._leaves(dice, base, np.tile([0.0, 0.0, 1.0], (50, 1)), np.full(50, 500.0),
                                0.6, np.array([0.0, 0.0, 300.0]), 0.0, 20.0, 800.0)
        quads = _leaf_quads(leaves)
        np.testing.assert_allclose(np.linalg.norm(quads[:, 2] - quads[:, 0], axis=1),
                                   plants._LEAF_LONGEST * 20.0, rtol=1e-9)

    def test_a_lobe_on_the_ground_has_no_sole(self) -> None:
        """A volume that reaches below the ground sits on it, and its hidden sole —
        the triangles pressed flat at z = 0 — is not built (6.7 % of a mound's
        triangles lay there, coplanar with the ground)."""
        dice = plants._Dice(6)
        centers = np.array([[0.0, 0.0, 20.0], [40.0, 0.0, 10.0]])
        radii = np.array([[50.0, 50.0, 40.0], [30.0, 30.0, 35.0]])
        heart = np.array([0.0, 0.0, 20.0])
        whole = plants._lobes(centers, radii, heart, dice, 0.0, skin=-1.0)        # nothing dropped
        lobes = plants._lobes(centers, radii, heart, plants._Dice(6), 0.0, skin=0.6)
        tops = lobes.pos[lobes.tri][:, :, 2].max(axis=1)
        assert float(tops.min()) > 0.6
        assert float(lobes.pos[:, 2].min()) == 0.0                                 # it stands on the ground
        assert len(np.unique(lobes.tri)) == len(lobes.pos)                         # no orphan vertex
        flat = int((whole.pos[whole.tri][:, :, 2].max(axis=1) <= 0.0).sum())
        assert flat > 0 and len(lobes.tri) <= len(whole.tri) - flat                # the instrument bites

    def test_the_tallest_lobe_ends_at_the_height(self) -> None:
        centers = np.array([[0.0, 0.0, 30.0], [10.0, 0.0, 55.0]])
        radii = np.array([[20.0, 20.0, 25.0], [20.0, 20.0, 30.0]])
        plants._top_out(centers, radii, 100.0)
        assert float((centers[:, 2] + radii[:, 2]).max()) == pytest.approx(100.0, rel=1e-12)

    def test_a_blade_is_as_tall_as_it_is_told(self) -> None:
        """The arch nods the tip; it does not shorten the blade (it did: 0.88)."""
        base = np.zeros((3, 3))
        direction = np.array([[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]])
        blades = plants._ribbons(base, direction, np.full(3, 20.0), np.array([50.0, 80.0, 100.0]),
                                 np.full(3, 4.0), 5, np.zeros(3), np.zeros(3), arch=0.12)
        tops = blades.pos.reshape(3, -1, 3)[:, :, 2].max(axis=1)
        np.testing.assert_allclose(tops, [50.0, 80.0, 100.0], rtol=1e-12)

    def test_the_dice_name_their_generator(self) -> None:
        """PCG64 by name — numpy's ``default_rng`` may pick another generator one day,
        and every plant in every plan would change its shape."""
        dice = plants._Dice(12345)
        assert isinstance(dice._rng.bit_generator, np.random.PCG64)
        assert dice.uf(0.0, 1.0) == 0.22733602246716966
        assert dice.u(0.0, 1.0, 3).tolist() == [0.31675833970975287, 0.7973654573327341,
                                                0.6762546707509746]


# ── the shape cache: a plant that grows is the same shape, scaled ─────────────


class TestShapeCache:
    @pytest.mark.parametrize("name", ["apple_tree", "lettuce", "tomato", "leek", "carrot",
                                      "sunflower", "spruce"])
    def test_growing_in_proportion_is_an_exact_scale(self, name: str) -> None:
        """The same plant at twice its size is the same shape, twice as large:
        positions × 2 (to float32) and the very same colour, uv and index
        arrays — nothing was rebuilt, and nothing but positions was computed."""
        recipe = SPECIES[name]
        small = build_silhouette(recipe, 5, 80.0, 30.0)
        large = build_silhouette(recipe, 5, 160.0, 60.0)
        assert [p.name for p in small] == [p.name for p in large]
        for a, b in zip(small, large, strict=True):
            np.testing.assert_allclose(b.mesh.positions, 2.0 * a.mesh.positions, rtol=2e-6, atol=1e-5)
            assert np.array_equal(a.mesh.normals, b.mesh.normals)
            assert b.mesh.colors is a.mesh.colors and b.mesh.uvs is a.mesh.uvs
            assert b.mesh.indices is a.mesh.indices

    def test_the_size_is_never_misstated(self) -> None:
        """The PROPORTION a shape is built at is rounded to a 5 % step; the size is
        not. Over 600 sizes of any proportion from 1 : 25 to 40 : 1 the top is the
        height and the widest span the canopy diameter, to float32 — the rounding
        cannot move a number the plan states, let alone past the ± 3 % / ± 10 %
        gates — and the form is stretched by at most half a step (2.5 %)."""
        rng = np.random.Generator(np.random.PCG64(1))
        recipes = [recipe_of(sprite) for sprite in ALL_SPRITES[::9]]
        worst = 0.0
        for n in range(600):
            radius = float(10.0 ** rng.uniform(0.0, 3.0))
            height = radius * float(10.0 ** rng.uniform(-1.4, 1.6))
            recipe = recipes[n % len(recipes)]
            parts = build_silhouette(recipe, n, height, radius)
            low, high = bbox(parts)
            assert high[2] == pytest.approx(height, rel=1e-6)
            assert low[2] == pytest.approx(0.0, abs=1e-6 * height)
            assert max(high[0] - low[0], high[1] - low[1]) == pytest.approx(2.0 * radius, rel=1e-6)
            for item in parts:
                item.validate()
            stretch = fit_report(recipe, n, height, radius).stretch
            worst = max(worst, abs(math.log(stretch)))
        assert 0.4 * math.log(PROPORTION_STEP) < worst <= 0.5 * math.log(PROPORTION_STEP) + 1e-9
        assert PROPORTION_STEP == 1.05

    def test_inside_a_proportion_step_the_shape_is_stretched_not_rebuilt(self) -> None:
        clear_shape_cache()
        recipe = SPECIES["tomato"]
        first = build_silhouette(recipe, 9, 100.0, 40.0)[0].mesh
        assert shape_cache_info() == (0, 1, 1)
        grown = build_silhouette(recipe, 9, 101.0, 40.0)[0].mesh          # 1 % taller: same step
        assert shape_cache_info() == (1, 1, 1)
        np.testing.assert_allclose(grown.positions, first.positions * np.float32([1.0, 1.0, 1.01]),
                                   rtol=2e-6, atol=1e-5)
        assert grown.colors is first.colors and grown.indices is first.indices
        np.testing.assert_allclose(np.linalg.norm(grown.normals, axis=1), 1.0, atol=1e-5)
        build_silhouette(recipe, 9, 120.0, 40.0)                           # 20 % taller: another step
        assert shape_cache_info() == (1, 2, 2)
        build_silhouette(recipe, 10, 100.0, 40.0)                          # another plant
        assert shape_cache_info() == (1, 3, 3)

    def test_remembering_changes_nothing(self) -> None:
        """Cold or warm, the same arguments give the same mesh, bit for bit."""
        recipe = SPECIES["apple_tree"]
        clear_shape_cache()
        cold = build_silhouette(recipe, 3, 500.0, 200.0)
        warm = build_silhouette(recipe, 3, 500.0, 200.0)
        clear_shape_cache()
        again = build_silhouette(recipe, 3, 500.0, 200.0)
        assert cold == warm == again
        assert shape_cache_info() == (0, 1, 1)

    def test_remembered_arrays_cannot_be_written(self) -> None:
        for item in build_silhouette(SPECIES["apple_tree"], 1, 500.0, 200.0):
            for name in ("positions", "normals", "colors", "uvs", "indices"):
                with pytest.raises(ValueError, match="read-only"):
                    getattr(item.mesh, name)[0] = 0

    def test_the_cache_is_bounded_and_an_entry_is_small(self) -> None:
        """Measured 2026-10-11 over the 123 recipes at their mature size: an entry
        holds 12 … 131 kB, 63 kB on average — the cache at most ~130 MB, and
        ~65 MB when full of average plants (``SHAPE_CACHE_SIZE`` says so)."""
        assert plants._built_shape.cache_info().maxsize == SHAPE_CACHE_SIZE == 1024
        held = [shape_bytes(recipe_of(sprite), 1, *_size(sprite)) for sprite in ALL_SPRITES]
        print(f"\nshape bytes: min {min(held)}, mean {sum(held) / len(held):.0f}, max {max(held)}")
        assert min(held) > 8_000 and max(held) < 150_000 and sum(held) / len(held) < 80_000

    def test_a_builder_reads_only_what_the_cache_is_keyed_on(self) -> None:
        """The cache's key and the recipe a builder is handed are one thing (``_form``):
        a builder cannot read a recipe key the key does not hold — it would be handed
        a remembered shape of a recipe that differed in exactly that key."""
        recipe = {"a": "mound", "pal": "dark", "leaf_scale": 0.7, "n_out": 20, "narrow": True,
                  "frilly": True, "heart": False, "fruit": ("tomato", 5), "r": 99.0}
        form = plants._form(recipe)
        seen = form.recipe()
        assert dict(seen) == {"leaf_scale": 0.7, "n_out": 20.0, "narrow": True, "frilly": True,
                              "heart": False}
        assert seen.get("n_out", 16.0) == 20.0
        for unread in ("fruit", "r", "pal", "a", "flowers", "head"):
            with pytest.raises(KeyError, match="may read only"):
                seen.get(unread)
        for key, other in (("leaf_scale", 1.2), ("n_out", 9), ("narrow", False), ("frilly", False),
                           ("heart", True), ("pal", "fresh"), ("a", "ground")):
            assert plants._form({**recipe, key: other}) != form, key
        assert plants._form({**recipe, "fruit": None, "r": 1.0, "flowers": ("pink", 3)}) == form
        assert hash(form) == hash(plants._form(dict(recipe)))
        # every recipe of the tables builds through it (a builder reading another key raises)
        for sprite in ALL_SPRITES:
            assert build_silhouette(recipe_of(sprite), 1, 100.0, 50.0)

    @pytest.mark.parametrize(("height", "radius"), [(0.0, 50.0), (-1.0, 50.0), (100.0, 0.0),
                                                    (float("nan"), 50.0), (100.0, float("inf"))])
    def test_a_size_that_is_no_size_is_refused(self, height: float, radius: float) -> None:
        with pytest.raises(ValueError, match="positive height and radius"):
            build_silhouette(SPECIES["tomato"], 1, height, radius)
