"""Multidimensional analytics aggregation (Phase 6).

Money is summed per currency and normalized to USD at view time. Demand
(units) comes from order_items, economics (revenue/fees/net) from settlement
lines, market position (avg rank/last price) from the latest snapshots.

The hierarchy lives in `categories` (category → niche → family → products);
`build_tree` rolls child metrics up so each level "explains" the level above.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from .fx import price_in_usd
from .models import (
    Category,
    Marketplace,
    Order,
    OrderItem,
    PriceSnapshot,
    Product,
    Settlement,
    SettlementLine,
)


def _num(v):
    return round(float(v), 2) if v is not None else 0.0


def _money(value, currency: str) -> float:
    return _num(value)


async def _latest_snap_map(session, market_id: int | None) -> dict[int, dict]:
    """product_id -> {rank, price(best), currency} from the latest owned snapshot."""
    max_snap = (
        select(
            PriceSnapshot.product_id,
            PriceSnapshot.marketplace_id,
            func.max(PriceSnapshot.captured_at).label("captured_at"),
        )
        .where(PriceSnapshot.ours.is_(True))
        .group_by(PriceSnapshot.product_id, PriceSnapshot.marketplace_id)
    )
    if market_id:
        max_snap = max_snap.where(PriceSnapshot.marketplace_id == market_id)
    max_snap = max_snap.subquery()
    snap = (
        select(PriceSnapshot)
        .join(
            max_snap,
            (PriceSnapshot.product_id == max_snap.c.product_id)
            & (PriceSnapshot.marketplace_id == max_snap.c.marketplace_id)
            & (PriceSnapshot.captured_at == max_snap.c.captured_at),
        )
    ).subquery()
    rows = (await session.execute(select(snap))).all()
    out: dict[int, dict] = {}
    for r in rows:
        key = r.product_id
        if key in out:
            continue
        out[key] = {"rank": r.sales_rank, "price": _num(r.price), "currency": r.currency or "USD"}
    return out


async def _units_by_product(session, days: int, market_code: str | None) -> dict[int, int]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    stmt = (
        select(OrderItem.product_id, func.coalesce(func.sum(OrderItem.quantity), 0).label("units"))
        .join(Order, Order.id == OrderItem.order_id)
        .where(Order.purchase_date >= cutoff)
    )
    if market_code:
        stmt = stmt.join(Marketplace, Marketplace.id == Order.marketplace_id).where(
            Marketplace.code == market_code.upper()
        )
    rows = (await session.execute(stmt.group_by(OrderItem.product_id))).all()
    return {r.product_id: int(r.units or 0) for r in rows}


async def _economic_by_product(session, days: int, market_code: str | None) -> dict[int, dict]:
    """product_id -> {revenue_usd, net_usd} from settlement lines (USD-normalized)."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    stmt = (
        select(
            SettlementLine.product_id,
            SettlementLine.currency,
            func.coalesce(func.sum(SettlementLine.units), 0).label("units"),
            func.coalesce(func.sum(SettlementLine.revenue), 0).label("revenue"),
            func.coalesce(func.sum(SettlementLine.net), 0).label("net"),
        )
        .join(Settlement, Settlement.id == SettlementLine.settlement_id)
        .where(Settlement.posted_date >= cutoff)
    )
    if market_code:
        stmt = stmt.join(Marketplace, Marketplace.id == Settlement.marketplace_id).where(
            Marketplace.code == market_code.upper()
        )
    rows = (await session.execute(stmt.group_by(SettlementLine.product_id, SettlementLine.currency))).all()
    out: dict[int, dict] = {}
    for r in rows:
        entry = out.setdefault(r.product_id, {"revenue": 0.0, "net": 0.0, "units": 0})
        entry["revenue"] += await price_in_usd(session, _money(r.revenue, r.currency), r.currency)
        entry["net"] += await price_in_usd(session, _money(r.net, r.currency), r.currency)
        entry["units"] += int(r.units or 0)
    return out


