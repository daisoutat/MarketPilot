# -*- coding: utf-8 -*-
"""Tests for the reports pipeline: parsers, aggregation, decryption, loaders,
and the driver end-to-end with a fake SP-API client."""
from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from common.models import (
    Inventory,
    Marketplace,
    PriceSnapshot,
    Product,
    Seller,
    Settlement,
    SettlementLine,
    SpApiCreds,
)
from common.reports import (
    ReportTimeout,
    aggregate_settlement_lines,
    decrypt_document,
    load_inventory,
    load_settlements,
    parse_inventory_tsv,
    parse_settlements_tsv,
    poll_report,
    sync_reports_driver,
)

SETTLEMENT_HEADER = (
    "settlement-id\tsettlement-start-date\tsettlement-end-date\tdeposit-date\ttotal-amount\tcurrency"
    + "\tsales-order-id\torder-id\torder-item-id\tadjustment-id\tmerchant-order-item-id"
    + "\tquantity-type\tquantity\tmarketplace-id\tcurrency-code\tamount\tsku"
)

INVENTORY_TSV = (
    "item-name\tseller-sku\tprice\tquantity\topen-date\tproduct-id\tstatus\titem-condition\tfulfillment-channel\n"
    "Widget A\tWID-A\t12.50\t5\t2026-08-01T00:00:00+00:00\tB0WID1\tActive\tNew\tMFN\n"
    "Gadget B\tGAD-B\t9.99\t0\t2026-08-02T00:00:00+00:00\tB0GAD2\tInactive\tRefurbished\tFBA\n"
)

SETTLEMENT_TSV = (
    "settlement-id\tsettlement-start-date\tsettlement-end-date\tdeposit-date\ttotal-amount\tcurrency\n"
    "SET-1\t2026-08-01T00:00:00+00:00\t2026-08-15T23:59:59+00:00\t2026-08-20T12:00:00+00:00\t480.00\tUSD\n"
    + SETTLEMENT_HEADER + "\n"
    "SET-1\t2026-08-01T00:00:00+00:00\t2026-08-15T23:59:59+00:00\t2026-08-20T12:00:00+00:00\t480.00\tUSD"
    + "\t\tORD-1\ti1\t\tMI-1\tQuantity\t2\tATVPDKIKX0DER\tUSD\t25.00\tWID-A\n"
    "SET-1\t2026-08-01T00:00:00+00:00\t2026-08-15T23:59:59+00:00\t2026-08-20T12:00:00+00:00\t480.00\tUSD"
    + "\t\tORD-1\ti1\tADJ-1\t\t\t\tATVPDKIKX0DER\tUSD\t-3.75\tWID-A\n"
    "SET-1\t2026-08-01T00:00:00+00:00\t2026-08-15T23:59:59+00:00\t2026-08-20T12:00:00+00:00\t480.00\tUSD"
    + "\t\tORD-2\ti2\t\tMI-2\tQuantity\t1\tATVPDKIKX0DER\tUSD\t40.00\tGAD-B\n"
)


# ------------------------------------------------------------------ parsers
def test_inventory_parser():
    rows = parse_inventory_tsv(INVENTORY_TSV)
    assert len(rows) == 2
    first = rows[0]
    assert first["sku"] == "WID-A" and first["asin"] == "B0WID1"
    assert first["price"] == 12.5 and first["quantity"] == 5
    assert first["title"] == "Widget A" and first["condition"] == "New"
    assert parse_inventory_tsv(b"not\ta\ttsv") == []


def test_settlement_parser_and_aggregation():
    summaries, order_lines = parse_settlements_tsv(SETTLEMENT_TSV)
    assert len(summaries) == 1
    s = summaries[0]
    assert s["settlement_id"] == "SET-1" and s["total"] == "480.00" and s["currency"] == "USD"
    assert len(order_lines) == 3

    lines = aggregate_settlement_lines(order_lines)
    by_order = {l["order_id"]: l for l in lines}
    assert len(lines) == 2
    a = by_order["ORD-1"]
    assert a["units"] == 2 and a["revenue"] == 25.0 and a["net"] == 21.25
    assert round(a["fees"], 2) == 3.75  # net == revenue - fees
    assert a["sku"] == "WID-A"
    b = by_order["ORD-2"]
    assert b["units"] == 1 and b["revenue"] == 40.0 and b["fees"] == 0.0


def test_aggregate_skips_blank_groups():
    assert aggregate_settlement_lines([]) == []
    assert aggregate_settlement_lines([{"order_id": "", "sku": "", "amount": "5"}]) == []


# ------------------------------------------------------------ decryption
def test_decrypt_aes_roundtrip():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = AESGCM.generate_key(bit_length=128)
    iv = b"0123456789abcde"
    ct = AESGCM(key).encrypt(iv, b"settlement payload", None)
    details = {
        "standard": "AES",
        "key": base64.b64encode(key).decode(),
        "initializationVector": base64.b64encode(iv).decode(),
    }
    assert decrypt_document(ct, details) == b"settlement payload"
    assert decrypt_document(b"plain", None) == b"plain"
    with pytest.raises(Exception):
        decrypt_document(ct, {**details, "key": base64.b64encode(b"0" * 16).decode()})


# ------------------------------------------------------------ polling
class _FakePollingClient:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.report = None

    async def get_report(self, report_id):
        self.report = report_id
        st = self.statuses.pop(0)
        if st == "DONE":
            return {"processingStatus": "DONE", "reportDocumentId": "doc-1"}
        return {"processingStatus": st}


