"""An item's style ATTRIBUTES describe what its brush and pen paint — for every
operation that makes an item out of an item (review of Phase 17 L1.5, P1-1).

An item states its look twice: in ``fill_color`` / ``fill_pattern`` /
``stroke_color`` / ``stroke_width`` / ``stroke_style`` (what the project file,
the clipboard, the properties panel and the 3D ground bake read) and in its live
``brush()`` and ``pen()`` (what the canvas paints). The offset tool and the
corner tools copied the second and not the first: an offset copy of a blue
brick terrace was saved, and baked, as default wood. Nothing asserted the class.

This file does, in three steps:

1. a CENSUS, read off the source, of the places that build an item from an item
   — a new one fails here until it is added to the table below;
2. every such operation, on a recoloured, patterned, dash-stroked source of
   every shape it accepts: the derived item's attributes describe its brush and
   pen (``core.item_style.style_gaps``), and where the operation is a copy, the
   copy looks like its source;
3. the look survives the project file: derived → saved → loaded.

Operations that CANNOT derive an item, and are therefore not here: undo / redo
of a delete, group / ungroup, move / rotate / resize / vertex edits (the same
item object), changing an item's type (it re-styles the item from the type:
``GardenItemMixin``'s type change sets the attributes and calls
``_setup_styling``), the Agent API (it creates items from a spec without style
keys and has no copy, mirror, array or offset tool), plant lists and smart
symbols (they build items from data, not from items).
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QMouseEvent, QPen

from open_garden_planner.core import ProjectManager
from open_garden_planner.core.fill_patterns import FillPattern, create_pattern_brush
from open_garden_planner.core.item_style import _same_brush, style_gaps
from open_garden_planner.core.mirror_geometry import build_mirrored_item
from open_garden_planner.core.object_types import ObjectType, PathFenceStyle, StrokeStyle
from open_garden_planner.core.tools import ToolType
from open_garden_planner.core.tools.corner_edit_base import (
    CornerTarget,
    rebuild_with_corner_replaced,
    rectangle_corners_local,
)
from open_garden_planner.core.tools.offset_tool import _compute_offset_item
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.canvas.canvas_view import CanvasView
from open_garden_planner.ui.canvas.items import (
    CircleItem,
    EllipseItem,
    PolygonItem,
    PolylineItem,
    RectangleItem,
)

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "open_garden_planner"

FILL = QColor(30, 60, 200)        # blue …
PATTERN = FillPattern.BRICK       # … brick: neither is any type's default
STROKE = QColor(200, 30, 30, 220)
WIDTH = 6.5
STYLE = StrokeStyle.DASHED


def restyle(item: Any) -> Any:
    """Recolour, re-pattern and re-stroke an item the way the properties panel
    does: the attribute AND the live Qt object, together."""
    if not isinstance(item, PolylineItem):
        item.fill_pattern = PATTERN
        item.fill_color = QColor(FILL)
        item.setBrush(create_pattern_brush(PATTERN, QColor(FILL)))
    # a style preset paints its own picture along the line: its pen is not dashed
    dashed = getattr(item, "path_fence_style", PathFenceStyle.NONE) is PathFenceStyle.NONE
    item.stroke_color = QColor(STROKE)
    item.stroke_width = WIDTH
    pen = QPen(item.pen())
    pen.setColor(QColor(STROKE))
    pen.setWidthF(WIDTH)
    if dashed:
        item.stroke_style = STYLE
        pen.setStyle(STYLE.to_qt_pen_style())
    item.setPen(pen)
    assert style_gaps(item) == []
    return item


SOURCES: dict[str, Callable[[], Any]] = {
    "rectangle": lambda: RectangleItem(100.0, 100.0, 300.0, 200.0, object_type=ObjectType.TERRACE_PATIO),
    "polygon": lambda: PolygonItem(
        [QPointF(100.0, 100.0), QPointF(400.0, 100.0), QPointF(400.0, 180.0), QPointF(220.0, 180.0),
         QPointF(220.0, 300.0), QPointF(100.0, 300.0)], object_type=ObjectType.LAWN),
    "circle": lambda: CircleItem(300.0, 300.0, 90.0, object_type=ObjectType.GENERIC_CIRCLE),
    "ellipse": lambda: EllipseItem(100.0, 100.0, 300.0, 180.0, object_type=ObjectType.GENERIC_ELLIPSE),
    "polyline": lambda: PolylineItem(
        [QPointF(100.0, 100.0), QPointF(300.0, 140.0), QPointF(360.0, 320.0)], object_type=ObjectType.FENCE),
    "styled path": lambda: PolylineItem(
        [QPointF(100.0, 100.0), QPointF(300.0, 140.0), QPointF(360.0, 320.0)], object_type=ObjectType.PATH,
        path_fence_style=PathFenceStyle.GRAVEL_PATH),
}


def source(shape: str) -> Any:
    return restyle(SOURCES[shape]())


def looks_like(copy: Any, original: Any) -> list[str]:
    """What of the original's painted look the copy does not have."""
    off = []
    if not _same_brush(copy.brush(), original.brush()):
        off.append("brush")
    mine, theirs = copy.pen(), original.pen()
    if (mine.color(), mine.widthF(), mine.style()) != (theirs.color(), theirs.widthF(), theirs.style()):
        off.append(f"pen {mine.color().name()} {mine.widthF()} {mine.style().name}")
    return off


