"""Bounded, explicit provider retrieval and non-destructive source refresh."""
from copy import deepcopy
from datetime import UTC, datetime
from enum import Enum
from hashlib import sha256
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

from open_garden_planner.services.local_plant_sources import (
    MAX_HTML_FILE_BYTES,
    ReferencePhotoCandidate,
    _product_page,
    collect_reference_photo_candidates,
)

USER_AGENT = "OpenGardenPlanner/plant-catalog"
_PROVIDER = "promesse_de_fleurs"
_MAX_REFERENCE_PHOTO_CHOICES = 12
_PROVIDER_PAGE_HOSTS = frozenset({"www.promessedefleurs.com", "promessedefleurs.com"})
# The provider currently serves product and media URLs on these exact hosts.
# Keep this a host allowlist, not a suffix rule: a page cannot nominate an
# arbitrary third-party image server for an automatic request.
_PROVIDER_PHOTO_HOSTS = _PROVIDER_PAGE_HOSTS


def validate_source_url(url):
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.username or parts.password
            or parts.port not in (None, 443)
            or parts.hostname not in _PROVIDER_PAGE_HOSTS):
        raise ValueError("Only HTTPS Promesse de Fleurs product URLs are supported")
    return url


def validate_reference_photo_url(url):
    """Accept images hosted only by the exact Promesse de Fleurs hosts."""
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.username or parts.password
            or parts.port not in (None, 443)
            or parts.hostname not in _PROVIDER_PHOTO_HOSTS):
        raise ValueError("Only HTTPS Promesse de Fleurs reference photos are supported")
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


def _retrieve(url, limit, opener, *, validator=validate_source_url, accept="text/html,text/plain"):
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with opener.open(request, timeout=12) as response:
        validator(response.url)
        if _request_identity(response.url) != _request_identity(url):
            raise ValueError("Source redirects are restricted; use a local file")
        if response.headers.get("Content-Encoding", "identity") not in ("", "identity"):
            raise ValueError("Compressed source responses are not supported")
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Plant source exceeds the download size limit")
        return data


def _permitted_payload(url, limit, opener, *, validator=validate_source_url,
                       accept="text/html,text/plain"):
    """Apply the same provider restriction and robots policy to every request."""
    validator(url)
    redirect_handler = (_ProviderRedirects() if validator is validate_source_url
                        else _ProviderPhotoRedirects())
    opener = opener or build_opener(redirect_handler)
    origin = "https://" + urlsplit(url).netloc
    try:
        try:
            policy = _retrieve(origin + "/robots.txt", 128 * 1024, opener, validator=validator)
        except HTTPError as error:
            if error.code != 404:
                raise
            policy = b""
        robots = RobotFileParser()
        robots.parse(policy.decode("utf-8", errors="replace").splitlines())
        if not robots.can_fetch(USER_AGENT, url):
            raise ValueError("The source does not permit automated retrieval; use a local file")
        return _retrieve(url, limit, opener, validator=validator, accept=accept)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise ValueError(f"Could not retrieve the source; use a local file: {error}") from error


