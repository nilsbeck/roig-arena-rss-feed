#!/usr/bin/env python3
"""
RSS feed generator for Roig Arena events.
Scrapes https://www.roigarena.com/es/eventos/?layout=list with pagination
and serves an RSS feed on a local HTTP server.
"""

import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from xml.etree.ElementTree import Element, SubElement, tostring
from zoneinfo import ZoneInfo

VALENCIA_TZ = ZoneInfo("Europe/Madrid")

BASE_URL = "https://www.roigarena.com"
EVENTS_URL = f"{BASE_URL}/es/eventos/?layout=list"
ITEMS_PER_PAGE = 8
PORT = 8888

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}


def fetch_page(page: int) -> str:
    url = f"{EVENTS_URL}&page={page}" if page > 1 else EVENTS_URL
    req = urllib.request.Request(url, headers=_HEADERS)
    last_exc: Exception = RuntimeError("no attempts made")
    for attempt in range(3):
        if attempt:
            delay = 2 ** attempt
            print(f"Retrying in {delay}s (attempt {attempt + 1}/3)...", file=sys.stderr)
            time.sleep(delay)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            print(f"HTTP {e.code} fetching page {page}", file=sys.stderr)
            last_exc = e
            if e.code in (403, 404, 410):
                break  # no point retrying client errors
        except Exception as e:
            print(f"Error fetching page {page}: {e}", file=sys.stderr)
            last_exc = e
    raise last_exc


def resolve_nuxt_value(data: list, index: int, depth: int = 0) -> object:
    """Resolve a Nuxt payload index reference to its actual value."""
    if not isinstance(index, int) or depth > 5 or index >= len(data):
        return index  # return as-is if not a valid index reference
    return data[index]


def _resolve_event(data: list, event_obj: dict) -> dict:
    event = {}
    for key, val_idx in event_obj.items():
        val = resolve_nuxt_value(data, val_idx)
        if isinstance(val, list):
            val = [resolve_nuxt_value(data, i) for i in val]
        event[key] = val
    return event


def _parse_structured(data: list) -> tuple[list[dict], int]:
    """Primary strategy: navigate the known Nuxt SSR payload index structure."""
    root = data[1]
    if not isinstance(root, dict) or "data" not in root:
        raise ValueError("unexpected root structure")

    inner = data[root["data"] + 1]  # skip ShallowReactive marker
    if not isinstance(inner, dict):
        raise ValueError("unexpected inner structure")

    events_list_key = None
    for key in inner:
        if "events" in key.lower() and "categor" not in key.lower():
            events_list_key = key
            break

    if not events_list_key:
        raise ValueError("events-list key not found")

    events_meta = data[inner[events_list_key]]
    total = data[events_meta["total"]]
    event_indices = data[events_meta["data"]]

    events = [_resolve_event(data, data[idx]) for idx in event_indices]
    return events, total


def _parse_scan(data: list) -> tuple[list[dict], int]:
    """Fallback strategy: scan the flat payload array for event-shaped objects."""
    # Events have at minimum "name" and "slug" with integer index values
    required = {"name", "slug"}
    events = []
    seen: set = set()

    for item in data:
        if not isinstance(item, dict):
            continue
        if not required.issubset(item.keys()):
            continue
        # All values must be integer indices (Nuxt devalue reference format)
        if not all(isinstance(v, int) for v in item.values()):
            continue
        event = _resolve_event(data, item)
        ident = event.get("slug") or event.get("name")
        if not ident or ident in seen:
            continue
        seen.add(ident)
        events.append(event)

    return events, len(events)


def parse_events_from_html(html: str) -> tuple[list[dict], int]:
    """Extract event objects from Nuxt SSR payload embedded in HTML."""
    match = re.search(
        r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL
    )
    if not match:
        print("WARNING: __NUXT_DATA__ script tag not found in HTML", file=sys.stderr)
        return [], 0

    data = json.loads(match.group(1))
    print(f"Nuxt payload: {len(data)} entries", file=sys.stderr)

    try:
        events, total = _parse_structured(data)
        print(f"Structured parse: {len(events)} events (total={total})", file=sys.stderr)
        return events, total
    except Exception as e:
        print(f"Structured parse failed ({e}), trying scan fallback...", file=sys.stderr)

    events, total = _parse_scan(data)
    print(f"Scan parse: {len(events)} events", file=sys.stderr)
    return events, total


def fetch_all_events() -> list[dict]:
    """Fetch events from all pages."""
    html = fetch_page(1)
    events, total = parse_events_from_html(html)
    if total == 0:
        return events

    total_pages = math.ceil(total / ITEMS_PER_PAGE)
    for page in range(2, total_pages + 1):
        try:
            html = fetch_page(page)
            page_events, _ = parse_events_from_html(html)
            if not page_events:
                break
            events.extend(page_events)
        except Exception as e:
            print(f"Error fetching page {page}: {e}", file=sys.stderr)
            break

    return events


