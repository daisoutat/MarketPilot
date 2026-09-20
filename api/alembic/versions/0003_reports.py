"""reports sync (settlements) support

Revision ID: 0003_reports
Revises: 0002_order_sync
Create Date: 2026-09-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_reports"
down_revision = "0002_order_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One settlement summary row per marketplace period (idempotent re-syncs).
    op.create_unique_constraint(
        "uq_settlement_period", "settlements", ["marketplace_id", "posted_date"]
    )
    op.create_table(
        "settlement_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("settlement_id", sa.Integer(), sa.ForeignKey("settlements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("asin", sa.String(10), nullable=False, server_default=""),
        sa.Column("sku", sa.String(80), nullable=False, server_default=""),
        sa.Column("order_id", sa.String(40), nullable=False, server_default=""),
        sa.Column("units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revenue", sa.Numeric(12, 2), nullable=True),
        sa.Column("fees", sa.Numeric(12, 2), nullable=True),
        sa.Column("net", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.UniqueConstraint("settlement_id", "order_id", "sku", name="uq_settlement_line"),
    )
    op.create_index("idx_settlement_lines_product", "settlement_lines", ["product_id"])
    op.create_index("idx_settlements_posted", "settlements", ["posted_date"])


def downgrade() -> None:
    op.drop_index("idx_settlements_posted", table_name="settlements")
    op.drop_index("idx_settlement_lines_product", table_name="settlement_lines")
    op.drop_table("settlement_lines")
    op.drop_constraint("uq_settlement_period", "settlements", type_="unique")