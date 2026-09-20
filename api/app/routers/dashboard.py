"""Dashboard endpoints (Phase 1): KPIs, series, orders feed, sync status."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from common.db import get_session
from common.fx import price_in_usd
from common.models import Inventory, Marketplace, Order, OrderItem, PipelineRun, PriceSnapshot, Product, ResearchTarget, Settlement, SettlementLine
from ..auth import get_current_user

router = APIRouter()


def _num(v):
    return float(v) if v is not None else 0.0


@router.get("/dashboard/summary")
async def dashboard_summary(
    days: int = Query(30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict:
    cutoff = datetime.now(UTC) - timedelta(days=days)

    market_rows = (
        await session.execute(
            select(
                Marketplace.code,
                func.count(Order.id).label("orders"),
                func.coalesce(func.sum(OrderItem.quantity), 0).label("units"),
                func.coalesce(func.sum(OrderItem.unit_price * OrderItem.quantity), 0).label("gross"),
            )
            .join(Order, Order.marketplace_id == Marketplace.id)
            .outerjoin(OrderItem, OrderItem.order_id == Order.id)
            .where(Order.purchase_date >= cutoff)
            .group_by(Marketplace.id, Marketplace.code)
        )
    ).all()

    status_rows = (
        await session.execute(
            select(Order.status, func.count(Order.id))
            .where(Order.purchase_date >= cutoff)
            .group_by(Order.status)
        )
    ).all()

    markets = [
        {
            "market": r.code,
            "orders": int(r.orders or 0),
            "units": int(r.units or 0),
            "gross": _num(r.gross),
        }
        for r in market_rows
    ]
    total = {
        "orders": sum(m["orders"] for m in markets),
        "units": sum(m["units"] for m in markets),
        "gross": round(sum(m["gross"] for m in markets), 2),
    }
    return {
        "window_days": days,
        "total": total,
        "by_market": markets,
        "by_status": [{"status": s, "count": int(c)} for s, c in status_rows],
    }


@router.get("/dashboard/series")
async def dashboard_series(
    days: int = Query(30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict:
    cutoff = datetime.now(UTC).date() - timedelta(days=days - 1)
    day = func.date(Order.purchase_date).label("day")
    rows = (
        await session.execute(
            select(
                day,
                func.count(Order.id).label("orders"),
                func.coalesce(func.sum(OrderItem.quantity), 0).label("units"),
                func.coalesce(func.sum(OrderItem.unit_price * OrderItem.quantity), 0).label("gross"),
            )
            .outerjoin(OrderItem, OrderItem.order_id == Order.id)
            .where(Order.purchase_date >= cutoff)
            .group_by(day)
            .order_by(day)
        )
    ).all()
    return {
        "days": days,
        "points": [
            {"date": r.day.isoformat() if hasattr(r.day, "isoformat") else str(r.day),
             "orders": int(r.orders or 0), "units": int(r.units or 0),
             "gross": _num(r.gross)}
            for r in rows
        ],
    }


@router.get("/dashboard/orders")
async def dashboard_orders(
    market: str | None = Query(None),
    status: str | None = Query(None),
    q: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict:
    units_sq = (
        select(func.coalesce(func.sum(OrderItem.quantity), 0))
        .where(OrderItem.order_id == Order.id)
        .scalar_subquery()
        .label("units")
    )
    gross_sq = (
        select(func.coalesce(func.sum(OrderItem.unit_price * OrderItem.quantity), 0))
        .where(OrderItem.order_id == Order.id)
        .scalar_subquery()
        .label("gross")
    )
    stmt = (
        select(
            Order.id, Order.amazon_order_id, Order.status, Order.purchase_date,
            Order.city, Order.state_or_region, Order.buyer_name,
            Marketplace.code.label("market"), units_sq, gross_sq,
        )
        .join(Marketplace, Marketplace.id == Order.marketplace_id)
    )
    if market:
        stmt = stmt.where(Marketplace.code == market.upper())
    if status:
        stmt = stmt.where(Order.status == status)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Order.amazon_order_id.like(like) | Order.buyer_name.like(like))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    stmt = stmt.order_by(func.coalesce(Order.purchase_date, Order.created_at).desc()).limit(limit).offset(offset)
    rows = (await session.execute(stmt)).all()

    def iso(v):
        return v.isoformat() if v is not None else None

    return {
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "orders": [
            {
                "id": r.id, "amazon_order_id": r.amazon_order_id, "market": r.market,
                "status": r.status, "purchase_date": iso(r.purchase_date),
                "city": r.city, "state_or_region": r.state_or_region,
                "buyer_name": r.buyer_name, "units": int(r.units or 0),
                "gross": _num(r.gross),
            }
            for r in rows
        ],
    }


@router.get("/dashboard/inventory")
async def dashboard_inventory(
    market: str | None = Query(None),
    q: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict:
    """Latest listed price snapshot + inventory quantity per product."""
    max_snap = (
        select(
            PriceSnapshot.product_id,
            PriceSnapshot.marketplace_id,
            func.max(PriceSnapshot.captured_at).label("captured_at"),
        )
        .where(PriceSnapshot.ours.is_(True))
        .group_by(PriceSnapshot.product_id, PriceSnapshot.marketplace_id)
        .subquery()
    )
    snap = (
        select(PriceSnapshot)
        .join(
            max_snap,
            (PriceSnapshot.product_id == max_snap.c.product_id)
            & (PriceSnapshot.marketplace_id == max_snap.c.marketplace_id)
            & (PriceSnapshot.captured_at == max_snap.c.captured_at),
        )
        .subquery()
    )
    max_inv = (
        select(Inventory.product_id, func.max(Inventory.snapped_at).label("snapped_at"))
        .group_by(Inventory.product_id)
        .subquery()
    )
    inv = (
        select(Inventory)
        .join(max_inv, (Inventory.product_id == max_inv.c.product_id) & (Inventory.snapped_at == max_inv.c.snapped_at))
        .subquery()
    )
    stmt = (
        select(
            Product.asin, Product.title, Product.brand,
            Marketplace.code.label("market"), snap.c.price, snap.c.currency,
            snap.c.captured_at, inv.c.quantity,
        )
        .join(snap, snap.c.product_id == Product.id)
        .join(Marketplace, Marketplace.id == snap.c.marketplace_id)
        .outerjoin(inv, inv.c.product_id == Product.id)
    )
    if market:
        stmt = stmt.where(Marketplace.code == market.upper())
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Product.title.like(like) | Product.asin.like(like) | Product.brand.like(like))
    rows = (await session.execute(stmt.order_by(Product.title).limit(limit))).all()

    def iso(v):
        return v.isoformat() if v is not None else None

    return {
        "limit": limit,
        "rows": [
            {
                "asin": r.asin, "title": r.title, "brand": r.brand or "",
                "market": r.market, "price": _num(r.price), "currency": r.currency,
                "quantity": int(r.quantity or 0),
                "captured_at": iso(r.captured_at),
            }
            for r in rows
        ],
    }


@router.get("/dashboard/margin")
async def dashboard_margin(
    days: int = Query(90, ge=1, le=365),
    market: str | None = Query(None),
    min_units: int = Query(0, ge=0),
    q: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict:
    """Per-product margin from settlement lines (revenue, fees, net %, units)."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    stmt = (
        select(
            Product.asin, Product.title, Marketplace.code.label("market"),
            SettlementLine.currency,
            func.sum(SettlementLine.units).label("units"),
            func.sum(SettlementLine.revenue).label("revenue"),
            func.sum(SettlementLine.fees).label("fees"),
            func.sum(SettlementLine.net).label("net"),
        )
        .join(Settlement, Settlement.id == SettlementLine.settlement_id)
        .join(Marketplace, Marketplace.id == Settlement.marketplace_id)
        .join(Product, Product.id == SettlementLine.product_id)
        .where(Settlement.posted_date >= cutoff)
        .group_by(Product.asin, Product.title, Marketplace.code, SettlementLine.currency)
    )
    if market:
        stmt = stmt.where(Marketplace.code == market.upper())
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Product.title.like(like) | Product.asin.like(like))
    rows = (await session.execute(stmt)).all()

    def money(v):
        return round(float(v), 2) if v is not None else 0.0

    out = []
    for r in rows:
        if int(r.units or 0) < min_units:
            continue
        revenue = money(r.revenue)
        net = money(r.net)
        out.append({
            "asin": r.asin, "title": r.title, "market": r.market,
            "currency": r.currency, "units": int(r.units or 0),
            "revenue": revenue, "fees": money(r.fees), "net": net,
            "margin_pct": round(net / revenue * 100, 1) if revenue else None,
            "avg_price": round(revenue / r.units, 2) if (r.units and revenue) else None,
        })
    out.sort(key=lambda x: (x["net"] is not None, x["net"]), reverse=True)
    return {"window_days": days, "limit": limit, "rows": out[:limit]}


