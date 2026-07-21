import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, Transaction, TransactionStatus, TransactionType
from app.services.ledger import InsufficientBalanceError, LedgerService


class PaymentError(Exception):
    """Base class for payment-related domain errors."""


class AccountNotFoundError(PaymentError):
    """Raised when a target account (e.g. a transfer destination) does not exist."""


class TransactionNotFoundError(PaymentError):
    """Raised when a transaction (deposit or transfer) does not exist."""


class SelfTransferError(PaymentError):
    """Raised when the source and destination accounts are the same."""


class PaymentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.ledger = LedgerService(session)

    async def _get_by_idempotency_key(self, key: str) -> Transaction | None:
        result = await self.session.execute(
            select(Transaction).where(Transaction.idempotency_key == key)
        )
        return result.scalar_one_or_none()

    async def _get_account_by_number(self, account_number: str) -> Account | None:
        result = await self.session.execute(
            select(Account).where(Account.account_number == account_number)
        )
        return result.scalar_one_or_none()

    async def create_deposit(
        self, *, account_id: uuid.UUID, amount_cents: int, idempotency_key: str
    ) -> Transaction:
        """Creates a PENDING PIX charge. Money only moves once /pay confirms it."""
        existing = await self._get_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing

        transaction = Transaction(
            idempotency_key=idempotency_key,
            type=TransactionType.PIX_IN,
            status=TransactionStatus.PENDING,
            amount=amount_cents,
            extra_data={"account_id": str(account_id)},
        )
        self.session.add(transaction)

        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            existing = await self._get_by_idempotency_key(idempotency_key)
            if existing is not None:
                return existing
            raise

        await self.session.refresh(transaction)
        return transaction

    async def confirm_deposit(self, txid: uuid.UUID) -> Transaction:
        """Simulates the bank's PIX webhook firing: credits the account for real.

        Idempotent even under concurrency: we lock the transaction row with
        SELECT ... FOR UPDATE before inspecting its status, so two webhooks racing
        on the same deposit serialize - the second one re-reads status COMPLETED
        and returns without posting a duplicate credit.
        """
        result = await self.session.execute(
            select(Transaction).where(Transaction.id == txid).with_for_update()
        )
        transaction = result.scalar_one_or_none()
        if transaction is None or transaction.type != TransactionType.PIX_IN:
            raise TransactionNotFoundError(str(txid))

        if transaction.status == TransactionStatus.COMPLETED:
            return transaction

        account_id = uuid.UUID(transaction.extra_data["account_id"])
        settlement_account = await self.ledger.get_or_create_settlement_account()

        await self.ledger.post_double_entry(
            transaction_id=transaction.id,
            debit_account_id=settlement_account.id,
            credit_account_id=account_id,
            amount=transaction.amount,
            enforce_sufficient_funds=False,
        )
        transaction.status = TransactionStatus.COMPLETED
        await self.session.commit()
        await self.session.refresh(transaction)
        return transaction

    async def create_withdrawal(
        self, *, account_id: uuid.UUID, amount_cents: int, idempotency_key: str
    ) -> Transaction:
        """PIX withdrawal: debits the user and credits the settlement account.

        The mirror image of a deposit. Unlike a deposit, it enforces sufficient
        funds - you cannot withdraw money you do not have. A withdrawal that fails
        the balance check is recorded as FAILED, keeping an audit trail.
        """
        existing = await self._get_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing

        settlement_account = await self.ledger.get_or_create_settlement_account()

        transaction = Transaction(
            idempotency_key=idempotency_key,
            type=TransactionType.PIX_OUT,
            status=TransactionStatus.PENDING,
            amount=amount_cents,
            extra_data={"account_id": str(account_id)},
        )
        self.session.add(transaction)

        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            existing = await self._get_by_idempotency_key(idempotency_key)
            if existing is not None:
                return existing
            raise

        try:
            await self.ledger.post_double_entry(
                transaction_id=transaction.id,
                debit_account_id=account_id,
                credit_account_id=settlement_account.id,
                amount=amount_cents,
                enforce_sufficient_funds=True,
            )
        except InsufficientBalanceError:
            transaction.status = TransactionStatus.FAILED
            await self.session.commit()
            raise

        transaction.status = TransactionStatus.COMPLETED
        await self.session.commit()
        await self.session.refresh(transaction)
        return transaction

    async def create_transfer(
        self,
        *,
        from_account_id: uuid.UUID,
        to_account_number: str,
        amount_cents: int,
        idempotency_key: str,
    ) -> Transaction:
        existing = await self._get_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing

        to_account = await self._get_account_by_number(to_account_number)
        if to_account is None:
            raise AccountNotFoundError(to_account_number)
        if to_account.id == from_account_id:
            raise SelfTransferError()

        transaction = Transaction(
            idempotency_key=idempotency_key,
            type=TransactionType.P2P,
            status=TransactionStatus.PENDING,
            amount=amount_cents,
            extra_data={
                "from_account_id": str(from_account_id),
                "to_account_id": str(to_account.id),
            },
        )
        self.session.add(transaction)

        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            existing = await self._get_by_idempotency_key(idempotency_key)
            if existing is not None:
                return existing
            raise

        try:
            await self.ledger.post_double_entry(
                transaction_id=transaction.id,
                debit_account_id=from_account_id,
                credit_account_id=to_account.id,
                amount=amount_cents,
                enforce_sufficient_funds=True,
            )
        except InsufficientBalanceError:
            transaction.status = TransactionStatus.FAILED
            await self.session.commit()
            raise

        transaction.status = TransactionStatus.COMPLETED
        await self.session.commit()
        await self.session.refresh(transaction)
        return transaction

    async def get_transaction(self, transaction_id: uuid.UUID) -> Transaction:
        result = await self.session.execute(
            select(Transaction).where(Transaction.id == transaction_id)
        )
        transaction = result.scalar_one_or_none()
        if transaction is None:
            raise TransactionNotFoundError(str(transaction_id))
        return transaction
