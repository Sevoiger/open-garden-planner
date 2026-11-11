"""Local source adapters return plant records without touching app storage."""

import hashlib
import json

import pytest

from open_garden_planner.models.plant_data import GrowthRate, SunRequirement, WaterNeeds
from open_garden_planner.services.local_plant_sources import (
    MAX_HTML_FILE_BYTES,
    collect_reference_photo_candidates,
    read_local_plant_sources,
)


def _promesse_html(url="https://www.promessedefleurs.com/arbustes/hibiscus-diana.html"):
    return f"""<!doctype html>
    <html lang="fr"><head>
      <meta property="og:site_name" content="Promesse de Fleurs">
      <meta property="og:description" content="Arbuste à fleurs blanches.">
      <meta property="og:image" content="/images/hibiscus.jpg">
      <link rel="canonical" href="{url}">
      <title>Hibiscus syriacus Diana - Althéa blanc</title>
      <script type="application/ld+json">{{"@context":"https://schema.org",
        "@type":"Product","name":"Hibiscus syriacus Diana - Althéa blanc",
        "url":"{url}","description":"Arbuste à fleurs blanches.",
        "image":"/images/hibiscus.jpg"}}</script>
    </head><body>
      <h1>Hibiscus syriacus Diana</h1>
      <dl>
        <dt>Hauteur à maturité</dt><dd>2 m</dd>
        <dt>Largeur à maturité</dt><dd>1,50 m</dd>
        <dt>Exposition</dt><dd>Soleil, Mi-ombre</dd>
        <dt>Humidité du sol</dt><dd>sol sec, sol frais</dd>
        <dt>Type de sol</dt><dd>Argilo-limoneux</dd>
        <dt>Croissance</dt><dd>lente</dd>
        <dt>Fleur de couleur</dt><dd>blanc, pur</dd>
        <dt>Feuillage de couleur</dt><dd>vert foncé</dd>
        <dt>Période de floraison</dt><dd>Août à Octobre</dd>
      </dl>
      <section class="botanique">
        <div><span>Genre</span><h2>Hibiscus</h2></div>
        <div><span>Espèce</span><h2>syriacus</h2></div>
        <div><span>Cultivar</span><h2>Diana</h2></div>
        <div><span>Famille</span><h2>Malvaceae</h2></div>
        <div><span>Autres noms communs</span><h2>Althéa, Mauve en arbre</h2></div>
      </section>
    </body></html>"""


def _json_sheet(directory, *, url):
    path = directory / "json-source.json"
    path.write_text(json.dumps({
        "schema_version": "1.0",
        "url": url,
        "safe_name": "json-source",
        "nom_botanique": "Hibiscus syriacus 'Diana'",
        "nom_commun": "Althéa",
        "description_text": "Fiche JSON explicite",
        "characteristics": {"Hauteur à maturité": "2 m"},
    }), encoding="utf-8")
    return path


def test_json_directory_uses_existing_sheet_reader_and_writes_no_store(tmp_path):
    _json_sheet(tmp_path, url="https://example.test/plant")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    plant, = read_local_plant_sources(tmp_path)

    assert plant.scientific_name == "Hibiscus syriacus 'Diana'"
    assert plant.max_height_cm == 200
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


@pytest.mark.parametrize("suffix", [".json", ".JSON", ".html", ".HTM"])
def test_single_file_import_ignores_unselected_invalid_siblings(tmp_path, suffix):
    if suffix.casefold() == ".json":
        path = _json_sheet(tmp_path, url="https://example.test/plant")
        path = path.rename(path.with_suffix(suffix))
    else:
        path = tmp_path / ("hibiscus" + suffix)
        path.write_text(_promesse_html(), encoding="utf-8")
    (tmp_path / "invalid.json").write_text("not a plant sheet", encoding="utf-8")

    plant, = read_local_plant_sources(path)

    assert plant.scientific_name == "Hibiscus syriacus 'Diana'"
    if suffix.casefold() != ".json":
        assert plant.raw_data["promesse_de_fleurs"]["image_url"] == (
            "https://www.promessedefleurs.com/images/hibiscus.jpg"
        )
    assert plant.raw_data["source_file_uri"] == path.resolve().as_uri()
    assert not (tmp_path / "custom_plants.json").exists()


@pytest.mark.parametrize("label,activity", [
    ("Meilleure période de plantation", "planting_optimal"),
    ("Période optimale de plantation", "planting_optimal"),
    ("Période de fructification", "harvest"),
    ("Période de multiplication", "propagation"),
])
def test_html_specialized_periods_reach_structured_calendar(tmp_path, label, activity):
    from open_garden_planner.services.botanical_calendar import calendar_for_species

    path = tmp_path / "period.html"
    path.write_text(_promesse_html().replace("</dl>", f"<dt>{label}</dt><dd>Novembre à Février</dd></dl>"))
    plant, = read_local_plant_sources(path)
    period = getattr(calendar_for_species(plant), activity)
    assert set(period.months) == {11, 12, 1, 2}
    assert period.source_text == "Novembre à Février"


