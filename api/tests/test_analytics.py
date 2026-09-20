# -*- coding: utf-8 -*-
"""Phase 6: multidimensional analytics aggregates, tree rollups, segments + API."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from common.analytics import build_tree, family_metrics, segment_products
from common.categorize import ensure_family, find_family
from common.forecast import compute_forecast
from common.models import (
    Category,
    Forecast,
    Marketplace,
    Order,
    OrderItem,
    PriceSnapshot,
    Product,
    Seller,
    Settlement,
    SettlementLine,
)
from common.orders import upsert_product


async def _seed_eco(session, market, seller, pid, asin, units, days_ago=2, revenue=100.0, net=40.0, currency="USD"):
    order = Order(
        seller_id=seller.id, marketplace_id=market.id,
        amazon_order_id="ECO-%s-%d-%d" % (asin, days_ago, int(units)),
        purchase_date=datetime.now(UTC) - timedelta(days=days_ago), status="Shipped",
    )
    session.add(order)
    await session.flush()
    session.add(OrderItem(
        order_id=order.id, product_id=pid, asin=asin, seller_sku="SKU",
        quantity=units, unit_price=revenue, item_currency=currency,
    ))
    sett = Settlement(
        seller_id=seller.id, marketplace_id=market.id, posted_date=order.purchase_date,
        gross=revenue * units, fees=(revenue - net) * units, net=net * units, currency=currency,
    )
    session.add(sett)
    await session.flush()
    session.add(SettlementLine(
        settlement_id=sett.id, product_id=pid, asin=asin, sku="SKU",
        order_id=order.amazon_order_id, units=units, revenue=revenue * units,
        fees=(revenue - net) * units, net=net * units, currency=currency,
    ))


async def _upi(session, asin, title, market, price=50.0, rank=10, ours=True):
    pid = await upsert_product(session, asin, title)
    session.add(PriceSnapshot(product_id=pid, marketplace_id=market.id, ours=ours,
                              price=price, sales_rank=rank, currency=market.currency))
    return pid


async def _make_tree(session, market, seller, asin, title):
    rule = find_family(title=title)
    assert rule is not None
    family = await ensure_family(session, market.id, rule)
    await session.commit()
    pid = await _upi(session, asin, title, market)
    product = (await session.execute(select(Product).where(Product.id == pid))).scalar_one()
    product.family_id = family.id
    await session.commit()
    return family, pid


async def test_build_tree_rollups_and_ph(seeded_session):
    us = (await seeded_session.execute(select(Marketplace).where(Marketplace.code == "US"))).scalar_one()
    seller = Seller(name="Analytics Seller", credentials_ref="env")
    seeded_session.add(seller)
    await seeded_session.commit()

    fam, pid = await _make_tree(seeded_session, us, seller, "B0AUDIO2", "Bluetooth Headphones Studio")
    await _seed_eco(seeded_session, us, seller, pid, "B0AUDIO2", units=5, revenue=120.0, net=60.0)
    fam2, pid2 = await _make_tree(seeded_session, us, seller, "B0SPKR01", "Portable Speaker Waterproof")
    await _seed_eco(seeded_session, us, seller, pid2, "B0SPKR01", units=2, revenue=90.0, net=30.0, days_ago=1)
    await seeded_session.commit()

    tree = await build_tree(seeded_session, days=90, market_code=us.code)
    assert len(tree) == 4  # 2 families + 1 shared niche + 1 category root

    family = next(n for n in tree if n["name"] == "Headphones & Earbuds")
    assert family["kind"] == "family"
    assert family["product_count"] == 1
    assert family["units"] == 5
    assert family["revenue"] == pytest.approx(600.0)
    assert family["net"] == pytest.approx(300.0)
    assert family["margin_pct"] == pytest.approx(50.0)

    category = next(n for n in tree if n["kind"] == "category")
    assert category["name"] == "Electronics"
    assert category["units"] == 7
    assert category["revenue"] == pytest.approx(780.0)
    assert category["child_count"] == 1  # Audio niche


async def test_segment_products_drills_into_family(seeded_session):
    us = (await seeded_session.execute(select(Marketplace).where(Marketplace.code == "US"))).scalar_one()
    seller = Seller(name="Seg Seller", credentials_ref="env")
    seeded_session.add(seller)
    await seeded_session.commit()

    fam, pid = await _make_tree(seeded_session, us, seller, "B0SEG01", "Wireless Earbuds Sport")
    await _seed_eco(seeded_session, us, seller, pid, "B0SEG01", units=3)
    await seeded_session.commit()

    out = await segment_products(seeded_session, fam.id, days=90, market_code="US", limit=10)
    assert out["exists"]
    assert out["path"] == "Electronics/Audio/Headphones & Earbuds"
    rows = out["rows"]
    assert rows and rows[0]["asin"] == "B0SEG01"
    assert rows[0]["units"] == 3
    assert rows[0]["revenue"] == pytest.approx(300.0)


async def test_analytics_endpoints(seeded_session, app_client):
    us = (await seeded_session.execute(select(Marketplace).where(Marketplace.code == "US"))).scalar_one()
    seller = Seller(name="API Seller", credentials_ref="env")
    seeded_session.add(seller)
    await seeded_session.commit()

    fam, pid = await _make_tree(seeded_session, us, seller, "B0API01", "Smart Phone Charger")
    for d in (1, 4, 8, 15):
        await _seed_eco(seeded_session, us, seller, pid, "B0API01", units=2, days_ago=d)
    await seeded_session.commit()

    forecast = await compute_forecast(seeded_session, pid)
    assert forecast is not None
    seeded_session.add(Forecast(
        product_id=pid, model="seasonal-ols", unit="units/day",
        window_days=forecast["window_days"], horizon=30,
        params=forecast["params"], history=forecast["history"], points=forecast["points"],
    ))
    await seeded_session.commit()

    r = app_client.get("/api/v1/analytics/tree?days=90&market=US")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] >= 3
    family = next(n for n in body["nodes"] if n["name"] == "Phone Accessories")
    assert family["units"] == 8

    seg = app_client.get("/api/v1/analytics/segment/%d?days=90&market=US" % fam.id)
    assert seg.status_code == 200
    assert seg.json()["rows"][0]["asin"] == "B0API01"

    fc = app_client.get("/api/v1/analytics/forecast?days=90&market=US")
    assert fc.status_code == 200, fc.text
    assert fc.json()["count"] == 1
    assert fc.json()["rows"][0]["asin"] == "B0API01"

    det = app_client.get("/api/v1/analytics/forecast/%d" % pid)
    assert det.status_code == 200, det.text
    assert len(det.json()["points"]["dates"]) == 30


async def test_family_metrics_multi_currency(seeded_session):
    us = (await seeded_session.execute(select(Marketplace).where(Marketplace.code == "US"))).scalar_one()
    ca = (await seeded_session.execute(select(Marketplace).where(Marketplace.code == "CA"))).scalar_one()
    seller = Seller(name="FX Seller", credentials_ref="env")
    seeded_session.add(seller)
    await seeded_session.commit()

    fam, pid_us = await _make_tree(seeded_session, us, seller, "B0FX01", "Formal Button Down Shirt")
    await _seed_eco(seeded_session, us, seller, pid_us, "B0FX01", units=2, revenue=100.0, net=50.0, currency="USD")
    pid_ca = await _upi(seeded_session, "B0FX02", "Casual Oxford Shirt", ca, price=75.0)
    product = (await seeded_session.execute(select(Product).where(Product.id == pid_ca))).scalar_one()
    product.family_id = fam.id
    await seeded_session.commit()
    await _seed_eco(seeded_session, ca, seller, pid_ca, "B0FX02", units=4, revenue=60.0, net=25.0, currency="CAD")
    await seeded_session.commit()

    metrics = await family_metrics(seeded_session, days=90, market_code="US")
    row = next(m for m in metrics if m["name"] == fam.name)
    assert row["units"] == 2          # US-only orders
    assert row["revenue"] == pytest.approx(200.0)
    assert row["net"] == pytest.approx(100.0)
    assert row["currency"] == "USD"


async def test_tree_ignores_outside_window(seeded_session):
    us = (await seeded_session.execute(select(Marketplace).where(Marketplace.code == "US"))).scalar_one()
    seller = Seller(name="Window Seller", credentials_ref="env")
    seeded_session.add(seller)
    await seeded_session.commit()

    fam, pid = await _make_tree(seeded_session, us, seller, "B0OLD01", "Coffee Percolator Classic")
    await _seed_eco(seeded_session, us, seller, pid, "B0OLD01", units=9, days_ago=400)
    await seeded_session.commit()

    tree = await build_tree(seeded_session, days=90, market_code="US")
    family = next(n for n in tree if n["name"] == "Coffee & Espresso")
    assert family["units"] == 0
    assert family["revenue"] == 0.0