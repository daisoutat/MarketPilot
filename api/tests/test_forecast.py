# -*- coding: utf-8 -*-
"""Phase 6: explainable seasonal-trend demand forecasts + forecast worker."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from common.forecast import (
    compute_forecast,
    daily_demand,
    fit_seasonal_trend,
    forecast_horizon,
)
from common.models import Forecast, Marketplace, Order, OrderItem, Product, Seller
from common.orders import upsert_product


def _dates(n: int, start=None) -> list[date]:
    start = start or date(2026, 1, 1)
    return [start + timedelta(days=i) for i in range(n)]


def test_fit_detects_upward_trend():
    dates = _dates(90)
    values = [10.0 + 0.2 * i for i in range(90)]  # +0.2 units/day
    params = fit_seasonal_trend(dates, values)
    assert params is not None
    assert params["slope"] > 0.1
    assert params["trend_per_week_pct"] > 0
    assert abs(params["level"] - (10.0 + 0.2 * 44.5)) < 2.0


def test_fit_detects_weekend_seasonality():
    dates = _dates(84)
    values = []
    for i, d in enumerate(dates):
        base = 10.0
        if d.weekday() >= 5:  # weekend bump
            base += 5.0
        values.append(base)
    params = fit_seasonal_trend(dates, values)
    assert params is not None
    assert params["seasonal"]["saturday"] > 2.0
    assert params["seasonal"]["monday"] < 1.0


def test_fit_rejects_sparse_series():
    dates = _dates(90)
    values = [1.0] + [0.0] * 89  # only a single order day
    assert fit_seasonal_trend(dates, values) is None


def test_horizon_bounds_and_direction():
    params = fit_seasonal_trend(_dates(90), [5.0 + 0.1 * i for i in range(90)])
    assert params is not None
    fdates, yhat, lo, hi = forecast_horizon(params, _dates(90), horizon=14)
    assert len(fdates) == len(yhat) == len(lo) == len(hi) == 14
    assert all(0 <= lo[i] <= yhat[i] <= hi[i] for i in range(14))
    assert fdates[0] > _dates(90)[-1]  # strictly after history


async def _seed_order(session, market_id, seller_id, product_id, asin, qty, days_ago: int, revenue=99.0, net=40.0):
    from common.models import Settlement, SettlementLine

    order = Order(
        seller_id=seller_id, marketplace_id=market_id, amazon_order_id=f"ORD-{asin}-{days_ago}",
        purchase_date=datetime.now(UTC) - timedelta(days=days_ago), status="Shipped",
    )
    session.add(order)
    await session.flush()
    session.add(OrderItem(
        order_id=order.id, product_id=product_id, asin=asin, seller_sku="SKU",
        quantity=qty, unit_price=revenue, item_currency="USD",
    ))
    settlement = Settlement(seller_id=seller_id, marketplace_id=market_id, posted_date=order.purchase_date,
                            gross=revenue, fees=revenue - net, net=net, currency="USD")
    session.add(settlement)
    await session.flush()
    session.add(SettlementLine(
        settlement_id=settlement.id, product_id=product_id, asin=asin, sku="SKU",
        order_id=order.amazon_order_id, units=qty, revenue=revenue, fees=revenue - net, net=net, currency="USD",
    ))


@pytest.mark.asyncio
async def test_compute_forecast_end_to_end(seeded_session):
    us = (await seeded_session.execute(select(Marketplace).where(Marketplace.code == "US"))).scalar_one()
    seller = Seller(name="Demo", credentials_ref="env")
    seeded_session.add(seller)
    await seeded_session.commit()

    pid = await upsert_product(seeded_session, "B0TREND1", "Trending Widget")
    for i in range(35):  # 35 days of growing demand
        await _seed_order(seeded_session, us.id, seller.id, pid, "B0TREND1", qty=2 + i // 5, days_ago=i)
    await seeded_session.commit()

    forecast = await compute_forecast(seeded_session, pid, window_days=90, horizon=30)
    assert forecast is not None
    assert forecast["horizon"] == 30
    assert len(forecast["points"]["dates"]) == 30
    assert forecast["params"]["slope"] > 0
    assert len(forecast["history"]["dates"]) == 90  # aligned 90-day window

    dates, values = await daily_demand(seeded_session, pid, days=90)
    assert len(dates) == 90 and len(values) == 90


@pytest.mark.asyncio
async def test_compute_forecast_skips_sparse(seeded_session):
    us = (await seeded_session.execute(select(Marketplace).where(Marketplace.code == "US"))).scalar_one()
    seller = Seller(name="Demo", credentials_ref="env")
    seeded_session.add(seller)
    await seeded_session.commit()
    pid = await upsert_product(seeded_session, "B0SOLO1", "One-off")
    await _seed_order(seeded_session, us.id, seller.id, pid, "B0SOLO1", qty=1, days_ago=1)
    await seeded_session.commit()
    assert await compute_forecast(seeded_session, pid) is None


@pytest.mark.asyncio
async def test_forecast_worker_run(monkeypatch):
    """forecast.run() fits real products and records/prunes Forecast rows."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    from common.models import Base, Marketplace, Seller

    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        us = Marketplace(code="US", marketplace_id="ATVPDKIKX0DER", currency="USD", iso="en-US")
        ca = Marketplace(code="CA", marketplace_id="A2VIGQ35RCS4UG", currency="CAD", iso="fr-CA")
        session.add_all([us, ca])
        seller = Seller(name="Demo", credentials_ref="env")
        session.add(seller)
        await session.commit()
        pid = await upsert_product(session, "B0WRKR1", "Worker Headphones")
        for i in range(30):
            await _seed_order(session, us.id, seller.id, pid, "B0WRKR1", qty=3, days_ago=i)
        await session.commit()

    from workers.funcs import forecast as forecast_worker

    monkeypatch.setattr(forecast_worker, "init_db", lambda: engine)

    result = await forecast_worker.run({})

    assert result["status"] == "ok"
    assert result["fitted"] >= 1
    assert result["skipped"] == 0

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        rows = (await session.execute(select(Forecast))).scalars().all()
        assert len(rows) >= 1
        assert rows[0].params["model"] == "seasonal-ols"
        assert len(rows[0].points["yhat"]) == 30


def test_forecast_horizon_gap_dates():
    params = {"n": 30, "level": 5.0, "slope": 0.0,
              "seasonal": {"monday": 0, "tuesday": 0, "wednesday": 0, "thursday": 0,
                           "friday": 0, "saturday": 0, "sunday": 0}, "rmse": 1.0}
    fdates, _, _, _ = forecast_horizon(params, _dates(30), horizon=7)
    assert not set(fdates) & set(_dates(30))