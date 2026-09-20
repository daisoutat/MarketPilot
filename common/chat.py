"""Dashboard assistant (Phase 7) — explainable, bilingual, data-grounded.

No external LLM: the assistant is a small, deterministic NLU over the LIVE
database. Intents map to the same queries the rest of the app uses
(summary, orders, margin, inventory, alerts, cross-market FX, category
rollups, and the Phase 6 seasonal-OLS forecasts), so every answer is
verifiable by the existing screens.

Every reply is ``{intent, text, data}``: `text` is the natural-language
answer (in the request language), `data` is an optional structured payload
(kpis / rows / chart series) the SPA renders inline.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from .analytics import build_tree
from .forecast import predictive_insights
from .fx import price_in_usd
from .models import (
    Alert,
    Category,
    Forecast,
    Inventory,
    Marketplace,
    Order,
    OrderItem,
    PriceSnapshot,
    Product,
    SettlementLine,
    Settlement,
)

ASIN_RE = re.compile(r"\bB0[0-9A-Za-z]{8}\b")
DAYS_RE = re.compile(r"(\d{1,3})\s*(?:day|days|jour|jours)\b")
US_RE = re.compile(r"\b(us|usa|united states|etats[- ]?unis|am[ée]ricain|americain|america)\b", re.I)
CA_RE = re.compile(r"\b(ca|canada|canadi(?:en|enne)?|qu[ée]bec)\b", re.I)

_FR_MARKERS = [
    "bonjour", "salut", "bonsoir", "prévision", "prevision", "prévoir", "prevoir",
    "prédire", "predire", "marge", "combien", "quelle", "quelles", "quels", "quel",
    "comment", "pourrais", "pourriez", "inventaire", "commandes", "commande",
    "alertes", "alerte", "catégorie", "categorie", "segments", "à venir", "ventes",
    "vente", "bénéfices", "benefices", "bénéfice", "benefice", "marché", "marche",
    "dis-moi", "dis moi", "montre", "donne", "chiffres", "états-unis", "canada",
    "répartition", "repartition", "aperçu", "apercu", "résumé", "resume", "aide",
]
_FR_HIT = 2


def detect_language(text: str) -> str:
    """Cheap EN/FR detector ('' let the router fall back to the user's prefs)."""
    t = text.casefold().strip()
    if not t:
        return ""
    hits = sum(1 for marker in _FR_MARKERS if marker in t)
    return "fr" if hits >= _FR_HIT else "en"


def _parse_params(text: str, lang: str) -> dict:
    """Extract market / asin / days hints from free text (bilingual)."""
    t = text.casefold() if lang == "fr" else text
    params: dict = {"market": None, "asin": None, "days": None}
    m = ASIN_RE.search(text)
    if m:
        params["asin"] = m.group(0).upper()
    if US_RE.search(text):
        params["market"] = "US"
    elif CA_RE.search(text):
        params["market"] = "CA"
    d = DAYS_RE.search(text)
    if d:
        params["days"] = min(int(d.group(1)), 365)
    return params


# ---------------------------------------------------------------------------
# Intent NLU (priority order; EN + FR)
# ---------------------------------------------------------------------------

_INTENT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("greeting", re.compile(
        r"\b(hi|hello|hey|good\s*(morning|afternoon|evening)|yo)\b|"
        r"\b(bonjour|salut|bonsoir|allo|coucou)\b", re.I)),
    ("help", re.compile(
        r"\b(help|help me|what can you|how (to|do i)|commands|usage)\b|"
        r"\b(aide|que (peux|puis)|comment (utiliser|fonctionne)|commandes)\b", re.I)),
    ("predict", re.compile(
        r"\b(forecast|predict|prediction|projection|outlook|demand|trend|to come)\b|"
        r"\b(pr[ée]vision|pr[ée]voir|pr[ée]dire|projection|perspective|demande|tendance|"
        r"strat[ée]gie|recommend|recommande)\b|"
        r"\b(what should i|focus on|next move|prioritize|prioritise)\b", re.I)),
    ("orders", re.compile(
        r"\b(order|orders|sales|sold|units? sold|selling)\b|"
        r"\b(commande|commandes|vente|ventes|unit[ée]s?\s*vendues?)\b", re.I)),
    ("margin", re.compile(
        r"\b(margin|profit|profitab|benefit|net income|gross profit|fees)\b|"
        r"\b(marge|b[ée]n[ée]fices?|profit|rentab|frais|brut|net)\b", re.I)),
    ("inventory", re.compile(
        r"\b(inventory|stock level|on hand|low stock|restock)\b|"
        r"\b(inventaire|stock|quantit[ée](\s*en\s*stock)?|r[ée]assort)\b", re.I)),
    ("alerts", re.compile(
        r"\b(alert|alerts|notification|notifications|warning|warn)\b|"
        r"\b(alerte|alertes|notification|notifications|avertissement|pr[ée]venir)\b", re.I)),
    ("analyze", re.compile(
        r"\b(categor\w*|segment\w*|segmentation|famil\w*|niche\w*|analys\w*|"
        r"dim[ée]nsion\w*|top categor\w*|r[ée]partition)\b", re.I)),
    ("compare", re.compile(
        r"\b(compare|compar|versus|vs\.?)\b|"
        r"\b(compar|versus|vs\.?|contre)\b|\bdevise|currency|exchange rate|cours\b", re.I)),
    ("summary", re.compile(
        r"\b(overview|summary|big picture|status|health|dashboard)\b|"
        r"\b(aper.?u|r[ée]sum[ée]|situation|chiffres cl[ée]s|vitrine|état des lieux|"
        r"\bcomment (vont|va)( les?)? (affaires|ventes))\b", re.I)),
]

