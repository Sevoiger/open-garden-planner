"""Explicit source imports do not mutate the library and preserve corrections."""
import hashlib
import json
from copy import deepcopy
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services.plant_source_errors import plant_source_error_info
from open_garden_planner.services.plant_source_import import (
    ReferencePhotoSelectionRequired,
    fetch_plant_source,
    fetch_reference_photo,
    fetch_reference_photo_for_plant,
    import_reference_photo_bytes,
    mark_import_baseline,
    plan_source_refresh,
    plan_source_update,
    retrieve_reference_photo,
    source_identity,
    validate_source_url,
)

URL = "https://www.promessedefleurs.com/rosier.html"


class Response(BytesIO):
    headers = {}
    url = URL


class FakeOpener:
    def __init__(self, policy=b"User-agent: *\nAllow: /", payload=None, payloads=None):
        self.policy = policy
        self.payload = payload or (
            f'<html><head><link rel="canonical" href="{URL}">'
            '<meta property="og:site_name" content="Promesse de Fleurs">'
            '<script type="application/ld+json">{"@type":"Product","name":"Rose"}</script>'
            '</head><body><h1>Rose</h1></body></html>'
        ).encode()
        self.payloads = payloads or {}
        self.calls = []

    def open(self, request, timeout):
        self.calls.append(request.full_url)
        assert timeout == 12
        payload = self.policy if request.full_url.endswith("robots.txt") else self.payloads.get(
            request.full_url, self.payload
        )
        response = Response(payload)
        response.url = request.full_url
        return response


def test_explicit_provider_fetch_and_policy_refusal():
    opener = FakeOpener()
    plant = fetch_plant_source(URL, opener=opener)
    assert plant.common_name == "Rose"
    assert plant.raw_data["catalog_import"]["retrieved_at"]
    assert plant.raw_data["catalog_import"]["verified_at"]
    forbidden = FakeOpener(policy=b"User-agent: *\nDisallow: /")
    with pytest.raises(ValueError, match="does not permit"):
        fetch_plant_source(URL, opener=forbidden)
    assert len(forbidden.calls) == 1


@pytest.mark.parametrize("url", ["http://www.promessedefleurs.com/", "https://localhost/", "file:///tmp/test", "https://user@www.promessedefleurs.com/"])
def test_unsupported_source_urls_refused(url):
    with pytest.raises(ValueError):
        validate_source_url(url)


def test_size_limit_and_network_failure_are_explicit():
    with pytest.raises(ValueError, match="size limit"):
        fetch_plant_source(URL, opener=FakeOpener(payload=b"x" * (4 * 1024 * 1024 + 1)))

    class Offline:
        def open(self, *_args, **_kwargs):
            raise URLError("offline")

    with pytest.raises(ValueError, match="local file") as raised:
        fetch_plant_source(URL, opener=Offline())
    assert raised.value.code == "SOURCE_NETWORK_ERROR"


def test_refresh_keeps_manual_fields_and_accepts_unchanged_source_fields():
    first = PlantSpeciesData("Rosa", "Rose", source_id="rose", max_height_cm=100,
                             max_spread_cm=60, raw_data={"future": {"keep": True}})
    current = mark_import_baseline(first)
    current.max_height_cm = 123  # Manual correction after import.
    current.raw_data["botanical_profile"] = {"cultivar": "My correction"}
    current.raw_data["catalog_import"]["future_provenance"] = {"keep": True}
    current.raw_data["botanical_calendar"] = {"flowering": {
        "months": [7], "source_text": "Juin", "manually_edited": True,
    }}
    incoming = deepcopy(first)
    incoming.max_height_cm = 200
    incoming.max_spread_cm = 90
    before = deepcopy(current.to_dict())
    candidate, differences = plan_source_refresh(current, incoming)
    assert current.to_dict() == before
    assert candidate.max_height_cm == 123
    assert candidate.max_spread_cm == 90
    assert candidate.raw_data["botanical_profile"]["cultivar"] == "My correction"
    assert candidate.raw_data["botanical_calendar"]["flowering"]["months"] == [7]
    assert candidate.raw_data["future"] == {"keep": True}
    assert candidate.raw_data["catalog_import"]["future_provenance"] == {"keep": True}
    assert any(diff["field"] == "max_height_cm" and diff["protected"] for diff in differences)


def test_near_names_never_authorize_refresh():
    with pytest.raises(ValueError, match="identity mismatch") as raised:
        plan_source_refresh(PlantSpeciesData("Rosa", "Rose", source_id="a"),
                            PlantSpeciesData("Rosa", "Rose", source_id="b"))
    assert raised.value.code == "SOURCE_IDENTITY_MISMATCH"


def test_manual_correction_survives_supplier_agreement_then_another_refresh():
    original = mark_import_baseline(PlantSpeciesData("Rosa", "Rose", source_id="rose", max_height_cm=100))
    original.max_height_cm = 123
    source = PlantSpeciesData("Rosa", "Rose", source_id="rose", max_height_cm=123)
    agreed, _ = plan_source_refresh(original, source)
    source.max_height_cm = 200
    refreshed, differences = plan_source_refresh(agreed, source)
    assert refreshed.max_height_cm == 123
    assert "max_height_cm" in refreshed.raw_data["catalog_import"]["manual_fields"]
    assert any(change["field"] == "max_height_cm" and change["protected"] for change in differences)


def test_explicit_reference_download_reuses_local_media_and_retains_credit(tmp_path):
    from PIL import Image

    from open_garden_planner.services.plant_library import PlantLibrary

    stream = BytesIO()
    Image.new("RGB", (24, 24), "green").save(stream, "PNG")
    library = PlantLibrary(tmp_path / "custom_plants.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose"))
    before = library.path.read_bytes()
    asset = fetch_reference_photo(URL, library.path, identity, credit="Fournisseur", opener=FakeOpener(payload=stream.getvalue()))
    assert asset["source_url"] == URL
    assert asset["credit"] == "Fournisseur"
    assert asset["kind"] == "reference"
    assert (tmp_path / asset["photo_path"]).read_bytes() == stream.getvalue()
    assert (tmp_path / asset["thumbnail_path"]).is_file()
    assert library.path.read_bytes() == before  # Association still needs a reviewed transaction.
    forbidden = FakeOpener(policy=b"User-agent: *\nDisallow: /")
    with pytest.raises(ValueError, match="does not permit"):
        fetch_reference_photo(URL, library.path, identity, opener=forbidden)
    assert len(forbidden.calls) == 1


