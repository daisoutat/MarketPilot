# -*- coding: utf-8 -*-
"""Research snapshot driver tests (PA-API mocked) + dashboard research/compare."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from common.models import Marketplace, PriceSnapshot, Product, ResearchTarget
from common.orders import upsert_product
from common.paapi import PaApiClient
from common.research import sync_research_driver

from tests.test_paapi import make_item


class FakePaClient:
    normalize_item = staticmethod(PaApiClient.normalize_item)

    def __init__(self, items_by_asin=None, search_map=None):
        self.items_by_asin = items_by_asin or {}
        self.search_map = search_map or {}

    async def get_items(self, ids):
        return [self.items_by_asin[i] for i in ids if i in self.items_by_asin]

    async def search_items(self, keywords):
        return self.search_map.get(keywords, [])


def _fake_builder(items_by_asin, search_map=None):
    async def builder(market):
        return FakePaClient(items_by_asin, search_map)
    return builder


@pytest.mark.asyncio
async def test_sync_research_driver_snapshots(seeded_session):
    market_us = (await seeded_session.execute(select(Marketplace).where(Marketplace.code == "US"))).scalar_one()

    owned_id = await upsert_product(seeded_session, "B0OWN1", "Our Coffee")
    seeded_session.add(PriceSnapshot(product_id=owned_id, marketplace_id=market_us.id,
                                     ours=True, price=9.99, currency="USD"))
    seeded_session.add_all([
        ResearchTarget(marketplace_id=market_us.id, asin="B0EXT1", keywords=""),
        ResearchTarget(marketplace_id=market_us.id, asin="B0EXT2", keywords="coffee"),
    ])
    await seeded_session.commit()

    items = {
        "B0OWN1": make_item("B0OWN1", "Our Coffee", 9.99, rank=11),
        "B0EXT1": make_item("B0EXT1", "Competitor A", 12.50, currency="CAD", rank=5),
        "B0EXT2": make_item("B0EXT2", "Competitor B", 15.00, rank=8),
        "B0NEW1": make_item("B0NEW1", "Discovered", 7.25, rank=2),
    }
    search_map = {"coffee": [items["B0NEW1"]]}
    runs = await sync_research_driver(
        seeded_session,
        _fake_builder(items, search_map),
        search_keywords=["coffee"],
    )
    await seeded_session.commit()

    by_market = {r.market: r for r in runs}
    assert set(by_market) == {"US", "CA"}

    us = by_market["US"]
    assert us.owned == 1                       # B0OWN1
    assert us.research == 3                    # B0EXT1 + B0EXT2 + discovered B0NEW1
    assert us.snapshots == 4
    assert us.new_targets == 1                 # B0NEW1 registered
    assert us.keyword_searches == 1            # "coffee" deduped across target + env seeds

    ca = by_market["CA"]
    assert ca.owned == 0
    assert ca.research == 1                    # B0NEW1 discovered for CA too
    assert ca.new_targets == 1

    snap = (await seeded_session.execute(
        select(PriceSnapshot).join(Product, Product.id == PriceSnapshot.product_id)
        .where(Product.asin == "B0EXT1")
    )).scalar_one()
    assert float(snap.price) == 12.5 and snap.currency == "CAD" and snap.ours is False

    owned_snaps = (await seeded_session.execute(
        select(PriceSnapshot).join(Product, Product.id == PriceSnapshot.product_id)
        .where(Product.asin == "B0OWN1")
    )).scalars().all()
    assert owned_snaps and all(s.ours for s in owned_snaps)

    targets = (await seeded_session.execute(select(ResearchTarget))).scalars().all()
    original = [t for t in targets if t.asin in ("B0EXT1", "B0EXT2")]
    assert all(t.last_snap_at is not None for t in original)


@pytest.mark.asyncio
async def test_driver_survives_per_market_failure(seeded_session):
    market_us = (await seeded_session.execute(select(Marketplace).where(Marketplace.code == "US"))).scalar_one()
    seeded_session.add(ResearchTarget(marketplace_id=market_us.id, asin="B0EXT1", keywords=""))
    await seeded_session.commit()

    class ExplodingClient(FakePaClient):
        async def get_items(self, ids):
            raise RuntimeError("certificate expired")

    async def builder(market):
        return ExplodingClient({}, {})

    runs = await sync_research_driver(seeded_session, builder)
    us = {r.market: r for r in runs}["US"]
    assert us.errors and "RuntimeError" in us.errors[0]
    assert us.snapshots == 0
    assert (await seeded_session.execute(select(PriceSnapshot))).scalars().all() == []


# -- dashboard endpoints ----------------------------------------------------

async def _seed_compare(session):
    markets = {m.code: m for m in (await session.execute(select(Marketplace))).scalars().all()}
    us, ca = markets["US"], markets["CA"]
    pid = await upsert_product(session, "B0COMP1", "Compare Widget")
    session.add(ResearchTarget(marketplace_id=us.id, asin="B0COMP1", keywords="widget"))
    session.add_all([
        PriceSnapshot(product_id=pid, marketplace_id=us.id, ours=False, price=10.0, buybox=10.0, currency="USD"),
        PriceSnapshot(product_id=pid, marketplace_id=ca.id, ours=False, price=13.0, buybox=13.0, currency="CAD"),
    ])
    await session.commit()


@pytest.mark.asyncio
async def test_compare_endpoint_normalizes_cad(seeded_session, app_client, monkeypatch):
    async def fake_fetch(base, quote):
        assert (base, quote) == ("CAD", "USD")
        return 0.75

    monkeypatch.setattr("common.fx._fetch_rate", fake_fetch)
    await _seed_compare(seeded_session)

    resp = app_client.get("/api/v1/dashboard/compare")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert len(rows) == 1
    entry = rows[0]
    assert entry["asin"] == "B0COMP1"
    by_market = {m["market"]: m for m in entry["markets"]}
    assert round(by_market["US"]["price_usd"], 2) == 10.0
    assert round(by_market["CA"]["price_usd"], 2) == 9.75
    assert round(entry["gap_usd"], 2) == 0.25


@pytest.mark.asyncio
async def test_research_endpoint(seeded_session, app_client, monkeypatch):
    async def fake_fetch(base, quote):
        return 0.75

    monkeypatch.setattr("common.fx._fetch_rate", fake_fetch)
    await _seed_compare(seeded_session)

    resp = app_client.get("/api/v1/dashboard/research")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert len(rows) == 1
    row = rows[0]
    assert row["asin"] == "B0COMP1" and row["market"] == "US"
    assert row["price"] == 10.0 and row["currency"] == "USD"
    assert row["keywords"] == "widget"
    assert app_client.get("/api/v1/dashboard/research", params={"market": "CA"}).json()["rows"] == []