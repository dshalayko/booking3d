"""PIN хранится как HMAC и уникален

На киоске человек вводит только четыре цифры, без имени, поэтому по PIN нужно
находить пользователя. Bcrypt-хеш искать не позволяет, а перебор хешей всех
членов коворкинга на каждый ввод — это секунды на запрос. Заменяем на HMAC с
серверным перцем и уникальный индекс.

Миграция рассчитана на то, что пользователей ещё нет (система не запущена):
старые PIN-ы восстановить всё равно нельзя, их нужно выдать заново.

Revision ID: 0002_pin_digest
Revises: 0001_initial
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_pin_digest"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("users", "pin_hash")
    op.add_column("users", sa.Column("pin_digest", sa.String(64), nullable=False))
    op.create_unique_constraint("users_pin_digest_key", "users", ["pin_digest"])


def downgrade() -> None:
    op.drop_constraint("users_pin_digest_key", "users", type_="unique")
    op.drop_column("users", "pin_digest")
    op.add_column("users", sa.Column("pin_hash", sa.String(128), nullable=False))