@router.get("/dashboard/research")
async def dashboard_research(
    market: str | None = Query(None),
    q: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict:
    """Latest snapshot per research target (PA-API), USD-normalized."""
    max_snap = (
        select(
            PriceSnapshot.product_id,
            PriceSnapshot.marketplace_id,
            func.max(PriceSnapshot.captured_at).label("captured_at"),
        )
        .where(PriceSnapshot.ours.is_(False))
        .group_by(PriceSnapshot.product_id, PriceSnapshot.marketplace_id)
        .subquery()
    )
    snap = (
        select(PriceSnapshot)
        .join(
            max_snap,
            (PriceSnapshot.product_id == max_snap.c.product_id)
            & (PriceSnapshot.marketplace_id == max_snap.c.marketplace_id)
            & (PriceSnapshot.captured_at == max_snap.c.captured_at),
        )
        .subquery()
    )
    stmt = (
        select(
            Product.asin, Product.title, Marketplace.code.label("market"),
            snap.c.price, snap.c.currency, snap.c.buybox, snap.c.sales_rank,
            snap.c.captured_at, ResearchTarget.keywords, ResearchTarget.last_snap_at,
        )
        .join(snap, snap.c.product_id == Product.id)
        .join(Marketplace, Marketplace.id == snap.c.marketplace_id)
        .join(ResearchTarget, (ResearchTarget.asin == Product.asin) & (ResearchTarget.marketplace_id == snap.c.marketplace_id))
        .where(ResearchTarget.active.is_(True))
    )
    if market:
        stmt = stmt.where(Marketplace.code == market.upper())
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Product.title.like(like) | Product.asin.like(like))
    rows = (await session.execute(stmt.order_by(Product.title).limit(limit))).all()

    def iso(v):
        return v.isoformat() if v is not None else None

    out = []
    for r in rows:
        price_usd = await price_in_usd(session, r.price, r.currency)
        out.append({
            "asin": r.asin, "title": r.title, "market": r.market,
            "keywords": r.keywords or "", "price": _num(r.price),
            "currency": r.currency, "price_usd": price_usd,
            "buybox": _num(r.buybox) if r.buybox else None,
            "sales_rank": r.sales_rank,
            "captured_at": iso(r.captured_at), "last_snap_at": iso(r.last_snap_at),
        })
    return {"limit": limit, "count": len(out), "rows": out}


