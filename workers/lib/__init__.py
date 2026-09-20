# -*- coding: utf-8 -*-
"""Worker helpers shared by all Lambda functions.

Phase 0 skeleton: these record pipeline telemetry via the standard library and
defer the actual HTTP/DB plumbing to Phase 1+ (SP-API sync). The functions stay
pure and importable so unit tests can run without AWS.
"""
from __future__ import annotations

import json
import os
from datetime import datetime


def job_name() -> str:
    return os.environ.get("MP_JOB", "unknown")


def marketplace_codes() -> list[str]:
    raw = os.environ.get("MP_MARKETPLACES", "US,CA")
    return [c.strip().upper() for c in raw.split(",") if c.strip()]


def utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def record_run(job: str, status: str, rows: int = 0, error: str = "") -> dict:
    """Phase 0 in-memory run record. Phase 2+: upsert into `pipeline_runs`."""
    return {
        "job": job,
        "status": status,
        "rows": rows,
        "error": error,
        "started": utc_now(),
    }


def load_event(event: dict) -> dict:
    """Normalize the trigger event to {job, markets, payload}."""
    payload = event.get("detail", {}) if "detail" in event else event.get("Records", event)
    return {
        "job": payload.get("job", job_name()),
        "markets": payload.get("markets", marketplace_codes()),
        "payload": payload,
    }


def message(text: str) -> dict:
    return {"ok": True, "log": json.dumps({"job": job_name(), "msg": text})}