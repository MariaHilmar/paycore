import uuid

import pytest

from app.db.models import LedgerEntryType, TransactionStatus, TransactionType
from app.services.ledger import (
    SETTLEMENT_ACCOUNT_NUMBER,
    InsufficientBalanceError,
    LedgerService,
)
from app.services.payment import PaymentService
from tests.conftest import create_verified_account


@pytest.mark.asyncio
async def test_withdrawal_debits_the_account(db_session):
    account = await create_verified_account(db_session, funded_cents=20_000)
    payment = PaymentService(db_session)
    ledger = LedgerService(db_session)

    withdrawal = await payment.create_withdrawal(
        account_id=account.id, amount_cents=8_000, idempotency_key=str(uuid.uuid4())
    )

    assert withdrawal.type == TransactionType.PIX_OUT
    assert withdrawal.status == TransactionStatus.COMPLETED
    assert await ledger.get_balance(account.id) == 12_000


@pytest.mark.asyncio
async def test_withdrawal_is_mirror_of_deposit_on_settlement(db_session):
    """A deposit then a full withdrawal must leave the settlement account back at zero."""
    account = await create_verified_account(db_session, funded_cents=15_000)
    payment = PaymentService(db_session)
    ledger = LedgerService(db_session)
    settlement = await ledger.get_or_create_settlement_account()

    await payment.create_withdrawal(
        account_id=account.id, amount_cents=15_000, idempotency_key=str(uuid.uuid4())
    )

    assert await ledger.get_balance(account.id) == 0
    # settlement went -15000 (funding) then +15000 (withdrawal) = 0
    assert await ledger.get_balance(settlement.id) == 0
    assert settlement.account_number == SETTLEMENT_ACCOUNT_NUMBER


@pytest.mark.asyncio
async def test_withdrawal_with_insufficient_balance_is_recorded_as_failed(db_session):
    account = await create_verified_account(db_session, funded_cents=1_000)
    payment = PaymentService(db_session)
    ledger = LedgerService(db_session)

    with pytest.raises(InsufficientBalanceError):
        await payment.create_withdrawal(
            account_id=account.id, amount_cents=9_000, idempotency_key=str(uuid.uuid4())
        )

    assert await ledger.get_balance(account.id) == 1_000


@pytest.mark.asyncio
async def test_withdrawal_is_idempotent(db_session):
    account = await create_verified_account(db_session, funded_cents=20_000)
    payment = PaymentService(db_session)
    ledger = LedgerService(db_session)
    key = str(uuid.uuid4())

    first = await payment.create_withdrawal(
        account_id=account.id, amount_cents=5_000, idempotency_key=key
    )
    second = await payment.create_withdrawal(
        account_id=account.id, amount_cents=5_000, idempotency_key=key
    )

    assert first.id == second.id
    assert await ledger.get_balance(account.id) == 15_000  # debited only once


@pytest.mark.asyncio
async def test_withdrawal_posts_debit_on_user_credit_on_settlement(db_session):
    account = await create_verified_account(db_session, funded_cents=10_000)
    payment = PaymentService(db_session)
    ledger = LedgerService(db_session)
    settlement = await ledger.get_or_create_settlement_account()

    withdrawal = await payment.create_withdrawal(
        account_id=account.id, amount_cents=3_000, idempotency_key=str(uuid.uuid4())
    )

    entries, _ = await ledger.get_statement(account.id, page=1, page_size=50)
    withdrawal_entry = next(e for e in entries if e.transaction_id == withdrawal.id)
    assert withdrawal_entry.entry_type == LedgerEntryType.DEBIT

    settlement_entries, _ = await ledger.get_statement(settlement.id, page=1, page_size=50)
    settlement_entry = next(e for e in settlement_entries if e.transaction_id == withdrawal.id)
    assert settlement_entry.entry_type == LedgerEntryType.CREDIT
