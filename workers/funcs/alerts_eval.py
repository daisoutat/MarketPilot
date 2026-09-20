# -*- coding: utf-8 -*-
"""alerts-eval worker (scheduled, every 10 min) — Phase 4.

Evaluates the enabled ``alert_rules`` per seller and materializes open
``alerts`` rows with a per-rule cooldown (``common.alerts``). Degrades to a
``skipped`` status when no database is configured.
"""
from __future__ import annotations

import asyncio

from common import telemetry
from common.config import get_settings
from common.db import init_db, make_sessionmaker

JOB = "alerts-eval"


async def run(event: dict) -> dict:
    engine = init_db()
    if engine is None:
        return {"status": "skipped", "reason": "no database configured (MP_DATABASE_URL / MP_DB_SECRET_ARN)"}
    factory = make_sessionmaker(engine)

    from common.alerts import run_alerts_driver

    async with factory() as session:
        run_row = await telemetry.start_run(session, JOB)
        result = await run_alerts_driver(session)
        created = result["created"]
        errors = result["errors"]
        rows = sum(v for k, v in created.items() if not k.endswith(":error"))
        if errors:
            await telemetry.finish_run(
                session, run_row, rows=rows,
                error="; ".join(f"{k}:{v[:120]}" for k, v in errors.items()),
                status="error",
            )
        else:
            await telemetry.finish_run(session, run_row, rows=rows)
        await session.commit()

    return {
        "status": "done" if not errors else "ok",
        "rules": result["rules"],
        "created": created,
        "errors": errors,
        "seller_id": result["seller_id"],
    }


def handler(event: dict, context=None) -> dict:
    return asyncio.run(run(event))


if __name__ == "__main__":
    print(handler({"detail": {}}))