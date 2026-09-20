"""phase 7: assistant chat history (dashboard chatbot conversations)

Revision ID: 0007_chat
Revises: 0006_analytics
Create Date: 2026-09-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_chat"
down_revision = "0006_analytics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.String(80), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),  # user | assistant
        sa.Column("intent", sa.String(60), nullable=False, server_default=""),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_chat_session", "chat_messages", ["session_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_chat_session", table_name="chat_messages")
    op.drop_table("chat_messages")