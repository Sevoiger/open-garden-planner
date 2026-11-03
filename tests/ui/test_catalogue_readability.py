"""Quick view stays readable and media retrieval is an explicit worker action."""
from PyQt6.QtCore import QThread
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services.botanical_media import import_reference_media, reference_assets
from open_garden_planner.services.plant_library import PlantLibrary
from open_garden_planner.ui.views.plant_catalog_view import PlantCatalogView


def test_quick_view_hides_planting_empty_fields_and_long_source_blocks(qtbot, tmp_path):
    library = PlantLibrary(tmp_path / "plants.json")
    library.add_plant(PlantSpeciesData("Rosa", "Rose", description="Présentation lisible.\n\nCulture (fiche source): détails.",
                                      raw_data={"local_sheet": {"characteristics": {
                                          "Période de floraison": "Mai à Juillet",
                                          "Période raisonnable de plantation": "Mars à Mai"}}}))
    view = PlantCatalogView(library)
    qtbot.addWidget(view)
    view.resize(1000, 700)
    view.setFont(QFont(view.font().family(), 16))
    view.show()
    QApplication.processEvents()
    assert "planting" not in view.preview_calendar.months()
    assert view.preview_calendar.months()["flowering"] == [5, 6, 7]
    assert view.preview_description.text() == "Présentation lisible."
    assert not view._preview_rows[0][0].isVisible()  # No empty cultivar label.
    assert not view.filters_widget.isVisible()
    view.filters_button.click()
    assert view.filters_widget.isVisible()
    shown = [widget for widget in (view.preview_name, view.preview_scientific_name,
             view.preview_calendar, view.preview_description_label, view.preview_description) if widget.isVisible()]
    boxes = [(widget.mapTo(view.preview_scroll.widget(), widget.rect().topLeft()), widget.height()) for widget in shown]
    for (position, height), (following, _) in zip(boxes, boxes[1:], strict=False):
        assert position.y() + height <= following.y(), "Quick-view sections overlap"


def test_reference_photo_download_is_explicit_and_preserves_manual_fields(qtbot, tmp_path, monkeypatch):
    from PIL import Image

    library = PlantLibrary(tmp_path / "plants.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Mon nom", source_id="personal-id", max_height_cm=123))
    source = tmp_path / "provider.png"
    Image.new("RGB", (40, 40), "green").save(source)
    seen = []
    def download(plant, path, library_id):
        seen.append(QThread.currentThread())
        return import_reference_media(source, path, library_id, source_url="https://www.promessedefleurs.com/photo.png")
    monkeypatch.setattr("open_garden_planner.services.plant_source_import.fetch_reference_photo_for_plant", download)
    view = PlantCatalogView(library)
    qtbot.addWidget(view)
    view.show()
    writes = []
    update = library.update_plant
    def record_write(*args, **kwargs):
        writes.append(QThread.currentThread())
        return update(*args, **kwargs)
    monkeypatch.setattr(library, "update_plant", record_write)
    assert not seen
    before = library.path.read_bytes()
    assert not reference_assets(library.get_plant(identity))
    view.photo_button.click()
    qtbot.waitUntil(lambda: bool(reference_assets(library.get_plant(identity))))
    assert seen[0] != QApplication.instance().thread()
    assert writes == [QApplication.instance().thread()]
    assert library.path.read_bytes() != before
    saved = PlantLibrary(library.path).get_plant(identity)
    assert saved.common_name == "Mon nom"
    assert saved.max_height_cm == 123
    assert saved.source_id == "personal-id"


def test_photo_result_cannot_replace_a_concurrent_manual_edit(qtbot, tmp_path):
    from PIL import Image

    library = PlantLibrary(tmp_path / "plants.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose", max_height_cm=123))
    source = tmp_path / "provider.png"
    Image.new("RGB", (30, 30), "green").save(source)
    asset = import_reference_media(source, library.path, identity)
    view = PlantCatalogView(library)
    qtbot.addWidget(view)
    view._photo_jobs[1] = (None, identity, library.revision)
    edited = library.get_plant(identity)
    edited.max_height_cm = 456
    library.update_plant(identity, edited)
    view._reference_photo_ready(1, asset, "")
    saved = PlantLibrary(library.path).get_plant(identity)
    assert saved.max_height_cm == 456
    assert not reference_assets(saved)
    assert "could not be associated" in view.status_label.text()


def test_enrichment_preview_keeps_the_selected_record_and_manual_dimensions(qtbot, tmp_path, monkeypatch):
    from io import BytesIO

    from PyQt6.QtWidgets import QDialog, QInputDialog

    from open_garden_planner.services.plant_source_import import fetch_plant_source
    from open_garden_planner.ui.views.plant_catalog_view import PlantSpeciesEditDialog
    from open_garden_planner.ui.widgets.responsive_dialog import ResponsiveDialog

    library = PlantLibrary(tmp_path / "plants.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Mon rosier", max_height_cm=123,
                                                raw_data={"future": {"keep": True}}))
    url = "https://www.promessedefleurs.com/rose.html"
    page = f'''<html><head><link rel="canonical" href="{url}">
      <script type="application/ld+json">[
        {{"@type":"Product","name":"Églantier","url":"{url}"}},
        {{"@type":"Product","name":"Tulipe recommandée","url":"/tulipe.html"}}]
      </script></head><body><h1>Églantier</h1><dl>
        <dt>Famille</dt><dd>Rosaceae</dd><dt>Hauteur à maturité</dt><dd>2 m</dd>
      </dl></body></html>'''.encode()
    class Opener:
        def open(self, request, timeout):
            response = BytesIO(b"User-agent: *\nAllow: /" if request.full_url.endswith("robots.txt") else page)
            response.url, response.headers = request.full_url, {}
            return response
    incoming = fetch_plant_source(url, opener=Opener())
    monkeypatch.setattr("open_garden_planner.ui.widgets.plant_source_worker.fetch_plant_source",
                        lambda source: fetch_plant_source(source, opener=Opener()))
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: (url, True))
    view = PlantCatalogView(library)
    qtbot.addWidget(view)
    photo_requests = []
    monkeypatch.setattr(view, "_download_reference_photo", photo_requests.append)
    monkeypatch.setattr(ResponsiveDialog, "exec", lambda _dialog: QDialog.DialogCode.Accepted)
    def accept_editor(editor):
        editor._save()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(PlantSpeciesEditDialog, "exec", accept_editor)
    view._select_id(identity)
    view._retrieve_source(True)
    qtbot.waitUntil(lambda: library.get_plant(identity).family == "Rosaceae")
    saved = PlantLibrary(library.path).get_plant(identity)
    assert library.count == 1
    assert saved.common_name == "Mon rosier"
    assert saved.max_height_cm == 123
    assert saved.family == "Rosaceae"
    assert saved.source_id == identity
    assert saved.raw_data["future"] == {"keep": True}
    assert photo_requests == [identity]
    assert view._local_import_duplicate_indexes([incoming]) == {0}
