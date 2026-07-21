import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Transaction, TransactionStatus, TransactionType
from app.services.ledger import InsufficientBalanceError, LedgerService
from tests.conftest import create_verified_account


async def make_transaction(session: AsyncSession, amount: int) -> Transaction:
    """Insert a minimal Transaction row so ledger entries satisfy the FK constraint."""
    tx = Transaction(
        idempotency_key=str(uuid.uuid4()),
        type=TransactionType.P2P,
        status=TransactionStatus.PENDING,
        amount=amount,
        extra_data={},
    )
    session.add(tx)
    await session.flush()
    return tx


@pytest.mark.asyncio
async def test_balance_is_derived_from_ledger_entries(db_session):
    account = await create_verified_account(db_session, funded_cents=20_000)

    ledger = LedgerService(db_session)
    balance = await ledger.get_balance(account.id)

    assert balance == 20_000


@pytest.mark.asyncio
async def test_double_entry_transfer_keeps_books_balanced(db_session):
    alice = await create_verified_account(db_session, funded_cents=10_000)
    bob = await create_verified_account(db_session, funded_cents=0)

    ledger = LedgerService(db_session)
    tx = await make_transaction(db_session, 3_000)
    await ledger.post_double_entry(
        transaction_id=tx.id,
        debit_account_id=alice.id,
        credit_account_id=bob.id,
        amount=3_000,
    )
    await db_session.commit()

    assert await ledger.get_balance(alice.id) == 7_000
    assert await ledger.get_balance(bob.id) == 3_000


@pytest.mark.asyncio
async def test_transfer_rejected_when_balance_insufficient(db_session):
    alice = await create_verified_account(db_session, funded_cents=1_000)
    bob = await create_verified_account(db_session, funded_cents=0)
    # Save IDs before rollback — after rollback all ORM objects are expired
    # and accessing attributes would trigger a sync lazy-load (which is invalid in async).
    alice_id, bob_id = alice.id, bob.id

    ledger = LedgerService(db_session)
    tx = await make_transaction(db_session, 5_000)
    with pytest.raises(InsufficientBalanceError):
        await ledger.post_double_entry(
            transaction_id=tx.id,
            debit_account_id=alice_id,
            credit_account_id=bob_id,
            amount=5_000,
        )
    await db_session.rollback()

    # No partial entries were persisted - balances are untouched.
    assert await ledger.get_balance(alice_id) == 1_000
    assert await ledger.get_balance(bob_id) == 0


@pytest.mark.asyncio
async def test_settlement_account_can_go_negative_for_deposits(db_session):
    alice = await create_verified_account(db_session, funded_cents=0)
    ledger = LedgerService(db_session)
    settlement = await ledger.get_or_create_settlement_account()
    tx = await make_transaction(db_session, 50_000)

    await ledger.post_double_entry(
        transaction_id=tx.id,
        debit_account_id=settlement.id,
        credit_account_id=alice.id,
        amount=50_000,
        enforce_sufficient_funds=False,
    )
    await db_session.commit()

    assert await ledger.get_balance(alice.id) == 50_000
    assert await ledger.get_balance(settlement.id) == -50_000
