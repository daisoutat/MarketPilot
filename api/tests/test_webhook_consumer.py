# -*- coding: utf-8 -*-
"""Phase 4: webhook-consumer parsing + idempotent processing."""
from __future__ import annotations

import base64
import json

import pytest
from sqlalchemy import func, select

from common.models import PriceSnapshot, Product, WebhookEvent
from workers.funcs.webhook_consumer import parse_record, process_records


def _sqs_record(body) -> dict:
    return {"body": json.dumps(body)}


def _offer_change_event(payload: dict) -> dict:
    return {
        "NotificationType": "ANY_OFFER_CHANGED",
        "Payload": base64.b64encode(json.dumps(payload).encode()).decode(),
    }


class TestParseRecord:
    def test_decodes_base64_payload(self):
        payload = {
            "NotificationId": "evt-1",
            "OfferChangeTrigger": {"ASIN": "B0HOOK1", "MarketplaceId": "ATVPDKIKX0DER",
                                   "ChangeType": "Update"},
            "Summary": {"LowestPrice": [{"LandedPrice": {"Amount": 42.5, "CurrencyCode": "USD"}}]},
        }
        parsed = parse_record(_sqs_record(_offer_change_event(payload)))
        assert parsed["event_id"] == "evt-1"
        assert parsed["notification_type"] == "ANY_OFFER_CHANGED"
        assert parsed["payload"]["Summary"]["LowestPrice"][0]["LandedPrice"]["Amount"] == 42.5

    def test_sns_message_envelope(self):
        body = {"Message": json.dumps({"eventId": "evt-2", "NotificationType": "ORDER_CHANGE"})}
        parsed = parse_record(_sqs_record(body))
        assert parsed["event_id"] == "evt-2"
        assert parsed["notification_type"] == "ORDER_CHANGE"

    def test_garbage_is_none(self):
        assert parse_record({"body": "{not json"}) is None
        assert parse_record({"body": "just a string"}) is None
        assert parse_record({"body": None}) is None


@pytest.mark.asyncio
async def test_process_records_idempotent_and_ingests_offer(seeded_session):
    payload = {
        "NotificationId": "evt-10",
        "OfferChangeTrigger": {"ASIN": "B0HOOK1", "MarketplaceId": "ATVPDKIKX0DER"},
        "Summary": {"LowestPrice": [{"LandedPrice": {"Amount": 42.5, "CurrencyCode": "USD"}}]},
    }
    records = [_sqs_record(_offer_change_event(payload)), _sqs_record("garbage")]
    first = await process_records(seeded_session, records)
    await seeded_session.commit()
    assert first == {"records": 2, "processed": 1, "errors": 1}

    events = (await seeded_session.execute(select(WebhookEvent))).scalars().all()
    assert len(events) == 1
    assert events[0].event_id == "evt-10"
    assert events[0].processed is True
    assert events[0].error == ""

    # Offer-change event was ingested as a market (ours=False) snapshot.
    product = (await seeded_session.execute(select(Product).where(Product.asin == "B0HOOK1"))).scalar_one()
    snap = (await seeded_session.execute(
        select(PriceSnapshot).where(PriceSnapshot.product_id == product.id)
    )).scalar_one()
    assert snap.ours is False
    assert float(snap.price) == 42.5
    assert snap.marketplace_id == 1  # US

    # Re-delivery (same event id) is a no-op.
    second = await process_records(seeded_session, [records[0]])
    await seeded_session.commit()
    assert second["processed"] == 0
    count = (await seeded_session.execute(select(func.count(WebhookEvent.id)))).scalar()
    assert count == 1


@pytest.mark.asyncio
async def test_process_records_unknown_payload_ok(seeded_session):
    body = {"eventId": "evt-3", "NotificationType": "FEED_PROCESSING_FINISHED",
            "Foo": {"Bar": 1}}
    result = await process_records(seeded_session, [_sqs_record(body)])
    await seeded_session.commit()
    assert result["processed"] == 1
    event = (await seeded_session.execute(select(WebhookEvent).where(WebhookEvent.event_id == "evt-3"))).scalar_one()
    assert event.payload["Foo"]["Bar"] == 1