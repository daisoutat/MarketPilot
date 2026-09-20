"""Reports pipeline (SP-API v2021-06-30 -> local store).

Async report flow: ``createReport`` -> poll ``getReport`` -> download the
pre-signed document URL -> (decrypt AES/GCM when present) -> parse -> load.

The two report types in Phase 2:

* ``GET_MERCHANT_LISTINGS_ALL_DATA``  — inventory flat-file (TSV). Columns:
  ``seller-sku``, ``product-id`` (ASIN), ``item-name``, ``price``, ``quantity``,
  ``open-date``, ``status``. Drives ``products`` + ``inventory`` snapshots and
  the per-market listed ``price_snapshots``.
* ``GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE_V2`` — settlement flat-file V2.
  Repeating blocks: a 6-column settlement summary row, a long column-header
  row, then order/receipt rows. We aggregate rows by item into
  ``settlements`` (summary) + ``settlement_lines`` (per-SKU margin source).

Parsing is header-driven so column order renames on Amazon's side degrade
gracefully instead of breaking the worker.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable

from sqlalchemy import select

from .models import (
    Inventory,
    Marketplace,
    PriceSnapshot,
    Product,
    Seller,
    Settlement,
    SettlementLine,
    SpApiCreds,
)
from .orders import upsert_product
from .spapi import SpApiClient


class ReportError(Exception):
    pass


class ReportTimeout(ReportError):
    pass


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _to_float(v: Any) -> float | None:
    try:
        return round(float(v), 2) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> int:
    try:
        return int(float(v)) if v not in (None, "") else 0
    except (TypeError, ValueError):
        return 0


def _to_date(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_date(v: str) -> bool:
    return _to_date(v) is not None


class _TSV:
    """Header-driven TSV reader: first row with known column wins."""

    def __init__(self, content: bytes | str, required: set[str]):
        text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
        self.required = required
        self.rows: list[list[str]] = []
        for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            cells = raw.split("\t")
            if not cells or (len(cells) == 1 and not cells[0].strip()):
                continue
            self.rows.append(cells)
        self.header: dict[str, int] | None = None
        for row in self.rows:
            lowered = {c.lower().strip() for c in row}
            if required & lowered:
                self.header = {c.lower().strip(): i for i, c in enumerate(row)}
                break
        # skip the header row itself on iteration
        self._consume_header = bool(self.header)

    def __iter__(self):
        for row in self.rows:
            if self._consume_header and self._row_is_header(row):
                continue
            yield self._row(row)

    def _row_is_header(self, row: list[str]) -> bool:
        lowered = {c.lower().strip() for c in row}
        return bool(self.required & lowered) and self.header is not None

    def _row(self, row: list[str]) -> dict[str, str]:
        if self.header is None:
            return {}
        out: dict[str, str] = {}
        for name, idx in self.header.items():
            out[name] = row[idx] if idx < len(row) else ""
        return out


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

INVENTORY_COLUMNS = {"seller-sku", "product-id"}


def parse_inventory_tsv(content: bytes | str) -> list[dict[str, Any]]:
    """Merchant listings flat file -> normalized inventory rows."""
    rows = []
    for r in _TSV(content, INVENTORY_COLUMNS):
        if not r.get("seller-sku") and not r.get("product-id"):
            continue
        rows.append({
            "sku": r.get("seller-sku") or "",
            "asin": r.get("product-id") or "",
            "title": r.get("item-name") or "",
            "price": _to_float(r.get("price")),
            "quantity": _to_int(r.get("quantity")),
            "status": r.get("status") or "",
            "condition": r.get("item-condition") or "New",
            "open_date": _to_date(r.get("open-date")),
            "fulfillment": r.get("fulfillment-channel") or "",
        })
    return rows


SETTLEMENT_SUMMARY_KEYS = {"settlement-id", "settlement-start-date"}
SETTLEMENT_HEADER_KEYS = {"order-id", "sku", "quantity-type", "amount"}


def parse_settlements_tsv(content: bytes | str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Settlement flat-file V2 -> (summary rows, raw order/receipt rows)."""
    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    summaries: list[dict[str, str]] = []
    order_lines: list[dict[str, str]] = []
    header: dict[str, int] | None = None

    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        cells = raw.split("\t")
        if not cells or (len(cells) == 1 and not cells[0].strip()):
            continue
        lowered = [c.lower().strip() for c in cells]
        lowered_set = set(lowered)

        # Settlement summary row: 6 columns, first != the literal legend. In the
        # legend row cells[0]=='settlement-id'; value rows carry a deposit date.
        if len(cells) == 6 and lowered[0] != "settlement-id" and _is_date(cells[1]) and cells[5]:
            summaries.append({
                "settlement_id": cells[0],
                "start": cells[1],
                "end": cells[2],
                "deposit": cells[3],
                "total": cells[4],
                "currency": cells[5],
            })
            continue

        if SETTLEMENT_HEADER_KEYS & lowered_set:
            header = {c: i for i, c in enumerate(lowered)}
            continue

        if header is None:
            continue
        get_col = lambda name: cells[header[name]] if name in header and header[name] < len(cells) else ""
        marker = get_col("order-id")
        if not marker:
            continue
        order_lines.append({
            "settlement_id": get_col("settlement-id"),
            "order_id": marker,
            "order_item_id": get_col("order-item-id"),
            "merchant_order_item_id": get_col("merchant-order-item-id") or get_col("adjustment-id"),
            "sku": get_col("sku") or get_col("merchant-sku"),
            "asin": get_col("asin"),
            "quantity_type": get_col("quantity-type"),
            "quantity": get_col("quantity"),
            "marketplace_id": get_col("marketplace-id"),
            "currency": get_col("currency"),
            "amount": get_col("amount"),
            "adjustment_id": get_col("adjustment-id"),
            "promotion_id": get_col("promotion-id"),
            "custom_text": get_col("custom-text"),
        })
    return summaries, order_lines


