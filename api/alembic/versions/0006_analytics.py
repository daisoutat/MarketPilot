"""phase 6: taxonomy tree (category/niche/family) + explainable forecasts

Revision ID: 0006_analytics
Revises: 0005_alerts
Create Date: 2026-09-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_analytics"
down_revision = "0005_alerts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("marketplace_id", sa.Integer(), sa.ForeignKey("marketplaces.id"), nullable=False),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("categories.id", ondelete="CASCADE"), nullable=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),  # category | niche | family
        sa.UniqueConstraint("marketplace_id", "parent_id", "name", name="uq_category_path"),
    )
    op.create_index("idx_categories_parent", "categories", ["marketplace_id", "kind"])

    op.add_column("products", sa.Column("family_id", sa.Integer(), sa.ForeignKey("categories.id", ondelete="SET NULL"), nullable=True))
    op.create_index("idx_products_family", "products", ["family_id"])

    op.create_table(
        "forecasts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model", sa.String(40), nullable=False, server_default="seasonal-ols"),
        sa.Column("unit", sa.String(20), nullable=False, server_default="units/day"),
        sa.Column("window_days", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("horizon", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("history", sa.JSON(), nullable=True),
        sa.Column("points", sa.JSON(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_forecasts_product", "forecasts", ["product_id", "generated_at"])


def downgrade() -> None:
    op.drop_index("idx_forecasts_product", table_name="forecasts")
    op.drop_table("forecasts")

    op.drop_index("idx_products_family", table_name="products")
    op.drop_column("products", "family_id")

    op.drop_index("idx_categories_parent", table_name="categories")
    op.drop_table("categories")