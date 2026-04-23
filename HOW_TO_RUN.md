# How to Run

## Local

```bash
# Prereqs: Python 3.9+, PostgreSQL running
# First time only:
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run
source .venv/bin/activate
uvicorn src.main:app --port 8001

# Stop
lsof -ti:8001 | xargs kill -9
```

## Deploy (Render)

1. Push to `https://github.com/dylanbc1/thecrew-inventory`
2. Create a **Web Service** on Render, connect the repo, select **Docker**
3. Set env vars: `API_KEY`, `DATABASE_URL`
4. Deploy

## API

All endpoints except `/health` require header `X-API-Key: <your-key>`.

### GET /health

No auth. Returns `{"status": "ok"}`.

### GET /vehicles

List all available vehicles. **Auto-scrapes if data is older than 10 minutes.**

```bash
# All vehicles
curl -H "X-API-Key: KEY" https://YOUR_URL/vehicles

# Search by text (year, make, model)
curl -H "X-API-Key: KEY" "https://YOUR_URL/vehicles?q=bmw x3"

# Filter by make
curl -H "X-API-Key: KEY" "https://YOUR_URL/vehicles?make=Honda"

# Filter by max price
curl -H "X-API-Key: KEY" "https://YOUR_URL/vehicles?max_price=15000"

# Combine filters
curl -H "X-API-Key: KEY" "https://YOUR_URL/vehicles?make=Chevrolet&max_price=20000"

# Include sold/removed vehicles
curl -H "X-API-Key: KEY" "https://YOUR_URL/vehicles?include_unavailable=true"
```

**Response:**
```json
{
  "count": 71,
  "vehicles": [
    {
      "vin": "19VDE1F78DE000752",
      "year": "2013",
      "make": "Acura",
      "model": "Ilx",
      "stock_number": "000752",
      "price": "$9,685",
      "mileage": "136,923",
      "detail_url": "https://www.thecrewautos.com/inventory/...",
      "image_url": "https://imagescf.dealercenter.net/...",
      "is_available": true,
      "first_seen_at": "2026-04-21T...",
      "last_seen_at": "2026-04-23T..."
    }
  ]
}
```

### GET /vehicles/{vin}

Single vehicle by VIN.

```bash
curl -H "X-API-Key: KEY" https://YOUR_URL/vehicles/19VDE1F78DE000752
```

### POST /scrape

Manually trigger a scrape. Skips if last scrape was < 10 min ago.

```bash
# Normal (respects cache)
curl -X POST -H "X-API-Key: KEY" https://YOUR_URL/scrape

# Force (ignores cache)
curl -X POST -H "X-API-Key: KEY" "https://YOUR_URL/scrape?force=true"
```
