"""``snapshot_records`` on REAL canvas items (Phase 17 L1.1, #385).

The gates this file owns, each on a real ``CanvasScene`` with real items driven
through the app's own commands (``MoveItemsCommand``, ``RotateItemCommand`` +
``apply_rotation``, ``ResizeItemCommand`` + ``apply_rect_like_geometry``,
``build_move_vertex_command`` — the paths the GUI and the Agent API share):

- G1 — the diff matrix over every footprint shape: move → transform only,
  rotate → transform only, recolour → material only, resize / height / vertex
  edit / a plant growing with the date → geometry, select / hover / rename →
  empty, add / remove → added / removed.
- G4 — ``transform ∘ local footprint`` reproduces ``item_footprints`` to
  ≤ 1e-6 cm for every shape x rotation x position (a polyline's stroke polygon
  only to pyclipper's 1/1000 cm grid — see ``TestTransformReproducesFootprint``).
- G7 — every mesh the default builder emits for these shapes validates, and a
  prism's top is exactly the resolved height.
- G8 — an unchanged scene snapshots identically, 100 times, on both bench plans.

Fix round 1 added what the first matrix did not contain and therefore could not
catch: a stroke that closes on itself (outer ring + hole), a polygon that crosses
itself, an item whose metadata is malformed, two items with one id, and a house
with its ridge item — plus G4, G7 and G8 over a third committed plan made of
exactly those cases (``scene3d_edge_cases.ogp``).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PyQt6.QtCore import QPoint, QPointF
from PyQt6.QtGui import QColor, QTransform
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QGraphicsItemGroup

from open_garden_planner.core import ProjectManager
from open_garden_planner.core.commands import (
    ChangePropertyCommand,
    CreateItemCommand,
    DeleteItemsCommand,
    MoveItemsCommand,
    ResizeItemCommand,
    RotateItemCommand,
)
from open_garden_planner.core.fill_patterns import FillPattern, create_pattern_brush
from open_garden_planner.core.object_height import METADATA_KEY, effective_height_cm
from open_garden_planner.core.object_types import ObjectType, PathFenceStyle
from open_garden_planner.core.scene3d import (
    SHAPES,
    Material,
    Params,
    Record,
    RecordingSink,
    SceneDiff,
    SceneSync,
    Transform,
    default_builder,
    diff,
    is_simple_ring,
    point_in_regions,
    pose_ring,
    signed_area,
    verify_builder,
)
from open_garden_planner.core.stacking import ArrangeMode
from open_garden_planner.models.plant_data import species_key
from open_garden_planner.ui.canvas.arrange import build_arrange_command
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.canvas.canvas_view import CanvasView
from open_garden_planner.ui.canvas.footprints import (
    OUTLINE_SHAPES,
    item_footprints,
    item_outline,
)
from open_garden_planner.ui.canvas.geometry_apply import (
    apply_rect_like_geometry,
    apply_rotation,
    build_circle_resize,
    build_ellipse_resize,
    build_move_vertex_command,
    build_rect_resize,
)
from open_garden_planner.ui.canvas.items.circle_item import CircleItem
from open_garden_planner.ui.canvas.items.ellipse_item import EllipseItem
from open_garden_planner.ui.canvas.items.polygon_item import PolygonItem
from open_garden_planner.ui.canvas.items.polyline_item import PolylineItem
from open_garden_planner.ui.canvas.items.rectangle_item import RectangleItem
from open_garden_planner.ui.view3d import snapshot as snapshot_module
from open_garden_planner.ui.view3d.snapshot import (
    SkippedItem,
    Snapshot,
    collect_scene3d_records,
    snapshot_records,
)

PLANS = Path(__file__).resolve().parents[1] / "fixtures" / "plans"
ROTATIONS = [0.0, 17.0, 90.0, 213.5]
POSITIONS = [(0.0, 0.0), (137.25, -42.5)]
JUNE = date(2026, 6, 21)
SPECIES = {
    "common_name": "Apple tree", "scientific_name": "Malus domestica",
    "min_height_cm": 100.0, "max_height_cm": 500.0,
    "min_spread_cm": 50.0, "max_spread_cm": 400.0,
}


# ── the six footprint shapes of the gate ──────────────────────────────────────


def _circle() -> Any:
    return CircleItem(310.5, 220.25, 75.0, object_type=ObjectType.TRAMPOLINE)


def _ellipse() -> Any:
    return EllipseItem(120.5, 80.25, 260.0, 110.5, object_type=ObjectType.POND_POOL)


def _rectangle() -> Any:
    return RectangleItem(100.3, 50.7, 240.0, 130.5, object_type=ObjectType.TOOL_SHED)


def _polygon() -> Any:
    # concave L; vertex 3 is the reflex corner, inside the bounding box
    vertices = [QPointF(200.0, 100.0), QPointF(520.5, 100.0), QPointF(520.5, 260.25),
                QPointF(360.0, 260.25), QPointF(360.0, 410.0), QPointF(200.0, 410.0)]
    return PolygonItem(vertices, object_type=ObjectType.HEDGE_POLYGON)


def _polyline() -> Any:
    # vertex 1 lies inside the bounding box of the other three
    points = [QPointF(100.0, 100.0), QPointF(250.5, 180.25), QPointF(430.0, 380.0),
              QPointF(180.75, 300.0)]
    item = PolylineItem(points, object_type=ObjectType.WALL)
    pen = item.pen()
    pen.setWidthF(24.5)
    item.setPen(pen)
    return item


def _plant() -> Any:
    item = CircleItem(640.0, 480.5, 100.0, object_type=ObjectType.TREE)
    item.metadata["plant_species"] = dict(SPECIES)
    item.metadata["plant_instance"] = {"planting_date": "2026-01-01",
                                       "current_height_cm": 100.0, "current_spread_cm": 50.0}
    return item


FACTORIES: dict[str, Callable[[], Any]] = {
    "circle": _circle, "ellipse": _ellipse, "rectangle": _rectangle,
    "polygon": _polygon, "polyline": _polyline, "plant": _plant,
}
SHAPE_NAMES = sorted(FACTORIES)
ROUND = {"circle", "plant"}
RECT_BACKED = {"ellipse", "rectangle"}
VERTEX_BACKED = {"polygon", "polyline"}


class Stage:
    """A real view + scene with one item of the shape under test and a bystander."""

    def __init__(self, view: CanvasView, shape: str) -> None:
        self.view = view
        self.scene: CanvasScene = view.scene()  # type: ignore[assignment]
        self.commands = view.command_manager
        self.shape = shape
        self.bystander = RectangleItem(2000.0, 1500.0, 100.0, 100.0,
                                       object_type=ObjectType.RAISED_BED)
        self.item = FACTORIES[shape]()
        for item, label in ((self.bystander, "rectangle"), (self.item, shape)):
            item.layer_id = self.scene.active_layer.id  # what every drawing tool does
            self.commands.execute(CreateItemCommand(self.scene, item, label))
        self.id = str(self.item.item_id)
        self.at: date | None = JUNE if shape == "plant" else None
        self.last = self.snapshot()

    def snapshot(self) -> dict[str, Record]:
        return snapshot_records(self.scene, self.at)

    def step(self) -> SceneDiff:
        """Snapshot again and diff against the previous snapshot."""
        new = self.snapshot()
        result = diff(self.last, new)
        self.last = new
        return result

    @property
    def record(self) -> Record:
        return self.last[self.id]


def _stage_fixture(shapes: set[str]) -> Any:
    """A ``Stage`` fixture parametrised over the shapes an edit applies to — the
    matrix names exactly its cells, with no skipped ones."""
    @pytest.fixture(params=sorted(shapes))
    def fixture(request: pytest.FixtureRequest, canvas: CanvasView) -> Stage:
        return Stage(canvas, request.param)

    return fixture


stage = _stage_fixture(set(FACTORIES))  # every footprint shape
resizable_stage = _stage_fixture(ROUND | RECT_BACKED)  # resized through a rect
rect_stage = _stage_fixture(RECT_BACKED)  # can keep a corner fixed
vertex_stage = _stage_fixture(VERTEX_BACKED)  # edited vertex by vertex
undated_stage = _stage_fixture(set(FACTORIES) - {"plant"})  # the date does not reach them


def _recolour(item: Any, color: QColor) -> ChangePropertyCommand:
    """The properties panel's fill-colour edit (``_on_color_changed``)."""
    def apply(target: Any, value: QColor) -> None:
        target.fill_color = value
        target.setBrush(create_pattern_brush(target.fill_pattern, value))

    return ChangePropertyCommand(item, "fill color", QColor(item.fill_color), color, apply)


