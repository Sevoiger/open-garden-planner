"""Meteociel products: literal published run URLs, never invented image paths."""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlencode


class HttpClient(Protocol):
    def get(self, url: str) -> bytes: ...


FAMILIES: dict[str, tuple[str, str, str, int, int]] = {
    "arome_hd": ("arome.php", "arome", "aromehd", 100, 51),
    "arome": ("arome.php", "arome", "arome", 0, 51),
    "arpege": ("arpege.php", "arpege", "arpege", 0, 114),
    "ecmwf": ("ecmwf_hres.php", "ecmwf", "ecmwffr", 0, 360),
    "gfs": ("gfse_cartes.php", "gfs", "gfs", 0, 384),
}


def page_url(family: str, mode: int, zone: int, lead: int = 3) -> str:
    page = FAMILIES[family][0]
    params = {"ech": lead, "mode": mode, "carte" if family in {"ecmwf", "gfs"} else "map": zone}
    if family == "gfs":
        params["code"] = 0
    return "https://www.meteociel.fr/modeles/" + page + "?" + urlencode(params)


def published_maps(
    pages: list[str], family: str, mode: int, zone: int, leads: list[int]
) -> dict[str, Any]:
    _, directory, prefix, offset, _ = FAMILIES[family]
    mode_code = mode - offset
    suffix = (
        f"-{zone - 300 if family == 'arpege' else zone}"
        if family in {"arome_hd", "arome", "arpege"}
        else ""
    )
    pattern = (
        rf"https://modeles\d+\.meteociel\.fr/modeles/{directory}/runs/(\d{{10}})/"
        rf"{prefix}-{mode_code}-(\d+){suffix}\.png(?:\?[^\s\x22\x27<>]*)?"
    )
    runs: dict[str, dict[int, str]] = {}
    for page in pages:
        for match in re.finditer(pattern, page):
            runs.setdefault(match[1], {})[int(match[2])] = html.unescape(match[0])
    if not runs:
        raise ValueError(f"No published {family} product {mode}; HTML or product unavailable")
    # The provider can publish a newer short run alongside an older long run.
    # Select the newest published run PER LEAD and expose every run explicitly.
    selected, selected_runs = {}, {}
    for lead in leads:
        candidates = [run for run in runs if lead in runs[run]]
        if candidates:
            run = max(candidates)
            selected[lead] = runs[run][lead]
            selected_runs[lead] = run
    if not selected:
        raise ValueError("No requested map lead published for this product")
    return {
        "urls": selected,
        "selected_runs": selected_runs,
        "available_leads_hours": sorted({h for items in runs.values() for h in items}),
        "missing_leads_hours": [h for h in leads if h not in selected],
        "published_runs": sorted(runs, reverse=True),
    }


def fetch_product(
    client: HttpClient,
    save: Callable[[str, bytes], dict[str, Any]],
    family: str,
    group: dict[str, Any],
    product: dict[str, Any],
) -> dict[str, Any]:
    mode, zone = product["mode"], group["map"]
    leads = product.get("leads_hours", group["leads_hours"])
    # GFS publishes its >192h range on a separate page linked as "Suite".
    page_leads = [min(leads)]
    if family == "gfs" and min(leads) < 192 < max(leads):
        page_leads.append(204)
    pages, references = [], []
    for lead in page_leads:
        url = page_url(family, mode, zone, lead)
        raw = client.get(url)
        pages.append(raw.decode("iso-8859-1"))
        references.append({"url": url, **save(f"{family}-{mode}-page-{lead}.html", raw)})
    published = published_maps(pages, family, mode, zone, leads)
    maps = []
    for lead, url in published["urls"].items():
        run_at = datetime.strptime(published["selected_runs"][lead], "%Y%m%d%H").replace(tzinfo=UTC)
        image = client.get(url)
        if not image.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Map response is not a PNG")
        maps.append(
            {
                "url": url,
                "lead_hours": lead,
                "model_run_at": run_at.isoformat(),
                "valid_at": (run_at + timedelta(hours=lead)).isoformat(),
                **save(f"{family}-{mode}-{lead:03d}h.png", image),
            }
        )
    return {
        "provider": "Meteociel",
        "model": group["label"],
        "family": family,
        "mode": mode,
        "label": product["label"],
        "semantics": product["semantics"],
        "status": "forecast_map",
        "model_run_at": maps[0]["model_run_at"]
        if len(set(published["selected_runs"].values())) == 1
        else None,
        "mixed_runs": len(set(published["selected_runs"].values())) > 1,
        "requested_leads_hours": leads,
        "available_leads_hours": published["available_leads_hours"],
        "missing_leads_hours": published["missing_leads_hours"],
        "published_runs": published["published_runs"],
        "run_selection": "Newest published run per lead; each image retains its own run",
        "pages": references,
        "maps": maps,
    }


def validate_groups(groups: dict[str, Any]) -> None:
    for family, group in groups.items():
        if family not in FAMILIES:
            raise ValueError(f"Unsupported map family: {family}")
        maximum = FAMILIES[family][-1]
        if not group["leads_hours"] or any(
            not isinstance(h, int) or not 1 <= h <= maximum for h in group["leads_hours"]
        ):
            raise ValueError(f"Invalid map horizon for {family}")
        zones = {
            "arome_hd": range(5),
            "arome": range(5),
            "arpege": range(300, 305),
            "ecmwf": [2],
            "gfs": [0],
        }
        if group["map"] not in zones[family]:
            raise ValueError(f"Unsupported map zone for {family}")
        modes = [p["mode"] for p in group["products"]]
        if len(set(modes)) != len(modes) or any(
            not isinstance(m, int) or not 0 <= m <= 199 for m in modes
        ):
            raise ValueError(f"Invalid or duplicate products for {family}")
        for product in group["products"]:
            product_leads = product.get("leads_hours", group["leads_hours"])
            if (
                not product_leads
                or len(set(product_leads)) != len(product_leads)
                or any(not isinstance(h, int) or not 1 <= h <= maximum for h in product_leads)
            ):
                raise ValueError(f"Invalid product horizon for {family}")
            if (family == "arome_hd") != (product["mode"] >= 100) and family in {
                "arome_hd",
                "arome",
            }:
                raise ValueError("AROME HD and 2.5 km products must remain distinct")
            if not product["label"] or product["semantics"] not in {
                "instant",
                "interval",
                "cumulative",
                "extreme",
                "simulation",
                "diagnostic",
            }:
                raise ValueError("Each map needs a label and explicit semantics")