_INTENT_BY_NAME = {name: pat for name, pat in _INTENT_PATTERNS}
_DATA_ORDER = ["orders", "margin", "inventory", "alerts", "analyze", "compare", "predict"]


def parse_intent(text: str) -> tuple[str, dict]:
    """Return (intent, params). ASIN matches win; then priority-ordered rules.

    Data intents get precedence over greeting/help/summary so "bonjour, donne-
    moi les ventes" answers with sales, not a pleasantry.
    """
    params = _parse_params(text, detect_language(text))
    if params["asin"]:
        return "asin", params
    for name in _DATA_ORDER:
        if _INTENT_BY_NAME[name].search(text):
            return name, params
    if _INTENT_BY_NAME["summary"].search(text):
        return "summary", params
    for name in ("help", "greeting"):
        if _INTENT_BY_NAME[name].search(text):
            return name, params
    return "unknown", params


# ---------------------------------------------------------------------------
# Number formatting helpers (backend phrasing, locale-neutral)
# ---------------------------------------------------------------------------

def _money(value, currency: str) -> str:
    return f"{value:,.2f} {currency}"


def _pct(value) -> str:
    return f"{value:.1f}%"


def _fmt(v, digits: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v:,.{digits}f}"


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def _summary(session, days: int, market: str | None) -> dict:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    stmt = (
        select(
            Marketplace.code.label("market"),
            Marketplace.currency.label("currency"),
            func.count(func.distinct(Order.id)).label("orders"),
            func.coalesce(func.sum(OrderItem.quantity), 0).label("units"),
            func.coalesce(func.sum(OrderItem.quantity * OrderItem.unit_price), 0).label("gross"),
        )
        .join(Order, Order.id == OrderItem.order_id)
        .join(Marketplace, Marketplace.id == Order.marketplace_id)
        .where(Order.purchase_date >= cutoff)
    )
    if market:
        stmt = stmt.where(Marketplace.code == market.upper())
    rows = (await session.execute(stmt.group_by(Marketplace.code, Marketplace.currency))).all()
    by_market = [
        {"market": r.market, "orders": int(r.orders), "units": int(r.units),
         "gross": float(r.gross), "currency": r.currency}
        for r in rows
    ]
    status = (
        await session.execute(
            select(Order.status, func.count(Order.id))
            .where(Order.purchase_date >= cutoff)
            .group_by(Order.status)
        )
    ).all()
    by_status = [{"status": s, "count": int(c)} for s, c in status]
    total = {
        "orders": sum(b["orders"] for b in by_market),
        "units": sum(b["units"] for b in by_market),
        "gross": round(sum(b["gross"] for b in by_market), 2),
    }
    return {
        "window_days": days, "market": market,
        "total": total, "by_market": by_market, "by_status": by_status,
    }


