"""PostgreSQL persistence for scraped inventory and market comparisons.

Tables:
- vehicles: one row per VIN, updated on each scrape
- scrape_runs: log of every scrape attempt for cache/audit
- market_comparisons: cached market comparison results (TTL 24h)
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

import psycopg2
import psycopg2.extras

from src.config import get_settings


def _conn():
    return psycopg2.connect(get_settings().database_url)


def init_db() -> None:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vehicles (
                    vin             TEXT PRIMARY KEY,
                    year            TEXT,
                    make            TEXT,
                    model           TEXT,
                    stock_number    TEXT,
                    price           TEXT,
                    mileage         TEXT,
                    detail_url      TEXT,
                    image_url       TEXT,
                    is_available    BOOLEAN DEFAULT TRUE,
                    first_seen_at   TIMESTAMPTZ,
                    last_seen_at    TIMESTAMPTZ
                );

                CREATE TABLE IF NOT EXISTS scrape_runs (
                    id              SERIAL PRIMARY KEY,
                    started_at      TIMESTAMPTZ,
                    completed_at    TIMESTAMPTZ,
                    vehicles_found  INTEGER DEFAULT 0,
                    status          TEXT DEFAULT 'running'
                );

                CREATE TABLE IF NOT EXISTS market_comparisons (
                    id              SERIAL PRIMARY KEY,
                    lookup_key      TEXT UNIQUE NOT NULL,
                    year            TEXT,
                    make            TEXT,
                    model           TEXT,
                    our_price       INTEGER,
                    market_avg      INTEGER,
                    market_min      INTEGER,
                    market_max      INTEGER,
                    listing_count   INTEGER DEFAULT 0,
                    savings_vs_avg  INTEGER DEFAULT 0,
                    savings_pct     REAL DEFAULT 0,
                    deal_rating     TEXT,
                    advantages      JSONB DEFAULT '[]',
                    raw_data        JSONB DEFAULT '{}',
                    scraped_at      TIMESTAMPTZ
                );
            """)
        conn.commit()
    finally:
        conn.close()


# ── Scrape runs ──────────────────────────────────────────────────

def start_run() -> int:
    now = datetime.now(timezone.utc)
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO scrape_runs (started_at, status) VALUES (%s, 'running') RETURNING id",
                (now,),
            )
            run_id = cur.fetchone()[0]
        conn.commit()
        return run_id
    finally:
        conn.close()


def finish_run(run_id: int, count: int, status: str = "success") -> None:
    now = datetime.now(timezone.utc)
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE scrape_runs SET completed_at=%s, vehicles_found=%s, status=%s WHERE id=%s",
                (now, count, status, run_id),
            )
        conn.commit()
    finally:
        conn.close()


def last_successful_run() -> Optional[Dict]:
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM scrape_runs WHERE status='success' ORDER BY id DESC LIMIT 1"
            )
            row = cur.fetchone()
            if row:
                # Convert datetimes to ISO strings for JSON serialization
                d = dict(row)
                for k in ("started_at", "completed_at"):
                    if d.get(k) and hasattr(d[k], "isoformat"):
                        d[k] = d[k].isoformat()
                return d
            return None
    finally:
        conn.close()


# ── Vehicles ─────────────────────────────────────────────────────

def upsert_vehicles(vehicles: List[Dict]) -> int:
    now = datetime.now(timezone.utc)
    seen_vins = set()

    conn = _conn()
    try:
        with conn.cursor() as cur:
            for v in vehicles:
                vin = v.get("vin", "")
                if not vin:
                    continue
                seen_vins.add(vin)
                cur.execute("""
                    INSERT INTO vehicles (vin, year, make, model, stock_number, price,
                        mileage, detail_url, image_url, is_available, first_seen_at, last_seen_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, TRUE, %s, %s)
                    ON CONFLICT (vin) DO UPDATE SET
                        year=EXCLUDED.year, make=EXCLUDED.make, model=EXCLUDED.model,
                        stock_number=EXCLUDED.stock_number, price=EXCLUDED.price,
                        mileage=EXCLUDED.mileage, detail_url=EXCLUDED.detail_url,
                        image_url=EXCLUDED.image_url, is_available=TRUE, last_seen_at=EXCLUDED.last_seen_at
                """, (
                    vin, v.get("year",""), v.get("make",""), v.get("model",""),
                    v.get("stock_number",""), v.get("price",""), v.get("mileage",""),
                    v.get("detail_url",""), v.get("image_url",""), now, now,
                ))

            if seen_vins:
                cur.execute(
                    "UPDATE vehicles SET is_available=FALSE WHERE vin != ALL(%s)",
                    (list(seen_vins),),
                )

        conn.commit()
    finally:
        conn.close()

    return len(seen_vins)


