"""Read local plant source files without installing them into an OGP store.

JSON records are delegated to :mod:`local_plant_import`, which owns the
validated JSON sheet mapping. Saved Promesse de Fleurs product pages are
adapted in memory; this module has no UI or persistence side effects.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from open_garden_planner.models.plant_data import (
    GrowthRate,
    PlantSpeciesData,
    SunRequirement,
    WaterNeeds,
    species_key,
)
from open_garden_planner.services.botanical_calendar import (
    calendar_for_species,
    has_botanical_periods,
)
from open_garden_planner.services.local_plant_import import (
    MAX_SOURCE_FILE_BYTES,
    MAX_SOURCE_FILES,
    MAX_SOURCE_TOTAL_BYTES,
    read_sheet_bytes,
    read_source_bytes,
)

# Kept for existing callers; the same limits now apply to JSON and HTML.
MAX_HTML_FILE_BYTES = MAX_SOURCE_FILE_BYTES
MAX_HTML_TOTAL_BYTES = MAX_SOURCE_TOTAL_BYTES
MAX_HTML_FILES = MAX_SOURCE_FILES

_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}


@dataclass(slots=True)
class _Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[_Node | str] = field(default_factory=list)


class _DocumentParser(HTMLParser):
    """Small tolerant tree builder for the few HTML structures used here."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag.lower(), {key.lower(): value or "" for key, value in attrs})
        self.stack[-1].children.append(node)
        if node.tag not in _VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.stack[-1].children.append(
            _Node(tag.lower(), {key.lower(): value or "" for key, value in attrs})
        )

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1].children.append(data)


def _children(node: _Node, tag: str | None = None) -> list[_Node]:
    return [child for child in node.children
            if isinstance(child, _Node) and (tag is None or child.tag == tag)]


def _text(node: _Node | str) -> str:
    if isinstance(node, str):
        return " ".join(node.split())
    if node.tag in {"script", "style", "noscript"}:
        return ""
    parts = [_text(child) for child in node.children]
    return " ".join(part for part in parts if part).strip()


def _normalize(value: str) -> str:
    folded = unicodedata.normalize("NFD", value.casefold())
    ascii_text = "".join(char for char in folded if unicodedata.category(char) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_text).split())


_LABELS = {
    "hauteur a maturite": "Hauteur à maturité",
    "largeur a maturite": "Largeur à maturité",
    "exposition": "Exposition",
    "humidite du sol": "Humidité du sol",
    "type de sol": "Type de sol",
    "ph du sol": "pH du sol",
    "fleur de couleur": "Fleur de couleur",
    "feuillage de couleur": "Feuillage de couleur",
    "croissance": "Croissance",
    "periode de floraison": "Période de floraison",
    "periode raisonnable de plantation": "Période raisonnable de plantation",
    "meilleure periode de plantation": "Meilleure période de plantation",
    "periode optimale de plantation": "Période optimale de plantation",
    "periode de fructification": "Période de fructification",
    "periode de multiplication": "Période de multiplication",
    "rusticite": "Rusticité",
    "fleur de": "Fleur de",
    "inflorescence": "Inflorescence",
    "port": "Port",
    "persistance feuillage": "Persistance feuillage",
    "origine": "Origine",
    "genre": "Genre",
    "espece": "Espèce",
    "cultivar": "Cultivar",
    "famille": "Famille",
    "nom botanique": "Nom botanique",
    "autres noms communs": "Autres noms communs",
    "difficulte de culture": "Difficulté de culture",
    "climat de preference": "Climat de préférence",
    "type d utilisation": "Type d'utilisation",
    "convient pour": "Convient pour",
    "periode de recolte": "Période de récolte",
    "diametre du fruit": "Diamètre du fruit",
    "periode de semis": "Période de semis",
    "mode de semis": "Mode de semis",
    "taille": "Taille",
    "taille conseillee": "Taille conseillée",
    "periode de taille": "Période de taille",
    "descriptif taille": "Descriptif taille",
    "hivernage": "Hivernage",
    "resistance aux maladies": "Résistance aux maladies",
    "plante mellifere": "Plante mellifère",
    "aromatique": "Aromatique?",
    "densite de plantation": "Densité de plantation",
}


