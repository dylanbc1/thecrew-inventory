from __future__ import annotations

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader

from src.config import get_settings

_header = APIKeyHeader(name="X-API-Key")


async def require_api_key(key: str = Security(_header)) -> str:
    if key != get_settings().api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return key
