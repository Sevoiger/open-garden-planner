"""Reference centres survive their complete editing and persistence lifecycle."""

import json

import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QComboBox, QDialog, QLineEdit

from open_garden_planner.core.commands import DeleteItemsCommand, GroupCommand
from open_garden_planner.core.measure_snapper import AnchorType, get_anchor_points
from open_garden_planner.core.project import ProjectManager
from open_garden_planner.core.reference_points import is_reference_point
from open_garden_planner.core.snap.providers.nearest import NearestSnapProvider
from open_garden_planner.core.snap.providers.perpendicular import PerpendicularSnapProvider
from open_garden_planner.core.snap.providers.tangent import TangentSnapProvider
from open_garden_planner.core.tools import ToolType
from open_garden_planner.ui import reference_points
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.canvas.canvas_view import CanvasView
from open_garden_planner.ui.canvas.items import CircleItem, PolygonItem, PolylineItem
from open_garden_planner.ui.widgets.toolbar import MainToolbar


@pytest.fixture
def canvas(qtbot):
    scene = CanvasScene(width_cm=5000, height_cm=3000)
    view = CanvasView(scene)
    qtbot.addWidget(view)
    return scene, view


def add_point(monkeypatch, scene, view, parent=None):
    monkeypatch.setattr(reference_points, "point_dialog", lambda *_args: (
        "Borne OGE", parent.item_id if parent else None,
    ))
    assert reference_points.place_reference_point(view, QPointF(240, 180))
    return next(obj for obj in scene.items() if is_reference_point(obj))


@pytest.mark.parametrize("attached", [False, True])
def test_save_reopen_undo_and_delete(canvas, qtbot, monkeypatch, tmp_path, attached):
    scene, view = canvas
    parent = PolygonItem([QPointF(50, 50), QPointF(500, 50), QPointF(400, 600)])
    scene.addItem(parent)
    point = add_point(monkeypatch, scene, view, parent if attached else None)
    identity = point.item_id
    parent_id = parent.item_id if attached else None
    assert point.parent_bed_id == parent_id
    assert point.mapToScene(point.rect().center()) == QPointF(240, 180)
    view.command_manager.undo()
    assert point.scene() is None
    assert identity not in parent.child_item_ids
    view.command_manager.redo()
    assert point.parent_bed_id == parent_id
    assert (identity in parent.child_item_ids) == attached

    manager = ProjectManager()
    path = tmp_path / "references.ogp"
    manager.save(scene, path)
    raw = json.loads(path.read_text())
    record = next(obj for obj in raw["objects"] if obj["item_id"] == str(identity))
    assert record["type"] == "circle"  # Previous versions retain a visible fallback.
    assert record["metadata"]["reference_point"] is True
    other = CanvasScene()
    other_view = CanvasView(other)
    qtbot.addWidget(other_view)
    ProjectManager().load(other, path)
    restored = other.find_item_by_id(identity)
    assert restored.name == "Borne OGE"
    assert restored.parent_bed_id == parent_id
    assert restored.mapToScene(restored.rect().center()) == QPointF(240, 180)
    anchors = get_anchor_points(restored)
    assert len(anchors) == 1
    assert anchors[0].anchor_type == AnchorType.CENTER
    assert anchors[0].point == QPointF(240, 180)
    if attached:
        loaded_parent = other.find_item_by_id(parent_id)
        assert identity in loaded_parent.child_item_ids
        command = DeleteItemsCommand(other, [loaded_parent])
        other_view.command_manager.execute(command)
        assert restored.scene() is other
        assert restored.parent_bed_id is None
        other_view.command_manager.undo()
        assert restored.parent_bed_id == parent_id


@pytest.mark.parametrize("shape", [PolygonItem, PolylineItem])
def test_parent_drag_and_duplicate_keep_reference_offset(canvas, monkeypatch, shape):
    scene, view = canvas
    parent = shape([QPointF(100, 100), QPointF(500, 100), QPointF(500, 500)])
    scene.addItem(parent)
    point = add_point(monkeypatch, scene, view, parent)
    scene.clearSelection()
    parent.setSelected(True)
    view._drag_start_positions = {parent: QPointF(parent.pos())}
    parent.setPos(35, 60)
    view._propagate_bed_children_during_drag()
    assert point.pos() == QPointF(275, 240)
    view._finalize_drag_move()
    assert point.pos() == QPointF(275, 240)
    view.command_manager.undo()
    assert point.pos() == QPointF(240, 180)
    view.command_manager.redo()
    assert point.pos() == QPointF(275, 240)

    view.duplicate_selected()
    points = [obj for obj in scene.items() if is_reference_point(obj)]
    assert len(points) == 2
    copied = next(obj for obj in points if obj is not point)
    assert copied.item_id != point.item_id
    assert copied.parent_bed_id not in (None, parent.item_id)
    assert copied.item_id in scene.find_item_by_id(copied.parent_bed_id).child_item_ids
    copied_parent = copied.parent_bed_id
    view.command_manager.undo()
    view.command_manager.redo()
    assert copied.parent_bed_id == copied_parent
    assert copied.item_id in scene.find_item_by_id(copied_parent).child_item_ids