def _restroke(item: Any, color: QColor) -> ChangePropertyCommand:
    """The properties panel's stroke-colour edit."""
    def apply(target: Any, value: QColor) -> None:
        target.stroke_color = value
        pen = target.pen()
        pen.setColor(value)
        target.setPen(pen)

    return ChangePropertyCommand(item, "stroke color", QColor(item.pen().color()), color, apply)


def _resize_command(item: Any, shape: str, *, keep_center: bool, factor: float = 1.5) -> ResizeItemCommand:
    rect = item.rect()
    if shape in ROUND:
        old, new = build_circle_resize(item, rect.width() * factor, keep_center=keep_center)
    elif shape == "ellipse":
        old, new = build_ellipse_resize(item, rect.width() * factor, rect.height() * 0.8,
                                        keep_center=keep_center)
    else:
        old, new = build_rect_resize(item, rect.width() * factor, rect.height() * 0.8,
                                     keep_center=keep_center)
    return ResizeItemCommand(item, old, new, apply_rect_like_geometry)


# ── G1: the diff matrix ───────────────────────────────────────────────────────


class TestDiffMatrix:
    def test_move_is_transform_only(self, stage: Stage) -> None:
        before = stage.record
        stage.commands.execute(MoveItemsCommand([stage.item], QPointF(137.25, -42.5)))
        assert stage.step() == SceneDiff(transform=(stage.id,))
        after = stage.record
        assert after.transform.east_cm == pytest.approx(before.transform.east_cm + 137.25, abs=1e-6)
        assert after.transform.north_cm == pytest.approx(before.transform.north_cm - 42.5, abs=1e-6)
        assert after.transform.rotation_deg == before.transform.rotation_deg
        assert after.footprints == before.footprints and after.path == before.path

    def test_undo_of_a_move_restores_the_very_same_record(self, stage: Stage) -> None:
        before = stage.record
        stage.commands.execute(MoveItemsCommand([stage.item], QPointF(137.25, -42.5)))
        stage.step()
        stage.commands.undo()
        assert stage.step() == SceneDiff(transform=(stage.id,))
        assert stage.record == before

    def test_undo_dust_near_the_origin_is_absorbed_by_the_record_grid(self, canvas: CanvasView) -> None:
        """Undo is ``moveBy(-delta)``, and ``(p + d) - d`` is not always ``p``:
        ``(0.1 + 0.7) - 0.7 == 0.09999999999999998``. Far from the origin that dust
        is below one ulp of the scene position and vanishes by itself; next to the
        origin it survives into ``sceneTransform()``. On the record grid both paths
        give ONE position, so undo returns the very record it left."""
        item = RectangleItem(-0.7, -0.8, 2.0, 2.0, object_type=ObjectType.TOOL_SHED)  # centre (0.3, 0.2)
        commands = canvas.command_manager
        commands.execute(CreateItemCommand(canvas.scene(), item, "rectangle"))
        commands.execute(MoveItemsCommand([item], QPointF(0.1, 0.3)))
        before = snapshot_records(canvas.scene())
        raw_before = item.mapToScene(item.rect().center())
        commands.execute(MoveItemsCommand([item], QPointF(0.7, 0.9)))
        commands.undo()
        raw_after = item.mapToScene(item.rect().center())
        assert (raw_after.x(), raw_after.y()) != (raw_before.x(), raw_before.y())  # the dust is real
        after = snapshot_records(canvas.scene())
        assert after == before and diff(before, after).is_empty

    @pytest.mark.parametrize("angle", [17.0, 90.0, 213.5])
    def test_rotate_is_transform_only(self, stage: Stage, angle: float) -> None:
        before = stage.record
        stage.commands.execute(RotateItemCommand(stage.item, 0.0, angle, apply_rotation))
        result = stage.step()
        assert result.geometry == () and result.material == ()
        assert result.added == () and result.removed == () and not result.reordered
        after = stage.record
        assert after.footprints == before.footprints and after.path == before.path
        if stage.shape in ROUND:
            # A circle's footprint does not turn with its item (`item_footprints`
            # polygonises it around the mapped centre), so its record keeps rotation
            # 0 — and a turn about its own centre, which is the app's rotation
            # gesture, changes nothing at all: "transform only" with nothing in it.
            assert result == SceneDiff()
            assert after == before
        else:
            assert result == SceneDiff(transform=(stage.id,))
            assert after.transform.rotation_deg == angle

    def test_a_rotated_item_still_moves_as_transform_only(self, stage: Stage) -> None:
        stage.commands.execute(RotateItemCommand(stage.item, 0.0, 213.5, apply_rotation))
        stage.step()
        stage.commands.execute(MoveItemsCommand([stage.item], QPointF(-300.5, 80.0)))
        assert stage.step() == SceneDiff(transform=(stage.id,))

    def test_recolour_is_material_only(self, stage: Stage) -> None:
        before = stage.record
        if stage.shape == "polyline":  # a line has no fill: its colour is the stroke
            stage.commands.execute(_restroke(stage.item, QColor(10, 200, 30)))
        else:
            stage.commands.execute(_recolour(stage.item, QColor(10, 200, 30)))
        assert stage.step() == SceneDiff(material=(stage.id,))
        assert stage.record.material.tint_rgba == (10, 200, 30, 255)
        assert stage.record.geometry_sig == before.geometry_sig
        stage.commands.undo()
        assert stage.step() == SceneDiff(material=(stage.id,))
        assert stage.record == before

    def test_height_change_is_geometry_only(self, stage: Stage) -> None:
        stage.item.metadata[METADATA_KEY] = 321.5
        assert stage.step() == SceneDiff(geometry=(stage.id,))
        assert stage.record.height_cm == 321.5
        del stage.item.metadata[METADATA_KEY]
        assert stage.step() == SceneDiff(geometry=(stage.id,))

    def test_centred_resize_is_geometry_only(self, resizable_stage: Stage) -> None:
        stage = resizable_stage
        before = stage.record
        stage.commands.execute(_resize_command(stage.item, stage.shape, keep_center=True))
        if stage.shape == "plant":
            # A MEASURED plant's footprint is its canopy (`plant_canopy_radius_cm`),
            # not the drawn circle — as in the 2D shadow. Resizing the circle the
            # user drew changes neither, so nothing reaches the engine.
            assert stage.step().is_empty
            assert stage.record == before
            return
        assert stage.step() == SceneDiff(geometry=(stage.id,))
        assert stage.record.transform == before.transform
        assert stage.record.footprints != before.footprints
        stage.commands.undo()
        assert stage.step() == SceneDiff(geometry=(stage.id,))
        assert stage.record == before

    @pytest.mark.parametrize("angle", [0.0, 213.5])
    def test_one_sided_resize_also_moves_the_centre(self, rect_stage: Stage, angle: float) -> None:
        """The record frame hangs on the item's centre. A resize that keeps a corner
        fixed moves that centre, so it is geometry AND transform — never material,
        and both are exactly what happened."""
        stage = rect_stage
        stage.commands.execute(RotateItemCommand(stage.item, 0.0, angle, apply_rotation))
        stage.step()
        stage.commands.execute(_resize_command(stage.item, stage.shape, keep_center=False))
        assert stage.step() == SceneDiff(geometry=(stage.id,), transform=(stage.id,))

    def test_stroke_width_changes_a_polyline_s_geometry(self, canvas: CanvasView) -> None:
        stage = Stage(canvas, "polyline")  # only a polyline's footprint depends on its stroke
        pen = stage.item.pen()
        pen.setWidthF(40.0)
        stage.item.setPen(pen)
        assert stage.step() == SceneDiff(geometry=(stage.id,))
        assert stage.record.path_width_cm == 40.0

    def test_interior_vertex_edit_is_geometry_only(self, vertex_stage: Stage) -> None:
        stage = vertex_stage
        index = 3 if stage.shape == "polygon" else 1
        old = QPointF(stage.item._get_vertex_position(index))
        new = QPointF(old.x() + 12.5, old.y() - 7.25)  # stays inside the bounding box
        before = stage.record
        stage.commands.execute(build_move_vertex_command(stage.item, index, old, new))
        assert stage.step() == SceneDiff(geometry=(stage.id,))
        stage.commands.undo()
        assert stage.step() == SceneDiff(geometry=(stage.id,))
        assert stage.record == before

    def test_extreme_vertex_edit_also_moves_the_centre(self, vertex_stage: Stage) -> None:
        stage = vertex_stage
        old = QPointF(stage.item._get_vertex_position(0))
        new = QPointF(old.x() - 60.0, old.y() - 30.0)  # grows the bounding box
        stage.commands.execute(build_move_vertex_command(stage.item, 0, old, new))
        assert stage.step() == SceneDiff(geometry=(stage.id,), transform=(stage.id,))

    def test_a_plant_growing_with_the_date_is_geometry_only(self, canvas: CanvasView) -> None:
        stage = Stage(canvas, "plant")
        young = stage.record
        stage.at = date(2040, 1, 1)
        assert stage.step() == SceneDiff(geometry=(stage.id,))
        grown = stage.record
        assert grown.height_cm == 500.0 and young.height_cm < 500.0  # type: ignore[operator]
        assert grown.params["radius_cm"] == 200.0 > young.params["radius_cm"]  # type: ignore[operator]
        assert grown.transform == young.transform

    def test_the_date_changes_nothing_but_dated_plants(self, undated_stage: Stage) -> None:
        undated_stage.at = date(2040, 1, 1)
        assert undated_stage.step().is_empty

    def test_select_deselect_hover_and_rename_give_an_empty_diff(self, stage: Stage) -> None:
        stage.item.setSelected(True)  # handles, annotations and labels appear as child items
        assert stage.step().is_empty
        # A REAL hover, through the view's event pipeline: over the item itself and
        # over one of its handles (which highlights — proof the hover arrived).
        view = stage.view
        view.resize(900, 600)
        view.show()
        QTest.qWaitForWindowExposed(view)
        stage.item.setAcceptHoverEvents(True)
        handle = next(c for c in stage.item.childItems() if c.acceptHoverEvents())
        idle = QColor(handle.brush().color())
        centre = stage.item.sceneBoundingRect().center()
        view.centerOn(centre)
        for target in (centre, handle.scenePos()):
            spot = view.mapFromScene(target)
            QTest.mouseMove(view.viewport(), spot + QPoint(30, 30))  # approach, then enter
            QTest.mouseMove(view.viewport(), spot)
            QApplication.processEvents()
            assert stage.step().is_empty
        assert handle.brush().color() != idle
        stage.item.setSelected(False)
        assert stage.step().is_empty
        named = stage.snapshot()
        stage.item.name = "Renamed"
        assert stage.step().is_empty
        assert stage.record.name == "Renamed" and stage.record != named[stage.id]

    def test_add_and_remove(self, stage: Stage) -> None:
        stage.commands.execute(DeleteItemsCommand(stage.scene, [stage.item]))
        assert stage.step() == SceneDiff(removed=(stage.id,))
        stage.commands.undo()
        assert stage.step() == SceneDiff(added=(stage.id,))
        stage.commands.redo()
        assert stage.step() == SceneDiff(removed=(stage.id,))

    def test_hiding_removes_and_showing_adds(self, stage: Stage) -> None:
        stage.item.setVisible(False)
        assert stage.step() == SceneDiff(removed=(stage.id,))
        stage.item.setVisible(True)
        assert stage.step() == SceneDiff(added=(stage.id,))

    def test_arrange_is_a_reorder_never_a_geometry_change(self, stage: Stage) -> None:
        assert list(stage.last) == [str(stage.bystander.item_id), stage.id]  # bottom to top
        command, _outcome = build_arrange_command(stage.scene, [stage.item], ArrangeMode.SEND_TO_BACK)
        assert command is not None
        stage.commands.execute(command)
        result = stage.step()
        assert result == SceneDiff(reordered=True) and not result.touches_sink
        assert list(stage.last) == [stage.id, str(stage.bystander.item_id)]

    def test_the_bystander_never_changes(self, stage: Stage) -> None:
        bystander = str(stage.bystander.item_id)
        stage.commands.execute(MoveItemsCommand([stage.item], QPointF(5.0, 5.0)))
        stage.commands.execute(RotateItemCommand(stage.item, 0.0, 33.0, apply_rotation))
        stage.item.metadata[METADATA_KEY] = 77.0
        assert bystander not in stage.step().changed


