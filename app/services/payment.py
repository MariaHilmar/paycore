import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Account,
    FraudStatus,
    Transaction,
    TransactionStatus,
    TransactionType,
)
from app.services.fraud import FraudContext, FraudService
from app.services.ledger import InsufficientBalanceError, LedgerService


class PaymentError(Exception):
    """Base class for payment-related domain errors."""


class AccountNotFoundError(PaymentError):
    """Raised when a target account (e.g. a transfer destination) does not exist."""


class TransactionNotFoundError(PaymentError):
    """Raised when a transaction (deposit or transfer) does not exist."""


class SelfTransferError(PaymentError):
    """Raised when the source and destination accounts are the same."""


class FraudBlockedError(PaymentError):
    """Raised when fraud screening hard-blocks a transaction. Carries the evaluation."""

    def __init__(self, evaluation) -> None:
        self.evaluation = evaluation
        super().__init__("transaction blocked by fraud screening")


class ReviewNotPendingError(PaymentError):
    """Raised when resolving a review for a transaction not held awaiting review."""


class PaymentService:
    def __init__(self, session: AsyncSession, fraud: FraudService | None = None) -> None:
        self.session = session
        self.ledger = LedgerService(session)
        self.fraud = fraud if fraud is not None else FraudService(session)

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

        held = await self._screen(
            transaction,
            account_id=account_id,
            transaction_type=TransactionType.PIX_OUT,
            amount_cents=amount_cents,
        )
        if held is not None:
            return held

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

    async def _screen(
        self,
        transaction: Transaction,
        *,
        account_id: uuid.UUID,
        transaction_type: TransactionType,
        amount_cents: int,
    ) -> Transaction | None:
        """Run fraud screening on an outbound transaction, before money moves.

        Persists the resulting ``fraud_status`` on the transaction and, depending
        on the decision:
        - BLOCKED: marks the transaction FAILED, commits, raises FraudBlockedError.
        - REVIEW:  leaves the transaction PENDING (money not moved), commits, and
          returns it - the caller must return this held transaction to the client.
        - APPROVED: returns None, signalling the caller to proceed to settlement.
        """
        evaluation = await self.fraud.evaluate(
            FraudContext(
                account_id=account_id,
                amount_cents=amount_cents,
                transaction_type=transaction_type,
            )
        )
        transaction.fraud_status = evaluation.status

        if evaluation.status == FraudStatus.BLOCKED:
            transaction.status = TransactionStatus.FAILED
            await self.session.commit()
            await self.session.refresh(transaction)
            raise FraudBlockedError(evaluation)

        if evaluation.status == FraudStatus.REVIEW:
            await self.session.commit()
            await self.session.refresh(transaction)
            return transaction

        return None

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

        held = await self._screen(
            transaction,
            account_id=from_account_id,
            transaction_type=TransactionType.P2P,
            amount_cents=amount_cents,
        )
        if held is not None:
            return held

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

    async def _resolve_posting_accounts(
        self, transaction: Transaction
    ) -> tuple[uuid.UUID, uuid.UUID]:
        """Reconstructs (debit_account_id, credit_account_id) for a held transaction."""
        if transaction.type == TransactionType.P2P:
            return (
                uuid.UUID(transaction.extra_data["from_account_id"]),
                uuid.UUID(transaction.extra_data["to_account_id"]),
            )
        if transaction.type == TransactionType.PIX_OUT:
            settlement = await self.ledger.get_or_create_settlement_account()
            return uuid.UUID(transaction.extra_data["account_id"]), settlement.id
        raise ValueError(f"cannot post transaction of type {transaction.type}")

    async def list_pending_reviews(self) -> list[Transaction]:
        stmt = (
            select(Transaction)
            .where(
                Transaction.status == TransactionStatus.PENDING,
                Transaction.fraud_status == FraudStatus.REVIEW,
            )
            .order_by(Transaction.created_at.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def _lock_pending_review(self, transaction_id: uuid.UUID) -> Transaction:
        result = await self.session.execute(
            select(Transaction).where(Transaction.id == transaction_id).with_for_update()
        )
        transaction = result.scalar_one_or_none()
        if transaction is None:
            raise TransactionNotFoundError(str(transaction_id))
        if (
            transaction.status != TransactionStatus.PENDING
            or transaction.fraud_status != FraudStatus.REVIEW
        ):
            raise ReviewNotPendingError(str(transaction_id))
        return transaction

    async def approve_review(self, transaction_id: uuid.UUID) -> Transaction:
        """Manually release a held transaction, settling it now.

        Funds are re-checked at approval time (the balance may have changed while
        the transaction sat in the review queue); if insufficient, the release
        fails and the transaction is marked FAILED.
        """
        transaction = await self._lock_pending_review(transaction_id)
        debit_id, credit_id = await self._resolve_posting_accounts(transaction)

        try:
            await self.ledger.post_double_entry(
                transaction_id=transaction.id,
                debit_account_id=debit_id,
                credit_account_id=credit_id,
                amount=transaction.amount,
                enforce_sufficient_funds=True,
            )
        except InsufficientBalanceError:
            transaction.status = TransactionStatus.FAILED
            await self.session.commit()
            raise

        transaction.fraud_status = FraudStatus.APPROVED
        transaction.status = TransactionStatus.COMPLETED
        await self.session.commit()
        await self.session.refresh(transaction)
        return transaction

    async def reject_review(self, transaction_id: uuid.UUID) -> Transaction:
        """Manually reject a held transaction: no money moves, recorded as FAILED."""
        transaction = await self._lock_pending_review(transaction_id)
        transaction.status = TransactionStatus.FAILED
        transaction.fraud_status = FraudStatus.BLOCKED
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
