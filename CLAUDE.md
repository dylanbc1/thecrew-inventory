# Inventory Service — The Crew Autos

Standalone API that scrapes vehicle inventory from thecrewautos.com, persists to PostgreSQL, and serves via authenticated REST endpoints.

## Stack

- **FastAPI** on port 8001
- **PostgreSQL** (local dev, deployed later)
- **Scrapling + StealthyFetcher** (Camoufox headless browser) to bypass Cloudflare
- **API key auth** via `X-API-Key` header

## How It Works

1. `POST /scrape` triggers a scrape of thecrewautos.com/inventory/
2. Scraped vehicles are upserted into Postgres (VIN is PK)
3. Vehicles not found in the latest scrape are marked `is_available=false`
4. `scrape_runs` table logs every attempt with timestamps
5. Cache logic: if last successful scrape was < `CACHE_TTL_MINUTES` ago, skip

## DB Schema

- **vehicles**: vin (PK), year, make, model, stock_number, price, mileage, detail_url, image_url, is_available, first_seen_at, last_seen_at
- **scrape_runs**: id (serial), started_at, completed_at, vehicles_found, status

## Consumers

- `agent-app` calls this service via `inventory_client.py` (needs `INVENTORY_SERVICE_URL` + `INVENTORY_API_KEY`)
