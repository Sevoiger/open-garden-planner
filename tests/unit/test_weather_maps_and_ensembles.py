"""Product coverage, run identity and the statistical meaning of ensembles."""

import json
from pathlib import Path

import pytest

from open_garden_planner.services.weather_ensembles import normalize_ensemble
from open_garden_planner.services.weather_maps import (
    fetch_product,
    page_url,
    published_maps,
    validate_groups,
)


def image(family, filename, run="2026102900"):
    return f"https://modeles7.meteociel.fr/modeles/{family}/runs/{run}/{filename}.png?29-02"


def test_entire_supplied_arome_catalogue_and_cumuls_are_configured():
    config = json.loads((Path(__file__).resolve().parents[2] / "weather/config.json").read_text())
    groups = config["meteociel"]["groups"]
    catalogue = json.loads(
        (Path(__file__).resolve().parents[2] / "weather/map-catalogue.json").read_text()
    )
    hd = {p["mode"] for p in catalogue["arome_hd"]}
    standard = {p["mode"] for p in catalogue["arome"]}
    active = {p["mode"] for p in groups["arome_hd"]["products"]}
    assert active == {100, 131, 132, 118, 101, 125, 142, 103, 111, 138, 119}
    assert "arome" not in groups
    assert not active & {129, 154, 155, 128}
    assert hd == {
        100,
        141,
        131,
        132,
        143,
        147,
        118,
        101,
        129,
        142,
        157,
        125,
        145,
        153,
        156,
        158,
        103,
        111,
        152,
        128,
        138,
        119,
        154,
        155,
    }
    assert standard == {
        0,
        41,
        31,
        32,
        43,
        47,
        18,
        16,
        21,
        1,
        29,
        49,
        42,
        46,
        25,
        45,
        3,
        11,
        52,
        28,
        24,
        19,
        13,
        38,
        4,
        2,
        30,
        14,
        5,
        50,
        17,
        37,
        8,
        35,
        15,
        48,
        51,
        9,
    }
    assert (
        next(p for p in groups["arome_hd"]["products"] if p["mode"] == 125)["semantics"]
        == "cumulative"
    )
    assert (
        next(p for p in catalogue["arome_hd"] if p["mode"] == 129)["semantics"]
        == "simulation"
    )
    assert max(groups["gfs"]["leads_hours"]) == 384
    assert max(groups["ecmwf"]["leads_hours"]) == 360
    assert config["ensembles"] == {"ecmwf_ifs025": 15, "gfs05": 15}
    validate_groups(groups)


def test_cumulative_and_interval_urls_are_never_confused():
    page = " ".join([image("arome", "aromehd-1-24-1"), image("arome", "aromehd-25-24-1")])
    result = published_maps([page], "arome_hd", 125, 1, [24, 48])
    assert "aromehd-25-" in result["urls"][24]
    assert result["missing_leads_hours"] == [48]
    with pytest.raises(ValueError):
        published_maps([page], "arome", 25, 1, [24])


def test_long_leads_keep_their_older_run_and_newer_short_run_is_identified():
    short = image("ecmwf", "ecmwffr-25-72")
    old_short = image("ecmwf", "ecmwffr-25-72", "2026102812")
    long = image("ecmwf", "ecmwffr-25-360", "2026102812")
    page = " ".join([short, old_short, long])

    class Client:
        def get(self, url):
            return page.encode() if ".php?" in url else b"\x89PNG\r\n\x1a\nrest"

    def save(name, raw):
        return {"path": name, "sha256": "test"}

    data = fetch_product(
        Client(),
        save,
        "ecmwf",
        {"label": "ECMWF", "map": 2, "leads_hours": [72, 360]},
        {"mode": 25, "label": "Cumul", "semantics": "cumulative"},
    )
    assert data["mixed_runs"] is True
    assert data["model_run_at"] is None
    assert data["maps"][0]["model_run_at"] == "2026-10-29T00:00:00+00:00"
    assert data["maps"][1]["model_run_at"] == "2026-10-28T12:00:00+00:00"
    assert data["maps"][1]["valid_at"] == "2026-11-12T12:00:00+00:00"


def test_product_specific_minimum_maximum_hours():
    page = image("arome", "aromehd-31-6-1")

    class Client:
        def get(self, url):
            return page.encode() if ".php?" in url else b"\x89PNG\r\n\x1a\nrest"

    data = fetch_product(
        Client(),
        lambda name, _raw: {"path": name},
        "arome_hd",
        {"label": "HD", "map": 1, "leads_hours": [3, 12]},
        {"mode": 131, "label": "Max", "semantics": "extreme", "leads_hours": [6, 18]},
    )
    assert data["requested_leads_hours"] == [6, 18]
    assert data["missing_leads_hours"] == [18]
    assert len(data["maps"]) == 1


def test_gfs_fetches_the_published_long_range_page():
    calls = []

    class Client:
        def get(self, url):
            calls.append(url)
            if ".php?" in url:
                return image("gfs", "gfs-25-384" if "ech=204&" in url else "gfs-25-120").encode()
            return b"\x89PNG\r\n\x1a\nrest"

    data = fetch_product(
        Client(),
        lambda name, _raw: {"path": name},
        "gfs",
        {"label": "GFS", "map": 0, "leads_hours": [120, 384]},
        {"mode": 25, "label": "Cumul", "semantics": "cumulative"},
    )
    assert len(data["maps"]) == 2
    assert any("ech=204&" in u for u in calls)
    assert "map=301" in page_url("arpege", 25, 301)


def ensemble():
    return {
        "hourly_units": {
            "time": "unixtime",
            "temperature_2m": "°C",
            "temperature_2m_member01": "°C",
            "precipitation": "mm",
            "precipitation_member01": "mm",
        },
        "hourly": {
            "time": [1793232000, 1793235600, 1793239200],
            "temperature_2m": [-2, None, None],
            "temperature_2m_member01": [2, 4, None],
            "precipitation": [0, None, None],
            "precipitation_member01": [2, 0, None],
        },
    }


def test_ensemble_statistics_use_real_members_and_keep_nulls():
    data = normalize_ensemble(ensemble(), "Europe/Paris")
    stats = data["statistics"]["temperature_2m"]
    assert stats[0]["median"] == 0
    assert stats[0]["p10"] == -1.6
    assert stats[0]["p90"] == 1.6
    assert stats[0]["fraction_below_0c"] == 0.5
    assert stats[1]["member_count"] == 1
    assert stats[2]["median"] is None
    assert data["statistics"]["precipitation"][0]["fraction_precip_ge_0_1mm"] == 0.5
    assert data["members"]["temperature_2m"]["control"][1] is None
    assert data["coverage_end"] == data["valid_at"][1]
    assert data["model_run_at"] is None


@pytest.mark.parametrize("kind", ["unit", "duplicate", "length", "negative", "null"])
def test_invalid_ensembles_rejected(kind):
    data = ensemble()
    if kind == "unit":
        data["hourly_units"]["precipitation"] = "cm"
    elif kind == "duplicate":
        data["hourly"]["time"][1] = data["hourly"]["time"][0]
    elif kind == "length":
        data["hourly"]["precipitation"].pop()
    elif kind == "negative":
        data["hourly"]["precipitation"][0] = -1
    else:
        for k in data["hourly"]:
            if k != "time":
                data["hourly"][k] = [None] * 3
    with pytest.raises(ValueError):
        normalize_ensemble(data, "Europe/Paris")