def test_reference_photo_bytes_can_be_retrieved_and_imported_with_page_context(tmp_path):
    from PIL import Image

    from open_garden_planner.services.botanical_media import resolve_reference_media
    from open_garden_planner.services.plant_library import PlantLibrary

    stream = BytesIO()
    Image.new("RGB", (24, 24), "green").save(stream, "PNG")
    image_url = "https://www.promessedefleurs.com/media/rose.png"
    opener = FakeOpener(payloads={image_url: stream.getvalue()})
    payload = retrieve_reference_photo(image_url, opener=opener)

    library = PlantLibrary(tmp_path / "custom_plants.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose"))
    page_url = "https://www.promessedefleurs.com/rosier.html"
    asset = import_reference_photo_bytes(
        payload, library.path, identity, source_url=image_url,
        page_url=page_url, caption="Rose ancienne", credit="Promesse de Fleurs",
    )

    assert opener.calls == ["https://www.promessedefleurs.com/robots.txt", image_url]
    assert asset["source_url"] == image_url
    assert asset["source_page_url"] == page_url
    assert asset["caption"] == "Rose ancienne"
    assert resolve_reference_media(library.path, asset["photo_path"]).read_bytes() == payload


def test_multiple_page_images_raise_selection_before_any_media_is_written(tmp_path):
    from open_garden_planner.services.plant_library import PlantLibrary

    page = f'''<html><head><link rel="canonical" href="{URL}">
      <meta property="og:title" content="Rose ancienne">
      <meta property="og:image" content="/media/rose.jpg">
      <script type="application/ld+json">[{{"@type":"Product","name":"Rose",
        "url":"{URL}","image":["/media/rose.jpg"]}},
        {{"@type":"Product","name":"Tulipe recommandée",
         "url":"https://www.promessedefleurs.com/tulipe.html",
         "image":[{{"url":"/media/tulipe.jpg"}}]}}]</script>
      </head><body><h1>Rose ancienne</h1></body></html>'''.encode()
    opener = FakeOpener(payloads={URL: page})
    library = PlantLibrary(tmp_path / "custom_plants.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose"))
    plant = PlantSpeciesData("Rosa", "Rose", image_url="https://www.promessedefleurs.com/media/rose.jpg",
                            raw_data={"promesse_de_fleurs": {"source_url": URL,
                                      "image_url": "https://www.promessedefleurs.com/media/rose.jpg"}})

    with pytest.raises(ReferencePhotoSelectionRequired) as raised:
        fetch_reference_photo_for_plant(plant, library.path, identity, opener=opener)

    assert [candidate.url for candidate in raised.value.candidates] == [
        "https://www.promessedefleurs.com/media/rose.jpg",
        "https://www.promessedefleurs.com/media/tulipe.jpg",
    ]
    assert [candidate.caption for candidate in raised.value.candidates] == [
        "Rose ancienne", "Tulipe recommandée",
    ]
    assert all(candidate.source_url == URL for candidate in raised.value.candidates)
    assert opener.calls == ["https://www.promessedefleurs.com/robots.txt", URL]
    assert not (tmp_path / "botanical-media").exists()


@pytest.mark.parametrize("image_markup", [
    b'<meta property="og:image" content="https://outside.example/rose.jpg">',
    b"",
], ids=["external-host", "absent-image"])
def test_page_photo_fallback_ignores_external_hosts_and_returns_none_when_absent(
    tmp_path, image_markup,
):
    from open_garden_planner.services.plant_library import PlantLibrary

    no_image_page = _source_page().replace(
        b'<meta property="og:image" content="/media/rose.jpg">',
        image_markup,
    )
    opener = FakeOpener(payloads={URL: no_image_page})
    library = PlantLibrary(tmp_path / "custom_plants.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose"))
    plant = PlantSpeciesData("Rosa", "Rose", raw_data={"local_sheet": {"url": URL}})

    assert fetch_reference_photo_for_plant(plant, library.path, identity, opener=opener) is None
    assert opener.calls == ["https://www.promessedefleurs.com/robots.txt", URL]
    assert not (tmp_path / "botanical-media").exists()


def test_reference_photo_selection_exposes_no_more_than_twelve_safe_candidates(tmp_path):
    from open_garden_planner.services.plant_library import PlantLibrary

    products = [
        {"@type": "Product", "name": f"Plante {index}",
         "url": URL if index == 0 else f"https://www.promessedefleurs.com/plante-{index}.html",
         "image": f"/media/plante-{index}.jpg"}
        for index in range(16)
    ]
    page = (f'<html><head><link rel="canonical" href="{URL}">'
            f'<script type="application/ld+json">{json.dumps(products)}'
            "</script></head><body><h1>Plantes</h1></body></html>").encode()
    opener = FakeOpener(payloads={URL: page})
    library = PlantLibrary(tmp_path / "custom_plants.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose"))
    plant = PlantSpeciesData("Rosa", "Rose", raw_data={"local_sheet": {"url": URL}})

    with pytest.raises(ReferencePhotoSelectionRequired) as raised:
        fetch_reference_photo_for_plant(plant, library.path, identity, opener=opener)

    assert len(raised.value.candidates) == 12
    assert all(candidate.url.startswith("https://www.promessedefleurs.com/")
               for candidate in raised.value.candidates)
    assert opener.calls == ["https://www.promessedefleurs.com/robots.txt", URL]
    assert not (tmp_path / "botanical-media").exists()


def test_reference_photo_selection_filters_hosts_before_applying_twelve_choice_limit(tmp_path):
    from open_garden_planner.services.plant_library import PlantLibrary

    external_images = "".join(
        f'<meta property="og:image" content="https://outside.example/{index}.jpg">'
        for index in range(50)
    )
    products = [
        {"@type": "Product", "name": f"Plante {index}",
         "url": URL if index == 0 else f"https://www.promessedefleurs.com/plante-{index}.html",
         "image": f"/media/plante-{index}.jpg"}
        for index in range(13)
    ]
    page = (f'<html><head><link rel="canonical" href="{URL}">{external_images}'
            f'<script type="application/ld+json">{json.dumps(products)}'
            "</script></head><body><h1>Plantes</h1></body></html>").encode()
    opener = FakeOpener(payloads={URL: page})
    library = PlantLibrary(tmp_path / "custom_plants.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose"))
    plant = PlantSpeciesData("Rosa", "Rose", raw_data={"local_sheet": {"url": URL}})

    with pytest.raises(ReferencePhotoSelectionRequired) as raised:
        fetch_reference_photo_for_plant(plant, library.path, identity, opener=opener)

    assert len(raised.value.candidates) == 12
    assert [candidate.url for candidate in raised.value.candidates] == [
        f"https://www.promessedefleurs.com/media/plante-{index}.jpg"
        for index in range(12)
    ]
    assert opener.calls == ["https://www.promessedefleurs.com/robots.txt", URL]
    assert not any("outside.example" in request for request in opener.calls)
    assert not (tmp_path / "botanical-media").exists()


