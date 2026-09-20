# -*- coding: utf-8 -*-
"""reports-sync worker — real Phase 2 implementation.

Per seller x marketplace, runs the async Reports API flow for:
  1. `GET_MERCHANT_LISTINGS_ALL_DATA`  -> products, listed price snapshot,
     inventory quantity snapshot;
  2. `GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE_V2` -> settlement summary +
     per-SKU settlement lines (margin source).

The settlement window starts at the last successful run (or `-30d` by default)
so incremental runs stay bounded. Report payloads are DECRYPTED locally (AES/GCM)
and staged to S3 when `MP_REPORTS_BUCKET` is configured.
"""
from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from common import telemetry
from common.db import init_db, make_sessionmaker
from common.models import Marketplace, Seller, SpApiCreds
from common.reports import sync_reports_driver

from workers.lib.spapi_helpers import build_spapi_client, creds_configured

JOB = "reports-sync"
DEFAULT_WINDOW_DAYS = int(os.environ.get("MP_REPORTS_WINDOW_DAYS", "30"))


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _window() -> tuple[str, str]:
    end = datetime.now(UTC) + timedelta(days=1)
    start = datetime.now(UTC) - timedelta(days=DEFAULT_WINDOW_DAYS)
    return _iso(start), _iso(end)


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

        # Pre-flight: active sellers with usable credentials.
        creds_by_seller: dict[int, SpApiCreds] = {}
        for seller in sellers:
            creds_row = (
                await session.execute(select(SpApiCreds).where(SpApiCreds.seller_id == seller.id).limit(1))
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
            creds_by_seller[seller.id] = creds_row

        if not creds_by_seller:
            await session.commit()
            return {"status": "ok", "runs": [], "skipped": skipped}

        # Open one telemetry run per market so a mid-run failure is auditable.
        pending: dict[str, object] = {}
        for market in markets:
            pending[market.code] = await telemetry.start_run(session, JOB, market.code)

        async def client_builder(seller: Seller, market: Marketplace):
            return build_spapi_client(creds_by_seller[seller.id], market)

        start_iso, end_iso = _window()
        runs = await sync_reports_driver(
            session,
            client_builder,
            settlement_start=start_iso,
            settlement_end=end_iso,
            attempts=int(os.environ.get("MP_REPORTS_POLL_ATTEMPTS", "8")),
            interval=float(os.environ.get("MP_REPORTS_POLL_INTERVAL", "3.0")),
        )

        rows_staged = 0
        for run_row in runs:
            tele_row = pending.get(run_row.market)
            if run_row.error:
                await telemetry.finish_run(session, tele_row, error=run_row.error, status="error")
                results.append({"market": run_row.market, "error": run_row.error})
            else:
                rows_staged += run_row.settlements + run_row.settlement_lines
                await telemetry.finish_run(
                    session, tele_row,
                    rows=run_row.settlements + run_row.settlement_lines,
                )
                results.append({
                    "market": run_row.market,
                    "products": run_row.products,
                    "price_snapshots": run_row.snapshots,
                    "inventory_rows": run_row.inventory_rows,
                    "settlements": run_row.settlements,
                    "settlement_lines": run_row.settlement_lines,
                    "skipped": run_row.skipped[:5],
                })
            await session.flush()

        # Surface unbounded runs (a market pending run not covered by driver).
        covered = {r.market: True for r in runs}
        for code, tele_row in pending.items():
            if code not in covered:
                await telemetry.finish_run(session, tele_row, rows=0, status="skipped")

        await session.commit()

    return {
        "status": "ok",
        "runs": results,
        "skipped": skipped,
        "rows_staged": rows_staged,
    }


def handler(event: dict, context=None) -> dict:
    return asyncio.run(run(event))