import uuid

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.db.models import Account, LedgerEntry, LedgerEntryType

SETTLEMENT_ACCOUNT_NUMBER = "0000000000"
"""Well-known contra-account representing money entering/leaving the system via PIX.

Every deposit is DEBIT settlement / CREDIT user, so nothing is ever credited into
the ledger out of thin air - the books always balance to zero across all accounts.
"""


class LedgerError(Exception):
    """Base class for ledger-related domain errors."""


class AccountNotFoundError(LedgerError):
    def __init__(self, account_id: uuid.UUID):
        super().__init__(f"account {account_id} not found")


class InsufficientBalanceError(LedgerError):
    def __init__(self, account_id: uuid.UUID, balance: int, requested: int):
        super().__init__(
            f"account {account_id} has balance {balance} cents, requested {requested} cents"
        )
        self.balance = balance
        self.requested = requested


class LedgerService:
    """Double-entry bookkeeping core.

    Balance is never stored - it is always derived as SUM(credits) - SUM(debits)
    over ledger_entries. This makes the ledger the single source of truth and
    makes it impossible for a balance to drift out of sync with its history.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_balance(self, account_id: uuid.UUID) -> int:
        credit = func.coalesce(
            func.sum(
                case(
                    (LedgerEntry.entry_type == LedgerEntryType.CREDIT, LedgerEntry.amount), else_=0
                )
            ),
            0,
        )
        debit = func.coalesce(
            func.sum(
                case((LedgerEntry.entry_type == LedgerEntryType.DEBIT, LedgerEntry.amount), else_=0)
            ),
            0,
        )
        stmt = select(credit - debit).where(LedgerEntry.account_id == account_id)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def get_or_create_settlement_account(self) -> Account:
        stmt = select(Account).where(Account.account_number == SETTLEMENT_ACCOUNT_NUMBER)
        result = await self.session.execute(stmt)
        account = result.scalar_one_or_none()
        if account is not None:
            return account

        account = Account(user_id=None, account_number=SETTLEMENT_ACCOUNT_NUMBER)
        self.session.add(account)
        await self.session.flush()
        return account

    async def _lock_account(self, account_id: uuid.UUID) -> Account:
        """SELECT ... FOR UPDATE so concurrent transfers on the same account serialize."""
        stmt = select(Account).where(Account.id == account_id).with_for_update()
        result = await self.session.execute(stmt)
        account = result.scalar_one_or_none()
        if account is None:
            raise AccountNotFoundError(account_id)
        return account

    async def post_double_entry(
        self,
        *,
        transaction_id: uuid.UUID,
        debit_account_id: uuid.UUID,
        credit_account_id: uuid.UUID,
        amount: int,
        enforce_sufficient_funds: bool = True,
    ) -> None:
        """Post a balanced pair of ledger entries (one DEBIT, one CREDIT).

        When ``enforce_sufficient_funds`` is True we lock **only the debit account**
        with SELECT ... FOR UPDATE before reading its balance. This is enough to
        prevent overdraft: two concurrent debits from the same account serialize on
        that single row lock, while a concurrent credit into the account is always
        safe (it can only raise the balance, never lower it).

        Locking only the debit side - instead of both accounts - avoids a global
        bottleneck on shared credit accounts (e.g. the PIX settlement account, which
        every deposit credits) and removes any possibility of a lock-ordering
        deadlock, since at most one row is ever locked per posting.
        """
        if amount <= 0:
            raise ValueError("amount must be a positive number of cents")
        if debit_account_id == credit_account_id:
            raise ValueError("debit and credit account must differ")

        if enforce_sufficient_funds:
            await self._lock_account(debit_account_id)
            balance = await self.get_balance(debit_account_id)
            if balance < amount:
                raise InsufficientBalanceError(debit_account_id, balance, amount)

        self.session.add_all(
            [
                LedgerEntry(
                    transaction_id=transaction_id,
                    account_id=debit_account_id,
                    entry_type=LedgerEntryType.DEBIT,
                    amount=amount,
                ),
                LedgerEntry(
                    transaction_id=transaction_id,
                    account_id=credit_account_id,
                    entry_type=LedgerEntryType.CREDIT,
                    amount=amount,
                ),
            ]
        )

    async def get_statement(
        self, account_id: uuid.UUID, page: int, page_size: int
    ) -> tuple[list[LedgerEntry], int]:
        count_stmt = (
            select(func.count())
            .select_from(LedgerEntry)
            .where(LedgerEntry.account_id == account_id)
        )
        total = (await self.session.execute(count_stmt)).scalar_one()

        stmt = (
            select(LedgerEntry)
            .options(joinedload(LedgerEntry.transaction))
            .where(LedgerEntry.account_id == account_id)
            .order_by(LedgerEntry.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.session.execute(stmt)
        entries = list(result.scalars().all())
        return entries, int(total)
