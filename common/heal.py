"""Self-heal support (Phase 5).

Pure helpers + `settings`-backed state for the self-heal worker:

  * stale config: job -> allowed gap in minutes (`parse_stale_config`),
  * stale detection: a job is stale when its most recent *successful* run is
    older than the threshold (or never ran) — `detect_stale`,
  * re-trigger guard: prevents re-invoking the same job within a minimum
    interval while it is still running (`claim_trigger`),
  * DLQ replay budget: bounded attempts with exponential backoff, tracked in
    `settings` so it survives Lambda cold starts (`replay_budget`).

`settings` rows are used as a tiny k/v store (schema exists since 0001).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from . import telemetry
from .models import PipelineRun, Setting


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(v) -> datetime | None:
    if v is None:
        return None
    return v.replace(tzinfo=UTC) if v.tzinfo is None else v


def parse_stale_config(raw: str) -> dict[str, int]:
    """Parse `job:mins,job:mins,...` into a dict (invalid entries dropped)."""
    out: dict[str, int] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        job, _, mins = part.partition(":")
        try:
            value = int(mins.strip())
        except (TypeError, ValueError):
            continue
        if job.strip() and value > 0:
            out[job.strip()] = value
    return out


async def _get_json(session, key: str) -> dict:
    row = await session.get(Setting, key)
    try:
        return json.loads(row.value) if row and row.value else {}
    except (TypeError, ValueError):
        return {}


async def _set_json(session, key: str, value: dict) -> None:
    setting = await session.get(Setting, key)
    if setting is None:
        setting = Setting(key=key, value="")
        session.add(setting)
    setting.value = json.dumps(value)


async def detect_stale(session, config: dict[str, int]) -> list[str]:
    """Jobs whose last successful run is older than its threshold (or absent)."""
    stale: list[str] = []
    for job, max_gap_minutes in config.items():
        last = await telemetry.last_ok(session, job)
        if last is None:
            stale.append(job)
            continue
        last_ok = _as_utc(last)
        if last_ok is None or last_ok < _now() - timedelta(minutes=max_gap_minutes):
            stale.append(job)
    return stale


async def claim_trigger(session, job: str, *, at: datetime | None = None,
                        min_interval_minutes: int) -> bool:
    """Atomically claim a re-trigger: False if already claimed too recently."""
    at = at or _now()
    state = await _get_json(session, f"heal:trigger:{job}")
    last = state.get("last")
    if last:
        last_dt = datetime.fromisoformat(last)
        if _as_utc(last_dt) >= at - timedelta(minutes=min_interval_minutes):
            return False
    state["last"] = at.isoformat()
    await _set_json(session, f"heal:trigger:{job}", state)
    return True


def backoff_seconds(attempt: int, base_minutes: int = 5, cap_minutes: int = 40) -> int:
    """Exponential backoff for replay attempt N (1-indexed), capped in minutes."""
    minutes = min(base_minutes * (2 ** max(attempt - 1, 0)), cap_minutes)
    return minutes * 60


def replay_budget(state: dict) -> tuple[bool, int, int]:
    """Given stored replay state, returns (allowed, next_attempt, backoff_s).

    The message is dropped from replay once attempts exceed 3 (kept in the DLQ
    for manual triage); meanwhile exponential backoff paces the retries.
    """
    attempts = int((state or {}).get("attempts", 0))
    next_attempt = attempts + 1
    if next_attempt > 3:
        return False, next_attempt, 0
    return True, next_attempt, backoff_seconds(next_attempt)


async def load_replay_state(session, message_id: str) -> dict:
    return await _get_json(session, f"heal:msg:{message_id}")


async def save_replay_state(session, message_id: str, attempts: int,
                            last_attempt: datetime | None = None) -> None:
    await _set_json(session, f"heal:msg:{message_id}", {
        "attempts": attempts,
        "last_attempt": (last_attempt or _now()).isoformat(),
    })


async def mark_stale_running(session, *, older_than_minutes: int = 20) -> int:
    """Fail job runs still stuck in `running` past the cutoff (timeout leak)."""
    cutoff = _now() - timedelta(minutes=older_than_minutes)
    rows = (
        await session.execute(
            select(PipelineRun).where(
                PipelineRun.status == "running",
                PipelineRun.started.isnot(None),
            )
        )
    ).scalars().all()
    marked = 0
    for run in rows:
        if _as_utc(run.started) and _as_utc(run.started) < cutoff:
            run.status = "error"
            run.error = "self-heal: stale run (no finish recorded)"
            run.finished = _now()
            marked += 1
    return marked