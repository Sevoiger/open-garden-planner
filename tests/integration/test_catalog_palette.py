"""Catalog → toolbar selection/drop → real creation, undo and saved identity."""

import json
from copy import deepcopy

import pytest
from PyQt6.QtCore import QPointF, QRect, Qt
from PyQt6.QtGui import QDropEvent, QFont, QMouseEvent
from PyQt6.QtWidgets import QApplication, QLabel, QWidget

from open_garden_planner.core.object_types import ObjectType
from open_garden_planner.core.plant_sizing import catalog_footprint_diameter_cm
from open_garden_planner.core.project import ProjectManager
from open_garden_planner.core.tools import ToolType
from open_garden_planner.models.plant_data import PermacultureLayer, PlantSpeciesData, species_key
from open_garden_planner.services.plant_library import get_plant_library
from open_garden_planner.services.plant_palette import (
    illustration_for,
    illustrations,
    palette_group,
    validate_catalog_snapshot,
)
from open_garden_planner.services.species_catalog import offline_catalog
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.canvas.canvas_view import CanvasView
from open_garden_planner.ui.canvas.items import CircleItem, RectangleItem
from open_garden_planner.ui.theme import ThemeMode, apply_theme
from open_garden_planner.ui.widgets.catalog_palette import PAGE_SIZE, catalog_item, gallery_mime
from open_garden_planner.ui.widgets.category_dropdown import CategoryDropdown
from open_garden_planner.ui.widgets.gallery_data import build_toolbar_categories
from open_garden_planner.ui.widgets.global_search import GlobalSearchField


def sophora():
    return PlantSpeciesData(scientific_name="Sophora prostrata", common_name="Sophora prostrata",
                            source_id="test:prostrata", data_source="custom", max_spread_cm=140,
                            raw_data={"cultivar": None, "provenance": "local test reference"})


def plants(scene):
    return [i for i in scene.items() if isinstance(i, CircleItem)]


def test_catalog_default_requires_size_and_preserves_botanical_spread(qtbot, monkeypatch):
    from open_garden_planner.app.application import GardenPlannerApp

    monkeypatch.setattr(GardenPlannerApp, "_startup_sequence", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_start_update_check", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_maybe_start_agent_api", lambda _self: None)
    get_plant_library().add_plant(sophora())
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    window.canvas_view.set_snap_enabled(False)
    dropdown = next(d for d in window.category_toolbar._category_dropdowns if d._catalog_group == "shrub")
    anchor = QWidget()
    qtbot.addWidget(anchor)
    anchor.show()
    dropdown.show_below(anchor)
    dropdown._search_box.setText("Sophora prostrata")
    assert dropdown._choose_size.isChecked()
    dropdown._catalog_list._choose(dropdown._catalog_list.item(0))
    tool = window.canvas_view.active_tool
    tool.commit_typed_coordinate(QPointF(300, 300))
    assert not plants(window.canvas_scene)
    assert window.canvas_view.command_manager.undo_depth == 0
    tool.commit_typed_coordinate(QPointF(390, 300))
    plant = plants(window.canvas_scene)[0]
    assert plant.radius == 90
    assert plant.metadata["plant_species"]["max_spread_cm"] == 140
    assert window.canvas_view.command_manager.undo_depth == 1
    window.canvas_view.command_manager.undo()
    assert not plants(window.canvas_scene)
    window.canvas_view.command_manager.redo()
    assert plants(window.canvas_scene)[0] is plant
    window._project_manager.mark_clean()
    window.close()


def test_cultivar_illustration_uses_parent_or_explicit_approximation_without_changing_identity():
    record = {"scientific_name": "Magnolia grandiflora Treyve", "raw_data": {}}
    before = deepcopy(record)
    assert illustration_for(record)["id"] == "magnolia"

    assert record == before
    record["raw_data"]["plan_illustration_id"] = "lily"
    assert illustration_for(record)["id"] == "lily"
    record["raw_data"]["plan_illustration_id"] = "../../outside"
    assert illustration_for(record)["id"] == "magnolia"


