"""Bounded, explicit provider retrieval and non-destructive source refresh."""
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

from open_garden_planner.services.local_plant_sources import MAX_HTML_FILE_BYTES, _product_page

USER_AGENT = "OpenGardenPlanner/plant-catalog"


def validate_source_url(url):
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.username or parts.password
            or parts.port not in (None, 443)
            or parts.hostname not in ("www.promessedefleurs.com", "promessedefleurs.com")):
        raise ValueError("Only HTTPS Promesse de Fleurs product URLs are supported")
    return url


class _ProviderRedirects(HTTPRedirectHandler):
    max_redirections = 3
    max_repeats = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_source_url(newurl)
        # A different URL may have a different robots rule. Refuse it before
        # urllib sends the redirected request, and offer the local-file path.
        if _request_identity(newurl) != _request_identity(req.full_url):
            raise ValueError("Source redirects are restricted; use a local file")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _request_identity(url):
    parts = urlsplit(url)
    return parts.scheme, parts.netloc, parts.path or "/", parts.query


def _retrieve(url, limit, opener):
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain"})
    with opener.open(request, timeout=12) as response:
        validate_source_url(response.url)
        if _request_identity(response.url) != _request_identity(url):
            raise ValueError("Source redirects are restricted; use a local file")
        if response.headers.get("Content-Encoding", "identity") not in ("", "identity"):
            raise ValueError("Compressed source responses are not supported")
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Plant source exceeds the download size limit")
        return data


def _permitted_payload(url, limit, opener):
    """Apply the same provider restriction and robots policy to every request."""
    validate_source_url(url)
    opener = opener or build_opener(_ProviderRedirects())
    origin = "https://" + urlsplit(url).netloc
    try:
        try:
            policy = _retrieve(origin + "/robots.txt", 128 * 1024, opener)
        except HTTPError as error:
            if error.code != 404:
                raise
            policy = b""
        robots = RobotFileParser()
        robots.parse(policy.decode("utf-8", errors="replace").splitlines())
        if not robots.can_fetch(USER_AGENT, url):
            raise ValueError("The source does not permit automated retrieval; use a local file")
        return _retrieve(url, limit, opener)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise ValueError(f"Could not retrieve the source; use a local file: {error}") from error


def fetch_reference_photo(url, library_path, library_id, *, caption="", credit="", opener=None):
    """Explicit provider-photo download into the existing library media store.

    Runs in a worker; returns an unattached asset for the Qt-side transaction.
    Unsupported external image hosts use the local-file fallback, never a
    relaxed redirect policy. Image dimensions and originals use the same
    checks as local reference imports.
    """
    from io import BytesIO
    from tempfile import TemporaryDirectory

    from PIL import Image

    from open_garden_planner.services.botanical_media import import_reference_media
    from open_garden_planner.services.plant_media import MAX_MEDIA_BYTES

    payload = _permitted_payload(url, MAX_MEDIA_BYTES, opener)
    with Image.open(BytesIO(payload)) as image:
        suffix = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif", "TIFF": ".tif"}.get(image.format)
        if suffix is None:
            raise ValueError("Unsupported reference image; use a local file")
        if image.width * image.height > 50_000_000:
            raise ValueError("Image exceeds the supported pixel limit")
    with TemporaryDirectory(prefix="ogp-reference-") as directory:
        source = Path(directory) / ("reference" + suffix)
        source.write_bytes(payload)
        return import_reference_media(source, library_path, library_id, kind="reference",
                                      source_url=url, caption=caption, credit=credit)


def fetch_plant_source(url, *, opener=None):
    """Check provider policy, then fetch once. Never writes a store or photos."""
    payload = _permitted_payload(url, MAX_HTML_FILE_BYTES, opener)
    plant = _product_page(Path("download.html"), payload)
    # The returned canonical identity must be the requested product, not a
    # different record reached through a marketing redirect.
    source = plant.raw_data["promesse_de_fleurs"]
    canonical = urlsplit(source["source_url"])
    requested = urlsplit(url)
    validate_source_url(source["source_url"])
    if (canonical.path.rstrip("/"), canonical.query) != (requested.path.rstrip("/"), requested.query):
        raise ValueError("The downloaded product identity differs from the requested URL")
    return mark_import_baseline(plant)


