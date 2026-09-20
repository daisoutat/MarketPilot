"""Amazon Selling Partner API (SP-API) — Phase 1.

Implements (behind one interface) the two authorization models:

1. **New key-based model** (`SPAPIKeyCredentials`): LWA *client_credentials*
   grant (scope=sellerapi) -> AssumeRoleWithWebIdentity to a role trusted by
   `sina.amazonaws.com` -> SigV4-signed calls. No per-merchant consent flow.
2. **Legacy LWA model** (`SPAPILwaCredentials`): LWA *refresh_token* grant ->
   AssumeRoleWithWebIdentity (role trusted by the app's sina identity) ->
   SigV4-signed calls.

Signing region for the NA marketplace endpoint is `us-east-1`, SigV4 service
`execute-api`.

Throttling: per-endpoint quotas are enforced server-side. We honor
`Retry-After` / `x-amzn-RateLimit-*` when present, otherwise exponential
backoff + full jitter; after `retries` attempts we raise `RateLimited` so the
worker's DLQ can take over.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import random
import time
import urllib.parse
from datetime import UTC, datetime
from typing import Any, NamedTuple

import httpx

LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
SP_API_SERVICE = "execute-api"
UA = "marketpilot/0.1 (sp-api-sync)"
_DEFAULT_TIMEOUT = 20.0


class SpApiError(Exception):
    def __init__(self, status: int, code: str = "", message: str = "", headers: Any = None):
        super().__init__(message or code)
        self.status = status
        self.code = code
        self.message = message or code
        self.headers = headers or {}


class RateLimited(SpApiError):
    pass


class CredentialError(Exception):
    pass


class Credentials(NamedTuple):
    access_key: str
    secret_key: str
    session_token: str | None
    region: str = "us-east-1"


# ---------------------------------------------------------------------------
# SigV4 (execute-api)
# ---------------------------------------------------------------------------

def _signing_key(secret_key: str, datestamp: str, region: str, service: str) -> bytes:
    def h(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    k_date = h(("AWS4" + secret_key).encode("utf-8"), datestamp)
    k_region = h(k_date, region)
    k_service = h(k_region, service)
    return h(k_service, "aws4_request")


def sign_v4(
    method: str,
    url: str,
    region: str,
    creds: Credentials,
    body: bytes = b"",
    extra_headers: dict[str, str] | None = None,
    service: str = SP_API_SERVICE,
) -> dict[str, str]:
    """Return the SigV4 Authorization / amz headers for one request.

    ``service`` default is the SP-API one (``execute-api``); PA-API passes
    ``ProductAdvertisingAPI``.
    """
    split = urllib.parse.urlsplit(url)
    host = split.netloc
    now = datetime.now(UTC)
    amzdate = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest()

    headers: dict[str, str] = {"host": host, "x-amz-date": amzdate}
    if creds.session_token:
        headers["x-amz-security-token"] = creds.session_token
    for k, v in (extra_headers or {}).items():
        headers[k.lower()] = v.strip()

    canonical_headers = "".join(
        f"{k}:{' '.join(headers[k].split())}\n" for k in sorted(headers)
    )
    signed_headers = ";".join(sorted(headers))
    canonical_request = "\n".join(
        [
            method.upper(),
            split.path or "/",
            split.query,
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )
    scope = f"{datestamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amzdate, scope, hashlib.sha256(canonical_request.encode()).hexdigest()]
    )
    signature = hmac.new(
        _signing_key(creds.secret_key, datestamp, region, service),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return {
        **headers,
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={creds.access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
    }


# ---------------------------------------------------------------------------
# LWA token bits
# ---------------------------------------------------------------------------

async def _lwa_token(data: dict[str, str], timeout: float = 10.0) -> str:
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(LWA_TOKEN_URL, data=data, headers={"User-Agent": UA})
    if resp.status_code != 200:
        raise CredentialError(
            f"LWA token failed ({resp.status_code}): {resp.text[:200]}"
        )
    token = resp.json().get("access_token")
    if not token:
        raise CredentialError("LWA token missing from response")
    return token


async def _sts_assume(role_arn: str, web_identity_token: str, region: str, session_name: str) -> Credentials:
    """boto3 STS AssumeRoleWithWebIdentity, thread-safe in async context."""

    def _call():
        import boto3

        sts = boto3.client("sts", region_name=region)
        resp = sts.assume_role_with_web_identity(
            RoleArn=role_arn,
            RoleSessionName=session_name,
            WebIdentityToken=web_identity_token,
            DurationSeconds=3600,
        )
        return resp["Credentials"]

    try:
        creds = await asyncio.to_thread(_call)
    except Exception as exc:  # boto3 botocore exceptions
        raise CredentialError(f"STS AssumeRoleWithWebIdentity failed: {exc}") from exc
    return Credentials(
        access_key=creds["AccessKeyId"],
        secret_key=creds["SecretAccessKey"],
        session_token=creds["SessionToken"],
        region=region,
    )


class CredentialProvider:
    """Base class. Subclasses expose `async get() -> Credentials`."""

    def __init__(self, region: str = "us-east-1", session_name: str = "marketpilot"):
        self.region = region
        self.session_name = session_name
        self._cached: Credentials | None = None
        self._expires_at = 0.0

    async def get(self) -> Credentials:
        if self._cached is not None and time.time() < self._expires_at - 300:
            return self._cached
        creds = await self._acquire()
        self._cached = creds
        self._expires_at = time.time() + 3300
        return creds

    async def _acquire(self) -> Credentials:  # pragma: no cover - abstract
        raise NotImplementedError
        # unavailable in abstract base; subclasses override


class SPAPIKeyCredentials(CredentialProvider):
    """New key-based model: client_credentials grant + sina web identity."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        role_arn: str,
        region: str = "us-east-1",
        session_name: str = "marketpilot",
    ):
        super().__init__(region, session_name)
        self.client_id = client_id
        self.client_secret = client_secret
        self.role_arn = role_arn

    async def _acquire(self) -> Credentials:
        token = await _lwa_token(
            {
                "grant_type": "client_credentials",
                "scope": "sellerapi",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }
        )
        return await _sts_assume(self.role_arn, token, self.region, self.session_name)