def _label(value: str) -> str | None:
    candidate = _normalize(value.rstrip(":"))
    return _LABELS.get(candidate)


def _jsonld_objects(root: _Node) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            kind = value.get("@type", [])
            kinds = [kind] if isinstance(kind, str) else kind
            if isinstance(kinds, list) and any(
                isinstance(item, str) and item.rsplit("/", 1)[-1] == "Product"
                for item in kinds
            ):
                products.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for node in _walk(root):
        if node.tag != "script" or _normalize(node.attrs.get("type", "")) != "application ld json":
            continue
        raw = "".join(child for child in node.children if isinstance(child, str)).strip()
        if not raw:
            continue
        try:
            decoded = json.loads(raw, parse_constant=lambda item: (_ for _ in ()).throw(
                ValueError(f"Invalid JSON-LD constant: {item}")))
        except (json.JSONDecodeError, ValueError):
            continue
        visit(decoded)
    return products


def _walk(node: _Node):
    yield node
    for child in _children(node):
        yield from _walk(child)


def _collect_fields(root: _Node) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}

    def add(label: str, value: str) -> None:
        canonical = _label(label)
        cleaned = " ".join(value.split())
        if canonical and cleaned:
            values = fields.setdefault(canonical, [])
            if cleaned not in values:
                values.append(cleaned)

    for node in _walk(root):
        # Standard definition lists and table rows.
        if node.tag == "dl":
            pending_label = ""
            for child in _children(node):
                value = _text(child)
                if child.tag == "dt":
                    pending_label = value
                elif child.tag == "dd" and pending_label:
                    add(pending_label, value)
                    pending_label = ""
        elif node.tag == "tr":
            cells = [child for child in _children(node) if child.tag in {"th", "td"}]
            if len(cells) >= 2:
                add(_text(cells[0]), _text(cells[1]))

        # Promesse de Fleurs renders some labels and values as adjacent blocks
        # (including headings for its botanical taxonomy section).
        for index, child in enumerate(node.children):
            if not isinstance(child, _Node):
                continue
            field_name = _label(_text(child))
            if not field_name:
                continue
            for next_child in node.children[index + 1:]:
                value = _text(next_child)
                if value:
                    add(field_name, value)
                    break
    return fields


def _single(fields: dict[str, list[str]], label: str) -> str:
    values = fields.get(label, [])
    return values[0] if len(values) == 1 else ""


def _dimension(value: str) -> float | None:
    match = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(cm|m)\s*", value, re.IGNORECASE)
    if match is None:
        return None
    result = float(match[1].replace(",", ".")) * (100 if match[2].casefold() == "m" else 1)
    return result if math.isfinite(result) and result > 0 else None


def _meta(root: _Node) -> dict[str, str]:
    result: dict[str, str] = {}
    for node in _walk(root):
        if node.tag == "meta":
            key = node.attrs.get("property") or node.attrs.get("name")
            if key and node.attrs.get("content"):
                result[key.casefold()] = node.attrs["content"].strip()
    return result


def _canonical_url(root: _Node, meta: dict[str, str], products: list[dict[str, Any]]) -> str:
    for node in _walk(root):
        if node.tag == "link" and "canonical" in node.attrs.get("rel", "").casefold().split():
            href = node.attrs.get("href", "").strip()
            if href:
                return href
    for key in ("og:url", "twitter:url"):
        if meta.get(key):
            return meta[key]
    if len(products) == 1 and isinstance(products[0].get("url"), str):
        return products[0]["url"].strip()
    return ""


def _first_product_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return next((item.strip() for item in value if isinstance(item, str) and item.strip()), "")
    if isinstance(value, dict):
        text = value.get("@value")
        return text.strip() if isinstance(text, str) else ""
    return ""


