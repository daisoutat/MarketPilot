# -*- coding: utf-8 -*-
"""Tests for the orders sync pipeline (idempotent, incremental)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from common.models import Marketplace, Order, OrderItem, Product, Seller, SpApiCreds
from common.orders import (
    default_since,
    sync_market,
    sync_orders_driver,
    upsert_order,
    upsert_order_item,
    upsert_product,
)
from common import telemetry


class FakeSpClient:
    """Two pages: [2 orders] then [1 order]; each with 1 item."""

    def __init__(self):
        self.calls = 0
        self.item_calls = 0

    async def get_orders(self, last_updated_after=None, statuses=None, next_token=None, max_results=50):
        self.calls += 1
        if next_token:
            return [self._order("333")], None
        return [self._order("111"), self._order("222")], "page2"

    async def get_order_items(self, order_id):
        self.item_calls += 1
        return [self._item(order_id)]

    @staticmethod
    def _order(oid):
        return {
            "AmazonOrderId": oid,
            "PurchaseDate": "2026-09-15T10:00:00Z",
            "LatestDeliveryDate": "2026-09-20T10:00:00Z",
            "OrderStatus": "Shipped",
            "BuyerInfo": {"BuyerName": f"Buyer {oid}", "ShippingAddress": {"City": "Montreal", "StateOrRegion": "QC"}},
        }

    @staticmethod
    def _item(order_id):
        return {
            "ASIN": "B0EXAMPLE1", "SellerSKU": "SKU-1",
            "QuantityOrdered": 3, "ItemTitle": "Widget",
            "ItemPrice": {"Amount": "12.50", "CurrencyCode": "USD"},
        }


async def _seed_parts(session):
    market = Marketplace(code="US", marketplace_id="ATVPDKIKX0DER", currency="USD", iso="en-US")
    seller = Seller(name="Demo", credentials_ref="env:SP_API")
    session.add_all([market, seller])
    await session.flush()
    creds = SpApiCreds(seller_id=seller.id, auth_model="key", client_id_arn="env:SP_API", status="active")
    session.add(creds)
    await session.commit()
    return seller, market


@pytest.mark.asyncio
async def test_sync_market_upserts_and_is_idempotent(session_factory):
    async with session_factory() as session:
        seller, market = await _seed_parts(session)
        client = FakeSpClient()

        res1 = await sync_market(session, client, seller.id, market.id, datetime.now(UTC) - timedelta(days=7))
        await session.commit()
        assert res1.orders == 3 and res1.items == 3
        assert len(res1.errors) == 0

        res2 = await sync_market(session, client, seller.id, market.id, datetime.now(UTC) - timedelta(days=7))
        await session.commit()
        assert res2.orders == 3 and res2.items == 3  # re-run does not duplicate

        n_orders = (await session.execute(select(func.count()).select_from(Order))).scalar()
        assert int(n_orders) == 3
        n_items = (await session.execute(select(func.count()).select_from(OrderItem))).scalar()
        assert int(n_items) == 3
        product = (await session.execute(select(Product).where(Product.asin == "B0EXAMPLE1"))).scalar_one()
        assert product.title == "Widget"


@pytest.mark.asyncio
async def test_driver_runs_each_market(session_factory):
    async with session_factory() as session:
        seller, market_us = await _seed_parts(session)
        market_ca = Marketplace(code="CA", marketplace_id="A2VIGQ35RCS4UG", currency="CAD", iso="fr-CA")
        session.add(market_ca)
        await session.commit()

        async def builder(market: Marketplace):
            assert market.code in ("US", "CA")
            return FakeSpClient()

        result = await sync_orders_driver(session, builder, datetime.now(UTC) - timedelta(days=7))
        await session.commit()
        assert result.total_orders == 6  # 3 per market
        assert result.total_items == 6
        assert [m.market for m in result.markets] == ["US", "CA"]


@pytest.mark.asyncio
async def test_upsert_helpers(session_factory):
    async with session_factory() as session:
        product_id = await upsert_product(session, "B0NONE1", "Title A")
        product_id_2 = await upsert_product(session, "B0NONE1", "Title B")  # same asin -> id unchanged, title updated
        assert product_id == product_id_2
        market = Marketplace(code="CA", marketplace_id="A2VIGQ35RCS4UG", currency="CAD", iso="fr-CA")
        session.add(market)
        await session.flush()
        oid = await upsert_order(session, 1, market.id, {
            "AmazonOrderId": "ORD-1", "OrderStatus": "Pending",
            "BuyerInfo": {"BuyerName": "A", "ShippingAddress": {"City": "Toronto"}},
        })
        oid2 = await upsert_order(session, 1, market.id, {
            "AmazonOrderId": "ORD-1", "OrderStatus": "Shipped",
        })
        assert oid == oid2
        iid = await upsert_order_item(session, oid, product_id, {
            "ASIN": "B0NONE1", "SellerSKU": "S1", "QuantityOrdered": "2",
            "ItemPrice": {"Amount": "9.90", "CurrencyCode": "CAD"},
        })
        iid2 = await upsert_order_item(session, oid, product_id, {
            "ASIN": "B0NONE1", "SellerSKU": "S1", "QuantityOrdered": "4",
        })
        assert iid == iid2
        row = (await session.execute(select(OrderItem).where(OrderItem.id == iid))).scalar_one()
        assert row.quantity == 4


@pytest.mark.asyncio
async def test_telemetry_and_default_since(session_factory):
    async with session_factory() as session:
        since = await default_since(session, "orders-sync", "US", days_back=7)
        assert since is not None  # no prior runs -> backfill window
        run = await telemetry.start_run(session, "orders-sync", "US")
        await telemetry.finish_run(session, run, rows=5)
        await session.commit()
        last = await telemetry.last_ok(session, "orders-sync", "US")
        assert last is not None
        since2 = await default_since(session, "orders-sync", "US")
        assert since2 == last