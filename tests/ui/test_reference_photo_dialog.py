"""Photo choices remain previews until the user accepts one image."""
from io import BytesIO

import pytest
from PIL import Image
from PyQt6.QtCore import Qt, QThread
from PyQt6.QtGui import QKeySequence
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog

from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services.botanical_media import (
    edit_reference_media,
    primary_reference,
    reference_assets,
)
from open_garden_planner.services.plant_library import PlantLibrary
from open_garden_planner.services.plant_source_import import (
    ReferencePhotoCandidate,
    ReferencePhotoSelectionRequired,
    import_reference_photo_bytes,
)
from open_garden_planner.ui.dialogs.reference_photo_dialog import ReferencePhotoDialog
from open_garden_planner.ui.views.plant_catalog_view import PlantCatalogView
from open_garden_planner.ui.widgets.botanical_reference_gallery import BotanicalReferenceGallery


def _photo(color):
    stream = BytesIO()
    Image.new("RGB", (60, 40), color).save(stream, "PNG")
    return stream.getvalue()


def _candidates():
    page = "https://www.promessedefleurs.com/rose.html"
    return (ReferencePhotoCandidate(page + "?image=1", "Rose — vue générale", page, True),
            ReferencePhotoCandidate(page + "?image=2", "Rose — détail", page, False))


def test_dialog_previews_in_worker_and_can_choose_a_different_photo(qtbot, monkeypatch):
    requests = []
    def retrieve(url):
        requests.append((url, QThread.currentThread()))
        return _photo("pink" if url.endswith("1") else "green")
    monkeypatch.setattr("open_garden_planner.services.plant_source_import.retrieve_reference_photo", retrieve)
    dialog = ReferencePhotoDialog(_candidates())
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: dialog.selected_payload is not None)
    dialog.options.setCurrentRow(1)
    qtbot.waitUntil(lambda: dialog.selected_candidate == _candidates()[1])
    assert dialog.selected_payload == _photo("green")
    assert all(thread != QApplication.instance().thread() for _, thread in requests)
    assert _candidates()[1].url in dialog.details.toPlainText()
    dialog.main_photo.setChecked(True)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted and dialog.use_as_main


def test_cancel_multiple_photos_does_not_write_library_or_originals(qtbot, tmp_path, monkeypatch):
    library = PlantLibrary(tmp_path / "plants.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose"))
    view = PlantCatalogView(library)
    qtbot.addWidget(view)
    view.status_label.setText("Downloading the reference photo…")
    before = library.path.read_bytes()
    monkeypatch.setattr("open_garden_planner.services.plant_source_import.retrieve_reference_photo", lambda _url: _photo("pink"))
    def reject(dialog):
        qtbot.waitUntil(lambda: dialog.selected_payload is not None)
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(ReferencePhotoDialog, "exec", reject)
    view._photo_jobs[1] = (None, identity, library.revision)
    view._reference_photo_ready(1, ReferencePhotoSelectionRequired(_candidates()), "")
    assert library.path.read_bytes() == before
    assert not (tmp_path / "botanical-media").exists()
    assert "Downloading" not in view.status_label.text()


def test_accept_keeps_only_chosen_original_and_botanical_fields(qtbot, tmp_path, monkeypatch):
    library = PlantLibrary(tmp_path / "plants.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Mon nom", max_height_cm=123))
    view = PlantCatalogView(library)
    qtbot.addWidget(view)
    monkeypatch.setattr("open_garden_planner.services.plant_source_import.retrieve_reference_photo",
                        lambda url: _photo("pink" if url.endswith("1") else "green"))
    def accept(dialog):
        qtbot.waitUntil(lambda: dialog.selected_payload is not None)
        dialog.options.setCurrentRow(1)
        qtbot.waitUntil(lambda: dialog.selected_candidate == _candidates()[1])
        dialog.main_photo.setChecked(True)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ReferencePhotoDialog, "exec", accept)
    view._photo_jobs[1] = (None, identity, library.revision)
    view._reference_photo_ready(1, ReferencePhotoSelectionRequired(_candidates()), "")
    qtbot.waitUntil(lambda: bool(reference_assets(library.get_plant(identity))))
    saved = PlantLibrary(library.path).get_plant(identity)
    assert saved.common_name == "Mon nom" and saved.max_height_cm == 123
    assert len(reference_assets(saved)) == 1
    asset = primary_reference(saved)
    assert asset["source_url"] == _candidates()[1].url
    assert (tmp_path / asset["photo_path"]).read_bytes() == _photo("green")
    assert asset["source_page_url"] == _candidates()[1].source_url
    assert len(list((tmp_path / "botanical-media").rglob("*.png"))) == 1


