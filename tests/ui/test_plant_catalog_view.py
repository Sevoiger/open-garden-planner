"""UI coverage for the standalone custom-species catalog."""

import json

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog

from open_garden_planner.app.application import GardenPlannerApp
from open_garden_planner.models.plant_data import (
    PlantCycle,
    PlantSpeciesData,
    SunRequirement,
)
from open_garden_planner.services.local_plant_sources import read_local_plant_sources
from open_garden_planner.services.plant_library import PlantLibrary
from open_garden_planner.ui.dialogs.plant_sheet_dialog import PlantSheetDialog
from open_garden_planner.ui.views.plant_catalog_view import (
    LocalPlantImportPreviewDialog,
    PlantCatalogView,
    PlantSpeciesEditDialog,
)


def _plant(**changes) -> PlantSpeciesData:
    values = {
        "scientific_name": "Rosa canina",
        "common_name": "Églantier",
        "family": "Rosaceae",
        "genus": "Rosa",
        "cycle": PlantCycle.PERENNIAL,
        "sun_requirement": SunRequirement.FULL_SUN,
        "max_height_cm": 250,
        "description": "A native rose.",
        "data_source": "custom",
    }
    values.update(changes)
    return PlantSpeciesData(**values)


def _edit_through_sheet(monkeypatch):
    def open_sheet(dialog):
        assert dialog.tabs.count() == 5
        dialog._edit()
        return int(QDialog.DialogCode.Accepted)
    monkeypatch.setattr(PlantSheetDialog, "exec", open_sheet)


def test_calendar_source_preview_and_manual_month_correction_survive_reopen(qtbot, tmp_path):
    library = PlantLibrary(tmp_path / "custom_plants.json")
    plant_id = library.add_plant(_plant(raw_data={
        "local_sheet": {"characteristics": {"Période de floraison": "Août à Octobre"}},
    }))
    view = PlantCatalogView(library)
    qtbot.addWidget(view)
    view.show()
    assert view.preview_calendar.months()["flowering"] == [8, 9, 10]
    dialog = PlantSpeciesEditDialog(library.get_plant(plant_id))
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.mouseClick(dialog.calendar._buttons["flowering"][10], Qt.MouseButton.LeftButton)
    dialog._save()
    library.update_plant(plant_id, dialog.plant)

    reopened = PlantLibrary(tmp_path / "custom_plants.json").get_plant(plant_id)
    saved = reopened.raw_data["botanical_calendar"]["flowering"]
    assert saved["months"] == [8, 9, 10, 11]
    assert saved["source_text"] == "Août à Octobre"
    assert saved["manually_edited"] is True
    assert reopened.raw_data["local_sheet"]["characteristics"]["Période de floraison"] == "Août à Octobre"


def test_calendar_edit_preserves_unknown_future_fields(qtbot, tmp_path):
    library = PlantLibrary(tmp_path / "custom_plants.json")
    plant_id = library.add_plant(_plant(raw_data={"botanical_calendar": {
        "climate_context": "future data",
        "flowering": {"months": [8], "source_text": "Août", "extra": {"credit": "source"}},
        "harvest": {"months": [10]},
    }}))
    dialog = PlantSpeciesEditDialog(library.get_plant(plant_id))
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.mouseClick(dialog.calendar._buttons["flowering"][8], Qt.MouseButton.LeftButton)
    dialog._save()
    library.update_plant(plant_id, dialog.plant)

    saved = PlantLibrary(tmp_path / "custom_plants.json").get_plant(plant_id)
    calendar = saved.raw_data["botanical_calendar"]
    assert calendar["climate_context"] == "future data"
    assert calendar["harvest"] == {"months": [10]}
    assert calendar["flowering"]["extra"] == {"credit": "source"}
    assert calendar["flowering"]["months"] == [8, 9]