_SCENES: list[CanvasScene] = []  # a scene owns its items: kept while the module's tests run


def through_the_file(item: Any, tmp_path: Path) -> Any:
    """``item`` saved in a plan and loaded again — the one item of the loaded plan."""
    scene = CanvasScene(width_cm=2000.0, height_cm=1500.0)
    if item.scene() is not None:
        item.scene().removeItem(item)
    scene.addItem(item)
    manager = ProjectManager()
    path = tmp_path / f"plan_{len(list(tmp_path.iterdir()))}.ogp"
    manager.save(scene, path)
    loaded = CanvasScene()
    _SCENES.extend((scene, loaded))
    ProjectManager().load(loaded, path)
    found = [candidate for candidate in loaded.items() if getattr(candidate, "object_type", None) is not None
             and candidate.parentItem() is None]
    assert len(found) == 1, found
    return found[0]


# ── the operations ────────────────────────────────────────────────────────────


def _serialised(item: Any) -> Any:
    """The clipboard, duplicate and the four array commands: ``_serialize_item`` →
    ``_deserialize_item`` (``CanvasView`` delegates both to ``ProjectManager``)."""
    data = ProjectManager._serialize_item(item)  # noqa: SLF001
    assert data is not None
    return ProjectManager._deserialize_item(data)  # noqa: SLF001


def _mirrored(item: Any) -> Any:
    return build_mirrored_item(item, QPointF(500.0, 0.0), QPointF(500.0, 400.0))


def _offset(item: Any) -> Any:
    return _compute_offset_item(item, 20.0, inward=False)


def _corner(item: Any) -> Any:
    """What the chamfer and the fillet tool both build of their host
    (``rebuild_with_corner_replaced``), at the item's second corner."""
    if isinstance(item, RectangleItem):
        corners = [item.mapToScene(p) for p in rectangle_corners_local(item)]
    elif isinstance(item, PolygonItem):
        corners = [item.mapToScene(item.polygon().at(k)) for k in range(item.polygon().count())]
    else:
        corners = [item.mapToScene(p) for p in item.points]
    before, corner, after = corners[0], corners[1], corners[2]

    def toward(a: QPointF, b: QPointF) -> QPointF:
        length = ((b.x() - a.x()) ** 2 + (b.y() - a.y()) ** 2) ** 0.5
        return QPointF(a.x() + (b.x() - a.x()) * 15.0 / length, a.y() + (b.y() - a.y()) * 15.0 / length)

    target = CornerTarget(item=item, vertex_index=1, p_prev_scene=before, p_corner_scene=corner,
                          p_next_scene=after)
    return rebuild_with_corner_replaced(target, toward(corner, before), toward(corner, after))


def _re_pointed(item: Any) -> Any:
    return item.clone_with_points([QPointF(0.0, 0.0), QPointF(150.0, 40.0), QPointF(260.0, 10.0)])