async def _recent_orders(session, days: int, market: str | None, limit: int = 8) -> dict:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    totals = (
        await session.execute(
            select(
                func.count(func.distinct(Order.id)).label("orders"),
                func.coalesce(func.sum(OrderItem.quantity), 0).label("units"),
                func.coalesce(func.sum(OrderItem.quantity * OrderItem.unit_price), 0).label("gross"),
                func.max(OrderItem.item_currency).label("currency"),
            )
            .join(OrderItem, OrderItem.order_id == Order.id)
            .where(Order.purchase_date >= cutoff)
        )
    ).one()
    if market:
        totals = (
            await session.execute(
                select(
                    func.count(func.distinct(Order.id)).label("orders"),
                    func.coalesce(func.sum(OrderItem.quantity), 0).label("units"),
                    func.coalesce(func.sum(OrderItem.quantity * OrderItem.unit_price), 0).label("gross"),
                    func.max(OrderItem.item_currency).label("currency"),
                )
                .join(OrderItem, OrderItem.order_id == Order.id)
                .join(Marketplace, Marketplace.id == Order.marketplace_id)
                .where(Order.purchase_date >= cutoff, Marketplace.code == market.upper())
            )
        ).one()
    stmt = (
        select(
            Order.id, Order.amazon_order_id, Order.status, Order.purchase_date,
            Marketplace.code.label("market"),
            func.coalesce(func.sum(OrderItem.quantity), 0).label("units"),
            func.coalesce(func.sum(OrderItem.quantity * OrderItem.unit_price), 0).label("gross"),
            func.max(OrderItem.item_currency).label("currency"),
        )
        .join(OrderItem, OrderItem.order_id == Order.id)
        .join(Marketplace, Marketplace.id == Order.marketplace_id)
        .where(Order.purchase_date >= cutoff)
    )
    if market:
        stmt = stmt.where(Marketplace.code == market.upper())
    rows = (
        await session.execute(
            stmt.group_by(Order.id, Order.amazon_order_id, Order.status, Order.purchase_date,
                          Marketplace.code)
            .order_by(Order.purchase_date.desc())
            .limit(limit)
        )
    ).all()
    return {
        "window_days": days,
        "total": {
            "orders": int(totals.orders or 0), "units": int(totals.units or 0),
            "gross": round(float(totals.gross or 0), 2), "currency": totals.currency or "USD",
        },
        "rows": [
            {"order_id": r.id, "amazon_order_id": r.amazon_order_id, "status": r.status,
             "market": r.market, "date": r.purchase_date.isoformat() if r.purchase_date else None,
             "units": int(r.units), "gross": float(r.gross), "currency": r.currency or "USD"}
            for r in rows
        ],
    }