def test_month_filter_uses_any_known_recommended_period(qtbot, tmp_path):
    library = PlantLibrary(tmp_path / "custom_plants.json")
    library.add_plant(_plant(common_name="Hiver", raw_data={"local_sheet": {
        "characteristics": {"Période de floraison": "Décembre à Février"},
    }}))
    library.add_plant(_plant(common_name="Inconnue"))
    view = PlantCatalogView(library)
    qtbot.addWidget(view)
    view.month_filter.setCurrentIndex(view.month_filter.findData(1))
    assert view.results_list.count() == 1
    assert "Hiver" in view.results_list.item(0).text()
    view.month_filter.setCurrentIndex(view.month_filter.findData(7))
    assert view.results_list.count() == 0


@pytest.mark.parametrize("calendar", ["future", {"flowering": None}, {"flowering": {"months": "future"}}])
def test_edit_rejects_unrecognized_calendar_data(qtbot, calendar):
    plant = _plant(raw_data={"botanical_calendar": calendar})
    dialog = PlantSpeciesEditDialog(plant)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.mouseClick(dialog.calendar._buttons["flowering"][0], Qt.MouseButton.LeftButton)
    dialog._save()
    assert dialog.plant is None
    assert not dialog.error_label.isHidden()
    assert plant.raw_data["botanical_calendar"] == calendar


def test_catalog_search_and_preview_use_species_records_without_canvas(qtbot, tmp_path):
    library = PlantLibrary(tmp_path / "custom_plants.json")
    plant_id = library.add_plant(_plant())
    library.add_plant(_plant(
        scientific_name="Lavandula angustifolia",
        common_name="Lavande",
        family="Lamiaceae",
    ))

    view = PlantCatalogView(library)
    qtbot.addWidget(view)
    view.show()

    assert view.results_list.count() == 2
    assert view.open_button.isEnabled()
    view.search_edit.setText("rosaceae")

    assert view.results_list.count() == 1
    assert view.results_list.currentItem().data(Qt.ItemDataRole.UserRole) == plant_id
    assert view.preview_name.text() == "Églantier"
    assert view.preview_scientific_name.text() == "Rosa canina"
    assert view.preview_description.text() == "A native rose."
    assert "Perennial" in [
        view.preview_form.itemAt(row, view.preview_form.ItemRole.FieldRole).widget().text()
        for row in range(view.preview_form.rowCount())
    ]
    assert not hasattr(view, "canvas_scene")


def test_enter_opens_and_saves_selected_species_without_canvas(
    qtbot, tmp_path, monkeypatch
):
    _edit_through_sheet(monkeypatch)
    library = PlantLibrary(tmp_path / "custom_plants.json")
    plant_id = library.add_plant(_plant(raw_data={"future_field": "keep"}))
    view = PlantCatalogView(library)
    qtbot.addWidget(view)
    view.show()

    def accept_edit(dialog):
        dialog.common_name_edit.setText("Églantier commun")
        dialog._save()
        return int(QDialog.DialogCode.Accepted)

    monkeypatch.setattr(PlantSpeciesEditDialog, "exec", accept_edit)
    view.results_list.setFocus()
    view.results_list.setCurrentRow(0)
    qtbot.keyClick(view.results_list, Qt.Key.Key_Return)

    saved = library.get_plant(plant_id)
    assert saved is not None
    assert saved.common_name == "Églantier commun"
    assert saved.raw_data == {"future_field": "keep"}
    assert view.preview_name.text() == "Églantier commun"


@pytest.mark.parametrize("activation", ["double_click", "button"])
def test_species_can_be_opened_by_double_click_or_button(
    qtbot, tmp_path, monkeypatch, activation
):
    _edit_through_sheet(monkeypatch)
    library = PlantLibrary(tmp_path / "custom_plants.json")
    plant_id = library.add_plant(_plant())
    view = PlantCatalogView(library)
    qtbot.addWidget(view)
    view.show()

    def accept_edit(dialog):
        dialog.common_name_edit.setText("Fiche ouverte")
        dialog._save()
        return int(QDialog.DialogCode.Accepted)

    monkeypatch.setattr(PlantSpeciesEditDialog, "exec", accept_edit)
    item = view.results_list.item(0)
    view.results_list.setCurrentItem(item)
    if activation == "double_click":
        position = view.results_list.visualItemRect(item).center()
        qtbot.mouseDClick(
            view.results_list.viewport(), Qt.MouseButton.LeftButton, pos=position
        )
    else:
        qtbot.mouseClick(view.open_button, Qt.MouseButton.LeftButton)

    assert library.get_plant(plant_id).common_name == "Fiche ouverte"


