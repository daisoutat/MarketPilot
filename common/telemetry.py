"""`pipeline_runs` bookkeeping shared by workers."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from .models import PipelineRun


async def last_ok(session, job: str, market: str = "") -> datetime | None:
    """Most recent successful finish for a job (optionally per market)."""
    stmt = (
        select(PipelineRun.finished)
        .where(PipelineRun.job == job, PipelineRun.status == "done")
        .order_by(PipelineRun.started.desc())
        .limit(1)
    )
    if market:
        stmt = stmt.where(PipelineRun.marketplace_code == market)
    row = (await session.execute(stmt)).first()
    return row[0] if row else None


async def start_run(session, job: str, market: str = "") -> PipelineRun:
    run = PipelineRun(job=job, marketplace_code=market, status="running", started=datetime.now(UTC))
    session.add(run)
    await session.flush()
    return run


async def finish_run(session, run: PipelineRun, rows: int = 0, error: str = "", status: str = "done") -> None:
    run.status = status
    run.finished = datetime.now(UTC)
    run.rows = rows
    run.error = error or run.error
    await session.flush()