def test_reference_photo_fallback_rejects_mismatched_canonical_page(tmp_path):
    from open_garden_planner.services.plant_library import PlantLibrary

    other_url = "https://www.promessedefleurs.com/another-plant.html"
    opener = FakeOpener(payloads={URL: _source_page(other_url)})
    library = PlantLibrary(tmp_path / "custom_plants.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose"))
    plant = PlantSpeciesData("Rosa", "Rose", raw_data={"local_sheet": {"url": URL}})

    with pytest.raises(ValueError, match="identity differs"):
        fetch_reference_photo_for_plant(plant, library.path, identity, opener=opener)
    assert opener.calls == ["https://www.promessedefleurs.com/robots.txt", URL]
    assert not (tmp_path / "botanical-media").exists()


def test_reference_photo_fallback_classifies_malformed_page_as_parse_error():
    from open_garden_planner.services.plant_source_errors import SOURCE_PARSE_ERROR

    nested = "<div>" * 1400 + "<img src='/media/rose.jpg'>" + "</div>" * 1400
    page = f'''<html><head><link rel="canonical" href="{URL}">
      <meta property="og:site_name" content="Promesse de Fleurs"></head>
      <body>{nested}</body></html>'''.encode()
    plant = PlantSpeciesData("Rosa", "Rose", raw_data={"local_sheet": {"url": URL}})

    with pytest.raises(ValueError, match="too deeply nested") as raised:
        fetch_reference_photo_for_plant(plant, "unused", "unused", opener=FakeOpener(payload=page))
    assert raised.value.code == SOURCE_PARSE_ERROR
    assert raised.value.stage == "parse"
    assert raised.value.url == URL


def test_import_still_rejects_ambiguous_products_without_matching_urls():
    page = f'''<html><head><link rel="canonical" href="{URL}">
      <script type="application/ld+json">[{{"@type":"Product","name":"Rose"}},
        {{"@type":"Product","name":"Tulipe recommandée"}}]</script>
      </head><body><h1>Rose</h1></body></html>'''.encode()
    with pytest.raises(ValueError, match="multiple product records") as raised:
        fetch_plant_source(URL, opener=FakeOpener(payload=page))
    assert raised.value.code == "SOURCE_PRODUCT_AMBIGUOUS"


def test_import_selects_only_canonical_product_and_deduplicates_identical_jsonld():
    main = {
        "@type": "Product",
        "url": URL + "#offer",
        "name": "Rosier anglais",
        "description": "Description du rosier principal.",
        "image": "/media/rosier-principal.jpg",
    }
    recommendation = {
        "@type": "Product",
        "url": "https://www.promessedefleurs.com/rosier-anglais-compact.html",
        "name": "Rosier anglais compact",
        "description": "Description de la recommandation.",
        "image": "/media/rosier-recommande.jpg",
    }
    page = f'''<html><head><link rel="canonical" href="{URL}">
      <meta property="og:site_name" content="Promesse de Fleurs">
      <script type="application/ld+json">{json.dumps([main, recommendation, main])}</script>
      </head><body><h1>Rosier anglais</h1></body></html>'''.encode()

    plant = fetch_plant_source(URL, opener=FakeOpener(payload=page))
    source = plant.raw_data["promesse_de_fleurs"]
    assert source["product_json_ld"] == main
    assert source["source_url"] == URL
    assert plant.description == "Description du rosier principal."
    assert plant.image_url == "https://www.promessedefleurs.com/media/rosier-principal.jpg"
    assert plant.source_id == "local-sheet:" + hashlib.sha256(
        URL.encode("utf-8")
    ).hexdigest()[:32]


def test_import_excludes_recommendation_dom_fields_and_title_from_main_product():
    page = f'''<html><head><link rel="canonical" href="{URL}">
      <meta property="og:site_name" content="Promesse de Fleurs">
      <script type="application/ld+json">[
        {{"@type":"Product","url":"{URL}#main","name":"Rosier principal"}},
        {{"@type":"Product","url":"https://www.promessedefleurs.com/recommande.html",
          "name":"Rosier recommandé"}}]
      </script></head><body>
      <article itemscope itemtype="https://schema.org/Product"
          itemid="https://www.promessedefleurs.com/recommande.html#product">
        <h1>Rosier recommandé</h1><dl>
          <dt>Hauteur à maturité</dt><dd>40 cm</dd>
        </dl>
      </article>
      <aside><article itemscope itemtype="https://schema.org/Product"
          itemid="https://www.promessedefleurs.com/recommande.html#product">
        <h2>Autre recommandation</h2><dl>
          <dt>Période de floraison</dt><dd>Avril</dd>
        </dl>
      </article></aside>
      <main><h1>Rosier principal</h1></main>
      </body></html>'''.encode()

    plant = fetch_plant_source(URL, opener=FakeOpener(payload=page))
    assert plant.common_name == "Rosier principal"
    assert plant.max_height_cm is None
    assert "Hauteur à maturité" not in plant.raw_data["promesse_de_fleurs"]["fields"]
    from open_garden_planner.services.botanical_calendar import calendar_for_species

    flowering = calendar_for_species(plant).flowering
    assert flowering.months is None
    assert flowering.source_text == ""