#: operation → (how, the shapes it accepts, is the result a COPY that keeps the look)
OPERATIONS: dict[str, tuple[Callable[[Any], Any], tuple[str, ...], bool]] = {
    "clipboard / duplicate / arrays": (_serialised, tuple(SOURCES), True),
    "mirror": (_mirrored, tuple(SOURCES), True),
    # a rectangle, circle or ellipse is offset as a new shape of its type's default
    # look — in step, and no copy of its source's look (shipped behaviour, not changed)
    "offset": (_offset, ("polygon", "polyline", "styled path"), True),
    "offset (new shape)": (_offset, ("rectangle", "circle", "ellipse"), False),
    "chamfer / fillet": (_corner, ("rectangle", "polygon", "polyline", "styled path"), True),
    "clone_with_points (trim, corner, mirror of a line)": (_re_pointed, ("polyline", "styled path"), True),
}
CASES = [(name, shape) for name, (_how, shapes, _copy) in OPERATIONS.items() for shape in shapes]


class TestEveryDerivedItemSaysWhatItPaints:
    @pytest.mark.parametrize(("operation", "shape"), CASES, ids=[f"{o} of a {s}" for o, s in CASES])
    def test_the_attributes_describe_the_brush_and_the_pen(
            self, qtbot: Any, operation: str, shape: str) -> None:
        how, _shapes, is_copy = OPERATIONS[operation]
        original = source(shape)
        derived = how(original)
        assert derived is not None
        assert style_gaps(derived) == []
        if is_copy:
            assert looks_like(derived, original) == []

    @pytest.mark.parametrize(("operation", "shape"), CASES, ids=[f"{o} of a {s}" for o, s in CASES])
    def test_the_look_survives_the_project_file(
            self, qtbot: Any, tmp_path: Path, operation: str, shape: str) -> None:
        """The shipped 2D defect behind the finding: the file is written from the
        attributes, so a copy that did not carry them changed colour on save."""
        how, _shapes, _copy = OPERATIONS[operation]
        derived = how(source(shape))
        loaded = through_the_file(derived, tmp_path)
        assert style_gaps(loaded) == []
        assert looks_like(loaded, derived) == []

    @pytest.mark.parametrize("shape", sorted(SOURCES))
    def test_a_loaded_item_says_what_it_paints(self, qtbot: Any, tmp_path: Path, shape: str) -> None:
        """Load → save → load: the same look both times, and attributes that state it
        (a loaded item's stroke attributes were its type's defaults: the properties
        panel showed them, and the undo of a type change painted with them)."""
        original = source(shape)
        once = through_the_file(original, tmp_path)
        assert style_gaps(once) == [] and looks_like(once, original) == []
        twice = through_the_file(once, tmp_path)
        assert style_gaps(twice) == [] and looks_like(twice, original) == []


def _left_click() -> MagicMock:
    event = MagicMock(spec=QMouseEvent)
    event.button.return_value = Qt.MouseButton.LeftButton
    event.modifiers.return_value = Qt.KeyboardModifier.NoModifier
    return event


@pytest.fixture()
def canvas(qtbot: Any) -> CanvasView:
    scene = CanvasScene(width_cm=5000, height_cm=3000)
    view = CanvasView(scene)
    qtbot.addWidget(view)
    view.set_snap_enabled(False)
    return view


