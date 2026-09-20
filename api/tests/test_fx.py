# -*- coding: utf-8 -*-
"""FX caching / USD normalization tests (fetch injected, no network)."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from common.fx import FxError, _fetch_rate, get_rate, price_in_usd
from common.models import FxRate


@pytest.mark.asyncio
async def test_rate_cached_until_ttl(seeded_session):
    calls = []

    async def fetch(base, quote):
        calls.append((base, quote))
        return 0.75

    assert await get_rate(seeded_session, "CAD", "USD", fetch=fetch) == 0.75
    assert await get_rate(seeded_session, "cad", "usd", fetch=fetch) == 0.75
    assert calls == [("CAD", "USD")]  # single fetch, case-insensitive dedupe
    rows = (await seeded_session.execute(select(FxRate))).scalars().all()
    assert len(rows) == 1
    assert float(rows[0].rate) == 0.75


@pytest.mark.asyncio
async def test_rate_refreshes_when_stale(seeded_session):
    calls = []

    async def fetch(base, quote):
        calls.append((base, quote))
        return 0.70 + 0.05 * len(calls)

    await get_rate(seeded_session, "CAD", "USD", fetch=fetch, ttl_seconds=3600)
    assert await get_rate(seeded_session, "CAD", "USD", fetch=fetch, ttl_seconds=-1) == pytest.approx(0.80)
    assert len(calls) == 2  # stale -> refetch (row upserted, not duplicated)
    assert len((await seeded_session.execute(select(FxRate))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_same_currency_is_identity(seeded_session):
    async def fetch(base, quote):  # pragma: no cover - must not be called
        raise AssertionError("same-currency should short-circuit")

    assert await get_rate(seeded_session, "USD", "USD", fetch=fetch) == 1.0


@pytest.mark.asyncio
async def test_price_in_usd_normalizes(seeded_session):
    async def fetch(base, quote):
        assert (base, quote) == ("CAD", "USD")
        return 0.75

    assert await price_in_usd(seeded_session, 10, "CAD", fetch=fetch) == 7.5
    assert await price_in_usd(seeded_session, 12.5, "USD", fetch=fetch) == 12.5
    assert await price_in_usd(seeded_session, None, "CAD", fetch=fetch) == 0.0


@pytest.mark.asyncio
async def test_fetch_error_propagates(seeded_session):
    async def fetch(base, quote):
        raise FxError("provider down")

    with pytest.raises(FxError):
        await get_rate(seeded_session, "CAD", "USD", fetch=fetch)


def test_fetch_rate_parses_bad_payload(monkeypatch):
    import httpx

    class FakeResp:
        status_code = 200
        text = "{}"

        def json(self):
            return {}

    async def get(self, url, **kwargs):
        return FakeResp()

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    with pytest.raises(FxError):
        import asyncio

        asyncio.run(_fetch_rate("CAD", "USD"))