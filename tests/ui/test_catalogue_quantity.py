"""Aggregate personal catalogue edits need no project or individual creation."""
from copy import deepcopy

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services.plant_catalog_filters import (
    catalogue_personal_record,
    specimen_summaries,
)
from open_garden_planner.services.plant_library import PlantLibrary
from open_garden_planner.ui.dialogs.plant_specimens_dialog import PlantSpecimensDialog
from open_garden_planner.ui.views.plant_catalog_view import PlantCatalogView


def test_quantity_fields_roundtrip_without_project_and_preserve_extensions(qtbot, tmp_path):
    library = PlantLibrary(tmp_path / "library.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose", raw_data={"future": {"keep": True},
                                                                          "catalog_relations": ["owned", "study", "future"]}))
    before = library.path.read_bytes()
    dialog = PlantSpecimensDialog(identity, None, None, None, library=library)
    qtbot.addWidget(dialog)
    assert dialog.quantity.value() == 1
    assert dialog.name.text() == "Rose"
    assert not dialog.acquisition_period.text() and not dialog.price.text()
    assert library.path.read_bytes() == before
    dialog.quantity.setValue(4)
    dialog.relation.setCurrentIndex(dialog.relation.findData("ordered"))
    dialog.supplier.setText("Pépinière test")
    dialog.acquisition_period.setText("2026-02")
    dialog.price.setText("19,95")
    dialog.notes.setPlainText("Lot de quatre")
    dialog._save()
    reopened = PlantLibrary(library.path).get_plant(identity)
    record, quantity = catalogue_personal_record(reopened, identity)
    assert quantity == 4
    instance = record["metadata"]["plant_instance"]
    assert instance["acquisition_period"]["precision"] == "month"
    assert instance["acquisition_price"] == 19.95
    assert instance["notes"] == "Lot de quatre"
    assert reopened.raw_data["future"] == {"keep": True}
    assert reopened.raw_data["catalog_relations"] == ["future", "study", "ordered"]
    summary = specimen_summaries([(identity, reopened)])[identity]
    assert summary["count"] == 4 and "ordered" in summary["relations"]
    assert "owned" not in summary["relations"]
    dialog.quantity.setValue(0)
    dialog._save()
    assert specimen_summaries(library.get_all_plants())[identity]["count"] == 0


def test_stale_personal_editor_does_not_overwrite_newer_library(qtbot, tmp_path, monkeypatch):
    library = PlantLibrary(tmp_path / "library.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose"))
    dialog = PlantSpecimensDialog(identity, None, None, None, library=library)
    qtbot.addWidget(dialog)
    plant = deepcopy(library.get_plant(identity))
    plant.common_name = "Correction concurrente"
    library.update_plant(identity, plant)
    errors = []
    monkeypatch.setattr("open_garden_planner.ui.dialogs.plant_specimens_dialog.show_copyable_error",
                        lambda *_args: errors.append(True))
    dialog.quantity.setValue(9)
    dialog._save()
    assert errors
    assert library.get_plant(identity).common_name == "Correction concurrente"
    assert "catalog_personal" not in library.get_plant(identity).raw_data


def test_catalogue_delete_confirmation_cancel_and_accept(qtbot, tmp_path, monkeypatch):
    library = PlantLibrary(tmp_path / "library.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose"))
    view = PlantCatalogView(library)
    qtbot.addWidget(view)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.No)
    view._delete_plant(identity)
    assert library.get_plant(identity) is not None
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    view._delete_plant(identity)
    assert PlantLibrary(library.path).get_plant(identity) is None
    assert view.results_list.count() == 0


def test_four_cards_and_pixel_scrolling_at_desktop_size(qtbot, tmp_path):
    library = PlantLibrary(tmp_path / "library.json")
    for index in range(8):
        library.add_plant(PlantSpeciesData("Rosa", f"Rose {index}"))
    view = PlantCatalogView(library)
    qtbot.addWidget(view)
    view.resize(1440, 900)
    view.show()
    qtbot.waitExposed(view)
    results = view.results_list
    assert results.verticalScrollMode() == results.ScrollMode.ScrollPerPixel
    fourth = results.visualItemRect(results.item(3))
    assert fourth.bottom() < results.viewport().height()
    results.verticalScrollBar().setValue(27)
    assert results.visualItemRect(results.item(0)).top() == -27
    assert results.item(0).data(Qt.ItemDataRole.UserRole)
    view.grab().save(str(tmp_path / "catalogue.png"))


