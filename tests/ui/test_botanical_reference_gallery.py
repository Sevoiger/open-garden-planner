"""Reference import stays independent of specimens and the canvas."""
from PIL import Image
from PyQt6.QtWidgets import QFileDialog

from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services.botanical_media import (
    edit_reference_media,
    import_reference_media,
    primary_reference,
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
