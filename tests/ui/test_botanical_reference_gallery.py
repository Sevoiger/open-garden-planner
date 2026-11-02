"""Reference import stays independent of specimens and the canvas."""
from PIL import Image
from PyQt6.QtWidgets import QFileDialog

from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services.botanical_media import (
    edit_reference_media,
    import_reference_media,
    primary_reference,
    reference_assets,
    resolve_reference_media,
)
from open_garden_planner.services.plant_library import PlantLibrary
from open_garden_planner.ui.widgets.botanical_reference_gallery import BotanicalReferenceGallery


def test_reference_gallery_imports_and_removes_without_deleting_original(qtbot, tmp_path, monkeypatch):
    library = PlantLibrary(tmp_path / "app-data/custom_plants.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose"))
    source = tmp_path / "reference.jpg"
    Image.new("RGB", (400, 250), "pink").save(source)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_a, **_kw: (str(source), ""))
    gallery = BotanicalReferenceGallery(library, identity)
    qtbot.addWidget(gallery)
    gallery.show()
    gallery.caption.setText("Fleur de référence")
    gallery.credit.setText("Fournisseur")
    gallery._import()
    qtbot.waitUntil(lambda: gallery.gallery.count() == 1 and not gallery._jobs)
    assert not gallery.gallery.item(0).icon().isNull()
    gallery.gallery.setCurrentRow(0)
    gallery._primary()
    qtbot.waitUntil(lambda: not gallery._jobs)
    asset = primary_reference(PlantLibrary(library.path).get_plant(identity))
    assert asset["caption"] == "Fleur de référence"
    assert asset["credit"] == "Fournisseur"
    original = resolve_reference_media(library.path, asset["photo_path"])
    gallery.gallery.setCurrentRow(0)
    gallery._remove()
    assert library.get_plant(identity).raw_data["reference_media"] == []
    assert original.read_bytes() == source.read_bytes()
    qtbot.waitUntil(lambda: not gallery._jobs)


def test_stale_media_form_cannot_overwrite_a_concurrent_caption(qtbot, tmp_path):
    library = PlantLibrary(tmp_path / "custom_plants.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose"))
    source = tmp_path / "reference.jpg"
    Image.new("RGB", (32, 32), "white").save(source)
    asset = import_reference_media(source, library.path, identity, caption="Old caption", credit="Old credit")
    library.update_plant(identity, edit_reference_media(library.get_plant(identity), asset=asset))
    gallery = BotanicalReferenceGallery(library, identity)
    qtbot.addWidget(gallery)
    gallery.gallery.setCurrentRow(0)
    newer = edit_reference_media(library.get_plant(identity), update_id=asset["id"], fields={"caption": "Concurrent caption"})
    library.update_plant(identity, newer)
    gallery.credit.setText("New credit")
    gallery._save_details()
    saved = primary_reference(library.get_plant(identity))
    assert saved["caption"] == "Concurrent caption"
    assert saved["credit"] == "Old credit"
    assert gallery.status.text()
    qtbot.waitUntil(lambda: not gallery._jobs)


def test_explicit_gallery_download_uses_saved_provider_page_for_legacy_record(
    qtbot, tmp_path, monkeypatch,
):
    from PyQt6.QtWidgets import QInputDialog

    import open_garden_planner.services.plant_source_import as source_import

    library = PlantLibrary(tmp_path / "custom_plants.json")
    identity = library.add_plant(PlantSpeciesData(
        "Rosa", "Rose", raw_data={"local_sheet": {
            "url": "https://www.promessedefleurs.com/rosier.html",
        }},
    ))
    source = tmp_path / "reference.png"
    Image.new("RGB", (32, 32), "green").save(source)
    asset = import_reference_media(
        source, library.path, identity,
        source_url="https://www.promessedefleurs.com/media/rose.png",
    )
    gallery = BotanicalReferenceGallery(library, identity)
    qtbot.addWidget(gallery)
    called = {}

    def fetch(plant, library_path, library_id, **fields):
        called.update(plant=plant, library_path=library_path, library_id=library_id, fields=fields)
        return asset

    monkeypatch.setattr(source_import, "fetch_reference_photo_for_plant", fetch)
    monkeypatch.setattr(QInputDialog, "getText", lambda *_a, **_kw: (_ for _ in ()).throw(
        AssertionError("the saved provider page should be used directly after the button click")
    ))
    monkeypatch.setattr(gallery, "_run", lambda operation, callback: callback(operation(), ""))

    gallery._download_reference()

    assert called["plant"].source_id == identity
    assert called["library_path"] == library.path
    assert called["library_id"] == identity
    assert reference_assets(library.get_plant(identity)) == [asset]


def test_non_provider_local_sheet_keeps_manual_photo_url_dialog(qtbot, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QInputDialog

    import open_garden_planner.services.plant_source_import as source_import

    library = PlantLibrary(tmp_path / "custom_plants.json")
    identity = library.add_plant(PlantSpeciesData(
        "Rosa", "Rose", image_url="https://example.test/personal.jpg",
        raw_data={"local_sheet": {"url": "https://example.test/plant.json"}},
    ))
    source = tmp_path / "reference.png"
    Image.new("RGB", (32, 32), "pink").save(source)
    asset = import_reference_media(
        source, library.path, identity,
        source_url="https://www.promessedefleurs.com/media/rose.png",
    )
    gallery = BotanicalReferenceGallery(library, identity)
    qtbot.addWidget(gallery)
    photo_url = "https://www.promessedefleurs.com/media/rose.png"
    prompt = {}
    fetched = {}

    def get_text(*_args, **kwargs):
        prompt["default"] = kwargs.get("text")
        return photo_url, True

    def fetch(url, library_path, library_id, **fields):
        fetched.update(url=url, library_path=library_path, library_id=library_id, fields=fields)
        return asset

    monkeypatch.setattr(QInputDialog, "getText", get_text)
    monkeypatch.setattr(source_import, "fetch_reference_photo", fetch)
    monkeypatch.setattr(gallery, "_run", lambda operation, callback: callback(operation(), ""))

    gallery._download_reference()

    assert prompt["default"] == ""
    assert fetched["url"] == photo_url
    assert fetched["library_path"] == library.path
    assert fetched["library_id"] == identity
    assert reference_assets(library.get_plant(identity)) == [asset]
