"""Explicit source imports do not mutate the library and preserve corrections."""
from copy import deepcopy
from io import BytesIO
from urllib.error import URLError

import pytest

from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services.plant_source_import import (
    fetch_plant_source,
    fetch_reference_photo,
    mark_import_baseline,
    plan_source_refresh,
    validate_source_url,
)

URL = "https://www.promessedefleurs.com/rosier.html"


class Response(BytesIO):
    headers = {}
    url = URL


class FakeOpener:
    def __init__(self, policy=b"User-agent: *\nAllow: /", payload=None):
        self.policy = policy
        self.payload = payload or (
            f'<html><head><link rel="canonical" href="{URL}">'
            '<meta property="og:site_name" content="Promesse de Fleurs">'
            '<script type="application/ld+json">{"@type":"Product","name":"Rose"}</script>'
            '</head><body><h1>Rose</h1></body></html>'
        ).encode()
        self.calls = []

    def open(self, request, timeout):
        self.calls.append(request.full_url)
        assert timeout == 12
        response = Response(self.policy if request.full_url.endswith("robots.txt") else self.payload)
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