def test_canonical_product_scope_wins_inside_aside_and_keeps_secondary_product_out():
    page = f'''<html><head><link rel="canonical" href="{URL}">
      <meta property="og:site_name" content="Promesse de Fleurs">
      <script type="application/ld+json">[
        {{"@type":"Product","url":"{URL}","name":"Rosier principal"}},
        {{"@type":"Product","url":"https://www.promessedefleurs.com/recommande.html",
          "name":"Rosier recommandé"}}]
      </script></head><body>
      <aside class="product-sidebar"><div class="details">
        <article itemscope itemtype="https://schema.org/Product" itemid="{URL}#product">
          <h1>Rosier principal</h1><dl>
            <dt>Hauteur à maturité</dt><dd>60 cm</dd>
          </dl>
        </article>
      </div><article itemscope itemtype="https://schema.org/Product"
          itemid="https://www.promessedefleurs.com/recommande.html#product">
        <h1>Rosier recommandé</h1><dl>
          <dt>Période de floraison</dt><dd>Avril</dd>
        </dl>
      </article></aside>
      </body></html>'''.encode()

    plant = fetch_plant_source(URL, opener=FakeOpener(payload=page))
    assert plant.common_name == "Rosier principal"
    assert plant.max_height_cm == 60
    assert "Période de floraison" not in plant.raw_data["promesse_de_fleurs"]["fields"]


def test_canonical_aside_product_scope_keeps_its_fields_and_prunes_secondary_product():
    page = f'''<html><head><link rel="canonical" href="{URL}">
      <meta property="og:site_name" content="Promesse de Fleurs">
      <script type="application/ld+json">[
        {{"@type":"Product","url":"{URL}","name":"Rosier principal"}},
        {{"@type":"Product","url":"https://www.promessedefleurs.com/recommande.html",
          "name":"Rosier recommandé"}}]
      </script></head><body>
      <aside itemscope itemtype="https://schema.org/Product" itemid="{URL}#product">
        <h1>Rosier principal</h1><dl>
          <dt>Hauteur à maturité</dt><dd>60 cm</dd>
        </dl>
        <article itemscope itemtype="https://schema.org/Product"
            itemid="https://www.promessedefleurs.com/recommande.html#product">
          <h1>Rosier recommandé</h1><dl>
            <dt>Période de floraison</dt><dd>Avril</dd>
          </dl>
        </article>
      </aside></body></html>'''.encode()

    plant = fetch_plant_source(URL, opener=FakeOpener(payload=page))
    assert plant.common_name == "Rosier principal"
    assert plant.max_height_cm == 60
    assert "Période de floraison" not in plant.raw_data["promesse_de_fleurs"]["fields"]


@pytest.mark.parametrize("reference_kind", ["@id", "mainEntityOfPage"])
def test_product_id_and_main_entity_page_can_link_a_product_to_the_canonical_url(reference_kind):
    main = {
        "@type": "Product",
        "name": "Rosier canonique",
        "url": "https://www.promessedefleurs.com/autre-reference.html#offer",
    }
    if reference_kind == "@id":
        main["@id"] = URL + "#product"
    else:
        main["mainEntityOfPage"] = {"@type": "WebPage", "@id": URL + "#webpage"}
    recommendation = {
        "@type": "Product",
        "url": "https://www.promessedefleurs.com/rosier-proche.html",
        "name": "Rosier canonique compact",
    }
    page = f'''<html><head><link rel="canonical" href="{URL}">
      <meta property="og:site_name" content="Promesse de Fleurs">
      <script type="application/ld+json">{json.dumps([recommendation, main])}</script>
      </head><body><h1>Rosier canonique</h1></body></html>'''.encode()

    plant = fetch_plant_source(URL, opener=FakeOpener(payload=page))
    assert plant.raw_data["promesse_de_fleurs"]["product_json_ld"] == main


def test_import_keeps_similarly_named_products_ambiguous_without_url_evidence():
    page = f'''<html><head><link rel="canonical" href="{URL}">
      <meta property="og:site_name" content="Promesse de Fleurs">
      <script type="application/ld+json">[
        {{"@type":"Product","name":"Rosier anglais",
          "url":"https://www.promessedefleurs.com/rosier-anglais.html"}},
        {{"@type":"Product","name":"Rosier anglais compact",
          "url":"https://www.promessedefleurs.com/rosier-anglais-compact.html"}}]
      </script></head><body><h1>Rosier anglais</h1></body></html>'''.encode()

    from open_garden_planner.services.plant_source_errors import SOURCE_PRODUCT_AMBIGUOUS

    with pytest.raises(ValueError, match="multiple product records") as raised:
        fetch_plant_source(URL, opener=FakeOpener(payload=page))
    assert raised.value.code == SOURCE_PRODUCT_AMBIGUOUS
    assert "Rosier anglais (https://www.promessedefleurs.com/rosier-anglais.html)" in str(raised.value)
    assert "Rosier anglais compact (https://www.promessedefleurs.com/rosier-anglais-compact.html)" in str(raised.value)


@pytest.mark.parametrize(
    ("failure", "expected_code", "expected_status"),
    [
        ("http", "SOURCE_HTTP_ERROR", 429),
        ("timeout", "SOURCE_TIMEOUT", None),
        ("offline", "SOURCE_NETWORK_ERROR", None),
    ],
)
def test_worker_error_info_reports_stable_network_code_and_sanitized_url(
    failure, expected_code, expected_status,
):
    url = URL + "?token=secret&variant=rose"

    class FailingOpener(FakeOpener):
        def open(self, request, timeout):
            self.calls.append(request.full_url)
            if request.full_url.endswith("robots.txt"):
                response = Response(self.policy)
                response.url = request.full_url
                return response
            if failure == "http":
                raise HTTPError(request.full_url, 429, "Too Many Requests", {}, None)
            if failure == "timeout":
                raise URLError(TimeoutError("timed out"))
            raise URLError("offline")

    with pytest.raises(ValueError) as raised:
        fetch_plant_source(url, opener=FailingOpener())
    info = plant_source_error_info(raised.value, url=url)
    assert info.code == expected_code
    assert info.http_status == expected_status
    assert info.url == URL + "?token=%5Bredacted%5D&variant=rose"
    assert "secret" not in info.message
    assert info.stage == "fetch"


def test_worker_error_info_reports_robots_policy_denial_with_url():
    url = URL + "?api_key=private"
    opener = FakeOpener(policy=b"User-agent: *\nDisallow: /")
    with pytest.raises(ValueError) as raised:
        fetch_plant_source(url, opener=opener)
    info = plant_source_error_info(raised.value, url=url)
    assert info.code == "SOURCE_ROBOTS_DENIED"
    assert info.url == URL + "?api_key=%5Bredacted%5D"
    assert info.stage == "robots"
    assert info.http_status is None


