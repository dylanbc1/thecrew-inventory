"""Inventory scraper — Scrapling + StealthyFetcher for thecrewautos.com.

Bypasses Cloudflare via headless Camoufox browser. Parses vehicle data
from the DWS dealer plugin HTML and persists to SQLite.
"""

from __future__ import annotations

import asyncio
import logging
import re
from functools import partial
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _parse_vehicle(item_html: str) -> Optional[Dict]:
    """Parse a single vehicle block into a dict."""
    # Title from aria-label
    aria = re.findall(r'aria-label="([^"]*)"', item_html)
    title = aria[0] if aria else ""
    if not title:
        return None

    # Year / make / model
    ymm = re.match(r"(?:apply credit for\s+)?(\d{4})\s+(\w+)\s+(.*)", title, re.I)
    year = ymm.group(1) if ymm else ""
    make = ymm.group(2).title() if ymm else ""
    model = ymm.group(3).strip().title() if ymm else ""

    # VIN
    vin_match = re.findall(r"dws-vehicle-price-([A-HJ-NPR-Z0-9]{17})", item_html)
    vin = vin_match[0] if vin_match else ""

    # Stock number
    stock_match = re.findall(r'data-stockvid="([^"]+)"', item_html)
    stock = stock_match[0] if stock_match else ""

    # Price
    price_match = re.findall(r">\s*\$([\d,]+)\s*<", item_html)
    price = "$" + price_match[0] if price_match else "Call for price"

    # Mileage
    mileage_match = re.findall(r"([\d,]+)\s*(?:mi|miles|MI)", item_html)
    mileage = mileage_match[0] if mileage_match else ""

    # Detail URL
    urls = re.findall(r'href="(/inventory/[^"]+/)"', item_html)
    detail = "https://www.thecrewautos.com" + urls[0] if urls else ""

    # Image — lozad lazy-load uses data-background-image
    img_match = re.findall(r'data-background-image="(https://[^"]+)"', item_html, re.I)
    if not img_match:
        img_match = re.findall(
            r'(?:src|data-src)="(https://[^"]*(?:\.jpg|\.jpeg|\.png|\.webp)[^"]*)"',
            item_html, re.I,
        )
    img = img_match[0] if img_match else ""

    return {
        "year": year, "make": make, "model": model, "vin": vin,
        "stock_number": stock, "price": price, "mileage": mileage,
        "detail_url": detail, "image_url": img,
    }


async def scrape(url: str) -> List[Dict]:
    """Scrape the inventory page. Returns list of vehicle dicts."""
    logger.info("scrape_start url=%s", url)

    from scrapling.fetchers import StealthyFetcher

    loop = asyncio.get_event_loop()
    page = await loop.run_in_executor(
        None, partial(StealthyFetcher.fetch, url, headless=True)
    )

    from scrapling import Adaptor
    adaptor = Adaptor(page.html_content, url=url)
    items = adaptor.css(".col-sm-12.container-fluid.in-inventory")

    vehicles: List[Dict] = []
    seen_vins = set()
    for item in items:
        v = _parse_vehicle(item.html_content)
        if v and v["vin"] and v["vin"] not in seen_vins:
            seen_vins.add(v["vin"])
            vehicles.append(v)

    logger.info("scrape_done count=%d", len(vehicles))
    return vehicles