class _ProviderPhotoRedirects(_ProviderRedirects):
    """Use the image host allowlist while retaining same-resource redirects."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_reference_photo_url(newurl)
        if _request_identity(newurl) != _request_identity(req.full_url):
            raise ValueError("Source redirects are restricted; use a local file")
        return HTTPRedirectHandler.redirect_request(self, req, fp, code, msg, headers, newurl)


class ReferencePhotoSelectionRequired(ValueError):
    """A source page offers multiple safe images and requires a user choice."""

    def __init__(self, candidates):
        self.candidates = tuple(candidates)
        if not self.candidates or len(self.candidates) > _MAX_REFERENCE_PHOTO_CHOICES:
            raise ValueError("Reference photo choices must contain between 1 and 12 candidates")
        super().__init__("The source page offers multiple reference photos; choose one to continue")


def _reference_image_suffix(payload):
    from io import BytesIO

    from PIL import Image, UnidentifiedImageError

    from open_garden_planner.services.plant_media import MAX_MEDIA_BYTES

    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise ValueError("Reference photo data must be bytes")
    payload = bytes(payload)
    if len(payload) > MAX_MEDIA_BYTES:
        raise ValueError("Reference photo exceeds the download size limit")
    try:
        with Image.open(BytesIO(payload)) as image:
            suffix = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp",
                      "GIF": ".gif", "TIFF": ".tif"}.get(image.format)
            if suffix is None:
                raise ValueError("Unsupported reference image; use a local file")
            if image.width * image.height > 50_000_000:
                raise ValueError("Image exceeds the supported pixel limit")
            image.verify()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise ValueError("Unsupported reference image; use a local file") from error
    return suffix


def retrieve_reference_photo(url, opener=None):
    """Retrieve one allowed image and validate its format, size and dimensions."""
    from open_garden_planner.services.plant_media import MAX_MEDIA_BYTES

    payload = _permitted_payload(url, MAX_MEDIA_BYTES, opener,
                                 validator=validate_reference_photo_url, accept="image/*")
    _reference_image_suffix(payload)
    return payload


def import_reference_photo_bytes(payload, library_path, library_id, source_url, *,
                                 caption="", credit="", page_url=""):
    """Import already retrieved bytes as an unattached reference media asset.

    This is intended to run in a worker. ``source_url`` remains the image URL;
    optional ``page_url`` retains the product page from which it was selected.
    """
    from tempfile import TemporaryDirectory

    from open_garden_planner.services.botanical_media import import_reference_media

    validate_reference_photo_url(source_url)
    if page_url:
        validate_source_url(page_url)
    payload = bytes(payload) if isinstance(payload, (bytes, bytearray, memoryview)) else payload
    suffix = _reference_image_suffix(payload)
    with TemporaryDirectory(prefix="ogp-reference-") as directory:
        source = Path(directory) / ("reference" + suffix)
        source.write_bytes(payload)
        asset = import_reference_media(source, library_path, library_id, kind="reference",
                                       source_url=source_url, caption=caption, credit=credit)
        if page_url:
            asset["source_page_url"] = page_url
        return asset


def fetch_reference_photo(url, library_path, library_id, *, caption="", credit="", opener=None,
                          page_url=""):
    """Download one explicit provider image into the existing media store.

    Unsupported external image hosts use the local-file fallback, never a
    relaxed redirect policy. The returned asset is unattached and must be
    associated by a separate Qt-side transaction.
    """
    payload = retrieve_reference_photo(url, opener=opener)
    return import_reference_photo_bytes(payload, library_path, library_id, url,
                                        caption=caption, credit=credit, page_url=page_url)


def _same_product_page(requested_url, canonical_url):
    requested = urlsplit(requested_url)
    canonical = urlsplit(canonical_url)
    return (canonical.path.rstrip("/"), canonical.query) == (
        requested.path.rstrip("/"), requested.query,
    )


def _page_reference_photo_candidates(page_url, opener):
    """Read a provider product page, then retain only exact-host image URLs."""
    validate_source_url(page_url)
    payload = _permitted_payload(page_url, MAX_HTML_FILE_BYTES, opener)
    canonical_url, candidates = collect_reference_photo_candidates(payload, page_url)
    validate_source_url(canonical_url)
    if not _same_product_page(page_url, canonical_url):
        raise ValueError("The downloaded product identity differs from the requested URL")

    safe_candidates = []
    seen = set()
    for candidate in candidates:
        try:
            validate_reference_photo_url(candidate.url)
        except ValueError:
            continue
        if candidate.url in seen:
            continue
        seen.add(candidate.url)
        safe_candidates.append(ReferencePhotoCandidate(
            url=candidate.url,
            caption=candidate.caption,
            source_url=canonical_url,
            primary=candidate.primary,
        ))
        if len(safe_candidates) == _MAX_REFERENCE_PHOTO_CHOICES:
            break
    return tuple(safe_candidates)


def fetch_reference_photo_for_plant(plant, library_path, library_id, *, opener=None,
                                    caption="", credit="Promesse de Fleurs"):
    """Explicitly save the provider's proposed image as an unattached reference.

    The source image URL is retained separately from ``plant.image_url`` so a
    manually supplied catalogue URL can remain protected while the provider
    photo is still available for an explicit download.
    """
    raw = plant.raw_data if isinstance(getattr(plant, "raw_data", None), dict) else {}
    provider = raw.get(_PROVIDER, {})
    image_url = provider.get("image_url", "") if isinstance(provider, dict) else ""
    page_url = ""
    if isinstance(provider, dict):
        page_url = provider.get("source_url", "")
    if not page_url:
        local_sheet = raw.get("local_sheet", {})
        if isinstance(local_sheet, dict):
            page_url = local_sheet.get("url", "")
    if not image_url:
        image_url = getattr(plant, "image_url", "")
        if image_url:
            try:
                validate_reference_photo_url(image_url)
            except ValueError:
                # The catalogue may have a personal URL here. It remains
                # protected as a field and is never fetched from this path.
                image_url = ""
    selected_page_url = ""
    selected_caption = ""
    if not image_url:
        # The botanical import stays strict about multiple product records.
        # Photo recovery reads only image metadata and keeps product captions
        # attached to their own candidate records.
        if not page_url:
            return None
        candidates = _page_reference_photo_candidates(page_url, opener)
        if not candidates:
            return None
        if len(candidates) > 1:
            raise ReferencePhotoSelectionRequired(candidates)
        image_url = candidates[0].url
        selected_page_url = candidates[0].source_url
        selected_caption = candidates[0].caption
    if not caption:
        caption = (selected_caption or getattr(plant, "common_name", "")
                   or getattr(plant, "scientific_name", ""))
    return fetch_reference_photo(image_url, library_path, library_id, opener=opener,
                                 caption=caption, credit=credit or "Promesse de Fleurs",
                                 page_url=selected_page_url)


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
    existing_identity = source_identity(existing)
    incoming_identity = source_identity(incoming)
    if existing_identity is not None or incoming_identity is not None:
        identity_matches = existing_identity == incoming_identity and existing_identity is not None
    else:
        identity_matches = bool(existing.source_id) and existing.source_id == incoming.source_id
    if not identity_matches:
        raise ValueError("Source identity mismatch; a similar name is not sufficient")
    current = existing.to_dict()
    offered = incoming.to_dict()
    state_key = "catalog_enrichment" if "catalog_enrichment" in existing.raw_data else "catalog_import"
    state = existing.raw_data.get(state_key, {})
    if not isinstance(state, dict):
        raise ValueError("Unrecognized import provenance; refusing to replace it")
    baseline = state.get("baseline", {})
    if not isinstance(baseline, dict):
        raise ValueError("Unrecognized import baseline; refusing to replace it")
    manual_fields = state.get("manual_fields", [])
    if not isinstance(manual_fields, list) or any(not isinstance(key, str) for key in manual_fields):
        raise ValueError("Unrecognized manual source corrections; refusing to replace them")
    manual_fields = set(manual_fields)
    protected_periods = state.get("protected_periods", [])
    if not isinstance(protected_periods, list) or any(not isinstance(key, str) for key in protected_periods):
        raise ValueError("Unrecognized protected calendar periods; refusing to replace them")
    protected_periods = set(protected_periods)
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
            protected = (key in protected_periods
                         or key in merged and not isinstance(old, dict)
                         or isinstance(old, dict) and old.get("manually_edited", False))
            if old != value:
                changes.append({"field": "calendar." + key, "current": deepcopy(old),
                                "source": deepcopy(value),
                                "protected": protected})
            if key not in merged or isinstance(old, dict) and not protected:
                merged[key] = {**(old or {}), **value}
        candidate.raw_data["botanical_calendar"] = merged
    else:
        changes.append({"field": "calendar", "current": deepcopy(old_calendar), "source": source_calendar,
                        "protected": True})
    refreshed_state = {
        **deepcopy(state), **deepcopy(mark_import_baseline(incoming).raw_data["catalog_import"]),
        "manual_fields": sorted(manual_fields),
    }
    if state_key == "catalog_enrichment":
        refreshed_state["source_identity"] = _identity_record(incoming_identity)
    candidate.raw_data[state_key] = refreshed_state
    return candidate, changes


def source_identity(plant):
    """Return the stable external provider identity, independent of library ID.

    Enriched records keep their catalogue UUID in ``source_id``; this helper
    reads their separate provider link. Legacy provider records derive the
    same identity from their stable source ID or canonical product URL.
    """
    raw = getattr(plant, "raw_data", None)
    if not isinstance(raw, dict):
        raise ValueError("Unrecognized plant source data")

    enrichment = raw.get("catalog_enrichment")
    if "catalog_enrichment" in raw:
        if not isinstance(enrichment, dict):
            raise ValueError("Unrecognized source enrichment; refusing to replace it")
        identity = enrichment.get("source_identity")
        if not isinstance(identity, dict):
            raise ValueError("Unrecognized source enrichment identity")
        provider, source_id = identity.get("provider"), identity.get("source_id")
        if provider != _PROVIDER or not isinstance(source_id, str) or not source_id:
            raise ValueError("Unrecognized source enrichment identity")
        return provider, source_id

    provider_record = raw.get(_PROVIDER)
    if _PROVIDER in raw and not isinstance(provider_record, dict):
        raise ValueError("Unrecognized Promesse de Fleurs source data")
    if getattr(plant, "data_source", "") == _PROVIDER:
        source_id = getattr(plant, "source_id", "")
        if isinstance(source_id, str) and source_id:
            return _PROVIDER, source_id
    if isinstance(provider_record, dict):
        url = provider_record.get("source_url")
        if isinstance(url, str) and url:
            validate_source_url(url)
            source_id = "local-sheet:" + sha256(url.encode("utf-8")).hexdigest()[:32]
            return _PROVIDER, source_id
    local_sheet = raw.get("local_sheet")
    if isinstance(local_sheet, dict):
        url = local_sheet.get("url")
        if isinstance(url, str) and url.strip():
            url = url.strip()
            if urlsplit(url).hostname not in _PROVIDER_PAGE_HOSTS:
                return None
            validate_source_url(url)
            source_id = "local-sheet:" + sha256(url.encode("utf-8")).hexdigest()[:32]
            return _PROVIDER, source_id
    return None


def _identity_record(identity):
    if not isinstance(identity, tuple) or len(identity) != 2:
        raise ValueError("A stable provider identity is required")
    provider, source_id = identity
    return {"provider": provider, "source_id": source_id}


def _known(value):
    if value is None:
        return False
    if isinstance(value, Enum):
        return value.value != "unknown"
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def plan_source_enrichment(existing, incoming):
    """Prepare a preview-safe, fill-only provider enrichment for a selected record.

    This path intentionally does not match records by name. The caller chose
    both the exact target catalogue record and source URL. Library identity,
    document snapshots and all established fields remain untouched.
    """
    incoming_identity = source_identity(incoming)
    if incoming_identity is None:
        raise ValueError("The selected source has no stable provider identity")
    if source_identity(existing) is not None:
        raise ValueError("This plant already has a provider source; use source refresh")
    if not existing.source_id:
        raise ValueError("An exact catalogue identity is required for enrichment")
    provider_record = incoming.raw_data.get(_PROVIDER)
    if not isinstance(provider_record, dict):
        raise ValueError("The selected source is missing its provider record")
    source_url = provider_record.get("source_url")
    if not isinstance(source_url, str) or not source_url:
        raise ValueError("The selected source is missing its product URL")
    validate_source_url(source_url)
    expected_identity = (_PROVIDER, "local-sheet:" + sha256(source_url.encode("utf-8")).hexdigest()[:32])
    if incoming_identity != expected_identity:
        raise ValueError("The selected source identity does not match its product URL")

    candidate = deepcopy(existing)
    current = existing.to_dict()
    offered = incoming.to_dict()
    changes = []
    for field in offered:
        if field in {"raw_data", "source_id", "data_source"}:
            continue
        old = getattr(existing, field)
        proposed = getattr(incoming, field)
        if old == proposed:
            continue
        if not _known(old) and _known(proposed):
            setattr(candidate, field, deepcopy(proposed))
            changes.append({"field": field, "current": deepcopy(current.get(field)),
                            "source": deepcopy(offered.get(field)), "protected": False})
        elif _known(old) and _known(proposed):
            changes.append({"field": field, "current": deepcopy(current.get(field)),
                            "source": deepcopy(offered.get(field)), "protected": True})

    # A custom record's established values are user-owned even when they
    # happen to agree with the first provider response. Remember that so a
    # later refresh cannot overwrite them after the provider changes its data.
    protected_fields = {
        field for field in offered
        if field not in {"raw_data", "source_id", "data_source"}
        and _known(getattr(existing, field))
    }

    raw = candidate.raw_data
    profile = raw.get("botanical_profile", {})
    if not isinstance(profile, dict):
        raise ValueError("Unrecognized botanical corrections; source enrichment cannot replace them safely")
    from open_garden_planner.services.botanical_profile import SOURCE_FIELDS, source_trait, trait

    for key in SOURCE_FIELDS:
        proposed = source_trait(incoming, key)
        old = trait(existing, key)
        if proposed and old != proposed:
            changes.append({"field": "profile." + key, "current": old, "source": proposed,
                            "protected": key in profile or bool(old)})
        if old and key not in profile:
            # Pin the selected custom record's established trait. The
            # contradictory provider value remains available in raw_data.
            profile[key] = old
    if profile:
        raw["botanical_profile"] = profile

    # Keep the provider proposal discoverable even when a personal image URL
    # is already present and therefore protected on the main field.
    for key in (_PROVIDER,):
        if key in raw and not isinstance(raw[key], dict):
            raise ValueError("Unrecognized provider source data; refusing to replace it")
        previous = raw.get(key)
        if previous is not None and previous != provider_record:
            history = raw.setdefault("catalog_source_history", [])
            if not isinstance(history, list):
                raise ValueError("Unrecognized source history; refusing to replace it")
            history.append({"provider": key, "payload": deepcopy(previous)})
        raw[key] = deepcopy(provider_record)

    # Preserve existing raw calendars and fill only genuinely absent periods.
    from open_garden_planner.services.botanical_calendar import (
        calendar_for_species,
        has_botanical_periods,
    )

    old_calendar = calendar_for_species(existing)
    source_calendar = calendar_for_species(incoming)
    protected_periods = set()
    stored_calendar = raw.get("botanical_calendar", {})
    if "botanical_calendar" in raw and not isinstance(stored_calendar, dict):
        raise ValueError("Unrecognized botanical calendar; refusing to replace it")
    merged_calendar = deepcopy(stored_calendar)
    for key in source_calendar.to_dict():
        old_period = getattr(old_calendar, key)
        source_period = getattr(source_calendar, key)
        source_present = source_period.months is not None or bool(source_period.source_text)
        old_blank = old_period.months is None and not old_period.source_text
        stored_period = merged_calendar.get(key)
        if key in merged_calendar and not isinstance(stored_period, dict):
            # Keep future or malformed values intact; do not silently replace
            # a representation that this version cannot interpret.
            changes.append({"field": "calendar." + key, "current": deepcopy(stored_period),
                            "source": source_period.to_dict(), "protected": True})
            continue
        if key in merged_calendar:
            from open_garden_planner.models.botanical_calendar import MonthlyPeriod

            try:
                MonthlyPeriod.from_dict(stored_period)
            except (TypeError, ValueError):
                changes.append({"field": "calendar." + key, "current": deepcopy(stored_period),
                                "source": source_period.to_dict(), "protected": True})
                continue
        if not old_blank:
            # Existing periods came from this custom record or an earlier
            # source. Keep them pinned across later provider refreshes.
            preserved = deepcopy(stored_period) if isinstance(stored_period, dict) else old_period.to_dict()
            preserved["months"] = list(old_period.months) if old_period.months is not None else None
            preserved["source_text"] = old_period.source_text
            preserved["manually_edited"] = old_period.manually_edited
            merged_calendar[key] = preserved
            protected_periods.add(key)
        if source_present and old_blank and not old_period.manually_edited:
            merged_calendar[key] = {**(stored_period or {}), **source_period.to_dict()}
            changes.append({"field": "calendar." + key, "current": old_period.to_dict(),
                            "source": source_period.to_dict(), "protected": False})
        elif source_present and old_period != source_period:
            changes.append({"field": "calendar." + key, "current": old_period.to_dict(),
                            "source": source_period.to_dict(), "protected": True})
    if has_botanical_periods(calendar_for_species(candidate)) or any(
        isinstance(value, dict) and (value.get("months") is not None or value.get("source_text"))
        for value in merged_calendar.values()
    ):
        raw["botanical_calendar"] = merged_calendar

    marked = mark_import_baseline(incoming).raw_data["catalog_import"]
    enrichment = raw.get("catalog_enrichment", {})
    if not isinstance(enrichment, dict):
        raise ValueError("Unrecognized source enrichment; refusing to replace it")
    raw["catalog_enrichment"] = {
        **deepcopy(enrichment),
        **deepcopy(marked),
        "source_identity": _identity_record(incoming_identity),
        "manual_fields": sorted(protected_fields),
        "protected_periods": sorted(protected_periods),
    }
    # Library ownership stays with the selected record even though the source
    # baseline carries the provider's own identity and origin.
    candidate.source_id = existing.source_id
    candidate.data_source = existing.data_source
    return candidate, changes


def plan_source_update(existing, incoming):
    """Choose strict refresh for the same linked source, else safe enrichment."""
    offered_identity = source_identity(incoming)
    if offered_identity is None:
        raise ValueError("The selected source has no stable provider identity")
    current_identity = source_identity(existing)
    if current_identity is None:
        return plan_source_enrichment(existing, incoming)
    if current_identity != offered_identity:
        raise ValueError("Source identity mismatch; a similar name is not sufficient")
    return plan_source_refresh(existing, incoming)