def parse_event_datetime(iso_str: str) -> datetime | None:
    """Parse ISO date string and convert to Valencia local time."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone(VALENCIA_TZ)
    except (ValueError, AttributeError):
        return None


def format_date_rfc822(iso_str: str) -> str:
    """Convert ISO date string to RFC 822 format for RSS (Valencia time)."""
    dt = parse_event_datetime(iso_str)
    return dt.strftime("%a, %d %b %Y %H:%M:%S %z") if dt else ""


def format_date_display(iso_str: str) -> str:
    """Format date for human display: DD/MM/YYYY HH:MM (Valencia time)."""
    dt = parse_event_datetime(iso_str)
    return dt.strftime("%d/%m/%Y %H:%M") if dt else ""


def build_rss(events: list[dict]) -> bytes:
    """Build RSS 2.0 XML from event list."""
    rss = Element("rss", version="2.0", attrib={
        "xmlns:media": "http://search.yahoo.com/mrss/",
        "xmlns:atom": "http://www.w3.org/2005/Atom",
    })
    channel = SubElement(rss, "channel")
    SubElement(channel, "title").text = "Roig Arena - Eventos"
    SubElement(channel, "link").text = f"{BASE_URL}/es/eventos/"
    SubElement(channel, "description").text = "Próximos eventos en Roig Arena, Valencia"
    SubElement(channel, "language").text = "es"
    SubElement(channel, "lastBuildDate").text = datetime.now(timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S %z"
    )

    for event in events:
        item = SubElement(channel, "item")
        name = event.get("name", "Sin título")
        slug = event.get("slug", "")
        start = event.get("start", "")
        price = event.get("startingPrice", "")
        location = event.get("locationName", "")
        category = event.get("category", "")
        sold_out = event.get("soldOut", False)
        banner = event.get("bannerUrl", "")
        vertical_img = event.get("verticalImageUrl", "")
        purchase_link = event.get("purchaseLink", "")
        description_text = event.get("description", "")

        event_url = f"{BASE_URL}/es/event/{slug}" if slug else purchase_link

        SubElement(item, "title").text = name
        SubElement(item, "link").text = event_url
        SubElement(item, "guid", isPermaLink="false").text = event.get(
            "id", slug or name
        )

        if start:
            SubElement(item, "pubDate").text = format_date_rfc822(start)

        # Build HTML description with image and details
        img_url = vertical_img or banner
        desc_parts = []
        if img_url:
            desc_parts.append(f'<img src="{img_url}" alt="{name}" />')
        desc_parts.append(f"<p><strong>Fecha:</strong> {format_date_display(start)}</p>")
        if location:
            desc_parts.append(f"<p><strong>Lugar:</strong> {location}</p>")
        if category:
            desc_parts.append(f"<p><strong>Categoría:</strong> {category}</p>")
        if price and price != "-":
            desc_parts.append(f"<p><strong>Desde:</strong> {price} €</p>")
        if sold_out:
            desc_parts.append("<p><strong>⚠ AGOTADO</strong></p>")
        if description_text:
            desc_parts.append(f"<p>{description_text}</p>")
        if purchase_link:
            desc_parts.append(f'<p><a href="{purchase_link}">Comprar entradas</a></p>')

        # External purchase links
        ext_titles = event.get("externalPurchaseTitle", [])
        ext_links = event.get("externalPurchaseLinks", [])
        if isinstance(ext_titles, list) and isinstance(ext_links, list):
            for title, link in zip(ext_titles, ext_links):
                if title and link:
                    desc_parts.append(f'<p><a href="{link}">{title}</a></p>')

        SubElement(item, "description").text = "\n".join(desc_parts)

        if img_url:
            SubElement(item, "media:content", url=img_url, medium="image")

        if category:
            SubElement(item, "category").text = category

    xml_bytes = b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(
        rss, encoding="unicode"
    ).encode("utf-8")
    return xml_bytes


class RSSHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/feed", "/feed.xml", "/rss", "/rss.xml"):
            try:
                events = fetch_all_events()
                xml = build_rss(events)
                self.send_response(200)
                self.send_header("Content-Type", "application/rss+xml; charset=utf-8")
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.write_body(xml)
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.write_body(f"Error: {e}".encode())
        else:
            self.send_response(404)
            self.end_headers()
            self.write_body(b"Not found")

    def write_body(self, data: bytes):
        self.wfile.write(data)

    def log_message(self, format, *args):
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {format % args}")


def main():
    print("Fetching events from Roig Arena...", file=sys.stderr)
    events = fetch_all_events()
    print(f"Found {len(events)} events across all pages", file=sys.stderr)

    if not events:
        print("ERROR: no events found — aborting to avoid publishing empty feed", file=sys.stderr)
        sys.exit(1)

    if "--once" in sys.argv:
        xml = build_rss(events)
        sys.stdout.buffer.write(xml)
        return

    print(f"Starting RSS server on http://localhost:{PORT}/feed.xml")
    server = HTTPServer(("0.0.0.0", PORT), RSSHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.server_close()


if __name__ == "__main__":
    main()