def mark_import_baseline(plant):
    result = deepcopy(plant)
    previous = result.raw_data.get("catalog_import", {})
    if not isinstance(previous, dict):
        raise ValueError("Unrecognized import provenance; refusing to replace it")
    baseline = result.to_dict()
    baseline.pop("raw_data", None)
    now = datetime.now(UTC).isoformat()
    result.raw_data["catalog_import"] = {
        **previous, "baseline": baseline, "retrieved_at": now, "verified_at": now,
    }
    return result


def plan_source_refresh(existing, incoming):
    """Three-way refresh; manual corrections and unknown extensions survive.

    For legacy imports without a baseline, keep all established values. The
    preview reports protected differences so they can be corrected explicitly.
    No placed snapshot or specimen is modified by this preparation.
    """
    if not existing.source_id or existing.source_id != incoming.source_id:
        raise ValueError("Source identity mismatch; a similar name is not sufficient")
    current = existing.to_dict()
    offered = incoming.to_dict()
    state = existing.raw_data.get("catalog_import", {})
    if not isinstance(state, dict):
        raise ValueError("Unrecognized import provenance; refusing to replace it")
    baseline = state.get("baseline", {})
    if not isinstance(baseline, dict):
        raise ValueError("Unrecognized import baseline; refusing to replace it")
    manual_fields = state.get("manual_fields", [])
    if not isinstance(manual_fields, list) or any(not isinstance(key, str) for key in manual_fields):
        raise ValueError("Unrecognized manual source corrections; refusing to replace them")
    manual_fields = set(manual_fields)
    # A supplier can temporarily agree with a manual correction. Remember its
    # ownership even then, so a subsequent refresh cannot erase that correction.
    manual_fields.update(key for key, value in baseline.items()
                         if key not in {"raw_data", "data_source", "source_id"}
                         and current.get(key) != value)
    candidate = deepcopy(existing)
    changes = []
    for field, proposed in offered.items():
        if field in {"raw_data", "data_source", "source_id"} or current.get(field) == proposed:
            continue
        old = current.get(field)
        protected = field in manual_fields or field not in baseline or old != baseline[field]
        if protected:
            manual_fields.add(field)
        changes.append({"field": field, "current": deepcopy(old), "source": deepcopy(proposed),
                        "protected": protected})
        if not protected:
            setattr(candidate, field, deepcopy(getattr(incoming, field)))
    from open_garden_planner.services.botanical_profile import SOURCE_FIELDS, source_trait, trait

    overrides = existing.raw_data.get("botanical_profile", {})
    for key in SOURCE_FIELDS:
        previous_source, proposed = source_trait(existing, key), source_trait(incoming, key)
        if previous_source != proposed:
            if not isinstance(overrides, dict):
                raise ValueError("Unrecognized botanical corrections; source refresh cannot replace them safely")
            changes.append({"field": "profile." + key, "current": trait(existing, key), "source": proposed,
                            "protected": key in overrides})
    for key in ("promesse_de_fleurs", "local_sheet", "source_file", "sha256"):
        if key in incoming.raw_data:
            if key in candidate.raw_data and candidate.raw_data[key] != incoming.raw_data[key]:
                history = candidate.raw_data.setdefault("catalog_source_history", [])
                if not isinstance(history, list):
                    raise ValueError("Unrecognized source history; refusing to replace it")
                history.append({"provider": key, "payload": deepcopy(candidate.raw_data[key])})
            candidate.raw_data[key] = deepcopy(incoming.raw_data[key])
    # Preserve profile overrides and manually edited calendar bands.
    from open_garden_planner.services.botanical_calendar import calendar_for_species

    old_calendar = existing.raw_data.get("botanical_calendar", calendar_for_species(existing).to_dict())
    source_calendar = calendar_for_species(incoming).to_dict()
    if isinstance(old_calendar, dict):
        merged = deepcopy(old_calendar)
        for key, value in source_calendar.items():
            old = merged.get(key)
            if old != value:
                changes.append({"field": "calendar." + key, "current": deepcopy(old),
                                "source": deepcopy(value),
                                "protected": key in merged and not isinstance(old, dict)
                                or isinstance(old, dict) and old.get("manually_edited", False)})
            if key not in merged or isinstance(old, dict) and not old.get("manually_edited", False):
                merged[key] = {**(old or {}), **value}
        candidate.raw_data["botanical_calendar"] = merged
    else:
        changes.append({"field": "calendar", "current": deepcopy(old_calendar), "source": source_calendar,
                        "protected": True})
    candidate.raw_data["catalog_import"] = {
        **deepcopy(state), **deepcopy(mark_import_baseline(incoming).raw_data["catalog_import"]),
        "manual_fields": sorted(manual_fields),
    }
    return candidate, changes
