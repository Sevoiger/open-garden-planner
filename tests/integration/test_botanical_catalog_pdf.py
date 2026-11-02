"""Catalogue reports include canonical personal data and publish atomically."""
import shutil
import subprocess
import unicodedata
from copy import deepcopy

import pytest
from PIL import Image

from open_garden_planner.core.plan_io import write_plan
from open_garden_planner.core.project_data import ProjectData
from open_garden_planner.models.garden_memory import period
from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services.botanical_media import (
    edit_reference_media,
    import_reference_media,
)
from open_garden_planner.services.pdf_report_service import PdfReportService
from open_garden_planner.services.plant_library import PlantLibrary
from open_garden_planner.services.plant_media import edit_media_metadata, import_specimen_media
from open_garden_planner.services.plant_specimens import add_specimen


def report_example(tmp_path):
    plant = PlantSpeciesData("Hibiscus syriacus 'Diana'", "Althéa blanc", source_id="hibiscus",
                             max_height_cm=200, max_spread_cm=150, flower_color="Blanc", family="Malvaceae",
                             description=("Floraison élégante, feuillage caduc. " * 120), raw_data={
                                 "botanical_profile": {"care": "Arrosage régulier. " * 200,
                                                       "safety_source": "Précautions à vérifier auprès du fournisseur."},
                                 "local_sheet": {"url": "https://example.test/hibiscus",
                                                 "characteristics": {"Période de floraison": "Août à Octobre"}},
                             })
    project = tmp_path / "garden.ogp"
    snapshot, identity = add_specimen(ProjectData().to_dict(), "hibiscus", "Althéa en pot",
                                       cultivation_mode="pot", species_data=plant.to_dict())
    write_plan(project, snapshot)
    image = tmp_path / "personal.jpg"
    Image.new("RGB", (640, 480), "#87ae79").save(image)
    asset = import_specimen_media(image, project, identity, caption="Photographie personnelle choisie", credit="Jardin")
    record = snapshot["plant_specimens"][identity]
    record["metadata"] = edit_media_metadata(record["metadata"], asset=asset)
    return plant, project, snapshot, asset


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="Poppler text inspection unavailable")
def test_long_accented_personal_catalogue_export(qtbot, tmp_path):
    plant, project, snapshot, asset = report_example(tmp_path)
    destination = tmp_path / "catalog.pdf"
    manual = PlantSpeciesData("", "Nom provisoire")
    PdfReportService.export_botanical_catalog([("hibiscus", plant), ("manual", manual)], destination, snapshot=snapshot,
                                             project_file=project, selected_photos={(next(iter(snapshot["plant_specimens"])), asset["id"])}, export_date="2026-11-01")
    text = subprocess.run(["pdftotext", str(destination), "-"], check=True, capture_output=True, text=True).stdout
    text = unicodedata.normalize("NFKC", text)
    assert "Althéa blanc" in text
    assert "Nom provisoire" in text
    assert "Althéa en pot" in text
    assert "Photographie personnelle choisie" in text
    assert "Précautions à vérifier" in text
    assert "2026-11-01" in text
    assert text.count("\f") >= 2


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="Poppler text inspection unavailable")
def test_botanical_export_omits_personal_content_and_unselected_photos(qtbot, tmp_path):
    plant, project, snapshot, asset = report_example(tmp_path)
    destination = tmp_path / "botanical.pdf"
    PdfReportService.export_botanical_catalog([("hibiscus", plant)], destination, snapshot=snapshot,
                                             project_file=project, selected_photos={(next(iter(snapshot["plant_specimens"])), asset["id"])}, include_personal=False)
    text = subprocess.run(["pdftotext", str(destination), "-"], check=True, capture_output=True, text=True).stdout
    assert "Althéa blanc" in text
    assert "Althéa en pot" not in text
    assert "Photographie personnelle choisie" not in text


def test_missing_image_cannot_replace_existing_report(qtbot, tmp_path):
    plant, project, snapshot, asset = report_example(tmp_path)
    destination = tmp_path / "catalog.pdf"
    destination.write_bytes(b"existing report")
    (project.parent / asset["photo_path"]).unlink()
    with pytest.raises(FileNotFoundError):
        PdfReportService.export_botanical_catalog([("hibiscus", plant)], destination, snapshot=snapshot,
                                                 project_file=project, selected_photos={(next(iter(snapshot["plant_specimens"])), asset["id"])})
    assert destination.read_bytes() == b"existing report"