async def _margin_rows(session, days: int, market: str | None, limit: int = 8) -> dict:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    stmt = (
        select(
            SettlementLine.product_id,
            func.coalesce(func.sum(SettlementLine.units), 0).label("units"),
            func.coalesce(func.sum(SettlementLine.revenue), 0).label("revenue"),
            func.coalesce(func.sum(SettlementLine.net), 0).label("net"),
        )
        .join(Settlement, Settlement.id == SettlementLine.settlement_id)
    )
    if market:
        stmt = stmt.join(
            Marketplace, Marketplace.id == Settlement.marketplace_id
        ).where(Marketplace.code == market.upper())
    rows = (await session.execute(stmt.group_by(SettlementLine.product_id))).all()
    if not rows:
        return {"window_days": days, "rows": []}
    # every() unreliable in sqlite; fetch currency separately
    curr_stmt = (
        select(SettlementLine.product_id, SettlementLine.currency)
        .join(Settlement, Settlement.id == SettlementLine.settlement_id)
    )
    if market:
        curr_stmt = curr_stmt.join(Marketplace, Marketplace.id == Settlement.marketplace_id).where(
            Marketplace.code == market.upper()
        )
    currs = {p: c for p, c in (await session.execute(curr_stmt)).all()}
    products = {
        p.id: p for p in (
            await session.execute(select(Product).where(Product.id.in_([r.product_id for r in rows])))
        ).scalars()
    }
    out: list[dict] = []
    for r in rows:
        p = products.get(r.product_id)
        revenue = float(r.revenue or 0)
        net = float(r.net or 0)
        out.append({
            "product_id": r.product_id,
            "asin": p.asin if p else "",
            "title": p.title if p else "",
            "market": market or "ALL",
            "currency": currs.get(r.product_id, "USD"),
            "units": int(r.units or 0),
            "revenue": round(revenue, 2),
            "net": round(net, 2),
            "margin_pct": round(net / revenue * 100, 1) if revenue else None,
        })
    out.sort(key=lambda x: (x["net"] is not None, x["net"]), reverse=True)
    margins_all = [r["margin_pct"] for r in out if r["margin_pct"] is not None]
    return {
        "window_days": days,
        "avg_margin_pct": round(sum(margins_all) / len(margins_all), 1) if margins_all else None,
        "rows": out[:limit],
    }


async def _inventory_rows(session, limit: int = 10) -> dict:
    max_snap = (
        select(Inventory.product_id, func.max(Inventory.snapped_at).label("snapped_at"))
        .group_by(Inventory.product_id)
    ).subquery()
    rows = (
        await session.execute(
            select(Inventory.product_id, Inventory.quantity, Inventory.snapped_at)
            .join(max_snap, (Inventory.product_id == max_snap.c.product_id)
                  & (Inventory.snapped_at == max_snap.c.snapped_at))
            .order_by(Inventory.quantity.asc())
            .limit(limit)
        )
    ).all()
    pids = [r.product_id for r in rows]
    products = {
        p.id: p for p in (
            await session.execute(select(Product).where(Product.id.in_(pids)))
        ).scalars()
    }
    snaps = {}
    if pids:
        snap_rows = (
            await session.execute(
                select(PriceSnapshot.product_id, PriceSnapshot.price, PriceSnapshot.currency)
                .where(PriceSnapshot.product_id.in_(pids), PriceSnapshot.ours.is_(True))
                .order_by(PriceSnapshot.captured_at.desc())
            )
        ).all()
        for pid, price, currency in snap_rows:
            snaps.setdefault(pid, {"price": float(price) if price is not None else None,
                                   "currency": currency or "USD"})
    out = []
    for r in rows:
        p = products.get(r.product_id)
        out.append({
            "product_id": r.product_id,
            "asin": p.asin if p else "",
            "title": p.title if p else "",
            "quantity": int(r.quantity or 0),
            "price": snaps.get(r.product_id, {}).get("price"),
            "currency": snaps.get(r.product_id, {}).get("currency", "USD"),
        })
    return {"rows": out}


