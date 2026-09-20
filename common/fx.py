"""FX: async, cached exchange rates (USD base) for cross-market comparisons.

Sources: Frankfurter (European Central Bank daily reference rates, free, no
key) by default; override ``MP_FX_BASE_URL`` for a different provider that
speaks the  ``/latest?base=<FROM>&symbols=<TO>`` shape.

Rates cache in the ``fx_rates`` table with a TTL (default 30 min). Conversions
happen at view time; stored money stays in its native ISO currency.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from .models import FxRate

FX_TTL_SECONDS = int(os.environ.get("MP_FX_TTL", "1800"))
FX_BASE_URL = os.environ.get("MP_FX_BASE_URL", "https://api.frankfurter.app")


class FxError(Exception):
    pass


async def _fetch_rate(base: str, quote: str, base_url: str = FX_BASE_URL, timeout: float = 8.0) -> float:
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(f"{base_url}/latest", params={"base": base, "symbols": quote})
    if resp.status_code != 200:
        raise FxError(f"fx fetch failed ({resp.status_code}): {resp.text[:200]}")
    rates = (resp.json() or {}).get("rates") or {}
    value = rates.get(quote)
    if value is None:
        raise FxError(f"fx response missing {quote} for base {base}")
    return round(float(value), 6)


async def _get_row(session, base: str, quote: str) -> FxRate | None:
    return (
        await session.execute(
            select(FxRate).where(FxRate.base == base, FxRate.quote == quote).limit(1)
        )
    ).scalar_one_or_none()


def _as_utc(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def get_rate(session, base: str, quote: str, fetch=None, ttl_seconds: int = FX_TTL_SECONDS) -> float:
    """Return the cached rate base->quote, refreshing via ``fetch`` on miss/stale."""
    base, quote = base.upper(), quote.upper()
    if base == quote:
        return 1.0
    now = datetime.now(UTC)
    row = await _get_row(session, base, quote)
    if row is not None and row.refreshed_at is not None:
        if _as_utc(row.refreshed_at) >= now - timedelta(seconds=ttl_seconds):
            return float(row.rate)
    rate = await (fetch or _fetch_rate)(base, quote)
    if row is not None:
        row.rate = rate
        row.refreshed_at = now
    else:
        session.add(FxRate(base=base, quote=quote, rate=rate, refreshed_at=now))
    await session.flush()
    return rate


async def price_in_usd(session, amount, currency: str, fetch=None) -> float:
    """Normalize an amount in its ISO currency to USD at view time."""
    if not amount:
        return 0.0
    currency = (currency or "USD").upper()
    if currency == "USD":
        return round(float(amount), 2)
    rate = await get_rate(session, currency, "USD", fetch=fetch)
    return round(float(amount) * rate, 2)