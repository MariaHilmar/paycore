import asyncio
import uuid

import pytest

from app.db.models import TransactionStatus
from app.services.ledger import LedgerService
from app.services.payment import PaymentService
from tests.conftest import create_verified_account


@pytest.mark.asyncio
async def test_deposit_credits_account_only_after_confirmation(db_session):
    account = await create_verified_account(db_session)
    payment = PaymentService(db_session)
    ledger = LedgerService(db_session)

    deposit = await payment.create_deposit(
        account_id=account.id, amount_cents=20_000, idempotency_key=str(uuid.uuid4())
    )
    assert deposit.status == TransactionStatus.PENDING
    assert await ledger.get_balance(account.id) == 0

    confirmed = await payment.confirm_deposit(deposit.id)
    assert confirmed.status == TransactionStatus.COMPLETED
    assert await ledger.get_balance(account.id) == 20_000


@pytest.mark.asyncio
async def test_confirming_deposit_twice_is_idempotent(db_session):
    account = await create_verified_account(db_session)
    payment = PaymentService(db_session)
    ledger = LedgerService(db_session)

    deposit = await payment.create_deposit(
        account_id=account.id, amount_cents=15_000, idempotency_key=str(uuid.uuid4())
    )
    await payment.confirm_deposit(deposit.id)
    await payment.confirm_deposit(deposit.id)  # second call must be a no-op

    assert await ledger.get_balance(account.id) == 15_000


@pytest.mark.asyncio
async def test_creating_deposit_twice_with_same_key_returns_same_transaction(db_session):
    account = await create_verified_account(db_session)
    payment = PaymentService(db_session)
    key = str(uuid.uuid4())

    first = await payment.create_deposit(account_id=account.id, amount_cents=1_000, idempotency_key=key)
    second = await payment.create_deposit(account_id=account.id, amount_cents=1_000, idempotency_key=key)

    assert first.id == second.id


@pytest.mark.asyncio
async def test_concurrent_confirmation_credits_the_account_only_once(db_session, session_factory):
    """Two PIX webhooks firing on the same deposit must not double-credit.

    Guards the SELECT ... FOR UPDATE on the transaction row in confirm_deposit.
    """
    account = await create_verified_account(db_session)
    payment = PaymentService(db_session)
    deposit = await payment.create_deposit(
        account_id=account.id, amount_cents=30_000, idempotency_key=str(uuid.uuid4())
    )

    async def confirm() -> None:
        async with session_factory() as session:
            await PaymentService(session).confirm_deposit(deposit.id)

    await asyncio.gather(confirm(), confirm())

    async with session_factory() as session:
        balance = await LedgerService(session).get_balance(account.id)

    assert balance == 30_000  # credited once, not twice
