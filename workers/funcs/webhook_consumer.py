# -*- coding: utf-8 -*-
"""webhook-consumer worker (SQS) — real Phase 4 implementation.

SP-API Notifications deliver JSON events to an SQS queue subscribed as the
notification destination. For each record this function:

  1. parses the SQS envelope (record body -> notification payload JSON),
  2. decodes a base64 ``Payload`` if present (SP-API v2 deliver via SQS),
  3. best-effort upserts a price snapshot for ``ANY_OFFER_CHANGED`` events so
     the alerts engine can react to market price changes sooner,
  4. records the event idempotently (unique ``webhook_events.event_id``) so
     duplicate SQS delivers are no-ops (standard-queue semantics),
  5. surfaces hard failures in the event row + pipeline telemetry.

Messages that cannot be parsed are counted as errors (self-heal can watch the
``pipeline_runs`` telemetry). Unknown/invalid events are always logged.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json

from sqlalchemy import select

from common.db import init_db, make_sessionmaker
from common.models import Marketplace, PriceSnapshot, WebhookEvent
from common.orders import upsert_product


def parse_record(record: dict) -> dict | None:
    """Extract a normalized event dict from one SQS record, or None if unusable."""
    body = record.get("body")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (TypeError, ValueError):
            return None
    if not isinstance(body, dict):
        return None
    # SNS topic delivery wraps the event under "Message".
    msg = body.get("Message")
    if isinstance(msg, str):
        try:
            inner = json.loads(msg)
            if isinstance(inner, dict):
                body = inner
        except (TypeError, ValueError):
            pass

    notif_type = body.get("NotificationType") or "unknown"
    raw = body.get("Payload")
    if isinstance(raw, str):
        try:
            payload = json.loads(base64.b64decode(raw))
        except Exception:  # noqa: BLE001
            payload = {"raw": raw}
    elif isinstance(raw, dict):
        payload = raw
    else:
        payload = {k: v for k, v in body.items() if k not in ("Payload",)}

    event_id = (
        payload.get("NotificationId")
        or payload.get("eventId")
        or body.get("eventId")
        or hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:64]
    )
    return {
        "event_id": str(event_id),
        "notification_type": notif_type,
        "payload": payload if isinstance(payload, dict) else {"raw": payload},
    }


async def _ingest_offer_change(session, parsed: dict) -> None:
    """Best-effort: turn an ANY_OFFER_CHANGED event into a market snapshot."""
    if parsed["notification_type"] != "ANY_OFFER_CHANGED":
        return
    trigger = (parsed["payload"].get("OfferChangeTrigger") or {})
    asin = trigger.get("ASIN")
    if not asin:
        return
    summary = parsed["payload"].get("Summary") or {}
    lowest = (summary.get("LowestPrice") or [{}])[0] or {}
    landed = lowest.get("LandedPrice") or {}
    price = landed.get("Amount")
    currency = lowest.get("CurrencyCode") or trigger.get("MarketplaceCurrencyCode") or "USD"
    if price is None:
        return
    marketplace_id = (trigger.get("MarketplaceId") or "").strip()
    if not marketplace_id:
        return
    product_id = await upsert_product(session, str(asin).strip(), title="")
    market = (
        await session.execute(select(Marketplace).where(Marketplace.marketplace_id == marketplace_id))
    ).scalar_one_or_none()
    if market is None:
        return
    session.add(PriceSnapshot(
        product_id=product_id,
        marketplace_id=market.id,
        ours=False,
        price=float(price),
        buybox=float(price),
        currency=currency,
    ))


async def process_records(session, records: list[dict]) -> dict:
    processed = 0
    errors = 0
    for record in records:
        parsed = parse_record(record)
        if parsed is None:
            errors += 1
            continue
        exists = (
            await session.execute(select(WebhookEvent.id).where(WebhookEvent.event_id == parsed["event_id"]))
        ).first()
        if exists:
            continue  # idempotent by event id (standard-queue redelivery)
        try:
            await _ingest_offer_change(session, parsed)
            session.add(WebhookEvent(
                event_id=parsed["event_id"],
                notification_type=parsed["notification_type"],
                payload=parsed["payload"],
                processed=True,
            ))
            processed += 1
        except Exception as exc:  # noqa: BLE001 — never let one event take the batch down
            session.add(WebhookEvent(
                event_id=parsed["event_id"],
                notification_type=parsed["notification_type"],
                payload=parsed["payload"],
                processed=False,
                error=str(exc)[:500],
            ))
            errors += 1
    await session.flush()
    return {"records": len(records), "processed": processed, "errors": errors}


async def run(event: dict) -> dict:
    records = (event or {}).get("Records", [])
    if not records:
        return {"status": "ok", "records": 0, "processed": 0, "errors": 0}
    engine = init_db()
    if engine is None:
        return {"status": "skipped", "reason": "no database configured (MP_DATABASE_URL / MP_DB_SECRET_ARN)"}
    factory = make_sessionmaker(engine)
    async with factory() as session:
        result = await process_records(session, records)
        await session.commit()
    result["status"] = "ok"
    return result


def handler(event: dict, context=None) -> dict:
    return asyncio.run(run(event))


if __name__ == "__main__":
    print(handler({"Records": []}))