def test_report_refuses_overwriting_attached_original(qtbot, tmp_path):
    plant, project, snapshot, _asset = report_example(tmp_path)
    original = tmp_path / "receipt.pdf"
    original.write_bytes(b"purchase document")
    snapshot["receipt"] = {"photo_path": "receipt.pdf"}
    with pytest.raises(ValueError, match="attached original"):
        PdfReportService.export_botanical_catalog([("hibiscus", plant)], original, snapshot=snapshot, project_file=project)
    assert original.read_bytes() == b"purchase document"


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="Poppler text inspection unavailable")
def test_photo_choice_is_scoped_to_specimen_and_partial_planting_is_printed(qtbot, tmp_path):
    plant, project, snapshot, asset = report_example(tmp_path)
    identity = next(iter(snapshot["plant_specimens"]))
    snapshot["plant_specimens"][identity]["metadata"]["garden_tracking"]["period"] = period("autumn 2023")
    snapshot, other = add_specimen(snapshot, "hibiscus", "Autre althéa")
    other_photo = {**deepcopy(asset), "caption": "Photo du second exemplaire non choisie"}
    metadata = snapshot["plant_specimens"][other]["metadata"]
    snapshot["plant_specimens"][other]["metadata"] = edit_media_metadata(metadata, asset=other_photo)
    destination = tmp_path / "scoped-photos.pdf"
    PdfReportService.export_botanical_catalog([("hibiscus", plant)], destination, snapshot=snapshot,
                                             project_file=project, selected_photos={(identity, asset["id"])})
    text = subprocess.run(["pdftotext", str(destination), "-"], check=True, capture_output=True, text=True).stdout
    assert "Photographie personnelle choisie" in text
    assert "Photo du second exemplaire non choisie" not in text
    assert "autumn 2023" in text


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="Poppler text inspection unavailable")
def test_reference_photo_uses_library_location_without_any_project(qtbot, tmp_path):
    root = tmp_path / "library-root"
    root.mkdir()
    library = PlantLibrary(root / "custom_plants.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose"))
    image = tmp_path / "reference.png"
    Image.new("RGB", (32, 32), "white").save(image)
    asset = import_reference_media(image, library.path, identity, caption="Botanical reference only", credit="Fixture")
    plant = edit_reference_media(library.get_plant(identity), asset=asset, primary_id=asset["id"])
    destination = tmp_path / "reference-sheet.pdf"
    PdfReportService.export_botanical_catalog([(identity, plant)], destination, library_path=library.path)
    text = subprocess.run(["pdftotext", str(destination), "-"], check=True, capture_output=True, text=True).stdout
    assert "Botanical reference only" in text
    assert "Fixture" in text


def test_export_cannot_overwrite_document_on_an_unselected_botanical_record(qtbot, tmp_path):
    library = PlantLibrary(tmp_path / "custom_plants.json")
    selected = library.add_plant(PlantSpeciesData("Rosa", "Rose"))
    other = library.add_plant(PlantSpeciesData("Lavandula", "Lavande"))
    receipt = tmp_path / "receipt.pdf"
    receipt.write_bytes(b"ORIGINAL RECEIPT")
    asset = import_reference_media(receipt, library.path, other, kind="document")
    plant = edit_reference_media(library.get_plant(other), asset=asset)
    library.update_plant(other, plant)
    attached = library.path.parent / asset["photo_path"]
    with pytest.raises(ValueError, match="attached original"):
        PdfReportService.export_botanical_catalog([(selected, library.get_plant(selected))], attached,
                                                  library_path=library.path)
    assert attached.read_bytes() == b"ORIGINAL RECEIPT"
    assert receipt.read_bytes() == b"ORIGINAL RECEIPT"


@pytest.mark.parametrize("include_personal", [True, False])
def test_export_protects_reference_retained_only_by_a_specimen_snapshot(qtbot, tmp_path, include_personal):
    library = PlantLibrary(tmp_path / "custom_plants.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose"))
    receipt = tmp_path / "receipt.pdf"
    receipt.write_bytes(b"ORIGINAL SNAPSHOT RECEIPT")
    asset = import_reference_media(receipt, library.path, identity, kind="document")
    with_reference = edit_reference_media(library.get_plant(identity), asset=asset)
    snapshot, _ = add_specimen(ProjectData().to_dict(), identity, "Rose en pot",
                                species_data=with_reference.to_dict())
    current = edit_reference_media(with_reference, remove_id=asset["id"])
    library.update_plant(identity, current)
    original = library.path.parent / asset["photo_path"]
    with pytest.raises(ValueError, match="attached original"):
        PdfReportService.export_botanical_catalog([(identity, current)], original,
            snapshot=snapshot, library_path=library.path, include_personal=include_personal)
    assert original.read_bytes() == b"ORIGINAL SNAPSHOT RECEIPT"


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="Poppler text inspection unavailable")
def test_specialized_months_and_frost_relative_calendar_are_printed_separately(qtbot, tmp_path):
    plant = PlantSpeciesData("", "Calendriers documentés", indoor_sow_start=-6, indoor_sow_end=-4, raw_data={
        "botanical_calendar": {
            "harvest": {"months": [8, 9], "source_text": "Août à Septembre"},
            "planting_optimal": {"months": [11, 12, 1], "source_text": "Novembre à Janvier"},
        },
        "botanical_profile": {"climate": "Climat océanique"},
    })
    destination = tmp_path / "calendars.pdf"
    PdfReportService.export_botanical_catalog([("calendar", plant)], destination)
    text = subprocess.run(["pdftotext", str(destination), "-"], check=True, capture_output=True, text=True).stdout
    text = unicodedata.normalize("NFKC", text)
    assert "Août à Septembre" in text
    assert "Novembre à Janvier" in text
    assert "Climat océanique" in text
    assert "-6 / -4" in text
