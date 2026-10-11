"""Plant v0 — archetype silhouettes from the shared recipes (Phase 17 L1.7, #385).

``core/scene3d/builders/plants.py`` turns one plant record into a readable,
cheap silhouette: the archetype and the colours come from the recipe the 2D
sprite is drawn from, the size from the plan. The look may be stylised; the
numbers may not lie. Gates (the story's G2 – G6):

- G2 truth and budget — every recipe of both tables × three sizes × five seeds:
  top = the plan's height (± 3 %), widest span = the canopy diameter (± 10 %),
  base at 0, a valid mesh, at most 2,000 triangles;
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
from collections import defaultdict
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
    TRIANGLE_BUDGET,
    build_plant,
    build_silhouette,
    plant_archetype,
    plant_sprite,
    register,
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
SIZES = {"seedling": 0.08, "mid": 0.5, "mature": 1.0}
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


def _mature_sizes() -> dict[SpriteRef, tuple[float, float]]:
    """Mature height × spread per sprite from the bundled species data — the
    largest species that reaches it — else the archetype's synthetic size."""
    sizes: dict[SpriteRef, tuple[float, float]] = {}
    for species in BUNDLED:
        resolved = resolve_recipe("PERENNIAL", species["common_name"], None)
        assert resolved is not None
        size = (float(species["max_height_cm"]), float(species["max_spread_cm"]))
        if resolved[0] not in sizes or size[0] * size[1] > np.prod(sizes[resolved[0]]):
            sizes[resolved[0]] = size
    for sprite in ALL_SPRITES:
        sizes.setdefault(sprite, ARCHETYPE_SIZE[recipe_of(sprite)["a"]])
    return sizes


MATURE = _mature_sizes()


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

TRIANGLES_SEEN: dict[str, int] = defaultdict(int)


@pytest.mark.parametrize("sprite", ALL_SPRITES, ids=lambda s: f"{s.group}/{s.name}")
def test_every_recipe_fits_the_plan_s_numbers(sprite: SpriteRef) -> None:
    """Every recipe × seedling / mid / mature × five seeds: the bounding box IS
    the data, the mesh is sound, and it stays inside the archetype budget."""
    recipe = recipe_of(sprite)
    mature_height, mature_spread = MATURE[sprite]
    for factor in SIZES.values():
        height, radius = mature_height * factor, mature_spread * factor / 2.0
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
            # than 10 % (measured over this matrix: at most 7 %, a palm)
            assert abs(high[0] + low[0]) <= 1e-4 * radius + 1e-5
            assert abs(high[1] + low[1]) <= 1e-4 * radius + 1e-5
            reach = max(float(np.hypot(p.mesh.positions[:, 0], p.mesh.positions[:, 1]).max())
                        for p in parts)
            assert reach <= 1.10 * radius
            for item in parts:
                item.validate()
                assert item.casts_shadow and not item.tinted
                assert item.material_kind == ("vc" if item.name == PART_WOOD else "foliage")
            count = triangles(parts)
            assert 0 < count <= TRIANGLE_BUDGET == 2000
            TRIANGLES_SEEN[recipe["a"]] = max(TRIANGLES_SEEN[recipe["a"]], count)


def test_the_budget_was_measured_for_every_archetype() -> None:
    """Runs after the matrix above (file order): every archetype was built, and
    the largest plant of each is on record for the report."""
    if not TRIANGLES_SEEN:
        pytest.skip("the recipe matrix did not run in this session")
    assert set(TRIANGLES_SEEN) == ARCHETYPES
    assert max(TRIANGLES_SEEN.values()) <= TRIANGLE_BUDGET
    print("\nmax triangles per archetype:", dict(sorted(TRIANGLES_SEEN.items())))


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


EXTREMES = [(1.0, 0.5), (0.4, 3.0), (5000.0, 1500.0), (15.0, 400.0), (800.0, 10.0), (2.0, 2.0)]


