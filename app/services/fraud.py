import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import FraudStatus, LedgerEntry, LedgerEntryType, TransactionType

# Severity ordering so the engine can pick the most severe outcome across rules.
_SEVERITY: dict[FraudStatus, int] = {
    FraudStatus.APPROVED: 0,
    FraudStatus.REVIEW: 1,
    FraudStatus.BLOCKED: 2,
}


@dataclass(frozen=True)
class FraudContext:
    """Everything a rule needs to judge a single money-moving transaction.

    ``account_id`` is always the *paying* account (the one being debited), which
    is what velocity and daily-limit rules screen.
    """

    account_id: uuid.UUID
    amount_cents: int
    transaction_type: TransactionType


@dataclass(frozen=True)
class RuleOutcome:
    rule: str
    status: FraudStatus
    reason: str


@dataclass(frozen=True)
class FraudEvaluation:
    status: FraudStatus
    triggered: list[RuleOutcome] = field(default_factory=list)

    @property
    def is_approved(self) -> bool:
        return self.status == FraudStatus.APPROVED


class FraudRule(Protocol):
    """A single, single-responsibility screening rule.

    Returns a RuleOutcome when it wants to flag the transaction, or None when it
    has nothing to say (the transaction passes this rule).
    """

    name: str

    async def evaluate(self, session: AsyncSession, ctx: FraudContext) -> RuleOutcome | None:
        ...


async def _debit_total_since(session: AsyncSession, account_id: uuid.UUID, since: datetime) -> int:
    """Total cents debited from an account since a point in time (money that left)."""
    stmt = select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
        LedgerEntry.account_id == account_id,
        LedgerEntry.entry_type == LedgerEntryType.DEBIT,
        LedgerEntry.created_at >= since,
    )
    return int((await session.execute(stmt)).scalar_one())


async def _debit_count_since(session: AsyncSession, account_id: uuid.UUID, since: datetime) -> int:
    """Number of debit movements out of an account since a point in time."""
    stmt = (
        select(func.count())
        .select_from(LedgerEntry)
        .where(
            LedgerEntry.account_id == account_id,
            LedgerEntry.entry_type == LedgerEntryType.DEBIT,
            LedgerEntry.created_at >= since,
        )
    )
    return int((await session.execute(stmt)).scalar_one())


class AmountThresholdRule:
    """Screens a single transaction by size: large -> review, huge -> hard block."""

    name = "amount_threshold"

    def __init__(self, review_cents: int, block_cents: int) -> None:
        self.review_cents = review_cents
        self.block_cents = block_cents

    async def evaluate(self, session: AsyncSession, ctx: FraudContext) -> RuleOutcome | None:
        if ctx.amount_cents >= self.block_cents:
            return RuleOutcome(
                self.name,
                FraudStatus.BLOCKED,
                f"amount {ctx.amount_cents} >= block threshold {self.block_cents}",
            )
        if ctx.amount_cents >= self.review_cents:
            return RuleOutcome(
                self.name,
                FraudStatus.REVIEW,
                f"amount {ctx.amount_cents} >= review threshold {self.review_cents}",
            )
        return None


class VelocityRule:
    """Flags an account making too many debits in a short window for manual review."""

    name = "velocity"

    def __init__(self, window_seconds: int, max_debits: int) -> None:
        self.window_seconds = window_seconds
        self.max_debits = max_debits

    async def evaluate(self, session: AsyncSession, ctx: FraudContext) -> RuleOutcome | None:
        since = datetime.now(UTC) - timedelta(seconds=self.window_seconds)
        count = await _debit_count_since(session, ctx.account_id, since)
        if count >= self.max_debits:
            return RuleOutcome(
                self.name,
                FraudStatus.REVIEW,
                f"{count} debits in the last {self.window_seconds}s "
                f"(review threshold {self.max_debits})",
            )
        return None


class DailyDebitLimitRule:
    """Hard-blocks a transaction that would push 24h outbound volume over the limit."""

    name = "daily_debit_limit"

    def __init__(self, limit_cents: int) -> None:
        self.limit_cents = limit_cents

    async def evaluate(self, session: AsyncSession, ctx: FraudContext) -> RuleOutcome | None:
        since = datetime.now(UTC) - timedelta(days=1)
        already = await _debit_total_since(session, ctx.account_id, since)
        if already + ctx.amount_cents > self.limit_cents:
            return RuleOutcome(
                self.name,
                FraudStatus.BLOCKED,
                f"24h debits {already}+{ctx.amount_cents} exceed limit {self.limit_cents}",
            )
        return None


def default_rules() -> list[FraudRule]:
    settings = get_settings()
    return [
        AmountThresholdRule(settings.FRAUD_REVIEW_AMOUNT_CENTS, settings.FRAUD_BLOCK_AMOUNT_CENTS),
        VelocityRule(settings.FRAUD_VELOCITY_WINDOW_SECONDS, settings.FRAUD_VELOCITY_MAX_DEBITS),
        DailyDebitLimitRule(settings.FRAUD_DAILY_DEBIT_LIMIT_CENTS),
    ]


class FraudService:
    """Composable rule engine that screens outbound transactions before they settle.

    Runs every rule and takes the **most severe** outcome (APPROVED < REVIEW <
    BLOCKED). Rules are injectable so tests can drive deterministic decisions
    without depending on the production thresholds.
    """

    def __init__(self, session: AsyncSession, rules: list[FraudRule] | None = None) -> None:
        self.session = session
        self.rules = rules if rules is not None else default_rules()

    async def evaluate(self, ctx: FraudContext) -> FraudEvaluation:
        triggered: list[RuleOutcome] = []
        for rule in self.rules:
            outcome = await rule.evaluate(self.session, ctx)
            if outcome is not None:
                triggered.append(outcome)

        status = max(
            (o.status for o in triggered),
            key=lambda s: _SEVERITY[s],
            default=FraudStatus.APPROVED,
        )
        return FraudEvaluation(status=status, triggered=triggered)
