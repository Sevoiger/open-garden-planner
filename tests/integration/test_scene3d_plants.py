"""Plants in the 3D pipeline, end to end (Phase 17 L1.7, #385 — §8.10).

live plant items → ``snapshot_records`` → ``SceneSync`` (plant builder registered)
→ ``RecordingSink``

A real canvas is planted the way a user plants it — gallery drops (the real
``dropEvent``) for a tree, a shrub and perennials of ten archetypes with bundled
species, category plants with a height the user typed, and one tree drawn with
the TREE tool that the plan knows no height for. Then the plan date is scrubbed.

After every step the sink is asserted: each plant's top is the height the 2D
shadow resolves for that date, its widest span is the canopy the 2D shadow
uses, it stands where the item stands, its archetype is the one of the sprite
the canvas draws for it — and a date change rebuilds exactly the plants that
grew. Nothing is rendered: the sink is the recording double (L1.2 runs the same
flow against Qt Quick 3D).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest
from PyQt6.QtCore import QMimeData, QPointF, Qt
from PyQt6.QtGui import QDropEvent

from open_garden_planner.core import plant_renderer
from open_garden_planner.core.commands import MoveItemsCommand
from open_garden_planner.core.object_height import METADATA_KEY, effective_height_cm
from open_garden_planner.core.plant_art import SPECIES_SPRITES, SpriteRef
from open_garden_planner.core.scene3d import (
    PARAM_SPECIES_NAME,
    PARAM_SPRITE_SPECIES,
    BuilderRegistry,
    Record,
    RecordingSink,
    SceneDiff,
    SceneSync,
)
from open_garden_planner.core.scene3d.builders.plants import (
    PART_FOLIAGE,
    PART_PLACEHOLDER,
    PART_WOOD,
    PLACEHOLDER,
    TRIANGLE_BUDGET,
    plant_archetype,
    plant_sprite,
    register,
)
from open_garden_planner.core.tools import ToolType
from open_garden_planner.services.bundled_species_db import lookup_species, merge_calendar_data
from open_garden_planner.ui.canvas.canvas_view import CanvasView
from open_garden_planner.ui.canvas.footprints import plant_canopy_radius_cm
from open_garden_planner.ui.canvas.items.circle_item import CircleItem
from open_garden_planner.ui.plant_species_assignment import apply_species_to_item
from open_garden_planner.ui.view3d.snapshot import snapshot_records
from open_garden_planner.ui.widgets.gallery_data import (
    GalleryItem,
    all_items,
    build_toolbar_categories,
)

JUNE_2026, JUNE_2027 = date(2026, 6, 21), date(2027, 6, 21)
BEGIN, COMMIT = ("begin", None), ("commit", None)

#: What is planted: label → (gallery MIME tail, archetype the sprite's recipe names).
GALLERY = {
    "apple": ("TREE:species=apple tree", "canopy"),
    "spruce": ("TREE:species=spruce", "conifer"),
    "hydrangea": ("SHRUB:species=hydrangea", "mound"),
    "tomato": ("PERENNIAL:species=tomato", "mound"),
    "lettuce": ("PERENNIAL:species=lettuce", "rosette"),
    "broccoli": ("PERENNIAL:species=broccoli", "head"),
    "leek": ("PERENNIAL:species=leek", "allium"),
    "iris": ("PERENNIAL:species=iris", "grass"),
    "carrot": ("PERENNIAL:species=carrot", "feathery"),
    "sunflower": ("PERENNIAL:species=sunflower", "flower"),
    "cucumber": ("PERENNIAL:species=cucumber", "climber"),
    # no species data, so no height — until the user types one (below)
    "palm": ("TREE:category=PALM", "palm"),
    "hedge": ("SHRUB:category=HEDGE_SECTION", "hedge"),
    "carpet": ("PERENNIAL:category=GROUND_COVER", "ground"),
}
TYPED_HEIGHTS = {"palm": 520.0, "hedge": 140.0, "carpet": 12.0}
WOODY = {"canopy", "conifer", "palm"}
#: measured by the user on the day they were planted: these two grow with the date
MEASURED = {
    "apple": {"planting_date": "2024-04-01", "current_height_cm": 180.0,
              "current_spread_cm": 120.0},
    "tomato": {"planting_date": "2026-05-15", "current_height_cm": 25.0,
               "current_spread_cm": 20.0},
}


def drop(view: CanvasView, mime_tail: str, at: QPointF) -> CircleItem:
    """A gallery drop through the real ``dropEvent``; returns the new plant."""
    before = {id(item) for item in view.scene().items()}
    mime = QMimeData()
    mime.setText(f"gallery:{mime_tail}")
    view.dropEvent(QDropEvent(QPointF(view.mapFromScene(at)), Qt.DropAction.CopyAction, mime,
                              Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    new = [item for item in view.scene().items()
           if isinstance(item, CircleItem) and id(item) not in before]
    assert len(new) == 1
    return new[0]


class Garden:
    """The planted canvas plus the pipeline L1.3 will own, in miniature."""

    def __init__(self, view: CanvasView, mouse_event) -> None:
        self.view = view
        self.scene = view.scene()
        self.plants: dict[str, CircleItem] = {}
        for index, (label, (mime_tail, _archetype)) in enumerate(GALLERY.items()):
            where = QPointF(400.0 + 600.0 * (index % 5), 500.0 + 700.0 * (index // 5))
            self.plants[label] = drop(view, mime_tail, where)
        for label, height in TYPED_HEIGHTS.items():
            self.plants[label].metadata[METADATA_KEY] = height
        for label, instance in MEASURED.items():
            self.plants[label].metadata["plant_instance"] = dict(instance)
        # a tree drawn with the real tool: a click for the centre, one for the rim
        view.set_active_tool(ToolType.TREE)
        tool = view.tool_manager.active_tool
        tool.mouse_press(mouse_event, QPointF(3600.0, 2400.0))
        tool.mouse_press(mouse_event, QPointF(3750.0, 2400.0))
        drawn = [item for item in self.scene.items() if isinstance(item, CircleItem)
                 and item not in self.plants.values()]
        assert len(drawn) == 1
        self.plants["unknown"] = drawn[0]
        registry = BuilderRegistry()
        register(registry)
        self.sink = RecordingSink()
        self.sync = SceneSync(self.sink, registry, validate=True)

    def item_id(self, label: str) -> str:
        return str(self.plants[label].item_id)

    def tick(self, at: date) -> tuple[SceneDiff, list[tuple[str, str | None]], dict[str, Record]]:
        self.sink.clear_calls()
        records = snapshot_records(self.scene, at)
        changes = self.sync.apply(records)
        assert not self.sync.failures, {k: str(v.error) for k, v in self.sync.failures.items()}
        fresh = SceneSync(RecordingSink(), self.sync.registry, validate=True)
        fresh.apply(records)                       # incremental == a build from scratch
        assert isinstance(fresh.sink, RecordingSink)
        assert self.sink.state() == fresh.sink.state()
        return changes, self.sink.ops(), records

    def assert_matches_the_plan(self, records: dict[str, Record], at: date) -> None:
        """Every plant in the sink is the plan's plant: size, place, archetype."""
        for label, item in self.plants.items():
            record = records[self.item_id(label)]
            held = self.sink.item(record.item_id)
            # ── the numbers 2D resolves, asked independently of the record ────────
            height = effective_height_cm(item.object_type, item.metadata, at_date=at)
            radius = plant_canopy_radius_cm(item, at) or item.radius
            assert record.height_cm == height
            assert record.params["radius_cm"] == pytest.approx(radius, abs=1e-6)
            centre = item.mapToScene(item.rect().center())
            assert (held.transform.east_cm, held.transform.north_cm) == pytest.approx(
                (centre.x(), centre.y()), abs=1e-6)
            points = np.concatenate([part.mesh.positions for part in held.parts])
            low, high = points.min(axis=0), points.max(axis=0)
            assert low[2] == pytest.approx(0.0, abs=1e-3)
            assert max(high[0] - low[0], high[1] - low[1]) == pytest.approx(2 * radius, rel=1e-5)
            assert sum(part.mesh.triangle_count for part in held.parts) <= TRIANGLE_BUDGET
            # ── the sprite the canvas draws is the recipe 3D builds ───────────────
            drawn = plant_renderer._resolve_svg_path(
                item.object_type, item.plant_species, item.plant_category)
            sprite = plant_sprite(record)
            assert drawn is not None and sprite is not None
            assert (drawn.parent.name, drawn.stem) == (sprite.group, sprite.name), label
            names = {part.name for part in held.parts}
            if label == "unknown":
                assert height is None and plant_archetype(record) == PLACEHOLDER
                assert names == {PART_PLACEHOLDER}
                assert not held.parts[0].casts_shadow and not record.casts_shadow
                assert high[2] <= 25.0                        # a marker, never a guessed tree
                continue
            archetype = GALLERY[label][1]
            assert plant_archetype(record) == archetype, label
            assert height is not None and high[2] == pytest.approx(height, rel=1e-5), label
            assert names == ({PART_WOOD, PART_FOLIAGE} if archetype in WOODY else {PART_FOLIAGE})
            assert all(part.casts_shadow for part in held.parts)


