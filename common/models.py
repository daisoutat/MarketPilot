"""Async SQLAlchemy models (shared by API and workers).

Design notes:
- All money stored as Numeric(12,2) with an explicit `currency` column.
- JSON payloads kept in JSON for flexible vendor payloads.
- `pipeline_runs` doubles as the audit/telemetry channel for workers.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# NOTE: generic `JSON` keeps the model portable (SQLite for tests, Postgres in
# prod via migrations which use JSON where it matters).


class Base(AsyncAttrs, DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def make_engine(database_url: str):
    return create_async_engine(database_url, pool_pre_ping=True)


def make_sessionmaker(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Reference / config
# ---------------------------------------------------------------------------

class Marketplace(Base):
    __tablename__ = "marketplaces"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(3), unique=True, nullable=False)  # US / CA
    marketplace_id: Mapped[str] = mapped_column(String(14), unique=True, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)  # USD / CAD
    iso: Mapped[str] = mapped_column(String(7), nullable=False)  # en-US / fr-CA


class Seller(Base, TimestampMixin):
    __tablename__ = "sellers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    story: Mapped[str] = mapped_column(String(500), default="")
    credentials_ref: Mapped[str] = mapped_column(String(300), nullable=False)  # Secrets Manager ARN ("env" = env vars)


class SpApiCreds(Base):
    __tablename__ = "spapi_creds"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("sellers.id", ondelete="CASCADE"))
    auth_model: Mapped[str] = mapped_column(String(20), nullable=False)  # key | lwa
    client_id_arn: Mapped[str] = mapped_column(String(300), default="")  # ARN, or "env:SP_API"
    iam_role_arn: Mapped[str] = mapped_column(String(300), default="")
    merchant_token_arn: Mapped[str] = mapped_column(String(300), default="")  # LWA refresh token (arn) for lwa mode
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|active|error


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class NotifSubscription(Base, TimestampMixin):
    __tablename__ = "notif_subscriptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("sellers.id", ondelete="CASCADE"))
    notification_type: Mapped[str] = mapped_column(String(80), nullable=False)  # ORDER_CHANGE ...
    destination_arn: Mapped[str] = mapped_column(String(300), nullable=False)  # SQS queue ARN
    status: Mapped[str] = mapped_column(String(20), default="active")


class ResearchTarget(Base, TimestampMixin):
    """An ASIN we track for market research (owned or external)."""
    __tablename__ = "research_targets"
    __table_args__ = (
        UniqueConstraint("marketplace_id", "asin", name="uq_research_target"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    marketplace_id: Mapped[int] = mapped_column(ForeignKey("marketplaces.id"))
    asin: Mapped[str] = mapped_column(String(10), nullable=False)
    keywords: Mapped[str] = mapped_column(String(200), default="")  # source search term
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_snap_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class FxRate(Base):
    """Cached exchange rate (base -> quote), refreshed on TTL."""
    __tablename__ = "fx_rates"
    base: Mapped[str] = mapped_column(String(3), primary_key=True)
    quote: Mapped[str] = mapped_column(String(3), primary_key=True)
    rate: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False)
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AppUser(Base):
    """Profile linked to a Cognito `sub`. Auth itself is Cognito's concern."""
    __tablename__ = "app_users"
    sub: Mapped[str] = mapped_column(String(128), primary_key=True)
    email: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120), default="")
    role: Mapped[str] = mapped_column(String(20), default="viewer")  # admin|analyst|viewer
    prefs: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=dict)


# ---------------------------------------------------------------------------
# Seller analytics (SP-API)
# ---------------------------------------------------------------------------

class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asin: Mapped[str] = mapped_column(String(10), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, default="")
    brand: Mapped[str] = mapped_column(String(120), default="")
    image_url: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(120), default="")
    family_id: Mapped[Optional[int]] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"))
    attributes: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=dict)


