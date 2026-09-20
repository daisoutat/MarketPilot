# -*- coding: utf-8 -*-
"""Phase 4: alert rule engine + alerts API."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from common.alerts import ensure_default_rules, run_alerts_driver
from common.models import (
    Alert,
    AlertRule,
    Inventory,
    Marketplace,
    PriceSnapshot,
    Product,
    Seller,
    Settlement,
    SettlementLine,
)
from common.orders import upsert_product


@pytest.fixture
async def seller(seeded_session):
    s = Seller(name="Demo Seller", credentials_ref="env")
    seeded_session.add(s)
    await seeded_session.commit()
    return s


async def _market(session, code: str) -> Marketplace:
    return (await session.execute(select(Marketplace).where(Marketplace.code == code))).scalar_one()


async def _product(session, asin: str, title: str = "") -> int:
    return await upsert_product(session, asin, title)


@pytest.mark.asyncio
async def test_ensure_default_rules(seeded_session, seller):
    added = await ensure_default_rules(seeded_session, seller.id)
    assert added == 3
    kinds = set(
        (await seeded_session.execute(select(AlertRule.kind).where(AlertRule.seller_id == seller.id))).scalars()
    )
    assert kinds == {"price_drop", "stock_out", "margin_erosion"}
    # Idempotent on a second call.
    assert await ensure_default_rules(seeded_session, seller.id) == 0


@pytest.mark.asyncio
async def test_price_drop_alert_with_cooldown(seeded_session, seller):
    us = await _market(seeded_session, "US")
    pid = await _product(seeded_session, "B0DROP1", "Watch")
    now = datetime.now()
    seeded_session.add_all([
        PriceSnapshot(product_id=pid, marketplace_id=us.id, ours=True,
                      price=100.00, currency="USD", captured_at=now - timedelta(days=2)),
        PriceSnapshot(product_id=pid, marketplace_id=us.id, ours=True,
                      price=88.00, currency="USD", captured_at=now),
    ])
    await seeded_session.commit()

    result = await run_alerts_driver(seeded_session, seller.id)
    await seeded_session.commit()
    assert result["created"].get("price_drop") == 1
    assert result["errors"] == {}

    alerts = (await seeded_session.execute(select(Alert))).scalars().all()
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.kind == "price_drop"
    assert alert.severity == "warning"          # 12% drop < 1.5 x threshold (10%)
    assert alert.product_id == pid
    assert alert.marketplace_id == us.id
    assert alert.meta["drop_pct"] == 12.0
    assert "B0DROP1" in alert.message and "88.00" in alert.message

    # Cooldown: a second evaluation must not duplicate the alert.
    result2 = await run_alerts_driver(seeded_session, seller.id)
    await seeded_session.commit()
    assert result2["created"].get("price_drop", 0) == 0
    assert len((await seeded_session.execute(select(Alert))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_stock_out_alert(seeded_session, seller):
    us = await _market(seeded_session, "US")
    pid = await _product(seeded_session, "B0STOCK1")
    seeded_session.add_all([
        PriceSnapshot(product_id=pid, marketplace_id=us.id, ours=True, price=5.00, currency="USD"),
        Inventory(product_id=pid, quantity=0),
    ])
    await seeded_session.commit()

    result = await run_alerts_driver(seeded_session, seller.id)
    assert result["created"].get("stock_out") == 1
    alert = (await seeded_session.execute(select(Alert).where(Alert.kind == "stock_out"))).scalar_one()
    assert alert.severity == "critical"
    assert alert.meta == {"asin": "B0STOCK1", "market": "US", "quantity": 0, "threshold": 0}


@pytest.mark.asyncio
async def test_margin_erosion_alert(seeded_session, seller):
    us = await _market(seeded_session, "US")
    pid = await _product(seeded_session, "B0MARG1", "Widget")
    settlement = Settlement(
        seller_id=seller.id, marketplace_id=us.id, currency="USD",
        posted_date=datetime.now(),
        gross=100.00, fees=130.00, net=-30.00,
    )
    seeded_session.add(settlement)
    await seeded_session.flush()
    seeded_session.add(SettlementLine(
        settlement_id=settlement.id, product_id=pid, asin="B0MARG1", sku="W1",
        units=1, revenue=100.00, fees=130.00, net=-30.00, currency="USD",
    ))
    await seeded_session.commit()

    result = await run_alerts_driver(seeded_session, seller.id)
    assert result["created"].get("margin_erosion") == 1
    alert = (await seeded_session.execute(select(Alert).where(Alert.kind == "margin_erosion"))).scalar_one()
    assert alert.severity == "critical"   # -30% <= threshold*0.5 (-5)
    assert alert.meta["margin_pct"] == -30.0


async def _seed_alert(session, seller) -> None:
    us = (await session.execute(select(Marketplace).where(Marketplace.code == "US"))).scalar_one()
    pid = await _product(session, "B0DROP1", "Watch")
    now = datetime.now()
    session.add_all([
        PriceSnapshot(product_id=pid, marketplace_id=us.id, ours=True,
                      price=100.00, currency="USD", captured_at=now - timedelta(days=2)),
        PriceSnapshot(product_id=pid, marketplace_id=us.id, ours=True,
                      price=88.00, currency="USD", captured_at=now),
    ])
    session.add(Inventory(product_id=pid, quantity=0))
    await session.commit()
    await run_alerts_driver(session, seller.id)
    await session.commit()


def test_alerts_api(app_client, seeded_session, seller):
    asyncio.run(_seed_alert(seeded_session, seller))

    resp = app_client.get("/api/v1/alerts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "open"
    price_alerts = [r for r in body["rows"] if r["kind"] == "price_drop"]
    stock_alerts = [r for r in body["rows"] if r["kind"] == "stock_out"]
    assert len(price_alerts) == 1 and len(stock_alerts) == 1
    assert price_alerts[0]["market"] == "US"
    assert price_alerts[0]["asin"] == "B0DROP1"
    assert stock_alerts[0]["severity"] == "critical"

    # Resolve it (only affects that alert).
    got = app_client.post(f"/api/v1/alerts/{price_alerts[0]['id']}/resolve")
    assert got.status_code == 200
    assert got.json()["resolved_by"]
    opened = app_client.get("/api/v1/alerts?status=open").json()
    assert all(r["id"] != price_alerts[0]["id"] for r in opened["rows"])
    resolved = app_client.get("/api/v1/alerts?status=resolved").json()
    assert any(r["id"] == price_alerts[0]["id"] for r in resolved["rows"])


def test_alerts_rules_api(app_client):
    created = app_client.post("/api/v1/alerts/rules", json={
        "kind": "price_drop", "threshold": 5.0, "cooldown_hours": 12, "enabled": True,
    })
    assert created.status_code == 200
    rid = created.json()["id"]

    listed = app_client.get("/api/v1/alerts/rules").json()["rules"]
    assert any(r["kind"] == "price_drop" for r in listed)

    updated = app_client.patch(f"/api/v1/alerts/rules/{rid}", json={"enabled": False, "threshold": 7.5})
    assert updated.status_code == 200
    assert updated.json()["threshold"] == 7.5
    assert updated.json()["enabled"] is False

    bad = app_client.post("/api/v1/alerts/rules", json={"kind": "nonsense", "threshold": 1.0})
    assert bad.status_code == 422

    missing = app_client.patch("/api/v1/alerts/rules/99999", json={"enabled": True})
    assert missing.status_code == 404
    missing_resolve = app_client.post("/api/v1/alerts/99999/resolve")
    assert missing_resolve.status_code == 404