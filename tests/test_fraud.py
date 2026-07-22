import uuid

import pytest

from app.db.models import FraudStatus, TransactionStatus, TransactionType
from app.services.fraud import (
    AmountThresholdRule,
    DailyDebitLimitRule,
    FraudContext,
    FraudService,
    VelocityRule,
)
from app.services.ledger import LedgerService
from app.services.payment import FraudBlockedError, PaymentService
from tests.conftest import create_verified_account


def ctx(account_id, amount_cents, tx_type=TransactionType.P2P) -> FraudContext:
    return FraudContext(account_id=account_id, amount_cents=amount_cents, transaction_type=tx_type)


# --- Rule engine (unit) -------------------------------------------------------


@pytest.mark.asyncio
async def test_amount_rule_blocks_above_block_threshold(db_session):
    engine = FraudService(
        db_session, rules=[AmountThresholdRule(review_cents=100, block_cents=1_000)]
    )
    result = await engine.evaluate(ctx(uuid.uuid4(), 1_000))
    assert result.status == FraudStatus.BLOCKED


@pytest.mark.asyncio
async def test_amount_rule_reviews_between_thresholds(db_session):
    engine = FraudService(
        db_session, rules=[AmountThresholdRule(review_cents=100, block_cents=1_000)]
    )
    result = await engine.evaluate(ctx(uuid.uuid4(), 500))
    assert result.status == FraudStatus.REVIEW


@pytest.mark.asyncio
async def test_amount_rule_approves_below_thresholds(db_session):
    engine = FraudService(
        db_session, rules=[AmountThresholdRule(review_cents=100, block_cents=1_000)]
    )
    result = await engine.evaluate(ctx(uuid.uuid4(), 50))
    assert result.status == FraudStatus.APPROVED
    assert result.is_approved


@pytest.mark.asyncio
async def test_engine_takes_the_most_severe_outcome(db_session):
    # One rule says REVIEW, another says BLOCKED -> aggregate must be BLOCKED.
    engine = FraudService(
        db_session,
        rules=[
            AmountThresholdRule(review_cents=1, block_cents=10_000_000),  # -> REVIEW
            DailyDebitLimitRule(limit_cents=0),  # -> BLOCKED (any amount exceeds 0)
        ],
    )
    result = await engine.evaluate(ctx(uuid.uuid4(), 500))
    assert result.status == FraudStatus.BLOCKED
    assert {o.status for o in result.triggered} == {FraudStatus.REVIEW, FraudStatus.BLOCKED}


@pytest.mark.asyncio
async def test_velocity_rule_reviews_after_enough_debits(db_session):
    alice = await create_verified_account(db_session, funded_cents=100_000)
    bob = await create_verified_account(db_session, funded_cents=0)
    payment = PaymentService(db_session)

    # Three small transfers create three DEBIT entries on alice.
    for _ in range(3):
        await payment.create_transfer(
            from_account_id=alice.id,
            to_account_number=bob.account_number,
            amount_cents=100,
            idempotency_key=str(uuid.uuid4()),
        )

    engine = FraudService(db_session, rules=[VelocityRule(window_seconds=3600, max_debits=3)])
    result = await engine.evaluate(ctx(alice.id, 100))
    assert result.status == FraudStatus.REVIEW


@pytest.mark.asyncio
async def test_daily_limit_counts_prior_debits(db_session):
    alice = await create_verified_account(db_session, funded_cents=100_000)
    bob = await create_verified_account(db_session, funded_cents=0)
    payment = PaymentService(db_session)

    await payment.create_transfer(
        from_account_id=alice.id,
        to_account_number=bob.account_number,
        amount_cents=6_000,
        idempotency_key=str(uuid.uuid4()),
    )

    # Prior debits = 6000; a further 5000 would total 11000, over a 10000 limit.
    engine = FraudService(db_session, rules=[DailyDebitLimitRule(limit_cents=10_000)])
    result = await engine.evaluate(ctx(alice.id, 5_000))
    assert result.status == FraudStatus.BLOCKED


# --- Integration with PaymentService -----------------------------------------


def blocking_fraud(session) -> FraudService:
    return FraudService(session, rules=[AmountThresholdRule(review_cents=1, block_cents=2)])


def reviewing_fraud(session) -> FraudService:
    # amount >= 1 triggers REVIEW, block threshold set unreachably high
    return FraudService(session, rules=[AmountThresholdRule(review_cents=1, block_cents=10**12)])


