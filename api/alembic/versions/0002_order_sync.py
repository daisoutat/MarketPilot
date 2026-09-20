"""order sync support + demo seller

Revision ID: 0002_order_sync
Revises: 0001_initial
Create Date: 2026-09-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_order_sync"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Orders: track last sync + index for dashboard series.
    op.add_column("orders", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("idx_orders_purchase_date", "orders", ["purchase_date"])

    # Order items: unique per (order, asin, sku) so re-syncs stay idempotent.
    op.create_unique_constraint("uq_order_item", "order_items", ["order_id", "asin", "seller_sku"])

    # Pipeline telemetry: market as code (simpler for workers).
    op.drop_column("pipeline_runs", "marketplace_id")
    op.add_column(
        "pipeline_runs",
        sa.Column("marketplace_code", sa.String(3), nullable=False, server_default=""),
    )

    # Demo seller wired to environment variables (`env:SP_API` marker).
    op.execute(
        "INSERT INTO sellers (name, story, credentials_ref, created_at) "
        "VALUES ('Demo Seller', 'Environment-wired SP-API credentials', 'env:SP_API', now())"
    )
    op.execute(
        "INSERT INTO spapi_creds (seller_id, auth_model, client_id_arn, iam_role_arn, merchant_token_arn, status) "
        "SELECT id, 'key', 'env:SP_API', '', '', 'active' FROM sellers WHERE name = 'Demo Seller'"
    )


def downgrade() -> None:
    op.execute("UPDATE orders SET updated_at = NULL")
    op.drop_index("idx_orders_purchase_date", table_name="orders")
    op.drop_column("orders", "updated_at")
    op.drop_constraint("uq_order_item", "order_items", type_="unique")
    op.add_column(
        "pipeline_runs",
        sa.Column("marketplace_id", sa.Integer(), sa.ForeignKey("marketplaces.id"), nullable=True),
    )
    op.drop_column("pipeline_runs", "marketplace_code")
    op.execute("DELETE FROM spapi_creds WHERE client_id_arn = 'env:SP_API'")
    op.execute("DELETE FROM sellers WHERE name = 'Demo Seller'")