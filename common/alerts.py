"""Alert rule engine (Phase 4).

Evaluates enabled ``alert_rules`` against collected data and materializes
open ``alerts`` rows. A per-rule cooldown prevents repeated pipeline runs from
spamming the inbox with the same condition.

Kinds:
  price_drop       last snapshot vs previous snapshot fell >= threshold (%)
  stock_out        latest inventory quantity <= threshold (units, default 0)
  margin_erosion   net margin % over the window below threshold (default -10)
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from .models import (
    Alert,
    AlertRule,
    Inventory,
    Marketplace,
    PriceSnapshot,
    Product,
    Settlement,
    SettlementLine,
)

DEFAULT_MARGIN_WINDOW_DAYS = 90

# Default rules created for a seller on first evaluation / first rules fetch.
DEFAULT_RULES: dict[str, dict] = {
    "price_drop": {"threshold": 10.0, "cooldown_hours": 24},
    "stock_out": {"threshold": 0, "cooldown_hours": 6},
    "margin_erosion": {"threshold": -10.0, "cooldown_hours": 168},
}

SEVERITIES: dict[str, str] = {
    "price_drop": "warning",
    "stock_out": "critical",
    "margin_erosion": "warning",
}

# kind -> evaluator(rule, markets) returning a list of created Alert rows.
EVALUATORS: dict[str, str] = {
    "price_drop": "evaluate_price_drop",
    "stock_out": "evaluate_stock_out",
    "margin_erosion": "evaluate_margin_erosion",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _num(v):
    return float(v) if v is not None else 0.0


def _as_utc(v: datetime | None) -> datetime | None:
    return v.replace(tzinfo=UTC) if v is not None and v.tzinfo is None else v


async def ensure_default_rules(session, seller_id: int = 1) -> int:
    """Create default rules for the seller if none exist yet. Returns rules added."""
    existing = {
        k for k in (
            await session.execute(
                select(AlertRule.kind).where(AlertRule.seller_id == seller_id)
            )
        ).scalars()
    }
    added = 0
    for kind, cfg in DEFAULT_RULES.items():
        if kind not in existing:
            session.add(AlertRule(
                seller_id=seller_id,
                kind=kind,
                threshold=cfg["threshold"],
                cooldown_hours=cfg["cooldown_hours"],
                enabled=True,
            ))
            added += 1
    if added:
        await session.flush()
    return added


async def _recent_open(session, *, kind: str, product_id: int | None,
                       marketplace_id: int | None, cooldown_hours: int) -> bool:
    """True if an unresolved alert for the same key was raised within the cooldown."""
    stmt = (
        select(Alert)
        .where(Alert.kind == kind, Alert.resolved_at.is_(None))
    )
    if product_id is not None:
        stmt = stmt.where(Alert.product_id == product_id)
    else:
        stmt = stmt.where(Alert.product_id.is_(None))
    if marketplace_id is not None:
        stmt = stmt.where(Alert.marketplace_id == marketplace_id)
    else:
        stmt = stmt.where(Alert.marketplace_id.is_(None))
    row = (await session.execute(stmt.order_by(Alert.created_at.desc()).limit(1))).first()
    if row is None:
        return False
    created = _as_utc(row[0].created_at)
    return created is not None and created >= _now() - timedelta(hours=cooldown_hours)


async def _raise(session, *, kind: str, severity: str, message: str,
                 meta: dict, product_id: int | None = None,
                 marketplace_id: int | None = None) -> Alert:
    alert = Alert(
        kind=kind,
        severity=severity,
        ref_type="product" if product_id else "",
        ref_id=product_id or 0,
        product_id=product_id,
        marketplace_id=marketplace_id,
        message=message,
        meta=meta,
        seen=False,
    )
    session.add(alert)
    return alert


async def evaluate_price_drop(session, rule: AlertRule, markets: dict) -> list[Alert]:
    """Compare the two most recent snapshots per (product, marketplace)."""
    rows = (
        await session.execute(
            select(
                PriceSnapshot.product_id, PriceSnapshot.marketplace_id,
                PriceSnapshot.price, PriceSnapshot.currency, PriceSnapshot.captured_at,
                Product.asin,
            )
            .join(Product, Product.id == PriceSnapshot.product_id)
            .where(PriceSnapshot.price.isnot(None))
            .order_by(PriceSnapshot.product_id, PriceSnapshot.marketplace_id,
                      PriceSnapshot.captured_at.asc())
        )
    ).all()
    by_key: dict[tuple[int, int], list] = {}
    for r in rows:
        by_key.setdefault((r.product_id, r.marketplace_id), []).append(r)

    created: list[Alert] = []
    for (product_id, market_id), snaps in by_key.items():
        if len(snaps) < 2:
            continue
        prev, last = snaps[-2], snaps[-1]
        prev_price, last_price = _num(prev.price), _num(last.price)
        if prev_price <= 0:
            continue
        drop_pct = (prev_price - last_price) / prev_price * 100.0
        if drop_pct < _num(rule.threshold):
            continue
        if await _recent_open(session, kind=rule.kind, product_id=product_id,
                              marketplace_id=market_id,
                              cooldown_hours=rule.cooldown_hours):
            continue
        market = markets.get(market_id)
        severity = "critical" if drop_pct >= _num(rule.threshold) * 1.5 else SEVERITIES[rule.kind]
        created.append(await _raise(
            session, kind=rule.kind, severity=severity,
            message=("price dropped {drop:.1f}% for {asin} "
                     "({prev:.2f} → {last:.2f} {currency})").format(
                drop=drop_pct, asin=last.asin, prev=prev_price, last=last_price,
                currency=last.currency or ""),
            meta={
                "asin": last.asin, "market": market.code if market else None,
                "prev_price": round(prev_price, 2), "price": round(last_price, 2),
                "currency": last.currency, "drop_pct": round(drop_pct, 1),
            },
            product_id=product_id, marketplace_id=market_id,
        ))
    return created


async def evaluate_stock_out(session, rule: AlertRule, markets: dict) -> list[Alert]:
    """Latest inventory quantity per product (best-effort market via own snapshots)."""
    max_inv = (
        select(Inventory.product_id, func.max(Inventory.snapped_at).label("snapped_at"))
        .group_by(Inventory.product_id)
        .subquery()
    )
    inv = (
        select(Inventory)
        .join(max_inv, (Inventory.product_id == max_inv.c.product_id)
              & (Inventory.snapped_at == max_inv.c.snapped_at))
        .subquery()
    )
    market_latest = (
        select(PriceSnapshot.product_id, func.max(PriceSnapshot.captured_at).label("captured_at"))
        .where(PriceSnapshot.ours.is_(True))
        .group_by(PriceSnapshot.product_id)
        .subquery()
    )
    snap_market = (
        select(PriceSnapshot.product_id, PriceSnapshot.marketplace_id)
        .join(market_latest,
              (PriceSnapshot.product_id == market_latest.c.product_id)
              & (PriceSnapshot.captured_at == market_latest.c.captured_at))
        .subquery()
    )
    rows = (
        await session.execute(
            select(inv.c.product_id, inv.c.quantity, Product.asin, snap_market.c.marketplace_id)
            .join(Product, Product.id == inv.c.product_id)
            .outerjoin(snap_market, snap_market.c.product_id == inv.c.product_id)
        )
    ).all()
    created: list[Alert] = []
    threshold = int(_num(rule.threshold))
    seen_keys: set[tuple[int, int | None]] = set()
    for product_id, quantity, asin, market_id in rows:
        qty = int(quantity or 0)
        if qty > threshold:
            continue
        market_id = int(market_id) if market_id is not None else None
        key = (product_id, market_id)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if await _recent_open(session, kind=rule.kind, product_id=product_id,
                              marketplace_id=market_id,
                              cooldown_hours=rule.cooldown_hours):
            continue
        market = markets.get(market_id or -1)
        created.append(await _raise(
            session, kind=rule.kind, severity=SEVERITIES[rule.kind],
            message=f"stock-out for {asin}: inventory {qty}",
            meta={"asin": asin, "market": market.code if market else None,
                  "quantity": qty, "threshold": threshold},
            product_id=product_id, marketplace_id=market_id,
        ))
    return created


async def evaluate_margin_erosion(session, rule: AlertRule, markets: dict) -> list[Alert]:
    """Margin below threshold over a 90-day window of settlement lines."""
    cutoff = _now() - timedelta(days=DEFAULT_MARGIN_WINDOW_DAYS)
    rows = (
        await session.execute(
            select(
                SettlementLine.product_id,
                Product.asin,
                Settlement.marketplace_id,
                SettlementLine.currency,
                func.sum(SettlementLine.revenue).label("revenue"),
                func.sum(SettlementLine.fees).label("fees"),
                func.sum(SettlementLine.net).label("net"),
            )
            .join(Settlement, Settlement.id == SettlementLine.settlement_id)
            .join(Product, Product.id == SettlementLine.product_id)
            .where(Settlement.posted_date >= cutoff)
            .group_by(SettlementLine.product_id, Product.asin,
                      Settlement.marketplace_id, SettlementLine.currency)
        )
    ).all()
    created: list[Alert] = []
    threshold = float(rule.threshold)
    for product_id, asin, market_id, currency, revenue, fees, net in rows:
        revenue, net = _num(revenue), _num(net)
        if revenue <= 0:
            continue
        margin_pct = net / revenue * 100.0
        if margin_pct >= threshold:
            continue
        market = markets.get(int(market_id) if market_id is not None else -1)
        if await _recent_open(session, kind=rule.kind, product_id=product_id,
                              marketplace_id=int(market_id) if market_id is not None else None,
                              cooldown_hours=rule.cooldown_hours):
            continue
        severity = "critical" if margin_pct <= threshold * 0.5 else SEVERITIES[rule.kind]
        alert = await _raise(
            session, kind=rule.kind, severity=severity,
            message=f"margin erosion for {asin}: {margin_pct:.1f}% (below {threshold:.1f}%)",
            meta={"asin": asin, "market": market.code if market else None,
                  "currency": currency, "revenue": round(revenue, 2),
                  "fees": _num(fees), "net": round(net, 2),
                  "margin_pct": round(margin_pct, 1), "threshold": threshold,
                  "window_days": DEFAULT_MARGIN_WINDOW_DAYS},
            product_id=product_id,
            marketplace_id=int(market_id) if market_id is not None else None,
        )
        if alert is not None:
            created.append(alert)
    return created


async def run_alerts_driver(session, seller_id: int = 1) -> dict:
    """Evaluate every enabled rule; isolate per-kind failures like price_snapshot."""
    await ensure_default_rules(session, seller_id)
    rules = (
        await session.execute(
            select(AlertRule)
            .where(AlertRule.seller_id == seller_id, AlertRule.enabled.is_(True))
            .order_by(AlertRule.kind)
        )
    ).scalars().all()
    markets = {m.id: m for m in (await session.execute(select(Marketplace))).scalars()}

    created: dict[str, int] = {}
    errors: dict[str, str] = {}
    for rule in rules:
        fn_name = EVALUATORS.get(rule.kind)
        # Resolve the actual evaluator function from its declared name.
        fn = globals().get(fn_name) if fn_name else None
        if fn is None:
            continue
        try:
            alerts = await fn(session, rule, markets)
            created[rule.kind] = created.get(rule.kind, 0) + len(alerts)
        except Exception as exc:  # noqa: BLE001 — keep other rules running
            errors[rule.kind] = str(exc)
    await session.flush()
    return {"created": created, "errors": errors, "rules": len(rules), "seller_id": seller_id}