@pytest.mark.asyncio
async def test_blocked_transfer_moves_no_money_and_is_failed(db_session):
    alice = await create_verified_account(db_session, funded_cents=50_000)
    bob = await create_verified_account(db_session, funded_cents=0)
    payment = PaymentService(db_session, fraud=blocking_fraud(db_session))
    ledger = LedgerService(db_session)

    with pytest.raises(FraudBlockedError):
        await payment.create_transfer(
            from_account_id=alice.id,
            to_account_number=bob.account_number,
            amount_cents=5_000,
            idempotency_key=str(uuid.uuid4()),
        )

    assert await ledger.get_balance(alice.id) == 50_000
    assert await ledger.get_balance(bob.id) == 0


@pytest.mark.asyncio
async def test_reviewed_transfer_is_held_without_moving_money(db_session):
    alice = await create_verified_account(db_session, funded_cents=50_000)
    bob = await create_verified_account(db_session, funded_cents=0)
    payment = PaymentService(db_session, fraud=reviewing_fraud(db_session))
    ledger = LedgerService(db_session)

    held = await payment.create_transfer(
        from_account_id=alice.id,
        to_account_number=bob.account_number,
        amount_cents=5_000,
        idempotency_key=str(uuid.uuid4()),
    )

    assert held.status == TransactionStatus.PENDING
    assert held.fraud_status == FraudStatus.REVIEW
    assert await ledger.get_balance(alice.id) == 50_000  # money not moved yet
    assert await ledger.get_balance(bob.id) == 0


@pytest.mark.asyncio
async def test_approving_a_held_transfer_settles_it(db_session):
    alice = await create_verified_account(db_session, funded_cents=50_000)
    bob = await create_verified_account(db_session, funded_cents=0)
    payment = PaymentService(db_session, fraud=reviewing_fraud(db_session))
    ledger = LedgerService(db_session)

    held = await payment.create_transfer(
        from_account_id=alice.id,
        to_account_number=bob.account_number,
        amount_cents=5_000,
        idempotency_key=str(uuid.uuid4()),
    )

    settled = await payment.approve_review(held.id)

    assert settled.status == TransactionStatus.COMPLETED
    assert settled.fraud_status == FraudStatus.APPROVED
    assert await ledger.get_balance(alice.id) == 45_000
    assert await ledger.get_balance(bob.id) == 5_000


@pytest.mark.asyncio
async def test_rejecting_a_held_transfer_fails_it(db_session):
    alice = await create_verified_account(db_session, funded_cents=50_000)
    bob = await create_verified_account(db_session, funded_cents=0)
    payment = PaymentService(db_session, fraud=reviewing_fraud(db_session))
    ledger = LedgerService(db_session)

    held = await payment.create_transfer(
        from_account_id=alice.id,
        to_account_number=bob.account_number,
        amount_cents=5_000,
        idempotency_key=str(uuid.uuid4()),
    )

    rejected = await payment.reject_review(held.id)

    assert rejected.status == TransactionStatus.FAILED
    assert rejected.fraud_status == FraudStatus.BLOCKED
    assert await ledger.get_balance(alice.id) == 50_000
    assert await ledger.get_balance(bob.id) == 0


@pytest.mark.asyncio
async def test_held_review_appears_in_pending_queue(db_session):
    alice = await create_verified_account(db_session, funded_cents=50_000)
    bob = await create_verified_account(db_session, funded_cents=0)
    payment = PaymentService(db_session, fraud=reviewing_fraud(db_session))

    held = await payment.create_transfer(
        from_account_id=alice.id,
        to_account_number=bob.account_number,
        amount_cents=5_000,
        idempotency_key=str(uuid.uuid4()),
    )

    reviews = await payment.list_pending_reviews()
    assert [r.id for r in reviews] == [held.id]

    # After resolution the queue is empty.
    await payment.reject_review(held.id)
    assert await payment.list_pending_reviews() == []


@pytest.mark.asyncio
async def test_approving_held_withdrawal_settles_against_settlement(db_session):
    alice = await create_verified_account(db_session, funded_cents=50_000)
    payment = PaymentService(db_session, fraud=reviewing_fraud(db_session))
    ledger = LedgerService(db_session)

    held = await payment.create_withdrawal(
        account_id=alice.id, amount_cents=5_000, idempotency_key=str(uuid.uuid4())
    )
    assert held.type == TransactionType.PIX_OUT
    assert held.fraud_status == FraudStatus.REVIEW

    settled = await payment.approve_review(held.id)
    assert settled.status == TransactionStatus.COMPLETED
    assert await ledger.get_balance(alice.id) == 45_000