def test_edit_name_and_detach_is_one_undo(canvas, monkeypatch):
    scene, view = canvas
    parent = PolygonItem([QPointF(0, 0), QPointF(500, 0), QPointF(500, 500)])
    scene.addItem(parent)
    point = add_point(monkeypatch, scene, view, parent)
    monkeypatch.setattr(reference_points, "point_dialog", lambda *_args: ("Poteau 1", None))
    reference_points.edit_reference_point(point)
    assert point.name == "Poteau 1"
    assert point.parent_bed_id is None
    assert point.item_id not in parent.child_item_ids
    view.command_manager.undo()
    assert point.name == "Borne OGE"
    assert point.parent_bed_id == parent.item_id


def test_dialog_defaults_to_selected_parent_and_cancel_is_inert(canvas, monkeypatch):
    scene, view = canvas
    parent = CircleItem(250, 250, 100, name="Clôture")
    scene.addItem(parent)
    parent.setSelected(True)

    def accept(dialog):
        assert dialog.findChild(QComboBox).currentData() == parent.item_id
        dialog.findChild(QLineEdit).setText("Poteau nord")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", accept)
    assert reference_points.point_dialog(view) == ("Poteau nord", parent.item_id)
    monkeypatch.setattr(QDialog, "exec", lambda _dialog: QDialog.DialogCode.Rejected)
    assert not reference_points.place_reference_point(view, QPointF(250, 250))
    assert not view.command_manager.can_undo


def test_toolbar_placement_through_viewport(canvas, qtbot, monkeypatch):
    scene, view = canvas
    toolbar = MainToolbar(view)
    qtbot.addWidget(toolbar)
    toolbar.tool_selected.connect(view.set_active_tool)
    monkeypatch.setattr(reference_points, "point_dialog", lambda *_args: ("Poteau", None))
    view.resize(900, 600)
    view.show()
    view.centerOn(500, 500)
    button = toolbar._buttons[ToolType.REFERENCE_POINT]
    assert not button.icon().isNull()
    button.click()
    position = view.mapToScene(view.viewport().rect().center())
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.mapFromScene(position))
    points = [obj for obj in scene.items() if is_reference_point(obj)]
    assert len(points) == 1
    assert view._tool_manager.active_tool_type == ToolType.SELECT


def test_only_exact_centre_has_geometric_meaning(canvas, monkeypatch):
    scene, view = canvas
    point = add_point(monkeypatch, scene, view)
    assert point._compute_area_cm2() is None
    for provider in (NearestSnapProvider(), PerpendicularSnapProvider(), TangentSnapProvider()):
        assert not list(provider.candidates(QPointF(248, 180), [point], 25, QPointF(300, 180)))


@pytest.mark.parametrize("state", ["hidden", "locked", "grouped"])
def test_current_parent_survives_name_edit(canvas, monkeypatch, state):
    scene, view = canvas
    parent = PolygonItem([QPointF(0, 0), QPointF(500, 0), QPointF(500, 500)])
    scene.addItem(parent)
    with monkeypatch.context() as patch:
        point = add_point(patch, scene, view, parent)
    if state == "hidden":
        parent.setVisible(False)
    elif state == "locked":
        parent.setFlag(parent.GraphicsItemFlag.ItemIsSelectable, False)
    else:
        other = CircleItem(500, 500, 10)
        scene.addItem(other)
        view.command_manager.execute(GroupCommand(scene, [parent, other]))

    def accept(dialog):
        assert dialog.findChild(QComboBox).currentData() == parent.item_id
        dialog.findChild(QLineEdit).setText("Borne renommée")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", accept)
    reference_points.edit_reference_point(point)
    assert point.parent_bed_id == parent.item_id
    assert point.name == "Borne renommée"


def test_api_translation_includes_polygon_references(canvas, monkeypatch):
    from types import SimpleNamespace

    from open_garden_planner.app.application import GardenPlannerApp

    scene, view = canvas
    parent = PolygonItem([QPointF(0, 0), QPointF(500, 0), QPointF(500, 500)])
    scene.addItem(parent)
    point = add_point(monkeypatch, scene, view, parent)
    delta = QPointF(20, 30)
    pairs = GardenPlannerApp._agent_move_item_deltas(SimpleNamespace(canvas_scene=scene), parent, delta)
    assert pairs == [(parent, delta), (point, delta)]
