# -*- coding: utf-8 -*-
"""price-snapshot worker (scheduled, hourly) — real Phase 3 implementation.

Per marketplace, snapshots Buy-Box / price / sales rank for:
  * owned ASINs (have an ``ours=True`` snapshot or a research target link),
  * research targets registered in ``research_targets``,
  * ASINs discovered via ``MP_RESEARCH_KEYWORDS`` and target keywords.

All reads go through PA-API 5.0 ``GetItems``/``SearchItems`` (one Associates
partner tag covers US + CA; the request ``Marketplace`` selects the store).
Writes are appended to ``price_snapshots`` in ISO currency — FX-normalized
USD comparison happens at view time via ``common.fx``.
"""
from __future__ import annotations

import asyncio
import os

from sqlalchemy import select

from common import telemetry
from common.config import get_settings
from common.db import init_db, make_sessionmaker
from common.models import Marketplace
from common.paapi import PaApiClient
from common.research import sync_research_driver

JOB = "price-snapshot"
DEFAULT_MAX_OWNED = int(os.environ.get("MP_SNAPSHOT_MAX_ASINS", "200"))


def _client(market: Marketplace) -> PaApiClient:
    s = get_settings()
    return PaApiClient(
        s.pa_access_key, s.pa_secret_key, s.pa_partner_tag,
        market.code, region=s.pa_region,
    )


def _keywords() -> list[str]:
    s = get_settings()
    return [k.strip() for k in s.research_keywords.split(",") if k.strip()]


async def run(event: dict) -> dict:
    s = get_settings()
    if not (s.pa_access_key and s.pa_secret_key and s.pa_partner_tag):
        return {
            "status": "skipped",
            "reason": "PA-API not configured (MP_PA_ACCESS_KEY / MP_PA_SECRET_KEY / MP_PA_PARTNER_TAG)",
        }
    engine = init_db()
    if engine is None:
        return {"status": "skipped", "reason": "no database configured (MP_DATABASE_URL / MP_DB_SECRET_ARN)"}
    factory = make_sessionmaker(engine)

    async with factory() as session:
        markets = (await session.execute(select(Marketplace).order_by(Marketplace.id))).scalars().all()
        pending: dict[str, object] = {}
        for market in markets:
            pending[market.code] = await telemetry.start_run(session, JOB, market.code)

        runs = await sync_research_driver(
            session,
            _client,
            search_keywords=_keywords(),
            max_owned=int(os.environ.get("MP_SNAPSHOT_MAX_ASINS", str(DEFAULT_MAX_OWNED))),
        )

        rows_staged = 0
        results = []
        for run_row in runs:
            tele_row = pending.get(run_row.market)
            if run_row.errors:
                await telemetry.finish_run(session, tele_row, error=run_row.errors[0], status="error")
                results.append({"market": run_row.market, "error": run_row.errors[0]})
            else:
                rows_staged += run_row.snapshots
                await telemetry.finish_run(session, tele_row, rows=run_row.snapshots)
                results.append({
                    "market": run_row.market,
                    "owned": run_row.owned,
                    "research": run_row.research,
                    "snapshots": run_row.snapshots,
                    "new_targets": run_row.new_targets,
                    "keyword_searches": run_row.keyword_searches,
                })
            await session.flush()

        await session.commit()

    return {"status": "ok", "runs": results, "rows_staged": rows_staged}


def handler(event: dict, context=None) -> dict:
    return asyncio.run(run(event))


if __name__ == "__main__":
    print(handler({"detail": {}}))