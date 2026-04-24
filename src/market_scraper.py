"""Market comparison scraper — fetches competitor prices from Cars.com.

Uses the same Scrapling + StealthyFetcher + Camoufox stack as the inventory
scraper. Searches for comparable vehicles (same year range, make, model) near
Alpharetta GA (zip 30009) and extracts prices to build a market comparison.

The agent uses this data to prove The Crew's price is competitive and
build an "irresistible offer" argument.
"""

from __future__ import annotations

import asyncio
import logging
import re
from functools import partial
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_SEARCH_ZIP = "30009"  # Alpharetta, GA
_SEARCH_RADIUS = 100   # miles
_PAGE_SIZE = 20


def _build_search_url(year: str, make: str, model: str) -> str:
    """Build Cars.com search URL for comparable vehicles."""
    y = int(year)
    make_lower = make.lower().strip()
    model_lower = model.lower().strip().replace(" ", "-")
    return (
        f"https://www.cars.com/shopping/results/"
        f"?stock_type=used"
        f"&makes[]={make_lower}"
        f"&models[]={make_lower}-{model_lower}"
        f"&year_min={y - 1}"
        f"&year_max={y + 1}"
        f"&maximum_distance={_SEARCH_RADIUS}"
        f"&zip={_SEARCH_ZIP}"
        f"&page_size={_PAGE_SIZE}"
        f"&sort=best_match_desc"
    )


async def _fetch_page(url: str) -> str:
    """Fetch a page via StealthyFetcher in a thread (same as inventory scraper)."""
    from scrapling.fetchers import StealthyFetcher

    loop = asyncio.get_event_loop()
    page = await loop.run_in_executor(
        None, partial(StealthyFetcher.fetch, url, headless=True)
    )
    return page.html_content


def _parse_price(text: str) -> Optional[int]:
    """Extract numeric price from text like '$12,495' or '12495'."""
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits and int(digits) > 0 else None


def _parse_listings(html: str) -> List[Dict]:
    """Parse vehicle listings from Cars.com search results HTML."""
    listings = []

    # Cars.com uses <div class="vehicle-card"> or similar structured containers
    # Try multiple selector strategies for robustness

    # Strategy 1: Look for vehicle-card containers
    card_pattern = re.compile(
        r'<(?:div|a)[^>]*class="[^"]*vehicle-card[^"]*"[^>]*>(.*?)</(?:div|a)>',
        re.DOTALL | re.IGNORECASE,
    )
    cards = card_pattern.findall(html)

    if not cards:
        # Strategy 2: Look for listing-row or inventory-listing
        card_pattern = re.compile(
            r'<(?:div|a)[^>]*class="[^"]*(?:listing-row|inventory-listing|shop-srp-listing)[^"]*"[^>]*>(.*?)</(?:div|a)>\s*(?=<(?:div|a)[^>]*class="[^"]*(?:listing-row|inventory-listing|shop-srp-listing|vehicle-card))',
            re.DOTALL | re.IGNORECASE,
        )
        cards = card_pattern.findall(html)

    if not cards:
        # Strategy 3: Split by data attributes common on cars.com
        card_pattern = re.compile(
            r'data-override-payload="([^"]*)"',
            re.DOTALL,
        )
        # Fall through to JSON extraction below
        pass

    for card_html in cards:
        listing = _parse_single_listing(card_html)
        if listing and listing.get("price"):
            listings.append(listing)

    # Strategy 4: If regex didn't find cards, try to parse the full page
    # for any price + title patterns
    if not listings:
        listings = _fallback_parse(html)

    return listings


def _parse_single_listing(card_html: str) -> Optional[Dict]:
    """Parse a single listing card from Cars.com."""
    # Title: usually in <h2> with class containing "title"
    title_match = re.search(
        r'<h2[^>]*>\s*(.*?)\s*</h2>',
        card_html, re.DOTALL | re.IGNORECASE,
    )
    title = ""
    if title_match:
        title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip()

    if not title:
        # Try aria-label
        aria = re.search(r'aria-label="([^"]*)"', card_html)
        if aria:
            title = aria.group(1).strip()

    # Price
    price = None
    price_patterns = [
        r'class="[^"]*primary-price[^"]*"[^>]*>\s*\$?([\d,]+)',
        r'class="[^"]*price[^"]*"[^>]*>\s*\$?([\d,]+)',
        r'>\s*\$([\d,]+)\s*<',
    ]
    for pp in price_patterns:
        pm = re.search(pp, card_html, re.IGNORECASE)
        if pm:
            price = _parse_price(pm.group(1))
            if price and price > 1000:  # filter out nonsense
                break
            price = None

    # Mileage
    mileage = ""
    mi_match = re.search(r'([\d,]+)\s*(?:mi|miles)', card_html, re.IGNORECASE)
    if mi_match:
        mileage = mi_match.group(1)

    # Dealer name
    dealer = ""
    dealer_match = re.search(
        r'class="[^"]*dealer-name[^"]*"[^>]*>\s*(.*?)\s*<',
        card_html, re.IGNORECASE,
    )
    if dealer_match:
        dealer = re.sub(r"<[^>]+>", "", dealer_match.group(1)).strip()

    # Distance
    distance = ""
    dist_match = re.search(r'([\d.]+)\s*mi\.?\s*away', card_html, re.IGNORECASE)
    if dist_match:
        distance = f"{dist_match.group(1)} mi"

    if not title and not price:
        return None

    return {
        "title": title,
        "price": price,
        "mileage": mileage,
        "dealer": dealer,
        "distance": distance,
    }


