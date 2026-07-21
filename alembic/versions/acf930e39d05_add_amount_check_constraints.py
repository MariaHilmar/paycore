"""add amount check constraints

Revision ID: acf930e39d05
Revises: 09a75fcf872e
Create Date: 2026-07-21 09:39:12.312432

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'acf930e39d05'
down_revision: Union[str, None] = '09a75fcf872e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_transactions_amount_positive", "transactions", "amount > 0"
    )
    op.create_check_constraint(
        "ck_ledger_entries_amount_positive", "ledger_entries", "amount > 0"
    )


def downgrade() -> None:
    op.drop_constraint("ck_ledger_entries_amount_positive", "ledger_entries", type_="check")
    op.drop_constraint("ck_transactions_amount_positive", "transactions", type_="check")