@pytest.mark.asyncio
async def test_poll_success():
    client = _FakePollingClient(["IN_PROGRESS", "IN_PROGRESS", "DONE"])
    out = await poll_report(client, "r-1", attempts=5, interval=0)
    assert out["reportDocumentId"] == "doc-1"


@pytest.mark.asyncio
async def test_poll_fatal_and_timeout():
    from common.reports import ReportError

    class _DoneNoDoc:
        async def get_report(self, report_id):
            return {"processingStatus": "DONE"}

    with pytest.raises(ReportError):
        await poll_report(_DoneNoDoc(), "x", attempts=1, interval=0)
    with pytest.raises(ReportError):
        await poll_report(_FakePollingClient(["FATAL"]), "x", attempts=1, interval=0)
    with pytest.raises(ReportTimeout):
        await poll_report(_FakePollingClient(["IN_QUEUE", "IN_QUEUE"]), "x", attempts=2, interval=0)


# ---------------------------------------------------------------- loaders
async def _seed_market_seller(session):
    market = Marketplace(code="US", marketplace_id="ATVPDKIKX0DER", currency="USD", iso="en-US")
    seller = Seller(name="Demo", credentials_ref="env:SP_API")
    session.add_all([market, seller])
    await session.flush()
    session.add(SpApiCreds(seller_id=seller.id, auth_model="key", client_id_arn="env:SP_API", status="active"))
    await session.commit()
    return seller, market


@pytest.mark.asyncio
async def test_load_inventory(session_factory):
    async with session_factory() as session:
        _, market = await _seed_market_seller(session)
        rows = parse_inventory_tsv(INVENTORY_TSV)
        res = await load_inventory(session, rows, market.id, "USD")
        assert res.products == 2 and res.snapshots == 2 and res.inventory == 2
        snap = (await session.execute(select(PriceSnapshot).where(PriceSnapshot.price == 12.5))).scalar_one()
        assert snap.currency == "USD" and snap.ours is True
        inv = (await session.execute(select(Inventory))).scalars().all()
        assert {i.quantity for i in inv} == {5, 0}
        # rows without asin are skipped
        res2 = await load_inventory(session, [{"sku": "NOASIN", "title": "x"}], market.id, "USD")
        assert res2.products == 0 and len(res2.skipped) == 1


@pytest.mark.asyncio
async def test_load_settlements_idempotent(session_factory):
    async with session_factory() as session:
        seller, market = await _seed_market_seller(session)
        summaries, order_lines = parse_settlements_tsv(SETTLEMENT_TSV)
        sku_to_asin = {"WID-A": "B0WID1", "GAD-B": "B0GAD2"}

        res1 = await load_settlements(session, seller.id, market.id, summaries, order_lines, sku_to_asin)
        assert res1.settlements == 1 and res1.lines == 2

        res2 = await load_settlements(session, seller.id, market.id, summaries, order_lines, sku_to_asin)
        assert res2.settlements == 0 and res2.lines == 0  # already loaded
        assert any("already loaded" in s for s in res2.skipped)

        lines = (await session.execute(select(SettlementLine))).scalars().all()
        assert len(lines) == 2
        line = (await session.execute(select(SettlementLine).where(SettlementLine.sku == "WID-A"))).scalar_one()
        assert line.units == 2 and line.net == 21.25


@pytest.mark.asyncio
async def test_load_settlements_skips_unresolved(session_factory):
    async with session_factory() as session:
        seller, market = await _seed_market_seller(session)
        summaries, order_lines = parse_settlements_tsv(SETTLEMENT_TSV)
        res = await load_settlements(session, seller.id, market.id, summaries, order_lines, {})
        assert res.settlements == 1 and res.lines == 0
        assert any("no product" in s for s in res.skipped)


# ---------------------------------------------------------------- driver
class FakeReportsClient:
    def __init__(self, payloads: dict[str, bytes]):
        self.payloads = payloads
        self.created = []

    async def create_report(self, report_type, report_options=None, start_date=None, end_date=None):
        self.created.append(report_type)
        return f"rep-{report_type}"

    async def get_report(self, report_id):
        return {"processingStatus": "DONE", "reportDocumentId": report_id}

    async def get_report_document(self, document_id):
        return {"url": f"https://example.invalid/doc/{document_id}", "encryptionDetails": None}

    async def download_document(self, url):
        rtype = url.split("/")[-1].removeprefix("rep-")
        return self.payloads[rtype]


@pytest.mark.asyncio
async def test_driver_end_to_end(session_factory):
    async with session_factory() as session:
        await _seed_market_seller(session)
        client = FakeReportsClient({
            "GET_MERCHANT_LISTINGS_ALL_DATA": INVENTORY_TSV.encode(),
            "GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE_V2": SETTLEMENT_TSV.encode(),
        })

        async def builder(seller, market):
            return client

        runs = await sync_reports_driver(session, builder, attempts=1, interval=0)
        await session.commit()
        assert len(runs) == 1
        run = runs[0]
        assert run.error == ""
        assert run.products == 2 and run.snapshots == 2 and run.inventory_rows == 2
        assert run.settlements == 1 and run.settlement_lines == 2
        assert set(client.created) == {
            "GET_MERCHANT_LISTINGS_ALL_DATA",
            "GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE_V2",
        }
        assert (await session.execute(select(func.count()).select_from(Settlement))).scalar_one() == 1