def test_worker_error_url_removes_userinfo_and_password():
    from open_garden_planner.services.plant_source_errors import (
        PlantSourceErrorInfo,
        format_plant_source_error,
        sanitize_url,
    )

    assert sanitize_url("https://gardener:pass@www.promessedefleurs.com/rose?key=hidden") == (
        "https://www.promessedefleurs.com/rose?key=%5Bredacted%5D"
    )
    info = PlantSourceErrorInfo("SOURCE_UNKNOWN", "legacy detail", URL, stage="unknown")
    assert plant_source_error_info(info) is info
    legacy = plant_source_error_info(ValueError("legacy failure"), url=URL + "?token=secret")
    assert legacy.code == "SOURCE_UNKNOWN"
    assert legacy.message == "legacy failure"
    assert legacy.url == URL + "?token=%5Bredacted%5D"
    assert format_plant_source_error(legacy) == "[SOURCE_UNKNOWN] legacy failure"


def test_provider_redirect_cannot_fetch_a_path_outside_the_robots_check():
    from urllib.request import Request

    from open_garden_planner.services.plant_source_import import _ProviderRedirects

    allowed = "https://www.promessedefleurs.com/allowed.png"
    forbidden = "https://www.promessedefleurs.com/blocked/photo.png"
    with pytest.raises(ValueError, match="redirects are restricted"):
        _ProviderRedirects().redirect_request(Request(allowed), None, 302, "Found", {}, forbidden)

    class RedirectedResponse(FakeOpener):
        def open(self, request, timeout):
            response = super().open(request, timeout)
            if not request.full_url.endswith("robots.txt"):
                response.url = forbidden
            return response

    with pytest.raises(ValueError, match="redirects are restricted"):
        fetch_plant_source(URL, opener=RedirectedResponse())


def test_refresh_reports_changed_source_traits_and_preserves_future_period():
    original = PlantSpeciesData("Rosa", "Rose", source_id="same", raw_data={
        "local_sheet": {"characteristics": {"Cultivar": "Ancien", "Période de floraison": "Mai"}},
        "botanical_profile": {"cultivar": "Correction personnelle"},
        "botanical_calendar": {"pruning": None},
    })
    incoming = PlantSpeciesData("Rosa", "Rose", source_id="same", raw_data={
        "local_sheet": {"characteristics": {"Cultivar": "Nouveau", "Période de floraison": "Juin"}},
    })
    candidate, differences = plan_source_refresh(original, incoming)
    cultivar = next(change for change in differences if change["field"] == "profile.cultivar")
    assert cultivar["protected"]
    assert cultivar["current"] == "Correction personnelle"
    assert cultivar["source"] == "Nouveau"
    assert candidate.raw_data["botanical_calendar"]["pruning"] is None
    assert candidate.raw_data["botanical_profile"]["cultivar"] == "Correction personnelle"


def _source_page(url=URL):
    return f'''<!doctype html><html><head>
      <link rel="canonical" href="{url}">
      <meta property="og:site_name" content="Promesse de Fleurs">
      <meta property="og:title" content="Rose ancienne">
      <meta property="og:description" content="Rosier de jardin.">
      <meta property="og:image" content="/media/rose.jpg">
      <script type="application/ld+json">{{"@type":"Product","name":"Rose"}}</script>
      </head><body><h1>Rose ancienne</h1><dl>
      <dt>Famille</dt><dd>Rosaceae</dd>
      <dt>Cultivar</dt><dd>Nouveau</dd>
      <dt>Hauteur à maturité</dt><dd>1,5 m</dd>
      <dt>Période de floraison</dt><dd>Mai à Octobre</dd>
      <dt>Période de semis</dt><dd>Février à Mars</dd>
      </dl></body></html>'''.encode()


def test_provider_page_retains_the_source_photo_url_separately():
    plant = fetch_plant_source(URL, opener=FakeOpener(payload=_source_page()))
    assert plant.image_url == "https://www.promessedefleurs.com/media/rose.jpg"
    assert plant.raw_data["promesse_de_fleurs"]["image_url"] == plant.image_url


def test_legacy_promesse_json_url_matches_the_same_provider_html_source():
    from hashlib import sha256

    source_id = "local-sheet:" + sha256(URL.encode("utf-8")).hexdigest()[:32]
    legacy = mark_import_baseline(PlantSpeciesData(
        "Rosa", "Rose", data_source="custom", source_id=source_id,
        raw_data={"local_sheet": {"url": URL, "characteristics": {}}},
    ))
    incoming = fetch_plant_source(URL, opener=FakeOpener(payload=_source_page()))

    assert source_identity(legacy) == source_identity(incoming)
    updated, _changes = plan_source_update(legacy, incoming)
    assert updated.source_id == source_id
    assert updated.data_source == "custom"
    assert updated.raw_data["local_sheet"]["url"] == URL
    assert updated.raw_data["promesse_de_fleurs"]["image_url"] == incoming.image_url


def test_enrichment_pins_existing_traits_without_hiding_provider_payload():
    from open_garden_planner.services.botanical_profile import source_trait, trait

    existing = PlantSpeciesData(
        "Rosa", "Rose", data_source="custom", source_id="library-uuid",
        raw_data={"future": {"keep": True}, "local_sheet": {"characteristics": {
            "Cultivar": "Ancien",
        }}, "botanical_profile": {"future_trait": {"keep": True}}},
    )
    incoming = fetch_plant_source(URL, opener=FakeOpener(payload=_source_page()))

    candidate, _changes = plan_source_update(existing, incoming)

    assert trait(existing, "cultivar") == "Ancien"
    assert trait(candidate, "cultivar") == "Ancien"
    assert source_trait(candidate, "cultivar") == "Nouveau"
    assert candidate.raw_data["promesse_de_fleurs"]["fields"]["Cultivar"] == ["Nouveau"]
    assert candidate.raw_data["local_sheet"]["characteristics"]["Cultivar"] == "Ancien"
    assert candidate.raw_data["botanical_profile"]["future_trait"] == {"keep": True}

    newer_source = fetch_plant_source(URL, opener=FakeOpener(
        payload=_source_page().replace(b"Nouveau", b"Nouveau 2")
    ))
    refreshed, _changes = plan_source_update(candidate, newer_source)
    assert trait(refreshed, "cultivar") == "Ancien"
    assert source_trait(refreshed, "cultivar") == "Nouveau 2"

    deliberate_blank = PlantSpeciesData(
        "Rosa", "Rose", data_source="custom", source_id="blank-library-uuid",
        raw_data={"local_sheet": {"characteristics": {"Cultivar": "Ancien"}},
                  "botanical_profile": {"cultivar": ""}},
    )
    blank_candidate, _changes = plan_source_update(deliberate_blank, incoming)
    assert trait(blank_candidate, "cultivar") == ""