@pytest.fixture()
def garden(canvas: CanvasView, mouse_event) -> Garden:
    return Garden(canvas, mouse_event)


def test_a_planted_garden_is_built_to_the_plan_and_grows_with_the_date(garden: Garden) -> None:
    # ── first tick: everything is added, every plant built once ───────────────
    changes, calls, records = garden.tick(JUNE_2026)
    ids = [garden.item_id(label) for label in garden.plants]
    assert sorted(changes.added) == sorted(ids) and len(ids) == 15
    assert calls[0] == BEGIN and calls[-1] == COMMIT
    assert sorted(calls[1:-1]) == sorted(("add", item_id) for item_id in ids)
    assert garden.sync.build_count == 15
    garden.assert_matches_the_plan(records, JUNE_2026)
    # the species plants stand at their species' mature size, the measured two part-grown
    assert records[garden.item_id("spruce")].height_cm == 5000.0
    assert records[garden.item_id("lettuce")].height_cm == 30.0
    assert 180.0 < records[garden.item_id("apple")].height_cm < 800.0
    assert 25.0 < records[garden.item_id("tomato")].height_cm < 200.0

    # ── nothing changed: silence ──────────────────────────────────────────────
    assert garden.tick(JUNE_2026)[:2] == (SceneDiff(), [])

    # ── a year later: exactly the two measured, dated plants regrow ───────────
    june = records
    changes, calls, records = garden.tick(JUNE_2027)
    grown = sorted(garden.item_id(label) for label in MEASURED)
    assert sorted(changes.geometry) == grown
    assert changes == SceneDiff(geometry=changes.geometry)
    assert calls == [BEGIN, *(("replace_geometry", item_id) for item_id in changes.geometry),
                     COMMIT]
    assert garden.sync.build_count == 17                       # two builds, no more
    for label in MEASURED:
        item_id = garden.item_id(label)
        assert records[item_id].height_cm > june[item_id].height_cm
        assert records[item_id].params["radius_cm"] > june[item_id].params["radius_cm"]
    assert records[garden.item_id("tomato")].height_cm == 200.0   # an annual: full grown
    garden.assert_matches_the_plan(records, JUNE_2027)

    # ── scrubbing back shrinks them again; the others were never touched ──────
    changes, calls, records = garden.tick(JUNE_2026)
    assert sorted(changes.geometry) == grown and garden.sync.build_count == 19
    assert records == june
    garden.assert_matches_the_plan(records, JUNE_2026)


