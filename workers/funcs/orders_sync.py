# -*- coding: utf-8 -*-
"""orders-sync worker — real Phase 1 implementation.

Flow per seller x marketplace:
  1. resolve SP-API credentials (db `spapi_creds`, `env:SP_API` -> env vars,
     otherwise Secrets Manager ARN),
  2. build a `SpApiClient` (key-based auth by default, LWA fallback),
  3. delta sync from the last successful run (or `-7d`), page by page,
     upserting orders / order_items / products,
  4. record `pipeline_runs` telemetry; failures land in the DLQ via the
     Lambda retry config + `RateLimited` from the client.
"""
from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from common import telemetry
from common.db import init_db, make_sessionmaker
from common.models import Marketplace, Seller, SpApiCreds
from common.orders import sync_market
from common.spapi import CredentialError

from workers.lib.spapi_helpers import build_spapi_client, creds_configured

JOB = "orders-sync"
DEFAULT_BACKFILL_DAYS = int(os.environ.get("MP_ORDERS_BACKFILL_DAYS", "7"))


def _default_since() -> datetime:
    return datetime.now(UTC) - timedelta(days=DEFAULT_BACKFILL_DAYS)


async def run(event: dict) -> dict:
    engine = init_db()
    if engine is None:
        return {"status": "skipped", "reason": "no database configured (MP_DATABASE_URL / MP_DB_SECRET_ARN)"}
    factory = make_sessionmaker(engine)

    results = []
    skipped = []
    async with factory() as session:
        markets = (await session.execute(select(Marketplace).order_by(Marketplace.id))).scalars().all()
        sellers = (await session.execute(select(Seller))).scalars().all()
        for seller in sellers:
            creds_row = (
                await session.execute(
                    select(SpApiCreds).where(SpApiCreds.seller_id == seller.id).limit(1)
                )
            ).scalar_one_or_none()
            if creds_row is None or creds_row.status != "active":
                skipped.append({"seller": seller.name, "reason": "no active spapi_creds"})
                continue
            try:
                configured, reason = creds_configured(creds_row)
            except Exception as exc:
                skipped.append({"seller": seller.name, "reason": f"credential fetch failed: {exc}"})
                continue
            if not configured:
                skipped.append({"seller": seller.name, "reason": reason})
                continue

            for market in markets:
                last = await telemetry.last_ok(session, JOB, market.code)
                since = last if last is not None else _default_since()
                run_row = await telemetry.start_run(session, JOB, market.code)
                try:
                    client = build_spapi_client(creds_row, market)
                    market_result = await sync_market(session, client, seller.id, market.id, since)
                    await telemetry.finish_run(session, run_row, rows=market_result.orders)
                    results.append({
                        "market": market.code, "seller": seller.name,
                        "orders": market_result.orders, "items": market_result.items,
                        "pages": market_result.pages, "errors": market_result.errors[:5],
                    })
                except CredentialError as exc:
                    await session.rollback()  # drop partial upserts for this leg
                    run_row = await telemetry.start_run(session, JOB, market.code)
                    await telemetry.finish_run(session, run_row, error=str(exc), status="error")
                    results.append({"market": market.code, "seller": seller.name, "error": str(exc)})
                except Exception as exc:
                    await session.rollback()
                    run_row = await telemetry.start_run(session, JOB, market.code)
                    await telemetry.finish_run(session, run_row, error=f"{type(exc).__name__}: {exc}", status="error")
                    results.append({"market": market.code, "seller": seller.name, "error": str(exc)})

        await session.commit()

    return {
        "status": "ok",
        "markets": results,
        "skipped": skipped,
        "last_run": results[-1] if results else None,
    }


def handler(event: dict, context=None) -> dict:
    return asyncio.run(run(event))