def test_plant_list_drop_uses_assigned_illustration_and_keeps_identity(canvas, monkeypatch):
    from open_garden_planner.models.plant_data import PlantSpeciesData
    from open_garden_planner.models.plant_lists import get_plant_list_store

    plant = PlantSpeciesData(scientific_name="Callicarpa bodinieri Profusion",
                             common_name="Callicarpe Profusion", data_source="custom",
                             source_id="test:callicarpa", max_height_cm=250, max_spread_cm=180,
                             raw_data={"plan_illustration_id": "viburnum", "future_field": True})
    store = get_plant_list_store()
    collection = store.create_list("My garden")
    entry = store.add_entry(collection.id, plant)
    from PyQt6.QtCore import QMimeData

    mime = QMimeData()
    mime.setText(f"plant_list:{entry.id}")
    event = QDropEvent(QPointF(canvas.mapFromScene(QPointF(300, 300))), Qt.DropAction.CopyAction,
                       mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.dropEvent(event)
    item = plants(canvas.scene())[0]
    assert item.plant_species == plant.common_name
    assert item.metadata["plant_species"]["scientific_name"] == plant.scientific_name
    assert item.metadata["plant_species"]["raw_data"] == plant.raw_data
    from PyQt6.QtGui import QImage, QPainter

    import open_garden_planner.ui.canvas.items.circle_item as circle_module

    render = circle_module.render_plant_pixmap
    rendered_species = []

    def render_symbol(**kwargs):
        rendered_species.append(kwargs["species"])
        return render(**kwargs)

    monkeypatch.setattr(circle_module, "render_plant_pixmap", render_symbol)
    image = QImage(400, 400, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    item.paint(painter, None)
    painter.end()
    assert rendered_species == ["viburnum"]


def test_plant_palette_keeps_illustrations_visible_and_can_hide_them(qtbot):
    category = next(c for c in build_toolbar_categories() if c.icon_name == "shrub")
    dropdown = CategoryDropdown(category)
    anchor = QWidget()
    qtbot.addWidget(anchor)
    qtbot.addWidget(dropdown)
    anchor.show()
    dropdown.show_below(anchor)
    assert dropdown._catalog_list.isVisible()
    assert dropdown._grid_widget.isVisible()
    dropdown._illustrations_toggle.click()
    assert dropdown._grid_widget.isHidden()
    dropdown.close()
    dropdown.show_below(anchor)
    assert dropdown._grid_widget.isVisible()


@pytest.fixture
def canvas(qtbot):
    scene = CanvasScene(width_cm=2000, height_cm=1500)
    view = CanvasView(scene)
    view.set_snap_enabled(False)
    qtbot.addWidget(view)
    return view


def test_catalog_selection_preserves_reference_and_places_once_with_undo_and_reload(qtbot, monkeypatch, tmp_path):
    from open_garden_planner.app.application import GardenPlannerApp

    monkeypatch.setattr(GardenPlannerApp, "_startup_sequence", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_start_update_check", lambda _self: None)
    monkeypatch.setattr(GardenPlannerApp, "_maybe_start_agent_api", lambda _self: None)
    get_plant_library().add_plant(sophora())
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    view = window.canvas_view
    view.set_snap_enabled(False)
    dropdown = next(d for d in window.category_toolbar._category_dropdowns if d._category.icon_name == "shrub")
    anchor = QWidget()
    qtbot.addWidget(anchor)
    anchor.show()
    dropdown.show_below(anchor)
    dropdown._choose_size.setChecked(False)
    dropdown._search_box.setText("Sophora prostrata")
    assert dropdown._catalog_list.count() == 1
    row = dropdown._catalog_list.item(0)
    selected = row.data(Qt.ItemDataRole.UserRole)
    expected = deepcopy(selected.species_data)
    dropdown._catalog_list._choose(row)
    assert view.active_tool.tool_type == ToolType.SHRUB
    event = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(0, 0),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    tool = view.active_tool
    assert tool.mouse_press(event, QPointF(250, 350))
    plant = plants(window.canvas_scene)[0]
    assert plant.center == QPointF(250, 350)
    assert plant.radius == 70
    assert plant.plant_species == "sophora prostrata"
    assert plant.metadata["plant_species"] == expected
    identity = plant.item_id
    view.command_manager.undo()
    assert not plants(window.canvas_scene)
    view.command_manager.redo()
    assert plants(window.canvas_scene)[0].item_id == identity
    path = tmp_path / "catalog.ogp"
    window._project_manager.save(window.canvas_scene, path)
    scene, manager = CanvasScene(), ProjectManager()
    manager.load(scene, path)
    restored = plants(scene)[0]
    assert restored.metadata["plant_species"] == expected
    assert restored.item_id == identity and restored.radius == 70
    window._project_manager.mark_clean()
    window.close()


def test_drag_keeps_snapshot_auto_parenting_and_one_undo(canvas):
    bed = RectangleItem(100, 100, 400, 400, object_type=ObjectType.GARDEN_BED)
    canvas.scene().addItem(bed)
    record = sophora().to_dict()
    item = catalog_item(record)
    mime = gallery_mime(item)
    event = QDropEvent(QPointF(canvas.mapFromScene(QPointF(250, 250))), Qt.DropAction.CopyAction,
                       mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.dropEvent(event)
    plant = plants(canvas.scene())[0]
    assert plant.metadata["plant_species"] == record
    assert plant.parent_bed_id == bed.item_id and plant.item_id in bed.child_item_ids
    assert plant.radius == 70
    canvas.command_manager.undo()
    assert not plants(canvas.scene()) and not bed.child_item_ids
    canvas.command_manager.redo()
    assert plants(canvas.scene())[0].item_id == plant.item_id


def test_corrupt_catalog_drag_is_rejected_without_creating_a_plant(canvas):
    mime = gallery_mime(catalog_item(sophora().to_dict()))
    mime.setData("application/x-ogp-plant-data", b'{"scientific_name": null}')
    event = QDropEvent(QPointF(20, 20), Qt.DropAction.CopyAction, mime,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.dropEvent(event)
    assert not plants(canvas.scene())


@pytest.mark.parametrize("field,value", [
    ("max_spread_cm", float("nan")), ("max_height_cm", float("inf")),
    ("common_name", None), ("scientific_name", 123), ("source_id", {}),
    ("edible_parts", "leaves"), ("max_spread_cm", "140"),
    ("raw_data", {"nested": [float("nan")]}),
])
def test_invalid_catalog_drop_keeps_scene_and_history_unchanged(canvas, field, value):
    record = sophora().to_dict()
    record[field] = value
    mime = gallery_mime(catalog_item(sophora().to_dict()))
    mime.setData("application/x-ogp-plant-data", json.dumps(record).encode())
    before = list(canvas.scene().items())
    canvas.set_active_tool(ToolType.SELECT)
    event = QDropEvent(QPointF(20, 20), Qt.DropAction.CopyAction, mime,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.dropEvent(event)
    assert list(canvas.scene().items()) == before
    assert not canvas.command_manager.can_undo
    assert canvas.active_tool.tool_type == ToolType.SELECT


def test_bounded_pages_search_entire_catalog_and_refresh_on_open(qtbot, monkeypatch):
    records = {str(i): PlantSpeciesData(scientific_name=f"Example {i:05}", common_name=f"Arbre {i:05}",
                source_id=str(i), data_source="custom", permaculture_layer=PermacultureLayer.CANOPY).to_dict()
               for i in range(1000)}
    monkeypatch.setattr("open_garden_planner.services.species_catalog.offline_catalog", lambda: records)
    category = next(c for c in build_toolbar_categories() if c.icon_name == "tree")
    dropdown, anchor = CategoryDropdown(category), QWidget()
    qtbot.addWidget(dropdown)
    qtbot.addWidget(anchor)
    anchor.show()
    dropdown.show_below(anchor)
    assert dropdown._catalog_list.count() == PAGE_SIZE
    assert dropdown._catalog_list.item(0).text() == "Arbre 00000\nExample 00000"
    dropdown._next_page.click()
    assert dropdown._catalog_list.count() == PAGE_SIZE
    assert dropdown._catalog_list.item(0).text().startswith("Arbre 00048")
    dropdown._previous_page.click()
    assert dropdown._catalog_list.item(0).text().startswith("Arbre 00000")
    dropdown._search_box.setText("00999")
    assert dropdown._catalog_list.count() == 1
    assert not dropdown._next_page.isEnabled()
    records.clear()
    dropdown.close()
    dropdown.show_below(anchor)
    assert dropdown._catalog_list.count() == 0


def test_unknown_growth_form_is_accessible_without_inventing_traits(qtbot):
    unknown = PlantSpeciesData(scientific_name="Example incognita", common_name="Inconnue", max_height_cm=1200)
    assert palette_group(unknown.to_dict()) is None
    get_plant_library().add_plant(unknown)
    dropdown = CategoryDropdown(next(c for c in build_toolbar_categories() if c.icon_name == "tree"))
    qtbot.addWidget(dropdown)
    dropdown._catalog_records = offline_catalog()
    dropdown._search_box.setText("incognita")
    assert dropdown._catalog_list.count() == 0
    dropdown._all_plants.setChecked(True)
    assert dropdown._catalog_list.count() == 1
    item = dropdown._catalog_list.item(0).data(Qt.ItemDataRole.UserRole)
    assert item.tool_type == ToolType.TREE
    assert item.species_data["permaculture_layer"] == "unknown"
    assert item.species == ""  # no accidental partial-name artwork match


def test_global_search_finds_new_local_reference_with_full_identity(qtbot):
    get_plant_library().add_plant(sophora())
    field = GlobalSearchField(build_toolbar_categories())
    qtbot.addWidget(field)
    received = []
    field.item_selected.connect(received.append)
    field.setText("Sophora prostrata")
    field._popup.activate_current()
    assert len(received) == 1
    assert species_key(received[0].species_data) == species_key(sophora().to_dict())


@pytest.mark.parametrize("exact_field", ["common_name", "scientific_name", "alias"])
def test_global_search_prioritizes_exact_record_before_limit(qtbot, monkeypatch, exact_field):
    records = {str(i): PlantSpeciesData(scientific_name=f"Rosa example{i}", common_name=f"A matching Rose {i}").to_dict()
               for i in range(15)}
    exact = PlantSpeciesData(scientific_name="Rosa exacta", common_name="Z reference").to_dict()
    if exact_field == "alias":
        exact["aliases"] = ["Rose"]
    else:
        exact[exact_field] = "Rose"
    records["exact"] = exact
    monkeypatch.setattr("open_garden_planner.services.species_catalog.offline_catalog", lambda: records)
    field = GlobalSearchField(build_toolbar_categories())
    qtbot.addWidget(field)
    field.setText("rose")
    assert field._popup._list.count() == 12
    assert field._popup._list.item(0).data(Qt.ItemDataRole.UserRole).species_data is exact


def test_exact_object_tool_remains_reachable_among_botanical_substrings(qtbot, monkeypatch):
    records = {str(i): PlantSpeciesData(scientific_name=f"Example {i}", common_name=f"Garden Bed {i}").to_dict()
               for i in range(15)}
    monkeypatch.setattr("open_garden_planner.services.species_catalog.offline_catalog", lambda: records)
    field = GlobalSearchField(build_toolbar_categories())
    qtbot.addWidget(field)
    field.setText("Garden Bed")
    first = field._popup._list.item(0).data(Qt.ItemDataRole.UserRole)
    assert first.tool_type == ToolType.GARDEN_BED


def test_database_shortcut_uses_query_and_activates_placement_without_editing_selection(qtbot, monkeypatch):
    from open_garden_planner.app.application import GardenPlannerApp
    from open_garden_planner.ui.dialogs import PlantSearchDialog

    for name in ("_startup_sequence", "_start_update_check", "_maybe_start_agent_api"):
        monkeypatch.setattr(GardenPlannerApp, name, lambda _self: None)
    seen = []

    def accept_reference(dialog):
        seen.append(dialog.search_input.text())
        dialog._selected_plant = sophora()
        dialog.accept()
        return 1

    monkeypatch.setattr(PlantSearchDialog, "exec", accept_reference)
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    original = CircleItem(200, 200, 20, object_type=ObjectType.SHRUB)
    window.canvas_scene.addItem(original)
    original.setSelected(True)
    before = deepcopy(original.metadata)
    dropdown = next(d for d in window.category_toolbar._category_dropdowns if d._catalog_group == "shrub")
    dropdown._search_box.setText("prostrata")
    dropdown._database_button.click()
    assert seen == ["prostrata"]
    assert original.metadata == before
    tool = window.canvas_view.active_tool
    assert tool.tool_type == ToolType.SHRUB
    tool.commit_typed_coordinate(QPointF(450, 450))
    assert len(plants(window.canvas_scene)) == 1
    tool.commit_typed_coordinate(QPointF(520, 450))
    created = next(p for p in plants(window.canvas_scene) if p is not original)
    assert species_key(created.metadata["plant_species"]) == species_key(sophora().to_dict())
    assert created.radius == 70
    window._project_manager.mark_clean()
    window.close()


def test_every_illustration_is_exposed_and_exact_renderer_aliases_exist(qtbot):
    from open_garden_planner.core.plant_renderer import _resolve_svg_path

    exposed = {i.species for c in build_toolbar_categories() for i in c.items if i.species}
    assert len(illustrations()) == 246
    for art in illustrations():
        name = art["id"].replace("_", " ")
        assert name in exposed
        assert _resolve_svg_path(ObjectType.PERENNIAL, name).stem == art["id"]


@pytest.mark.parametrize("spread, expected", [(None, 100), (True, 100), (-2, 100),
    (float("nan"), 100), (float("inf"), 100), (0, 100), (2, 100), (140, 140), (10000, 5000)])
def test_catalog_footprint_bound_does_not_mutate_data(spread, expected):
    record = {"max_spread_cm": spread}
    assert catalog_footprint_diameter_cm(record, "SHRUB") == expected
    assert record["max_spread_cm"] is spread


def test_art_is_a_convention_not_a_new_species_or_cultivar():
    record = sophora().to_dict()
    before = json.dumps(record, sort_keys=True)
    assert illustration_for(record)["id"] == "sophora_prostrata"
    assert palette_group(record) == "shrub"
    assert json.dumps(record, sort_keys=True) == before


def test_snapshot_validation_keeps_search_aliases_and_additive_source_fields():
    record = sophora().to_dict()
    record.update(aliases=["Alias conservé"], common_name_de="Unverändert", extra_source={"value": 17})
    before = deepcopy(record)
    assert validate_catalog_snapshot(record) == before
    assert record == before


def test_invalid_click_selection_cancels_tool_without_adding_history(qtbot, monkeypatch):
    from open_garden_planner.app.application import GardenPlannerApp

    for name in ("_startup_sequence", "_start_update_check", "_maybe_start_agent_api"):
        monkeypatch.setattr(GardenPlannerApp, name, lambda _self: None)
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    item = catalog_item(sophora().to_dict())
    item.species_data["max_spread_cm"] = float("nan")
    window.canvas_view.set_active_tool(ToolType.SHRUB)
    window._on_gallery_item_selected(item)
    assert window.canvas_view.active_tool.tool_type == ToolType.SELECT
    assert not window.canvas_view.command_manager.can_undo
    assert not plants(window.canvas_scene)
    window._project_manager.mark_clean()
    window.close()


@pytest.mark.parametrize("font_size,language", [(13, "en"), (18, "de")])
@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
def test_all_illustration_titles_fit_complete_words_and_wrapped_lines(qtbot, font_size, language, mode):
    from open_garden_planner.core.i18n import load_translator

    app = QApplication.instance()
    original_font = QFont(app.font())
    anchor = QWidget()
    qtbot.addWidget(anchor)
    try:
        app.setFont(QFont("Helvetica", font_size))
        apply_theme(app, mode)
        load_translator(app, language)
        anchor.show()
        for category in build_toolbar_categories():
            dropdown = CategoryDropdown(category)
            qtbot.addWidget(dropdown)
            dropdown.show_below(anchor)
            if dropdown._illustrations_toggle is not None:
                dropdown._illustrations_toggle.setChecked(True)
            app.processEvents()
            for button in dropdown._buttons:
                label = next(label for label in button.findChildren(QLabel) if label.text() == button.item.name)
                content = label.contentsRect()
                metrics = label.fontMetrics()
                assert max(metrics.horizontalAdvance(w) for w in label.text().split()) <= content.width(), label.text()
                required = metrics.boundingRect(QRect(0, 0, content.width(), 10000),
                    Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, label.text())
                assert required.height() <= content.height(), (label.text(), required, content)
                assert button.rect().contains(label.geometry()), (label.text(), button.rect(), label.geometry())
            dropdown.close()
    finally:
        app.setFont(original_font)
        load_translator(app, "en")
        apply_theme(app, ThemeMode.LIGHT)