def test_moving_a_plant_re_poses_it_and_builds_nothing(garden: Garden) -> None:
    garden.tick(JUNE_2026)
    built = garden.sync.build_count
    tree = garden.plants["apple"]
    garden.view.command_manager.execute(MoveItemsCommand([tree], QPointF(250.0, -125.0)))
    changes, calls, records = garden.tick(JUNE_2026)
    assert changes == SceneDiff(transform=(garden.item_id("apple"),))
    assert calls == [BEGIN, ("update_transform", garden.item_id("apple")), COMMIT]
    assert garden.sync.build_count == built
    garden.assert_matches_the_plan(records, JUNE_2026)


def test_a_typed_height_turns_the_placeholder_into_a_tree_and_back(garden: Garden) -> None:
    """The tree drawn with the tool has no species and no measurement: the plan
    knows its canopy, not its height. 3D shows the marker — until a height is
    typed into the Properties panel's field (``object_height_cm``)."""
    garden.tick(JUNE_2026)
    unknown, item_id = garden.plants["unknown"], garden.item_id("unknown")
    assert [part.name for part in garden.sink.item(item_id).parts] == [PART_PLACEHOLDER]

    unknown.metadata[METADATA_KEY] = 650.0
    changes, calls, records = garden.tick(JUNE_2026)
    assert changes == SceneDiff(geometry=(item_id,))
    assert calls == [BEGIN, ("replace_geometry", item_id), COMMIT]
    held = garden.sink.item(item_id)
    assert {part.name for part in held.parts} == {PART_WOOD, PART_FOLIAGE}
    assert plant_archetype(records[item_id]) == "canopy"       # a TREE's default sprite
    assert max(float(part.mesh.positions[:, 2].max()) for part in held.parts) == pytest.approx(650.0)
    assert all(part.casts_shadow for part in held.parts) and records[item_id].casts_shadow

    del unknown.metadata[METADATA_KEY]
    changes, calls, records = garden.tick(JUNE_2026)
    assert calls == [BEGIN, ("replace_geometry", item_id), COMMIT]
    assert [part.name for part in garden.sink.item(item_id).parts] == [PART_PLACEHOLDER]
    assert records[item_id].height_cm is None