def test_botanical_relation_edit_updates_personal_form_and_filters(qtbot, tmp_path):
    from open_garden_planner.services.plant_catalog_filters import update_catalogue_relations

    library = PlantLibrary(tmp_path / "library.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose"))
    dialog = PlantSpecimensDialog(identity, None, None, None, library=library)
    qtbot.addWidget(dialog)
    dialog._save()
    plant = update_catalogue_relations(library.get_plant(identity), {"wanted"})
    library.update_plant(identity, plant)
    reopened = PlantSpecimensDialog(identity, None, None, None, library=library)
    qtbot.addWidget(reopened)
    assert reopened.relation.currentData() == "wanted"
    assert specimen_summaries(library.get_all_plants())[identity]["relations"] == {"wanted"}


def test_sheet_sibling_editors_share_revision_without_losing_pending_fields(qtbot, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QDialog

    from open_garden_planner.ui.dialogs.plant_sheet_dialog import PlantSheetDialog
    from open_garden_planner.ui.views.plant_catalog_view import PlantSpeciesEditDialog

    library = PlantLibrary(tmp_path / "library.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose"))
    sheet = PlantSheetDialog(library, identity)
    qtbot.addWidget(sheet)
    sheet.specimens.quantity.setValue(3)
    sheet.specimens.notes.setPlainText("Saisie en cours")

    def edit(editor):
        editor.common_name_edit.setText("Rose corrigée")
        editor.relation_checks["owned"].setChecked(False)
        editor.relation_checks["wanted"].setChecked(True)
        editor._save()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(PlantSpeciesEditDialog, "exec", edit)
    sheet._edit()
    assert sheet.specimens.quantity.value() == 3
    assert sheet.specimens.notes.toPlainText() == "Saisie en cours"
    assert sheet.specimens.relation.currentData() == "wanted"
    sheet.specimens._save()
    assert sheet.revision == library.revision
    sheet._edit()
    record, quantity = catalogue_personal_record(PlantLibrary(library.path).get_plant(identity), identity)
    assert quantity == 3
    assert record["metadata"]["plant_instance"]["notes"] == "Saisie en cours"
    assert specimen_summaries(library.get_all_plants())[identity]["relations"] == {"wanted"}


def test_catalogue_receipt_uses_library_document_without_project(qtbot, tmp_path):
    from open_garden_planner.services.botanical_media import (
        edit_reference_media,
        import_reference_media,
    )

    library = PlantLibrary(tmp_path / "library.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose"))
    receipt = tmp_path / "receipt.pdf"
    receipt.write_bytes(b"%PDF-1.4\nsynthetic receipt")
    asset = import_reference_media(receipt, library.path, identity, kind="document")
    library.update_plant(identity, edit_reference_media(library.get_plant(identity), asset=asset))
    dialog = PlantSpecimensDialog(identity, None, None, None, library=library)
    qtbot.addWidget(dialog)
    dialog.receipt.setCurrentIndex(dialog.receipt.findData(asset["id"]))
    dialog._save()
    record, _quantity = catalogue_personal_record(PlantLibrary(library.path).get_plant(identity), identity)
    assert record["metadata"]["plant_instance"]["acquisition_receipt_media_id"] == asset["id"]


def test_receipt_popup_propagates_revision_to_sheet(qtbot, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QDialog

    from open_garden_planner.services.botanical_media import import_reference_media
    from open_garden_planner.ui.dialogs.plant_sheet_dialog import PlantSheetDialog
    from open_garden_planner.ui.widgets.botanical_reference_gallery import BotanicalReferenceGallery

    library = PlantLibrary(tmp_path / "library.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose"))
    sheet = PlantSheetDialog(library, identity)
    qtbot.addWidget(sheet)
    receipt = tmp_path / "receipt.pdf"
    receipt.write_bytes(b"%PDF-1.4\nsynthetic receipt")

    def import_in_popup(popup):
        gallery = popup.findChild(BotanicalReferenceGallery)
        asset = import_reference_media(receipt, library.path, identity, kind="document")
        gallery._commit(assets=[asset])
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", import_in_popup)
    sheet.specimens.notes.setPlainText("Note en cours")
    sheet.specimens._photos(receipt=True)
    assert sheet.revision == sheet.specimens._revision == library.revision
    assert sheet.specimens.notes.toPlainText() == "Note en cours"
