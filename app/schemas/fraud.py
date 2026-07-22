import uuid
from datetime import datetime

from pydantic import BaseModel

from app.db.models import FraudStatus, TransactionStatus, TransactionType


class FraudReviewItem(BaseModel):
    transaction_id: uuid.UUID
    type: TransactionType
    amount_cents: int
    created_at: datetime


class FraudReviewList(BaseModel):
    items: list[FraudReviewItem]
    total: int


class FraudReviewResolution(BaseModel):
    transaction_id: uuid.UUID
    status: TransactionStatus
    fraud_status: FraudStatus | None
