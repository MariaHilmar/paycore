"""add fraud_status to transactions

Revision ID: a82ae7b338f7
Revises: 2bfdbfff13c8
Create Date: 2026-07-22 14:55:41.505389

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a82ae7b338f7'
down_revision: Union[str, None] = '2bfdbfff13c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    fraud_status = sa.Enum("APPROVED", "REVIEW", "BLOCKED", name="fraud_status")
    fraud_status.create(op.get_bind(), checkfirst=True)
    op.add_column("transactions", sa.Column("fraud_status", fraud_status, nullable=True))


def downgrade() -> None:
    op.drop_column("transactions", "fraud_status")
    sa.Enum(name="fraud_status").drop(op.get_bind(), checkfirst=True)
