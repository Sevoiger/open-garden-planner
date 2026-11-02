"""Generate a synthetic French catalogue for visual PDF QA, without user data."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw
from PyQt6.QtCore import QLocale, QTranslator
from PyQt6.QtWidgets import QApplication

from open_garden_planner.core.plan_io import write_plan
from open_garden_planner.core.project_data import ProjectData
from open_garden_planner.models.garden_memory import period
from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services.pdf_report_service import PdfReportService
from open_garden_planner.services.plant_media import edit_media_metadata, import_specimen_media
from open_garden_planner.services.plant_specimens import add_specimen


def generate(destination):
    application = QApplication.instance() or QApplication([])
    translator = QTranslator()
    root = Path(__file__).resolve().parents[1]
    translator.load(str(root / "src/open_garden_planner/resources/translations/open_garden_planner_fr.qm"))
    application.installTranslator(translator)
    QLocale.setDefault(QLocale("fr_FR"))
    with TemporaryDirectory(prefix="ogp-catalog-pdf-qa-") as directory:
        folder = Path(directory)
        project = folder / "garden.ogp"
        snapshot = ProjectData().to_dict()
        write_plan(project, snapshot)
        plant = PlantSpeciesData("Hibiscus syriacus 'Diana'", "Althéa blanc", source_id="hibiscus",
                                 max_height_cm=200, max_spread_cm=150, flower_color="Blanc", family="Malvaceae",
                                 description="Floraison élégante, feuillage caduc. " * 120,
                                 raw_data={"botanical_profile": {"care": "Arrosage régulier. " * 200,
                                                                  "safety_source": "Précautions à vérifier auprès du fournisseur."},
                                           "local_sheet": {"url": "https://example.test/hibiscus",
                                                           "characteristics": {"Période de floraison": "Août à Octobre"}}})
        selected = set()
        for name, mode, color in (("Althéa en pot", "pot", "#e8deef"),
                                   ("Althéa en pleine terre", "ground", "#fff3d1")):
            snapshot, identity = add_specimen(snapshot, "hibiscus", name, cultivation_mode=mode,
                                               species_data=plant.to_dict())
            source = folder / (identity + ".jpg")
            image = Image.new("RGB", (640, 480), "#9ab68a")
            drawing = ImageDraw.Draw(image)
            drawing.line((320, 460, 320, 130), fill="#306c3c", width=13)
            for box in ((200, 100, 350, 250), (290, 60, 440, 210), (350, 180, 500, 330),
                        (250, 240, 400, 390), (150, 190, 300, 340)):
                drawing.ellipse(box, fill=color)
            drawing.ellipse((290, 190, 360, 260), fill="#c89a39")
            image.save(source)
            asset = import_specimen_media(source, project, identity, caption="Image de validation - données fictives",
                                           credit="Fixture OGP")
            record = snapshot["plant_specimens"][identity]
            record["metadata"] = edit_media_metadata(record["metadata"], asset=asset, primary_id=asset["id"])
            record["metadata"]["garden_tracking"].update(period=period("printemps 2025"), state="in_place")
            selected.add((identity, asset["id"]))
        manual = PlantSpeciesData("", "Nom provisoire - informations inconnues")
        destination.parent.mkdir(parents=True, exist_ok=True)
        PdfReportService.export_botanical_catalog([("hibiscus", plant), ("manual", manual)], destination,
                                                   snapshot=snapshot, project_file=project, selected_photos=selected,
                                                   export_date="2026-11-01")
    application.removeTranslator(translator)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/pdf/catalogue-exemple.pdf"))
    arguments = parser.parse_args()
    generate(arguments.output)
    print(arguments.output.resolve())