def test_a_species_assigned_from_the_database_is_built_as_the_canvas_draws_it(
    garden: Garden,
) -> None:
    """Assigning a species from the plant database (``ApplySpeciesCommand``) writes
    the item's species DATA and leaves its species STRING alone. The canvas
    resolves its sprite from the string, so it keeps drawing the tree's default
    sprite — and 3D builds that same sprite's recipe (ruling R14: the record
    carries the item's own string, and the builder resolves from it). The data
    still decides the size: the oak's 40 m. Whether the 2D sprite should follow
    an assigned species is a question for 2D, not a difference between the views
    any more (it was one: 3D built the oak — pinned as a known gap in the first
    version of this test)."""
    unknown, item_id = garden.plants["unknown"], garden.item_id("unknown")
    oak = lookup_species("oak")
    assert oak is not None
    apply_species_to_item(unknown, merge_calendar_data(dict(oak)))   # the app's own path
    assert unknown.plant_species == "" and unknown.metadata["plant_species"]["common_name"] == "Oak"
    _changes, _calls, records = garden.tick(JUNE_2026)
    record = records[item_id]
    assert record.height_cm == 4000.0                          # the data gives a height now
    assert record.params[PARAM_SPECIES_NAME] == "Oak"          # ... and a name, for labels
    assert PARAM_SPRITE_SPECIES not in record.params           # the item's own string is empty
    drawn = plant_renderer._resolve_svg_path(
        unknown.object_type, unknown.plant_species, unknown.plant_category)
    sprite = plant_sprite(record)
    assert drawn is not None and sprite is not None
    assert (drawn.parent.name, drawn.stem) == ("categories", "round_deciduous")   # 2D
    assert (sprite.group, sprite.name) == ("categories", "round_deciduous")       # 3D: the same


def _drag_data(entry: GalleryItem) -> str:
    """What the gallery puts on the drag (``ui/widgets/category_dropdown.py``)."""
    data = entry.tool_type.name
    if entry.species:
        data += f":species={entry.species}"
    if entry.plant_category:
        data += f":category={entry.plant_category.name}"
    return data