# ── G4: transform ∘ local footprint == the 2D footprint ───────────────────────


def _boundary_distance(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> float:
    """Largest distance from a vertex of ``a`` to the boundary of polygon ``b``."""
    def to_segment(p: tuple[float, float], s: tuple[float, float], e: tuple[float, float]) -> float:
        dx, dy = e[0] - s[0], e[1] - s[1]
        length2 = dx * dx + dy * dy
        t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((p[0] - s[0]) * dx + (p[1] - s[1]) * dy) / length2))
        return math.hypot(p[0] - (s[0] + t * dx), p[1] - (s[1] + t * dy))

    return max(min(to_segment(p, b[i], b[(i + 1) % len(b)]) for i in range(len(b))) for p in a)


def _rings_of(record: Record) -> list[tuple[tuple[float, float], ...]]:
    """Every ring of a record — outers, then the holes of each — in the scene."""
    rings = list(record.footprints) + [hole for group in record.holes for hole in group]
    return [pose_ring(record.transform, ring) for ring in rings]


def _ring_deviation(posed: Any, expected: Any) -> float:
    """Largest vertex-to-vertex distance between two rings with the same vertices.
    A record's outer ring is counter-clockwise; ``item_footprints`` hands a ring
    out as Qt mapped it, so a mirrored item's comes the other way round — the
    same ring, read backwards."""
    assert len(posed) == len(expected)

    def worst(other: Any) -> float:
        return max(math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(posed, other, strict=True))

    return min(worst(list(expected)), worst(list(expected)[::-1]))


def _strip_deviation(posed: list[Any], expected: list[Any]) -> float:
    """Largest boundary-to-boundary distance between two sets of stroke rings,
    each ring of one matched with the nearest ring of the other."""
    assert len(posed) == len(expected) >= 1
    return max(
        min(max(_boundary_distance(list(p), list(e)), _boundary_distance(list(e), list(p)))
            for p in posed)
        for e in expected
    )


def _signed_area_sum(rings: Any) -> float:
    """The area a set of pyclipper rings encloses: outers count positive, holes
    negative — the even-odd reading of ``item_footprints`` for a stroke."""
    return sum(signed_area(tuple(ring)) for ring in rings)


def _cap_area(mesh: Any) -> float:
    """Signed plan area of a prism's cap (its horizontal triangles)."""
    p = mesh.positions.astype(np.float64)
    a, b, c = (p[mesh.indices[:, k]] for k in range(3))
    flat = (a[:, 2] == b[:, 2]) & (a[:, 2] == c[:, 2])
    twice = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    return float(twice[flat].sum() / 2.0)


def _assert_walls_face_away_from_the_solid(mesh: Any, record: Record) -> None:
    """Step 0.1 mm along each wall triangle's normal from its centroid: that point
    is OUTSIDE the footprint (in the open, or in a hole), and the same step back
    is inside it. The inside-out wall of a hole extruded as a solid fails here.
    (0.1 mm: far above float32 at these coordinates, far below any slit a stroke
    leaves — a 1 cm step jumps the 5 mm gap of a nearly closed 3 cm fence.)"""
    p = mesh.positions.astype(np.float64)
    walls = 0
    for i0, i1, i2 in mesh.indices:
        normal = mesh.normals[i0].astype(np.float64) * 0.01
        if abs(normal[2]) > 0.005:
            continue
        centre = (p[i0] + p[i1] + p[i2]) / 3.0
        assert not point_in_regions(centre[0] + normal[0], centre[1] + normal[1], record.regions)
        assert point_in_regions(centre[0] - normal[0], centre[1] - normal[1], record.regions)
        walls += 1
    assert walls > 0


def _place(item: Any, shape: str, rotation: float, position: tuple[float, float], how: str) -> None:
    if how == "app":  # the rotation gesture's own pivot rule
        apply_rotation(item, rotation)
    else:  # a pivot that is NOT the item's centre: the transform must still be Qt's answer
        item.setTransformOriginPoint(QPointF(33.0, -71.5))
        item.setRotation(rotation)
    item.setPos(position[0], position[1])