async def _open_alerts(session, market: str | None, limit: int = 8) -> dict:
    stmt = select(Alert).where(Alert.resolved_at.is_(None))
    if market:
        stmt = stmt.join(Marketplace, Marketplace.id == Alert.marketplace_id).where(
            Marketplace.code == market.upper()
        )
    alerts = (
        await session.execute(stmt.order_by(Alert.created_at.desc()).limit(limit))
    ).scalars().all()
    pids = [a.product_id for a in alerts if a.product_id is not None]
    asins: dict[int, str] = {}
    if pids:
        asins = {
            p.id: p.asin
            for p in (await session.execute(select(Product).where(Product.id.in_(pids)))).scalars()
        }
    count_stmt = select(func.count(Alert.id)).where(Alert.resolved_at.is_(None))
    if market:
        count_stmt = count_stmt.join(Marketplace, Marketplace.id == Alert.marketplace_id).where(
            Marketplace.code == market.upper()
        )
    total_open = (await session.execute(count_stmt)).scalar_one()
    crit_stmt = select(func.count(Alert.id)).where(
        Alert.resolved_at.is_(None), Alert.severity == "critical"
    )
    if market:
        crit_stmt = crit_stmt.join(Marketplace, Marketplace.id == Alert.marketplace_id).where(
            Marketplace.code == market.upper()
        )
    critical = (await session.execute(crit_stmt)).scalar_one()
    return {
        "count": int(total_open), "critical": int(critical),
        "rows": [
            {"id": a.id, "severity": a.severity, "kind": a.kind, "message": a.message,
             "asin": asins.get(a.product_id) if a.product_id else None,
             "market": market, "created_at": a.created_at.isoformat() if a.created_at else None}
            for a in alerts
        ],
    }


async def _compare_markets(session, limit: int = 8) -> dict:
    max_snap = (
        select(
            PriceSnapshot.product_id, PriceSnapshot.marketplace_id,
            func.max(PriceSnapshot.captured_at).label("captured_at"),
        )
        .where(PriceSnapshot.ours.is_(True))
        .group_by(PriceSnapshot.product_id, PriceSnapshot.marketplace_id)
    ).subquery()
    snaps = (
        await session.execute(
            select(PriceSnapshot).join(
                max_snap,
                (PriceSnapshot.product_id == max_snap.c.product_id)
                & (PriceSnapshot.marketplace_id == max_snap.c.marketplace_id)
                & (PriceSnapshot.captured_at == max_snap.c.captured_at),
            )
        )
    ).scalars().all()
    marketplaces = {
        m.id: m for m in (await session.execute(select(Marketplace))).scalars()
    }
    by_product: dict[int, dict] = {}
    for s in snaps:
        mp = marketplaces.get(s.marketplace_id)
        if mp is None:
            continue
        by_product.setdefault(s.product_id, {})[mp.code] = {
            "price": float(s.price) if s.price is not None else None,
            "currency": s.currency or mp.currency or "USD",
        }
    products = {
        p.id: p for p in (
            await session.execute(select(Product).where(Product.id.in_(list(by_product))))
        ).scalars()
    }
    rows = []
    for pid, markets in by_product.items():
        if "US" not in markets and "CA" not in markets:
            continue
        p = products.get(pid)
        us = markets.get("US", {})
        ca = markets.get("CA", {})
        us_usd = await price_in_usd(session, us.get("price"), us.get("currency") or "USD") if us.get("price") is not None else None
        ca_usd = await price_in_usd(session, ca.get("price"), ca.get("currency") or "CAD") if ca.get("price") is not None else None
        gap = round(us_usd - ca_usd, 2) if (us_usd is not None and ca_usd is not None) else None
        rows.append({
            "asin": p.asin if p else "",
            "title": p.title if p else "",
            "us": {"price": us.get("price"), "currency": us.get("currency") or "USD"},
            "ca": {"price": ca.get("price"), "currency": ca.get("currency") or "CAD"},
            "gap_usd": gap,
        })
    rows.sort(key=lambda x: x["gap_usd"] if x["gap_usd"] is not None else 0, reverse=True)
    return {"count": len(rows), "rows": rows[:limit]}


async def _segments(session, days: int, limit: int = 8) -> dict:
    tree = await build_tree(session, days=days, market_code=None)
    families = [n for n in tree if n["kind"] == "family"][:limit]
    return {
        "window_days": days,
        "segments": [
            {"id": n["id"], "name": n["name"], "path": n["path"], "units": n["units"],
             "revenue": n["revenue"], "net": n["net"], "margin_pct": n["margin_pct"],
             "currency": n["currency"], "products": n["product_count"]}
            for n in families
        ],
    }


