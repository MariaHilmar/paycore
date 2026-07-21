import uuid
from dataclasses import dataclass, field

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, LedgerEntry, LedgerEntryType, Transaction, TransactionStatus


@dataclass
class TransactionDiscrepancy:
    transaction_id: uuid.UUID
    expected_amount_cents: int
    debit_total_cents: int
    credit_total_cents: int


@dataclass
class ReconciliationReport:
    total_accounts: int
    total_transactions: int
    transactions_by_status: dict[str, int]
    total_debit_cents: int
    total_credit_cents: int
    is_balanced: bool
    discrepancies: list[TransactionDiscrepancy] = field(default_factory=list)

    @property
    def is_healthy(self) -> bool:
        return self.is_balanced and not self.discrepancies


_credit_sum = func.coalesce(
    func.sum(case((LedgerEntry.entry_type == LedgerEntryType.CREDIT, LedgerEntry.amount), else_=0)),
    0,
)
_debit_sum = func.coalesce(
    func.sum(case((LedgerEntry.entry_type == LedgerEntryType.DEBIT, LedgerEntry.amount), else_=0)),
    0,
)


class ReconciliationService:
    """Cross-checks the ledger against transactions to prove the books are sound.

    Verifies two invariants:

    1. Global zero-sum: across every ledger entry, total credits == total debits.
       Money is only ever moved, never created or destroyed.
    2. Per-transaction balance: each COMPLETED transaction must have equal debit and
       credit totals, both matching the transaction's own amount.

    Any transaction violating (2) is reported as a discrepancy. In a correct system
    the report is always healthy - this endpoint exists to *prove* that continuously
    and to catch drift introduced by bugs or manual data changes.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def run(self) -> ReconciliationReport:
        total_accounts = (
            await self.session.execute(select(func.count()).select_from(Account))
        ).scalar_one()
        total_transactions = (
            await self.session.execute(select(func.count()).select_from(Transaction))
        ).scalar_one()

        status_rows = await self.session.execute(
            select(Transaction.status, func.count()).group_by(Transaction.status)
        )
        transactions_by_status = {str(status): count for status, count in status_rows.all()}

        totals = (
            await self.session.execute(select(_credit_sum, _debit_sum))
        ).one()
        total_credit_cents, total_debit_cents = int(totals[0]), int(totals[1])

        discrepancies = await self._find_unbalanced_transactions()

        return ReconciliationReport(
            total_accounts=int(total_accounts),
            total_transactions=int(total_transactions),
            transactions_by_status=transactions_by_status,
            total_debit_cents=total_debit_cents,
            total_credit_cents=total_credit_cents,
            is_balanced=total_credit_cents == total_debit_cents,
            discrepancies=discrepancies,
        )

    async def _find_unbalanced_transactions(self) -> list[TransactionDiscrepancy]:
        stmt = (
            select(
                Transaction.id,
                Transaction.amount,
                _debit_sum.label("debit_total"),
                _credit_sum.label("credit_total"),
            )
            .join(LedgerEntry, LedgerEntry.transaction_id == Transaction.id, isouter=True)
            .where(Transaction.status == TransactionStatus.COMPLETED)
            .group_by(Transaction.id, Transaction.amount)
            .having(
                (_debit_sum != _credit_sum) | (_credit_sum != Transaction.amount)
            )
        )
        rows = await self.session.execute(stmt)
        return [
            TransactionDiscrepancy(
                transaction_id=row.id,
                expected_amount_cents=int(row.amount),
                debit_total_cents=int(row.debit_total),
                credit_total_cents=int(row.credit_total),
            )
            for row in rows.all()
        ]
