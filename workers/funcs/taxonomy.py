# -*- coding: utf-8 -*-
"""taxonomy worker (scheduled, daily) — real Phase 6 implementation.

Assigns every catalogued product (one with a price snapshot in a marketplace)
to a family via the explainable keyword/brand rule set in `common.categorize`,
materializing the category → niche → family tree as it goes. Runs per
marketplace; idempotent (re-assigning a product just points family_id at the
same family node).
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from common import telemetry
from common.categorize import assign_all
from common.db import init_db, make_sessionmaker
from common.models import Marketplace

JOB = "taxonomy"


async def run(event: dict) -> dict:
    engine = init_db()
    if engine is None:
        return {"status": "skipped", "reason": "no database configured (MP_DATABASE_URL / MP_DB_SECRET_ARN)"}
    factory = make_sessionmaker(engine)

    async with factory() as session:
        markets = (await session.execute(select(Marketplace).order_by(Marketplace.id))).scalars().all()
        results = []
        for market in markets:
            tele_row = await telemetry.start_run(session, JOB, market.code)
            try:
                outcome = await assign_all(session, market.id)
                results.append({"market": market.code, **outcome})
                await telemetry.finish_run(session, tele_row, rows=outcome["assigned"])
            except Exception as exc:  # noqa: BLE001
                await telemetry.finish_run(session, tele_row, error=f"{type(exc).__name__}: {exc}", status="error")
                results.append({"market": market.code, "error": str(exc)})
            await session.flush()
        await session.commit()

    return {"status": "ok", "markets": results}


def handler(event: dict, context=None) -> dict:
    return asyncio.run(run(event))


if __name__ == "__main__":
    print(handler({"detail": {}}))