def test_single_json_uses_the_existing_import_adapter(tmp_path):
    from open_garden_planner.services.local_plant_import import read_sheets

    path = _json_sheet(tmp_path, url="https://example.test/plant")
    existing, = read_sheets(path)
    selected, = read_local_plant_sources(path)
    assert selected.raw_data.pop("source_file_uri") == path.resolve().as_uri()
    assert existing.to_dict() == selected.to_dict()


@pytest.mark.parametrize("invalid", ["NaN", "Infinity", "1e10000"])
def test_json_nonfinite_unmapped_supplier_values_are_rejected(tmp_path, invalid):
    path = _json_sheet(tmp_path, url="https://example.test/plant")
    path.write_text(path.read_text().replace('"characteristics": {',
                                           f'"unmapped_supplier_value": {invalid}, "characteristics": {{'),
                    encoding="utf-8")

    with pytest.raises(ValueError):
        read_local_plant_sources(path)


def test_limits_apply_to_the_combined_json_html_selection(tmp_path, monkeypatch):
    import open_garden_planner.services.local_plant_sources as sources

    _json_sheet(tmp_path, url="https://example.test/plant")
    (tmp_path / "hibiscus.html").write_text(_promesse_html(), encoding="utf-8")
    monkeypatch.setattr(sources, "MAX_SOURCE_FILES", 1)
    with pytest.raises(ValueError, match="Too many plant sources"):
        read_local_plant_sources(tmp_path)

    monkeypatch.setattr(sources, "MAX_SOURCE_FILES", 100)
    monkeypatch.setattr(sources, "MAX_SOURCE_TOTAL_BYTES", sum(
        path.stat().st_size for path in tmp_path.iterdir()) - 1)
    with pytest.raises(ValueError, match="in total"):
        read_local_plant_sources(tmp_path)


def test_single_json_source_size_is_bounded_before_parsing(tmp_path):
    path = tmp_path / "oversized.json"
    path.write_bytes(b" " * (MAX_HTML_FILE_BYTES + 1))
    with pytest.raises(ValueError, match="exceeds"):
        read_local_plant_sources(path)


@pytest.mark.parametrize("suffix", [".json", ".html"])
def test_excessively_nested_sources_report_validation_error(tmp_path, suffix):
    path = tmp_path / ("nested" + suffix)
    source = ("[" * 2000 + "0" + "]" * 2000 if suffix == ".json" else
              "<div>" * 2000 + _promesse_html() + "</div>" * 2000)
    path.write_text(source, encoding="utf-8")
    with pytest.raises(ValueError, match="too deeply nested"):
        read_local_plant_sources(path)


def test_promesse_html_maps_explicit_fields_and_keeps_unmapped_source_values(tmp_path):
    path = tmp_path / "hibiscus.html"
    page = _promesse_html()
    path.write_text(page, encoding="utf-8")

    plant, = read_local_plant_sources(tmp_path)

    assert plant.scientific_name == "Hibiscus syriacus 'Diana'"
    assert plant.common_name == "Althéa, Mauve en arbre"
    assert plant.family == "Malvaceae"
    assert plant.genus == "Hibiscus"
    assert plant.max_height_cm == 200
    assert plant.max_spread_cm == 150
    assert plant.growth_rate is GrowthRate.SLOW
    assert plant.sun_requirement is SunRequirement.UNKNOWN  # Multiple values stay unknown.
    assert plant.water_needs is WaterNeeds.UNKNOWN  # A combined range is not collapsed.
    assert plant.soil_type == "Argilo-limoneux"
    assert plant.description == "Arbuste à fleurs blanches."
    assert plant.image_url == "https://www.promessedefleurs.com/images/hibiscus.jpg"
    assert plant.transplant_start is None  # Month names never become frost-relative dates.
    source = plant.raw_data["promesse_de_fleurs"]
    assert source["fields"]["Période de floraison"] == ["Août à Octobre"]
    assert source["product_json_ld"]["name"] == "Hibiscus syriacus Diana - Althéa blanc"
    assert source["sha256"] == hashlib.sha256(page.encode()).hexdigest()