class TestTransformReproducesFootprint:
    """Decision 2 of the story, proven against Qt instead of assumed:
    ``scene = T + R(θ)·local`` with θ counter-clockwise seen from above.

    The measured worst deviation over the whole matrix is ~1.5e-7 cm (the record
    grid). ONE thing cannot meet 1e-6: a polyline's stroke POLYGON. The 2D code
    inflates the centre line in scene space on pyclipper's 1/1000 cm integer
    grid; the record inflates it in the item-local frame (or a move would change
    the polygon, and "move → transform only" would be false). The two differ by
    that grid — under 0.003 cm, 30 µm — while the polyline's centre line, which
    is what fences and walls are built from, agrees to 1e-6 like everything else.
    """

    @staticmethod
    def _measure(canvas: CanvasView, shape: str, rotation: float,
                 position: tuple[float, float], how: str) -> tuple[Record, float, float]:
        """(record, exact deviation in cm, polyline strip deviation in cm or 0.0).

        The exact deviation compares vertex by vertex: every ring of a
        non-polyline, the centre line of a polyline. The strip deviation is the
        largest boundary-to-boundary distance between the two stroke polygons —
        over every ring, holes included.
        """
        item = FACTORIES[shape]()
        canvas.scene().addItem(item)
        _place(item, shape, rotation, position, how)
        at = JUNE if shape == "plant" else None
        record = snapshot_records(canvas.scene(), at)[str(item.item_id)]
        expected = item_footprints(item, at)
        posed = _rings_of(record)
        assert len(posed) == len(expected) >= 1
        strip = 0.0
        if shape == "polyline":
            centre_line = [(p.x(), p.y()) for p in (item.mapToScene(q) for q in item.points)]
            exact = max(math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in
                        zip(pose_ring(record.transform, record.path), centre_line, strict=True))
            strip = _strip_deviation(posed, expected)
        else:
            exact = max(_ring_deviation(ring, scene_ring)
                        for ring, scene_ring in zip(posed, expected, strict=True))
        canvas.scene().removeItem(item)
        return record, exact, strip

    @pytest.mark.parametrize("how", ["app", "off-centre pivot"])
    @pytest.mark.parametrize("position", POSITIONS)
    @pytest.mark.parametrize("rotation", ROTATIONS)
    @pytest.mark.parametrize("shape", SHAPE_NAMES)
    def test_matrix(self, canvas: CanvasView, shape: str, rotation: float,
                    position: tuple[float, float], how: str) -> None:
        record, exact, strip = self._measure(canvas, shape, rotation, position, how)
        assert exact <= 1e-6, exact
        assert strip <= 3e-3, strip
        if shape in ROUND:
            assert record.transform.rotation_deg == 0.0
        elif how == "app":
            assert record.transform.rotation_deg == rotation

    def test_measured_worst_case_is_the_record_grid(self, canvas: CanvasView) -> None:
        """The numbers the report quotes, over the whole matrix: everything compared
        vertex by vertex sits at the 1e-7 cm record grid — a fifth of the 1e-6 gate;
        only the polyline's stroke polygon is at pyclipper's grid. Upper bounds
        only: an error that turns out smaller is not a failure."""
        worst_exact, worst_strip = 0.0, 0.0
        for shape in SHAPE_NAMES:
            for rotation in ROTATIONS:
                for position in POSITIONS:
                    for how in ("app", "off-centre pivot"):
                        _record, exact, strip = self._measure(canvas, shape, rotation, position, how)
                        worst_exact, worst_strip = max(worst_exact, exact), max(worst_strip, strip)
        assert worst_exact <= 2.5e-7, worst_exact
        assert worst_strip <= 3e-3, worst_strip

    def test_rotation_sense_is_counter_clockwise_seen_from_above(self, canvas: CanvasView) -> None:
        """A rectangle turned by +90° (Qt, on the Y-up canvas): its local +x edge
        midpoint — east of the centre — ends up NORTH of the centre in the scene."""
        item = RectangleItem(0.0, 0.0, 200.0, 100.0, object_type=ObjectType.TOOL_SHED)
        canvas.scene().addItem(item)
        apply_rotation(item, 90.0)
        record = snapshot_records(canvas.scene())[str(item.item_id)]
        assert record.transform == Transform(100.0, 50.0, 90.0)
        east_edge = item.mapToScene(QPointF(200.0, 50.0))
        assert (east_edge.x(), east_edge.y()) == pytest.approx((100.0, 150.0), abs=1e-9)
        assert pose_ring(record.transform, [(100.0, 0.0)])[0] == pytest.approx((100.0, 150.0), abs=1e-9)


# ── the non-rigid rule ────────────────────────────────────────────────────────


class TestNonRigidTransform:
    @pytest.mark.parametrize(
        ("label", "matrix"),
        [("scale", QTransform().scale(2.0, 0.5)),
         ("mirror", QTransform().scale(-1.0, 1.0)),
         ("turn + stretch", QTransform().rotate(30.0).scale(1.5, 1.0)),
         ("shear", QTransform().shear(0.4, 0.0))],
    )
    @pytest.mark.parametrize("shape", ["ellipse", "rectangle", "polygon", "polyline"])
    def test_scale_mirror_and_shear_are_baked_into_the_local_footprint(
        self, canvas: CanvasView, shape: str, label: str, matrix: QTransform
    ) -> None:
        item = FACTORIES[shape]()
        canvas.scene().addItem(item)
        item.setTransform(matrix)
        item.setPos(40.0, 60.0)
        record = snapshot_records(canvas.scene())[str(item.item_id)]
        assert record.transform.rotation_deg == 0.0, label  # the transform stays rigid
        expected = item_footprints(item)
        posed = _rings_of(record)
        assert all(signed_area(ring) > 0.0 for ring in record.footprints)  # outer rings, always
        if shape == "polyline":
            centre_line = [(p.x(), p.y()) for p in (item.mapToScene(q) for q in item.points)]
            for a, b in zip(pose_ring(record.transform, record.path), centre_line, strict=True):
                assert a == pytest.approx(b, abs=1e-6)
            assert _strip_deviation(posed, expected) <= 3e-3
        else:
            assert _ring_deviation(posed[0], expected[0]) <= 1e-6
            mirrored = matrix.determinant() < 0.0
            # a mirror hands the 2D ring out clockwise; the record turns it round
            assert (list(posed[0]) != [pytest.approx(q, abs=1e-6) for q in expected[0]]) is mirrored

    def test_an_item_in_a_scaled_group_moves_as_transform_only(self, canvas: CanvasView) -> None:
        item = _rectangle()
        group = QGraphicsItemGroup()
        canvas.scene().addItem(group)
        group.addToGroup(item)
        group.setScale(2.0)
        before = snapshot_records(canvas.scene())
        group.moveBy(50.0, -20.0)
        after = snapshot_records(canvas.scene())
        assert diff(before, after) == SceneDiff(transform=(str(item.item_id),))

    def test_a_scaled_circle_keeps_the_2d_footprint(self, canvas: CanvasView) -> None:
        """``item_footprints`` polygonises a circle around its mapped centre with its
        OWN radius — a scale does not reach it. The record says the same."""
        item = _circle()
        canvas.scene().addItem(item)
        item.setTransform(QTransform().scale(2.0, 0.5))
        record = snapshot_records(canvas.scene())[str(item.item_id)]
        for a, b in zip(pose_ring(record.transform, record.footprints[0]), item_footprints(item)[0],
                        strict=True):
            assert a == pytest.approx(b, abs=1e-6)


# ── what a record carries ─────────────────────────────────────────────────────


def _plain(value: Any) -> bool:
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, tuple):
        return all(_plain(v) for v in value)
    if isinstance(value, Params):
        return all(_plain(v) for v in value.values())
    if isinstance(value, Transform | Material):
        return all(_plain(getattr(value, f)) for f in value.__dataclass_fields__)
    return False