def _product_page(path: Path, source: bytes) -> PlantSpeciesData:
    if len(source) > MAX_HTML_FILE_BYTES:
        raise ValueError(f"HTML plant source exceeds {MAX_HTML_FILE_BYTES} bytes: {path.name}")
    try:
        document = source.decode("utf-8-sig")
    except UnicodeDecodeError:
        document = source.decode("cp1252")

    parser = _DocumentParser()
    parser.feed(document)
    parser.close()
    root = parser.root
    meta = _meta(root)
    products = _jsonld_objects(root)
    url = _canonical_url(root, meta, products)
    host = urlsplit(url).hostname or ""
    provider_mark = _normalize(meta.get("og:site_name", ""))
    is_promesse = (host == "promessedefleurs.com"
                   or host.endswith(".promessedefleurs.com")
                   or "promesse de fleurs" in provider_mark)
    if not is_promesse:
        raise ValueError(f"Unsupported local HTML plant source: {path.name}")
    if len(products) > 1:
        raise ValueError(f"HTML contains multiple product records: {path.name}")

    fields = _collect_fields(root)
    genus = _single(fields, "Genre")
    species = _single(fields, "Espèce")
    explicit_scientific_name = _single(fields, "Nom botanique")
    if explicit_scientific_name:
        scientific_name = explicit_scientific_name
    elif genus and species:
        scientific_name = f"{genus} {species}"
        cultivar = _single(fields, "Cultivar")
        if cultivar:
            clean_cultivar = cultivar.strip().strip("'\"‘’“”")
            scientific_name += f" '{clean_cultivar}'"
    else:
        scientific_name = ""

    product = products[0] if products else {}
    title = ""
    for node in _walk(root):
        if node.tag == "h1" and _text(node):
            title = _text(node)
            break
    if not title:
        title = _first_product_text(product.get("name"))
    if not title:
        title = meta.get("og:title", "")
    if not title:
        for node in _walk(root):
            if node.tag == "title" and _text(node):
                title = _text(node)
                break
    aliases = _single(fields, "Autres noms communs")
    common_name = aliases or title
    has_product_identity = bool(genus and species) or bool(explicit_scientific_name)
    if not common_name or not (has_product_identity or len(products) == 1):
        raise ValueError(f"HTML is not an identifiable Promesse de Fleurs product page: {path.name}")

    description = (meta.get("description") or meta.get("og:description")
                   or _first_product_text(product.get("description")))
    image_url = meta.get("og:image", "") or _first_product_text(product.get("image"))
    if image_url and url:
        image_url = urljoin(url, image_url)

    growth = _normalize(_single(fields, "Croissance"))
    sun = _normalize(_single(fields, "Exposition"))
    water = _normalize(_single(fields, "Humidité du sol"))
    plant = PlantSpeciesData(
        scientific_name=scientific_name,
        common_name=common_name,
        family=_single(fields, "Famille"),
        genus=genus,
        max_height_cm=_dimension(_single(fields, "Hauteur à maturité")),
        max_spread_cm=_dimension(_single(fields, "Largeur à maturité")),
        growth_rate={"lente": GrowthRate.SLOW, "normale": GrowthRate.MEDIUM,
                     "rapide": GrowthRate.FAST, "tres rapide": GrowthRate.FAST}.get(
                         growth, GrowthRate.UNKNOWN),
        sun_requirement={"soleil": SunRequirement.FULL_SUN,
                         "mi ombre": SunRequirement.PARTIAL_SHADE,
                         "ombre": SunRequirement.FULL_SHADE}.get(
                             sun, SunRequirement.UNKNOWN),
        water_needs={"sol sec": WaterNeeds.LOW, "sol frais": WaterNeeds.MEDIUM}.get(
            water, WaterNeeds.UNKNOWN),
        soil_type=_single(fields, "Type de sol"),
        flower_color=_single(fields, "Fleur de couleur"),
        foliage_color=_single(fields, "Feuillage de couleur"),
        image_url=image_url,
        description=description,
        data_source="promesse_de_fleurs",
        source_id=("local-sheet:" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
                   if url else "local-html:" + hashlib.sha256(source).hexdigest()[:32]),
        slug=Path(urlsplit(url).path).stem if url else "",
        raw_data={
            "promesse_de_fleurs": {
                "source_file": path.name,
                "source_url": url,
                "sha256": hashlib.sha256(source).hexdigest(),
                "mapping_version": 1,
                "page_title": title,
                "image_url": image_url,
                "meta": meta,
                "fields": fields,
                "product_json_ld": product,
            }
        },
    )
    # This is also the installer's JSON safety boundary. Reject a malformed
    # source before it can become a partially usable import preview.
    json.dumps(plant.to_dict(), ensure_ascii=False, allow_nan=False)
    return plant