class Category(Base):
    """Product taxonomy: category → niche → family (3 levels, self-referencing).

    `kind` mirrors the depth: "category" (root), "niche", "family" (leaf that
    products link to via `Product.family_id`). Populated by the taxonomy worker
    using explainable keyword/brand rules (`common.categorize`)."""
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("marketplace_id", "parent_id", "name", name="uq_category_path"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    marketplace_id: Mapped[int] = mapped_column(ForeignKey("marketplaces.id"))
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # category | niche | family


class Forecast(Base):
    """Explainable demand forecast per product (generated nightly).

    `params` keeps the model explanation (level, weekly trend, seasonal factors,
    rmse, volatility); `points` holds the horizon series with 80% bounds; the
    latest row per product is what the API serves."""
    __tablename__ = "forecasts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    model: Mapped[str] = mapped_column(String(40), default="seasonal-ols")  # seasonal + OLS trend (explainable)
    unit: Mapped[str] = mapped_column(String(20), default="units/day")
    window_days: Mapped[int] = mapped_column(Integer, default=90)
    horizon: Mapped[int] = mapped_column(Integer, default=30)
    params: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=dict)  # explanation
    history: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=dict)   # {dates, values}
    points: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=dict)    # {dates, yhat, lo, hi}
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    marketplace_id: Mapped[int] = mapped_column(ForeignKey("marketplaces.id"))
    ours: Mapped[bool] = mapped_column(Boolean, default=True)  # owned ASIN or market research
    price: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    buybox: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    sales_rank: Mapped[Optional[int]] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Order(Base, TimestampMixin):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("sellers.id", ondelete="CASCADE"))
    marketplace_id: Mapped[int] = mapped_column(ForeignKey("marketplaces.id"))
    amazon_order_id: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    purchase_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    latest_delivery_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="Pending")
    buyer_name: Mapped[str] = mapped_column(String(120), default="")
    city: Mapped[str] = mapped_column(String(120), default="")
    state_or_region: Mapped[str] = mapped_column(String(120), default="")
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), onupdate=func.now())


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (UniqueConstraint("order_id", "asin", "seller_sku", name="uq_order_item"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    asin: Mapped[str] = mapped_column(String(10), nullable=False)
    seller_sku: Mapped[str] = mapped_column(String(80), default="")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_price: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    item_currency: Mapped[str] = mapped_column(String(3), nullable=False)


class Fee(Base):
    __tablename__ = "fees"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_item_id: Mapped[int] = mapped_column(ForeignKey("order_items.id", ondelete="CASCADE"))
    fee_type: Mapped[str] = mapped_column(String(80), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)


class Settlement(Base, TimestampMixin):
    __tablename__ = "settlements"
    __table_args__ = (
        UniqueConstraint("marketplace_id", "posted_date", name="uq_settlement_period"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("sellers.id", ondelete="CASCADE"))
    marketplace_id: Mapped[int] = mapped_column(ForeignKey("marketplaces.id"))
    posted_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    gross: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    fees: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    net: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)


class SettlementLine(Base):
    """Per item line aggregated from a settlement flat-file (margin source)."""
    __tablename__ = "settlement_lines"
    __table_args__ = (
        UniqueConstraint("settlement_id", "order_id", "sku", name="uq_settlement_line"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    settlement_id: Mapped[int] = mapped_column(ForeignKey("settlements.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    asin: Mapped[str] = mapped_column(String(10), default="")
    sku: Mapped[str] = mapped_column(String(80), default="")
    order_id: Mapped[str] = mapped_column(String(40), default="")
    units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revenue: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    fees: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    net: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)


class Inventory(Base):
    __tablename__ = "inventory"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    condition: Mapped[str] = mapped_column(String(30), default="New")
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    snapped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# Pipelines / telemetry / alerts
# ---------------------------------------------------------------------------

class Report(Base, TimestampMixin):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_kind: Mapped[str] = mapped_column(String(80), nullable=False)  # GET_MERCHANT_LISTINGS_ALL_DATA ...
    marketplace_id: Mapped[int] = mapped_column(ForeignKey("marketplaces.id"))
    report_id: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(30), default="requested")  # requested|processed|done|error
    s3_key: Mapped[str] = mapped_column(Text, default="")


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job: Mapped[str] = mapped_column(String(60), nullable=False)  # orders-sync, price-snapshot ...
    marketplace_code: Mapped[str] = mapped_column(String(3), default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    started: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    rows: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")


class Alert(Base, TimestampMixin):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)  # price_drop, stock_out, margin_erosion ...
    severity: Mapped[str] = mapped_column(String(10), default="info")  # info|warning|critical
    ref_type: Mapped[str] = mapped_column(String(30), default="")
    ref_id: Mapped[int] = mapped_column(Integer, default=0)
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    marketplace_id: Mapped[Optional[int]] = mapped_column(ForeignKey("marketplaces.id"))
    message: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=dict)
    seen: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[str] = mapped_column(String(128), default="")


class AlertRule(Base, TimestampMixin):
    """A tunable detection rule (per seller); evaluated by the alerts-eval worker."""
    __tablename__ = "alert_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("sellers.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(40), nullable=False)  # price_drop | stock_out | margin_erosion
    threshold: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    cooldown_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class WebhookEvent(Base):
    """Idempotent audit log of inbound SP-API notification events (SQS consumer)."""
    __tablename__ = "webhook_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    notification_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=dict)
    processed: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatMessage(Base, TimestampMixin):
    """One persisted exchange of the dashboard assistant (session-scoped).

    `role` is user|assistant; `intent` names the detected NLU intent; `data`
    carries a small structured payload (kpis, rows, chart series) that the SPA
    renders inline next to the natural-language `text`."""
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant
    intent: Mapped[str] = mapped_column(String(60), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=dict)