def test_catalogue_labels_and_error_message_can_be_copied(qtbot, tmp_path):
    library = PlantLibrary(tmp_path / "plants.json")
    library.add_plant(PlantSpeciesData("Rosa", "Églantier"))
    view = PlantCatalogView(library)
    qtbot.addWidget(view)
    view.show()
    message = "Photo indisponible : HTML contains multiple product records"
    view.status_label.setText(message)
    assert view.status_label.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    view.status_label.setFocus()
    view.status_label.setSelection(0, len(message))
    QTest.keySequence(view.status_label, QKeySequence(QKeySequence.StandardKey.Copy))
    assert QApplication.clipboard().text() == message
    view._copy_selected_text()
    assert "Églantier" in QApplication.clipboard().text()
    assert "Rosa" in QApplication.clipboard().text()


@pytest.mark.parametrize("entry", ["catalogue", "gallery"])
@pytest.mark.parametrize("failure", [False, True])
def test_download_all_preserves_main_photo_and_commits_only_complete_batch(
    qtbot, tmp_path, monkeypatch, entry, failure,
):
    library = PlantLibrary(tmp_path / "plants.json")
    candidates = _candidates() + (ReferencePhotoCandidate(
        _candidates()[0].source_url + "?image=3", "Rose — feuillage", _candidates()[0].source_url),)
    identity = library.add_plant(PlantSpeciesData("Rosa", "Mon nom", max_height_cm=123,
        raw_data={"local_sheet": {"url": candidates[0].source_url}}))
    first = import_reference_photo_bytes(_photo("pink"), library.path, identity,
        source_url=candidates[0].url, page_url=candidates[0].source_url)
    library.update_plant(identity, edit_reference_media(library.get_plant(identity),
                                                      asset=first, primary_id=first["id"]))
    before = library.path.read_bytes()
    requests = []
    def retrieve(url, *, opener=None):
        requests.append((url, QThread.currentThread()))
        if failure and url == candidates[2].url:
            raise ValueError("Photo unavailable")
        return _photo("green" if url == candidates[1].url else "blue")
    monkeypatch.setattr("open_garden_planner.services.plant_source_import.retrieve_reference_photo", retrieve)
    monkeypatch.setattr("open_garden_planner.ui.widgets.plant_source_worker.reference_photo_request",
                        lambda *_args, **_kwargs: ReferencePhotoSelectionRequired(candidates))
    def choose_all(dialog):
        qtbot.waitUntil(lambda: dialog.selected_payload is not None)
        dialog.all_button.click()
        assert dialog.keep_all
        return dialog.result()
    monkeypatch.setattr(ReferencePhotoDialog, "exec", choose_all)
    if entry == "catalogue":
        widget = PlantCatalogView(library)
        button_name, jobs_name = "photo_button", "_photo_jobs"
    else:
        widget = BotanicalReferenceGallery(library, identity)
        button_name, jobs_name = "download_button", "_jobs"
    qtbot.addWidget(widget)
    getattr(widget, button_name).click()
    qtbot.waitUntil(lambda: not getattr(widget, jobs_name))
    reopened = PlantLibrary(library.path).get_plant(identity)
    assert primary_reference(reopened)["id"] == first["id"]
    assert reopened.common_name == "Mon nom" and reopened.max_height_cm == 123
    assert all(thread != QApplication.instance().thread() for _, thread in requests)
    if failure:
        assert library.path.read_bytes() == before
        assert len(reference_assets(reopened)) == 1
        status = widget.status_label if entry == "catalogue" else widget.status
        assert "Photo unavailable" in status.text()
        return
    status = widget.status_label if entry == "catalogue" else widget.status
    assert {asset["source_url"] for asset in reference_assets(reopened)} == {c.url for c in candidates}, status.text()
    assert all(asset["source_page_url"] == candidates[0].source_url for asset in reference_assets(reopened))
    before = library.path.read_bytes()
    requests.clear()
    getattr(widget, button_name).click()
    qtbot.waitUntil(lambda: not getattr(widget, jobs_name))
    assert library.path.read_bytes() == before
    # Only the dialog preview is re-read; all associated URLs are skipped.
    assert [url for url, _thread in requests] == [candidates[0].url]