def read_local_plant_sources(source: Path) -> list[PlantSpeciesData]:
    """Read one JSON/HTML file or a selected, non-recursive directory.

    JSON sheets are parsed only by the established JSON sheet adapter.
    HTML is accepted only when it is an identifiable, single-product Promesse
    de Fleurs page. Other HTML, including irrigation print tables without an
    explicit botanical identity, is refused instead of being guessed into a
    species record. Nothing is written to a library or project file.
    """
    source = Path(source)
    suffixes = {".json", ".html", ".htm"}
    if source.is_dir():
        paths = sorted(path for path in source.iterdir()
                       if path.is_file() and path.suffix.casefold() in suffixes)
    elif source.is_file() and source.suffix.casefold() in suffixes:
        paths = [source]
    else:
        raise ValueError(f"Unsupported or missing plant source: {source}")
    if len(paths) > MAX_SOURCE_FILES:
        raise ValueError(f"Too many plant sources (maximum {MAX_SOURCE_FILES})")

    # Check the combined selection, rather than allowing each adapter its own
    # independent allowance. The bounded reader also protects against growth
    # between this preflight and parsing.
    total_bytes = 0
    for path in paths:
        size = path.stat().st_size
        if size > MAX_SOURCE_FILE_BYTES:
            raise ValueError(f"Plant source exceeds {MAX_SOURCE_FILE_BYTES} bytes: {path.name}")
        total_bytes += size
        if total_bytes > MAX_SOURCE_TOTAL_BYTES:
            raise ValueError(f"Plant sources exceed {MAX_SOURCE_TOTAL_BYTES} bytes in total")
    plants: list[PlantSpeciesData] = []
    total_bytes = 0
    for path in paths:
        original = read_source_bytes(path)
        total_bytes += len(original)
        if total_bytes > MAX_SOURCE_TOTAL_BYTES:
            raise ValueError(f"Plant sources exceed {MAX_SOURCE_TOTAL_BYTES} bytes in total")
        try:
            if path.suffix.casefold() == ".json":
                plant = read_sheet_bytes(path, original)
            else:
                plant = _product_page(path, original)
        except RecursionError as error:
            raise ValueError(f"Plant source is too deeply nested: {path.name}") from error
        # This wrapper is used only for user-selected local files. The HTML
        # adapter is also used for network responses and must not invent a
        # local origin there.
        plant.raw_data["source_file_uri"] = path.resolve().as_uri()
        plants.append(plant)

    identities: set[str] = set()
    for plant in plants:
        calendar = calendar_for_species(plant)
        if has_botanical_periods(calendar):
            plant.raw_data["botanical_calendar"] = calendar.to_dict()
        identity = species_key(plant.to_dict())
        if identity == "_unknown":
            raise ValueError("Plant identity is missing")
        if identity in identities:
            raise ValueError(f"Duplicate plant source identity: {identity}")
        identities.add(identity)
    if not plants:
        raise ValueError(f"No supported plant source files found in {source}")
    return plants