class TestRecordContent:
    def test_outline_shapes_are_the_record_shapes(self) -> None:
        assert OUTLINE_SHAPES == SHAPES

    def test_records_are_plain_data(self, stage: Stage) -> None:
        """GUI thread in, plain data out: no Qt object survives into a record."""
        record = stage.record
        for field in Record.__dataclass_fields__:
            assert _plain(getattr(record, field)), field
        assert record.item_id == stage.id and record.kind == stage.item.object_type.name
        assert record.params["seed"] == stage.id

    def test_height_is_the_2d_resolver_s(self, stage: Stage) -> None:
        expected = effective_height_cm(stage.item.object_type, stage.item.metadata, at_date=stage.at)
        assert stage.record.height_cm == expected
        assert stage.record.casts_shadow is (expected is not None)
        assert stage.record.base_cm == 0.0  # L1.6 fills it from the parent bed

    def test_shape_specific_fields(self, stage: Stage) -> None:
        record = stage.record
        if stage.shape in ROUND:
            assert record.shape == "circle" and len(record.footprints[0]) == 24
            radius = record.params["radius_cm"]
            assert record.footprints[0][0] == (radius, 0.0) and record.path == ()
        elif stage.shape == "polyline":
            assert record.shape == "polyline" and len(record.path) == 4
            assert record.path_width_cm == 24.5 and len(record.footprints) == 1
        else:
            assert record.shape == stage.shape and record.path == () and record.path_width_cm is None
            xs = [x for x, _y in record.footprints[0]]
            ys = [y for _x, y in record.footprints[0]]
            assert min(xs) == -max(xs) and min(ys) == -max(ys)  # centred on its anchor

    def test_material(self, canvas: CanvasView) -> None:
        shed, wall = _rectangle(), _polyline()
        for item in (shed, wall):
            canvas.scene().addItem(item)
        records = snapshot_records(canvas.scene())
        shed_material = records[str(shed.item_id)].material
        fill = shed.fill_color
        assert shed_material.key == "TOOL_SHED"
        assert shed_material.fill_rgba == (fill.red(), fill.green(), fill.blue(), fill.alpha())
        assert shed_material.pattern == shed.fill_pattern.name
        wall_material = records[str(wall.item_id)].material
        stroke = wall.pen().color()
        assert wall_material.fill_rgba is None and wall_material.pattern is None
        assert wall_material.tint_rgba == (stroke.red(), stroke.green(), stroke.blue(), 255)

    def test_a_colour_stored_as_a_string_is_read_too(self, canvas: CanvasView) -> None:
        """``fill_color`` is typed loosely on the mixin; the legacy collector accepts
        a colour name, so contract v2 does as well — and an invalid one is no colour."""
        item = _rectangle()
        canvas.scene().addItem(item)
        item.fill_color = "#336699"
        assert snapshot_records(canvas.scene())[str(item.item_id)].material.fill_rgba == (51, 102, 153, 255)
        item.fill_color = "not a colour"
        material = snapshot_records(canvas.scene())[str(item.item_id)].material
        assert material.fill_rgba is None and material.tint_rgba[3] == 255

    def test_plant_params(self, canvas: CanvasView) -> None:
        tree = _plant()
        bare = CircleItem(100.0, 100.0, 30.0, object_type=ObjectType.SHRUB)
        bare.plant_species = "  Mystery bush "
        for item in (tree, bare):
            canvas.scene().addItem(item)
        records = snapshot_records(canvas.scene(), JUNE)
        params = records[str(tree.item_id)].params
        assert params["species_key"] == species_key(SPECIES) == "malus domestica"
        assert params["species_name"] == "Apple tree"
        bare_params = records[str(bare.item_id)].params
        assert bare_params["species_name"] == "Mystery bush" and "species_key" not in bare_params

    def test_parent_bed_is_the_property_not_metadata(self, canvas: CanvasView) -> None:
        bed = RectangleItem(0.0, 0.0, 300.0, 200.0, object_type=ObjectType.RAISED_BED)
        plant = CircleItem(100.0, 100.0, 20.0, object_type=ObjectType.PERENNIAL)
        for item in (bed, plant):
            canvas.scene().addItem(item)
        before = snapshot_records(canvas.scene())
        assert before[str(plant.item_id)].parent_id is None
        plant.parent_bed_id = bed.item_id
        after = snapshot_records(canvas.scene())
        assert after[str(plant.item_id)].parent_id == str(bed.item_id)
        assert diff(before, after).is_empty  # a link, not geometry: L1.6 turns it into base_cm

    def test_path_style_and_container_material(self, canvas: CanvasView) -> None:
        fence = PolylineItem([QPointF(0.0, 0.0), QPointF(500.0, 0.0)], object_type=ObjectType.FENCE,
                             path_fence_style=PathFenceStyle.WOODEN_FENCE)
        plain = PolylineItem([QPointF(0.0, 50.0), QPointF(500.0, 50.0)], object_type=ObjectType.FENCE)
        pot = CircleItem(700.0, 100.0, 25.0, object_type=ObjectType.CONTAINER_ROUND)
        pot.metadata["container_material"] = "terracotta"
        for item in (fence, plain, pot):
            canvas.scene().addItem(item)
        records = snapshot_records(canvas.scene())
        assert records[str(fence.item_id)].params["path_style"] == "WOODEN_FENCE"
        assert "path_style" not in records[str(plain.item_id)].params
        assert records[str(pot.item_id)].params["container_material"] == "terracotta"
        before = records
        fence.path_fence_style = PathFenceStyle.STONE_WALL
        assert str(fence.item_id) in diff(before, snapshot_records(canvas.scene())).geometry

    def test_order_is_bottom_to_top_like_the_legacy_collector(self, qtbot) -> None:
        scene = CanvasScene()
        ProjectManager().load(scene, PLANS / "bench_small.ogp")
        records = snapshot_records(scene, JUNE)
        items = {str(i.item_id): i for i in scene.items() if hasattr(i, "item_id")}
        zs = [items[item_id].zValue() for item_id in records]
        assert zs == sorted(zs) and len(set(zs)) > 5
        legacy = collect_scene3d_records(scene, JUNE)
        ridges = [i for i in items.values() if i.object_type is ObjectType.ROOF_RIDGE]
        # the legacy collector draws the ridge LINE as a decal; contract v2 hands the
        # ridge to its house as a param and gives the ridge item no record
        assert len(ridges) == 1 and str(ridges[0].item_id) not in records
        assert len(legacy) == 99 and len(records) == 98

    def test_skipped_items(self, canvas: CanvasView) -> None:
        from open_garden_planner.ui.canvas.items.text_item import TextItem

        scene = canvas.scene()
        hidden = _rectangle()
        scene.addItem(hidden)
        hidden.setVisible(False)
        scene.addItem(TextItem(10.0, 10.0, "a note"))
        degenerate = EllipseItem(0.0, 0.0, 0.0, 50.0, object_type=ObjectType.POND_POOL)
        scene.addItem(degenerate)
        stub = PolylineItem([QPointF(5.0, 5.0)], object_type=ObjectType.FENCE)
        scene.addItem(stub)
        dot = CircleItem(50.0, 50.0, 0.0, object_type=ObjectType.TRAMPOLINE)
        scene.addItem(dot)
        assert item_outline(degenerate) is None
        assert item_footprints(dot) == [[]] and item_footprints(stub) == []  # nothing in 2D either
        snapshot = snapshot_records(scene)
        assert snapshot == {} and isinstance(snapshot, Snapshot)
        assert snapshot.skipped == ()  # left out by design — nothing is wrong with them

    def test_a_circle_turned_about_another_pivot_moves_and_still_does_not_rotate(
        self, canvas: CanvasView
    ) -> None:
        """Only a pivot away from the centre makes a circle's rotation visible — as a
        new position. Its record still carries rotation 0."""
        item = _circle()
        canvas.scene().addItem(item)
        before = snapshot_records(canvas.scene())
        item.setTransformOriginPoint(QPointF(0.0, 0.0))
        item.setRotation(90.0)
        after = snapshot_records(canvas.scene())
        item_id = str(item.item_id)
        assert diff(before, after) == SceneDiff(transform=(item_id,))
        assert after[item_id].transform == Transform(-220.25, 310.5, 0.0)  # (x, y) → (−y, x)

    def test_a_nonsense_height_is_no_height(self, canvas: CanvasView) -> None:
        """``effective_height_cm`` lets an explicit ``Infinity`` through (a hand-edited
        plan can hold one). It is not a height: the record says decoration, and the
        engine never receives a non-finite mesh."""
        item = _rectangle()
        canvas.scene().addItem(item)
        item.metadata[METADATA_KEY] = float("inf")
        assert effective_height_cm(item.object_type, item.metadata) == float("inf")
        record = snapshot_records(canvas.scene())[str(item.item_id)]
        assert record.height_cm is None and default_builder(record) == ()

    def test_non_finite_geometry_is_skipped_not_propagated(self, canvas: CanvasView) -> None:
        """NaN != NaN: one such record would report a change on every diff, forever.
        It is left out — and said so (see ``TestContainment``)."""
        item = _rectangle()
        canvas.scene().addItem(item)
        item.setPos(float("nan"), 0.0)
        snapshot = snapshot_records(canvas.scene())
        assert snapshot == {}
        assert [(s.item_id, s.kind) for s in snapshot.skipped] == [(str(item.item_id), "TOOL_SHED")]
        assert isinstance(snapshot.skipped[0].error, ValueError)


# ── P1-1: a stroke that closes on itself ──────────────────────────────────────


