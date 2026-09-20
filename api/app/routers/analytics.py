"""Phase 6 analytics endpoints (multidimensional + forecasting).

  GET /api/v1/analytics/tree            category → niche → family rollups
  GET /api/v1/analytics/segment/{family_id}   family → ASIN drill with KPIs
  GET /api/v1/analytics/forecast        latest explainable forecasts (list)
  GET /api/v1/analytics/forecast/{product_id} full explanation (series + params)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.analytics import build_tree, segment_products
from common.db import get_session
from common.models import Forecast, Product
from ..auth import get_current_user

router = APIRouter()

FORECAST_UNIT = "units/day"


@router.get("/analytics/tree")
async def analytics_tree(
    days: int = Query(90, ge=1, le=365),
    market: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict:
    nodes = await build_tree(session, days=days, market_code=market)
    return {"window_days": days, "market": market, "count": len(nodes), "nodes": nodes}


@router.get("/analytics/segment/{family_id}")
async def analytics_segment(
    family_id: int,
    days: int = Query(90, ge=1, le=365),
    market: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict:
    return await segment_products(session, family_id, days=days, market_code=market, limit=limit)


@router.get("/analytics/forecast")
async def analytics_forecasts(
    days: int = Query(90, ge=1, le=365),
    market: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict:
    """Latest forecast per product with one-line explanation + next-30d total."""
    stmt = (
        select(Forecast, Product.asin, Product.title, Product.brand)
        .join(Product, Product.id == Forecast.product_id)
        .order_by(Forecast.generated_at.desc())
        .limit(2000)
    )
    rows = (await session.execute(stmt)).all()

    latest: dict[int, tuple] = {}
    for f, asin, title, brand in rows:  # first per product wins (desc by generated_at)
        latest.setdefault((f.product_id, asin), (f, asin, title, brand))

    out = []
    for f, asin, title, brand in list(latest.values())[:limit]:
        params = f.params or {}
        yhat = (f.points or {}).get("yhat") or []
        horizon_total = round(sum(yhat), 1)
        out.append({
            "product_id": f.product_id,
            "asin": asin,
            "title": title,
            "brand": brand or "",
            "generated_at": f.generated_at.isoformat() if f.generated_at else None,
            "unit": f.unit or FORECAST_UNIT,
            "window_days": f.window_days,
            "horizon": f.horizon,
            "mean_per_day": params.get("mean_per_day"),
            "trend_per_week_pct": params.get("trend_per_week_pct"),
            "volatility": params.get("volatility"),
            "rmse": params.get("rmse"),
            "horizon_total": horizon_total,
        })
    return {"count": len(rows), "unit": FORECAST_UNIT, "rows": out}


@router.get("/analytics/forecast/{product_id}")
async def analytics_forecast_detail(
    product_id: int,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict:
    latest = (
        await session.execute(
            select(Forecast)
            .where(Forecast.product_id == product_id)
            .order_by(Forecast.generated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    product = (
        await session.execute(select(Product).where(Product.id == product_id))
    ).scalar_one_or_none()
    if latest is None or product is None:
        return {"exists": False, "product_id": product_id}
    return {
        "exists": True,
        "product": {
            "id": product.id,
            "asin": product.asin,
            "title": product.title,
            "brand": product.brand or "",
        },
        "model": latest.model,
        "unit": latest.unit or FORECAST_UNIT,
        "window_days": latest.window_days,
        "horizon": latest.horizon,
        "generated_at": latest.generated_at.isoformat() if latest.generated_at else None,
        "params": latest.params,
        "history": latest.history,
        "points": latest.points,
    }