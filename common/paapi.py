"""Amazon Product Advertising API 5.0 (PA-API) — Phase 3.

PA-API is used for market research on ASINs *we do not necessarily sell*
(non-owned), snapshot-only so history lives in `price_snapshots`. It requires
an Associates account (Partner Tag) and SigV4 signing against the per-country
host (`webservices.amazon.com` / `webservices.amazon.ca`).

One partner tag is typically sufficient for both US and CA markets; the
`Marketplace` value in the request body tells PA-API which store to search.

Throttling: PA-API enforces per-account rate limits. We honor HTTP 429/5xx with
retry + jittered backoff, mirroring the SP-API client behaviour.
"""
from __future__ import annotations

import json
import random
import time
from typing import Any

import httpx

from .spapi import Credentials, RETRYABLE_STATUSES, RateLimited, sign_v4

PA_API_SERVICE = "ProductAdvertisingAPI"
UA = "marketpilot/0.3 (pa-api-research)"
_DEFAULT_TIMEOUT = 12.0

# market code -> (endpoint host, PA-API Marketplace string)
MARKETPLACES: dict[str, tuple[str, str]] = {
    "US": ("webservices.amazon.com", "www.amazon.com"),
    "CA": ("webservices.amazon.ca", "www.amazon.ca"),
}

RESOURCES = [
    "BrowseNodeInfo.BrowseNodes",
    "Images.Primary.Large",
    "ItemInfo.Title",
    "Offers.Listings.Price",
    "Offers.Listings.Availability.MaxOrderQuantity",
    "Offers.Listings.DeliveryInfo.IsAmazonFulfilled",
    "Offers.Summaries.LowestPrice",
    "Offers.Summaries.BuyBoxPrice",
    "Offers.Summaries.ListPrice",
]


class PaApiError(Exception):
    def __init__(self, status: int, code: str = "", message: str = "", headers: Any = None):
        super().__init__(message or code)
        self.status = status
        self.code = code
        self.message = message or code
        self.headers = headers or {}


