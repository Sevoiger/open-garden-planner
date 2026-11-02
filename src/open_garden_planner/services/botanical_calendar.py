"""Read monthly botanical periods without converting frost-relative timings."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from typing import Any

from open_garden_planner.models.botanical_calendar import (
    CALENDAR_ACTIVITIES,
    BotanicalCalendar,
    MonthlyPeriod,
)
from open_garden_planner.models.plant_data import PlantSpeciesData

_MONTHS = {
    "janvier": 1, "january": 1,
    "fevrier": 2, "february": 2,
    "mars": 3, "march": 3,
    "avril": 4, "april": 4,
    "mai": 5, "may": 5,
    "juin": 6, "june": 6,
    "juillet": 7, "july": 7,
    "aout": 8, "august": 8,
    "septembre": 9, "september": 9,
    "octobre": 10, "october": 10,
    "novembre": 11, "november": 11,
    "decembre": 12, "december": 12,
}
_MONTH_TOKEN = re.compile(
    r"(?<![a-z])(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")(?![a-z])"
)
_SOURCE_LABELS = {
    "planting": ("periode raisonnable de plantation", "periode de plantation"),
    "flowering": ("periode de floraison",),
    "pruning": ("periode de taille",),
    "planting_optimal": ("meilleure periode de plantation", "periode optimale de plantation"),
    "harvest": ("periode de recolte", "periode de fructification"),
    "sowing": ("periode de semis",),
    "propagation": ("periode de multiplication",),
}
_EXPLICIT_MONTH_KEYS = {
    "planting": ("plantation_mois", "planting_months"),
    "flowering": ("floraison_mois", "flowering_months"),
    "pruning": ("taille_mois", "pruning_months"),
    "planting_optimal": ("plantation_optimale_mois", "planting_optimal_months"),
    "harvest": ("recolte_mois", "fructification_mois", "harvest_months"),
    "sowing": ("semis_mois", "sowing_months"),
    "propagation": ("multiplication_mois", "propagation_months"),
}
_EMPTY_CALENDAR = BotanicalCalendar()


def _normalize(value: str) -> str:
    value = value.casefold().replace("’", "'").replace("‘", "'")
    value = value.replace("\u00a0", " ")
    for dash in "‐‑‒–—―":
        value = value.replace(dash, "-")
    decomposed = unicodedata.normalize("NFD", value)
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


def parse_months(source_text: str) -> tuple[int, ...] | None:
    """Parse a fully recognized month or month expression; otherwise return None.

    Month ranges are inclusive and wrap across December. Recognized separators
    are range words/dashes and list separators. Any unrecognized wording keeps
    the source available but declines to guess the months.
    """
    if not isinstance(source_text, str) or not source_text.strip():
        return None
    normalized = _normalize(source_text).strip()
    matches = list(_MONTH_TOKEN.finditer(normalized))
    if not matches:
        return None

    prefix = normalized[:matches[0].start()].strip()
    suffix = normalized[matches[-1].end():].strip()
    if prefix not in {"", "de", "du", "d'", "en", "entre", "from"}:
        return None
    if prefix == "entre" and len(matches) != 2:
        return None
    if suffix not in {"", "."}:
        return None

    months = [_MONTHS[match.group(1)] for match in matches]
    connectors: list[str] = []
    range_connectors = {"a", "au", "to", "until", "jusqua", "jusqu'a", "jusqu a", "-"}
    list_connectors = {",", ";", "/", "et", "and", "&", "or"}
    for left, right in zip(matches, matches[1:], strict=False):
        connector = normalized[left.end():right.start()].strip()
        if connector in range_connectors or (prefix == "entre" and connector == "et"):
            connectors.append("range")
        elif connector in list_connectors:
            connectors.append("list")
        else:
            return None

    selected = [months[0]]
    seen = {months[0]}
    for index, connector in enumerate(connectors, start=1):
        current = months[index]
        if connector == "range":
            cursor = months[index - 1]
            while cursor != current:
                cursor = cursor % 12 + 1
                if cursor not in seen:
                    selected.append(cursor)
                    seen.add(cursor)
        elif current not in seen:
            selected.append(current)
            seen.add(current)
    return tuple(selected)


def _source_containers(raw_data: Mapping[str, Any]) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """Return source record + labelled-fields pairs for supported old imports."""
    containers: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    local = raw_data.get("local_sheet")
    if isinstance(local, Mapping):
        characteristics = local.get("characteristics")
        containers.append((local, characteristics if isinstance(characteristics, Mapping) else {}))
    promesse = raw_data.get("promesse_de_fleurs")
    if isinstance(promesse, Mapping):
        fields = promesse.get("fields")
        containers.append((promesse, fields if isinstance(fields, Mapping) else {}))
    return containers


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str) and item.strip()]
    return []


def _source_text(raw_data: Mapping[str, Any], labels: tuple[str, ...]) -> tuple[str, bool]:
    targets = {_normalize(label).strip() for label in labels}
    found: list[str] = []
    for _, fields in _source_containers(raw_data):
        for key, value in fields.items():
            if isinstance(key, str) and _normalize(key).strip() in targets:
                for text in _strings(value):
                    if text not in found:
                        found.append(text)
    if not found:
        return "", False
    # Keep each original value intact; separators only combine conflicting
    # alternatives for display. The uncombined source still lives in raw_data.
    return "; ".join(found), len(found) > 1


def _explicit_months(
    raw_data: Mapping[str, Any], keys: tuple[str, ...],
) -> tuple[tuple[int, ...] | None, bool]:
    """Return the unique explicit month list and whether lists conflicted."""
    candidates: list[tuple[int, ...]] = []
    containers = [record for record, _ in _source_containers(raw_data)]
    local = raw_data.get("local_sheet")
    if isinstance(local, Mapping):
        characteristics = local.get("characteristics")
        if isinstance(characteristics, Mapping):
            containers.append(characteristics)
    for container in containers:
        for key in keys:
            candidate = container.get(key)
            if not isinstance(candidate, (list, tuple)) or not candidate:
                continue
            if any(type(month) is not int or not 1 <= month <= 12 for month in candidate):
                continue
            normalized = tuple(candidate)
            if len(set(normalized)) != len(normalized):
                continue
            if normalized not in candidates:
                candidates.append(normalized)
    if len(candidates) > 1:
        return None, True
    return (candidates[0] if candidates else None), False


def botanical_calendar_from_raw_data(raw_data: Mapping[str, Any] | None) -> BotanicalCalendar:
    """Extract safe monthly periods from legacy local source payloads.

    Supports the already-installed ``local_sheet.characteristics`` and
    ``promesse_de_fleurs.fields`` shapes, plus explicit numeric ``*_mois`` /
    ``*_months`` lists. It does not invent dates from frost offsets or free-form
    season words. All original records remain untouched in ``raw_data``.
    """
    if not isinstance(raw_data, Mapping):
        return _EMPTY_CALENDAR

    periods: dict[str, MonthlyPeriod] = {}
    for activity, label in _SOURCE_LABELS.items():
        source_text, conflicting_text = _source_text(raw_data, label)
        text_months = parse_months(source_text) if source_text and not conflicting_text else None
        explicit, conflicting_explicit = _explicit_months(
            raw_data, _EXPLICIT_MONTH_KEYS[activity]
        )
        conflicts_with_text = (
            explicit is not None
            and text_months is not None
            and set(explicit) != set(text_months)
        )
        if conflicting_text or conflicting_explicit or conflicts_with_text:
            months = None
        else:
            months = text_months if text_months is not None else explicit
        periods[activity] = MonthlyPeriod(months=months, source_text=source_text)
    return BotanicalCalendar(**periods)


def calendar_for_species(plant: PlantSpeciesData) -> BotanicalCalendar:
    """Read a species' saved calendar or derive one from its historical source.

    Existing structured values take precedence so manual edits survive reloads.
    Older imports without that key are parsed on read and are never mutated.
    """
    raw_data = getattr(plant, "raw_data", None)
    if not isinstance(raw_data, Mapping):
        return _EMPTY_CALENDAR
    stored = raw_data.get("botanical_calendar")
    if isinstance(stored, Mapping):
        historical = botanical_calendar_from_raw_data(raw_data)
        resolved: dict[str, MonthlyPeriod] = {}
        for activity in _SOURCE_LABELS:
            value = stored.get(activity)
            try:
                resolved[activity] = MonthlyPeriod.from_dict(value)
            except (TypeError, ValueError):
                # An invalid band must not hide other saved edits or the
                # historical source value for that same band.
                resolved[activity] = getattr(historical, activity)
        return BotanicalCalendar(**resolved)
    return botanical_calendar_from_raw_data(raw_data)


def has_botanical_periods(calendar: BotanicalCalendar) -> bool:
    """Whether the calendar contains an imported value worth serializing."""
    return any(
        period.months is not None or period.source_text
        for period in (getattr(calendar, key) for key in CALENDAR_ACTIVITIES)
    )