def test_add_button_creates_record_by_name_without_canvas(qtbot, tmp_path, monkeypatch):
    library = PlantLibrary(tmp_path / "custom_plants.json")
    view = PlantCatalogView(library)
    qtbot.addWidget(view)
    view.show()

    def accept_new(dialog):
        dialog.common_name_edit.setText("Consoude")
        dialog._save()
        return int(QDialog.DialogCode.Accepted)

    monkeypatch.setattr(PlantSpeciesEditDialog, "exec", accept_new)
    qtbot.mouseClick(view.add_button, Qt.MouseButton.LeftButton)

    assert library.count == 1
    plant_id, plant = library.get_all_plants()[0]
    assert plant.common_name == "Consoude"
    assert plant.scientific_name == ""
    assert view.results_list.currentItem().data(Qt.ItemDataRole.UserRole) == plant_id


def test_species_edit_dialog_requires_at_least_one_name(qtbot):
    dialog = PlantSpeciesEditDialog()
    qtbot.addWidget(dialog)

    dialog._save()

    assert dialog.plant is None
    assert not dialog.error_label.isHidden()

    dialog.common_name_edit.setText("Pissenlit")
    dialog._save()
    assert dialog.plant is not None
    assert dialog.plant.common_name == "Pissenlit"
    assert dialog.plant.scientific_name == ""


def test_unedited_dimensions_keep_their_original_precision(qtbot):
    plant = _plant(max_height_cm=123.4567, max_spread_cm=47.8912)
    dialog = PlantSpeciesEditDialog(plant)
    qtbot.addWidget(dialog)
    dialog.common_name_edit.setText("Rosier des chiens")
    dialog._save()

    assert dialog.plant.max_height_cm == 123.4567
    assert dialog.plant.max_spread_cm == 47.8912


def test_main_window_appends_catalog_after_existing_tabs(qtbot):
    window = GardenPlannerApp()
    qtbot.addWidget(window)

    catalog_index = window._tab_widget.indexOf(window.plant_catalog_view)
    assert catalog_index == window._tab_widget.count() - 1
    assert window._tab_widget.tabText(catalog_index) == "Plant Catalog"
    assert window._tab_widget.widget(catalog_index) is window.plant_catalog_view


def test_edit_conflict_reloads_latest_library_record(qtbot, tmp_path, monkeypatch):
    _edit_through_sheet(monkeypatch)
    path = tmp_path / "custom_plants.json"
    library = PlantLibrary(path)
    plant_id = library.add_plant(_plant())
    external_library = PlantLibrary(path)
    view = PlantCatalogView(library)
    qtbot.addWidget(view)
    view.show()
    external_library.update_plant(
        plant_id, _plant(common_name="Version externe"),
        expected_revision=external_library.revision,
    )

    def stale_edit(dialog):
        dialog.common_name_edit.setText("Écrasement refusé")
        dialog._save()
        return int(QDialog.DialogCode.Accepted)

    monkeypatch.setattr(PlantSpeciesEditDialog, "exec", stale_edit)
    monkeypatch.setattr(
        "open_garden_planner.ui.dialogs.plant_sheet_dialog.show_copyable_error",
        lambda *_args: None,
    )
    qtbot.mouseClick(view.open_button, Qt.MouseButton.LeftButton)

    assert library.get_plant(plant_id).common_name == "Version externe"
    assert view.preview_name.text() == "Version externe"