def _fence(points: list[tuple[float, float]], width_cm: float = 10.0) -> Any:
    item = PolylineItem([QPointF(x, y) for x, y in points], object_type=ObjectType.FENCE)
    pen = item.pen()
    pen.setWidthF(width_cm)
    item.setPen(pen)
    return item


#: label → (centre line, outer rings, holes) — a 10 m x 8 m garden fence, 10 cm wide
FENCES: dict[str, tuple[list[tuple[float, float]], int, int]] = {
    "closed loop": ([(0, 0), (1000, 0), (1000, 800), (0, 800), (0, 0)], 1, 1),
    "nearly closed (2 cm gap)": ([(0, 2), (0, 800), (1000, 800), (1000, 0), (0, 0)], 1, 1),
    "open U": ([(0, 800), (0, 0), (1000, 0), (1000, 800)], 1, 0),
}


class TestClosedStrokes:
    """Review P1-1. A closed fence's stroke is an outer ring and a HOLE. The first
    contract put both into ``footprints``; the default builder extruded both; the
    result was a slab at fence height over the whole garden — 81.8 m² of ground
    where the fence stands on 3.6 m², under two caps of 160 m² together — with an
    inside-out inner wall, and G4, G7 and ``verify_builder`` all passed, because
    none of them asked how much ground the solid covers. These tests ask."""

    @pytest.mark.parametrize("rotation", [0.0, 33.0])
    @pytest.mark.parametrize("label", sorted(FENCES))
    def test_the_solid_covers_the_stroke_and_nothing_else(
        self, canvas: CanvasView, label: str, rotation: float
    ) -> None:
        centre_line, outers, holes = FENCES[label]
        item = _fence(centre_line)
        canvas.scene().addItem(item)
        apply_rotation(item, rotation)
        item.setPos(140.5, 60.25)
        item.metadata[METADATA_KEY] = 120.0
        snapshot = snapshot_records(canvas.scene())
        record = snapshot[str(item.item_id)]
        assert snapshot.skipped == ()

        # structure: outer rings counter-clockwise, their holes clockwise
        assert len(record.footprints) == outers
        assert sum(len(group) for group in record.holes) == holes
        assert all(signed_area(ring) > 0.0 and is_simple_ring(ring) for ring in record.footprints)
        assert all(signed_area(hole) < 0.0 for group in record.holes for hole in group)
        assert len(record.regions) == outers

        # G4 over EVERY ring: the record's rings are the 2D stroke's rings
        two_d = item_footprints(item)
        assert len(two_d) == outers + holes
        assert _strip_deviation(_rings_of(record), two_d) <= 3e-3

        # the area. Stated tolerances: (1) the cap IS the footprint region, to
        # float32 (1e-6); (2) the region is what the 2D stroke encloses, to
        # pyclipper's grid — the two inflate the same line in two frames (5e-4);
        # (3) both are the line's length times its width, to 0.5 % — square
        # joins and butt ends are the rest. The slab of the defect was off by 4,350 %.
        (part,) = verify_builder(default_builder, record)
        part.validate()
        cap = _cap_area(part.mesh)
        length = sum(math.dist(centre_line[i], centre_line[i + 1]) for i in range(len(centre_line) - 1))
        assert cap == pytest.approx(record.plan_area_cm2, rel=1e-6)
        assert cap == pytest.approx(_signed_area_sum(two_d), rel=5e-4)
        assert cap == pytest.approx(length * 10.0, rel=5e-3)
        assert float(part.mesh.bounds()[1][2]) == 120.0
        _assert_walls_face_away_from_the_solid(part.mesh, record)

    def test_the_defect_reconstructed_is_caught_by_verify_builder(self, canvas: CanvasView) -> None:
        """The pre-fix default builder — every ring a solid — on the real closed
        fence. It used to pass every gate; ``verify_builder`` now refuses it."""
        from open_garden_planner.core.scene3d import BuilderContractError, MeshPart, prism_mesh

        item = _fence(FENCES["closed loop"][0])
        canvas.scene().addItem(item)
        item.metadata[METADATA_KEY] = 120.0
        record = snapshot_records(canvas.scene())[str(item.item_id)]

        def every_ring_a_solid(rec: Record) -> tuple[MeshPart, ...]:
            rings = [*rec.footprints, *(hole for group in rec.holes for hole in group)]
            assert rec.height_cm is not None
            return (MeshPart(prism_mesh(rings, rec.height_cm)),)

        (slab,) = every_ring_a_solid(record)
        assert abs(_cap_area(slab.mesh)) > 40 * record.plan_area_cm2  # 1,600,162 vs 35,962 cm²
        with pytest.raises(BuilderContractError, match="covers 100% of a hole"):
            verify_builder(every_ring_a_solid, record)

    def test_closing_the_gap_is_a_geometry_change_only(self, canvas: CanvasView) -> None:
        """An open U dragged shut gains a hole — and the hole is part of the
        geometry signature, or the engine would keep the open fence's solid."""
        stage_item = _fence([(0, 800), (0, 0), (1000, 0), (1000, 800), (500, 800)])
        canvas.scene().addItem(stage_item)
        before = snapshot_records(canvas.scene())
        item_id = str(stage_item.item_id)
        assert before[item_id].holes == ()
        # the last vertex, moved onto the first: inside the bounding box, so no move
        stage_item._move_vertex_to(4, QPointF(0.0, 800.0))
        after = snapshot_records(canvas.scene())
        assert diff(before, after) == SceneDiff(geometry=(item_id,))
        assert len(after[item_id].holes[0]) == 1

    def test_a_stroke_that_crosses_itself(self, canvas: CanvasView) -> None:
        """A figure of eight: one outer ring with two holes (the loops)."""
        item = _fence([(0, 0), (400, 400), (800, 0), (800, 400), (400, 0), (0, 400), (0, 0)])
        canvas.scene().addItem(item)
        item.metadata[METADATA_KEY] = 100.0
        record = snapshot_records(canvas.scene())[str(item.item_id)]
        assert len(record.footprints) == 1 and len(record.holes[0]) >= 2
        (part,) = verify_builder(default_builder, record)
        assert _cap_area(part.mesh) == pytest.approx(record.plan_area_cm2, rel=1e-6)
        assert _cap_area(part.mesh) == pytest.approx(_signed_area_sum(item_footprints(item)), rel=5e-4)


# ── P2-6: a polygon that crosses itself ───────────────────────────────────────


class TestSelfCrossingPolygon:
    def test_a_bow_tie_house_becomes_its_two_lobes(self, canvas: CanvasView) -> None:
        """The user can draw it; ``item_footprints`` hands it to the 2D shadow as
        one self-crossing ring, whose own ground the shadow's union fills by the
        non-zero rule (60,000 cm² at a vertical sun: both lobes). The record
        carries what that rule fills — two simple triangles — so no builder ever
        receives a ring it cannot triangulate, and the first contract's mesh that
        failed ``validate()`` cannot be emitted."""
        bow = PolygonItem([QPointF(100.0, 100.0), QPointF(500.0, 400.0), QPointF(500.0, 100.0),
                           QPointF(100.0, 400.0)], object_type=ObjectType.HOUSE)
        canvas.scene().addItem(bow)
        snapshot = snapshot_records(canvas.scene())
        record = snapshot[str(bow.item_id)]
        assert snapshot.skipped == ()
        assert len(item_footprints(bow)) == 1  # 2D: the ring as drawn
        assert len(record.footprints) == 2 and record.holes == ()
        assert all(is_simple_ring(ring) and len(ring) == 3 for ring in record.footprints)
        assert [signed_area(ring) for ring in record.footprints] == pytest.approx([30000.0, 30000.0])
        from open_garden_planner.core.shadow_geometry import compute_scene_shadows

        vertical_sun = compute_scene_shadows([(item_footprints(bow)[0], 300.0)], 89.999, 180.0)
        assert _signed_area_sum(vertical_sun) == pytest.approx(record.plan_area_cm2, rel=1e-6)
        (part,) = verify_builder(default_builder, record)
        part.validate()
        assert _cap_area(part.mesh) == pytest.approx(60000.0, rel=1e-6)
        sync = SceneSync(RecordingSink(), validate=True)
        sync.apply(snapshot)
        assert sync.failures == {}

    def test_it_still_moves_and_turns_as_transform_only(self, canvas: CanvasView) -> None:
        bow = PolygonItem([QPointF(100.0, 100.0), QPointF(500.0, 400.0), QPointF(500.0, 100.0),
                           QPointF(100.0, 400.0)], object_type=ObjectType.HOUSE)
        canvas.scene().addItem(bow)
        item_id = str(bow.item_id)
        before = snapshot_records(canvas.scene())
        bow.moveBy(77.25, -13.5)
        moved = snapshot_records(canvas.scene())
        assert diff(before, moved) == SceneDiff(transform=(item_id,))
        apply_rotation(bow, 41.0)
        assert diff(moved, snapshot_records(canvas.scene())) == SceneDiff(transform=(item_id,))