@router.get("/dashboard/compare")
async def dashboard_compare(
    market: str | None = Query(None),
    asin: str | None = Query(None),
    q: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict:
    """Latest per-market snapshots, USD-normalized, side by side per ASIN."""
    max_snap = (
        select(
            PriceSnapshot.product_id,
            PriceSnapshot.marketplace_id,
            func.max(PriceSnapshot.captured_at).label("captured_at"),
        )
        .group_by(PriceSnapshot.product_id, PriceSnapshot.marketplace_id)
        .subquery()
    )
    snap = (
        select(PriceSnapshot)
        .join(
            max_snap,
            (PriceSnapshot.product_id == max_snap.c.product_id)
            & (PriceSnapshot.marketplace_id == max_snap.c.marketplace_id)
            & (PriceSnapshot.captured_at == max_snap.c.captured_at),
        )
        .subquery()
    )
    stmt = (
        select(
            Product.asin, Product.title, Product.brand,
            Marketplace.code.label("market"), snap.c.price, snap.c.currency,
            snap.c.buybox, snap.c.sales_rank, snap.c.captured_at,
        )
        .join(snap, snap.c.product_id == Product.id)
        .join(Marketplace, Marketplace.id == snap.c.marketplace_id)
    )
    if market:
        stmt = stmt.where(Marketplace.code == market.upper())
    if asin:
        stmt = stmt.where(Product.asin == asin.strip().upper())
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Product.title.like(like) | Product.asin.like(like) | Product.brand.like(like))
    rows = (await session.execute(stmt.order_by(Product.title, Marketplace.code).limit(limit))).all()

    def iso(v):
        return v.isoformat() if v is not None else None

    by_asin: dict[str, dict] = {}
    for r in rows:
        price_usd = await price_in_usd(session, r.price, r.currency)
        entry = by_asin.setdefault(r.asin, {
            "asin": r.asin, "title": r.title, "brand": r.brand or "",
            "markets": [], "gap_usd": None,
        })
        entry["markets"].append({
            "market": r.market, "price": _num(r.price), "currency": r.currency,
            "price_usd": price_usd, "buybox": _num(r.buybox) if r.buybox else None,
            "sales_rank": r.sales_rank, "captured_at": iso(r.captured_at),
        })

    for entry in by_asin.values():
        usd = {m["market"]: m["price_usd"] for m in entry["markets"] if m["price_usd"]}
        if "US" in usd and "CA" in usd:
            entry["gap_usd"] = round(usd["US"] - usd["CA"], 2)
        entry["markets"].sort(key=lambda m: m["market"])
    return {"count": len(by_asin), "rows": list(by_asin.values())}


@router.get("/dashboard/sync")
async def dashboard_sync(
    limit: int = Query(12, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict:
    rows = (
        await session.execute(
            select(
                PipelineRun.job, PipelineRun.marketplace_code, PipelineRun.status,
                PipelineRun.started, PipelineRun.finished, PipelineRun.rows, PipelineRun.error,
            )
            .order_by(PipelineRun.started.desc())
            .limit(limit)
        )
    ).all()
    return {
        "runs": [
            {
                "job": r.job, "market": r.marketplace_code, "status": r.status,
                "started": r.started.isoformat() if r.started else None,
                "finished": r.finished.isoformat() if r.finished else None,
                "rows": int(r.rows or 0), "error": r.error or "",
            }
            for r in rows
        ]
    }


@router.get("/dashboard/marketplaces")
async def dashboard_marketplaces(
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict:
    rows = (await session.execute(select(Marketplace).order_by(Marketplace.id))).scalars().all()
    return {
        "marketplaces": [
            {"code": m.code, "marketplace_id": m.marketplace_id, "currency": m.currency, "iso": m.iso}
            for m in rows
        ]
    }


@router.get("/dashboard/healthz")
async def dashboard_healthz(session: AsyncSession = Depends(get_session)) -> dict:
    try:
        await session.execute(select(func.count()).select_from(Marketplace))
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}")