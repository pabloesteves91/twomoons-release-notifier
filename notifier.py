#!/usr/bin/env python3
"""Erkennt neue Produkte im Shop twomoons.ch und postet sie per Discord-Webhook.

Der Ablauf eines Laufs:

1. Produkt-URLs ermitteln (Sitemap, ersatzweise Listing-Seiten mit Paginierung)
2. gegen die gemerkten URLs in ``state.json`` abgleichen
3. nur die neuen URLs als Produktseite abrufen und auswerten
4. je Produkt ein Embed nach Discord posten und den Stand speichern

Beim allerersten Lauf wird nichts gepostet, sondern nur der Ist-Stand gemerkt.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import itertools
import json
import logging
import os
import random
import re
import sys
import time
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urljoin, urlsplit, urlunsplit
from urllib.request import url2pathname

import requests
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

LOG = logging.getLogger("twomoons")

DEFAULT_CONFIG = Path("config.json")
DEFAULT_STATE = Path("state.json")
STATE_VERSION = 1

# Auszeichnung, die eine Zeile nicht umbricht ("<b>Sprache:</b> Deutsch" ist eine Zeile).
INLINE_TAGS = {"a", "b", "code", "em", "font", "i", "label", "small", "span", "strong", "sub", "sup", "u"}
EMPHASIS_TAGS = {"b", "strong"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "dt", "th", "legend", "caption"}

# Bedienelemente und Seitenrahmen (Navigation, Footer, Buttons) sind kein Inhalt.
SKIP_TAGS = {
    "aside",
    "button",
    "footer",
    "form",
    "header",
    "input",
    "nav",
    "script",
    "select",
    "style",
    "svg",
    "textarea",
}
SKIP_CLASSES = {
    "breadcrumb",
    "btn",
    "btn-close",
    "close",
    "cookie-permission",
    "offcanvas",
    "sr-only",
    "visually-hidden",
}

# Badge-Texte, die keine Produktauszeichnung sind (Warenkorb-Zähler, Bewertungen).
BADGE_DENYLIST = {"0", "1", "2", "3", "4", "5", "neu!", "%"}

PRICE_RE = re.compile(r"(?:ab\s+)?(?:chf|fr\.?)\s*[\d'’.,]+", re.IGNORECASE)
# "Ab CHF 114.90" ist ein Ab-Preis; "Ab 4" in der Staffelpreis-Tabelle ist eine Stückzahl.
FROM_PRICE_RE = re.compile(r"\bab\s+(?:chf|fr\.?)\s*\d", re.IGNORECASE)


@dataclass
class Product:
    """Ein Produkt, so wie es von der Produktseite gelesen wurde."""

    url: str
    name: str = ""
    price: str = ""
    languages: list[str] = field(default_factory=list)
    badges: list[str] = field(default_factory=list)
    manufacturer: str = ""
    image_url: str = ""
    variant_text: str = ""
    categories: list[str] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)
    matched: dict[str, str] = field(default_factory=dict)

    @property
    def is_product(self) -> bool:
        return bool(self.name)


# --------------------------------------------------------------------------- #
# Hilfsfunktionen
# --------------------------------------------------------------------------- #


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()


def has_class(node: Any, css_class: str) -> bool:
    return isinstance(node, Tag) and css_class in (node.get("class") or [])


def is_skippable(node: Tag) -> bool:
    if node.name in SKIP_TAGS:
        return True
    return bool(set(node.get("class") or []) & SKIP_CLASSES)


@dataclass
class Line:
    text: str
    heading: bool
    block: int


def extract_lines(root: Tag) -> list[Line]:
    """Sichtbare Zeilen eines Elements.

    Inline-Auszeichnung bleibt in derselben Zeile, damit ``<b>Sprache:</b> Deutsch``
    als eine Zeile ankommt; ``<br>`` und Block-Elemente trennen. HTML-Kommentare
    sind ebenfalls ``NavigableString`` und würden sonst als Text auftauchen —
    Shopware-Templates enthalten Kommentare wie "@deprecated tag:v6.8.0".
    """
    lines: list[Line] = []
    buffer: list[tuple[str, bool]] = []
    blocks = itertools.count()
    block = next(blocks)

    def flush() -> None:
        pieces = [(text, emphasised) for text, emphasised in buffer if clean_text(text)]
        buffer.clear()
        text = clean_text(" ".join(piece for piece, _ in pieces))
        if text:
            heading = bool(pieces) and all(flag for _, flag in pieces)
            lines.append(Line(text=text, heading=heading, block=block))

    def walk(node: Tag, emphasis: int) -> None:
        nonlocal block
        for child in node.children:
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                buffer.append((str(child), emphasis > 0))
            elif isinstance(child, Tag):
                if is_skippable(child):
                    continue
                if child.name == "br":
                    flush()
                elif child.name in INLINE_TAGS:
                    walk(child, emphasis + (1 if child.name in EMPHASIS_TAGS else 0))
                else:
                    flush()
                    block = next(blocks)
                    walk(child, emphasis + (1 if child.name in HEADING_TAGS else 0))
                    flush()
                    block = next(blocks)

    walk(root, 0)
    flush()
    return lines


def element_text(node: Tag | None) -> str:
    """Text eines Elements ohne Kommentare und ohne Bedienelemente."""
    if node is None:
        return ""
    if node.name == "meta":
        return clean_text(node.get("content"))
    return clean_text(" ".join(line.text for line in extract_lines(node)))


def select_first(soup: BeautifulSoup | Tag, selectors: list[str]) -> tuple[Tag | None, str]:
    """Erstes Element, auf das einer der Selektoren passt — samt Selektor für das Log."""
    for selector in selectors:
        try:
            node = soup.select_one(selector)
        except Exception as error:  # ein kaputter Selektor darf den Lauf nicht stoppen
            LOG.warning("Selektor '%s' ist ungültig: %s", selector, error)
            continue
        if node is not None:
            return node, selector
    return None, ""


def text_from(soup: BeautifulSoup | Tag, selectors: list[str]) -> tuple[str, str]:
    for selector in selectors:
        try:
            nodes = soup.select(selector)
        except Exception as error:
            LOG.warning("Selektor '%s' ist ungültig: %s", selector, error)
            continue
        for node in nodes:
            text = element_text(node)
            if text:
                return text, selector
    return "", ""


def normalise_url(url: str) -> str:
    """URL-Form vereinheitlichen, damit dasselbe Produkt nur einen Schlüssel hat."""
    parts = urlsplit(clean_text(url))
    if not parts.scheme:
        return clean_text(url)
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def compile_patterns(patterns: list[str]) -> list[re.Pattern[str]]:
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error as error:
            LOG.error("Filter-Muster '%s' ist kein gültiger Ausdruck: %s", pattern, error)
    return compiled


def blocked_category(product: "Product", config: dict[str, Any]) -> str:
    """Prüft den Kategorienpfad gegen die Filterlisten.

    Einzelkarten und Zubehör liegen auf twomoons.ch wie alle Produkte direkt
    auf der Wurzelebene (/zubat-zubat) — über die URL sind sie nicht zu
    trennen. Die Breadcrumb verrät die Kategorie und filtert zuverlässig.
    """
    filters = config.get("filters", {})
    haystack = " | ".join(product.categories).casefold()
    if not haystack:
        return ""

    for wanted in filters.get("exclude_categories", []):
        if str(wanted).casefold() in haystack:
            return str(wanted)

    includes = filters.get("include_categories", [])
    if includes and not any(str(wanted).casefold() in haystack for wanted in includes):
        return "(keine erlaubte Kategorie)"
    return ""


def url_matches(url: str, patterns: list[re.Pattern[str]]) -> str:
    for pattern in patterns:
        if pattern.search(url):
            return pattern.pattern
    return ""


# --------------------------------------------------------------------------- #
# Abrufe
# --------------------------------------------------------------------------- #


def fetch_bytes(url: str, request_config: dict[str, Any]) -> bytes:
    """Lädt eine URL mit Wiederholung; ``file://`` für Testläufe ohne Netz."""
    if url.startswith("file://"):
        return Path(url2pathname(urlsplit(url).path)).read_bytes()

    headers = {
        "User-Agent": request_config.get("user_agent", "twomoons-release-notifier/1.0"),
        "Accept-Language": "de-CH,de;q=0.9,en;q=0.6",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    retries = int(request_config.get("retries", 3))
    timeout = int(request_config.get("timeout", 30))

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response.content
        except requests.RequestException as error:
            last_error = error
            LOG.warning("Abruf fehlgeschlagen (%s/%s) für %s: %s", attempt, retries, url, error)
            if attempt < retries:
                time.sleep(2**attempt)
    raise RuntimeError(f"Konnte {url} nicht laden: {last_error}")


def decode_body(payload: bytes) -> str:
    """Entpackt gzip-Sitemaps (erkannt am Magic-Byte, nicht an der Endung)."""
    if payload[:2] == b"\x1f\x8b":
        payload = gzip.decompress(payload)
    return payload.decode("utf-8", errors="replace")


def fetch_text(url: str, request_config: dict[str, Any]) -> str:
    return decode_body(fetch_bytes(url, request_config))


def local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_sitemap(xml_text: str) -> tuple[list[str], list[tuple[str, str]]]:
    """Liefert (Teil-Sitemaps, [(URL, lastmod)]) — Index und Sitemap in einem."""
    try:
        root = ElementTree.fromstring(xml_text.strip())
    except ElementTree.ParseError as error:
        raise RuntimeError(f"Sitemap ist kein gültiges XML: {error}") from error

    children: list[str] = []
    entries: list[tuple[str, str]] = []
    container = local_tag(root.tag)

    for node in root:
        name = local_tag(node.tag)
        location = ""
        lastmod = ""
        for field_node in node:
            field_name = local_tag(field_node.tag)
            if field_name == "loc":
                location = clean_text(field_node.text)
            elif field_name == "lastmod":
                lastmod = clean_text(field_node.text)
        if not location:
            continue
        if name == "sitemap" or container == "sitemapindex":
            children.append(location)
        else:
            entries.append((location, lastmod))
    return children, entries


def collect_sitemap_urls(
    sitemap_url: str,
    request_config: dict[str, Any],
    max_sitemaps: int = 50,
) -> dict[str, str]:
    """Alle URLs aus Sitemap bzw. Sitemap-Index als {URL: lastmod}."""
    found: dict[str, str] = {}
    queue = [sitemap_url]
    visited: set[str] = set()

    while queue and len(visited) < max_sitemaps:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        try:
            children, entries = parse_sitemap(fetch_text(current, request_config))
        except Exception as error:
            LOG.warning("Sitemap %s nicht lesbar: %s", current, error)
            continue
        LOG.info("Sitemap %s: %s Teil-Sitemap(s), %s URL(s)", current, len(children), len(entries))
        queue.extend(children)
        for location, lastmod in entries:
            key = normalise_url(location)
            if key and (key not in found or lastmod > found[key]):
                found[key] = lastmod
    return found


def collect_listing_urls(config: dict[str, Any]) -> dict[str, str]:
    """Ersatzquelle: Produktlinks von den Listing-Seiten, Seite für Seite."""
    shop = config.get("shop", {})
    request_config = config.get("request", {})
    listing_urls = shop.get("listing_urls", [])
    max_pages = int(shop.get("max_listing_pages", 10))
    pause = float(request_config.get("delay_between_requests", 1.0))

    found: dict[str, str] = {}
    for listing_url in listing_urls:
        for page in range(1, max_pages + 1):
            separator = "&" if "?" in listing_url else "?"
            url = listing_url if page == 1 else f"{listing_url}{separator}p={page}"
            try:
                html = fetch_text(url, request_config)
            except Exception as error:
                LOG.warning("Listing %s nicht lesbar: %s", url, error)
                break

            soup = BeautifulSoup(html, "html.parser")
            page_links = 0
            for anchor in soup.select("a.product-name, a.product-image-link, .product-box a[href]"):
                href = str(anchor.get("href") or "")
                if not href or href.startswith("#"):
                    continue
                key = normalise_url(urljoin(url, href))
                if key not in found:
                    found[key] = ""
                page_links += 1

            LOG.info("Listing %s: %s Produktlink(s)", url, page_links)
            if not page_links:
                break
            time.sleep(pause)
    return found


def listing_blocks(soup: BeautifulSoup) -> list[tuple[str, Tag]]:
    """Abschnitte einer Seite als (Überschrift, Block) — z. B. "Neu im Shop"."""
    blocks: list[tuple[str, Tag]] = []
    for block in soup.select("div.cms-block"):
        if not block.select(".product-box"):
            continue
        heading_node = block.select_one("h1, h2, h3, h4, .base-slider-controls-title")
        blocks.append((element_text(heading_node), block))
    return blocks


def urls_from_listing(html: str, page_url: str, heading: str = "") -> list[str]:
    """Produktlinks eines Abschnitts, in der Reihenfolge der Seite.

    Ohne `heading` werden alle Abschnitte genommen. Mit `heading` nur der, dessen
    Überschrift den Text enthält ("Neu im Shop").
    """
    soup = BeautifulSoup(html, "html.parser")
    wanted = clean_text(heading).casefold()

    found: list[str] = []
    for title, block in listing_blocks(soup):
        if wanted and wanted not in title.casefold():
            continue
        for card in block.select(".product-box"):
            for anchor in card.select("a[href]"):
                href = str(anchor.get("href") or "")
                if not href or href.startswith("#"):
                    continue
                url = normalise_url(urljoin(page_url, href))
                if url not in found:
                    found.append(url)
                break
        if wanted:
            break
    return found


def discover_urls(config: dict[str, Any]) -> dict[str, str]:
    """Produktkandidaten ermitteln — Sitemap zuerst, Listing-Seiten als Ersatz."""
    shop = config.get("shop", {})
    request_config = config.get("request", {})
    found: dict[str, str] = {}

    sitemap_url = shop.get("sitemap_url", "")
    if sitemap_url:
        found = collect_sitemap_urls(sitemap_url, request_config, int(shop.get("max_sitemaps", 50)))
        LOG.info("Sitemap insgesamt: %s URL(s)", len(found))

    if not found or shop.get("always_use_listings", False):
        if not found:
            LOG.warning("Sitemap lieferte nichts — es wird auf die Listing-Seiten ausgewichen")
        for url, lastmod in collect_listing_urls(config).items():
            found.setdefault(url, lastmod)
    return found


def filter_urls(urls: dict[str, str], config: dict[str, Any]) -> tuple[dict[str, str], dict[str, int]]:
    """Wendet Einschluss- und Ausschlussmuster an; liefert (Treffer, Statistik)."""
    filters = config.get("filters", {})
    includes = compile_patterns(filters.get("include_patterns", []))
    excludes = compile_patterns(filters.get("exclude_patterns", []))

    kept: dict[str, str] = {}
    dropped: dict[str, int] = {}
    for url, lastmod in urls.items():
        if includes and not url_matches(url, includes):
            dropped["(kein include-Muster)"] = dropped.get("(kein include-Muster)", 0) + 1
            continue
        hit = url_matches(url, excludes)
        if hit:
            dropped[hit] = dropped.get(hit, 0) + 1
            continue
        kept[url] = lastmod
    return kept, dropped


# --------------------------------------------------------------------------- #
# Produktseite auswerten
# --------------------------------------------------------------------------- #


def product_container(soup: BeautifulSoup, config: dict[str, Any]) -> tuple[Tag, str]:
    """Nur der Bereich des gezeigten Produkts — nie die ganze Seite.

    Auf twomoons.ch stehen unter dem Produkt Empfehlungs-Slider, deren Karten
    dieselben Klassen tragen (.product-badges, .twomoons-language-badges).
    Wer die ganze Seite auswertet, übernimmt deren Badges und Sprachflaggen.
    Der Kaufen-Bereich steckt im CMS-Block des Produkts — von dort aus nach
    oben bis zum umschliessenden cms-block ist genau die richtige Grenze.
    """
    product_config = config.get("product", {})
    anchor, _ = select_first(soup, product_config.get("container_anchors", ["div.product-detail-buy"]))
    if anchor is not None:
        for parent in anchor.parents:
            if not isinstance(parent, Tag):
                continue
            classes = parent.get("class") or []
            if "cms-block" in classes:
                name = next((css for css in classes if css.startswith("cms-block-")), "cms-block")
                return parent, f"div.{name}"

    selectors = product_config.get("container_selectors", ["main"])
    node, selector = select_first(soup, selectors)
    if node is not None:
        return node, selector
    return soup.body or soup, "(ganze Seite)"


def looks_like_product(soup: BeautifulSoup, config: dict[str, Any]) -> str:
    """Leerer String = keine Produktseite (Kategorie, CMS-Seite, Blog …)."""
    for selector in config.get("product", {}).get("product_markers", []):
        try:
            if soup.select_one(selector) is not None:
                return selector
        except Exception as error:
            LOG.warning("Marker-Selektor '%s' ist ungültig: %s", selector, error)
    return ""


def parse_heading(heading: Tag | None, product: "Product") -> tuple[str, str]:
    """Zerlegt die Überschrift der Produktseite in ihre drei Teile.

    Der Shop baut sie so auf:
        <h1 class="product-detail-name">
          <span class="subheadings">Rebel Sp. z o.o.</span><br>
          Zug um Zug Weltreise<br>
          <span class="product-variant-characteristics-option">Deutsch</span>
        </h1>
    Ohne diese Zerlegung stünden Serie und Variante mitten im Produktnamen.
    """
    if heading is None:
        return "", ""

    variant_nodes = heading.select(".product-variant-characteristics-option")
    product.variant_text = " ".join(filter(None, (element_text(node) for node in variant_nodes)))

    series_node = heading.select_one(".subheadings")
    if series_node is not None and not has_class(series_node, "product-variant-characteristics-option"):
        series = element_text(series_node)
        if series:
            product.manufacturer = series

    for node in heading.select(".subheadings, .product-variant-characteristics-option"):
        node.extract()

    name = element_text(heading)
    return name, "h1.product-detail-name (ohne Serie und Variante)"


def parse_properties(soup: BeautifulSoup) -> dict[str, str]:
    """Die Eigenschaften-Tabelle der Produktseite ("Brand: Pokémon, Sitting Cuties").

    Die Tabelle gibt es nur beim gezeigten Produkt, nicht in den Slider-Karten.
    """
    properties: dict[str, str] = {}
    for row in soup.select(".product-detail-properties-table tr"):
        label_node = row.select_one("th, .properties-label")
        value_node = row.select_one("td, .properties-value")
        label = clean_text(label_node.get_text() if label_node else "").rstrip(":")
        value = clean_text(value_node.get_text() if value_node else "")
        if label and value:
            properties[label] = value
    return properties


def parse_categories(soup: BeautifulSoup) -> list[str]:
    """Kategorienpfad aus der Breadcrumb — Grundlage für die Filterlisten."""
    categories: list[str] = []
    for item in soup.select(".breadcrumb .breadcrumb-link, .breadcrumb .breadcrumb-title, .breadcrumb li"):
        text = clean_text(item.get_text())
        if text and text not in categories:
            categories.append(text)
    return categories


def format_amount(amount: str, currency: str) -> str:
    """"19.9" wird zu "CHF 19.90" — so, wie der Shop es schreibt."""
    try:
        return f"{currency} {float(amount.replace(chr(39), '').replace(',', '.')):.2f}"
    except ValueError:
        return clean_text(f"{currency} {amount}")


def parse_price(soup: BeautifulSoup, container: Tag, config: dict[str, Any]) -> tuple[str, str]:
    """Preis des gezeigten Produkts, inklusive der Form "Ab CHF 114.90".

    Zuerst der ausgezeichnete Wert (``meta[itemprop=price]``) aus dem
    Preisblock: Bei Produkten mit Staffelpreisen stehen im sichtbaren Text
    mehrere Beträge, und der erste ist dann der falsche.
    """
    product_config = config.get("product", {})
    price_block, selector = select_first(container, product_config.get("price_selectors", []))
    if price_block is None:
        price_block, selector = select_first(soup, product_config.get("price_selectors", []))

    scope = price_block if price_block is not None else container
    text = element_text(scope)
    # "Ab CHF 114.90" ist ein Ab-Preis; "Ab 4" in der Staffelpreis-Tabelle
    # ist eine Stückzahl und darf den Preis nicht verfälschen.
    starts_from = FROM_PRICE_RE.search(text) is not None

    meta = scope.select_one("meta[itemprop='price']") or soup.select_one("meta[itemprop='price']")
    if meta is not None and clean_text(meta.get("content")):
        currency_node = soup.select_one("meta[itemprop='priceCurrency']")
        currency = clean_text(currency_node.get("content")) if currency_node else "CHF"
        price = format_amount(clean_text(meta.get("content")), currency or "CHF")
        return (f"Ab {price}" if starts_from else price), f"{selector} (meta[itemprop=price])"

    matches = PRICE_RE.findall(text)
    if matches:
        price = re.sub(r"^ab\s+", "", clean_text(matches[0]), flags=re.IGNORECASE)
        price = re.sub(r"^chf", "CHF", price, flags=re.IGNORECASE)
        return (f"Ab {price}" if starts_from else price), selector
    return "", selector


def parse_badges(soup: BeautifulSoup, container: Tag, config: dict[str, Any]) -> tuple[list[str], str]:
    """Auszeichnungen wie "Neu" oder "Vorbestellung" von der Produktseite."""
    product_config = config.get("product", {})
    aliases = {key.lower(): value for key, value in product_config.get("badge_aliases", {}).items()}
    badges: list[str] = []
    used = ""

    for selector in product_config.get("badge_selectors", []):
        try:
            # Bewusst ohne Rückfall auf die ganze Seite: die Empfehlungs-Slider
            # tragen dieselben Klassen und lieferten sonst fremde Badges.
            nodes = container.select(selector)
        except Exception as error:
            LOG.warning("Badge-Selektor '%s' ist ungültig: %s", selector, error)
            continue
        for node in nodes:
            text = element_text(node)
            if not text or len(text) > 40 or text.lower() in BADGE_DENYLIST:
                continue
            label = aliases.get(text.lower(), text)
            if label not in badges:
                badges.append(label)
                used = used or selector
    return badges, used


def parse_languages(product: Product, container: Tag, config: dict[str, Any]) -> list[str]:
    """Sprachen aus der Sprachflagge des Shops, den Varianten und dem Namen.

    Der Shop setzt eine eigene Flagge (``img.twomoons-language-badge``) mit der
    Sprache im ``title``/``alt``. Das ist die verlässlichste Quelle — nur eben
    ausschliesslich innerhalb des Produktbereichs, weil die Empfehlungskarten
    dieselbe Flagge für fremde Produkte tragen.
    """
    product_config = config.get("product", {})
    languages: list[str] = []

    for selector in product_config.get("language_badge_selectors", []):
        for node in container.select(selector):
            label = clean_text(node.get("title") or node.get("alt") or node.get_text())
            if label and label not in languages:
                languages.append(label)
    if languages:
        return languages

    haystack_parts = [product.name, product.variant_text]

    for selector in product_config.get("variant_selectors", []):
        try:
            nodes = container.select(selector)
        except Exception as error:
            LOG.warning("Varianten-Selektor '%s' ist ungültig: %s", selector, error)
            continue
        for node in nodes:
            haystack_parts.append(element_text(node))
            haystack_parts.append(clean_text(node.get("title")))

    haystack = " ".join(part for part in haystack_parts if part).lower()
    for label, markers in config.get("languages", {}).items():
        for marker in markers:
            if re.search(rf"(?<![\wäöüéèà]){re.escape(marker.lower())}(?![\wäöüéèà])", haystack):
                if label not in languages:
                    languages.append(label)
                break
    return languages


def parse_product(html: str, url: str, config: dict[str, Any]) -> Product:
    """Liest eine Produktseite. Ohne Namen gilt die Seite als kein Produkt."""
    soup = BeautifulSoup(html, "html.parser")
    product = Product(url=url)

    marker = looks_like_product(soup, config)
    if not marker:
        LOG.debug("%s: kein Produktmerkmal gefunden", url)
        return product

    container, container_selector = product_container(soup, config)
    product_config = config.get("product", {})

    heading = container.select_one("h1.product-detail-name") or container.select_one(".product-detail-name")
    name, name_selector = parse_heading(heading, product)
    if not name:
        name, name_selector = text_from(container, product_config.get("name_selectors", []))
    if not name:
        node, name_selector = select_first(soup, ["meta[property='og:title']", "title"])
        name = element_text(node)
        name = re.sub(r"\s*[|·-]\s*TwoMoons.*$", "", name, flags=re.IGNORECASE)
    if not name:
        return product
    product.name = name

    variant_selector = "Überschrift" if product.variant_text else ""
    if not product.variant_text:
        product.variant_text, variant_selector = text_from(container, product_config.get("variant_text_selectors", []))
    product.price, price_selector = parse_price(soup, container, config)
    product.properties = parse_properties(soup)
    product.categories = parse_categories(soup)

    manufacturer_selector = "Überschrift" if product.manufacturer else ""
    if not product.manufacturer:
        product.manufacturer, manufacturer_selector = text_from(container, product_config.get("manufacturer_selectors", []))
    if not product.manufacturer:
        wanted = [key.lower() for key in product_config.get("manufacturer_properties", [])]
        for label, value in product.properties.items():
            if label.lower() in wanted:
                product.manufacturer = value
                manufacturer_selector = f"Eigenschaft '{label}'"
                break
    product.badges, badge_selector = parse_badges(soup, container, config)
    product.languages = parse_languages(product, container, config)

    image_node, image_selector = select_first(soup, product_config.get("image_selectors", []))
    if image_node is not None:
        source = str(image_node.get("content") or image_node.get("src") or image_node.get("data-src") or "")
        if source:
            product.image_url = urljoin(url, source)

    product.matched = {
        "marker": marker,
        "container": container_selector,
        "name": name_selector,
        "variante": variant_selector,
        "preis": price_selector,
        "hersteller": manufacturer_selector,
        "badges": badge_selector,
        "bild": image_selector,
    }
    return product


def parse_card_badges(html: str, page_url: str, product_url: str, config: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Badges und Sprachflagge aus der Listenkarte eines Produkts.

    Die Produktseite selbst rendert keine Badges — "Neu" und "Vorbestellung"
    stehen ausschliesslich auf den Karten in Listen, Suche und Slidern. Gesucht
    wird deshalb die Karte, deren Link auf genau dieses Produkt zeigt.
    """
    card_config = config.get("card_lookup", {})
    aliases = {key.lower(): value for key, value in config.get("product", {}).get("badge_aliases", {}).items()}
    soup = BeautifulSoup(html, "html.parser")
    wanted = normalise_url(product_url)

    for card in soup.select(card_config.get("card_selector", ".product-box")):
        links = {normalise_url(urljoin(page_url, str(anchor.get("href") or ""))) for anchor in card.select("a[href]")}
        if wanted not in links:
            continue

        badges: list[str] = []
        for node in card.select(card_config.get("badge_selector", ".product-badges .badge")):
            text = element_text(node)
            if not text or len(text) > 40 or text.lower() in BADGE_DENYLIST:
                continue
            label = aliases.get(text.lower(), text)
            if label not in badges:
                badges.append(label)

        languages: list[str] = []
        for node in card.select(card_config.get("language_selector", "img.twomoons-language-badge")):
            label = clean_text(node.get("title") or node.get("alt"))
            if label and label not in languages:
                languages.append(label)
        return badges, languages
    return [], []


def fetch_card_details(product: Product, config: dict[str, Any]) -> bool:
    """Sucht das Produkt in der Shop-Suche, um an seine Karte zu kommen."""
    card_config = config.get("card_lookup", {})
    if not card_config.get("enabled", True) or not product.name:
        return False

    template = card_config.get("url_template", "")
    if not template:
        return False

    query = quote_plus(product.name[: int(card_config.get("max_query_length", 60))])
    url = template.format(query=query)
    try:
        html = fetch_text(url, config.get("request", {}))
    except Exception as error:  # ohne Karte fehlen nur die Badges, nicht das Produkt
        LOG.warning("Suchseite für '%s' nicht lesbar: %s", product.name, error)
        return False

    badges, languages = parse_card_badges(html, url, product.url, config)
    if badges:
        product.badges = badges
    if languages and not product.languages:
        product.languages = languages
    if badges or languages:
        product.matched["badges"] = "Listenkarte aus der Suche"
        return True

    LOG.debug("Keine Listenkarte für '%s' in der Suche gefunden", product.name)
    return False


def fetch_product(url: str, config: dict[str, Any]) -> Product:
    html = fetch_text(url, config.get("request", {}))
    product = parse_product(html, url, config)
    if product.is_product:
        fetch_card_details(product, config)
    return product


# --------------------------------------------------------------------------- #
# Discord
# --------------------------------------------------------------------------- #


def color_to_int(color: str) -> int:
    try:
        return int(str(color).lstrip("#"), 16)
    except ValueError:
        return 0x5865F2


def build_embed(product: Product, config: dict[str, Any]) -> dict[str, Any]:
    """Baut das Embed. Nur vorhandene Angaben erscheinen — keine leeren Zeilen."""
    discord_config = config.get("discord", {})
    lines: list[str] = []

    if product.badges:
        # Bewusst die erste Zeile unter dem Titel, damit "Neu"/"Vorbestellung" auffällt.
        lines.append("🏷️ " + " · ".join(f"**{badge}**" for badge in product.badges))
    if product.price:
        lines.append(f"**Preis:** {product.price}")
    if product.languages:
        lines.append(f"**Sprachen:** {', '.join(product.languages)}")
    if product.manufacturer:
        lines.append(f"**Hersteller:** {product.manufacturer}")
    lines.append(f"**Link:** [Zum Produkt]({product.url})")

    embed: dict[str, Any] = {
        "title": product.name[:256],
        "url": product.url,
        "description": "\n".join(lines)[:4096],
        "color": color_to_int(discord_config.get("color", "#5865F2")),
        "footer": {"text": discord_config.get("footer", "twomoons.ch")},
    }
    if product.image_url:
        embed["thumbnail"] = {"url": product.image_url}
    return embed


def embed_digest(embed: dict[str, Any]) -> str:
    """Fingerabdruck über das ganze Embed, nicht nur den Text.

    So wirken auch geänderte Farben, Fusszeilen oder Bilder aus der config.json
    beim nächsten Lauf auf bereits gepostete Nachrichten.
    """
    serialised = json.dumps(embed, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(serialised.encode("utf-8")).hexdigest()[:16]


def discord_request(method: str, url: str, payload: dict[str, Any], timeout: int = 30) -> requests.Response:
    for attempt in range(1, 4):
        response = requests.request(method, url, json=payload, timeout=timeout)
        if response.status_code == 429:
            retry_after = 2.0
            try:
                retry_after = float(response.json().get("retry_after", 2.0))
            except (ValueError, AttributeError, TypeError):
                pass
            LOG.warning("Discord Rate-Limit, warte %.1fs", retry_after)
            time.sleep(retry_after + 0.5)
            continue
        if 500 <= response.status_code < 600:
            LOG.warning("Discord antwortete %s, Versuch %s/3", response.status_code, attempt)
            time.sleep(2**attempt)
            continue
        return response
    raise RuntimeError(f"Discord-Anfrage nach mehreren Versuchen fehlgeschlagen: {method} {url}")


def post_embed(webhook_url: str, embed: dict[str, Any], username: str, avatar_url: str = "") -> str:
    """Postet das Embed und liefert die Message-ID für spätere Aktualisierungen."""
    payload: dict[str, Any] = {"embeds": [embed]}
    if username:
        payload["username"] = username
    if avatar_url:
        payload["avatar_url"] = avatar_url

    separator = "&" if "?" in webhook_url else "?"
    response = discord_request("POST", f"{webhook_url}{separator}wait=true", payload)
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Discord lehnte den Post ab ({response.status_code}): {response.text[:300]}")
    try:
        return str(response.json().get("id", ""))
    except ValueError:
        return ""


def edit_embed(webhook_url: str, message_id: str, embed: dict[str, Any]) -> bool:
    """Aktualisiert eine gepostete Nachricht. False = Nachricht gibt es nicht mehr."""
    base = webhook_url.split("?")[0].rstrip("/")
    response = discord_request("PATCH", f"{base}/messages/{message_id}", {"embeds": [embed]})
    if response.status_code == 404:
        LOG.warning("Nachricht %s existiert nicht mehr — wird nicht weiter aktualisiert", message_id)
        return False
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Discord lehnte das Update ab ({response.status_code}): {response.text[:300]}")
    return True


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #


def load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        LOG.error("%s ist kein gültiges JSON: %s", path, error)
        raise


def empty_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "initialized": False, "last_run": "", "known": [], "products": {}}


def load_state(path: Path) -> dict[str, Any]:
    state = load_json(path, empty_state())
    state.setdefault("version", STATE_VERSION)
    state.setdefault("initialized", False)
    state.setdefault("known", [])
    state.setdefault("products", {})
    return state


def save_state(path: Path, state: dict[str, Any], known: set[str]) -> None:
    state["known"] = sorted(known)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------- #
# Ablauf
# --------------------------------------------------------------------------- #


def sort_candidates(urls: dict[str, str]) -> list[str]:
    """Neueste zuerst — bei Rückstau werden die aktuellsten Produkte zuerst gemeldet."""
    return sorted(urls, key=lambda url: (urls.get(url, ""), url), reverse=True)


def post_new_products(
    candidates: list[str],
    config: dict[str, Any],
    state: dict[str, Any],
    known: set[str],
    args: argparse.Namespace,
    webhook_url: str,
    report: list[str],
) -> int:
    """Holt die neuen Seiten, postet die Produkte darunter und merkt sie sich."""
    discord_config = config.get("discord", {})
    request_config = config.get("request", {})
    limit = args.limit or int(discord_config.get("max_posts_per_run", 10))
    max_fetches = int(discord_config.get("max_detail_fetches_per_run", 60))
    pause = float(request_config.get("delay_between_requests", 1.0))
    delay = float(discord_config.get("delay_between_posts", 1.5))
    username = discord_config.get("username", "")
    avatar_url = discord_config.get("avatar_url", "")

    posted = 0
    fetched = 0
    for url in candidates:
        if posted >= limit or fetched >= max_fetches:
            break
        fetched += 1
        try:
            product = fetch_product(url, config)
        except Exception as error:  # ein kaputtes Produkt darf den Lauf nicht abbrechen
            LOG.warning("Produktseite %s nicht lesbar: %s", url, error)
            report.append(f"FEHLER  {url} -> {error}")
            continue
        time.sleep(pause)

        if not product.is_product:
            LOG.debug("%s ist keine Produktseite — wird nur gemerkt", url)
            report.append(f"kein Produkt  {url}")
            known.add(url)
            continue

        blocked = blocked_category(product, config)
        if blocked:
            LOG.info("Gefiltert (%s): %s", blocked, product.name)
            report.append(f"gefiltert ({blocked})  {product.name} | {url}")
            known.add(url)
            continue

        embed = build_embed(product, config)
        LOG.debug("Embed für %s:\n%s", url, json.dumps(embed, indent=2, ensure_ascii=False))

        message_id = ""
        if args.dry_run:
            LOG.info("DRY-RUN — würde posten: %s", product.name)
        else:
            message_id = post_embed(webhook_url, embed, username, avatar_url)
            LOG.info("Gepostet: %s", product.name)
            time.sleep(delay)

        posted += 1
        known.add(url)
        state["products"][url] = {
            "name": product.name,
            "price": product.price,
            "badges": product.badges,
            "languages": product.languages,
            "message_id": message_id,
            "digest": embed_digest(embed),
            "first_seen": timestamp(),
            "last_checked": timestamp(),
        }
        report.append(
            f"NEU  {product.name} | {product.price or 'kein Preis'} | "
            f"{', '.join(product.badges) or 'keine Badges'} | "
            f"{', '.join(product.languages) or 'keine Sprache'} | {url}"
        )

    remaining = len(candidates) - fetched
    if remaining > 0:
        LOG.warning("%s weitere neue URL(s) werden erst im nächsten Lauf geprüft", remaining)
    return posted


def refresh_posted_products(
    config: dict[str, Any],
    state: dict[str, Any],
    args: argparse.Namespace,
    webhook_url: str,
) -> int:
    """Preis- oder Badge-Änderungen in die bestehende Nachricht nachtragen."""
    update_config = config.get("update", {})
    if not update_config.get("enabled", True):
        return 0

    max_rechecks = int(update_config.get("max_rechecks_per_run", 10))
    recheck_seconds = float(update_config.get("recheck_hours", 24)) * 3600
    pause = float(config.get("request", {}).get("delay_between_requests", 1.0))
    delay = float(config.get("discord", {}).get("delay_between_posts", 1.5))
    now = time.time()

    def stale(record: dict[str, Any]) -> bool:
        checked = record.get("last_checked", "")
        if not checked:
            return True
        return (now - parse_timestamp(checked)) >= recheck_seconds

    candidates = [
        (url, record)
        for url, record in state.get("products", {}).items()
        if isinstance(record, dict) and record.get("message_id") and stale(record)
    ]
    candidates.sort(key=lambda item: item[1].get("last_checked", ""))

    updated = 0
    for url, record in candidates[:max_rechecks]:
        try:
            product = fetch_product(url, config)
        except Exception as error:
            LOG.warning("Nachkontrolle für %s fehlgeschlagen: %s", url, error)
            continue
        time.sleep(pause)
        if not product.is_product:
            continue

        embed = build_embed(product, config)
        digest = embed_digest(embed)
        if not args.dry_run:
            record["last_checked"] = timestamp()
        if digest == record.get("digest"):
            continue

        if args.dry_run:
            LOG.info("DRY-RUN — würde aktualisieren: %s", product.name)
            continue
        if edit_embed(webhook_url, record["message_id"], embed):
            LOG.info("Aktualisiert: %s (%s)", product.name, product.price or "Angaben geändert")
            record.update(
                {
                    "digest": digest,
                    "name": product.name,
                    "price": product.price,
                    "badges": product.badges,
                    "languages": product.languages,
                }
            )
            updated += 1
        else:
            record["message_id"] = ""
        time.sleep(delay)
    return updated


def delete_message(webhook_url: str, message_id: str) -> bool:
    """Löscht eine gepostete Nachricht. False = war schon weg."""
    base = webhook_url.split("?")[0].rstrip("/")
    response = discord_request("DELETE", f"{base}/messages/{message_id}", {})
    if response.status_code == 404:
        return False
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Discord lehnte das Löschen ab ({response.status_code}): {response.text[:300]}")
    return True


def parse_timestamp(value: str) -> float:
    """ISO-Zeitstempel aus state.json als Sekunden; unlesbar = "sehr alt"."""
    try:
        return time.mktime(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return 0.0


def cleanup_channel(
    config: dict[str, Any], state: dict[str, Any], args: argparse.Namespace, webhook_url: str
) -> int:
    """Löscht alte Meldungen, damit im Kanal nur die neuesten stehen bleiben.

    Gelöscht wird nur, was dieser Webhook selbst gepostet hat. Die URL bleibt in
    `known`, das Produkt wird also nicht erneut gemeldet — nur seine Nachricht
    verschwindet aus dem Kanal.
    """
    cleanup = config.get("cleanup", {})
    if not cleanup.get("enabled", False) or not webhook_url:
        return 0

    keep = int(cleanup.get("keep_newest", 0))
    max_age_days = float(cleanup.get("delete_after_days", 0))
    if keep <= 0 and max_age_days <= 0:
        return 0

    lebend = [
        (url, record)
        for url, record in state.get("products", {}).items()
        if isinstance(record, dict) and record.get("message_id")
    ]
    # Neueste zuerst — gepostet wird in derselben Reihenfolge, in der gemerkt wird.
    lebend.sort(key=lambda item: parse_timestamp(item[1].get("first_seen", "")), reverse=True)

    veraltet: dict[str, dict[str, Any]] = {}
    if keep > 0:
        for url, record in lebend[keep:]:
            veraltet[url] = record
    if max_age_days > 0:
        grenze = time.time() - max_age_days * 86400
        for url, record in lebend:
            if parse_timestamp(record.get("first_seen", "")) < grenze:
                veraltet[url] = record

    if not veraltet:
        return 0

    delay = float(config.get("discord", {}).get("delay_between_posts", 1.5))
    entfernt = 0
    # Älteste zuerst: Bricht der Lauf ab, ist das Aufgeräumte das am längsten Stehende.
    reihenfolge = sorted(veraltet.items(), key=lambda item: parse_timestamp(item[1].get("first_seen", "")))
    for url, record in reihenfolge:
        name = record.get("name", url)
        if args.dry_run:
            LOG.info("DRY-RUN — würde aus dem Kanal entfernen: %s", name)
            continue
        try:
            delete_message(webhook_url, record["message_id"])
        except Exception as error:  # ein Fehler beim Löschen darf den Lauf nicht kippen
            LOG.warning("Nachricht zu '%s' liess sich nicht löschen: %s", name, error)
            continue
        LOG.info("Aus dem Kanal entfernt: %s", name)
        state["products"].pop(url, None)
        entfernt += 1
        time.sleep(delay)
    return entfernt


def run_from_listing(
    config: dict[str, Any], state: dict[str, Any], args: argparse.Namespace
) -> tuple[int, list[str]]:
    """Postet Produkte aus einem Bereich einer Seite — auch schon bekannte.

    Gedacht als Probe mit echten Produkten ("Neu im Shop" auf der Startseite),
    bevor der erste echte Neuzugang kommt. Der Erstlauf-Schutz (`initialized`)
    wird dabei bewusst nicht gesetzt: Sonst gälte beim nächsten regulären Lauf
    das ganze übrige Sortiment als neu.
    """
    report: list[str] = []
    discord_config = config.get("discord", {})
    webhook_url = os.environ.get(discord_config.get("webhook_env", "DISCORD_WEBHOOK_RELEASES"), "").strip()

    html = fetch_text(args.post_from, config.get("request", {}))
    urls = urls_from_listing(html, args.post_from, args.heading or "")
    LOG.info(
        "%s Produkt(e) im Bereich '%s' von %s",
        len(urls),
        args.heading or "(ganze Seite)",
        args.post_from,
    )
    if not urls:
        titel = [title for title, _ in listing_blocks(BeautifulSoup(html, "html.parser")) if title]
        LOG.error("Kein passender Bereich gefunden. Vorhandene Überschriften: %s", titel[:15])
        return 0, ["FEHLER  kein passender Bereich auf der Seite"]

    if not webhook_url and not args.dry_run:
        LOG.error("%s ist nicht gesetzt — es wird nichts gepostet", discord_config.get("webhook_env"))
        return 0, ["FEHLER  Webhook-Secret fehlt"]

    known = set(state.get("known", []))
    limit = args.limit or int(discord_config.get("max_posts_per_run", 10))
    posted = post_new_products(urls[:limit], config, state, known, args, webhook_url, report)

    entfernt = cleanup_channel(config, state, args, webhook_url)
    if entfernt:
        report.append(f"{entfernt} alte Meldung(en) aus dem Kanal entfernt")

    if not args.dry_run:
        state["last_run"] = timestamp()
        save_state(args.state, state, known)
    else:
        LOG.info("DRY-RUN — state.json bleibt unverändert")
    return posted, report


def run(config: dict[str, Any], state: dict[str, Any], args: argparse.Namespace) -> tuple[int, list[str]]:
    report: list[str] = []
    discord_config = config.get("discord", {})
    webhook_env = discord_config.get("webhook_env", "DISCORD_WEBHOOK_RELEASES")
    webhook_url = os.environ.get(webhook_env, "").strip()

    if args.reset:
        # Auch im Dry-Run zurücksetzen: Gespeichert wird dort ohnehin nichts, und
        # nur so lässt sich nach dem Erstlauf überhaupt eine Vorschau erzeugen
        # ("--dry-run --reset --post-existing --limit 3").
        LOG.warning("Reset: %s gemerkte URL(s) werden vergessen", len(state.get("known", [])))
        state["known"] = []
        state["products"] = {}
        state["initialized"] = False

    urls = discover_urls(config)
    if not urls:
        raise RuntimeError("Keine einzige URL gefunden — Sitemap und Listing-Seiten waren beide leer")

    candidates_map, dropped = filter_urls(urls, config)
    LOG.info("%s URL(s) nach Filter (%s ausgefiltert)", len(candidates_map), sum(dropped.values()))
    for pattern, count in sorted(dropped.items(), key=lambda item: -item[1]):
        LOG.info("  ausgefiltert durch %s: %s", pattern, count)

    known = set(state.get("known", []))
    new_urls = [url for url in sort_candidates(candidates_map) if url not in known]
    LOG.info("%s davon neu", len(new_urls))

    first_run = not state.get("initialized", False)
    posted = 0

    if first_run and not args.post_existing:
        LOG.info(
            "Erster Lauf — %s URL(s) werden nur als bekannt gespeichert, es wird nichts gepostet",
            len(new_urls),
        )
        known.update(new_urls)
        report.append(f"Erstlauf: {len(new_urls)} URL(s) gemerkt, nichts gepostet")
    elif not new_urls:
        LOG.info("Keine neuen Produkte")
    elif not webhook_url and not args.dry_run:
        LOG.error("%s ist nicht gesetzt — es wird nichts gepostet", webhook_env)
        report.append(f"FEHLER  {webhook_env} fehlt — {len(new_urls)} neue URL(s) bleiben offen")
    else:
        posted = post_new_products(new_urls, config, state, known, args, webhook_url, report)

    if webhook_url and not first_run:
        try:
            updated = refresh_posted_products(config, state, args, webhook_url)
            if updated:
                report.append(f"{updated} bestehende Nachricht(en) aktualisiert")
        except Exception as error:
            LOG.exception("Nachkontrolle fehlgeschlagen: %s", error)

    # Erst nach dem Posten aufräumen, damit die neuen Meldungen mitzählen.
    entfernt = cleanup_channel(config, state, args, webhook_url)
    if entfernt:
        report.append(f"{entfernt} alte Meldung(en) aus dem Kanal entfernt")

    if args.dry_run:
        LOG.info("DRY-RUN — state.json bleibt unverändert")
        return posted, report

    state["initialized"] = True
    state["last_run"] = timestamp()
    save_state(args.state, state, known)
    return posted, report


# --------------------------------------------------------------------------- #
# Diagnose (ersetzt den fehlenden Netzzugang bei der Entwicklung)
# --------------------------------------------------------------------------- #


def url_statistics(urls: dict[str, str]) -> list[tuple[str, int]]:
    counter: dict[str, int] = {}
    for url in urls:
        parts = [part for part in urlsplit(url).path.split("/") if part]
        key = parts[0] if len(parts) > 1 else "(Wurzelebene)"
        counter[key] = counter.get(key, 0) + 1
    return sorted(counter.items(), key=lambda item: -item[1])


def interesting_classes(soup: BeautifulSoup) -> list[str]:
    wanted = ("badge", "label", "configurator", "option", "price", "manufacturer", "delivery", "property")
    classes = {
        css_class
        for tag in soup.find_all(True)
        for css_class in (tag.get("class") or [])
        if any(word in css_class.lower() for word in wanted)
    }
    return sorted(classes)


# Beim Abbild stört vor allem, was viel Platz braucht und nichts erklärt:
# Inline-SVG, Skripte und der Bewertungsblock mit seinen Sternen.
DUMP_NOISE = (
    "script",
    "style",
    "svg",
    "noscript",
    "template",
)
DUMP_NOISE_CLASSES = ("review", "point-rating", "point-container", "cookie")


def describe_chain(node: Tag, depth: int = 6) -> list[str]:
    """Element und seine Vorfahren als "tag.klasse" — zeigt den echten Produktbereich."""
    chain: list[str] = []
    current: Tag | None = node
    while current is not None and len(chain) < depth and getattr(current, "name", None):
        classes = ".".join(current.get("class") or [])
        chain.append(f"{current.name}.{classes}" if classes else str(current.name))
        current = current.parent
    return chain


def tidy_dump(container: Tag) -> str:
    """HTML-Abbild ohne Rauschen — brauchbar als Grundlage für eine Fixture."""
    copy = BeautifulSoup(container.decode(), "html.parser")
    for node in copy.find_all(DUMP_NOISE):
        node.decompose()
    for node in copy.find_all(True):
        classes = " ".join(node.get("class") or []).lower()
        if any(word in classes for word in DUMP_NOISE_CLASSES):
            node.decompose()
    for comment in copy.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()
    return re.sub(r"\n\s*\n+", "\n", copy.decode())


def survey(urls: dict[str, str], config: dict[str, Any], count: int) -> None:
    """Zufallsstichprobe: welche Kategorien stecken überhaupt im Sortiment?

    Die Produkte liegen alle auf der Wurzelebene, die URL verrät also nichts.
    Erst die Breadcrumb zeigt, wie viel davon Einzelkarten, Zubehör oder
    Veranstaltungen sind — die Grundlage für sinnvolle Filterlisten.
    """
    pool = sorted(urls)
    picked = random.Random(20260918).sample(pool, min(count, len(pool)))
    request_config = config.get("request", {})
    pause = float(request_config.get("delay_between_requests", 1.0))

    counter: dict[str, int] = {}
    keine = 0
    for url in picked:
        try:
            html = fetch_text(url, request_config)
        except Exception as error:
            LOG.warning("  Stichprobe %s nicht lesbar: %s", url, error)
            continue
        soup = BeautifulSoup(html, "html.parser")
        categories = parse_categories(soup)[1:]  # "Home" sagt nichts
        if not categories:
            keine += 1
        for category in categories:
            counter[category] = counter.get(category, 0) + 1
        time.sleep(pause)

    LOG.info("=== Kategorien in %s Stichproben ===", len(picked))
    for category, number in sorted(counter.items(), key=lambda item: -item[1]):
        LOG.info("  %-45s %s", category, number)
    LOG.info("  ohne Breadcrumb (keine Produktseite): %s", keine)


def inspect(config: dict[str, Any], args: argparse.Namespace) -> int:
    """Zeigt, wie der Shop wirklich aussieht — Grundlage für die Selektoren."""
    urls = discover_urls(config)
    LOG.info("=== Übersicht: %s URL(s) gefunden ===", len(urls))
    for prefix, count in url_statistics(urls)[:30]:
        LOG.info("  /%s/… : %s", prefix, count)

    kept, dropped = filter_urls(urls, config)
    LOG.info("Nach Filter: %s URL(s), ausgefiltert: %s", len(kept), sum(dropped.values()))

    request_config = config.get("request", {})
    samples = args.inspect_url or sort_candidates(kept)[: int(args.samples)]

    for url in samples:
        LOG.info("=== Produktseite %s ===", url)
        try:
            html = fetch_text(url, request_config)
        except Exception as error:
            LOG.error("  nicht abrufbar: %s", error)
            continue

        soup = BeautifulSoup(html, "html.parser")
        product = parse_product(html, url, config)
        if product.is_product:
            found = fetch_card_details(product, config)
            LOG.info("  Listenkarte in der Suche gefunden: %s", "ja" if found else "nein")
        LOG.info("  HTML-Grösse: %s Zeichen", len(html))
        LOG.info("  Treffer je Feld: %s", json.dumps(product.matched, ensure_ascii=False))
        LOG.info("  Name: %s", product.name or "(nichts)")
        LOG.info("  Preis: %s", product.price or "(nichts)")
        LOG.info("  Variante: %s", product.variant_text or "(nichts)")
        LOG.info("  Hersteller: %s", product.manufacturer or "(nichts)")
        LOG.info("  Badges: %s", product.badges or "(keine)")
        LOG.info("  Sprachen: %s", product.languages or "(keine)")
        LOG.info("  Bild: %s", product.image_url or "(nichts)")
        LOG.info("  Kategorien: %s", product.categories or "(keine)")
        LOG.info("  Eigenschaften: %s", json.dumps(product.properties, ensure_ascii=False)[:400])
        LOG.info("  Filter würde greifen: %s", blocked_category(product, config) or "nein")
        if product.is_product:
            LOG.info("  Embed:\n%s", json.dumps(build_embed(product, config), indent=2, ensure_ascii=False))
        LOG.debug("  Klassen mit badge/option/price/…: %s", interesting_classes(soup)[:60])

        gezeigt = 0
        for card in soup.select(".product-box"):
            badges = [text for text in (element_text(node) for node in card.select(".product-badges .badge")) if text]
            if not badges or gezeigt >= 5:
                continue
            link = card.select_one("a[href]")
            LOG.info("  Fremde Karte mit Badges %s -> %s", badges, link.get("href") if link else "(kein Link)")
            gezeigt += 1

        for selector in config.get("inspect", {}).get("ancestors_of", []):
            node = soup.select_one(selector)
            if node is not None:
                LOG.info("  Eltern von '%s': %s", selector, " < ".join(describe_chain(node)))

        if args.dump_html:
            wanted = args.dump_selector or config.get("inspect", {}).get("dump_selectors", [])
            container, container_selector = product_container(soup, config)
            LOG.info("  Produktbereich: %s", container_selector)
            for selector in wanted:
                node = container.select_one(selector)
                herkunft = "im Produktbereich"
                if node is None:
                    node = soup.select_one(selector)
                    herkunft = "ausserhalb des Produktbereichs"
                if node is None:
                    LOG.info("  Abbild '%s': nicht vorhanden", selector)
                    continue
                dump = tidy_dump(node)[: int(args.dump_bytes)]
                LOG.info("  Abbild '%s' (%s):\n<<<DUMP %s>>>\n%s\n<<<ENDE>>>", selector, herkunft, selector, dump)
        time.sleep(float(request_config.get("delay_between_requests", 1.0)))

    # Die Übersicht gehört ans Ende des Laufs — dort ist sie bequem lesbar.
    if args.survey:
        survey(kept, config, int(args.survey))
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TwoMoons Release-Notifier für Discord")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--dry-run", action="store_true", help="Nichts posten, state.json nicht ändern")
    parser.add_argument(
        "--post-existing",
        action="store_true",
        help="Auch beim allerersten Lauf die bereits vorhandenen Produkte posten",
    )
    parser.add_argument("--reset", action="store_true", help="Gemerkte Produkte vergessen")
    parser.add_argument("--limit", type=int, default=0, help="Maximale Anzahl Posts pro Lauf")
    parser.add_argument("--verbose", action="store_true", help="Ausführliches Log")
    parser.add_argument(
        "--post-from",
        default="",
        help="Probe: Produkte aus einem Bereich dieser Seite posten (auch schon bekannte)",
    )
    parser.add_argument(
        "--heading",
        default="",
        help="Zu --post-from: nur der Bereich mit dieser Überschrift, z. B. 'Neu im Shop'",
    )
    parser.add_argument("--inspect", action="store_true", help="Shop analysieren statt posten")
    parser.add_argument(
        "--inspect-url",
        action="append",
        help="Konkrete Produktseite für --inspect (mehrfach möglich)",
    )
    parser.add_argument("--samples", type=int, default=5, help="Anzahl Stichproben für --inspect")
    parser.add_argument(
        "--survey",
        type=int,
        default=0,
        help="Zufällige Produktseiten abrufen und ihre Kategorien zählen (für die Filterlisten)",
    )
    parser.add_argument("--dump-html", action="store_true", help="HTML-Abbild des Produktbereichs ins Log")
    parser.add_argument(
        "--dump-selector",
        action="append",
        help="Nur diese Bereiche abbilden (mehrfach möglich); sonst inspect.dump_selectors",
    )
    parser.add_argument("--dump-bytes", type=int, default=3000, help="Länge je HTML-Abbild")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    config = load_json(args.config, {})
    if not config:
        LOG.error("Konfiguration %s fehlt oder ist leer", args.config)
        return 1

    if args.inspect:
        return inspect(config, args)

    state = load_state(args.state)
    try:
        posted, report = run_from_listing(config, state, args) if args.post_from else run(config, state, args)
    except Exception as error:
        LOG.exception("Lauf abgebrochen: %s", error)
        return 1

    # Ein Diagnose-Log wird schnell mehrere hundert Zeilen lang — die Übersicht
    # gehört ans Ende, dort ist sie bequem lesbar.
    LOG.info("=== Übersicht des Laufs ===\n%s", "\n".join(report) if report else "(nichts zu berichten)")
    LOG.info("Fertig: %s Produkt(e) gepostet", posted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