def test_legacy_same_url_refresh_prefers_new_provider_value_to_old_raw_value():
    from hashlib import sha256

    from open_garden_planner.services.botanical_profile import source_trait, trait

    source_id = "local-sheet:" + sha256(URL.encode("utf-8")).hexdigest()[:32]
    legacy = mark_import_baseline(PlantSpeciesData(
        "Rosa", "Rose ancienne", data_source="custom", source_id=source_id,
        raw_data={"local_sheet": {"url": URL, "characteristics": {"Cultivar": "Ancien"}}},
    ))
    incoming = fetch_plant_source(URL, opener=FakeOpener(payload=_source_page()))

    updated, _changes = plan_source_update(legacy, incoming)
    assert trait(updated, "cultivar") == "Nouveau"
    assert source_trait(updated, "cultivar") == "Nouveau"
    assert updated.raw_data["local_sheet"]["characteristics"]["Cultivar"] == "Ancien"

    newer_source = fetch_plant_source(URL, opener=FakeOpener(
        payload=_source_page().replace(b"Nouveau", b"Nouveau 2")
    ))
    refreshed, _changes = plan_source_update(updated, newer_source)
    assert trait(refreshed, "cultivar") == "Nouveau 2"
    assert refreshed.raw_data["local_sheet"]["characteristics"]["Cultivar"] == "Ancien"
    assert refreshed.raw_data["catalog_source_history"]


def test_custom_enrichment_is_fill_only_and_later_refresh_uses_provider_identity():
    incoming = fetch_plant_source(URL, opener=FakeOpener(payload=_source_page()))
    existing = PlantSpeciesData(
        "Rosa", "Rose ancienne", data_source="custom", source_id="library-uuid",
        family="Famille personnelle", max_height_cm=130,
        image_url="https://example.test/photo.jpg", description="",
        raw_data={
            "future": {"keep": True},
            "botanical_profile": {"cultivar": "Correction personnelle"},
            "botanical_calendar": {"flowering": {
                "months": [5], "source_text": "Mai", "manually_edited": True,
                "future_period_field": "keep",
            }},
        },
    )
    before = deepcopy(existing.to_dict())

    candidate, changes = plan_source_update(existing, incoming)

    assert existing.to_dict() == before
    assert candidate.source_id == "library-uuid"
    assert candidate.data_source == "custom"
    assert candidate.family == "Famille personnelle"
    assert candidate.max_height_cm == 130
    assert candidate.image_url == "https://example.test/photo.jpg"
    assert candidate.description == "Rosier de jardin."
    assert candidate.raw_data["promesse_de_fleurs"]["image_url"] == incoming.image_url
    assert candidate.raw_data["future"] == {"keep": True}
    assert candidate.raw_data["botanical_profile"]["cultivar"] == "Correction personnelle"
    assert candidate.raw_data["botanical_calendar"]["flowering"] == {
        "months": [5], "source_text": "Mai", "manually_edited": True,
        "future_period_field": "keep",
    }
    assert candidate.raw_data["botanical_calendar"]["sowing"]["months"] == [2, 3]
    assert source_identity(candidate) == source_identity(incoming)
    assert "common_name" in candidate.raw_data["catalog_enrichment"]["manual_fields"]
    assert any(item["field"] == "description" and not item["protected"] for item in changes)
    assert any(item["field"] == "family" and item["protected"] for item in changes)
    assert any(item["field"] == "image_url" and item["protected"] for item in changes)

    refreshed_source = deepcopy(incoming)
    refreshed_source.description = "Description actualisée."
    refreshed_source.common_name = "Rose nouvelle"
    refreshed_source.raw_data["promesse_de_fleurs"]["fields"]["Période de floraison"] = [
        "Juin à Octobre"
    ]
    refreshed, refresh_changes = plan_source_update(candidate, refreshed_source)
    assert refreshed.description == "Description actualisée."
    assert refreshed.common_name == "Rose ancienne"
    assert refreshed.raw_data["botanical_calendar"]["flowering"]["months"] == [5]
    assert any(item["field"] == "common_name" and item["protected"] for item in refresh_changes)
    assert refreshed.source_id == "library-uuid"
    assert refreshed.data_source == "custom"

    other_source = fetch_plant_source(
        "https://www.promessedefleurs.com/rosier-autre.html",
        opener=FakeOpener(payload=_source_page("https://www.promessedefleurs.com/rosier-autre.html")),
    )
    with pytest.raises(ValueError, match="identity mismatch") as raised:
        plan_source_update(candidate, other_source)
    assert raised.value.code == "SOURCE_IDENTITY_MISMATCH"


def test_enrichment_refuses_missing_provider_identity_and_invalid_source_payload():
    existing = PlantSpeciesData("Rosa", "Rose", data_source="custom", source_id="library-id")
    with pytest.raises(ValueError, match="stable provider identity"):
        plan_source_update(existing, PlantSpeciesData("Rosa", "Rose", source_id="similar"))

    incoming = fetch_plant_source(URL, opener=FakeOpener(payload=_source_page()))
    incoming.raw_data["promesse_de_fleurs"]["source_url"] = "https://example.test/rose"
    with pytest.raises(ValueError):
        plan_source_update(existing, incoming)

    incoming = fetch_plant_source(URL, opener=FakeOpener(payload=_source_page()))
    incoming.raw_data["promesse_de_fleurs"]["source_url"] = (
        "https://www.promessedefleurs.com/autre-rose.html"
    )
    with pytest.raises(ValueError) as raised:
        plan_source_update(existing, incoming)
    assert raised.value.code == "SOURCE_IDENTITY_MISMATCH"


def test_reference_photo_helper_uses_suggested_image_before_personal_image(tmp_path):
    from PIL import Image

    from open_garden_planner.services.plant_library import PlantLibrary

    image = BytesIO()
    Image.new("RGB", (24, 24), "green").save(image, "PNG")
    photo_url = "https://www.promessedefleurs.com/media/rose.png"
    library = PlantLibrary(tmp_path / "custom_plants.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose"))
    plant = PlantSpeciesData("Rosa", "Rose", image_url="https://example.test/personal.jpg",
                             raw_data={"promesse_de_fleurs": {"image_url": photo_url}})
    opener = FakeOpener(payloads={photo_url: image.getvalue()})

    asset = fetch_reference_photo_for_plant(plant, library.path, identity, opener=opener)

    assert asset["source_url"] == photo_url
    assert asset["credit"] == "Promesse de Fleurs"
    assert asset["caption"] == "Rose"
    assert opener.calls == ["https://www.promessedefleurs.com/robots.txt", photo_url]


