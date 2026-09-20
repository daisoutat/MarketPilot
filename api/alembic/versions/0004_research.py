"""market research + FX support

Revision ID: 0004_research
Revises: 0003_reports
Create Date: 2026-09-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_research"
down_revision = "0003_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("marketplace_id", sa.Integer(), sa.ForeignKey("marketplaces.id"), nullable=False),
        sa.Column("asin", sa.String(10), nullable=False),
        sa.Column("keywords", sa.String(200), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_snap_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("marketplace_id", "asin", name="uq_research_target"),
    )
    op.create_table(
        "fx_rates",
        sa.Column("base", sa.String(3), primary_key=True),
        sa.Column("quote", sa.String(3), primary_key=True),
        sa.Column("rate", sa.Numeric(12, 6), nullable=False),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_research_targets_asin", "research_targets", ["asin"])
    op.create_index("idx_price_snapshots_latest", "price_snapshots", ["product_id", "captured_at"])


def downgrade() -> None:
    op.drop_index("idx_price_snapshots_latest", table_name="price_snapshots")
    op.drop_index("idx_research_targets_asin", table_name="research_targets")
    op.drop_table("fx_rates")
    op.drop_table("research_targets")