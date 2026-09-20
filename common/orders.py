"""Orders sync pipeline (SP-API -> local store). Idempotent and incremental.

Driver design (`sync_orders_driver`) takes a `client_builder` callable so tests
inject a fake client and workers inject the real `SpApiClient`. The per-market
customs (marketplace_id, currency) come from the database.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable

from sqlalchemy import select

from .models import Marketplace, Order, OrderItem, Product, Seller, SpApiCreds


class SyncError(Exception):
    pass


@dataclass
class SyncMarketResult:
    market: str
    orders: int = 0
    items: int = 0
    pages: int = 0
    next_token_seen: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass
class SyncResult:
    markets: list[SyncMarketResult] = field(default_factory=list)

    @property
    def total_orders(self) -> int:
        return sum(m.orders for m in self.markets)

    @property
    def total_items(self) -> int:
        return sum(m.items for m in self.markets)


# ---------------------------------------------------------------------------
# ISO helpers
# ---------------------------------------------------------------------------

def _to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Upserts
# ---------------------------------------------------------------------------

async def upsert_product(session, asin: str, title: str = "") -> int:
    existing = (
        await session.execute(select(Product).where(Product.asin == asin))
    ).scalar_one_or_none()
    if existing:
        if title and title != existing.title:
            existing.title = title
        return existing.id
    product = Product(asin=asin, title=title[:2000] or "")
    session.add(product)
    await session.flush()
    return product.id


async def upsert_order(session, seller_id: int, marketplace_id: int, raw: dict) -> int:
    order_id = raw.get("AmazonOrderId") or ""
    if not order_id:
        raise SyncError("order without AmazonOrderId")
    now = datetime.now(UTC)
    existing = (
        await session.execute(select(Order).where(Order.amazon_order_id == order_id))
    ).scalar_one_or_none()
    totals = raw.get("OrderTotal") or {}
    buyer = raw.get("BuyerInfo") or {}
    ship = (buyer.get("ShippingAddress") or {}) if isinstance(buyer, dict) else {}
    values = {
        "seller_id": seller_id,
        "marketplace_id": marketplace_id,
        "status": raw.get("OrderStatus") or "Pending",
        "purchase_date": _parse_dt(raw.get("PurchaseDate")),
        "latest_delivery_date": _parse_dt(raw.get("LatestDeliveryDate")),
        "buyer_name": (buyer.get("BuyerName") if isinstance(buyer, dict) else "") or "",
        "city": ship.get("City") or "",
        "state_or_region": ship.get("StateOrRegion") or "",
    }
    if existing:
        for k, v in values.items():
            setattr(existing, k, v)
        existing.updated_at = now
        return existing.id
    order = Order(amazon_order_id=order_id, created_at=now, updated_at=now)
    for k, v in values.items():
        setattr(order, k, v)
    session.add(order)
    await session.flush()
    return order.id


async def upsert_order_item(session, order_row_id: int, product_id: int, raw: dict) -> int:
    asin = raw.get("ASIN") or ""
    sku = raw.get("SellerSKU") or ""
    item_price = raw.get("ItemPrice") or {}
    currency = (item_price.get("CurrencyCode") if isinstance(item_price, dict) else "") or "USD"
    existing = (
        await session.execute(
            select(OrderItem).where(
                OrderItem.order_id == order_row_id,
                OrderItem.asin == asin,
                OrderItem.seller_sku == sku,
            )
        )
    ).scalar_one_or_none()
    values = {
        "quantity": int(raw.get("QuantityOrdered") or 0),
        "unit_price": _to_money(item_price.get("Amount")),
        "item_currency": currency,
        "product_id": product_id,
    }
    if existing:
        for k, v in values.items():
            setattr(existing, k, v)
        return existing.id
    item = OrderItem(
        order_id=order_row_id, asin=asin, seller_sku=sku, **values
    )
    session.add(item)
    await session.flush()
    return item.id


def _to_money(value: Any):
    try:
        return round(float(value), 2) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Market-level sync
# ---------------------------------------------------------------------------

async def sync_market(
    session,
    client,
    seller_id: int,
    marketplace_id: int,
    since: datetime,
    statuses: list[str] | None = None,
) -> SyncMarketResult:
    """Pulls orders (+items) for one marketplace, starting at `since`."""
    market = (
        await session.execute(select(Marketplace).where(Marketplace.id == marketplace_id))
    ).scalar_one()
    result = SyncMarketResult(market=market.code)
    last_updated_after = _to_iso(since)

    next_token: str | None = None
    while True:
        orders, next_token = await client.get_orders(
            last_updated_after=last_updated_after,
            statuses=statuses,
            next_token=next_token,
        )
        result.pages += 1
        result.next_token_seen = bool(next_token)
        for raw in orders:
            try:
                order_row_id = await upsert_order(session, seller_id, marketplace_id, raw)
                items = await client.get_order_items(raw.get("AmazonOrderId") or "")
                for it in items:
                    title = it.get("ItemTitle") or ""
                    pid = await upsert_product(session, it.get("ASIN") or "", title)
                    await upsert_order_item(session, order_row_id, pid, it)
                    result.items += 1
                result.orders += 1
            except Exception as exc:  # keep going, surface per-order errors
                result.errors.append(f"{raw.get('AmazonOrderId')}: {exc}")
        if not next_token:
            break
        last_updated_after = _to_iso(since)
    return result


# ---------------------------------------------------------------------------
# Worker-style driver
# ---------------------------------------------------------------------------

ClientBuilder = Callable[[Marketplace], Awaitable[Any]]


async def sync_orders_driver(session, client_builder: ClientBuilder, since: datetime) -> SyncResult:
    """Run orders sync for every seller/market combination in the database.

    `client_builder(marketplace)` must return an object with async
    `get_orders(...)` and `get_order_items(order_id)` methods.
    """
    result = SyncResult()
    sellers = (await session.execute(select(Seller))).scalars().all()
    for seller in sellers:
        creds_row = (
            await session.execute(
                select(SpApiCreds).where(SpApiCreds.seller_id == seller.id).limit(1)
            )
        ).scalar_one_or_none()
        if creds_row is None or creds_row.status != "active":
            continue
        markets = (await session.execute(select(Marketplace).order_by(Marketplace.id))).scalars().all()
        for market in markets:
            client = await client_builder(market)
            market_result = await sync_market(session, client, seller.id, market.id, since)
            result.markets.append(market_result)
    return result


async def default_since(session, job: str, market: str = "", days_back: int = 7) -> datetime:
    from .telemetry import last_ok

    last = await last_ok(session, job, market)
    if last is not None:
        return last
    return datetime.now(UTC) - timedelta(days=days_back)


def json_safe_run(result: SyncResult) -> dict:
    return {
        "markets": [
            {
                "market": m.market,
                "orders": m.orders,
                "items": m.items,
                "pages": m.pages,
                "errors": m.errors[:5],
            }
            for m in result.markets
        ],
        "total_orders": result.total_orders,
        "total_items": result.total_items,
    }


def compact_json(o: Any) -> str:
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))