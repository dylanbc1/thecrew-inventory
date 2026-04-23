FROM python:3.11-slim

# System deps for Camoufox headless browser (used by Scrapling)
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget curl libgtk-3-0 libdbus-glib-1-2 libxt6 libx11-xcb1 \
    libasound2 libxcomposite1 libxdamage1 libxrandr2 libxss1 \
    libxtst6 fonts-liberation libgbm1 libpango-1.0-0 libcairo2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Fetch Camoufox browser binary
RUN python -m camoufox fetch

COPY src/ src/

CMD uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8001}