class SPAPILwaCredentials(CredentialProvider):
    """Legacy model: refresh_token grant + web identity on the seller's role."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        role_arn: str,
        region: str = "us-east-1",
        session_name: str = "marketpilot",
    ):
        super().__init__(region, session_name)
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.role_arn = role_arn

    async def _acquire(self) -> Credentials:
        token = await _lwa_token(
            {
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }
        )
        return await _sts_assume(self.role_arn, token, self.region, self.session_name)


RETRYABLE_STATUSES = frozenset({429, 500, 503})


class SpApiClient:
    """Thin, retrying, SigV4-signed SP-API client."""

    def __init__(
        self,
        provider: CredentialProvider,
        marketplace_id: str,
        base_url: str | None = None,
        region: str | None = None,
        retries: int = 3,
        timeout: float = _DEFAULT_TIMEOUT,
        creds_factory: Any = None,
    ):
        self.provider = provider
        self.marketplace_id = marketplace_id
        self.base_url = base_url or os.environ.get("MP_SP_BASE_URL") or "https://sellingpartnerapi-na.amazon.com"
        self.region = region or provider.region
        self.retries = retries
        self.timeout = timeout
        self.creds_factory = creds_factory or provider.get

    async def _request(self, method: str, path: str, params: dict | None = None, body: Any = None) -> dict:
        if params:
            url = self.base_url + path + "?" + urllib.parse.urlencode(params, doseq=True)
        else:
            url = self.base_url + path
        payload = json.dumps(body).encode("utf-8") if body is not None else b""

        last_error: SpApiError | None = None
        for attempt in range(self.retries + 1):
            creds = await self.creds_factory()
            headers = sign_v4(
                method,
                url,
                self.region,
                creds,
                body=payload,
                extra_headers={
                    "content-type": "application/json",
                    "accept": "application/json",
                    "user-agent": UA,
                },
            )
            resp = await self._send(method, url, headers, payload)
            if resp.status_code >= 200 and resp.status_code < 300:
                if not resp.content:
                    return {}
                decoded = resp.json()
                return decoded if isinstance(decoded, dict) else {"value": decoded}

            error = self._to_error(resp)
            if resp.status_code in RETRYABLE_STATUSES:
                last_error = error
                await self._backoff(error, attempt)
                continue
            raise error
        raise last_error or RateLimited(429, "TooManyRequests", "request failed after retries")

    async def _send(self, method: str, url: str, headers: dict, payload: bytes):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            return await client.request(method, url, headers=headers, content=payload)

    async def _backoff(self, error: SpApiError, attempt: int) -> None:
        wait = 1.0 * (2 ** attempt)
        headers = error.headers or {}
        retry_after = headers.get("retry-after") or headers.get("x-amzn-ratelimit-per-second")
        if retry_after:
            try:
                wait = float(retry_after)
            except (TypeError, ValueError):
                pass
        wait = min(wait, 30.0)
        await asyncio.sleep(random.uniform(wait * 0.5, wait * 1.2))

    @staticmethod
    def _to_error(resp) -> SpApiError:
        headers = {k.lower(): str(v) for k, v in resp.headers.items()}
        code, message = "", ""
        try:
            payload = resp.json()
            for err in payload.get("errors", []) if isinstance(payload, dict) else []:
                code = err.get("code", "") or code
                message = err.get("message", "") or message
        except Exception:
            pass
        cls = RateLimited if resp.status_code == 429 else SpApiError
        return cls(resp.status_code, code, message or resp.text[:300], headers)

    # -- Orders API (v0) -----------------------------------------------------
    async def get_orders(
        self,
        created_after: str | None = None,
        last_updated_after: str | None = None,
        statuses: list[str] | None = None,
        max_results: int = 50,
        next_token: str | None = None,
    ) -> tuple[list[dict], str | None]:
        params = {"MarketplaceIds": self.marketplace_id, "MaxResultsPerPage": str(max_results)}
        if created_after:
            params["CreatedAfter"] = created_after
        if last_updated_after:
            params["LastUpdatedAfter"] = last_updated_after
        if statuses:
            params["OrderStatuses"] = ",".join(statuses)
        if next_token:
            params["NextToken"] = next_token
        payload = await self._request("GET", "/orders/v0/orders", params=params)
        return payload.get("Orders") or payload.get("orders") or [], payload.get("NextToken")

    async def get_order_items(self, order_id: str) -> list[dict]:
        payload = await self._request(
            "GET", f"/orders/v0/orders/{urllib.parse.quote(order_id)}/orderItems"
        )
        return payload.get("OrderItems") or payload.get("orderItems") or []

    # -- Reports API v2021-06-30 ---------------------------------------------
    # Async report flow: createReport -> poll getReport -> getReportDocument
    # (pre-signed URL) -> download -> decrypt (AES/GCM) -> parse -> load.
    # Throttling is burst-prone, so retries/config lives in the worker polling
    # loop, not here.

    async def create_report(
        self,
        report_type: str,
        report_options: dict | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> str:
        body: dict[str, Any] = {
            "reportType": report_type,
            "marketplaceIds": [self.marketplace_id],
        }
        options: dict[str, Any] = dict(report_options or {})
        if start_date:
            options["dateStart"] = start_date
        if end_date:
            options["dateEnd"] = end_date
        if options:
            body["reportOptions"] = json.dumps(options)
        payload = await self._request("POST", "/reports/2021-06-30/reports", body=body)
        return payload.get("reportId") or payload.get("ReportId") or ""

    async def get_report(self, report_id: str) -> dict:
        payload = await self._request("GET", f"/reports/2021-06-30/reports/{urllib.parse.quote(report_id)}")
        return payload or {}

    async def get_report_document(self, document_id: str) -> dict:
        payload = await self._request(
            "GET", f"/reports/2021-06-30/documents/{urllib.parse.quote(document_id)}"
        )
        return payload or {}

    async def download_document(self, url: str) -> bytes:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            resp = await client.get(url)
        resp.raise_for_status()
        return resp.content