# ── P2-3: one bad item must not blank the view ────────────────────────────────


def _break_species(item: Any) -> None:
    item.metadata["plant_species"] = {"scientific_name": None}  # the reviewer's case


def _break_species_name(item: Any) -> None:
    item.metadata["plant_species"] = {"common_name": 5}


def _break_category(item: Any) -> None:
    item.plant_category = "CONIFER"  # a string where the enum belongs


def _break_pattern(item: Any) -> None:
    item.fill_pattern = "GRASS"  # a string where the enum belongs


def _break_position(item: Any) -> None:
    item.setPos(float("nan"), 0.0)


#: one malformed item per group of record fields: (what breaks, the exception)
BREAKERS: dict[str, tuple[Callable[[Any], None], type[Exception]]] = {
    "geometry: species key": (_break_species, AttributeError),
    "geometry: species name": (_break_species_name, AttributeError),
    "geometry: plant category": (_break_category, AttributeError),
    "material: fill pattern": (_break_pattern, AttributeError),
    "transform: position": (_break_position, ValueError),
}


class TestContainment:
    """Review P2-3: ``plant_species = {"scientific_name": None}`` on ONE plant
    raised ``AttributeError`` out of ``snapshot_records`` — no 3D view for the
    whole plan. Now the item is left out, reported, and everything else is there."""

    @pytest.fixture(autouse=True)
    def _forget_what_was_logged(self) -> None:
        snapshot_module._reported.clear()

    @pytest.mark.parametrize("label", sorted(BREAKERS))
    def test_a_malformed_item_is_left_out_reported_and_logged_once(
        self, canvas: CanvasView, caplog: pytest.LogCaptureFixture, label: str
    ) -> None:
        breaker, error_type = BREAKERS[label]
        scene = canvas.scene()
        good = CircleItem(100.0, 100.0, 30.0, object_type=ObjectType.TREE)
        bad = CircleItem(300.0, 100.0, 30.0, object_type=ObjectType.TREE)
        also_good = _rectangle()
        for item in (good, bad, also_good):
            scene.addItem(item)
        healthy = snapshot_records(scene)
        assert len(healthy) == 3 and healthy.skipped == ()

        breaker(bad)
        with caplog.at_level(logging.WARNING, logger=snapshot_module.__name__):
            snapshot = snapshot_records(scene)
            again = snapshot_records(scene)  # the pipeline snapshots on every edit

        bad_id = str(bad.item_id)
        assert set(snapshot) == {str(good.item_id), str(also_good.item_id)}  # the rest is there
        assert diff(healthy, snapshot) == SceneDiff(removed=(bad_id,))
        (skipped,) = snapshot.skipped
        assert isinstance(skipped, SkippedItem)
        assert (skipped.item_id, skipped.kind) == (bad_id, "TREE")
        assert skipped.reason == "its record could not be built"
        assert isinstance(skipped.error, error_type)
        assert [s.item_id for s in again.skipped] == [bad_id]  # observable every time …
        mine = [r for r in caplog.records if bad_id in r.getMessage()]
        assert len(mine) == 1  # … logged once
        assert mine[0].levelno == logging.ERROR and mine[0].exc_info is not None
        assert mine[0].exc_info[1] is skipped.error  # with the exception

    def test_a_repaired_item_comes_back(self, canvas: CanvasView) -> None:
        scene = canvas.scene()
        tree = CircleItem(300.0, 100.0, 30.0, object_type=ObjectType.TREE)
        scene.addItem(tree)
        tree.metadata["plant_species"] = {"scientific_name": None}
        assert snapshot_records(scene) == {}
        tree.metadata["plant_species"] = {"scientific_name": "Malus domestica"}
        snapshot = snapshot_records(scene)
        assert list(snapshot) == [str(tree.item_id)] and snapshot.skipped == ()

    def test_the_log_is_keyed_by_item_and_kind_of_failure_not_by_message(
        self, canvas: CanvasView, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The pipeline snapshots on every edit, and an exception's message may
        carry a value that changes with each of them. So: the same kind of
        failure on the same item is one line however its message reads, and a
        different kind is a new line."""
        scene = canvas.scene()
        tree = CircleItem(300.0, 100.0, 30.0, object_type=ObjectType.TREE)
        scene.addItem(tree)
        with caplog.at_level(logging.WARNING, logger=snapshot_module.__name__):
            _break_species(tree)  # AttributeError: 'NoneType' object has no attribute 'strip'
            first = snapshot_records(scene).skipped[0].error
            _break_species_name(tree)  # AttributeError again, another message
            second = snapshot_records(scene).skipped[0].error
            assert str(first) != str(second) and type(first) is type(second)
            assert len(caplog.records) == 1
            tree.metadata.pop("plant_species")
            _break_position(tree)  # ValueError: another kind of failure
            assert isinstance(snapshot_records(scene).skipped[0].error, ValueError)
        assert len(caplog.records) == 2

    def test_the_snapshot_is_a_dict_to_every_consumer(self, canvas: CanvasView) -> None:
        scene = canvas.scene()
        shed = _rectangle()
        scene.addItem(shed)
        snapshot = snapshot_records(scene)
        assert isinstance(snapshot, dict) and snapshot == dict(snapshot)
        assert snapshot == {str(shed.item_id): snapshot[str(shed.item_id)]}
        sync = SceneSync(RecordingSink())
        assert sync.apply(snapshot) == SceneDiff(added=(str(shed.item_id),))
        assert type(sync.records[str(shed.item_id)]) is Record

    def test_the_fill_pattern_enum_is_what_the_healthy_path_reads(self, canvas: CanvasView) -> None:
        """The guard for the breaker above: a real ``FillPattern`` is the normal
        case and gives the pattern's name."""
        shed = _rectangle()
        canvas.scene().addItem(shed)
        shed.fill_pattern = FillPattern.GRAVEL
        assert snapshot_records(canvas.scene())[str(shed.item_id)].material.pattern == "GRAVEL"


# ── P2-5: two items, one id ───────────────────────────────────────────────────


class TestDuplicateIds:
    @pytest.fixture(autouse=True)
    def _forget_what_was_logged(self) -> None:
        snapshot_module._reported.clear()

    def test_the_lower_item_wins_and_the_other_is_reported(
        self, canvas: CanvasView, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A plan must not contain two items with one id (the loader dedupes,
        #403) — but if a scene does, the snapshot says so instead of letting the
        upper item silently overwrite the lower one's record."""
        scene = canvas.scene()
        low = RectangleItem(0.0, 0.0, 100.0, 100.0, object_type=ObjectType.TOOL_SHED)
        high = RectangleItem(300.0, 0.0, 50.0, 50.0, object_type=ObjectType.RAISED_BED)
        scene.addItem(low)
        scene.addItem(high)
        high._item_id = low.item_id
        shared = str(low.item_id)
        with caplog.at_level(logging.WARNING, logger=snapshot_module.__name__):
            snapshot = snapshot_records(scene)
            snapshot_records(scene)
        assert list(snapshot) == [shared] and snapshot[shared].kind == "TOOL_SHED"  # first wins
        (skipped,) = snapshot.skipped
        assert (skipped.item_id, skipped.kind, skipped.error) == (shared, "RAISED_BED", None)
        assert skipped.reason == "another item already has this id"
        (logged,) = caplog.records  # once, though snapshotted twice
        assert logged.levelno == logging.WARNING and shared in logged.getMessage()
        assert "already has this id" in logged.getMessage()


# ── a HOUSE and its ridge ─────────────────────────────────────────────────────


@pytest.fixture
def house_stage(canvas: CanvasView) -> tuple[CanvasView, Any, Any]:
    house = PolygonItem(
        [QPointF(1000.0, 800.0), QPointF(1900.0, 800.0), QPointF(1900.0, 1340.0),
         QPointF(1000.0, 1340.0)], object_type=ObjectType.HOUSE)
    ridge = PolylineItem([QPointF(1000.0, 1070.0), QPointF(1900.0, 1070.0)],
                         object_type=ObjectType.ROOF_RIDGE)
    house.set_metadata("ridge_item_id", str(ridge.item_id))
    ridge.set_metadata("owner_polygon_id", str(house.item_id))
    canvas.scene().addItem(house)
    canvas.scene().addItem(ridge)
    return canvas, house, ridge


class TestHouseRidge:
    def test_ridge_ends_are_in_the_house_s_local_frame(self, house_stage) -> None:
        canvas, house, _ridge = house_stage
        record = snapshot_records(canvas.scene())[str(house.item_id)]
        assert record.transform == Transform(1450.0, 1070.0, 0.0)
        assert record.params["ridge"] == ((-450.0, 0.0), (450.0, 0.0))

    def test_a_ridge_item_gets_no_record_of_its_own(self, house_stage) -> None:
        """Review P2-4. The ridge reaches 3D as its HOUSE's ``ridge`` param. As a
        record it changed GEOMETRY whenever the house turned (the app re-derives
        its points: measured on 400 of 400 rotations) — a rebuild for a line the
        house's own builder draws."""
        canvas, house, ridge = house_stage
        snapshot = snapshot_records(canvas.scene())
        assert list(snapshot) == [str(house.item_id)]
        assert snapshot.skipped == ()  # by design, not a fault
        assert len(collect_scene3d_records(canvas.scene())) == 2  # the Qt 3D view still draws the line
        ridge.setVisible(False)  # … and hiding the line does not take the ridge off the roof
        assert "ridge" in snapshot_records(canvas.scene())[str(house.item_id)].params

    @pytest.mark.parametrize("angle", [17.0, 90.0, 213.5])
    def test_turning_the_house_is_exactly_one_transform_entry(self, house_stage, angle) -> None:
        """The baseline is taken BEFORE the turn (the first version of this test
        turned first and so never compared across a rotation). Through the app's
        command, as the GUI and the Agent API do it."""
        canvas, house, _ridge = house_stage
        hid = str(house.item_id)
        before = snapshot_records(canvas.scene())
        ridge_local = before[hid].params["ridge"]
        canvas.command_manager.execute(RotateItemCommand(house, 0.0, angle, apply_rotation))
        turned = snapshot_records(canvas.scene())
        assert diff(before, turned) == SceneDiff(transform=(hid,))  # one entry: the house's
        assert turned[hid].transform.rotation_deg == angle
        assert turned[hid].params["ridge"] == ridge_local  # ADR-046 re-derived it; locally it stands
        assert turned[hid].same_geometry(before[hid])
        canvas.command_manager.undo()
        assert snapshot_records(canvas.scene()) == before

    @pytest.mark.parametrize("angle", [0.0, 17.0, 90.0, 213.5])
    def test_moving_a_turned_house_is_exactly_one_transform_entry(self, house_stage, angle) -> None:
        canvas, house, _ridge = house_stage
        hid = str(house.item_id)
        apply_rotation(house, angle)  # PolygonItem re-derives the ridge (ADR-046)
        before = snapshot_records(canvas.scene())
        house.moveBy(321.7, -45.3)  # the app shifts the ridge item's points by the same delta
        after = snapshot_records(canvas.scene())
        assert diff(before, after) == SceneDiff(transform=(hid,))
        assert after[hid].params["ridge"] == before[hid].params["ridge"]

    def test_a_thousand_moves_never_leak_float_dust_into_the_geometry(self, house_stage) -> None:
        """The app re-writes the ridge points by ``+ delta`` on every move, so their
        float error grows (measured: 1.1e-10 cm after 20,000 moves). On the record
        grid of 1e-7 cm it never becomes a 'change' — without the grid, the very
        first move rebuilds the house."""
        canvas, house, _ridge = house_stage
        hid = str(house.item_id)
        apply_rotation(house, 17.0)
        first = snapshot_records(canvas.scene())[hid]
        rng = np.random.default_rng(385)
        rebuilds = 0
        last = first
        for dx, dy in rng.uniform(-37.3, 41.9, size=(1000, 2)):
            house.moveBy(float(dx), float(dy))
            now = snapshot_records(canvas.scene())[hid]
            rebuilds += not last.same_geometry(now)
            last = now
        assert rebuilds == 0
        assert last.params["ridge"] == first.params["ridge"]

    def test_an_edited_ridge_changes_the_house_s_geometry(self, house_stage) -> None:
        canvas, house, ridge = house_stage
        hid = str(house.item_id)
        before = snapshot_records(canvas.scene())
        ridge._move_vertex_to(0, QPointF(1000.0, 900.0))  # a hand drag along the wall
        after = snapshot_records(canvas.scene())
        result = diff(before, after)
        assert hid in result.geometry and hid not in result.transform
        assert after[hid].params["ridge"] != before[hid].params["ridge"]

    def test_a_house_without_a_ridge_has_no_ridge_param(self, house_stage) -> None:
        canvas, house, ridge = house_stage
        canvas.scene().removeItem(ridge)
        assert "ridge" not in snapshot_records(canvas.scene())[str(house.item_id)].params

    def test_a_ridge_link_to_something_else_is_no_ridge(self, house_stage) -> None:
        """A stale or corrupt ``ridge_item_id`` (it names a shed, or a line that
        lost a point) gives a HOUSE without a ridge param — the builder falls back,
        nothing raises."""
        canvas, house, ridge = house_stage
        shed = _rectangle()
        canvas.scene().addItem(shed)
        house.set_metadata("ridge_item_id", str(shed.item_id))
        assert "ridge" not in snapshot_records(canvas.scene())[str(house.item_id)].params
        house.set_metadata("ridge_item_id", str(ridge.item_id))
        ridge._points = ridge._points[:1]
        assert "ridge" not in snapshot_records(canvas.scene())[str(house.item_id)].params

    def test_the_bench_house(self, qtbot) -> None:
        scene = CanvasScene()
        ProjectManager().load(scene, PLANS / "bench_small.ogp")
        houses = [r for r in snapshot_records(scene, JUNE).values() if r.kind == "HOUSE"]
        assert len(houses) == 1
        (start, end) = houses[0].params["ridge"]  # type: ignore[misc]
        assert start != end and houses[0].height_cm == 450.0


# ── G7: meshes of real items ──────────────────────────────────────────────────


class TestDefaultBuilderOnRealItems:
    @pytest.mark.parametrize("rotation", [0.0, 213.5])
    def test_every_mesh_validates_and_the_top_is_the_height(self, stage: Stage, rotation: float) -> None:
        apply_rotation(stage.item, rotation)
        stage.item.metadata[METADATA_KEY] = 187.3
        stage.step()
        parts = verify_builder(default_builder, stage.record)
        assert len(parts) == 1
        parts[0].validate()
        top = float(parts[0].mesh.bounds()[1][2])
        assert top == float(np.float32(187.3))  # exact, in the float32 the engine receives

    def test_a_shape_without_a_height_has_no_solid(self, canvas: CanvasView) -> None:
        lawn = RectangleItem(0.0, 0.0, 500.0, 400.0, object_type=ObjectType.LAWN)
        canvas.scene().addItem(lawn)
        record = snapshot_records(canvas.scene())[str(lawn.item_id)]
        assert record.height_cm is None and verify_builder(default_builder, record) == ()

    @pytest.mark.parametrize("plan", ["bench_small.ogp", "bench_large.ogp"])
    def test_every_bench_record_builds_a_valid_mesh(self, qtbot, plan: str) -> None:
        scene = CanvasScene()
        ProjectManager().load(scene, PLANS / plan)
        records = snapshot_records(scene, JUNE)
        solids = 0
        for record in records.values():
            parts = verify_builder(default_builder, record)
            assert bool(parts) is (record.height_cm is not None)
            solids += len(parts)
        assert solids == sum(1 for r in records.values() if r.height_cm is not None) > 80


# ── G8: an unchanged scene ────────────────────────────────────────────────────


@pytest.mark.parametrize("plan", ["bench_small.ogp", "bench_large.ogp"])
def test_unchanged_scene_snapshots_identically_100_times(qtbot, plan: str) -> None:
    """The float-noise guard: 100 snapshots of an untouched plan compare equal and
    diff empty. Deterministic by construction — nothing here depends on the grid."""
    scene = CanvasScene()
    ProjectManager().load(scene, PLANS / plan)
    first = snapshot_records(scene, JUNE)
    assert len(first) == (98 if plan == "bench_small.ogp" else 424)  # every item but the roof ridge
    assert first.skipped == ()
    for _ in range(100):
        again = snapshot_records(scene, JUNE)
        assert again == first
        assert list(again) == list(first)
        assert diff(first, again).is_empty and diff(again, first).is_empty