@pytest.mark.parametrize("entry", ["catalogue", "gallery"])
def test_reference_button_handles_a_real_multi_product_fixture(qtbot, tmp_path, monkeypatch, entry):
    import json

    page = "https://www.promessedefleurs.com/rose.html"
    first = "https://www.promessedefleurs.com/media/first.png"
    second = "https://www.promessedefleurs.com/media/second.png"
    payload = ('<link rel="canonical" href="' + page + '"><meta property="og:image" content="' + first + '">'
        '<script type="application/ld+json">' + json.dumps([
            {"@type": "Product", "url": page, "name": "Rose", "image": first},
            {"@type": "Product", "url": page + "?recommended=1", "name": "Autre vue", "image": second},
        ]) + '</script>').encode()
    class Response(BytesIO):
        headers = {}

    class Opener:
        def open(self, request, timeout):
            assert timeout == 12
            url = request.full_url
            if url.endswith("robots.txt"):
                data = b"User-agent: *\nAllow: /"
            elif url == page:
                data = payload
            else:
                assert url in (first, second)
                data = _photo("pink" if url == first else "green")
            response = Response(data)
            response.url = url
            return response
    monkeypatch.setattr("open_garden_planner.services.plant_source_import.build_opener", lambda *_args: Opener())
    library = PlantLibrary(tmp_path / "plants.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Mon nom", max_height_cm=123,
                                                image_url=first,
                                                raw_data={"promesse_de_fleurs": {
                                                    "source_url": page, "image_url": first}}))
    selected_url = [second]
    def choose(dialog):
        qtbot.waitUntil(lambda: dialog.selected_payload is not None)
        assert len(dialog.candidates) == 2
        dialog.options.setCurrentRow(1 if selected_url[0] == second else 0)
        qtbot.waitUntil(lambda: dialog.selected_candidate is not None
                       and dialog.selected_candidate.url == selected_url[0])
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ReferencePhotoDialog, "exec", choose)
    if entry == "catalogue":
        widget = PlantCatalogView(library)
        qtbot.addWidget(widget)
        widget.photo_button.click()
    else:
        widget = BotanicalReferenceGallery(library, identity)
        qtbot.addWidget(widget)
        widget.download_button.click()
    qtbot.waitUntil(lambda: bool(reference_assets(library.get_plant(identity))))
    saved = library.get_plant(identity)
    assert primary_reference(saved)["source_url"] == second
    assert len(reference_assets(saved)) == 1
    assert saved.common_name == "Mon nom" and saved.max_height_cm == 123

    selected_url[0] = first
    if entry == "catalogue":
        assert not widget.photo_button.isHidden()
        widget.photo_button.click()
    else:
        widget.download_button.click()
    qtbot.waitUntil(lambda: len(reference_assets(library.get_plant(identity))) == 2)
    reopened = PlantLibrary(library.path).get_plant(identity)
    assert primary_reference(reopened)["source_url"] == first
    assert {asset["source_url"] for asset in reference_assets(reopened)} == {first, second}
    assert reopened.image_url == first
    assert reopened.common_name == "Mon nom" and reopened.max_height_cm == 123
    if entry == "catalogue":
        qtbot.waitUntil(lambda: widget.preview_photo.pixmap() is not None
                       and widget.preview_photo.pixmap().toImage().pixelColor(0, 0).name() == "#ffc0cb")
