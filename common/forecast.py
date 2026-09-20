"""Explainable demand forecasting (Phase 6).

Model: additive decomposition — **weekly seasonality + linear (OLS) trend** —
fitted on the product's own daily unit sales (order_items). Fully dependency-
free and explainable:

* every horizon point = ``level + slope·time + weekday_factor``,
* ``params.seasonal`` lists the 7 weekday offsets (why Tuesday is up/down),
* ``params.trend_per_week_pct`` is the demand trajectory,
* ``params.rmse`` drives the 80 % band (yhat ± 1.28·rmse, floored at 0).

Products with too little history are skipped so we never publish noise.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select

from .models import Forecast, Order, OrderItem, Product

MIN_TOTAL_UNITS = 5
MIN_ACTIVE_DAYS = 4
MIN_WINDOW_DAYS = 21
MAX_WINDOW_DAYS = 90
DEFAULT_HORIZON = 30
CONFIDENCE_Z = 1.28  # 80% interval

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return (sum((v - m) ** 2 for v in values) / (len(values) - 1)) ** 0.5


def fit_seasonal_trend(dates: list[date], values: list[float]) -> dict | None:
    """Fit level + OLS slope + additive weekday factors on aligned daily data.

    Returns the explainable parameter dict, or None when there isn't enough
    demand signal to say anything meaningful.
    """
    if len(dates) != len(values) or len(dates) < MIN_WINDOW_DAYS:
        return None
    total = sum(values)
    active = sum(1 for v in values if v > 0)
    if total < MIN_TOTAL_UNITS or active < MIN_ACTIVE_DAYS:
        return None

    n = len(values)
    t_mid = (n - 1) / 2.0
    t = [i - t_mid for i in range(n)]
    t_mean = _mean(t)
    y_mean = _mean(values)
    denom = sum((ti - t_mean) ** 2 for ti in t)
    slope = (sum((ti - t_mean) * (yi - y_mean) for ti, yi in zip(t, values)) / denom) if denom else 0.0

    seasonal = {wk: 0.0 for wk in WEEKDAYS}
    residuals: list[float] = []
    for i, (d, yi) in enumerate(zip(dates, values)):
        base = y_mean + slope * t[i]
        seasonal[WEEKDAYS[d.weekday()]] += (yi - base)
        residuals.append(yi - base)
    for wk in WEEKDAYS:
        counts = sum(1 for d in dates if WEEKDAYS[d.weekday()] == wk)
        seasonal[wk] = round(seasonal[wk] / counts, 3) if counts else 0.0

    rmse = _std(residuals)
    mean_per_day = y_mean
    volatility = (_std(values) / mean_per_day) if mean_per_day > 0 else 0.0
    # slope is per day in units; express as %/week of current level.
    trend_pct_per_week = (slope * 7.0 / mean_per_day * 100.0) if mean_per_day > 0 else 0.0

    return {
        "model": "seasonal-ols",
        "n": n,
        "level": round(y_mean, 3),
        "slope": round(slope, 4),
        "trend_per_week_pct": round(trend_pct_per_week, 2),
        "seasonal": {wk: seasonal[wk] for wk in WEEKDAYS},
        "rmse": round(rmse, 3),
        "volatility": round(volatility, 3),
        "mean_per_day": round(mean_per_day, 3),
        "active_days": active,
    }


def forecast_horizon(
    params: dict,
    history_dates: list[date],
    horizon: int = DEFAULT_HORIZON,
) -> tuple[list[date], list[float], list[float], list[float]]:
    """Extend the fitted model horizon days past the last history date."""
    last = history_dates[-1]
    n = params["n"]
    t_mid = (n - 1) / 2.0
    lo: list[float] = []
    hi: list[float] = []
    yhat: list[float] = []
    dates: list[date] = []
    for h in range(1, horizon + 1):
        d = last + timedelta(days=h)
        base = params["level"] + params["slope"] * ((n - 1) + h - t_mid) + params["seasonal"][WEEKDAYS[d.weekday()]]
        y = max(0.0, base)
        band = CONFIDENCE_Z * params["rmse"]
        dates.append(d)
        yhat.append(round(y, 2))
        lo.append(round(max(0.0, y - band), 2))
        hi.append(round(y + band, 2))
    return dates, yhat, lo, hi


async def daily_demand(session, product_id: int, days: int) -> tuple[list[date], list[float]]:
    """Aligned daily unit demand (0-filled) for the last `days` calendar days."""
    today = datetime.now(UTC).date()
    start = today - timedelta(days=days - 1)
    rows = (
        await session.execute(
            select(
                func.date(Order.purchase_date).label("day"),
                func.coalesce(func.sum(OrderItem.quantity), 0).label("units"),
            )
            .join(OrderItem, OrderItem.order_id == Order.id)
            .where(
                OrderItem.product_id == product_id,
                Order.purchase_date >= start,
                Order.purchase_date <= today + timedelta(days=1),
            )
            .group_by(func.date(Order.purchase_date))
        )
    ).all()
    def _as_date(v):
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, date):
            return v
        return date.fromisoformat(str(v))

    counts = {_as_date(row.day): int(row.units or 0) for row in rows if row.day is not None}
    dates: list[date] = []
    values: list[float] = []
    for offset in range(days):
        d = start + timedelta(days=offset)
        dates.append(d)
        values.append(float(counts.get(d, 0)))
    return dates, values


async def compute_forecast(
    session,
    product_id: int,
    window_days: int = MAX_WINDOW_DAYS,
    horizon: int = DEFAULT_HORIZON,
) -> dict | None:
    """Fit + extend the demand model for one product; None when data is sparse."""
    window_days = max(MIN_WINDOW_DAYS, min(window_days, MAX_WINDOW_DAYS))
    dates, values = await daily_demand(session, product_id, window_days)
    params = fit_seasonal_trend(dates, values)
    if params is None:
        return None
    fdates, yhat, lo, hi = forecast_horizon(params, dates, horizon)
    return {
        "product_id": product_id,
        "model": params["model"],
        "unit": "units/day",
        "window_days": window_days,
        "horizon": horizon,
        "params": params,
        "history": {"dates": [d.isoformat() for d in dates], "values": values},
        "points": {
            "dates": [d.isoformat() for d in fdates],
            "yhat": yhat,
            "lo": lo,
            "hi": hi,
        },
    }


async def predictive_insights(
    session,
    limit: int = 6,
) -> dict:
    """Rank products from the latest stored forecasts.

    Returns ``gainers`` / ``decliners`` (by weekly trend) and ``top_next_30d``
    (by expected units in the horizon) so the assistant and the SPA can answer
    "what should I focus on next?" from explainable, already-computed numbers —
    no model re-fit at request time.
    """
    rows = (
        await session.execute(
            select(Forecast, Product.asin, Product.title, Product.brand)
            .join(Product, Product.id == Forecast.product_id)
            .order_by(Forecast.generated_at.desc())
            .limit(4000)
        )
    ).all()
    latest: dict[int, tuple] = {}
    for f, asin, title, brand in rows:  # first per product wins (desc by generated_at)
        latest.setdefault(f.product_id, (f, asin, title, brand))

    items: list[dict] = []
    for pid, (f, asin, title, brand) in latest.items():
        params = f.params or {}
        yhat = (f.points or {}).get("yhat") or []
        trend = params.get("trend_per_week_pct")
        if trend is None and not yhat:
            continue
        items.append({
            "product_id": pid,
            "asin": asin,
            "title": title,
            "brand": brand or "",
            "mean_per_day": params.get("mean_per_day"),
            "trend_per_week_pct": trend,
            "volatility": params.get("volatility"),
            "rmse": params.get("rmse"),
            "next_30d": round(sum(float(v) for v in yhat), 1),
            "yhat": [round(float(v), 1) for v in yhat],
        })

    def _rank(key: str, reverse: bool = True) -> list[dict]:
        present = [i for i in items if i[key] is not None]
        present.sort(key=lambda x: x[key], reverse=reverse)
        return present[:limit]

    gainers = [i for i in _rank("trend_per_week_pct", True) if i["trend_per_week_pct"] > 0][:limit]
    decliners = [i for i in _rank("trend_per_week_pct", False) if i["trend_per_week_pct"] < 0][:limit]
    top_next_30d = _rank("next_30d", True)

    return {
        "count": len(items),
        "generated_at": max((f.generated_at for f, *_ in latest.values()), default=None),
        "gainers": gainers,
        "decliners": decliners,
        "top_next_30d": top_next_30d,
    }