def test_photo_candidates_keep_recommended_product_titles_and_dedupe_images():
    url = "https://www.promessedefleurs.com/rosier.html"
    page = f'''<html><head>
      <link rel="canonical" href="{url}">
      <meta property="og:title" content="Rose ancienne">
      <meta property="og:image" content="/media/rose.jpg">
      <script type="application/ld+json">{{"@graph":[
        {{"@type":"Product","name":"Rose ancienne","url":"{url}",
         "image":[{{"@type":"ImageObject","url":"/media/rose.jpg"}},
                  {{"url":"/media/rose-detail.jpg"}}]}},
        {{"@type":"Product","name":"Tulipe recommandée",
         "url":"https://www.promessedefleurs.com/tulipe.html",
         "image":["/media/tulipe.jpg",{{"url":"/media/tulipe-close.jpg"}}]}}
      ]}}</script></head><body><h1>Rose ancienne</h1></body></html>'''.encode()

    canonical, candidates = collect_reference_photo_candidates(page, url)

    assert canonical == url
    assert [candidate.url for candidate in candidates] == [
        "https://www.promessedefleurs.com/media/rose.jpg",
        "https://www.promessedefleurs.com/media/rose-detail.jpg",
        "https://www.promessedefleurs.com/media/tulipe.jpg",
        "https://www.promessedefleurs.com/media/tulipe-close.jpg",
    ]
    assert candidates[0].caption == "Rose ancienne"
    assert candidates[0].primary is True
    assert candidates[2].caption == "Tulipe recommandée"
    assert candidates[2].primary is False
    assert all(candidate.source_url == url for candidate in candidates)


def test_photo_candidates_include_product_gallery_without_responsive_or_zoom_duplicates():
    url = "https://www.promessedefleurs.com/allium.html"
    pictures = []
    for index in range(7):
        name = f"A/i/Allium-Gladiator-{index}"
        picture = (f'<picture><source type="image/webp" srcset="/media/opti_image/webp/ri/1640/catalog/product/{name}.webp 1640w">'
                   f'<img alt="Allium Gladiator" src="/media/catalog/product/cache/hash/{name}.jpg" '
                   f'srcset="/media/ri/320/catalog/product/{name}.jpg 320w,/media/ri/1640/catalog/product/{name}.jpg 1640w"></picture>')
        pictures.append(picture + f'<template id="gallery-image-{index}">{picture}</template>')
    page = ('<meta property="og:image" content="/media/ri/820/catalog/product/A/i/Allium-Gladiator-0.jpg">'
            '<div id="gallery">' + ''.join(pictures) + '</div>'
            '<img src="/media/logo.jpg"><div id="recommendations"><img src="/media/other.jpg"></div>').encode()

    canonical, candidates = collect_reference_photo_candidates(page, url)

    assert canonical == url
    assert len(candidates) == 7
    assert all(candidate.caption == "Allium Gladiator" for candidate in candidates)
    assert [candidate.url.rsplit("/", 1)[-1] for candidate in candidates] == [
        f"Allium-Gladiator-{index}.jpg" for index in range(7)
    ]


def test_botanical_html_import_still_refuses_multiple_product_records(tmp_path):
    url = "https://www.promessedefleurs.com/arbustes/hibiscus-diana.html"
    page = f'''<html><head><link rel="canonical" href="{url}">
      <script type="application/ld+json">[{{"@type":"Product","name":"Hibiscus"}},
        {{"@type":"Product","name":"Rosier recommandé"}}]</script>
      </head><body><h1>Hibiscus</h1></body></html>'''
    path = tmp_path / "multiple.html"
    path.write_text(page, encoding="utf-8")

    with pytest.raises(ValueError, match="multiple product records"):
        read_local_plant_sources(path)


def test_promesse_html_and_json_for_same_url_are_rejected_as_duplicates(tmp_path):
    url = "https://www.promessedefleurs.com/arbustes/hibiscus-diana.html"
    _json_sheet(tmp_path, url=url)
    (tmp_path / "hibiscus.html").write_text(_promesse_html(url), encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate plant source identity"):
        read_local_plant_sources(tmp_path)


def test_unknown_html_formats_are_refused_without_guessing_botanical_identity(tmp_path):
    (tmp_path / "watering.html").write_text("""<html><body>
      <h1>Fiche d'Arrosage - Points &amp; Massifs</h1>
      <table><tr><th>ID</th><th>Nom / Point d'Arrosage</th>
      <th>Identifiant Technique (Slug)</th><th>Volume (L)</th></tr>
      <tr><td>1</td><td>Cerisier Kanzan</td><td>cerisier_kanzan</td><td>30 Litres</td></tr>
      </table></body></html>""", encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported local HTML plant source"):
        read_local_plant_sources(tmp_path)


def test_html_source_size_is_bounded_before_parsing(tmp_path):
    (tmp_path / "oversized.html").write_bytes(b" " * (MAX_HTML_FILE_BYTES + 1))

    with pytest.raises(ValueError, match="exceeds"):
        read_local_plant_sources(tmp_path)