class TestThroughTheRealTools:
    """The same, through the commands a user reaches — the tools' own entry points."""

    @pytest.mark.parametrize("shape", ["rectangle", "polygon"])
    def test_a_trimmed_shape_becomes_a_line_that_states_its_pen(self, canvas: CanvasView, shape: str) -> None:
        if shape == "rectangle":
            host: Any = RectangleItem(100.0, 100.0, 800.0, 800.0, object_type=ObjectType.TERRACE_PATIO)
        else:
            host = PolygonItem([QPointF(100.0, 100.0), QPointF(900.0, 100.0), QPointF(900.0, 900.0),
                                QPointF(100.0, 900.0)], object_type=ObjectType.LAWN)
        restyle(host)
        canvas.scene().addItem(host)
        for x in (300.0, 700.0):
            canvas.scene().addItem(PolylineItem([QPointF(x, 0.0), QPointF(x, 200.0)], object_type=ObjectType.FENCE))
        before = {id(item) for item in canvas.scene().items()}
        canvas.set_active_tool(ToolType.TRIM_EXTEND)
        tool = canvas.tool_manager.active_tool
        tool.mouse_move(_left_click(), QPointF(500.0, 100.0))
        tool.mouse_press(_left_click(), QPointF(500.0, 100.0))
        made = [item for item in canvas.scene().items()
                if isinstance(item, PolylineItem) and id(item) not in before]
        assert len(made) == 1 and host.scene() is None
        (line,) = made
        assert style_gaps(line) == []
        pen = line.pen()
        assert (pen.color(), pen.widthF(), pen.style()) == (STROKE, WIDTH, STYLE.to_qt_pen_style())
        assert (QColor(line.stroke_color), line.stroke_width, line.stroke_style) == (STROKE, WIDTH, STYLE)

    def test_a_trimmed_line_keeps_its_look_in_both_pieces(self, canvas: CanvasView) -> None:
        line = restyle(PolylineItem([QPointF(0.0, 500.0), QPointF(1000.0, 500.0)], object_type=ObjectType.FENCE))
        canvas.scene().addItem(line)
        for x in (300.0, 700.0):
            canvas.scene().addItem(PolylineItem([QPointF(x, 400.0), QPointF(x, 600.0)], object_type=ObjectType.FENCE))
        before = {id(item) for item in canvas.scene().items()}
        canvas.set_active_tool(ToolType.TRIM_EXTEND)
        tool = canvas.tool_manager.active_tool
        tool.mouse_move(_left_click(), QPointF(500.0, 500.0))
        tool.mouse_press(_left_click(), QPointF(500.0, 500.0))
        pieces = [item for item in canvas.scene().items()
                  if isinstance(item, PolylineItem) and id(item) not in before]
        assert len(pieces) == 2
        for piece in pieces:
            assert style_gaps(piece) == [] and looks_like(piece, line) == []

    @pytest.mark.parametrize("shape", sorted(SOURCES))
    def test_duplicate_and_paste(self, canvas: CanvasView, shape: str) -> None:
        original = source(shape)
        canvas.scene().addItem(original)
        original.setSelected(True)
        canvas.duplicate_selected()
        canvas.scene().clearSelection()
        original.setSelected(True)
        canvas.copy_selected()
        canvas.paste()
        copies = [item for item in canvas.scene().items()
                  if type(item) is type(original) and item is not original]
        assert len(copies) == 2
        for copy in copies:
            assert style_gaps(copy) == [] and looks_like(copy, original) == []


# ── the census: where items are built from items ──────────────────────────────

#: Every place in the product that builds a canvas item out of another one,
#: ``module → the functions that do it`` — and, beside each, the operation above
#: that covers it. Read off the source by the test below; a function that is not
#: here fails it.
DERIVING: dict[str, dict[str, str]] = {
    "core/mirror_geometry.py": {
        "_mirror_polygon": "mirror", "_mirror_circle": "mirror", "_mirror_box": "mirror",
        "_mirror_polyline": "mirror (clone_with_points)",
        # arcs and Béziers: their own stroke model (``ArcItem`` / ``BezierItem`` hold
        # ``stroke_color`` and ``stroke_width`` and build their pen from them); they
        # have no fill and no place on the 3D ground
        "_mirror_arc": "not a GardenItem fill/stroke: own model", "_mirror_bezier": "own model",
    },
    "core/tools/offset_tool.py": {
        "_offset_rectangle": "offset (new shape)", "_offset_circle": "offset (new shape)",
        "_offset_ellipse": "offset (new shape)", "_offset_polygon": "offset", "_offset_polyline": "offset",
    },
    "core/tools/corner_edit_base.py": {"rebuild_with_corner_replaced": "chamfer / fillet"},
    "core/tools/trim_tool.py": {
        "_trim_polyline": "trim (clone_with_points)", "_trim_polygon": "trim", "_trim_rectangle": "trim",
    },
    "ui/canvas/items/polyline_item.py": {"clone_with_points": "clone_with_points"},
    "ui/canvas/canvas_view.py": {
        "paste": "clipboard", "duplicate_selected": "duplicate", "create_linear_array": "arrays",
        "create_grid_array": "arrays", "create_circular_array": "arrays",
        "create_array_along_path": "arrays",
    },
}

