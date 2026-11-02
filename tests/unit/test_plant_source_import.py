"""Explicit source imports do not mutate the library and preserve corrections."""
from copy import deepcopy
from io import BytesIO
from urllib.error import URLError

import pytest

from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services.plant_source_import import (
    fetch_plant_source,
    fetch_reference_photo,
    fetch_reference_photo_for_plant,
    mark_import_baseline,
    plan_source_refresh,
    plan_source_update,
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

    with pytest.raises(ValueError, match="local file"):
        fetch_plant_source(URL, opener=Offline())


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
    with pytest.raises(ValueError, match="identity mismatch"):
        plan_source_refresh(PlantSpeciesData("Rosa", "Rose", source_id="a"),
                            PlantSpeciesData("Rosa", "Rose", source_id="b"))


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
    with pytest.raises(ValueError, match="identity mismatch"):
        plan_source_update(candidate, other_source)


def test_enrichment_refuses_missing_provider_identity_and_invalid_source_payload():
    existing = PlantSpeciesData("Rosa", "Rose", data_source="custom", source_id="library-id")
    with pytest.raises(ValueError, match="stable provider identity"):
        plan_source_update(existing, PlantSpeciesData("Rosa", "Rose", source_id="similar"))

    incoming = fetch_plant_source(URL, opener=FakeOpener(payload=_source_page()))
    incoming.raw_data["promesse_de_fleurs"]["source_url"] = "https://example.test/rose"
    with pytest.raises(ValueError):
        plan_source_update(existing, incoming)


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
    assert opener.calls == [
        "https://www.promessedefleurs.com/robots.txt", URL,
        "https://www.promessedefleurs.com/robots.txt", photo_url,
    ]
    forbidden = FakeOpener()
    old_plant.raw_data["promesse_de_fleurs"] = {"image_url": "https://example.test/plant.jpg"}
    with pytest.raises(ValueError, match="Promesse de Fleurs"):
        fetch_reference_photo_for_plant(old_plant, library.path, identity, opener=forbidden)
    assert forbidden.calls == []
