#!/usr/bin/env python3
"""
RSS feed generator for Roig Arena events.
Scrapes https://www.roigarena.com/es/eventos/?layout=list with pagination
and serves an RSS feed on a local HTTP server.
"""

import json
import math
import re
import urllib.request
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from xml.etree.ElementTree import Element, SubElement, tostring

BASE_URL = "https://www.roigarena.com"
EVENTS_URL = f"{BASE_URL}/es/eventos/?layout=list"
ITEMS_PER_PAGE = 8
PORT = 8888


def fetch_page(page: int) -> str:
    url = f"{EVENTS_URL}&page={page}" if page > 1 else EVENTS_URL
    req = urllib.request.Request(url, headers={"User-Agent": "RoigArenaRSS/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8")


def resolve_nuxt_value(data: list, index: int, depth: int = 0) -> object:
    """Resolve a Nuxt payload index reference to its actual value."""
    if depth > 5 or index >= len(data):
        return None
    return data[index]


def parse_events_from_html(html: str) -> tuple[list[dict], int]:
    """Extract event objects from Nuxt SSR payload embedded in HTML."""
    match = re.search(
        r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL
    )
    if not match:
        return [], 0

    data = json.loads(match.group(1))

    # Navigate the payload structure:
    # data[1] = root object with "data" key pointing to index
    # data[root.data] = ShallowReactive wrapper
    # data[wrapper+1] = {"events-list": idx, ...}
    # data[events_list_idx] = {"data": events_array_idx, "total": total_idx}
    root = data[1]
    inner = data[root["data"] + 1]  # skip ShallowReactive marker

    events_list_key = None
    for key in inner:
        if "events" in key.lower() and "categor" not in key.lower():
            events_list_key = key
            break

    if not events_list_key:
        return [], 0

    events_meta = data[inner[events_list_key]]
    total = data[events_meta["total"]]
    event_indices = data[events_meta["data"]]

    events = []
    for idx in event_indices:
        event_obj = data[idx]
        event = {}
        for key, val_idx in event_obj.items():
            val = resolve_nuxt_value(data, val_idx)
            # For array values (like externalPurchaseLinks), resolve inner refs
            if isinstance(val, list):
                val = [resolve_nuxt_value(data, i) for i in val]
            event[key] = val
        events.append(event)

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
            print(f"Error fetching page {page}: {e}")
            break

    return events


def format_date_rfc822(iso_str: str) -> str:
    """Convert ISO date string to RFC 822 format for RSS."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%a, %d %b %Y %H:%M:%S %z")
    except (ValueError, AttributeError):
        return ""


def format_date_display(iso_str: str) -> str:
    """Format date for human display: DD/MM/YYYY HH:MM."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%d/%m/%Y %H:%M")
    except (ValueError, AttributeError):
        return ""


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

        event_url = f"{BASE_URL}/es/eventos/{slug}" if slug else purchase_link

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
    print(f"Fetching events from Roig Arena...")
    events = fetch_all_events()
    print(f"Found {len(events)} events across all pages")

    import sys
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
