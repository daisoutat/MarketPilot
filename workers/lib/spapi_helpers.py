# -*- coding: utf-8 -*-
"""SP-API credential resolution + client building, shared by workers.

Resolution order per ``spapi_creds`` row:
  ``client_id_arn == 'env:SP_API'``  -> Lambda/ECS environment variables
    (``MP_SP_CLIENT_ID`` / ``MP_SP_CLIENT_SECRET`` / ``MP_SP_ROLE_ARN``);
  otherwise                           -> Secrets Manager JSON secret with the
    keys ``client_id``/``ClientId``, ``client_secret``/``ClientSecret``,
    ``role_arn``/``RoleArn`` (plus optional other secrets for legacy LWA).
"""
from __future__ import annotations

from common.config import get_settings
from common.models import Marketplace, SpApiCreds
from common.secretstore import get_secret, get_secret_dict
from common.spapi import SPAPILwaCredentials, SPAPIKeyCredentials, SpApiClient


def resolve_creds(creds_row: SpApiCreds) -> dict:
    """Return {'client_id','client_secret','role_arn','refresh_token'}."""
    settings = get_settings()
    if creds_row.client_id_arn == "env:SP_API":
        return {
            "client_id": settings.sp_client_id,
            "client_secret": settings.sp_client_secret,
            "role_arn": settings.sp_role_arn,
            "refresh_token": "",
        }
    payload = get_secret_dict(creds_row.client_id_arn)
    refresh_token = ""
    if creds_row.merchant_token_arn:
        try:
            refresh_token = get_secret(creds_row.merchant_token_arn)
        except Exception:
            token_payload = get_secret_dict(creds_row.merchant_token_arn)
            refresh_token = token_payload.get("refresh_token", "")
    return {
        "client_id": payload.get("client_id", "") or payload.get("ClientId", ""),
        "client_secret": payload.get("client_secret", "") or payload.get("ClientSecret", ""),
        "role_arn": payload.get("role_arn", "") or payload.get("RoleArn", "") or creds_row.iam_role_arn,
        "refresh_token": refresh_token,
    }


def creds_configured(creds_row: SpApiCreds) -> tuple[bool, str]:
    """(usable?, reason). Never raises: Secrets Manager failures degrade to a
    recorded skip instead of a hard Lambda failure."""
    try:
        creds = resolve_creds(creds_row)
    except Exception as exc:
        return False, f"credential fetch failed: {exc}"
    if not (creds["client_id"] and creds["client_secret"]):
        return False, "credentials not configured"
    if creds_row.auth_model == "lwa" and not creds["refresh_token"]:
        return False, "LWA model requires a refresh_token"
    return True, ""


def build_spapi_client(creds_row: SpApiCreds, market: Marketplace) -> SpApiClient:
    settings = get_settings()
    creds = resolve_creds(creds_row)
    if creds_row.auth_model == "lwa":
        provider = SPAPILwaCredentials(
            creds["client_id"], creds["client_secret"], creds["refresh_token"],
            creds["role_arn"], region=settings.sp_region,
        )
    else:
        provider = SPAPIKeyCredentials(
            creds["client_id"], creds["client_secret"], creds["role_arn"],
            region=settings.sp_region,
        )
    return SpApiClient(
        provider, market.marketplace_id,
        base_url=settings.sp_base_url, region=settings.sp_region,
    )