async def _forecast_answer(session, limit: int = 6) -> dict:
    insights = await predictive_insights(session, limit=limit)
    chart = None
    if insights["top_next_30d"]:
        chart = {
            "type": "bar",
            "labels": [i["asin"] for i in insights["top_next_30d"]],
            "values": [i["next_30d"] for i in insights["top_next_30d"]],
        }
    return {"insights": insights, "chart": chart}


async def _asin_answer(session, asin: str) -> dict:
    product = (await session.execute(select(Product).where(Product.asin == asin))).scalar_one_or_none()
    if product is None:
        return {"product": None}
    snap_rows = (
        await session.execute(
            select(PriceSnapshot, Marketplace)
            .join(Marketplace, Marketplace.id == PriceSnapshot.marketplace_id)
            .where(PriceSnapshot.product_id == product.id, PriceSnapshot.ours.is_(True))
            .order_by(PriceSnapshot.captured_at.desc())
        )
    ).all()
    snapshot = None
    if snap_rows:
        s, m = snap_rows[0]
        snapshot = {"price": float(s.price) if s.price is not None else None,
                    "buybox": float(s.buybox) if s.buybox is not None else None,
                    "rank": s.sales_rank, "market": m.code, "currency": s.currency,
                    "captured_at": s.captured_at.isoformat() if s.captured_at else None}
    forces = (
        await session.execute(
            select(Forecast).where(Forecast.product_id == product.id)
            .order_by(Forecast.generated_at.desc()).limit(1)
        )
    ).scalars().first()
    chart = None
    if forces is not None:
        f = forces
        chart = {
            "type": "forecast",
            "history": {"dates": (f.history or {}).get("dates", []),
                        "values": (f.history or {}).get("values", [])},
            "dates": (f.points or {}).get("dates", []),
            "yhat": (f.points or {}).get("yhat", []),
            "lo": (f.points or {}).get("lo", []),
            "hi": (f.points or {}).get("hi", []),
        }
    params = (forces.params or {}) if forces is not None else {}
    return {
        "product": {"asin": product.asin, "title": product.title, "brand": product.brand or ""},
        "snapshot": snapshot,
        "forecast": {
            "mean_per_day": params.get("mean_per_day"),
            "trend_per_week_pct": params.get("trend_per_week_pct"),
            "horizon": forces.horizon if forces is not None else 30,
            "next_30d": round(sum(float(v) for v in ((forces.points or {}).get("yhat") or [])), 1)
            if forces is not None else None,
        },
        "chart": chart,
    }


# ---------------------------------------------------------------------------
# Prompt builders (EN / FR)
# ---------------------------------------------------------------------------

