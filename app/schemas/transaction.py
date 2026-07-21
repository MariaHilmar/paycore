import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models import TransactionStatus


class PixDepositCreate(BaseModel):
    amount_cents: int = Field(gt=0, description="Deposit amount in cents")


class PixDepositOut(BaseModel):
    txid: str
    status: TransactionStatus
    amount_cents: int
    qr_code: str
    created_at: datetime


class PixWithdrawCreate(BaseModel):
    amount_cents: int = Field(gt=0, description="Withdrawal amount in cents")


class PixWithdrawOut(BaseModel):
    id: uuid.UUID
    status: TransactionStatus
    amount_cents: int
    created_at: datetime


class TransferCreate(BaseModel):
    to_account_number: str = Field(min_length=1, max_length=20)
    amount_cents: int = Field(gt=0, description="Transfer amount in cents")


class TransferOut(BaseModel):
    id: uuid.UUID
    status: TransactionStatus
    amount_cents: int
    from_account_number: str
    to_account_number: str
    created_at: datetime