def _drawn_and_built(view: CanvasView, items: list[CircleItem]) -> list[tuple[str, str, str]]:
    """(species string, sprite the canvas draws, sprite 3D builds) of every item where
    the two differ — through the real scene and ``snapshot_records``."""
    records = snapshot_records(view.scene(), JUNE_2026)
    different = []
    for item in items:
        record = records[str(item.item_id)]
        drawn = plant_renderer._resolve_svg_path(
            item.object_type, item.plant_species, item.plant_category)
        built = plant_sprite(record)
        assert drawn is not None and built is not None
        if (drawn.parent.name, drawn.stem) != (built.group, built.name):
            different.append((item.plant_species, f"{drawn.parent.name}/{drawn.stem}",
                              f"{built.group}/{built.name}"))
        assert record.params.get(PARAM_SPRITE_SPECIES, "") == item.plant_species   # raw
    return different


def test_every_gallery_plant_is_built_as_the_sprite_the_canvas_draws(canvas: CanvasView) -> None:
    """ALL plants of the gallery — every species and every category it offers —
    dropped through the real ``dropEvent`` with the drag data the gallery
    composes: the sprite the canvas draws is the recipe 3D builds. The first
    version of this file checked 11 of them and missed the pumpkin: its gallery
    string is "pumpkin", its bundled species data is named "Winter Squash", and
    3D — which resolved from the data's name — built the squash (review round 1,
    P1-2)."""
    plant_tools = (ToolType.TREE, ToolType.SHRUB, ToolType.PERENNIAL)
    entries = [entry for entry in all_items(build_toolbar_categories())
               if entry.tool_type in plant_tools and (entry.species or entry.plant_category)]
    with_species = [entry for entry in entries if entry.species]
    assert len(with_species) >= 85 and len(entries) - len(with_species) >= 12
    items = [drop(canvas, _drag_data(entry),
                  QPointF(80.0 + 110.0 * (n % 40), 200.0 + 400.0 * (n // 40)))
             for n, entry in enumerate(entries)]
    assert _drawn_and_built(canvas, items) == []
    pumpkin = next(item for item in items if item.plant_species == "pumpkin")
    assert pumpkin.metadata["plant_species"]["common_name"] == "Winter Squash"   # the trap
    record = snapshot_records(canvas.scene(), JUNE_2026)[str(pumpkin.item_id)]
    assert record.params[PARAM_SPECIES_NAME] == "Winter Squash"
    assert record.params[PARAM_SPRITE_SPECIES] == "pumpkin"
    assert plant_sprite(record) == SpriteRef("species", "pumpkin")


def test_every_species_string_is_built_as_the_sprite_the_canvas_draws(canvas: CanvasView) -> None:
    """Every key of the species map — 222 strings, on a tree, a shrub and a
    perennial in turn — and the strings the 2D rule is odd about (title case,
    blanks around, blanks ONLY, a name that partially matches another plant's
    key, a name the tables do not know): drawn and built from the same sprite."""
    odd = [" Rose ", "ROSE", "   ", " \t ", "Lemon Balm", "Melon", "Bean (Bush)", "Sweet Cherry",
           "no such plant", "e", "Wild Rose Hip", "sweet potato", "Winter Squash", "Watermelon"]
    strings = [*SPECIES_SPRITES, *(key.title() for key in list(SPECIES_SPRITES)[::9]), *odd]
    assert len(SPECIES_SPRITES) == 222 and not any(":" in text for text in strings)
    tools = ("TREE", "SHRUB", "PERENNIAL")
    items = [drop(canvas, f"{tools[n % 3]}:species={text}",
                  QPointF(80.0 + 110.0 * (n % 40), 200.0 + 300.0 * (n // 40)))
             for n, text in enumerate(strings)]
    assert [item.plant_species for item in items] == strings        # set as dropped, untouched
    assert _drawn_and_built(canvas, items) == []
    # ... and the canvas does draw a string of blanks as a rose (the quirk 3D now shares)
    blanks = items[strings.index("   ")]
    drawn = plant_renderer._resolve_svg_path(blanks.object_type, blanks.plant_species, None)
    assert drawn is not None and (drawn.parent.name, drawn.stem) == ("species", "rose")
