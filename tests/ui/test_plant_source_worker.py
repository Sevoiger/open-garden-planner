"""Source workers return to the Qt thread before any catalogue mutation."""
from PyQt6.QtCore import QThread, QThreadPool
from PyQt6.QtWidgets import QApplication, QDialog, QInputDialog

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
    def acknowledge(dialog):
        warnings.append((dialog.code, dialog.details.toPlainText(), QThread.currentThread()))
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr("open_garden_planner.ui.dialogs.copyable_error_dialog.CopyableErrorDialog.exec", acknowledge)
    view._retrieve_source(False)
    qtbot.waitUntil(lambda: bool(warnings))
    assert path.read_bytes() == before
    assert view.url_button.isEnabled()
    assert warnings[0][0] == "SOURCE_UNKNOWN"
    assert "offline" in warnings[0][1]
    assert warnings[0][2] == QApplication.instance().thread()


def test_enrichment_failure_keeps_status_url_and_record(qtbot, tmp_path, monkeypatch):
    from open_garden_planner.services.plant_source_errors import PlantSourceError

    library = PlantLibrary(tmp_path / "library.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Mon rosier", max_height_cm=123))
    before = library.path.read_bytes()
    view = PlantCatalogView(library)
    qtbot.addWidget(view)
    view._select_id(identity)
    url = "https://www.promessedefleurs.com/rose.html"
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: (url, True))
    def forbidden(_url):
        raise PlantSourceError("HTTP Error 403: Forbidden", code="SOURCE_HTTP_ERROR",
                               http_status=403, url=url, stage="fetch")
    monkeypatch.setattr("open_garden_planner.ui.widgets.plant_source_worker.fetch_plant_source", forbidden)
    reports = []
    def acknowledge(dialog):
        dialog.copy_button.click()
        reports.append(QApplication.clipboard().text())
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr("open_garden_planner.ui.dialogs.copyable_error_dialog.CopyableErrorDialog.exec", acknowledge)
    view._retrieve_source(True)
    qtbot.waitUntil(lambda: bool(reports))
    assert "SOURCE_HTTP_ERROR" in reports[0]
    assert "HTTP 403" in reports[0]
    assert url in reports[0]
    assert library.path.read_bytes() == before
    assert view.refresh_source_button.isEnabled()


def test_photo_worker_keeps_error_code_and_http_status(qtbot):
    from open_garden_planner.services.plant_source_errors import PlantSourceError
    from open_garden_planner.ui.widgets.plant_media_gallery import _MediaTask

    def forbidden():
        raise PlantSourceError("Image request refused", code="SOURCE_HTTP_ERROR", http_status=403)
    task = _MediaTask(forbidden)
    with qtbot.waitSignal(task.signals.done) as result:
        QThreadPool.globalInstance().start(task)
    assert result.args[1] is None
    assert result.args[2] == "[SOURCE_HTTP_ERROR HTTP 403] Image request refused"