class PaApiClient:
    """SigV4-signed PA-API 5.0 client (SearchItems / GetItems)."""

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        partner_tag: str,
        market_code: str,
        region: str = "us-east-1",
        retries: int = 3,
        timeout: float = _DEFAULT_TIMEOUT,
        base_url: str | None = None,
        resources: list[str] | None = None,
    ):
        host, marketplace = MARKETPLACES[market_code.upper()]
        self.base_url = (base_url or f"https://{host}").rstrip("/")
        self.marketplace = marketplace
        self.partner_tag = partner_tag
        self.region = region
        self.retries = retries
        self.timeout = timeout
        self.resources = list(resources or RESOURCES)
        self.creds = Credentials(access_key, secret_key, None, region)

    # -- network ------------------------------------------------------------
    async def _call(self, operation: str, payload: dict) -> dict:
        url = f"{self.base_url}/paapi5/{operation.lower()}"
        body = json.dumps(payload).encode("utf-8")

        last_error: PaApiError | None = None
        for attempt in range(self.retries + 1):
            headers = sign_v4(
                "POST",
                url,
                self.region,
                self.creds,
                body=body,
                extra_headers={
                    "content-type": "application/json;charset=UTF-8",
                    "accept": "application/json",
                    "user-agent": UA,
                    "x-amz-target": f"com.amazon.paapi5.v1.ProductAdvertisingAPIv1.{operation}",
                },
                service=PA_API_SERVICE,
            )
            resp = await self._send(url, body, headers)
            if resp.status_code >= 200 and resp.status_code < 300:
                return self._decode(resp)
            error = self._to_error(resp)
            if resp.status_code in RETRYABLE_STATUSES:
                last_error = error
                await self._backoff(resp, attempt)
                continue
            raise error
        raise last_error or RateLimited(429, "TooManyRequests", "PA-API request failed after retries")

    async def _send(self, url: str, body: bytes, headers: dict):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            return await client.post(url, headers=headers, content=body)

    async def _backoff(self, resp, attempt: int) -> None:
        wait = 1.0 * (2 ** attempt)
        retry_after = resp.headers.get("retry-after") or resp.headers.get("x-amzn-ratelimit-per-second")
        if retry_after:
            try:
                wait = float(retry_after)
            except (TypeError, ValueError):
                pass
        wait = min(wait, 20.0)
        await self._sleep(wait)

    async def _sleep(self, wait: float) -> None:  # overridable for tests
        time.sleep(random.uniform(wait * 0.5, wait))

    @staticmethod
    def _decode(resp) -> dict:
        try:
            payload = resp.json()
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {"value": payload}

    @staticmethod
    def _to_error(resp) -> PaApiError:
        headers = {k.lower(): str(v) for k, v in resp.headers.items()}
        code, message = "", ""
        try:
            payload = resp.json()
            errs = payload.get("Errors") or payload.get("errors") or []
            for err in errs if isinstance(errs, list) else []:
                code = err.get("Code", "") or code
                message = err.get("Message", "") or message
        except Exception:
            pass
        return PaApiError(resp.status_code, code, message or resp.text[:300], headers)

    # -- operations ----------------------------------------------------------
    def _base_payload(self, **extra) -> dict:
        return {
            "PartnerType": "Associates",
            "Marketplace": self.marketplace,
            "PartnerTag": self.partner_tag,
            "Resources": self.resources,
            **extra,
        }

    async def search_items(self, keywords: str, item_count: int = 10) -> list[dict]:
        payload = await self._call("SearchItems", self._base_payload(Keywords=keywords, ItemCount=item_count, SearchIndex="All"))
        return (payload.get("SearchResult") or {}).get("Items") or []

    async def get_items(self, item_ids: list[str]) -> list[dict]:
        if not item_ids:
            return []
        payload = await self._call(
            "GetItems", self._base_payload(ItemIds=item_ids[:10], ItemsCount=len(item_ids[:10]))
        )
        return (payload.get("ItemsResult") or {}).get("Items") or []

    # -- normalization ---------------------------------------------------------
    @staticmethod
    def normalize_item(raw: dict) -> dict | None:
        asin = (raw.get("ASIN") or "").strip()
        if not asin:
            return None
        title = (((raw.get("ItemInfo") or {}).get("Title") or {}).get("DisplayValue") or "").strip()
        price, price_currency, buybox = None, "", None

        offers = raw.get("Offers") or {}
        listings = offers.get("Listings") or []
        if listings:
            price_obj = (listings[0].get("Price") or {})
            amount = price_obj.get("Amount")
            if isinstance(amount, (int, float)):
                price = round(amount / 100.0, 2)
                price_currency = price_obj.get("Currency") or ""
        summaries = offers.get("Summaries") or []
        if summaries and summaries[0]:
            s = summaries[0]
            lowest = s.get("LowestPrice") or {}
            if price is None or not price_currency:
                from_lowest = _parse_display_amount(lowest.get("DisplayAmount"))
                if from_lowest:
                    price, price_currency = from_lowest
            bb = s.get("BuyBoxPrice") or lowest
            if not buybox:
                from_bb = _parse_display_amount(bb.get("DisplayAmount"))
                if from_bb:
                    buybox = from_bb[0]

        nodes = ((raw.get("BrowseNodeInfo") or {}).get("BrowseNodes")) or []
        sales_rank = nodes[0].get("SalesRank") if nodes else None
        image = ((raw.get("Images") or {}).get("Primary") or {}).get("Large", {}).get("URL", "")

        return {
            "asin": asin,
            "title": title,
            "price": price,
            "currency": price_currency or "USD",
            "buybox": buybox,
            "sales_rank": int(sales_rank) if isinstance(sales_rank, int) else None,
            "image_url": image,
        }


def _parse_display_amount(value: Any) -> tuple[float, str] | None:
    if not value:
        return None
    text = str(value).strip()
    parts = text.split()
    if len(parts) == 2:
        try:
            return round(float(parts[1]), 2), parts[0]
        except ValueError:
            return None
    try:
        return round(float(text), 2), "USD"
    except ValueError:
        return None