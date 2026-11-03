"""Capture the real catalogue widgets using isolated synthetic garden data."""
import argparse
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw
from PyQt6.QtCore import QLocale, QPointF, QThreadPool, QTimer, QTranslator
from PyQt6.QtWidgets import QApplication


def capture(destination):
    with TemporaryDirectory(prefix="ogp-catalog-ui-") as directory, ExitStack() as cleanup:
        root = Path(directory)
        import open_garden_planner.app.settings as app_settings
        import open_garden_planner.services.plant_library as library_module

        app_settings.ORGANIZATION_NAME = "OGP-catalogue-QA"
        app_settings.APPLICATION_NAME = "synthetic-catalogue-" + root.name
        cleanup.callback(lambda: app_settings.create_qsettings().clear())
        library_module._default_app_data_dir = lambda: root / "app-data"
        import open_garden_planner.services.plant_source_import as source_import
        from open_garden_planner.app.application import (
            TAB_PLAN,
            TAB_PLANT_CATALOG,
            GardenPlannerApp,
        )
        from open_garden_planner.core.commands import CommandManager
        from open_garden_planner.core.plant_specimens import SpecimenEditCommand
        from open_garden_planner.core.project import ProjectManager
        from open_garden_planner.models.plant_data import PlantSpeciesData
        from open_garden_planner.services.botanical_media import (
            edit_reference_media,
            import_reference_media,
        )
        from open_garden_planner.services.plant_media import (
            edit_media_metadata,
            import_specimen_media,
        )
        from open_garden_planner.services.plant_source_errors import PlantSourceError
        from open_garden_planner.services.plant_specimens import add_specimen
        from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
        from open_garden_planner.ui.dialogs.copyable_error_dialog import CopyableErrorDialog
        from open_garden_planner.ui.dialogs.plant_sheet_dialog import PlantSheetDialog
        from open_garden_planner.ui.dialogs.reference_photo_dialog import ReferencePhotoDialog
        from open_garden_planner.ui.dialogs.tab_configuration_dialog import TabConfigurationDialog
        from open_garden_planner.ui.specimen_placement import place_specimen
        from open_garden_planner.ui.theme import ThemeMode, apply_theme
        from open_garden_planner.ui.views.plant_catalog_view import PlantCatalogView

        application = QApplication.instance() or QApplication([])
        QLocale.setDefault(QLocale("fr_FR"))
        translator = QTranslator()
        translator.load(str(Path(__file__).resolve().parents[1] / "src/open_garden_planner/resources/translations/open_garden_planner_fr.qm"))
        application.installTranslator(translator)
        apply_theme(application, ThemeMode.DARK)
        library = library_module.PlantLibrary(root / "app-data/custom_plants.json")
        identity = library.add_plant(PlantSpeciesData("Hibiscus syriacus", "Althéa blanc", max_height_cm=200,
            max_spread_cm=150, family="Malvaceae", description="Fiche de validation sur données fictives. " * 12,
            raw_data={"botanical_profile": {"cultivar": "Diana", "exposures": "Soleil, mi-ombre", "category": "Arbuste"},
                      "catalog_relations": ["wanted"], "local_sheet": {"characteristics": {
                          "Période de floraison": "Août à Octobre", "Période raisonnable de plantation": "Mars à Mai"}}}))
        library.add_plant(PlantSpeciesData("", "Plante à identifier", raw_data={"catalog_relations": ["study"]}))
        library.add_plant(PlantSpeciesData("Rosa canina", "Églantier recherché", raw_data={"catalog_relations": ["wanted"]}))
        source = root / "reference.jpg"
        image = Image.new("RGB", (400, 300), "#9fb893")
        draw = ImageDraw.Draw(image)
        for box in ((100, 50, 230, 180), (180, 80, 310, 210), (120, 140, 250, 270)):
            draw.ellipse(box, fill="#f3e7fa")
        draw.ellipse((175, 125, 220, 170), fill="#d4b455")
        image.save(source)
        asset = import_reference_media(source, library.path, identity, caption="Référence fictive", credit="Fixture OGP")
        library.update_plant(identity, edit_reference_media(library.get_plant(identity), asset=asset, primary_id=asset["id"]))
        project, scene, commands = ProjectManager(), CanvasScene(), CommandManager()
        project.save(scene, root / "garden.ogp")
        for name, mode in (("Althéa en pot", "pot"), ("Althéa du massif", "ground")):
            document, specimen = add_specimen(project.snapshot_dict(scene), identity, name, cultivation_mode=mode,
                                              species_data=library.get_plant(identity).to_dict())
            personal = root / (mode + ".jpg")
            image.copy().save(personal)
            asset = import_specimen_media(personal, root / "garden.ogp", specimen,
                                           caption=name + " — photo personnelle fictive")
            record = document["plant_specimens"][specimen]
            record["metadata"] = edit_media_metadata(record["metadata"], asset=asset, primary_id=asset["id"])
            commands.execute(SpecimenEditCommand(project, document))
            if mode == "ground":
                place_specimen(project, scene, commands, specimen, QPointF(100, 150))
        project.save(scene, root / "garden.ogp")
        view = PlantCatalogView(library, project=project, scene=scene, commands=commands)
        view.resize(1250, 850)
        view.show()
        view._request_thumbnail(identity)
        sheet = PlantSheetDialog(library, identity, project=project, scene=scene, commands=commands)
        sheet.resize(1100, 850)
        GardenPlannerApp._startup_sequence = lambda _self: None
        GardenPlannerApp._start_update_check = lambda _self: None
        dashboard = GardenPlannerApp()
        dashboard.showNormal()
        dashboard.resize(1100, 800)
        dashboard._navigate_to_tab(TAB_PLANT_CATALOG)
        entries = [(tab_id, dashboard._tab_widget.tabText(dashboard._tab_widget.indexOf(dashboard._tab_widgets_by_id[tab_id])), True)
                   for tab_id in dashboard._tab_order_ids()]
        tabs_dialog = TabConfigurationDialog(entries, permanent_tab_id=TAB_PLAN, parent=dashboard)
        original_retrieve = source_import.retrieve_reference_photo
        cleanup.callback(lambda: setattr(source_import, "retrieve_reference_photo", original_retrieve))
        source_import.retrieve_reference_photo = lambda _url: source.read_bytes()
        photo_choice = ReferencePhotoDialog((
            source_import.ReferencePhotoCandidate("https://www.promessedefleurs.com/photo.jpg", "Vue générale — fixture", "https://www.promessedefleurs.com/fixture.html", True),
            source_import.ReferencePhotoCandidate("https://www.promessedefleurs.com/detail.jpg", "Détail de la fleur — fixture", "https://www.promessedefleurs.com/fixture.html", False),
        ))
        source_error = CopyableErrorDialog("Erreur de source", "", source_error=PlantSourceError(
            "HTTP Error 403: Forbidden", code="SOURCE_HTTP_ERROR", http_status=403,
            url="https://www.promessedefleurs.com/fixture.html", stage="fetch"))
        destination.mkdir(parents=True, exist_ok=True)
        steps = [(view, None, "catalogue"), (view, None, "catalogue-compact"),
                 (sheet, 0, "presentation"), (sheet, 1, "culture"),
                 (sheet, 2, "exemplaires"), (sheet, 3, "medias"), (sheet, 3, "photos-personnelles"),
                 (sheet, 4, "sources"), (dashboard, None, "onglets"),
                 (tabs_dialog, None, "configurer-onglets"), (photo_choice, None, "choisir-photo"),
                 (source_error, None, "erreur-source-copiable")]
        position = [0]
        def take():
            if QThreadPool.globalInstance().activeThreadCount():
                QTimer.singleShot(50, take)
                return
            widget, tab, name = steps[position[0]]
            if name == "photos-personnelles":
                sheet.tabs.widget(3).ensureWidgetVisible(sheet.photo_stack)
            if not widget.grab().save(str(destination / f"{name}-{application.platformName()}.png")):
                raise OSError("Could not save catalogue capture")
            position[0] += 1
            if position[0] == len(steps):
                application.quit()
                return
            widget, tab, _name = steps[position[0]]
            if _name == "catalogue-compact":
                view.resize(1000, 720)
            widget.show()
            if tab is not None:
                widget.tabs.setCurrentIndex(tab)
            if steps[position[0]][2] == "photos-personnelles":
                sheet.photo_chooser.setCurrentIndex(1)
            QTimer.singleShot(180, take)
        QTimer.singleShot(300, take)
        QTimer.singleShot(15000, application.quit)
        application.exec()
        if position[0] != len(steps):
            raise RuntimeError("Catalogue capture timed out")
        sheet.close()
        view.close()
        tabs_dialog.close()
        photo_choice.close()
        source_error.close()
        dashboard._project_manager.mark_clean()
        dashboard.close()
        QThreadPool.globalInstance().waitForDone(5000)
        print(f"Captured {position[0]} views using {application.platformName()} in {destination.resolve()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("tmp/catalogue-ui-qa"))
    arguments = parser.parse_args()
    capture(arguments.output)
