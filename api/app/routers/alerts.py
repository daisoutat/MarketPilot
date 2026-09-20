"""Alerts + rules endpoints (Phase 4).

  GET      /api/v1/alerts            list alerts (filter by kind/market/status)
  POST     /api/v1/alerts/{id}/resolve   mark an alert resolved
  GET      /api/v1/alerts/rules      list detection rules (seeds defaults)
  POST     /api/v1/alerts/rules      create a rule
  PATCH    /api/v1/alerts/rules/{id} update threshold / cooldown / enabled
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.alerts import ensure_default_rules
from common.db import get_session
from common.models import Alert, AlertRule, Marketplace, Product
from ..auth import get_current_user

router = APIRouter()

VALID_KINDS = {"price_drop", "stock_out", "margin_erosion"}
DEV_SELLER_ID = 1


class RuleCreate(BaseModel):
    kind: str
    threshold: float
    cooldown_hours: int = Field(24, ge=1, le=8760)
    enabled: bool = True


class RuleUpdate(BaseModel):
    threshold: float | None = None
    cooldown_hours: int | None = Field(None, ge=1, le=8760)
    enabled: bool | None = None


def _row(r) -> dict:
    return {
        "id": r.Alert.id,
        "kind": r.Alert.kind,
        "severity": r.Alert.severity,
        "message": r.Alert.message,
        "asin": r.asin,
        "market": r.market,
        "meta": r.Alert.meta,
        "created_at": r.Alert.created_at.isoformat() if r.Alert.created_at else None,
        "resolved_at": r.Alert.resolved_at.isoformat() if r.Alert.resolved_at else None,
        "resolved_by": r.Alert.resolved_by or "",
    }


@router.get("/alerts")
async def list_alerts(
    kind: str | None = Query(None),
    market: str | None = Query(None),
    status: str = Query("open", pattern="^(open|resolved|all)$"),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict:
    stmt = (
        select(Alert, Product.asin, Marketplace.code.label("market"))
        .outerjoin(Product, Product.id == Alert.product_id)
        .outerjoin(Marketplace, Marketplace.id == Alert.marketplace_id)
    )
    if status == "open":
        stmt = stmt.where(Alert.resolved_at.is_(None))
    elif status == "resolved":
        stmt = stmt.where(Alert.resolved_at.isnot(None))
    if kind:
        stmt = stmt.where(Alert.kind == kind)
    if market:
        stmt = stmt.where(Marketplace.code == market.upper())
    rows = (await session.execute(stmt.order_by(Alert.created_at.desc()).limit(limit))).all()
    return {"status": status, "count": len(rows), "rows": [_row(r) for r in rows]}


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(
    alert_id: int,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict:
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="alert not found")
    alert.resolved_at = datetime.now(UTC)
    alert.resolved_by = user.get("email") or user.get("display_name") or ""
    alert.seen = True
    await session.flush()
    return {
        "id": alert.id,
        "resolved_at": alert.resolved_at.isoformat(),
        "resolved_by": alert.resolved_by,
    }


@router.get("/alerts/rules")
async def list_rules(
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict:
    await ensure_default_rules(session, DEV_SELLER_ID)
    rules = (
        await session.execute(
            select(AlertRule).where(AlertRule.seller_id == DEV_SELLER_ID).order_by(AlertRule.kind)
        )
    ).scalars().all()
    return {
        "rules": [
            {
                "id": r.id,
                "kind": r.kind,
                "threshold": float(r.threshold),
                "cooldown_hours": r.cooldown_hours,
                "enabled": r.enabled,
            }
            for r in rules
        ]
    }


@router.post("/alerts/rules")
async def create_rule(
    body: RuleCreate,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict:
    if body.kind not in VALID_KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {sorted(VALID_KINDS)}")
    rule = AlertRule(
        seller_id=DEV_SELLER_ID,
        kind=body.kind,
        threshold=body.threshold,
        cooldown_hours=body.cooldown_hours,
        enabled=body.enabled,
    )
    session.add(rule)
    await session.flush()
    return {
        "id": rule.id,
        "kind": rule.kind,
        "threshold": float(rule.threshold),
        "cooldown_hours": rule.cooldown_hours,
        "enabled": rule.enabled,
    }


@router.patch("/alerts/rules/{rule_id}")
async def update_rule(
    rule_id: int,
    body: RuleUpdate,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> dict:
    rule = await session.get(AlertRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="rule not found")
    if body.threshold is not None:
        rule.threshold = body.threshold
    if body.cooldown_hours is not None:
        rule.cooldown_hours = body.cooldown_hours
    if body.enabled is not None:
        rule.enabled = body.enabled
    await session.flush()
    return {
        "id": rule.id,
        "kind": rule.kind,
        "threshold": float(rule.threshold),
        "cooldown_hours": rule.cooldown_hours,
        "enabled": rule.enabled,
    }