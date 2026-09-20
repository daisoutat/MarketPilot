# -*- coding: utf-8 -*-
"""forecast worker (scheduled, nightly) — real Phase 6 implementation.

Fits the explainable seasonal-trend demand model (`common.forecast`) for every
product and appends a `forecasts` row (history-first). Products without enough
demand history are skipped. Old forecast rows are pruned per product so the
table stays small while retaining the last `keep` snapshots as an audit trail.
"""
from __future__ import annotations

import asyncio
import os

from sqlalchemy import delete, select

from common import telemetry
from common.db import init_db, make_sessionmaker
from common.forecast import compute_forecast
from common.models import Forecast, Product

JOB = "forecast"
KEEP = int(os.environ.get("MP_FORECAST_KEEP_ROWS", "12"))


def _window_days() -> int:
    return int(os.environ.get("MP_FORECAST_WINDOW_DAYS", "90"))


def _horizon() -> int:
    return int(os.environ.get("MP_FORECAST_HORIZON", "30"))


async def run(event: dict) -> dict:
    engine = init_db()
    if engine is None:
        return {"status": "skipped", "reason": "no database configured (MP_DATABASE_URL / MP_DB_SECRET_ARN)"}
    factory = make_sessionmaker(engine)

    async with factory() as session:
        products = (await session.execute(select(Product).order_by(Product.id))).scalars().all()
        tele_row = await telemetry.start_run(session, JOB)
        fitted = skipped = 0
        errors: list[str] = []
        for product in products:
            try:
                forecast = await compute_forecast(
                    session, product.id, window_days=_window_days(), horizon=_horizon()
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{product.asin}: {type(exc).__name__}: {exc}")
                continue
            if forecast is None:
                skipped += 1
                continue
            session.add(Forecast(
                product_id=product.id,
                model=forecast["model"],
                unit=forecast["unit"],
                window_days=forecast["window_days"],
                horizon=forecast["horizon"],
                params=forecast["params"],
                history=forecast["history"],
                points=forecast["points"],
            ))
            fitted += 1
            # prune old rows for this product (keep the last KEEP).
            keep_ids = (
                select(Forecast.id)
                .where(Forecast.product_id == product.id)
                .order_by(Forecast.generated_at.desc())
                .limit(KEEP)
            )
            await session.execute(
                delete(Forecast).where(
                    Forecast.product_id == product.id,
                    Forecast.id.not_in(keep_ids),
                )
            )
            await session.flush()

        if errors:
            await telemetry.finish_run(session, tele_row, rows=fitted, error="; ".join(errors[:3]), status="error")
        else:
            await telemetry.finish_run(session, tele_row, rows=fitted)
        await session.commit()

    return {"status": "ok" if not errors else "degraded", "products": len(products),
            "fitted": fitted, "skipped": skipped, "errors": errors[:5]}


def handler(event: dict, context=None) -> dict:
    return asyncio.run(run(event))


if __name__ == "__main__":
    print(handler({"detail": {}}))