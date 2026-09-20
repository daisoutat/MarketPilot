# -*- coding: utf-8 -*-
"""PA-API 5.0 client tests (mocked transport — no network)."""
from __future__ import annotations

import json

import pytest

from common.paapi import PaApiClient, PaApiError
from common.paapi import _parse_display_amount


def make_item(asin: str, title: str | None = None, amount=None,
              currency="USD", rank=None, image=""):
    item = {"ASIN": asin}
    if title is not None:
        item["ItemInfo"] = {"Title": {"DisplayValue": title}}
    if amount is not None:
        item["Offers"] = {
            "Listings": [
                {"Price": {"Amount": int(round(amount * 100)), "Currency": currency},
                 "DeliveryInfo": {"IsAmazonFulfilled": True}}
            ],
            "Summaries": [
                {"LowestPrice": {"DisplayAmount": f"{currency} {amount}"},
                 "BuyBoxPrice": {"DisplayAmount": f"{currency} {amount}"}}
            ],
        }
    if rank is not None:
        item["BrowseNodeInfo"] = {"BrowseNodes": [{"SalesRank": rank}]}
    if image:
        item["Images"] = {"Primary": {"Large": {"URL": image}}}
    return item


class FakeResp:
    def __init__(self, payload, status=200, headers=None):
        self.payload = payload
        self.status_code = status
        self.headers = {k.lower(): str(v) for k, v in (headers or {}).items()}
        self.text = json.dumps(payload)

    def json(self):
        return self.payload


class FakePaClient(PaApiClient):
    """Replaces _send/_sleep so tests never touch the network."""

    def __init__(self, search_map=None, items_by_asin=None, failures=None):
        super().__init__("TESTAK", "TESTSK", "mytag-20", "US")
        self.search_map = search_map or {}
        self.items_by_asin = items_by_asin or {}
        self.failures = list(failures or [])
        self.calls = []
        self.slept = []

    async def _sleep(self, wait):
        self.slept.append(wait)

    async def _send(self, url, body, headers):
        self.calls.append((url, dict(headers), json.loads(body)))
        if self.failures:
            status, payload = self.failures.pop(0)
            return FakeResp(payload, status=status, headers={"retry-after": "1"})
        payload = json.loads(body)
        if "Keywords" in payload:
            return FakeResp({"SearchResult": {"Items": self.search_map.get(payload["Keywords"], [])}})
        ids = payload.get("ItemIds") or []
        items = [self.items_by_asin[i] for i in ids if i in self.items_by_asin]
        return FakeResp({"ItemsResult": {"Items": items}})


# -- normalization ----------------------------------------------------------

def test_normalize_full_item():
    item = make_item("B0001", title="Coffee Bottle 250g", amount=19.99, rank=412, image="https://img/x.jpg")
    n = PaApiClient.normalize_item(item)
    assert n is not None
    assert n["asin"] == "B0001"
    assert n["title"] == "Coffee Bottle 250g"
    assert n["price"] == 19.99
    assert n["currency"] == "USD"
    assert n["buybox"] == 19.99
    assert n["sales_rank"] == 412
    assert n["image_url"] == "https://img/x.jpg"


def test_normalize_no_offers():
    item = make_item("B0002", title="Out of Stock Widget")
    n = PaApiClient.normalize_item(item)
    assert n["asin"] == "B0002"
    assert n["price"] is None
    assert n["currency"] == "USD"
    assert n["buybox"] is None
    assert n["sales_rank"] is None


def test_normalize_missing_asin():
    assert PaApiClient.normalize_item({}) is None


def test_parse_display_amount():
    assert _parse_display_amount("CAD 12.34") == (12.34, "CAD")
    assert _parse_display_amount(None) is None


# -- signing / request shape -------------------------------------------------

@pytest.mark.asyncio
async def test_search_items_signing_and_payload():
    client = FakePaClient(search_map={"coffee": [make_item("B0001", "Coffee", 19.99)]})
    items = await client.search_items("coffee")
    assert len(items) == 1
    url, headers, payload = client.calls[0]
    assert url.endswith("/paapi5/searchitems")
    _, headers, _ = client.calls[0]
    assert headers["x-amz-target"].endswith("SearchItems")
    assert headers["x-amz-target"] == "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.SearchItems"
    assert "AWS4-HMAC-SHA256" in headers["Authorization"]
    assert "ProductAdvertisingAPI" in headers["Authorization"]
    assert payload == {
        "PartnerType": "Associates",
        "Marketplace": "www.amazon.com",
        "PartnerTag": "mytag-20",
        "Resources": client.resources,
        "Keywords": "coffee",
        "ItemCount": 10,
        "SearchIndex": "All",
    }


@pytest.mark.asyncio
async def test_get_items_round_trip():
    client = FakePaClient(items_by_asin={
        "B0001": make_item("B0001", "A", 10.0),
        "B0002": make_item("B0002", "B", 11.0),
    })
    items = await client.get_items(["B0001", "B0002", "B0999"])
    assert [i["ASIN"] for i in items] == ["B0001", "B0002"]
    _, headers, _ = client.calls[0]
    assert headers["x-amz-target"].endswith("GetItems")
    assert headers["x-amz-target"] == "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.GetItems"


@pytest.mark.asyncio
async def test_retries_on_throttle_then_succeeds():
    client = FakePaClient(search_map={"coffee": [make_item("B0001", "Coffee", 10.0)]},
                          failures=[(429, {"Errors": [{"Code": "TooManyRequests", "Message": "slow down"}]})])
    items = await client.search_items("coffee")
    assert len(items) == 1
    assert len(client.calls) == 2
    assert client.slept == [1.0]  # retry-after honored


@pytest.mark.asyncio
async def test_raises_on_permanent_error():
    client = FakePaClient(failures=[(400, {"Errors": [{"Code": "InvalidParameter", "Message": "bad kw"}]})])
    with pytest.raises(PaApiError) as exc:
        await client.search_items("coffee")
    assert exc.value.status == 400
    assert exc.value.code == "InvalidParameter"


@pytest.mark.asyncio
async def test_marketplace_map_ca():
    client = FakePaClient()
    assert client.marketplace == "www.amazon.com"
    ca = PaApiClient("AK", "SK", "tag", "CA")
    assert ca.marketplace == "www.amazon.ca"
    assert "webservices.amazon.ca" in ca.base_url