_ITEM_CLASSES = {"RectangleItem", "PolygonItem", "PolylineItem", "CircleItem", "EllipseItem", "ArcItem",
                 "BezierItem", "cls"}


def _reads_a_look(node: ast.AST) -> bool:
    """Does the expression read another object's ``brush()`` or ``pen()``?"""
    return any(isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
               and inner.func.attr in ("brush", "pen") for inner in ast.walk(node))


def _derives_an_item(function: ast.AST) -> bool:
    """Does this function build an item from another item? One of four marks:

    - it constructs an item class with a keyword taken off another ITEM
      (``object_type=x.object_type``, ``layer_id=x.layer_id``);
    - it calls ``clone_with_points``, or turns a serialised item back into one
      (``_deserialize_item``);
    - it calls the style helper (``copy_visual_style`` / ``adopt_pen``);
    - it hands one object's ``brush()`` or ``pen()`` to another's ``setBrush`` /
      ``setPen`` — the raw copy the finding was about (a painter's is not an item's).
    """
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", "")
        if name in ("clone_with_points", "_deserialize_item", "copy_visual_style", "adopt_pen",
                    "_copy_visual_style"):
            return True
        if name in _ITEM_CLASSES and any(
                isinstance(keyword.value, ast.Attribute) and keyword.value.attr == keyword.arg
                and keyword.arg in ("object_type", "layer_id") for keyword in node.keywords):
            return True
        if (name in ("setBrush", "setPen") and isinstance(callee, ast.Attribute)
                and not (isinstance(callee.value, ast.Name) and callee.value.id == "painter")
                and any(_reads_a_look(argument) for argument in node.args)):
            return True
    return False


def test_no_brush_or_pen_is_copied_past_the_helper() -> None:
    """The raw copy — ``a.setBrush(QBrush(b.brush()))`` — exists in ONE place:
    ``core/item_style.py``. Everywhere else it is the defect of the finding."""
    raw = []
    for folder in ("core", "ui/canvas"):
        for path in sorted((SRC / folder).rglob("*.py")):
            relative = path.relative_to(SRC).as_posix()
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("setBrush", "setPen")
                        and not (isinstance(node.func.value, ast.Name) and node.func.value.id == "painter")
                        and any(_reads_a_look(argument) for argument in node.args)):
                    raw.append(f"{relative}:{node.lineno}")
    assert raw == []
    # control: the scan sees the two lines the offset tool had
    was = ast.parse("\n".join((
        "new_item.setPen(QPen(item.pen()))", "new_item.setBrush(QBrush(item.brush()))",
        "painter.setPen(self.pen())", "pen = item.pen()", "item.setPen(pen)")))
    assert sum(1 for node in ast.walk(was) if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute) and node.func.attr in ("setBrush", "setPen")
               and not (isinstance(node.func.value, ast.Name) and node.func.value.id == "painter")
               and any(_reads_a_look(argument) for argument in node.args)) == 2


def test_the_census_of_item_from_item_operations_is_complete() -> None:
    """Every function under ``core/`` and ``ui/canvas/`` that builds an item from an
    item is in ``DERIVING`` — so a new offset, split or join tool cannot ship
    without being held to the rule above."""
    found: dict[str, set[str]] = {}
    for folder in ("core", "ui/canvas"):
        for path in sorted((SRC / folder).rglob("*.py")):
            relative = path.relative_to(SRC).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _derives_an_item(node):
                    found.setdefault(relative, set()).add(node.name)
    # the loader, the wrapper that IS the primitive and the helper itself are not derivations
    found.get("ui/canvas/canvas_view.py", set()).discard("_deserialize_item")
    found.pop("core/project.py", None)
    found.pop("core/item_style.py", None)
    assert {module: set(names) for module, names in DERIVING.items()} == found