async def family_metrics(
    session,
    days: int = 90,
    market_code: str | None = None,
) -> list[dict]:
    """Per-family aggregates as flat rows (family node + KPIs)."""
    units = await _units_by_product(session, days, market_code)
    econ = await _economic_by_product(session, days, market_code)
    snaps = await _latest_snap_map(session, None)

    families = (
        await session.execute(select(Category).where(Category.kind == "family"))
    ).scalars().all()
    rows: list[dict] = []
    for fam in families:
        products = (
            await session.execute(select(Product).where(Product.family_id == fam.id))
        ).scalars().all()
        prod_rows: list[dict] = []
        for p in products:
            revenue = econ.get(p.id, {}).get("revenue", 0.0)
            net = econ.get(p.id, {}).get("net", 0.0)
            prod_rows.append({
                "product_id": p.id,
                "asin": p.asin,
                "title": p.title,
                "units": units.get(p.id, 0),
                "revenue": revenue,
                "net": net,
                "rank": snaps.get(p.id, {}).get("rank"),
                "price": snaps.get(p.id, {}).get("price"),
                "currency": snaps.get(p.id, {}).get("currency", "USD"),
            })
        prod_rows.sort(key=lambda x: x["net"], reverse=True)
        fam_units = sum(r["units"] for r in prod_rows)
        fam_revenue = round(sum(r["revenue"] for r in prod_rows), 2)
        fam_net = round(sum(r["net"] for r in prod_rows), 2)
        ranks = [r["rank"] for r in prod_rows if r["rank"] is not None]
        prices = [r["price"] for r in prod_rows if r["price"] is not None]
        rows.append({
            "id": fam.id,
            "kind": "family",
            "name": fam.name,
            "path": f"{fam.name}",
            "product_count": len(prod_rows),
            "units": fam_units,
            "revenue": fam_revenue,
            "net": fam_net,
            "margin_pct": round(fam_net / fam_revenue * 100, 1) if fam_revenue else None,
            "avg_rank": round(sum(ranks) / len(ranks)) if ranks else None,
            "avg_price": round(sum(prices) / len(prices), 2) if prices else None,
            "currency": prices and (prod_rows[0]["currency"] if prices else "USD") or "USD",
            "products": prod_rows,
        })
    rows.sort(key=lambda x: (x["net"] is not None, x["net"]), reverse=True)
    return rows


