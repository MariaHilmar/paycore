import uuid

import pytest
from sqlalchemy import delete

from app.db.models import LedgerEntry, LedgerEntryType, TransactionStatus
from app.services.payment import PaymentService
from app.services.reconciliation import ReconciliationService
from tests.conftest import create_verified_account


@pytest.mark.asyncio
async def test_healthy_ledger_reconciles(db_session):
    alice = await create_verified_account(db_session, funded_cents=10_000)
    bob = await create_verified_account(db_session, funded_cents=0)
    payment = PaymentService(db_session)

    await payment.create_transfer(
        from_account_id=alice.id,
        to_account_number=bob.account_number,
        amount_cents=4_000,
        idempotency_key=str(uuid.uuid4()),
    )

    report = await ReconciliationService(db_session).run()

    assert report.is_healthy
    assert report.is_balanced
    assert report.total_debit_cents == report.total_credit_cents
    assert report.discrepancies == []
    assert report.transactions_by_status[TransactionStatus.COMPLETED] >= 2  # deposit + transfer


@pytest.mark.asyncio
async def test_global_sum_is_zero_across_all_flows(db_session):
    """Deposit, withdraw and transfer - the whole ledger must still net to zero."""
    alice = await create_verified_account(db_session, funded_cents=30_000)
    bob = await create_verified_account(db_session, funded_cents=0)
    payment = PaymentService(db_session)

    await payment.create_transfer(
        from_account_id=alice.id,
        to_account_number=bob.account_number,
        amount_cents=5_000,
        idempotency_key=str(uuid.uuid4()),
    )
    await payment.create_withdrawal(
        account_id=alice.id, amount_cents=2_000, idempotency_key=str(uuid.uuid4())
    )

    report = await ReconciliationService(db_session).run()

    assert report.is_balanced
    assert report.total_credit_cents == report.total_debit_cents


@pytest.mark.asyncio
async def test_reconciliation_detects_a_tampered_ledger(db_session):
    """If a debit entry is deleted out from under a completed transaction, the
    reconciliation must flag it - proving the check actually catches drift."""
    alice = await create_verified_account(db_session, funded_cents=10_000)
    bob = await create_verified_account(db_session, funded_cents=0)
    payment = PaymentService(db_session)

    transfer = await payment.create_transfer(
        from_account_id=alice.id,
        to_account_number=bob.account_number,
        amount_cents=4_000,
        idempotency_key=str(uuid.uuid4()),
    )

    # Simulate corruption: delete the DEBIT leg of the transfer.
    await db_session.execute(
        delete(LedgerEntry).where(
            LedgerEntry.transaction_id == transfer.id,
            LedgerEntry.entry_type == LedgerEntryType.DEBIT,
        )
    )
    await db_session.commit()

    report = await ReconciliationService(db_session).run()

    assert not report.is_healthy
    assert not report.is_balanced  # a credit now has no matching debit
    flagged = {d.transaction_id for d in report.discrepancies}
    assert transfer.id in flagged
