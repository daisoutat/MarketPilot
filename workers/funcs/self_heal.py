# -*- coding: utf-8 -*-
"""self-heal worker (scheduled, every 5 min) — real Phase 5 implementation.

Autonomous operations, all recorded in `pipeline_runs` (job `self-heal`) which
doubles as the audit trail:

  1. Stale-run cleanup   — jobs stuck in `running` past a cutoff are failed
     (Lambda timeout leaks),
  2. Stale job re-trigger — a scheduled job whose last successful run is older
     than its threshold is re-invoked in-process, guarded by a claim marker so
     we never stack multiple triggers while it is still starting up,
  3. DLQ replay          — messages dead-lettered by any worker Lambda are
     replayed in order with a bounded attempt budget (max 3) and exponential
     backoff; a successful replay deletes the message, failures stay in the
     DLQ for triage.

`MP_HEAL_SQS=0` disables the SQS relay (used in CI / local tests); boto3 keeps
working on Lambda where the runtime provides it.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
from datetime import datetime, timedelta

from common import heal, telemetry
from common.db import init_db, make_sessionmaker

JOB = "self-heal"
DEFAULT_STALE = ("orders-sync:15,price-snapshot:180,reports-sync:1440,alerts-eval:30,"
                 "taxonomy:1440,forecast:2880")
DEFAULT_DLQ_JOBS = ("orders_sync,reports_sync,price_snapshot,alerts_eval,webhook_consumer,"
                    "taxonomy,forecast")
STALE_RUN_MINUTES = int(os.environ.get("MP_HEAL_STALE_RUN_MINUTES", "20"))
MAX_REPLAY_BATCH = 10

_OK_STATES = {"ok", "done", "skipped"}


def _stale_config() -> dict[str, int]:
    return heal.parse_stale_config(os.environ.get("MP_HEAL_STALE", DEFAULT_STALE))


def _dlq_jobs() -> list[str]:
    raw = os.environ.get("MP_HEAL_DLQS", DEFAULT_DLQ_JOBS)
    return [j.strip() for j in raw.split(",") if j.strip()]


def _module_for(job: str):
    fn = job.replace("-", "_")
    return importlib.import_module(f"workers.funcs.{fn}")


def _now():
    return heal._now()


async def _mark_stale_running(session) -> int:
    return await heal.mark_stale_running(session, older_than_minutes=STALE_RUN_MINUTES)


async def _trigger_stale(session, stale: list[str], cfg: dict[str, int], errors: list[str]) -> dict:
    triggered = 0
    for job in stale:
        threshold = cfg[job]
        min_interval = max(5, min(threshold // 3, 60))
        claimed = await heal.claim_trigger(session, job, min_interval_minutes=min_interval)
        if not claimed:
            continue
        try:
            result = _module_for(job).handler({})
            if isinstance(result, dict) and result.get("status") in _OK_STATES:
                triggered += 1
            else:
                errors.append(f"{job}: unexpected result")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{job}: {type(exc).__name__}: {exc}")
    return {"triggered": triggered, "attempted": len(stale)}


async def _replay_dlqs(session, sqs, errors: list[str]) -> dict:
    counts = {"replayed": 0, "errors": 0, "deferred": 0}
    if sqs is None or os.environ.get("MP_HEAL_SQS", "1") != "1":
        return counts
    for job in _dlq_jobs():
        queue_name = f"mp-{job.replace('_', '-')}-dlq"
        try:
            url = sqs.get_queue_url(QueueName=queue_name)["QueueUrl"]
        except Exception:  # noqa: BLE001 — queue not deployed yet
            continue
        try:
            received = sqs.receive_message(
                QueueUrl=url, MaxNumberOfMessages=MAX_REPLAY_BATCH,
                AttributeNames=["SentTimestamp"],
            ).get("Messages", [])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{queue_name}: {type(exc).__name__}: {exc}")
            continue
        for msg in received:
            message_id = msg["MessageId"]
            state = await heal.load_replay_state(session, message_id)
            allowed, attempt, backoff = heal.replay_budget(state)
            last = state.get("last_attempt")
            if last:
                last_dt = heal._as_utc(datetime.fromisoformat(last))
                if last_dt and last_dt + timedelta(seconds=backoff) > _now():
                    counts["deferred"] += 1
                    continue
            if not allowed:
                counts["deferred"] += 1  # budget exhausted — stays in DLQ for triage
                continue
            try:
                body = json.loads(msg.get("Body", "{}"))
                result = _module_for(job).handler(body if isinstance(body, dict) else {})
                ok = isinstance(result, dict) and result.get("status") in _OK_STATES
                err = "" if ok else f"unexpected result: {result}"
            except Exception as exc:  # noqa: BLE001
                ok = False
                err = f"{type(exc).__name__}: {exc}"
            await heal.save_replay_state(session, message_id, attempt)
            if ok:
                try:
                    sqs.delete_message(QueueUrl=url, ReceiptHandle=msg["ReceiptHandle"])
                    counts["replayed"] += 1
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{queue_name}: delete failed: {exc}")
            else:
                counts["errors"] += 1
                errors.append(f"{job}: {err[:160]}")
    return counts


def _sqs_client():
    try:
        import boto3
    except ImportError:
        return None
    try:
        return boto3.client("sqs", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    except Exception:  # noqa: BLE001
        return None


async def run(event: dict) -> dict:
    engine = init_db()
    if engine is None:
        return {"status": "skipped", "reason": "no database configured (MP_DATABASE_URL / MP_DB_SECRET_ARN)"}
    factory = make_sessionmaker(engine)
    sqs = _sqs_client()
    errors: list[str] = []

    async with factory() as session:
        run_row = await telemetry.start_run(session, JOB)
        cleanup = await _mark_stale_running(session)
        cfg = _stale_config()
        stale = await heal.detect_stale(session, cfg)
        triggered = await _trigger_stale(session, stale, cfg, errors)
        replay = await _replay_dlqs(session, sqs, errors)

        rows = cleanup + triggered["triggered"] + replay["replayed"]
        if errors:
            await telemetry.finish_run(
                session, run_row, rows=rows,
                error="; ".join(errors[:3]), status="error",
            )
        else:
            await telemetry.finish_run(session, run_row, rows=rows)
        await session.commit()

    return {
        "status": "ok" if not errors else "degraded",
        "stale": stale,
        "cleanup": cleanup,
        "triggered": triggered,
        "replay": {k: v for k, v in replay.items()},
        "errors": errors[:5],
    }


def handler(event: dict, context=None) -> dict:
    return asyncio.run(run(event))


if __name__ == "__main__":
    print(handler({"detail": {}}))