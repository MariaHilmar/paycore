import uuid
from datetime import datetime

from pydantic import BaseModel

from app.db.models import AccountStatus, LedgerEntryType, TransactionType


class AccountOut(BaseModel):
    id: uuid.UUID
    account_number: str
    status: AccountStatus
    balance_cents: int

    model_config = {"from_attributes": True}


class StatementEntry(BaseModel):
    transaction_id: uuid.UUID
    transaction_type: TransactionType
    entry_type: LedgerEntryType
    amount_cents: int
    created_at: datetime


class StatementOut(BaseModel):
    items: list[StatementEntry]
    page: int
    page_size: int
    total: int
