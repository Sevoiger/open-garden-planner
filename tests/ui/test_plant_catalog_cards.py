"""Catalogue cards use local references and structured periods without network."""
import os
import subprocess
import sys

from PIL import Image

from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services.botanical_media import (
    edit_reference_media,
    import_reference_media,
)
from open_garden_planner.services.plant_library import PlantLibrary
from open_garden_planner.ui.views.plant_catalog_view import PlantCatalogView
from open_garden_planner.ui.widgets.plant_catalog_cards import CARD_ROLE


def test_card_thumbnail_and_month_bands_use_local_library_data(qtbot, tmp_path, monkeypatch):
    library = PlantLibrary(tmp_path / "library.json")
    identity = library.add_plant(PlantSpeciesData("Hibiscus syriacus", "Althéa", max_height_cm=200, max_spread_cm=150,
                                                 raw_data={"botanical_profile": {"cultivar": "Diana", "exposures": "Soleil, mi-ombre"},
                                                           "local_sheet": {"characteristics": {"Période de floraison": "Août à Octobre"}}}))
    photo = tmp_path / "reference.png"
    Image.new("RGB", (64, 64), "white").save(photo)
    asset = import_reference_media(photo, library.path, identity)
    library.update_plant(identity, edit_reference_media(library.get_plant(identity), asset=asset))
    def offline(*_a, **_kw):
        raise AssertionError("Catalogue display must not fetch images")
    monkeypatch.setattr("urllib.request.OpenerDirector.open", offline)
    view = PlantCatalogView(library)
    qtbot.addWidget(view)
    view.resize(1100, 800)
    view.show()
    item = view.results_list.item(0)
    qtbot.waitUntil(lambda: not item.icon().isNull())
    data = item.data(CARD_ROLE)
    assert data["calendar"]["flowering"]["months"] == [8, 9, 10]
    assert "Diana" in data["botanical"]
    assert "200" in data["conditions"] and "150" in data["conditions"]
    assert "Soleil, mi-ombre" in data["conditions"]
    assert view._preview_rows[0][1].text() == "Diana"
    rendered = view.results_list.viewport().grab().toImage()
    scale = rendered.devicePixelRatio()
    pixel = rendered.pixelColor(int(30 * scale), int(35 * scale))
    assert pixel.red() > 240 and pixel.green() > 240 and pixel.blue() > 240


def test_card_paint_keeps_owner_alive_during_cyclic_collection(tmp_path):
    script = '''
import gc, sys
from pathlib import Path
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QApplication
from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services.plant_library import PlantLibrary
from open_garden_planner.ui.views.plant_catalog_view import PlantCatalogView
app = QApplication([])
app.setOrganizationName("OGP-isolated-card-paint")
app.setApplicationName("card-paint")
library = PlantLibrary(Path(sys.argv[1]))
library.add_plant(PlantSpeciesData("Rosa", "Rose"))
gc.disable()
original = QPainter.fillRect
calls = []
def collect_while_painting(painter, *args, **kwargs):
    gc.collect()
    calls.append(True)
    return original(painter, *args, **kwargs)
QPainter.fillRect = collect_while_painting
view = PlantCatalogView(library)
view.resize(1000, 800)
view.show()
del view
app.processEvents()
assert calls, "The real item delegate did not paint"
QPainter.fillRect = original
gc.enable()
gc.collect()
app.processEvents()
'''
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path / "library.json")],
                            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"}, capture_output=True,
                            text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr
