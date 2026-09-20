"""Market research snapshot pipeline (PA-API 5.0 -> price_snapshots).

Snapshots are history-first: every run appends to ``price_snapshots`` (the
table is a timeseries by design). We snapshot:

* **owned** ASINs (have at least one ``ours=True`` snapshot) — price/Buy-Box/
  sales rank for our catalog per marketplace;
* **research targets** (``research_targets`` rows) that are not owned;
* **discovered** ASINs from keyword searches (registered as new targets).

FX-normalized comparison happens at view time (``common.fx``), never in the
store.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from sqlalchemy import select

from .models import Marketplace, PriceSnapshot, Product, ResearchTarget
from .orders import upsert_product

MAX_ITEMS_PER_CALL = 10


@dataclass
class ResearchRun:
    market: str
    owned: int = 0
    research: int = 0
    new_targets: int = 0
    keyword_searches: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def snapshots(self) -> int:
        return self.owned + self.research


ClientBuilder = Callable[[Marketplace], Awaitable[Any]]


def _chunks(values: list[str], size: int = MAX_ITEMS_PER_CALL):
    for i in range(0, len(values), size):
        yield values[i:i + size]


async def _owned_asins(session, marketplace_id: int) -> set[str]:
    rows = (
        await session.execute(
            select(Product.asin)
            .join(PriceSnapshot, PriceSnapshot.product_id == Product.id)
            .where(PriceSnapshot.ours.is_(True), PriceSnapshot.marketplace_id == marketplace_id)
            .distinct()
        )
    ).all()
    return {r.asin for r in rows}


async def _snapshot_batch(session, market: Marketplace, client, items: list[dict], ours: bool) -> int:
    count = 0
    for raw in items:
        item = client.normalize_item(raw)
        if not item:
            continue
        product_id = await upsert_product(session, item["asin"], item["title"])
        session.add(
            PriceSnapshot(
                product_id=product_id,
                marketplace_id=market.id,
                ours=ours,
                price=item["price"],
                buybox=item["buybox"],
                sales_rank=item["sales_rank"],
                currency=item["currency"],
            )
        )
        count += 1
    return count


async def _ensure_target(session, market: Marketplace, asin: str, keywords: str = "") -> bool:
    existing = (
        await session.execute(
            select(ResearchTarget).where(
                ResearchTarget.marketplace_id == market.id,
                ResearchTarget.asin == asin,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False
    session.add(ResearchTarget(marketplace_id=market.id, asin=asin, keywords=keywords))
    return True


async def snapshot_market(
    session,
    client,
    market: Marketplace,
    ours_asins: set[str],
    targets: list[ResearchTarget],
    search_keywords: list[str] | None = None,
    max_owned: int = 200,
) -> ResearchRun:
    run = ResearchRun(market=market.code)
    try:
        target_asins = {t.asin for t in targets}
        owned = sorted(o for o in ours_asins if o not in target_asins)[:max_owned]
        for chunk in _chunks(owned):
            run.owned += await _snapshot_batch(session, market, client, await client.get_items(chunk), ours=True)

        research = [a for a in target_asins if a not in ours_asins]
        for chunk in _chunks(research):
            run.research += await _snapshot_batch(session, market, client, await client.get_items(chunk), ours=False)

        keywords: list[str] = []
        for t in targets:
            if t.keywords and t.keywords not in keywords:
                keywords.append(t.keywords)
        for kw in search_keywords or []:
            if kw not in keywords:
                keywords.append(kw)
        for kw in keywords:
            run.keyword_searches += 1
            found = await client.search_items(kw)
            for raw in found:
                item = client.normalize_item(raw)
                if not item:
                    continue
                if await _ensure_target(session, market, item["asin"], kw):
                    run.new_targets += 1
                if item["asin"] not in ours_asins:
                    run.research += await _snapshot_batch(session, market, client, [raw], ours=False)

        snap_now = datetime.now(UTC)
        for t in targets:
            t.last_snap_at = snap_now
    except Exception as exc:
        run.errors.append(f"{type(exc).__name__}: {exc}")
    return run


async def sync_research_driver(
    session,
    client_builder: ClientBuilder,
    search_keywords: list[str] | None = None,
    max_owned: int = 200,
) -> list[ResearchRun]:
    results: list[ResearchRun] = []
    markets = (await session.execute(select(Marketplace).order_by(Marketplace.id))).scalars().all()
    for market in markets:
        targets = (
            await session.execute(
                select(ResearchTarget)
                .where(ResearchTarget.marketplace_id == market.id, ResearchTarget.active.is_(True))
                .order_by(ResearchTarget.created_at)
            )
        ).scalars().all()
        ours = await _owned_asins(session, market.id)
        client = await client_builder(market)
        results.append(await snapshot_market(session, client, market, ours, targets, search_keywords, max_owned))
    return results