def test_reference_photo_helper_can_recover_old_source_image_and_reject_external_host(tmp_path):
    from PIL import Image

    from open_garden_planner.services.plant_library import PlantLibrary

    image = BytesIO()
    Image.new("RGB", (24, 24), "purple").save(image, "PNG")
    photo_url = "https://www.promessedefleurs.com/media/rose.jpg"
    library = PlantLibrary(tmp_path / "custom_plants.json")
    identity = library.add_plant(PlantSpeciesData("Rosa", "Rose"))
    old_plant = PlantSpeciesData("Rosa", "Rose", raw_data={"local_sheet": {"url": URL}})
    opener = FakeOpener(payloads={URL: _source_page(), photo_url: image.getvalue()})

    asset = fetch_reference_photo_for_plant(old_plant, library.path, identity, opener=opener)

    assert asset["source_url"] == photo_url
    assert asset["source_page_url"] == URL
    assert opener.calls == [
        "https://www.promessedefleurs.com/robots.txt", URL,
        "https://www.promessedefleurs.com/robots.txt", photo_url,
    ]
    forbidden = FakeOpener()
    old_plant.raw_data["promesse_de_fleurs"] = {"image_url": "https://example.test/plant.jpg"}
    with pytest.raises(ValueError, match="Promesse de Fleurs"):
        fetch_reference_photo_for_plant(old_plant, library.path, identity, opener=forbidden)
    assert forbidden.calls == []


def _variant_page(products):
    return (f'<html><head><link rel="canonical" href="{URL}">'
            f'<script type="application/ld+json">{json.dumps(products)}</script>'
            '</head><body><h1>Allium Gladiator</h1><dl>'
            '<dt>Nom botanique</dt><dd>Allium Gladiator</dd>'
            '<dt>Hauteur à maturité</dt><dd>1,20 m</dd>'
            '</dl></body></html>').encode()


def _allium_variants():
    variants = []
    for index, packaging in enumerate(("Bulbe calibre 20+", "Pot de 2L/3L calibre 18",
                                       "Pot de 12 cm / 13 cm calibre 18")):
        reference = {"@type": "Product", "@id": f"{URL}#product-variant-{index}"}
        variants.append({
            **reference, "url": URL, "name": f"Allium Gladiator - {packaging}",
            "description": f"Allium Gladiator - {packaging}",
            "disambiguatingDescription": packaging,
            "mainEntityOfPage": {"@type": "WebPage", "@id": URL + "#webpage"},
            "image": "/media/allium.jpg",
            "offers": [{"@type": "Offer", "itemOffered": reference}],
        })
    return variants


def test_import_accepts_explicit_packaging_variants_and_keeps_original_records(tmp_path):
    from open_garden_planner.services.local_plant_sources import read_local_plant_sources

    variants = _allium_variants()
    page = _variant_page(variants)
    before = deepcopy(variants)
    plant = fetch_plant_source(URL, opener=FakeOpener(payload=page))
    assert plant.common_name == "Allium Gladiator"
    assert plant.scientific_name == "Allium Gladiator"
    assert plant.max_height_cm == 120
    assert plant.description == ""
    assert plant.image_url == "https://www.promessedefleurs.com/media/allium.jpg"
    raw = plant.raw_data["promesse_de_fleurs"]
    assert raw["product_json_ld"] == variants[0]
    assert raw["product_variants_json_ld"] == before
    assert raw["source_url"] == URL
    path = tmp_path / "allium.html"
    path.write_bytes(page)
    local, = read_local_plant_sources(path)
    assert local.source_id == plant.source_id
    assert local.raw_data["promesse_de_fleurs"]["product_variants_json_ld"] == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["allium.html"]


def test_product_reference_is_not_a_second_definition():
    product = {"@type": "Product", "@id": URL + "#product", "url": URL, "name": "Rose"}
    product["offers"] = {"@type": "Offer", "itemOffered": {
        "@type": "Product", "@id": URL + "#product"}}
    plant = fetch_plant_source(URL, opener=FakeOpener(payload=_variant_page([product])))
    assert plant.raw_data["promesse_de_fleurs"]["product_json_ld"] == product


@pytest.mark.parametrize("change", ["name", "url", "packaging", "id", "page", "heading", "extra"])
def test_import_rejects_unproven_or_conflicting_variants(change):
    variants = _allium_variants()
    if change == "name":
        variants[1]["name"] = "Allium Globemaster - " + variants[1]["disambiguatingDescription"]
    elif change == "url":
        variants[1]["url"] = "https://www.promessedefleurs.com/globemaster.html"
    elif change == "packaging":
        del variants[1]["disambiguatingDescription"]
    elif change == "id":
        variants[1]["@id"] = URL + "#other-product"
    elif change == "page":
        del variants[1]["mainEntityOfPage"]
    elif change == "extra":
        variants.append({"@type": "Product", "url": URL, "name": "Autre plante"})
    page = _variant_page(variants)
    if change == "heading":
        page = page.replace(b"<h1>Allium Gladiator</h1>", b"<h1>Allium Globemaster</h1>")
    with pytest.raises(ValueError) as raised:
        fetch_plant_source(URL, opener=FakeOpener(payload=page))
    assert raised.value.code == "SOURCE_PRODUCT_AMBIGUOUS"


def test_variant_import_excludes_recommendations_and_conflicting_fallback_images():
    variants = _allium_variants()
    variants[1]["image"] = "/media/packaging.jpg"
    variants.append({"@type": "Product", "url": "https://www.promessedefleurs.com/other.html",
                     "name": "Autre plante", "image": "/media/other.jpg"})
    plant = fetch_plant_source(URL, opener=FakeOpener(payload=_variant_page(variants)))
    assert plant.image_url == ""
    assert len(plant.raw_data["promesse_de_fleurs"]["product_variants_json_ld"]) == 3