def test_local_import_previews_and_skips_existing_species(
    qtbot, tmp_path, monkeypatch
):
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    records = (
        ("exact-duplicate.json", "https://example.test/rose", "Rosa canina", "Rose source"),
        ("new.json", "https://example.test/canna", "Canna indica", "Balisier"),
        ("similar.json", "https://example.test/rose-copy", "Rosa canina", "Rose variant"),
    )
    for filename, url, scientific, common in records:
        (source_dir / filename).write_text(json.dumps({
            "schema_version": "1.0",
            "url": url,
            "safe_name": filename.removesuffix(".json"),
            "nom_botanique": scientific,
            "nom_commun": common,
            "description_text": "",
            "characteristics": {},
        }), encoding="utf-8")

    library = PlantLibrary(tmp_path / "custom_plants.json")
    local_plants = read_local_plant_sources(source_dir)
    existing = local_plants[0]
    existing.common_name = "Nom personnel conservé"
    existing_id = library.add_plant(existing)
    view = PlantCatalogView(library)
    qtbot.addWidget(view)
    view.show()
    monkeypatch.setattr(
        "open_garden_planner.ui.views.plant_catalog_view.QFileDialog.getExistingDirectory",
        lambda *_args: str(source_dir),
    )
    monkeypatch.setattr(
        "open_garden_planner.ui.views.plant_catalog_view.QMessageBox.information",
        lambda *_args: None,
    )

    def accept_preview(dialog):
        assert library.count == 1  # Nothing is saved before the preview is accepted.
        assert dialog.table.rowCount() == 3
        assert len(dialog.pending_plants) == 2
        assert dialog.table.item(0, 3).text() == "Duplicate source ID — will be skipped"
        assert dialog.table.item(2, 3).text() == "Possible name match — compare"

        def edit_preview(edit_dialog):
            edit_dialog.common_name_edit.setText("Balisier modifié")
            edit_dialog._save()
            return int(QDialog.DialogCode.Accepted)

        monkeypatch.setattr(PlantSpeciesEditDialog, "exec", edit_preview)
        dialog.table.selectRow(1)
        dialog._edit_selected()
        assert dialog.pending_plants[0].common_name == "Balisier modifié"
        return int(QDialog.DialogCode.Accepted)

    monkeypatch.setattr(LocalPlantImportPreviewDialog, "exec", accept_preview)
    view.import_folder_action.trigger()

    assert library.count == 3
    assert library.get_plant(existing_id).common_name == "Nom personnel conservé"
    assert any(
        plant.common_name == "Balisier modifié"
        for _, plant in library.get_all_plants()
    )
    assert sum(
        plant.scientific_name == "Rosa canina"
        for _, plant in library.get_all_plants()
    ) == 2  # Similar names are surfaced for review, never silently deduplicated.


def test_single_file_import_is_previewed_without_reading_siblings(qtbot, tmp_path, monkeypatch):
    path = tmp_path / "selected.json"
    path.write_text(json.dumps({
        "schema_version": "1.0", "url": "https://example.test/rose",
        "safe_name": "rose", "nom_botanique": "Rosa canina",
        "nom_commun": "Églantier", "characteristics": {},
    }), encoding="utf-8")
    (tmp_path / "unselected.json").write_text("invalid", encoding="utf-8")
    library = PlantLibrary(tmp_path / "store" / "custom_plants.json")
    view = PlantCatalogView(library)
    qtbot.addWidget(view)
    view.show()
    monkeypatch.setattr(
        "open_garden_planner.ui.views.plant_catalog_view.QFileDialog.getOpenFileName",
        lambda *_args: (str(path), ""),
    )

    def reject_preview(dialog):
        assert library.count == 0
        assert dialog.table.rowCount() == 1
        assert dialog.plants[0].common_name == "Églantier"
        return int(QDialog.DialogCode.Rejected)

    monkeypatch.setattr(LocalPlantImportPreviewDialog, "exec", reject_preview)
    qtbot.mouseClick(view.import_button, Qt.MouseButton.LeftButton)

    assert library.count == 0
    assert not library.path.exists()


