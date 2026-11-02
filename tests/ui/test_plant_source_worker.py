"""Source workers return to the Qt thread before any catalogue mutation."""
from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication, QInputDialog, QMessageBox

from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services.plant_library import PlantLibrary
from open_garden_planner.ui.views.plant_catalog_view import PlantCatalogView


def test_url_result_is_reviewed_on_gui_thread(qtbot, tmp_path, monkeypatch):
    library = PlantLibrary(tmp_path / "library.json")
    view = PlantCatalogView(library)
    qtbot.addWidget(view)
    seen = []
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: (
        "https://www.promessedefleurs.com/rose.html", True))
    monkeypatch.setattr("open_garden_planner.ui.widgets.plant_source_worker.fetch_plant_source",
                        lambda _url: PlantSpeciesData("Rosa", "Rose", source_id="source"))
    view._preview_import = lambda plants: seen.append((plants, QThread.currentThread()))
    view._retrieve_source(False)
    qtbot.waitUntil(lambda: bool(seen))
    assert seen[0][1] == QApplication.instance().thread()
    assert seen[0][0][0].common_name == "Rose"
    assert library.count == 0  # Review has not authorized persistence.


def test_url_failure_keeps_library_bytes(qtbot, tmp_path, monkeypatch):
    path = tmp_path / "library.json"
    library = PlantLibrary(path)
    library.add_plant(PlantSpeciesData("Rosa", "Rose"))
    before = path.read_bytes()
    view = PlantCatalogView(library)
    qtbot.addWidget(view)
    warnings = []
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: (
        "https://www.promessedefleurs.com/rose.html", True))
    def offline(url):
        raise ValueError("offline")
    monkeypatch.setattr("open_garden_planner.ui.widgets.plant_source_worker.fetch_plant_source", offline)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a))
    view._retrieve_source(False)
    qtbot.waitUntil(lambda: bool(warnings))
    assert path.read_bytes() == before
    assert view.url_button.isEnabled()
