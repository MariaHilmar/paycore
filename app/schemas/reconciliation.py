import uuid

from pydantic import BaseModel


class TransactionDiscrepancyOut(BaseModel):
    transaction_id: uuid.UUID
    expected_amount_cents: int
    debit_total_cents: int
    credit_total_cents: int


class ReconciliationReportOut(BaseModel):
    is_healthy: bool
    is_balanced: bool
    total_accounts: int
    total_transactions: int
    transactions_by_status: dict[str, int]
    total_debit_cents: int
    total_credit_cents: int
    discrepancies: list[TransactionDiscrepancyOut]
