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


async def _fetch_page(url: str) -> str:
    """Fetch a single page via StealthyFetcher in a thread."""
    from scrapling.fetchers import StealthyFetcher

    loop = asyncio.get_event_loop()
    page = await loop.run_in_executor(
        None, partial(StealthyFetcher.fetch, url, headless=True)
    )
    return page.html_content


def _parse_page(html: str, url: str) -> List[Dict]:
    """Parse vehicles from a single page's HTML."""
    from scrapling import Adaptor

    adaptor = Adaptor(html, url=url)
    items = adaptor.css(".col-sm-12.container-fluid.in-inventory")
    vehicles = []
    for item in items:
        v = _parse_vehicle(item.html_content)
        if v and v["vin"]:
            vehicles.append(v)
    return vehicles


def _has_next_page(html: str) -> bool:
    """Check if there's a next page link."""
    return "?page_no=" in html and re.search(r'href="[^"]*page_no=\d+"[^>]*>\s*(?:Next|&raquo;|›|>)\s*<', html, re.I) is not None


async def scrape(url: str) -> List[Dict]:
    """Scrape all inventory pages. Follows pagination automatically."""
    logger.info("scrape_start url=%s", url)

    vehicles: List[Dict] = []
    seen_vins = set()
    page_no = 1

    while True:
        page_url = url if page_no == 1 else f"{url.rstrip('/')}/?page_no={page_no}"
        logger.info("scrape_page page=%d url=%s", page_no, page_url)

        html = await _fetch_page(page_url)
        page_vehicles = _parse_page(html, page_url)

        if not page_vehicles:
            break

        new_count = 0
        for v in page_vehicles:
            if v["vin"] not in seen_vins:
                seen_vins.add(v["vin"])
                vehicles.append(v)
                new_count += 1

        logger.info("scrape_page_done page=%d found=%d new=%d", page_no, len(page_vehicles), new_count)

        # Stop if no new vehicles (means we've looped) or no next page
        if new_count == 0:
            break

        # Check for next page link
        next_page = f"page_no={page_no + 1}"
        if next_page not in html:
            break

        page_no += 1

    logger.info("scrape_done total=%d pages=%d", len(vehicles), page_no)
    return vehicles
