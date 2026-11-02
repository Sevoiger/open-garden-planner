"""Filter controls and non-exclusive relations remain usable without a canvas selection."""
from open_garden_planner.core.commands import CommandManager
from open_garden_planner.core.plant_specimens import SpecimenEditCommand
from open_garden_planner.core.project import ProjectManager
from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services.plant_library import PlantLibrary
from open_garden_planner.services.plant_specimens import add_specimen
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.views.plant_catalog_view import PlantCatalogView, PlantSpeciesEditDialog


def test_relation_editor_preserves_future_flags_and_can_mark_wanted_without_specimen(qtbot, tmp_path):
    library = PlantLibrary(tmp_path / "library.json")
    plant_id = library.add_plant(PlantSpeciesData("Rosa", "Rose", raw_data={"catalog_relations": ["future"]}))
    editor = PlantSpeciesEditDialog(library.get_plant(plant_id))
    qtbot.addWidget(editor)
    editor.relation_checks["wanted"].setChecked(True)
    editor.relation_checks["owned"].setChecked(True)
    editor._save()
    library.update_plant(plant_id, editor.plant)
    saved = PlantLibrary(tmp_path / "library.json").get_plant(plant_id)
    assert saved.raw_data["catalog_relations"] == ["future", "wanted", "owned"]
    view = PlantCatalogView(library)
    qtbot.addWidget(view)
    assert "0 specimen(s)" in view.results_list.item(0).text()
    assert "Wanted" in view.results_list.item(0).text()
    assert "Owned" in view.results_list.item(0).text()


def test_catalogue_filters_and_badges_use_project_specimens(qtbot, tmp_path):
    library = PlantLibrary(tmp_path / "library.json")
    rose = library.add_plant(PlantSpeciesData("Rosa", "Rose", raw_data={"catalog_relations": ["wanted"],
                                                  "botanical_profile": {"category": "Arbuste", "cultivar": "Diana"}}))
    library.add_plant(PlantSpeciesData("Lavandula", "Lavande"))
    project, scene, commands = ProjectManager(), CanvasScene(), CommandManager()
    document, _ = add_specimen(project.snapshot_dict(scene), rose, "Rose terrasse", cultivation_mode="pot")
    commands.execute(SpecimenEditCommand(project, document))
    view = PlantCatalogView(library, project=project, scene=scene, commands=commands)
    qtbot.addWidget(view)
    view.relation_filter.setCurrentIndex(view.relation_filter.findData("owned"))
    assert view.results_list.count() == 1
    assert "Diana" in view.results_list.item(0).text()
    assert "1 specimen(s)" in view.results_list.item(0).text()
    view.mode_filter.setCurrentIndex(view.mode_filter.findData("pot"))
    view.location_filter.setText("terrasse")
    assert view.results_list.count() == 1
    view.category_filter.setCurrentIndex(view.category_filter.findData("Arbuste"))
    assert view.results_list.count() == 1
    view.mode_filter.setCurrentIndex(view.mode_filter.findData("ground"))
    assert view.results_list.count() == 0