def aggregate_settlement_lines(order_lines: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Group settlement rows by item -> {order_id, sku, asin, units, revenue,
    fees, net, currency}.

    Grouping key: ``order_id + order_item_id + sku``. This is the stable
    invariant across an item's rows (sale, fee/adjustment, promo, refund all
    share order_item_id + sku; merchant ids differ per row type).

    Within a group:
      * units   = signed sum of the ``quantity`` column (only Quantity rows)
      * revenue = sum of amounts on Quantity rows (gross sales, refunds sign)
      * net     = sum across ALL rows of the group (fees/promos/tax incl.)
      * fees    = revenue - net  (recomputed so net == revenue - fees)
    """
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in order_lines:
        order_id = (row.get("order_id") or "").strip()
        item_id = (row.get("order_item_id") or "").strip()
        sku = (row.get("sku") or "").strip()
        if not order_id or not sku:
            continue
        key = (order_id, item_id, sku)
        amount = _to_float(row.get("amount")) or 0.0
        currency = (row.get("currency") or "").strip()
        g = groups.setdefault(
            key,
            {
                "order_id": order_id,
                "item_id": item_id,
                "seller_sku": sku,
                "asin": (row.get("asin") or "").strip(),
                "currency": currency,
                "quantity_units": 0.0,
                "quantity_amount": 0.0,
                "net": 0.0,
            },
        )
        # prefer the first non-empty values we saw
        if currency:
            g["currency"] = currency
        if not g["asin"] and (row.get("asin") or "").strip():
            g["asin"] = row["asin"].strip()
        qty_type = (row.get("quantity_type") or "").strip().lower()
        if qty_type == "quantity":
            g["quantity_units"] += _to_int(row.get("quantity"))
            g["quantity_amount"] += amount
        g["net"] += amount

    lines = []
    for key, g in groups.items():
        units = _to_int(g["quantity_units"])
        revenue = round(g["quantity_amount"], 2)
        net = round(g["net"], 2)
        lines.append({
            "order_id": g["order_id"],
            "sku": g["seller_sku"],
            "asin": g["asin"],
            "units": units,
            "revenue": revenue,
            "fees": round(revenue - net, 2),
            "net": net,
            "currency": g["currency"] or "USD",
        })
    return lines


# ---------------------------------------------------------------------------
# Polling + document access
# ---------------------------------------------------------------------------

async def poll_report(client: SpApiClient, report_id: str, attempts: int = 8, interval: float = 20.0) -> dict:
    """Poll getReport until DONE (raise on FATAL/CANCELLED, timeout otherwise)."""
    for _ in range(attempts):
        report = await client.get_report(report_id)
        status = (report.get("processingStatus") or "").upper()
        if status == "DONE":
            if not report.get("reportDocumentId") and not report.get("url"):
                raise ReportError(f"report {report_id} DONE without reportDocumentId")
            return report
        if status in ("FATAL", "CANCELLED"):
            raise ReportError(f"report {report_id} failed: {status}")
        await asyncio.sleep(interval)
    raise ReportTimeout(f"report {report_id} not ready after {attempts} polls")


def decrypt_document(content: bytes, details: dict | None) -> bytes:
    """Decrypt a downloaded report document (AES/GCM) when encryptionDetails are
    present; otherwise return the payload untouched (unit-test friendly)."""
    if not details:
        return content
    standard = (details.get("standard") or "").upper()
    if standard != "AES":
        raise ReportError(f"unsupported encryption standard: {standard}")
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover - layer guarantees it
        raise ReportError("cryptography not installed (worker layer)") from exc
    import boto3

    key = base64.b64decode(details.get("key", ""))
    if details.get("kmsKeyId"):
        response = boto3.client("kms").decrypt(CiphertextBlob=key)
        key = response["Plaintext"]
    iv = base64.b64decode(details.get("initializationVector") or "")
    return AESGCM(key).decrypt(iv, content, None)


def stage_to_s3(bucket: str, key: str, content: bytes) -> None:
    """Best-effort staging of raw report payloads for audit (S3 lifecycle)."""
    import boto3  # noqa: PLC0415

    boto3.client("s3").put_object(Bucket=bucket, Key=key, Body=content)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

@dataclass
class LoadResult:
    products: int = 0
    snapshots: int = 0
    inventory: int = 0
    settlements: int = 0
    lines: int = 0
    skipped: list[str] = field(default_factory=list)


async def load_inventory(session, rows: list[dict], marketplace_id: int, currency: str) -> LoadResult:
    """Products + per-market listed price snapshot + inventory quantity snap."""
    result = LoadResult()
    for r in rows:
        asin = (r.get("asin") or "").strip()
        if not asin:
            result.skipped.append(f"{r.get('sku')}: no ASIN")
            continue
        product_id = await upsert_product(session, asin, r.get("title") or "")
        result.products += 1
        price = r.get("price")
        session.add(
            PriceSnapshot(
                product_id=product_id,
                marketplace_id=marketplace_id,
                ours=True,
                price=price,
                buybox=price,
                currency=currency,
            )
        )
        session.add(Inventory(
            product_id=product_id,
            condition=(r.get("condition") or "New")[:30],
            quantity=r.get("quantity") or 0,
        ))
        result.snapshots += 1
        result.inventory += 1
    return result


async def load_settlements(
    session,
    seller_id: int,
    marketplace_id: int,
    summaries: list[dict[str, str]],
    lines: list[dict[str, str]],
    sku_to_asin: dict[str, str] | None = None,
) -> LoadResult:
    """Upsert settlement summary (idempotent by marketplace + deposit date) and
    aggregated lines. Lines without a resolvable product are skipped + counted."""
    sku_to_asin = sku_to_asin or {}
    result = LoadResult()
    for summary in summaries:
        posted = _to_date(summary.get("deposit"))
        if posted is None:
            result.skipped.append(f"{summary.get('settlement_id')}: bad deposit date")
            continue
        currency = summary.get("currency") or "USD"

        existing = (
            await session.execute(
                select(Settlement).where(
                    Settlement.seller_id == seller_id,
                    Settlement.marketplace_id == marketplace_id,
                    Settlement.posted_date == posted,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            result.skipped.append(f"{summary.get('settlement_id')}: already loaded")
            continue

        gross = _to_float(summary.get("total"))
        settlement = Settlement(
            seller_id=seller_id,
            marketplace_id=marketplace_id,
            posted_date=posted,
            gross=gross,
            net=gross,
            fees=0.0,
            currency=currency,
        )
        session.add(settlement)
        await session.flush()
        result.settlements += 1

        for line in aggregate_settlement_lines(lines):
            if line["order_id"] == "Order ID" or not line["order_id"]:
                continue
            asin = (line["asin"] or sku_to_asin.get(line["sku"]) or "").strip()
            if not asin:
                result.skipped.append(f"{line['sku']}: no product (asin) to attach")
                continue
            product_id = await upsert_product(session, asin)
            session.add(SettlementLine(
                settlement_id=settlement.id,
                product_id=product_id,
                asin=asin[:10],
                sku=(line["sku"] or "")[:80],
                order_id=line["order_id"][:40],
                units=line["units"],
                revenue=_to_float(line["revenue"]),
                fees=_to_float(line["fees"]),
                net=_to_float(line["net"]),
                currency=line["currency"] or currency,
            ))
            result.lines += 1
    return result


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

ClientBuilder = Callable[[Seller, Marketplace], Awaitable[Any]]


@dataclass
class ReportRun:
    market: str
    products: int = 0
    snapshots: int = 0
    inventory_rows: int = 0
    settlements: int = 0
    settlement_lines: int = 0
    skipped: list[str] = field(default_factory=list)
    error: str = ""


async def _fetch_report_doc(
    client: SpApiClient,
    report_type: str,
    start: str | None = None,
    end: str | None = None,
    attempts: int = 8,
    interval: float = 3.0,
) -> bytes:
    """createReport -> poll -> download (pre-signed URL) -> decrypt."""
    report_id = await client.create_report(report_type, start_date=start, end_date=end)
    report = await poll_report(client, report_id, attempts=attempts, interval=interval)
    doc_id = report.get("reportDocumentId")
    if not doc_id:
        raise ReportError(f"{report_type}: DONE without reportDocumentId")
    doc = await client.get_report_document(doc_id)
    url = doc.get("url")
    if not url:
        raise ReportError(f"{report_type}: report document without url")
    content = await client.download_document(url)
    return decrypt_document(content, doc.get("encryptionDetails"))


async def sync_reports_driver(
    session,
    client_builder: ClientBuilder,
    settlement_start: str | None = None,
    settlement_end: str | None = None,
    attempts: int = 8,
    interval: float = 3.0,
) -> list[ReportRun]:
    """Run inventory + settlement reports for every active seller, each market."""
    results: list[ReportRun] = []
    sellers = (await session.execute(select(Seller))).scalars().all()
    markets = (await session.execute(select(Marketplace).order_by(Marketplace.id))).scalars().all()

    for seller in sellers:
        creds = (
            await session.execute(select(SpApiCreds).where(SpApiCreds.seller_id == seller.id).limit(1))
        ).scalar_one_or_none()
        if creds is None or creds.status != "active":
            continue
        for market in markets:
            run = ReportRun(market=market.code)
            results.append(run)
            try:
                client = await client_builder(seller, market)

                inv_raw = await _fetch_report_doc(client, "GET_MERCHANT_LISTINGS_ALL_DATA", attempts=attempts, interval=interval)
                inv_rows = parse_inventory_tsv(inv_raw)
                loaded = await load_inventory(session, inv_rows, market.id, market.currency)
                run.products = loaded.products
                run.snapshots = loaded.snapshots
                run.inventory_rows = loaded.inventory
                run.skipped.extend(loaded.skipped)

                sku_to_asin = {r["sku"]: r["asin"] for r in inv_rows if r.get("sku") and r.get("asin")}

                set_raw = await _fetch_report_doc(
                    client,
                    "GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE_V2",
                    start=settlement_start, end=settlement_end,
                    attempts=attempts, interval=interval,
                )
                summaries, order_lines = parse_settlements_tsv(set_raw)
                loaded_set = await load_settlements(
                    session, seller.id, market.id, summaries, order_lines, sku_to_asin,
                )
                run.settlements = loaded_set.settlements
                run.settlement_lines = loaded_set.lines
                run.skipped.extend(loaded_set.skipped)
            except Exception as exc:
                run.error = f"{type(exc).__name__}: {exc}"
    return results