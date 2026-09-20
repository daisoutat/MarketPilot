# -*- coding: utf-8 -*-
"""Self-heal (Phase 5) — config parsing, replay budget, stale detection,
re-trigger guard and stale-run cleanup. SQS relay is exercised only as a
disabled path since tests run without AWS (MP_HEAL_SQS=0)."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from common import heal, telemetry
from common.models import PipelineRun, Setting
from workers.funcs import self_heal

SETTINGS_KEY = "heal:msg:abc123"


def test_parse_stale_config_ok():
    cfg = heal.parse_stale_config("orders-sync:15, price-snapshot:180,reports-sync:1440, alerts-eval: 30")
    assert cfg == {
        "orders-sync": 15,
        "price-snapshot": 180,
        "reports-sync": 1440,
        "alerts-eval": 30,
    }


def test_parse_stale_config_skips_garbage():
    cfg = heal.parse_stale_config("orders-sync:15,,:x,no-colon,job:-5")
    assert cfg == {"orders-sync": 15}


def test_replay_budget_bounded():
    assert heal.replay_budget({}) == (True, 1, 300)
    assert heal.replay_budget({"attempts": 1}) == (True, 2, 600)
    assert heal.replay_budget({"attempts": 2}) == (True, 3, 1200)
    assert heal.replay_budget({"attempts": 3}) == (False, 4, 0)


def test_backoff_capped():
    assert heal.backoff_seconds(3) == 1200
    assert heal.backoff_seconds(9) == 2400  # cap at 40 min


async def test_stale_detection(seeded_session):
    cfg = {"orders-sync": 15, "price-snapshot": 180}

    await telemetry.finish_run(
        seeded_session,
        await telemetry.start_run(seeded_session, "orders-sync"),
        rows=1,
    )
    recent = await telemetry.start_run(seeded_session, "price-snapshot")
    await telemetry.finish_run(seeded_session, recent, rows=1)
    await seeded_session.commit()

    stale = await heal.detect_stale(seeded_session, cfg)
    assert stale == []  # both ran just now

    await seeded_session.commit()


async def test_stale_detection_when_run_old(seeded_session):
    old = PipelineRun(
        job="orders-sync", status="done",
        started=datetime.now(UTC) - timedelta(days=2),
        finished=datetime.now(UTC) - timedelta(days=2, minutes=-1),
    )
    seeded_session.add(old)
    await seeded_session.commit()

    stale = await heal.detect_stale(seeded_session, {"orders-sync": 15})
    assert stale == ["orders-sync"]


async def test_trigger_guard_cooldown(seeded_session):
    assert await heal.claim_trigger(seeded_session, "orders-sync", min_interval_minutes=5)
    # second claim within the interval must be rejected
    assert not await heal.claim_trigger(seeded_session, "orders-sync", min_interval_minutes=5)
    # a later claim (simulated by rewinding the stored stamp) re-arms
    setting = await seeded_session.get(Setting, "heal:trigger:orders-sync")
    setting.value = json.dumps({"last": (datetime.now(UTC) - timedelta(minutes=10)).isoformat()})
    await seeded_session.commit()
    assert await heal.claim_trigger(seeded_session, "orders-sync", min_interval_minutes=5)


async def test_stale_running_cleanup(seeded_session):
    stale_run = await telemetry.start_run(seeded_session, "orders-sync")
    stale_run.started = datetime.now(UTC) - timedelta(minutes=60)
    fresh = await telemetry.start_run(seeded_session, "price-snapshot")
    fresh.started = datetime.now(UTC)
    await seeded_session.commit()

    marked = await heal.mark_stale_running(seeded_session, older_than_minutes=20)
    await seeded_session.commit()

    assert marked == 1
    rows = (await seeded_session.execute(
        select(PipelineRun).where(PipelineRun.job == "orders-sync")
    )).scalars().all()
    assert rows[0].status == "error"
    assert "stale run" in rows[0].error


@pytest.mark.asyncio
async def test_heal_run_with_sqs_disabled(monkeypatch):
    """End-to-end self-heal.run() with SQS + triggers off: must finish green
    and record its own pipeline_runs row (the audit trail)."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    from common.models import Base

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(self_heal, "init_db", lambda: engine)
    monkeypatch.setenv("MP_HEAL_SQS", "0")
    monkeypatch.setenv("MP_HEAL_STALE", "")  # nudge config empty => no triggers

    result = await self_heal.run({})

    assert result["status"] == "ok"
    assert result["stale"] == []
    assert result["cleanup"] == 0
    assert result["replay"] == {"replayed": 0, "errors": 0, "deferred": 0}

    async with engine.begin() as conn:
        status = (await conn.execute(
            select(PipelineRun.status).where(PipelineRun.job == "self-heal")
        )).scalar_one()
    assert status == "done"