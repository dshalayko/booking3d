"""Добавить обращения из Mini App.

Revision ID: 0011_feedback_requests
Revises: 0010_booking_policy
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_feedback_requests"
down_revision: str | None = "0010_booking_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feedback_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "length(message) BETWEEN 1 AND 4000", name="feedback_message_length"
        ),
        sa.CheckConstraint(
            "length(username) BETWEEN 1 AND 64", name="feedback_username_length"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "feedback_requests_created_at", "feedback_requests", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("feedback_requests_created_at", table_name="feedback_requests")
    op.drop_table("feedback_requests")