_PHRASES: dict[str, dict[str, str]] = {
    "en": {
        "greeting": ("Hi! I'm the MarketPilot assistant. I read the live database to answer "
                     "sales, margin, inventory, alerts, cross-market prices and 30-day demand "
                     "forecasts — always explainably. Ask in your own words or tap a quick action."),
        "help": ("Here's what I can do (EN or FR):\n"
                 "• \"sales overview\" — KPIs & status mix\n"
                 "• \"margin last 90 days\" — net by product\n"
                 "• \"forecast\" — demand outlook + movers\n"
                 "• \"alerts\" — open alerts\n"
                 "• \"compare US vs CA\" — price gaps in USD\n"
                 "• \"B0123456789\" — full ASIN picture\n"
                 "Conversations are saved per session; customize me via the panel on the right."),
        "summary": "Last {days} days: {orders} orders, {units} units, {gross} gross across {markets} markets.",
        "orders": "Last {days} days: {orders} orders and {units} units ({gross} gross). Most recent order below.",
        "margin": "Margin over the last {days} days: average net margin {avg}. Best products shown below.",
        "inventory": "I have {count} inventory records; {low} at {threshold}+ units. Lowest stock listed below.",
        "alerts": "You have {open} open alerts ({critical} critical). Latest shown below.",
        "analyze": "Top segments over the last {days} days — by family, with rollups below.",
        "compare": "Cross-market price gaps (USD) below; positive gap = cheaper in Canada.",
        "forecast": "From the latest forecasts: {gainers} trending up, {decliners} trending down. Next-30d leaders below.",
        "asin": "{title} ({asin}) — last price {price}, latest forecast {mean} units/day "
                "({trend}/week) → {next} units in the next {h} days.",
        "unknown": "I didn't quite understand that. Try asking in plain words or tap a quick action (or \"help\").",
        "empty": "No data to answer that yet — the pipeline hasn't produced anything to read.",
    },
    "fr": {
        "greeting": ("Bonjour ! Je suis l'assistant MarketPilot. Je lis la base de données en "
                     "direct pour répondre sur les ventes, marges, stocks, alertes, prix "
                     "inter-marchés et prévisions de demande à 30 jours — toujours de façon "
                     "explicable. Posez votre question ou tapez une action rapide."),
        "help": ("Voici ce que je peux faire (FR ou EN) :\n"
                 "• \"aperçu des ventes\" — KPI et répartition par statut\n"
                 "• \"marge sur 90 jours\" — net par produit\n"
                 "• \"prévisions\" — perspective de demande + mouvements\n"
                 "• \"alertes\" — alertes ouvertes\n"
                 "• \"comparer É.-U. vs Canada\" — écarts de prix en USD\n"
                 "• \"B0123456789\" — fiche complète d'un ASIN\n"
                 "Les conversations sont sauvegardées ; personnalisez-moi via le panneau à droite."),
        "summary": "Sur {days} jours : {orders} commandes, {units} unités, {gross} de brut sur {markets} marchés.",
        "orders": "Sur {days} jours : {orders} commandes et {units} unités ({gross} de brut). Commande la plus récente ci-dessous.",
        "margin": "Marge sur {days} jours : marge nette moyenne {avg}. Meilleurs produits ci-dessous.",
        "inventory": "J'ai {count} fiches d'inventaire ; {low} à {threshold}+ unités. Stock le plus bas ci-dessous.",
        "alerts": "Vous avez {open} alertes ouvertes ({critical} critiques). Dernières ci-dessous.",
        "analyze": "Meilleurs segments sur {days} jours — par famille, agrégations ci-dessous.",
        "compare": "Écarts de prix inter-marchés (USD) ci-dessous ; écart positif = moins cher au Canada.",
        "forecast": "D'après les dernières prévisions : {gainers} en hausse, {decliners} en baisse. Leaders des 30 prochains jours ci-dessous.",
        "asin": "{title} ({asin}) — dernier prix {price}, prévision {mean} unité(s)/jour "
                "({trend}/sem.) → {next} unités dans les {h} prochains jours.",
        "unknown": "Je n'ai pas bien compris. Posez votre question simplement ou tapez une action rapide (ou « aide »).",
        "empty": "Aucune donnée pour répondre — le pipeline n'a encore rien produit à lire.",
    },
}