@pytest.mark.parametrize("archetype", sorted(ARCHETYPES))
@pytest.mark.parametrize(("height", "radius"), EXTREMES)
def test_extreme_sizes_and_proportions(archetype: str, height: float, radius: float) -> None:
    """Sizes a plan can hold: a seedling of a centimetre, a 50 m tree, a plant
    27 times wider than tall and one 40 times taller than wide."""
    for seed in (11, 12):
        parts = build_silhouette({"a": archetype}, seed, height, radius)
        low, high = bbox(parts)
        assert high[2] == pytest.approx(height, rel=1e-5)
        assert low[2] == pytest.approx(0.0, abs=1e-4 * height)
        assert max(high[0] - low[0], high[1] - low[1]) == pytest.approx(2 * radius, rel=1e-5)
        for item in parts:
            item.validate()
        assert triangles(parts) <= TRIANGLE_BUDGET


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
        no_radius = dataclasses.replace(tomato, params=Params({PARAM_SEED: "x"}))
        assert build_plant(no_radius) == default_builder(no_radius)
        assert plant_archetype(house) is None and plant_sprite(house) is None

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


#: Side-silhouette IoU of two ids of one recipe and size must stay below this.
#: Measured 2026-10-11 over 123 recipes × 10 id pairs (1,230 pairs), the most
#: alike pair per archetype: flower 0.65, feathery 0.74, allium 0.78, palm 0.84,
#: head 0.90, climber 0.92, grass 0.92, rosette 0.93, conifer 0.93, ground 0.94,
#: canopy 0.94, hedge 0.95, mound 0.965 (two petunias — a flat cushion fills its
#: box whatever its lobes do; the mean over 520 mound pairs is 0.92). Before the
#: hedge was given a batter and an uneven top, two hedge drums scored 0.999: the
#: instrument bites.
CLONE_IOU_MAX = 0.97


@pytest.mark.parametrize("sprite", ALL_SPRITES, ids=lambda s: f"{s.group}/{s.name}")
def test_two_ids_are_not_clones(sprite: SpriteRef) -> None:
    """The metric: IoU of the two plants' side silhouettes (x–z projection, a
    32 × 32 occupancy grid over the shared bounding box). A clone — or a copy
    turned about its axis, for a round shape — scores 1.0."""
    height, spread = MATURE[sprite]
    radius = spread / 2.0
    shapes = [side_silhouette(build_plant(record_for(sprite, height, radius, seed)),
                              height, radius) for seed in SEEDS]
    assert silhouette_iou(shapes[0], shapes[0]) == 1.0
    worst = max(silhouette_iou(shapes[i], shapes[j])
                for i in range(len(shapes)) for j in range(i + 1, len(shapes)))
    assert worst <= CLONE_IOU_MAX, f"{sprite.name}: two ids share {worst:.3f} of their silhouette"


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
        were there, buried) and not in the air between two (nothing floats)."""
        dice = plants._Dice(7)
        centers = np.array([[60.0, 0.0, 100.0], [-50.0, 30.0, 110.0], [0.0, -10.0, 150.0]])
        radii = np.array([[70.0, 70.0, 60.0], [65.0, 65.0, 55.0], [60.0, 60.0, 50.0]])
        origin = np.array([0.0, 0.0, 110.0])
        reach = plants._shell_points(dice, 400, -0.5) * np.array([120.0, 120.0, 90.0])
        points = plants._on_lobes(dice, origin, reach, centers, radii)
        # per lobe: 1 on its surface, < 1 inside it, > 1 outside it
        level = np.sqrt((((points[:, None, :] - centers[None]) / radii[None]) ** 2).sum(axis=2))
        outermost = level.min(axis=1)
        assert outermost.min() >= 0.90 and outermost.max() <= 1.12, (
            outermost.min(), outermost.max())
        missed = plants._lobe_surface(origin + [0.0, 0.0, 1000.0], reach, centers, radii)
        assert float(missed.max()) == 0.0      # a ray that meets no lobe says so

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


HUE_SEEN: dict[str, float] = {}


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
            HUE_SEEN[sprite.name] = max(HUE_SEEN.get(sprite.name, 0.0), error)
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
