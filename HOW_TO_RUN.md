# How to Run

## Prerequisites

- Python 3.9+
- PostgreSQL running locally
- `.env` file (see `.env.example`)

## Setup (first time)

```bash
# Create database
psql postgres -c "CREATE DATABASE inventory;"

# Python env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
source .venv/bin/activate
uvicorn src.main:app --port 8001
```

## API

All endpoints except `/health` require `X-API-Key` header.

```bash
# Health
curl http://localhost:8001/health

# Trigger scrape (skips if last run < 10 min)
curl -X POST -H "X-API-Key: YOUR_KEY" http://localhost:8001/scrape

# Force scrape
curl -X POST -H "X-API-Key: YOUR_KEY" "http://localhost:8001/scrape?force=true"

# List vehicles
curl -H "X-API-Key: YOUR_KEY" http://localhost:8001/vehicles

# Search
curl -H "X-API-Key: YOUR_KEY" "http://localhost:8001/vehicles?q=bmw&max_price=25000"

# Single vehicle
curl -H "X-API-Key: YOUR_KEY" http://localhost:8001/vehicles/VIN_HERE
```

## Stop

```bash
lsof -ti:8001 | xargs kill -9
```