def _ask_greeting_or_help_first(lang: str) -> dict:
    key = "greeting"
    return {"intent": key, "text": _PHRASES[lang][key], "data": None}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def assistant_reply(session, message: str, lang: str = "", market: str | None = None) -> dict:
    """Build an answer for `message`. `lang` '' = auto-detect."""
    lang = (lang or detect_language(message) or "en")
    lang = "en" if lang not in _PHRASES else lang
    ph = _PHRASES[lang]
    intent, params = parse_intent(message)
    market = market or params.get("market")
    days = params.get("days") or 30

    if intent == "greeting" or intent == "help":
        return {"intent": intent, "text": ph[intent], "data": None}

    if intent == "unknown":
        return {"intent": intent, "text": ph["unknown"], "data": None}

    if intent == "asin":
        info = await _asin_answer(session, params["asin"])
        if info["product"] is None:
            text = ph["unknown"]
            if lang == "fr":
                text = f"Je ne trouve pas l'ASIN {params['asin']} dans ma base."
            else:
                text = f"I can't find ASIN {params['asin']} in the database."
            return {"intent": intent, "text": text, "data": info}
        p = info["product"]
        f = info["forecast"]
        price_txt = "—"
        if info["snapshot"] and info["snapshot"]["price"] is not None:
            price_txt = _money(info["snapshot"]["price"], info["snapshot"]["currency"])
        if f["mean_per_day"] is None:
            if lang == "fr":
                text = (f"{p['title'] or p['asin']} ({p['asin']}) — pas encore de prévision ; "
                        f"dernier prix {price_txt}.")
            else:
                text = (f"{p['title'] or p['asin']} ({p['asin']}) — no forecast yet; "
                        f"last price {price_txt}.")
        else:
            text = ph["asin"].format(
                title=p["title"] or p["asin"], asin=p["asin"], price=price_txt,
                mean=_fmt(f["mean_per_day"]), trend=_fmt(f["trend_per_week_pct"], 1),
                next=_fmt(f["next_30d"]), h=f["horizon"] or 30,
            )
        data = {"product": p, "snapshot": info["snapshot"], "forecast": f, "chart": info["chart"]}
        return {"intent": intent, "text": text, "data": data}

    if intent == "orders":
        info = await _recent_orders(session, days, market)
        rows = info["rows"]
        text = ph["orders"].format(
            days=days, orders=info["total"]["orders"], units=info["total"]["units"],
            gross=_money(info["total"]["gross"], info["total"]["currency"] or "USD"),
        )
        if info["total"]["orders"] == 0:
            text = ph["empty"]
        return {"intent": intent, "text": text, "data": info}

    if intent == "margin":
        info = await _margin_rows(session, days, market)
        rows = info["rows"]
        avg = f"{info['avg_margin_pct']:.1f}%" if info["avg_margin_pct"] is not None else "—"
        text = ph["margin"].format(days=days, avg=avg)
        if not rows:
            text = ph["empty"]
        return {"intent": intent, "text": text, "data": info}

    if intent == "inventory":
        info = await _inventory_rows(session, limit=10)
        rows = info["rows"]
        low = [r for r in rows if r["quantity"] <= 5]
        text = ph["inventory"].format(
            count=len(rows), low=len(low), threshold=5,
        )
        if not rows:
            text = ph["empty"]
        return {"intent": intent, "text": text, "data": info}

    if intent == "alerts":
        info = await _open_alerts(session, market)
        text = ph["alerts"].format(open=info["count"], critical=info["critical"])
        return {"intent": intent, "text": text, "data": info}

    if intent == "analyze":
        info = await _segments(session, days)
        segs = info["segments"]
        text = ph["analyze"].format(days=days)
        if not segs:
            text = ph["empty"]
        return {"intent": intent, "text": text, "data": {"segments": segs}}

    if intent == "compare":
        info = await _compare_markets(session)
        text = ph["compare"]
        if info["count"] == 0:
            text = ph["empty"]
        return {"intent": intent, "text": text, "data": info}

    if intent == "predict":
        info = await _forecast_answer(session)
        ins = info["insights"]
        gainers = len(ins["gainers"])
        decliners = len(ins["decliners"])
        text = ph["forecast"].format(gainers=gainers, decliners=decliners)
        if ins["count"] == 0:
            text = ph["empty"]
        return {"intent": intent, "text": text, "data": info}

    # summary (default for everything else that looks data-ish)
    info = await _summary(session, days, market)
    if info["total"]["orders"] == 0:
        text = ph["empty"]
    else:
        text = ph["summary"].format(
            days=days, orders=info["total"]["orders"], units=info["total"]["units"],
            gross=_money(info["total"]["gross"], "USD"), markets=len(info["by_market"]),
        )
    return {"intent": intent, "text": text, "data": info}