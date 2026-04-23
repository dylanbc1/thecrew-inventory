"""PostgreSQL persistence for scraped inventory.

Tables:
- vehicles: one row per VIN, updated on each scrape
- scrape_runs: log of every scrape attempt for cache/audit
"""

from __future__ import annotations

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