def test_explicit_conflict_choices_survive_refresh_and_release_source_protection():
    from open_garden_planner.services.botanical_profile import trait
    from open_garden_planner.services.plant_source_import import resolve_source_choices

    incoming = fetch_plant_source(URL, opener=FakeOpener(payload=_source_page()))
    existing = deepcopy(incoming)
    existing.max_height_cm = 123
    existing.raw_data["botanical_profile"] = {"cultivar": "Personnel", "future": "keep"}
    existing.raw_data["botanical_calendar"] = {"flowering": {
        "months": [4], "source_text": "Avril", "manually_edited": True, "future": 17,
    }}
    original = deepcopy(existing.to_dict())
    offered = deepcopy(incoming.to_dict())
    choices = {"max_height_cm": True, "profile.cultivar": True, "calendar.flowering": True}
    accepted = resolve_source_choices(existing, incoming, choices)
    assert accepted.max_height_cm == 150
    assert trait(accepted, "cultivar") == "Nouveau"
    assert accepted.raw_data["botanical_profile"]["future"] == "keep"
    assert accepted.raw_data["botanical_calendar"]["flowering"]["months"] == [5, 6, 7, 8, 9, 10]
    assert accepted.raw_data["botanical_calendar"]["flowering"]["future"] == 17
    assert not accepted.raw_data["botanical_calendar"]["flowering"]["manually_edited"]
    retained = resolve_source_choices(existing, incoming, dict.fromkeys(choices, False))
    assert retained.max_height_cm == 123
    assert trait(retained, "cultivar") == "Personnel"
    assert retained.raw_data["botanical_calendar"]["flowering"]["months"] == [4]
    incoming.max_height_cm = 200
    incoming.raw_data["promesse_de_fleurs"]["fields"]["Cultivar"] = ["Suivant"]
    incoming.raw_data["promesse_de_fleurs"]["fields"]["Période de floraison"] = ["Juillet"]
    following, _ = plan_source_refresh(accepted, incoming)
    assert following.max_height_cm == 200
    assert trait(following, "cultivar") == "Suivant"
    assert following.raw_data["botanical_calendar"]["flowering"]["months"] == [7]
    following, _ = plan_source_refresh(retained, incoming)
    assert following.max_height_cm == 123
    assert trait(following, "cultivar") == "Personnel"
    assert following.raw_data["botanical_calendar"]["flowering"]["months"] == [4]
    assert existing.to_dict() == original
    assert offered["max_height_cm"] == 150


def test_retaining_automatic_updates_pins_the_old_values():
    from open_garden_planner.services.plant_source_import import resolve_source_choices

    existing = fetch_plant_source(URL, opener=FakeOpener(payload=_source_page()))
    incoming = deepcopy(existing)
    incoming.max_height_cm = 200
    incoming.raw_data["promesse_de_fleurs"]["fields"]["Période de floraison"] = ["Juillet"]
    retained = resolve_source_choices(existing, incoming, {
        "max_height_cm": False, "calendar.flowering": False,
    })
    # Persistence of the explicit refusal, even across a supplier agreement.
    agreed, _ = plan_source_refresh(retained, existing)
    following, _ = plan_source_refresh(agreed, incoming)
    assert following.max_height_cm == 150
    assert following.raw_data["botanical_calendar"]["flowering"]["months"] == [5, 6, 7, 8, 9, 10]


@pytest.mark.parametrize("choices", [{"source_id": True}, {"max_height_cm": "new"}])
def test_source_choices_refuse_unknown_fields_and_non_boolean_choices(choices):
    from open_garden_planner.services.plant_source_import import resolve_source_choices

    incoming = fetch_plant_source(URL, opener=FakeOpener(payload=_source_page()))
    existing = deepcopy(incoming)
    existing.max_height_cm = 123
    with pytest.raises(ValueError, match="Invalid source conflict choices"):
        resolve_source_choices(existing, incoming, choices)



def test_keep_current_profile_choice_preserves_unknown_override_format():
    from open_garden_planner.services.plant_source_import import resolve_source_choices

    incoming = fetch_plant_source(URL, opener=FakeOpener(payload=_source_page()))
    existing = deepcopy(incoming)
    future = {"future_format": "Personnel", "version": 2}
    existing.raw_data["botanical_profile"] = {"cultivar": future}
    _, changes = plan_source_update(existing, incoming)
    cultivar = next(change for change in changes if change["field"] == "profile.cultivar")
    assert cultivar["current"] == future
    assert cultivar["protected"]
    retained = resolve_source_choices(existing, incoming, {"profile.cultivar": False})
    assert retained.raw_data["botanical_profile"]["cultivar"] == future
    accepted = resolve_source_choices(existing, incoming, {"profile.cultivar": True})
    assert "cultivar" not in accepted.raw_data["botanical_profile"]
    assert existing.raw_data["botanical_profile"]["cultivar"] == future



def test_accepting_empty_source_trait_masks_history_but_follows_future_source():
    from open_garden_planner.services.botanical_profile import trait
    from open_garden_planner.services.plant_source_import import resolve_source_choices

    existing = fetch_plant_source(URL, opener=FakeOpener(payload=_source_page()))
    existing.raw_data["local_sheet"] = {"characteristics": {"Cultivar": ["Historique"]}}
    incoming = deepcopy(existing)
    incoming.raw_data.pop("local_sheet")
    incoming.raw_data["promesse_de_fleurs"]["fields"].pop("Cultivar")
    accepted = resolve_source_choices(existing, incoming, {"profile.cultivar": True})
    assert trait(accepted, "cultivar") == ""
    assert accepted.raw_data["local_sheet"] == existing.raw_data["local_sheet"]
    empty_refresh, _ = plan_source_refresh(accepted, incoming)
    assert trait(empty_refresh, "cultivar") == ""
    incoming.raw_data["promesse_de_fleurs"]["fields"]["Cultivar"] = ["Suivant"]
    following, changes = plan_source_refresh(empty_refresh, incoming)
    assert trait(following, "cultivar") == "Suivant"
    change = next(row for row in changes if row["field"] == "profile.cultivar")
    assert not change["protected"]
    assert following.raw_data["local_sheet"] == existing.raw_data["local_sheet"]
    # A later personal edit regains protection despite the earlier source choice.
    accepted.raw_data["botanical_profile"]["cultivar"] = "Correction personnelle"
    edited, _ = plan_source_refresh(accepted, incoming)
    assert trait(edited, "cultivar") == "Correction personnelle"
    assert "cultivar" not in edited.raw_data["catalog_import"]["source_profile_values"]