def test_specialized_calendar_is_collapsible_editable_and_preserves_context(qtbot, tmp_path):
    library = PlantLibrary(tmp_path / "plants.json")
    identity = library.add_plant(_plant(raw_data={
        "botanical_profile": {"climate": "Climat océanique"},
        "botanical_calendar": {"harvest": {"months": [8], "source_text": "Août", "unknown": "keep"}},
    }))
    dialog = PlantSpeciesEditDialog(library.get_plant(identity))
    qtbot.addWidget(dialog)
    dialog.show()
    group = dialog.calendar.specialized_group
    assert group is not None and not group.isChecked()
    group.setChecked(True)
    qtbot.mouseClick(dialog.calendar._buttons["harvest"][8], Qt.MouseButton.LeftButton)
    dialog._save()
    assert dialog.plant.raw_data["botanical_calendar"]["harvest"] == {
        "months": [8, 9], "source_text": "Août", "manually_edited": True, "unknown": "keep",
    }
    assert dialog.plant.raw_data["botanical_profile"]["climate"] == "Climat océanique"
    library.update_plant(identity, dialog.plant)
    reopened = PlantCatalogView(PlantLibrary(library.path))
    qtbot.addWidget(reopened)
    reopened.month_filter.setCurrentIndex(reopened.month_filter.findData(9))
    reopened.activity_filter.setCurrentIndex(reopened.activity_filter.findData("harvest"))
    assert reopened.results_list.count() == 1


@pytest.mark.parametrize("outcome", ["save", "cancel_review", "cancel_editor", "conflict"])
def test_source_review_choices_reach_editor_and_save_atomically(qtbot, tmp_path, monkeypatch, outcome):
    from copy import deepcopy
    from hashlib import sha256

    from PyQt6.QtWidgets import QTableWidget

    from open_garden_planner.services.plant_source_import import mark_import_baseline
    from open_garden_planner.ui.widgets.responsive_dialog import ResponsiveDialog

    url = "https://www.promessedefleurs.com/rose.html"
    existing = mark_import_baseline(_plant(
        source_id="local-sheet:" + sha256(url.encode()).hexdigest()[:32],
        data_source="promesse_de_fleurs", max_height_cm=100,
        raw_data={"promesse_de_fleurs": {"source_url": url, "fields": {}}},
    ))
    existing.max_height_cm = 123  # A personal correction is kept by default.
    incoming = deepcopy(existing)
    incoming.max_height_cm = 200
    incoming.common_name = "Nom fournisseur"
    library = PlantLibrary(tmp_path / "library.json")
    identity = library.add_plant(existing)
    before = library.path.read_bytes()
    view = PlantCatalogView(library)
    qtbot.addWidget(view)
    view._source_context = (identity, library.get_plant(identity), library.revision)
    errors, downloads, edited = [], [], []
    monkeypatch.setattr(view, "_show_save_error", errors.append)
    monkeypatch.setattr(view, "_download_reference_photo", downloads.append)

    def review(dialog):
        table = dialog.findChild(QTableWidget, "sourceConflictTable")
        assert table is not None
        for row in range(table.rowCount()):
            label = table.item(row, 0).text()
            selector = table.cellWidget(row, 3)
            if label == "Maximum Height (cm)":
                assert selector.currentData() is False
                selector.setCurrentIndex(1)
            elif label == "Common Name":
                assert selector.currentData() is True
                selector.setCurrentIndex(0)
        assert library.path.read_bytes() == before
        return 0 if outcome == "cancel_review" else 1

    def edit(dialog):
        edited.append(True)
        assert dialog._original.max_height_cm == 200
        assert dialog._original.common_name == existing.common_name
        assert library.path.read_bytes() == before
        if outcome == "cancel_editor":
            return 0
        dialog._save()
        if outcome == "conflict":
            external = PlantLibrary(library.path)
            latest = external.get_plant(identity)
            latest.common_name = "Modification concurrente"
            external.update_plant(identity, latest, expected_revision=external.revision)
        return 1

    monkeypatch.setattr(ResponsiveDialog, "exec", review)
    monkeypatch.setattr(PlantSpeciesEditDialog, "exec", edit)
    view._source_ready(incoming, None)
    reopened = PlantLibrary(library.path).get_plant(identity)
    if outcome == "save":
        assert reopened.max_height_cm == 200
        assert reopened.common_name == existing.common_name
        state = reopened.raw_data["catalog_import"]
        assert "common_name" in state["manual_fields"]
        assert "max_height_cm" not in state["manual_fields"]
        assert downloads == [identity]
    elif outcome == "conflict":
        assert errors
        assert reopened.common_name == "Modification concurrente"
        assert reopened.max_height_cm == 123
    else:
        assert library.path.read_bytes() == before
        assert not errors
        assert not downloads
        assert bool(edited) == (outcome == "cancel_editor")
