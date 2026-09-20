"""alerts engine: rule tables + alert enrichment + webhook audit log

Revision ID: 0005_alerts
Revises: 0004_research
Create Date: 2026-09-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_alerts"
down_revision = "0004_research"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("seller_id", sa.Integer(), sa.ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("threshold", sa.Numeric(12, 2), nullable=False),
        sa.Column("cooldown_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_alert_rules_seller", "alert_rules", ["seller_id", "kind"])

    op.add_column("alerts", sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=True))
    op.add_column("alerts", sa.Column("marketplace_id", sa.Integer(), sa.ForeignKey("marketplaces.id"), nullable=True))
    op.add_column("alerts", sa.Column("meta", sa.JSON(), nullable=True))
    op.add_column("alerts", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("alerts", sa.Column("resolved_by", sa.String(128), nullable=False, server_default=""))
    op.create_index("idx_alerts_open", "alerts", ["kind", "resolved_at"])
    op.create_index("idx_alerts_created", "alerts", ["created_at"])

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.String(128), nullable=False, unique=True),
        sa.Column("notification_type", sa.String(80), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_webhook_events_received", "webhook_events", ["received_at"])


def downgrade() -> None:
    op.drop_index("idx_webhook_events_received", table_name="webhook_events")
    op.drop_table("webhook_events")

    op.drop_index("idx_alerts_created", table_name="alerts")
    op.drop_index("idx_alerts_open", table_name="alerts")
    op.drop_column("alerts", "resolved_by")
    op.drop_column("alerts", "resolved_at")
    op.drop_column("alerts", "meta")
    op.drop_column("alerts", "marketplace_id")
    op.drop_column("alerts", "product_id")

    op.drop_index("idx_alert_rules_seller", table_name="alert_rules")
    op.drop_table("alert_rules")