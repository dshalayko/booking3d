"""Add usage limits without rewriting existing users, bookings or sessions."""

import sqlalchemy as sa
from alembic import op

revision = "0015_usage_cooldown"
down_revision = "0014_feature_flags"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "usage_policy",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("limit_minutes", sa.Integer(), nullable=False),
        sa.Column("cooldown_hours", sa.Integer(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="usage_policy_singleton"),
        sa.CheckConstraint(
            "limit_minutes > 0 AND cooldown_hours > 0", name="usage_policy_positive"
        ),
    )
    op.create_table(
        "usage_accounts",
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("limit_minutes", sa.Integer()),
        sa.Column("unlimited", sa.Boolean(), nullable=False),
        sa.Column("bonus_minutes", sa.Integer(), nullable=False),
        sa.Column("cycle_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True)),
        sa.Column("notified_until", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "limit_minutes IS NULL OR limit_minutes > 0", name="usage_account_limit"
        ),
        sa.CheckConstraint("bonus_minutes >= 0", name="usage_account_bonus"),
    )
    op.create_table(
        "usage_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("details", sa.Text(), nullable=False),
    )
    # All existing users inherit the policy lazily; historical work is untouched.
    op.execute("INSERT INTO usage_policy VALUES (1, true, 300, 168, CURRENT_TIMESTAMP)")


def downgrade():
    op.drop_table("usage_audit")
    op.drop_table("usage_accounts")
    op.drop_table("usage_policy")