def get_vehicles(
    available_only: bool = True,
    make: Optional[str] = None,
    max_price: Optional[int] = None,
    q: Optional[str] = None,
) -> List[Dict]:
    clauses = []
    params = []

    if available_only:
        clauses.append("is_available = TRUE")
    if make:
        clauses.append("LOWER(make) = %s")
        params.append(make.lower())
    if q:
        clauses.append("LOWER(year || ' ' || make || ' ' || model) LIKE %s")
        params.append(f"%{q.lower()}%")

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT * FROM vehicles{where} ORDER BY make, model, year"

    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        conn.close()

    results = []
    for row in rows:
        d = dict(row)
        for k in ("first_seen_at", "last_seen_at"):
            if d.get(k) and hasattr(d[k], "isoformat"):
                d[k] = d[k].isoformat()
        results.append(d)

    if max_price:
        def parse_price(p: str) -> int:
            digits = re.sub(r"[^\d]", "", p)
            return int(digits) if digits else 999999
        results = [v for v in results if parse_price(v.get("price","")) <= max_price]

    return results


def get_vehicle_by_vin(vin: str) -> Optional[Dict]:
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM vehicles WHERE vin = %s", (vin,))
            row = cur.fetchone()
            if row:
                d = dict(row)
                for k in ("first_seen_at", "last_seen_at"):
                    if d.get(k) and hasattr(d[k], "isoformat"):
                        d[k] = d[k].isoformat()
                return d
            return None
    finally:
        conn.close()


# ── Market Comparisons ─────────────────────────────────────────

_MARKET_CACHE_HOURS = 24  # market data valid for 24h


def _market_key(year: str, make: str, model: str) -> str:
    return f"{year}|{make.lower().strip()}|{model.lower().strip()}"


def get_cached_comparison(year: str, make: str, model: str) -> Optional[Dict]:
    """Return cached market comparison if fresh (< 24h old)."""
    key = _market_key(year, make, model)
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM market_comparisons WHERE lookup_key = %s", (key,)
            )
            row = cur.fetchone()
            if not row:
                return None

            d = dict(row)
            scraped = d.get("scraped_at")
            if scraped and hasattr(scraped, "timestamp"):
                age_hours = (datetime.now(timezone.utc) - scraped).total_seconds() / 3600
                if age_hours > _MARKET_CACHE_HOURS:
                    return None  # stale

            # Deserialize
            if d.get("scraped_at") and hasattr(d["scraped_at"], "isoformat"):
                d["scraped_at"] = d["scraped_at"].isoformat()
            d["competitive_advantages"] = d.pop("advantages", [])
            d["comparable_listings"] = (d.pop("raw_data", {}) or {}).get("listings", [])
            d["status"] = "ok"
            d.pop("id", None)
            d.pop("lookup_key", None)
            return d
    finally:
        conn.close()


def save_comparison(data: Dict) -> None:
    """Upsert market comparison results."""
    key = _market_key(data.get("year", ""), data.get("make", ""), data.get("model", ""))
    now = datetime.now(timezone.utc)
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO market_comparisons
                    (lookup_key, year, make, model, our_price, market_avg, market_min,
                     market_max, listing_count, savings_vs_avg, savings_pct, deal_rating,
                     advantages, raw_data, scraped_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (lookup_key) DO UPDATE SET
                    our_price=EXCLUDED.our_price, market_avg=EXCLUDED.market_avg,
                    market_min=EXCLUDED.market_min, market_max=EXCLUDED.market_max,
                    listing_count=EXCLUDED.listing_count, savings_vs_avg=EXCLUDED.savings_vs_avg,
                    savings_pct=EXCLUDED.savings_pct, deal_rating=EXCLUDED.deal_rating,
                    advantages=EXCLUDED.advantages, raw_data=EXCLUDED.raw_data,
                    scraped_at=EXCLUDED.scraped_at
            """, (
                key,
                data.get("year", ""),
                data.get("make", ""),
                data.get("model", ""),
                data.get("our_price", 0),
                data.get("market_avg", 0),
                data.get("market_min", 0),
                data.get("market_max", 0),
                data.get("listing_count", 0),
                data.get("savings_vs_avg", 0),
                data.get("savings_pct", 0),
                data.get("deal_rating", ""),
                json.dumps(data.get("competitive_advantages", [])),
                json.dumps({"listings": data.get("comparable_listings", [])}),
                now,
            ))
        conn.commit()
    finally:
        conn.close()
