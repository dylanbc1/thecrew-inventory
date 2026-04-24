"""Inventory Service — FastAPI app for The Crew Autos vehicle inventory.

Scrapes thecrewautos.com, persists to PostgreSQL, serves via authenticated API.
Auto-scrapes when data is stale (> CACHE_TTL_MINUTES since last scrape).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query

from src.auth import require_api_key
from src.config import get_settings
from src.db import (
    finish_run,
    get_cached_comparison,
    get_vehicle_by_vin,
    get_vehicles,
    init_db,
    last_successful_run,
    save_comparison,
    start_run,
    upsert_vehicles,
)
from src.market_scraper import scrape_market_comparison
from src.scraper import scrape

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Inventory Service — The Crew Autos", version="0.1.0")


@app.on_event("startup")
async def startup():
    init_db()
    logger.info("db_initialized")


# ── Internal: ensure fresh data ──────────────────────────────────

async def _ensure_fresh() -> None:
    """Scrape if no data or last scrape is older than TTL."""
    settings = get_settings()
    last = last_successful_run()

    if last and last.get("completed_at"):
        completed = datetime.fromisoformat(last["completed_at"])
        age_minutes = (datetime.now(timezone.utc) - completed).total_seconds() / 60
        if age_minutes < settings.cache_ttl_minutes:
            return  # fresh enough

    logger.info("data_stale — triggering scrape")
    run_id = start_run()
    try:
        vehicles = await scrape(settings.scrape_url)
        count = upsert_vehicles(vehicles)
        finish_run(run_id, count, "success")
        logger.info("auto_scrape_done count=%d", count)
    except Exception:
        logger.exception("auto_scrape_failed")
        finish_run(run_id, 0, "failed")


# ── Health (no auth) ─────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


# ── Scrape trigger ───────────────────────────────────────────────

@app.post("/scrape", dependencies=[Depends(require_api_key)])
async def trigger_scrape(force: bool = Query(False)):
    """Run a scrape. Skips if last success was < CACHE_TTL_MINUTES ago (unless force=true)."""
    settings = get_settings()

    if not force:
        last = last_successful_run()
        if last and last.get("completed_at"):
            completed = datetime.fromisoformat(last["completed_at"])
            age_minutes = (datetime.now(timezone.utc) - completed).total_seconds() / 60
            if age_minutes < settings.cache_ttl_minutes:
                return {
                    "status": "cached",
                    "message": f"Last scrape was {age_minutes:.1f}min ago (TTL={settings.cache_ttl_minutes}min)",
                    "last_run": last,
                }

    run_id = start_run()
    try:
        vehicles = await scrape(settings.scrape_url)
        count = upsert_vehicles(vehicles)
        finish_run(run_id, count, "success")
        return {
            "status": "success",
            "vehicles_found": count,
            "run_id": run_id,
        }
    except Exception as exc:
        logger.exception("scrape_failed")
        finish_run(run_id, 0, "failed")
        raise HTTPException(status_code=502, detail=f"Scrape failed: {exc}") from exc


# ── Vehicles ─────────────────────────────────────────────────────

@app.get("/vehicles", dependencies=[Depends(require_api_key)])
async def list_vehicles(
    q: Optional[str] = Query(None, description="Search by year, make, or model"),
    make: Optional[str] = Query(None, description="Filter by make (e.g. Bmw)"),
    max_price: Optional[int] = Query(None, description="Max price in dollars"),
    include_unavailable: bool = Query(False, description="Include sold/removed vehicles"),
):
    """List vehicles. Auto-scrapes if data is stale (>10min since last scrape)."""
    await _ensure_fresh()
    vehicles = get_vehicles(
        available_only=not include_unavailable,
        make=make,
        max_price=max_price,
        q=q,
    )
    return {"count": len(vehicles), "vehicles": vehicles}


@app.get("/vehicles/{vin}", dependencies=[Depends(require_api_key)])
async def get_vehicle(vin: str):
    """Get a single vehicle by VIN."""
    v = get_vehicle_by_vin(vin)
    if not v:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return v


# ── Market Comparison ───────────────────────────────────────────

@app.get("/market-compare", dependencies=[Depends(require_api_key)])
async def market_compare(
    year: str = Query(..., description="Vehicle year (e.g. 2013)"),
    make: str = Query(..., description="Vehicle make (e.g. Honda)"),
    model: str = Query(..., description="Vehicle model (e.g. Civic)"),
    our_price: str = Query("0", description="Our price for comparison (e.g. $9,685 or 9685)"),
    force: bool = Query(False, description="Force fresh scrape ignoring cache"),
):
    """Compare a vehicle against the market (Cars.com within 100mi of Alpharetta).

    Returns market average, savings, deal rating, and comparable listings.
    Results are cached for 24 hours per year/make/model combination.
    """
    if not force:
        cached = get_cached_comparison(year, make, model)
        if cached:
            logger.info("market_compare_cached year=%s make=%s model=%s", year, make, model)
            # Recalculate with current our_price if different
            from src.market_scraper import _parse_price, _compute_analysis
            current_our = _parse_price(our_price) or 0
            if current_our > 0 and cached.get("our_price") != current_our:
                cached["our_price"] = current_our
                if cached.get("market_avg"):
                    cached["savings_vs_avg"] = cached["market_avg"] - current_our
                    cached["savings_pct"] = round(
                        cached["savings_vs_avg"] / cached["market_avg"] * 100, 1
                    )
            cached["source"] = "cache"
            return cached

    logger.info("market_compare_scraping year=%s make=%s model=%s", year, make, model)
    result = await scrape_market_comparison(year, make, model, our_price)

    # Cache the result if successful
    if result.get("status") == "ok":
        try:
            save_comparison(result)
        except Exception:
            logger.warning("market_compare_cache_save_failed")

    result["source"] = "fresh"
    return result


@app.get("/market-compare/vin/{vin}", dependencies=[Depends(require_api_key)])
async def market_compare_by_vin(
    vin: str,
    force: bool = Query(False),
):
    """Compare a vehicle from our inventory against the market, by VIN.

    Automatically looks up the vehicle's year/make/model/price from our DB.
    """
    vehicle = get_vehicle_by_vin(vin)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found in our inventory")

    return await market_compare(
        year=vehicle.get("year", ""),
        make=vehicle.get("make", ""),
        model=vehicle.get("model", ""),
        our_price=vehicle.get("price", "0"),
        force=force,
    )
