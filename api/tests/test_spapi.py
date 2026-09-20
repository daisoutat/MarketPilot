# -*- coding: utf-8 -*-
"""Unit tests for common.spapi: SigV4, auth providers, throttling."""
from __future__ import annotations

import pytest
from httpx import Request, Response

import common.spapi as spapi
from common.spapi import (
    Credentials,
    RateLimited,
    SPAPIKeyCredentials,
    SPAPILwaCredentials,
    SpApiClient,
    SpApiError,
)


# ------------------------------------------------------------------ SigV4
def test_sign_v4_shape():
    creds = Credentials(access_key="AKID", secret_key="secret", session_token="tok", region="us-east-1")
    headers = spapi.sign_v4("GET", "https://sellingpartnerapi-na.amazon.com/orders/v0/orders?MarketplaceIds=X", "us-east-1", creds)
    assert headers["x-amz-date"].startswith("20")
    assert headers["x-amz-security-token"] == "tok"
    assert "AWS4-HMAC-SHA256" in headers["Authorization"]
    assert "SignedHeaders=host;x-amz-date;x-amz-security-token" in headers["Authorization"]
    assert headers["host"] == "sellingpartnerapi-na.amazon.com"


def test_sign_v4_no_token():
    creds = Credentials(access_key="AKID", secret_key="secret", session_token=None)
    headers = spapi.sign_v4("GET", "https://sellingpartnerapi-na.amazon.com/orders/v0/orders", "us-east-1", creds)
    assert "x-amz-security-token" not in headers


# ------------------------------------------------------------- auth adapters
@pytest.mark.asyncio
async def test_key_provider_acquire(monkeypatch):
    async def fake_lwa(data):
        assert data["grant_type"] == "client_credentials"
        assert data["scope"] == "sellerapi"
        return "LWA-TOKEN"

    async def fake_sts(role_arn, web_identity_token, region, session_name):
        assert role_arn == "arn:aws:iam::123:role/sp"
        assert web_identity_token == "LWA-TOKEN"
        return Credentials(access_key="A", secret_key="S", session_token="T", region=region)

    monkeypatch.setattr(spapi, "_lwa_token", fake_lwa)
    monkeypatch.setattr(spapi, "_sts_assume", fake_sts)
    provider = SPAPIKeyCredentials("cid", "csecret", "arn:aws:iam::123:role/sp")
    creds = await provider.get()
    assert creds.access_key == "A"
    # second get() is cached (no extra acquisitions)
    await provider.get()


@pytest.mark.asyncio
async def test_lwa_provider_grant(monkeypatch):
    seen = {}

    async def fake_lwa(data):
        seen.update(data)
        return "REF"

    async def fake_sts(role_arn, web_identity_token, region, session_name):
        return Credentials("a", "b", "c", region)

    monkeypatch.setattr(spapi, "_lwa_token", fake_lwa)
    monkeypatch.setattr(spapi, "_sts_assume", fake_sts)
    provider = SPAPILwaCredentials("cid", "secret", "refresh-tok", "arn:role")
    await provider.get()
    assert seen["grant_type"] == "refresh_token"
    assert seen["refresh_token"] == "refresh-tok"


# ------------------------------------------------------------------ client
class _FakeSessionTransport:
    """httpx fake: returns responses from a queue then works."""

    def __init__(self, responses):
        self._responses = list(responses)

    def handle_request(self, request: Request):
        return self._responses.pop(0)


class _FakeAsyncClient:
    def __init__(self, responses):
        self._responses = list(responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, headers=None, content=b""):
        self._received = (method, url, headers, content)
        return self._responses.pop(0)


def _json_response(status: int, payload: dict) -> Response:
    return Response(status, json=payload)


async def _test_creds_factory():
    return Credentials(access_key="AK", secret_key="SK", session_token=None, region="us-east-1")


def _make_client():
    provider = SPAPIKeyCredentials("cid", "sec", "arn:role")
    return SpApiClient(provider, "ATVPDKIKX0DER", creds_factory=_test_creds_factory)


@pytest.mark.asyncio
async def test_request_success_and_pagination_params(monkeypatch):
    client = _make_client()

    async def fake_send(method, url, headers, payload):
        assert "Signature=" in headers["Authorization"]
        assert "MarketplaceIds=ATVPDKIKX0DER" in url
        return _json_response(200, {"Orders": [{"AmazonOrderId": "111"}], "NextToken": "tok"})

    monkeypatch.setattr(client, "_send", fake_send)
    orders, nxt = await client.get_orders()
    assert orders == [{"AmazonOrderId": "111"}]
    assert nxt == "tok"
    fixture = _make_client()
    monkeypatch.setattr(fixture, "_send", fake_send)
    orders, nxt = await fixture.get_orders(last_updated_after="2026-09-01T00:00:00Z")
    assert nxt == "tok"


@pytest.mark.asyncio
async def test_rate_limit_retry_then_success(monkeypatch):
    client = _make_client()
    calls = []

    async def fake_send(method, url, headers, payload):
        calls.append(url)
        if len(calls) == 1:
            return _json_response(429, {"errors": [{"code": "TooManyRequests", "message": "slow down"}]})
        return _json_response(200, {"Orders": []})

    monkeypatch.setattr(client, "_send", fake_send)
    orders, nxt = await client.get_orders()
    assert orders == [] and nxt is None
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_rate_limit_exhausts_raises(monkeypatch):
    provider = SPAPIKeyCredentials("cid", "sec", "arn:role")
    client = SpApiClient(provider, "ATVPDKIKX0DER", retries=1, creds_factory=_test_creds_factory)

    async def fake_send(method, url, headers, payload):
        return _json_response(429, {"errors": [{"code": "TooManyRequests", "message": "nope"}]})

    async def no_backoff(error, attempt):
        return None

    monkeypatch.setattr(client, "_send", fake_send)
    monkeypatch.setattr(client, "_backoff", no_backoff)
    with pytest.raises(RateLimited):
        await client.get_orders()


@pytest.mark.asyncio
async def test_400_raises_immediately(monkeypatch):
    client = _make_client()

    async def fake_send(method, url, headers, payload):
        return _json_response(400, {"errors": [{"code": "InvalidInput", "message": "bad param"}]})

    monkeypatch.setattr(client, "_send", fake_send)
    with pytest.raises(SpApiError) as ei:
        await client.get_orders()
    assert ei.value.status == 400
    assert ei.value.code == "InvalidInput"