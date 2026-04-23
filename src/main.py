"""Inventory Service — FastAPI app for The Crew Autos vehicle inventory.

Scrapes thecrewautos.com, persists to SQLite, serves via authenticated API.
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
    get_vehicle_by_vin,
    get_vehicles,
    init_db,
    last_successful_run,
    start_run,
    upsert_vehicles,
)
from src.scraper import scrape

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Inventory Service — The Crew Autos", version="0.1.0")


@app.on_event("startup")
async def startup():
    init_db()
    logger.info("db_initialized")


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
    q: Optional[str] = Query(None, description="Search text"),
    make: Optional[str] = Query(None),
    max_price: Optional[int] = Query(None),
    include_unavailable: bool = Query(False),
):
    vehicles = get_vehicles(
        available_only=not include_unavailable,
        make=make,
        max_price=max_price,
        q=q,
    )
    return {"count": len(vehicles), "vehicles": vehicles}


@app.get("/vehicles/{vin}", dependencies=[Depends(require_api_key)])
async def get_vehicle(vin: str):
    v = get_vehicle_by_vin(vin)
    if not v:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return v
