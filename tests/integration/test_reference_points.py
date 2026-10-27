"""Reference centres survive their complete editing and persistence lifecycle."""

import json

import pytest
from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QAction, QContextMenuEvent, QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSpinBox,
)

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
    monkeypatch.setattr(reference_points, "point_dialog", lambda *_args: reference_points.PointProperties(
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
    monkeypatch.setattr(reference_points, "point_dialog", lambda *_args: reference_points.PointProperties("Poteau 1"))
    reference_points.edit_reference_point(point)
    assert point.name == "Poteau 1"
    assert point.parent_bed_id is None
    assert point.item_id not in parent.child_item_ids
    view.command_manager.undo()
    assert point.name == "Borne OGE"
    assert point.parent_bed_id == parent.item_id


def test_dialog_does_not_attach_to_previous_selection_and_cancel_is_inert(canvas, monkeypatch):
    scene, view = canvas
    parent = CircleItem(250, 250, 100, name="Clôture")
    scene.addItem(parent)
    parent.setSelected(True)

    def accept(dialog):
        assert dialog.findChild(QComboBox).currentData() is None
        dialog.findChild(QLineEdit).setText("Poteau nord")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", accept)
    result = reference_points.point_dialog(view)
    assert result.name == "Poteau nord"
    assert result.parent_id is None
    monkeypatch.setattr(QDialog, "exec", lambda _dialog: QDialog.DialogCode.Rejected)
    assert not reference_points.place_reference_point(view, QPointF(250, 250))
    assert not view.command_manager.can_undo


def test_toolbar_placement_through_viewport(canvas, qtbot, monkeypatch):
    scene, view = canvas
    toolbar = MainToolbar(view)
    qtbot.addWidget(toolbar)
    toolbar.tool_selected.connect(view.set_active_tool)
    monkeypatch.setattr(reference_points, "point_dialog", lambda *_args: reference_points.PointProperties("Poteau"))
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


@pytest.mark.parametrize("zoom", [0.2, 0.65, 2.0])
@pytest.mark.parametrize("target", ["halo", "label"])
@pytest.mark.parametrize("attached", [False, True])
def test_drag_reference_through_viewport(canvas, qtbot, monkeypatch, zoom, target, attached):
    scene, view = canvas
    parent = PolygonItem([QPointF(100, 100), QPointF(500, 100), QPointF(500, 500)])
    scene.addItem(parent)
    point = add_point(monkeypatch, scene, view, parent if attached else None)
    view.resize(850, 600)
    view._zoom_factor = zoom
    view._apply_transform()
    view.centerOn(point.pos())
    view._object_snap_enabled = False
    view._guides_visible = False
    view.show()
    qtbot.wait(15)
    start = view.mapFromScene(point.pos())
    if target == "label":
        label = point._label_item
        start = label.deviceTransform(view.viewportTransform()).map(label.boundingRect().center()).toPoint()
    else:
        start += QPoint(10, 0)
    end = start + QPoint(50, -30)
    expected = view.mapToScene(end) - view.mapToScene(start)
    original = QPointF(point.pos())
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    event = QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(end),
                        QPointF(view.viewport().mapToGlobal(end)), Qt.MouseButton.NoButton,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(view.viewport(), event)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert point.pos().x() == pytest.approx((original + expected).x(), abs=1)
    assert point.pos().y() == pytest.approx((original + expected).y(), abs=1)
    assert parent.pos() == QPointF()
    view.command_manager.undo()
    assert point.pos() == original


def test_edit_appearance_keeps_exact_centre_and_roundtrips(canvas, qtbot, monkeypatch, tmp_path):
    scene, view = canvas
    point = add_point(monkeypatch, scene, view)
    point.setPos(341.5, 227.25)
    original = reference_points.PointProperties.from_item(point)
    changed = reference_points.PointProperties("Borne rouge", None, 36, "#ffd5283a", 4)
    monkeypatch.setattr(reference_points, "point_dialog", lambda *_args: changed)
    reference_points.edit_reference_point(point)
    assert point.mapToScene(point.rect().center()) == QPointF(341.5, 227.25)
    assert reference_points.PointProperties.from_item(point) == changed
    view.command_manager.undo()
    assert reference_points.PointProperties.from_item(point) == original
    view.command_manager.redo()
    assert reference_points.PointProperties.from_item(point) == changed
    path = tmp_path / "point-style.ogp"
    ProjectManager().save(scene, path)
    other = CanvasScene()
    other_view = CanvasView(other)
    qtbot.addWidget(other_view)
    ProjectManager().load(other, path)
    restored = other.find_item_by_id(point.item_id)
    assert reference_points.PointProperties.from_item(restored) == changed
    assert restored.mapToScene(restored.rect().center()) == QPointF(341.5, 227.25)


def test_dialog_appearance_and_attachment_preview(canvas, monkeypatch):
    scene, view = canvas
    parent = PolygonItem([QPointF(100, 100), QPointF(500, 100), QPointF(500, 500)], name="Clôture")
    scene.addItem(parent)
    parent.setSelected(True)
    before = set(scene.items())

    def accept(dialog):
        from PyQt6.QtGui import QColor

        from open_garden_planner.ui.dialogs.properties_dialog import ColorButton

        parents = dialog.findChild(QComboBox)
        assert parents.currentData() is None
        parents.setCurrentIndex(parents.findData(parent.item_id))
        assert any("Clôture" in label.text() for label in dialog.findChildren(QLabel))
        assert set(scene.items()) - before
        dialog.findChild(QSpinBox, "reference_point_size").setValue(32)
        dialog.findChild(QDoubleSpinBox, "reference_point_width").setValue(3.5)
        dialog.findChild(ColorButton, "reference_point_color").set_color(QColor("#ff8800"))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", accept)
    result = reference_points.point_dialog(view, None, QPointF(300, 200))
    assert result == reference_points.PointProperties("", parent.item_id, 32, "#ffff8800", 3.5)
    assert set(scene.items()) == before


def test_reference_properties_panel_exposes_edit_and_link(canvas, qtbot, monkeypatch):
    from open_garden_planner.ui.panels.properties_panel import PropertiesPanel

    scene, view = canvas
    parent = PolygonItem([QPointF(100, 100), QPointF(500, 100), QPointF(500, 500)], name="Clôture")
    scene.addItem(parent)
    point = add_point(monkeypatch, scene, view, parent)
    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    panel.set_selected_items([point])
    assert any("Clôture" in label.text() for label in panel.findChildren(QLabel))
    assert any("Edit reference point" in button.text() for button in panel.findChildren(QPushButton))


def test_constraint_dimensions_toggle_keeps_constraints_and_tool(canvas, qtbot):
    from types import SimpleNamespace

    from open_garden_planner.app.application import GardenPlannerApp
    from open_garden_planner.app.settings import get_settings
    from open_garden_planner.core.constraints import AnchorRef

    scene, view = canvas
    parent = PolygonItem([QPointF(100, 100), QPointF(500, 100), QPointF(500, 500)])
    scene.addItem(parent)
    a = AnchorRef(parent.item_id, AnchorType.CORNER, 0)
    b = AnchorRef(parent.item_id, AnchorType.CORNER, 1)
    constraint = scene.constraint_graph.add_constraint(a, b, 400)
    scene.update_dimension_lines()
    toolbar = MainToolbar(view)
    qtbot.addWidget(toolbar)
    action = QAction("Dimensions", toolbar)
    action.setCheckable(True)
    action.setChecked(True)
    action.triggered.connect(lambda checked: GardenPlannerApp._on_toggle_constraints(
        SimpleNamespace(canvas_view=view), checked))
    toolbar.set_constraints_action(action)
    view.set_active_tool(ToolType.MEASURE)
    toolbar._constraints_button.click()
    assert not scene.constraints_visible
    assert not get_settings().show_constraints
    assert constraint.constraint_id in scene.constraint_graph.constraints
    assert all(not item.isVisible() for group in scene.dimension_line_manager._groups.values() for item in group.items)
    assert view._tool_manager.active_tool_type == ToolType.MEASURE
    toolbar._constraints_button.click()
    assert scene.constraints_visible
    assert get_settings().show_constraints
    assert scene.dimension_line_manager._groups[constraint.constraint_id].items


@pytest.mark.parametrize("protection", ["locked", "fixed"])
def test_reference_label_respects_movement_protection(canvas, qtbot, monkeypatch, protection):
    from open_garden_planner.core.constraints import AnchorRef, ConstraintType

    scene, view = canvas
    point = add_point(monkeypatch, scene, view)
    original = QPointF(point.pos())
    if protection == "locked":
        point.setFlag(point.GraphicsItemFlag.ItemIsSelectable, False)
        point.setFlag(point.GraphicsItemFlag.ItemIsMovable, False)
    else:
        anchor = AnchorRef(point.item_id, AnchorType.CENTER)
        scene.constraint_graph.add_constraint(anchor, anchor, 0, constraint_type=ConstraintType.FIXED,
                                             target_x=original.x(), target_y=original.y())
    view.resize(850, 600)
    view._zoom_factor = 0.65
    view._apply_transform()
    view.centerOn(original)
    view._object_snap_enabled = False
    view._guides_visible = False
    view.show()
    qtbot.wait(15)
    label = point._label_item
    start = label.deviceTransform(view.viewportTransform()).map(label.boundingRect().center()).toPoint()
    end = start + QPoint(50, -30)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QApplication.sendEvent(view.viewport(), QMouseEvent(
        QMouseEvent.Type.MouseMove, QPointF(end), QPointF(view.viewport().mapToGlobal(end)),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert point.pos() == original
    if protection == "locked":
        edits = []
        monkeypatch.setattr(reference_points, "edit_reference_point", edits.append)
        QTest.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        assert not edits


@pytest.mark.parametrize("target", ["point", "label"])
@pytest.mark.parametrize("lose_grab", [False, True])
def test_continuous_reference_drag_with_default_snapping(canvas, qtbot, monkeypatch, target, lose_grab):
    scene, view = canvas
    terrain = PolygonItem([QPointF(0, 0), QPointF(2000, 0), QPointF(2000, 1200), QPointF(0, 1200)])
    scene.addItem(terrain)
    point = add_point(monkeypatch, scene, view)
    point.setPos(500, 400)
    view.resize(850, 600)
    view._zoom_factor = 0.4
    view._apply_transform()
    view.centerOn(point.pos())
    view.show()
    qtbot.wait(40)
    start = view.mapFromScene(point.pos())
    if target == "label":
        label = point._label_item
        start = label.deviceTransform(view.viewportTransform()).map(label.boundingRect().center()).toPoint()
    original = QPointF(point.pos())
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    qtbot.wait(30)
    trace = []
    for offset in (QPoint(3, -1), QPoint(8, -3), QPoint(15, -7), QPoint(31, -16), QPoint(53, -29)):
        end = start + offset
        QApplication.sendEvent(view.viewport(), QMouseEvent(
            QMouseEvent.Type.MouseMove, QPointF(end), QPointF(view.viewport().mapToGlobal(end)),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        qtbot.wait(30)
        trace.append((point.pos().x(), point.pos().y(), type(scene.mouseGrabberItem()).__name__))
        if lose_grab and scene.mouseGrabberItem() is not None:
            scene.mouseGrabberItem().ungrabMouse()
    expected = original + view.mapToScene(end) - view.mapToScene(start)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert point.pos().x() == pytest.approx(expected.x(), abs=1), trace
    assert point.pos().y() == pytest.approx(expected.y(), abs=1), trace
    assert terrain.pos() == QPointF()


@pytest.mark.parametrize("attached", [False, True])
@pytest.mark.parametrize("offset,expected_xy", [
    (QPoint(42, -42), (584, 584)),
    (QPoint(50, -50), (600, 600)),
    (QPoint(49, -49), (600, 600)),
])
def test_reference_drag_snaps_only_its_centre_to_geometry(canvas, qtbot, monkeypatch, attached, offset, expected_xy):
    scene, view = canvas
    terrain = PolygonItem([QPointF(600, 600), QPointF(1000, 600), QPointF(1000, 1000)])
    scene.addItem(terrain)
    point = add_point(monkeypatch, scene, view, terrain if attached else None)
    point.setPos(500, 500)
    view.resize(850, 600)
    view._zoom_factor = 0.5
    view._apply_transform()
    view.centerOn(point.pos())
    view.show()
    qtbot.wait(20)
    start = view.mapFromScene(point.pos())
    end = start + offset
    expected = QPointF(*expected_xy)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QApplication.sendEvent(view.viewport(), QMouseEvent(
        QMouseEvent.Type.MouseMove, QPointF(end), QPointF(view.viewport().mapToGlobal(end)),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert point.pos().x() == pytest.approx(expected.x(), abs=0.01)
    assert point.pos().y() == pytest.approx(expected.y(), abs=0.01)


def test_reference_centre_can_reach_canvas_border(canvas, qtbot, monkeypatch):
    scene, view = canvas
    point = add_point(monkeypatch, scene, view)
    point.setPos(50, 500)
    view.resize(850, 600)
    view._zoom_factor = 1
    view._apply_transform()
    view.centerOn(point.pos())
    view.show()
    qtbot.wait(20)
    start = view.mapFromScene(point.pos())
    end = start + QPoint(-50, 0)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QApplication.sendEvent(view.viewport(), QMouseEvent(
        QMouseEvent.Type.MouseMove, QPointF(end), QPointF(view.viewport().mapToGlobal(end)),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert point.pos().x() == pytest.approx(0, abs=0.01)


def test_reference_as_snap_target_does_not_depend_on_symbol_size(canvas, monkeypatch):
    from open_garden_planner.core.snapping import ObjectSnapper

    scene, view = canvas
    point = add_point(monkeypatch, scene, view)
    point.setPos(500, 500)
    for radius in (8, 32):
        point.set_radius_centered(radius)
        result = ObjectSnapper().snap(QRectF(490, 490, 10, 10), [point])
        assert result.snapped_pos == QPointF()


def test_api_clamp_and_keyboard_bounds_use_reference_centre(canvas, monkeypatch):
    from types import SimpleNamespace

    from open_garden_planner.app.application import GardenPlannerApp

    scene, view = canvas
    point = add_point(monkeypatch, scene, view)
    point.setPos(0, 500)
    GardenPlannerApp._clamp_agent_items_to_canvas(SimpleNamespace(canvas_scene=scene), [point])
    assert point.pos() == QPointF(0, 500)
    assert view._clamp_delta_to_canvas([point], QPointF(-10, 0)) == QPointF()
    assert view._clamp_delta_to_canvas([point], QPointF(10, 0)) == QPointF(10, 0)


def test_placing_on_terrain_never_inherits_selected_pergola(canvas, monkeypatch):
    from open_garden_planner.core.object_types import ObjectType
    from open_garden_planner.ui.canvas.items import RectangleItem

    scene, view = canvas
    terrain = PolygonItem([QPointF(0, 0), QPointF(2000, 0), QPointF(2000, 1200), QPointF(0, 1200)])
    pergola = RectangleItem(1400, 700, 100, 100, object_type=ObjectType.PERGOLA, name="Pergola")
    scene.addItem(terrain)
    scene.addItem(pergola)
    pergola.setSelected(True)

    def accept(dialog):
        assert dialog.findChild(QComboBox).currentData() is None
        dialog.findChild(QLineEdit).setText("Borne terrain")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", accept)
    assert reference_points.place_reference_point(view, QPointF(300, 500))
    point = next(item for item in scene.items() if is_reference_point(item))
    assert point.parent_bed_id is None
    assert not pergola.child_item_ids
    assert not terrain.child_item_ids


@pytest.mark.parametrize("angle", [0, 31])
def test_boundary_click_attaches_to_clicked_polygon_with_contour_mode(canvas, monkeypatch, angle):
    from open_garden_planner.core.object_types import ObjectType
    from open_garden_planner.ui.canvas.items import RectangleItem

    scene, view = canvas
    terrain = PolygonItem([QPointF(200, 200), QPointF(800, 200), QPointF(800, 800), QPointF(200, 800)])
    terrain._apply_rotation(angle)
    pergola = RectangleItem(1400, 700, 100, 100, object_type=ObjectType.PERGOLA, name="Pergola")
    scene.addItem(terrain)
    scene.addItem(pergola)
    pergola.setSelected(True)
    click = terrain.mapToScene(QPointF(200, 510))

    def accept(dialog):
        assert dialog.findChild(QComboBox).currentData() == terrain.item_id
        assert dialog.findChild(QCheckBox, "reference_point_on_contour").isChecked()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", accept)
    assert reference_points.place_reference_point(view, click)
    point = next(item for item in scene.items() if is_reference_point(item))
    assert point.parent_bed_id == terrain.item_id
    assert point.metadata["reference_point_on_contour"] is True
    assert point.mapToScene(QPointF()) == click
    assert not pergola.child_item_ids


@pytest.mark.parametrize("angle", [0, 31])
@pytest.mark.parametrize("target", ["point", "label"])
def test_bound_point_slides_around_polygon_without_leaving_or_losing_grab(canvas, qtbot, monkeypatch,
                                                                      tmp_path, angle, target):
    scene, view = canvas
    terrain = PolygonItem([QPointF(200, 200), QPointF(800, 200), QPointF(800, 800), QPointF(200, 800)])
    terrain._apply_rotation(angle)
    scene.addItem(terrain)
    options = reference_points.PointProperties("Borne", terrain.item_id, on_contour=True)
    monkeypatch.setattr(reference_points, "point_dialog", lambda *_args: options)
    origin = terrain.mapToScene(QPointF(200, 500))
    assert reference_points.place_reference_point(view, origin)
    point = next(item for item in scene.items() if is_reference_point(item))
    view.resize(950, 750)
    view._zoom_factor = 0.7
    view._apply_transform()
    view.centerOn(terrain.sceneBoundingRect().center())
    view.show()
    qtbot.wait(20)
    start = view.mapFromScene(origin)
    if target == "label":
        label = point._label_item
        start = label.deviceTransform(view.viewportTransform()).map(label.boundingRect().center()).toPoint()
    press_offset = start - view.mapFromScene(origin)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    for local_target, axis, value in [(QPointF(350, 560), "x", 200), (QPointF(350, 850), "y", 800)]:
        if scene.mouseGrabberItem() is not None:
            scene.mouseGrabberItem().ungrabMouse()
        end = view.mapFromScene(terrain.mapToScene(local_target)) + press_offset
        QApplication.sendEvent(view.viewport(), QMouseEvent(
            QMouseEvent.Type.MouseMove, QPointF(end), QPointF(view.viewport().mapToGlobal(end)),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        qtbot.wait(20)
        actual_local = terrain.mapFromScene(point.mapToScene(QPointF()))
        assert getattr(actual_local, axis)() == pytest.approx(value, abs=1e-6)
    assert actual_local.x() == pytest.approx(350, abs=3)
    assert terrain.pos() == QPointF()
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    destination = QPointF(point.pos())
    view.command_manager.undo()
    assert point.pos() == origin
    view.command_manager.redo()
    assert point.pos() == destination
    path = tmp_path / "contour-point.ogp"
    ProjectManager().save(scene, path)
    other = CanvasScene()
    other_view = CanvasView(other)
    qtbot.addWidget(other_view)
    ProjectManager().load(other, path)
    restored = other.find_item_by_id(point.item_id)
    assert restored.metadata["reference_point_on_contour"] is True
    assert restored.parent_bed_id == terrain.item_id
    assert restored.pos() == destination


def test_edit_contour_mode_is_one_undo_including_projected_position(canvas, monkeypatch):
    scene, view = canvas
    terrain = PolygonItem([QPointF(200, 200), QPointF(800, 200), QPointF(800, 800), QPointF(200, 800)])
    scene.addItem(terrain)
    point = add_point(monkeypatch, scene, view, terrain)
    point.setPos(500, 500)
    options = reference_points.PointProperties("Borne", terrain.item_id, on_contour=True)
    monkeypatch.setattr(reference_points, "point_dialog", lambda *_args: options)
    reference_points.edit_reference_point(point)
    assert point.pos() == QPointF(500, 200)
    assert point.metadata["reference_point_on_contour"] is True
    view.command_manager.undo()
    assert point.pos() == QPointF(500, 500)
    assert point.metadata["reference_point_on_contour"] is False
    view.command_manager.redo()
    assert point.pos() == QPointF(500, 200)


def test_keyboard_cannot_pull_bound_point_off_outline(canvas, monkeypatch):
    scene, view = canvas
    terrain = PolygonItem([QPointF(200, 200), QPointF(800, 200), QPointF(800, 800), QPointF(200, 800)])
    scene.addItem(terrain)
    options = reference_points.PointProperties("Borne", terrain.item_id, on_contour=True)
    monkeypatch.setattr(reference_points, "point_dialog", lambda *_args: options)
    assert reference_points.place_reference_point(view, QPointF(200, 500))
    point = next(item for item in scene.items() if is_reference_point(item))
    QTest.keyClick(view.viewport(), Qt.Key.Key_Right)
    assert point.pos() == QPointF(200, 500)
    QTest.keyClick(view.viewport(), Qt.Key.Key_Up)
    assert point.pos() == QPointF(200, 550)
    view.command_manager.undo()
    assert point.pos() == QPointF(200, 500)


def test_keyboard_mixed_selection_keeps_contour_point_on_outline(canvas, monkeypatch):
    scene, view = canvas
    terrain = PolygonItem([QPointF(200, 200), QPointF(800, 200), QPointF(800, 800), QPointF(200, 800)])
    scene.addItem(terrain)
    options = reference_points.PointProperties("Borne", terrain.item_id, on_contour=True)
    monkeypatch.setattr(reference_points, "point_dialog", lambda *_args: options)
    assert reference_points.place_reference_point(view, QPointF(200, 500))
    point = next(item for item in scene.items() if is_reference_point(item))
    other = CircleItem(1100, 500, 20)
    scene.addItem(other)
    other.setSelected(True)
    QTest.keyClick(view.viewport(), Qt.Key.Key_Right)
    assert point.pos() == QPointF(200, 500)
    assert other.pos() == QPointF(50, 0)
    view.command_manager.undo()
    assert point.pos() == QPointF(200, 500)
    assert other.pos() == QPointF()


def test_contour_and_incompatible_distance_refuse_drag_without_breaking_either(canvas, qtbot, monkeypatch):
    from PyQt6.QtCore import QLineF

    from open_garden_planner.core.constraints import AnchorRef, ConstraintType

    scene, view = canvas
    terrain = PolygonItem([QPointF(200, 200), QPointF(800, 200), QPointF(800, 800), QPointF(200, 800)])
    scene.addItem(terrain)
    options = reference_points.PointProperties("Borne", terrain.item_id, on_contour=True)
    monkeypatch.setattr(reference_points, "point_dialog", lambda *_args: options)
    assert reference_points.place_reference_point(view, QPointF(200, 500))
    point = next(item for item in scene.items() if is_reference_point(item))
    fixed = CircleItem(500, 500, 20)
    scene.addItem(fixed)
    a, b = AnchorRef(point.item_id, AnchorType.CENTER), AnchorRef(fixed.item_id, AnchorType.CENTER)
    scene.constraint_graph.add_constraint(b, b, 0, constraint_type=ConstraintType.FIXED, target_x=0, target_y=0)
    scene.constraint_graph.add_constraint(a, b, 300)
    scene.set_constraints_visible(False)
    view.resize(950, 750)
    view._zoom_factor = 0.7
    view._apply_transform()
    view.centerOn(500, 500)
    view.show()
    qtbot.wait(20)
    start = view.mapFromScene(point.pos())
    end = start + QPoint(0, -35)
    before_stack = len(view.command_manager._undo_stack)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QApplication.sendEvent(view.viewport(), QMouseEvent(
        QMouseEvent.Type.MouseMove, QPointF(end), QPointF(view.viewport().mapToGlobal(end)),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert point.pos() == QPointF(200, 500)
    assert QLineF(point.pos(), fixed.mapToScene(fixed.rect().center())).length() == pytest.approx(300)
    assert fixed.pos() == QPointF()
    assert len(view.command_manager._undo_stack) == before_stack
    QTest.keyClick(view.viewport(), Qt.Key.Key_Up)
    assert point.pos() == QPointF(200, 500)
    assert len(view.command_manager._undo_stack) == before_stack


def test_keyboard_propagation_cannot_pull_unselected_point_off_contour(canvas, monkeypatch):
    from open_garden_planner.core.constraints import AnchorRef

    scene, view = canvas
    terrain = PolygonItem([QPointF(200, 200), QPointF(800, 200), QPointF(800, 800), QPointF(200, 800)])
    scene.addItem(terrain)
    options = reference_points.PointProperties("Borne", terrain.item_id, on_contour=True)
    monkeypatch.setattr(reference_points, "point_dialog", lambda *_args: options)
    assert reference_points.place_reference_point(view, QPointF(200, 500))
    point = next(item for item in scene.items() if is_reference_point(item))
    other = CircleItem(500, 500, 20)
    scene.addItem(other)
    scene.constraint_graph.add_constraint(AnchorRef(point.item_id, AnchorType.CENTER),
                                         AnchorRef(other.item_id, AnchorType.CENTER), 300)
    scene.clearSelection()
    other.setSelected(True)
    before = len(view.command_manager._undo_stack)
    QTest.keyClick(view.viewport(), Qt.Key.Key_Right)
    assert point.pos() == QPointF(200, 500)
    assert other.pos() == QPointF()
    assert len(view.command_manager._undo_stack) == before


def test_locked_terrain_can_anchor_a_movable_reference_on_editable_layer(canvas, monkeypatch):
    from open_garden_planner.models.layer import Layer

    scene, view = canvas
    ground = scene.active_layer
    terrain = PolygonItem([QPointF(200, 200), QPointF(800, 200), QPointF(800, 800), QPointF(200, 800)],
                          layer_id=ground.id)
    scene.addItem(terrain)
    scene.update_layer_lock(ground.id, True)
    editable = Layer(name="Repères", z_order=max(layer.z_order for layer in scene.layers) + 1)
    scene.add_layer(editable)
    scene.set_active_layer(editable)

    def accept(dialog):
        assert dialog.findChild(QComboBox).currentData() == terrain.item_id
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", accept)
    assert reference_points.place_reference_point(view, QPointF(200, 500))
    point = next(item for item in scene.items() if is_reference_point(item))
    assert point.parent_bed_id == terrain.item_id
    assert point.layer_id == editable.id
    assert point.flags() & point.GraphicsItemFlag.ItemIsMovable
    assert point.flags() & point.GraphicsItemFlag.ItemIsSelectable
    assert point.zValue() > terrain.zValue()
    QTest.keyClick(view.viewport(), Qt.Key.Key_Up)
    assert point.pos() == QPointF(200, 550)
    view.command_manager.undo()
    assert point.pos() == QPointF(200, 500)


def test_full_window_boundary_placement_and_prolonged_drags(qtbot, monkeypatch, tmp_path):
    from open_garden_planner.app.application import GardenPlannerApp

    window = GardenPlannerApp()
    qtbot.addWidget(window)
    view, scene = window.canvas_view, window.canvas_scene
    terrain = PolygonItem([QPointF(200, 200), QPointF(800, 200), QPointF(800, 800), QPointF(200, 800)],
                          name="Terrain")
    scene.addItem(terrain)
    window.resize(1450, 900)
    window.show()
    qtbot.wait(700)  # Let startup fit and panel layout settle before the gesture.
    view._zoom_factor = min(0.65, max(0.1, (view.viewport().width() - 100) / 650))
    view._apply_transform()
    view.centerOn(500, 500)
    qtbot.wait(100)
    monkeypatch.setattr(QDialog, "exec", lambda _dialog: QDialog.DialogCode.Accepted)
    try:
        for scene_position, attached in [(QPointF(200, 510), True), (QPointF(500, 500), False)]:
            QTest.mouseClick(window.main_toolbar._buttons[ToolType.REFERENCE_POINT], Qt.MouseButton.LeftButton)
            QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.mapFromScene(scene_position))
            created = [item for item in scene.items() if is_reference_point(item)]
            assert created, (view.mapFromScene(scene_position), view.viewport().rect(),
                             view._tool_manager.active_tool_type, window.statusBar().currentMessage(),
                             [(layer.name, layer.locked, layer.visible) for layer in scene.layers])
            point = next(item for item in scene.selectedItems() if is_reference_point(item))
            assert (point.parent_bed_id == terrain.item_id) == attached
            assert point.metadata["reference_point_on_contour"] is attached
            point.name = "Repère lié" if attached else "Repère indépendant"
            original = QPointF(point.pos())
            for target in ("point", "label"):
                start = view.mapFromScene(point.pos())
                if target == "label":
                    label = point._label_item
                    start = label.deviceTransform(view.viewportTransform()).map(label.boundingRect().center()).toPoint()
                QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
                for dx in range(2, 43, 2):
                    if scene.mouseGrabberItem() is not None:
                        scene.mouseGrabberItem().ungrabMouse()
                    end = start + QPoint(dx // 8 if attached else dx, -dx // 2)
                    QApplication.sendEvent(view.viewport(), QMouseEvent(
                        QMouseEvent.Type.MouseMove, QPointF(end), QPointF(view.viewport().mapToGlobal(end)),
                        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
                    qtbot.wait(20)
                    if attached:
                        assert point.pos().x() == pytest.approx(200)
                QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
                assert scene.mouseGrabberItem() is None, (attached, target, type(scene.mouseGrabberItem()).__name__)
                expected = original + view.mapToScene(end) - view.mapToScene(start)
                if attached:
                    assert point.pos().y() == pytest.approx(expected.y(), abs=1)
                else:
                    assert point.pos().x() == pytest.approx(expected.x(), abs=1)
                    assert point.pos().y() == pytest.approx(expected.y(), abs=1)
                view.command_manager.undo()
                assert point.pos() == original
        assert terrain.pos() == QPointF()
        window.grab().save(str(tmp_path / "reference-contour.png"))
    finally:
        window._project_manager.mark_clean()


@pytest.mark.parametrize("target", ["point", "label"])
def test_double_click_enabling_contour_keeps_intended_projection(canvas, qtbot, monkeypatch, target):
    scene, view = canvas
    terrain = PolygonItem([QPointF(200, 200), QPointF(800, 200), QPointF(800, 800), QPointF(200, 800)])
    scene.addItem(terrain)
    point = add_point(monkeypatch, scene, view)
    point.setPos(500, 500)
    options = reference_points.PointProperties("Borne", terrain.item_id, on_contour=True)
    monkeypatch.setattr(reference_points, "point_dialog", lambda *_args: options)
    view.resize(850, 650)
    view._zoom_factor = 0.6
    view._apply_transform()
    view.centerOn(point.pos())
    view.show()
    qtbot.wait(20)
    start = view.mapFromScene(point.pos())
    if target == "label":
        label = point._label_item
        start = label.deviceTransform(view.viewportTransform()).map(label.boundingRect().center()).toPoint()
    QTest.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    assert point.metadata["reference_point_on_contour"] is True
    assert point.pos() == QPointF(500, 200)
    view.command_manager.undo()
    assert point.pos() == QPointF(500, 500)


def test_reference_context_actions_lock_delete_and_restore_everything_once(canvas, monkeypatch):
    from open_garden_planner.core.constraints import ConstraintType

    scene, view = canvas
    terrain = PolygonItem([QPointF(0, 0), QPointF(600, 0), QPointF(600, 600)])
    scene.addItem(terrain)
    point = add_point(monkeypatch, scene, view, terrain)
    scene.clearSelection()
    terrain.setSelected(True)
    menu = reference_points.reference_context_menu(point)
    actions = {action.objectName(): action for action in menu.actions()}
    assert {"reference_edit", "reference_copy", "reference_duplicate", "reference_delete",
            "toggle_position_lock"} <= actions.keys()
    assert "split_shape" not in actions
    assert scene.selectedItems() == [point]
    actions["toggle_position_lock"].trigger()
    fixed = next(c for c in scene.constraint_graph.constraints.values() if c.constraint_type == ConstraintType.FIXED)
    assert fixed.anchor_a.item_id == point.item_id
    original = QPointF(point.pos())
    QTest.keyClick(view.viewport(), Qt.Key.Key_Right)
    assert point.pos() == original
    locked_menu = reference_points.reference_context_menu(point)
    unlock = next(a for a in locked_menu.actions() if a.objectName() == "toggle_position_lock")
    assert unlock.text() == "Unlock position"
    unlock.trigger()
    assert not scene.constraint_graph.constraints
    view.command_manager.undo()
    assert fixed.constraint_id in scene.constraint_graph.constraints
    before = len(view.command_manager._undo_stack)
    actions["reference_delete"].trigger()
    assert point.scene() is None
    assert terrain.scene() is scene
    assert not scene.constraint_graph.constraints
    assert point.item_id not in terrain.child_item_ids
    assert len(view.command_manager._undo_stack) == before + 1
    view.command_manager.undo()
    assert point.scene() is scene
    assert point.parent_bed_id == terrain.item_id
    assert point.item_id in terrain.child_item_ids
    assert fixed.constraint_id in scene.constraint_graph.constraints
    view.command_manager.redo()
    assert point.scene() is None
    assert not scene.constraint_graph.constraints


def test_reference_context_duplicate_and_layer_use_existing_undo(canvas, monkeypatch):
    from open_garden_planner.models.layer import Layer

    scene, view = canvas
    point = add_point(monkeypatch, scene, view)
    menu = reference_points.reference_context_menu(point)
    next(a for a in menu.actions() if a.objectName() == "reference_duplicate").trigger()
    assert len([it for it in scene.items() if is_reference_point(it)]) == 2
    view.command_manager.undo()
    assert len([it for it in scene.items() if is_reference_point(it)]) == 1
    old_layer = point.layer_id
    layer = Layer(name="Repères", z_order=5)
    scene.add_layer(layer)
    menu = reference_points.reference_context_menu(point)
    layer_menu = next(a.menu() for a in menu.actions() if a.menu() and a.menu().title() == "Move to Layer")
    next(a for a in layer_menu.actions() if a.data() == layer.id).trigger()
    assert point.layer_id == layer.id
    view.command_manager.undo()
    assert point.layer_id == old_layer


@pytest.mark.parametrize("target", ["point", "label"])
@pytest.mark.parametrize("zoom", [0.2, 0.9])
def test_reference_context_menu_opens_from_viewport(canvas, qtbot, monkeypatch, target, zoom):
    scene, view = canvas
    point = add_point(monkeypatch, scene, view)
    view.resize(850, 650)
    view._zoom_factor = zoom
    view._apply_transform()
    view.centerOn(point.pos())
    view.show()
    qtbot.wait(20)
    position = view.mapFromScene(point.pos())
    if target == "label":
        label = point._label_item
        position = label.deviceTransform(view.viewportTransform()).map(label.boundingRect().center()).toPoint()
    opened = []

    def capture(menu, *_args):
        opened.append({a.objectName() for a in menu.actions()})
        return None

    monkeypatch.setattr(QMenu, "exec", capture)
    QApplication.sendEvent(view.viewport(), QContextMenuEvent(QContextMenuEvent.Reason.Mouse, position,
                                                             view.viewport().mapToGlobal(position)))
    assert opened and "reference_delete" in opened[0]
    assert "toggle_position_lock" in opened[0]


def test_popup_reference_menu_can_lock_then_delete_with_one_undo(canvas, qtbot, monkeypatch, tmp_path):
    from open_garden_planner.core.constraints import ConstraintType

    scene, view = canvas
    point = add_point(monkeypatch, scene, view)
    view.resize(850, 650)
    view._zoom_factor = 0.5
    view._apply_transform()
    view.centerOn(point.pos())
    view.show()
    qtbot.wait(20)
    activated = []
    for name in ("toggle_position_lock", "reference_delete"):
        def choose(action_name=name):
            menu = QApplication.activePopupWidget()
            assert isinstance(menu, QMenu)
            menu.grab().save(str(tmp_path / f"{action_name}.png"))
            action = next(a for a in menu.actions() if a.objectName() == action_name)
            assert action.isEnabled()
            activated.append(action_name)
            action.trigger()
            menu.close()

        QTimer.singleShot(60, choose)
        position = view.mapFromScene(point.pos())
        QApplication.sendEvent(view.viewport(), QContextMenuEvent(QContextMenuEvent.Reason.Mouse, position,
                                                                 view.viewport().mapToGlobal(position)))
        if name == "toggle_position_lock":
            assert any(c.constraint_type == ConstraintType.FIXED for c in scene.constraint_graph.constraints.values())
    assert activated == ["toggle_position_lock", "reference_delete"]
    assert point.scene() is None
    assert not scene.constraint_graph.constraints
    view.command_manager.undo()
    assert point.scene() is scene
    assert any(c.constraint_type == ConstraintType.FIXED for c in scene.constraint_graph.constraints.values())
