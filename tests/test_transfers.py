import asyncio
import uuid

import pytest

from app.db.models import TransactionStatus
from app.services.ledger import InsufficientBalanceError, LedgerService
from app.services.payment import AccountNotFoundError, PaymentService, SelfTransferError
from tests.conftest import create_verified_account


@pytest.mark.asyncio
async def test_successful_transfer_moves_money_between_accounts(db_session):
    alice = await create_verified_account(db_session, funded_cents=10_000)
    bob = await create_verified_account(db_session, funded_cents=0)
    payment = PaymentService(db_session)
    ledger = LedgerService(db_session)

    transfer = await payment.create_transfer(
        from_account_id=alice.id,
        to_account_number=bob.account_number,
        amount_cents=4_000,
        idempotency_key=str(uuid.uuid4()),
    )

    assert transfer.status == TransactionStatus.COMPLETED
    assert await ledger.get_balance(alice.id) == 6_000
    assert await ledger.get_balance(bob.id) == 4_000


@pytest.mark.asyncio
async def test_transfer_with_insufficient_balance_is_recorded_as_failed(db_session):
    alice = await create_verified_account(db_session, funded_cents=1_000)
    bob = await create_verified_account(db_session, funded_cents=0)
    payment = PaymentService(db_session)

    with pytest.raises(InsufficientBalanceError):
        await payment.create_transfer(
            from_account_id=alice.id,
            to_account_number=bob.account_number,
            amount_cents=9_000,
            idempotency_key=str(uuid.uuid4()),
        )

    ledger = LedgerService(db_session)
    assert await ledger.get_balance(alice.id) == 1_000


@pytest.mark.asyncio
async def test_transfer_to_self_is_rejected(db_session):
    alice = await create_verified_account(db_session, funded_cents=5_000)
    payment = PaymentService(db_session)

    with pytest.raises(SelfTransferError):
        await payment.create_transfer(
            from_account_id=alice.id,
            to_account_number=alice.account_number,
            amount_cents=100,
            idempotency_key=str(uuid.uuid4()),
        )


@pytest.mark.asyncio
async def test_transfer_to_unknown_account_number_raises(db_session):
    alice = await create_verified_account(db_session, funded_cents=5_000)
    payment = PaymentService(db_session)

    with pytest.raises(AccountNotFoundError):
        await payment.create_transfer(
            from_account_id=alice.id,
            to_account_number="9999999999",
            amount_cents=100,
            idempotency_key=str(uuid.uuid4()),
        )


@pytest.mark.asyncio
async def test_repeated_idempotency_key_does_not_double_charge(db_session):
    alice = await create_verified_account(db_session, funded_cents=10_000)
    bob = await create_verified_account(db_session, funded_cents=0)
    payment = PaymentService(db_session)
    ledger = LedgerService(db_session)
    key = str(uuid.uuid4())

    first = await payment.create_transfer(
        from_account_id=alice.id,
        to_account_number=bob.account_number,
        amount_cents=2_000,
        idempotency_key=key,
    )
    second = await payment.create_transfer(
        from_account_id=alice.id,
        to_account_number=bob.account_number,
        amount_cents=2_000,
        idempotency_key=key,
    )

    assert first.id == second.id
    assert await ledger.get_balance(alice.id) == 8_000  # debited only once


@pytest.mark.asyncio
async def test_concurrent_transfers_never_overdraft_the_source_account(db_session, session_factory):
    """Two transfers race for the same 100.00 balance; only one may succeed.

    Exercises the real SELECT ... FOR UPDATE row lock in LedgerService against
    an actual Postgres instance - this is not just single-threaded arithmetic.
    """
    alice = await create_verified_account(db_session, funded_cents=10_000)
    bob = await create_verified_account(db_session, funded_cents=0)
    carol = await create_verified_account(db_session, funded_cents=0)

    async def attempt_transfer(to_account_number: str) -> TransactionStatus:
        async with session_factory() as session:
            payment = PaymentService(session)
            try:
                transfer = await payment.create_transfer(
                    from_account_id=alice.id,
                    to_account_number=to_account_number,
                    amount_cents=6_000,
                    idempotency_key=str(uuid.uuid4()),
                )
                return transfer.status
            except InsufficientBalanceError:
                return TransactionStatus.FAILED

    results = await asyncio.gather(
        attempt_transfer(bob.account_number),
        attempt_transfer(carol.account_number),
    )

    assert results.count(TransactionStatus.COMPLETED) == 1
    assert results.count(TransactionStatus.FAILED) == 1

    async with session_factory() as session:
        ledger = LedgerService(session)
        alice_balance = await ledger.get_balance(alice.id)
        bob_balance = await ledger.get_balance(bob.id)
        carol_balance = await ledger.get_balance(carol.id)

    assert alice_balance == 4_000
    assert alice_balance + bob_balance + carol_balance == 10_000