async def build_tree(
    session,
    days: int = 90,
    market_code: str | None = None,
) -> list[dict]:
    """Flattened category → niche → family tree with rolled-up metrics.

    Node fields: id, kind, name, depth, child_count, product_count, units,
    revenue, net, margin_pct, avg_rank, avg_price, currency. Flattening is
    depth-first so the UI can indent by `depth`; nodes sort by net within a
    level so the biggest segments float to the top.
    """
    families = await family_metrics(session, days, market_code)
    cat_rows = (await session.execute(
        select(Category).order_by(Category.kind, Category.name)
    )).scalars().all()

    node: dict[int, dict] = {}
    for row in cat_rows:
        node[row.id] = {
            "id": row.id, "kind": row.kind, "name": row.name,
            "parent_id": row.parent_id, "children": [],
            "_units": 0, "_revenue": 0.0, "_net": 0.0, "_products": 0,
            "_ranks": [], "_prices": [], "_currency": "USD",
        }
    for row in cat_rows:
        if row.parent_id in node:
            node[row.parent_id]["children"].append(node[row.id])

    fam_meta = {f["id"]: f for f in families}
    for n in node.values():
        if n["kind"] == "family":
            meta = fam_meta.get(n["id"])
            if meta is None:
                continue
            n["_units"] = meta["units"]
            n["_revenue"] = meta["revenue"]
            n["_net"] = meta["net"]
            n["_products"] = meta["product_count"]
            if meta["avg_rank"]:
                n["_ranks"].append(meta["avg_rank"])
            if meta["avg_price"]:
                n["_prices"].append(meta["avg_price"])
            n["_currency"] = meta["currency"]

    def rollup(n: dict) -> None:
        for child in n["children"]:
            rollup(child)
            n["_units"] += child["_units"]
            n["_revenue"] += child["_revenue"]
            n["_net"] += child["_net"]
            n["_products"] += child["_products"]
            n["_ranks"].extend(child["_ranks"])
            n["_prices"].extend(child["_prices"])

    roots = [n for n in node.values() if n["parent_id"] is None or n["parent_id"] not in node]
    for root in roots:
        rollup(root)

    flat: list[dict] = []
    seen: set[int] = set()

    def dfs(n: dict, depth: int) -> None:
        if n["id"] in seen:
            return
        seen.add(n["id"])
        rank_count = n["_ranks"]
        avg_rank = round(sum(n["_ranks"]) / len(n["_ranks"])) if n["_ranks"] else None
        avg_price = round(sum(n["_prices"]) / len(n["_prices"]), 2) if n["_prices"] else None
        revenue = round(n["_revenue"], 2)
        net = round(n["_net"], 2)
        flat.append({
            "id": n["id"],
            "kind": n["kind"],
            "name": n["name"],
            "depth": depth,
            "child_count": len(n["children"]),
            "product_count": n["_products"],
            "units": n["_units"],
            "revenue": revenue,
            "net": net,
            "margin_pct": round(net / revenue * 100, 1) if revenue else None,
            "avg_rank": avg_rank,
            "avg_price": avg_price,
            "currency": n["_currency"],
        })
        for child in sorted(n["children"], key=lambda c: c["_net"], reverse=True):
            dfs(child, depth + 1)

    for root in sorted(roots, key=lambda c: c["_net"], reverse=True):
        dfs(root, 0)
    return flat


async def family_path(session, family_id: int) -> list[dict]:
    """category → niche → family chain (for breadcrumbs)."""
    path: list[dict] = []
    current = (await session.execute(select(Category).where(Category.id == family_id))).scalar_one_or_none()
    while current is not None:
        path.append({"id": current.id, "kind": current.kind, "name": current.name})
        if current.parent_id is None:
            break
        current = (await session.execute(select(Category).where(Category.id == current.parent_id))).scalar_one_or_none()
    path.reverse()
    return path


async def segment_products(
    session,
    family_id: int,
    days: int = 90,
    market_code: str | None = None,
    limit: int = 100,
) -> dict:
    """The family → ASIN drill: products in a family with their KPIs."""
    family = (await session.execute(select(Category).where(Category.id == family_id))).scalar_one_or_none()
    if family is None or family.kind != "family":
        return {"family_id": family_id, "exists": False, "rows": []}
    units = await _units_by_product(session, days, market_code)
    econ = await _economic_by_product(session, days, market_code)
    snaps = await _latest_snap_map(session, None)
    path = await family_path(session, family_id)

    products = (await session.execute(
        select(Product).where(Product.family_id == family_id).order_by(Product.title)
    )).scalars().all()
    rows = []
    for p in products:
        revenue = econ.get(p.id, {}).get("revenue", 0.0)
        net = econ.get(p.id, {}).get("net", 0.0)
        rows.append({
            "product_id": p.id,
            "asin": p.asin,
            "title": p.title,
            "brand": p.brand or "",
            "units": units.get(p.id, 0),
            "revenue": round(revenue, 2),
            "net": round(net, 2),
            "margin_pct": round(net / revenue * 100, 1) if revenue else None,
            "rank": snaps.get(p.id, {}).get("rank"),
            "price": snaps.get(p.id, {}).get("price"),
            "currency": snaps.get(p.id, {}).get("currency", "USD"),
        })
    rows.sort(key=lambda x: (x["net"] is not None, x["net"]), reverse=True)
    return {
        "family_id": family_id,
        "exists": True,
        "name": family.name,
        "path": "/".join(n["name"] for n in path),
        "path_nodes": path,
        "count": len(rows),
        "rows": rows[:limit],
    }