"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "marketplaces",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(3), nullable=False, unique=True),
        sa.Column("marketplace_id", sa.String(14), nullable=False, unique=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("iso", sa.String(7), nullable=False),
    )
    op.create_table(
        "sellers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("story", sa.String(500), nullable=False, server_default=""),
        sa.Column("credentials_ref", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "spapi_creds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("seller_id", sa.Integer(), sa.ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("auth_model", sa.String(20), nullable=False),
        sa.Column("client_id_arn", sa.String(300), nullable=False, server_default=""),
        sa.Column("iam_role_arn", sa.String(300), nullable=False, server_default=""),
        sa.Column("merchant_token_arn", sa.String(300), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
    )
    op.create_table(
        "settings",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
    )
    op.create_table(
        "notif_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("seller_id", sa.Integer(), sa.ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("notification_type", sa.String(80), nullable=False),
        sa.Column("destination_arn", sa.String(300), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "app_users",
        sa.Column("sub", sa.String(128), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, server_default=""),
        sa.Column("display_name", sa.String(120), nullable=False, server_default=""),
        sa.Column("role", sa.String(20), nullable=False, server_default="viewer"),
        sa.Column("prefs", JSONB(), nullable=True),
    )
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("asin", sa.String(10), nullable=False, unique=True),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("brand", sa.String(120), nullable=False, server_default=""),
        sa.Column("image_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("category", sa.String(120), nullable=False, server_default=""),
        sa.Column("attributes", JSONB(), nullable=True),
    )
    op.create_table(
        "price_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("marketplace_id", sa.Integer(), sa.ForeignKey("marketplaces.id"), nullable=False),
        sa.Column("ours", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("price", sa.Numeric(12, 2), nullable=True),
        sa.Column("buybox", sa.Numeric(12, 2), nullable=True),
        sa.Column("sales_rank", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("seller_id", sa.Integer(), sa.ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("marketplace_id", sa.Integer(), sa.ForeignKey("marketplaces.id"), nullable=False),
        sa.Column("amazon_order_id", sa.String(40), nullable=False, unique=True),
        sa.Column("purchase_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_delivery_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="Pending"),
        sa.Column("buyer_name", sa.String(120), nullable=False, server_default=""),
        sa.Column("city", sa.String(120), nullable=False, server_default=""),
        sa.Column("state_or_region", sa.String(120), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "order_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("asin", sa.String(10), nullable=False),
        sa.Column("seller_sku", sa.String(80), nullable=False, server_default=""),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("item_currency", sa.String(3), nullable=False),
    )
    op.create_table(
        "fees",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_item_id", sa.Integer(), sa.ForeignKey("order_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fee_type", sa.String(80), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
    )
    op.create_table(
        "settlements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("seller_id", sa.Integer(), sa.ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("marketplace_id", sa.Integer(), sa.ForeignKey("marketplaces.id"), nullable=False),
        sa.Column("posted_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gross", sa.Numeric(12, 2), nullable=True),
        sa.Column("fees", sa.Numeric(12, 2), nullable=True),
        sa.Column("net", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "inventory",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("condition", sa.String(30), nullable=False, server_default="New"),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("snapped_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_kind", sa.String(80), nullable=False),
        sa.Column("marketplace_id", sa.Integer(), sa.ForeignKey("marketplaces.id"), nullable=True),
        sa.Column("report_id", sa.String(120), nullable=False, server_default=""),
        sa.Column("status", sa.String(30), nullable=False, server_default="requested"),
        sa.Column("s3_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job", sa.String(60), nullable=False),
        sa.Column("marketplace_id", sa.Integer(), sa.ForeignKey("marketplaces.id"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("started", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("finished", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
    )
    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False, server_default="info"),
        sa.Column("ref_type", sa.String(30), nullable=False, server_default=""),
        sa.Column("ref_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("seen", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.execute("""
        INSERT INTO marketplaces (id, code, marketplace_id, currency, iso) VALUES
        (1, 'US', 'ATVPDKIKX0DER', 'USD', 'en-US'),
        (2, 'CA', 'A2VIGQ35RCS4UG', 'CAD', 'fr-CA')
    """)


def downgrade() -> None:
    for table in (
        "alerts", "pipeline_runs", "reports", "inventory", "settlements", "fees",
        "order_items", "orders", "price_snapshots", "products", "app_users",
        "notif_subscriptions", "settings", "spapi_creds", "sellers", "marketplaces",
    ):
        op.drop_table(table)