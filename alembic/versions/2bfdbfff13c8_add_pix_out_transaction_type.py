"""add PIX_OUT transaction type

Revision ID: 2bfdbfff13c8
Revises: acf930e39d05
Create Date: 2026-07-21 09:55:50.345775

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '2bfdbfff13c8'
down_revision: Union[str, None] = 'acf930e39d05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL native enums require ALTER TYPE to gain a new value.
    # IF NOT EXISTS makes the migration safe to re-run.
    op.execute("ALTER TYPE transaction_type ADD VALUE IF NOT EXISTS 'PIX_OUT'")


def downgrade() -> None:
    # PostgreSQL does not support removing a value from an enum type without
    # recreating it. Left as a no-op: removing PIX_OUT would require rewriting
    # every row and dependent object, which is unsafe to automate here.
    pass