def _fallback_parse(html: str) -> List[Dict]:
    """Fallback: scan the entire page for price patterns near vehicle descriptions."""
    listings = []

    # Find all prices on the page
    prices = re.findall(r'\$([\d,]+)', html)
    valid_prices = []
    for p in prices:
        val = _parse_price(p)
        if val and 2000 < val < 200000:
            valid_prices.append(val)

    # If we found prices but couldn't parse cards, just return the prices
    # for statistical comparison (avg, min, max)
    for i, price in enumerate(valid_prices[:20]):
        listings.append({
            "title": f"Comparable listing #{i+1}",
            "price": price,
            "mileage": "",
            "dealer": "",
            "distance": "",
        })

    return listings


def _compute_analysis(
    our_price_str: str,
    listings: List[Dict],
) -> Dict:
    """Compute market analysis from scraped listings."""
    our_price = _parse_price(our_price_str) or 0

    prices = [l["price"] for l in listings if l.get("price")]

    if not prices:
        return {
            "status": "no_data",
            "our_price": our_price,
            "listing_count": 0,
            "message": "No comparable listings found in the market",
        }

    avg_price = sum(prices) / len(prices)
    min_price = min(prices)
    max_price = max(prices)

    # Deal rating
    if our_price <= 0:
        deal_rating = "UNKNOWN"
        savings_vs_avg = 0
        savings_pct = 0
    else:
        savings_vs_avg = avg_price - our_price
        savings_pct = (savings_vs_avg / avg_price * 100) if avg_price > 0 else 0

        if savings_pct >= 15:
            deal_rating = "EXCELLENT_DEAL"
        elif savings_pct >= 8:
            deal_rating = "GREAT_DEAL"
        elif savings_pct >= 3:
            deal_rating = "GOOD_DEAL"
        elif savings_pct >= -3:
            deal_rating = "FAIR_DEAL"
        else:
            deal_rating = "ABOVE_MARKET"

    # Competitive advantages
    advantages = []
    if savings_vs_avg > 0:
        advantages.append(f"Our price is ${savings_vs_avg:,.0f} below market average")
    if our_price <= min_price and our_price > 0:
        advantages.append(f"Best price within {_SEARCH_RADIUS} miles for this model")
    if our_price > 0 and len([p for p in prices if p > our_price]) > len(prices) * 0.7:
        advantages.append("Priced lower than 70%+ of comparable listings")

    return {
        "status": "ok",
        "our_price": our_price,
        "market_avg": round(avg_price),
        "market_min": min_price,
        "market_max": max_price,
        "listing_count": len(prices),
        "savings_vs_avg": round(savings_vs_avg),
        "savings_pct": round(savings_pct, 1),
        "deal_rating": deal_rating,
        "competitive_advantages": advantages,
        "comparable_listings": listings[:10],  # top 10 for context
    }


async def scrape_market_comparison(
    year: str,
    make: str,
    model: str,
    our_price: str = "0",
) -> Dict:
    """Scrape Cars.com for comparable vehicles and return market analysis.

    Returns a dict with market_avg, savings, deal_rating, and comparable listings.
    Never raises — returns status="error" on failure.
    """
    url = _build_search_url(year, make, model)
    logger.info("market_scrape_start year=%s make=%s model=%s url=%s", year, make, model, url)

    try:
        html = await _fetch_page(url)
        listings = _parse_listings(html)
        logger.info("market_scrape_parsed listings=%d", len(listings))

        analysis = _compute_analysis(our_price, listings)
        analysis["search_url"] = url
        analysis["year"] = year
        analysis["make"] = make
        analysis["model"] = model

        return analysis

    except Exception as exc:
        logger.exception("market_scrape_failed year=%s make=%s model=%s", year, make, model)
        return {
            "status": "error",
            "error": str(exc),
            "year": year,
            "make": make,
            "model": model,
            "our_price": _parse_price(our_price) or 0,
            "listing_count": 0,
        }
