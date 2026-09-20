# -*- coding: utf-8 -*-
"""Dashboard endpoint tests (dev auth, in-memory SQLite)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from common import telemetry
from common.models import (
    Inventory,
    Marketplace,
    Order,
    OrderItem,
    PipelineRun,
    PriceSnapshot,
    Product,
    Seller,
    Settlement,
    SettlementLine,
)


async def _seed_dashboard_data(session):
    market = (await session.execute(select(Marketplace).where(Marketplace.code == "US"))).scalar_one()
    seller = Seller(name="Demo", credentials_ref="env:SP_API")
    session.add(seller)
    await session.flush()
    product = Product(asin="B0DASH1", title="Dash Widget")
    session.add(product)
    await session.flush()
    order = Order(
        seller_id=seller.id, marketplace_id=market.id,
        amazon_order_id="DASH-1", status="Shipped",
        purchase_date=datetime.now(UTC) - timedelta(days=2),
        buyer_name="Buyer One", city="New York", state_or_region="NY",
    )
    session.add(order)
    await session.flush()
    session.add(OrderItem(order_id=order.id, product_id=product.id, asin="B0DASH1", seller_sku="SKU-D", quantity=3, unit_price=12.5, item_currency="USD"))
    run = await telemetry.start_run(session, "orders-sync", "US")
    await telemetry.finish_run(session, run, rows=1)
    await session.commit()


@pytest.mark.asyncio
async def test_summary_shape(seeded_session, app_client):
    await _seed_dashboard_data(seeded_session)
    resp = app_client.get("/api/v1/dashboard/summary?days=30")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"]["orders"] == 1
    assert data["total"]["units"] == 3
    assert round(data["total"]["gross"], 2) == 37.5
    assert any(m["market"] == "US" and m["gross"] == 37.5 for m in data["by_market"])
    assert {s["status"] for s in data["by_status"]} == {"Shipped"}


@pytest.mark.asyncio
async def test_series(seeded_session, app_client):
    await _seed_dashboard_data(seeded_session)
    resp = app_client.get("/api/v1/dashboard/series?days=30")
    assert resp.status_code == 200
    points = resp.json()["points"]
    assert len(points) >= 1
    assert points[-1]["orders"] >= 1


@pytest.mark.asyncio
async def test_orders_feed_filters(seeded_session, app_client):
    await _seed_dashboard_data(seeded_session)
    base = app_client.get("/api/v1/dashboard/orders")
    assert base.status_code == 200
    assert base.json()["total"] == 1
    row = base.json()["orders"][0]
    assert row["market"] == "US" and row["units"] == 3 and row["gross"] == 37.5
    by_status = app_client.get("/api/v1/dashboard/orders", params={"status": "Shipped"})
    assert by_status.json()["total"] == 1
    empty = app_client.get("/api/v1/dashboard/orders", params={"status": "Pending"})
    assert empty.json()["total"] == 0


@pytest.mark.asyncio
async def test_sync_and_marketplaces(seeded_session, app_client):
    await _seed_dashboard_data(seeded_session)
    sync = app_client.get("/api/v1/dashboard/sync")
    assert sync.status_code == 200
    assert any(r["job"] == "orders-sync" and r["status"] == "done" for r in sync.json()["runs"])
    markets = app_client.get("/api/v1/dashboard/marketplaces")
    codes = {m["code"] for m in markets.json()["marketplaces"]}
    assert codes == {"US", "CA"}
    hz = app_client.get("/api/v1/dashboard/healthz")
    assert hz.status_code == 200 and hz.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_healthz_and_ready(app_client):
    assert app_client.get("/healthz").json()["status"] == "ok"
    ready = app_client.get("/healthz/ready").json()
    assert ready["status"] in ("ok", "degraded")
    me = app_client.get("/api/v1/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"].startswith("demo@")


async def _seed_inventory_margin(session):
    market = (await session.execute(select(Marketplace).where(Marketplace.code == "US"))).scalar_one()
    seller = Seller(name="Demo", credentials_ref="env:SP_API")
    session.add(seller)
    await session.flush()
    product = Product(asin="B0MARG1", title="Margin Widget")
    session.add(product)
    await session.flush()
    session.add(PriceSnapshot(
        product_id=product.id, marketplace_id=market.id, ours=True,
        price=12.5, buybox=12.5, currency="USD",
    ))
    session.add(Inventory(product_id=product.id, condition="New", quantity=7))
    settlement = Settlement(
        seller_id=seller.id, marketplace_id=market.id,
        posted_date=datetime.now(UTC) - timedelta(days=10),
        gross=500.0, fees=20.0, net=480.0, currency="USD",
    )
    session.add(settlement)
    await session.flush()
    session.add(SettlementLine(
        settlement_id=settlement.id, product_id=product.id,
        asin="B0MARG1", sku="MARG-SKU", order_id="ORD-M",
        units=10, revenue=125.0, fees=25.0, net=100.0, currency="USD",
    ))
    await session.commit()


@pytest.mark.asyncio
async def test_inventory_endpoint(app_client, seeded_session):
    await _seed_inventory_margin(seeded_session)
    resp = app_client.get("/api/v1/dashboard/inventory")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert len(rows) == 1
    row = rows[0]
    assert row["asin"] == "B0MARG1" and row["market"] == "US"
    assert row["price"] == 12.5 and row["quantity"] == 7


@pytest.mark.asyncio
async def test_margin_endpoint(app_client, seeded_session):
    await _seed_inventory_margin(seeded_session)
    resp = app_client.get("/api/v1/dashboard/margin")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert len(rows) == 1
    row = rows[0]
    assert row["asin"] == "B0MARG1"
    assert row["units"] == 10 and row["revenue"] == 125.0
    assert row["fees"] == 25.0 and row["net"] == 100.0
    assert row["currency"] == "USD"
    assert round(row["margin_pct"], 1) == 80.0
    # min_units filter excludes too-thin products
    assert app_client.get("/api/v1/dashboard/margin", params={"min_units": 11}).json()["rows"] == []