import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class AccountStatus(StrEnum):
    ACTIVE = "ACTIVE"
    BLOCKED = "BLOCKED"


class TransactionType(StrEnum):
    """Extensible: FEE can be added later without touching existing rows."""

    PIX_IN = "PIX_IN"
    PIX_OUT = "PIX_OUT"
    P2P = "P2P"


class TransactionStatus(StrEnum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class LedgerEntryType(StrEnum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class FraudStatus(StrEnum):
    """Outcome of fraud screening for a money-moving transaction.

    Orthogonal to TransactionStatus: a transaction can be COMPLETED/APPROVED,
    FAILED/BLOCKED, or PENDING/REVIEW (held awaiting manual resolution).
    """

    APPROVED = "APPROVED"
    REVIEW = "REVIEW"
    BLOCKED = "BLOCKED"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    cpf: Mapped[str] = mapped_column(String(11), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    account: Mapped["Account"] = relationship(back_populates="user", uselist=False)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), unique=True, nullable=True
    )
    """Nullable to allow system accounts (e.g. the PIX settlement contra-account) with no owner."""

    account_number: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    status: Mapped[AccountStatus] = mapped_column(
        SQLEnum(AccountStatus, name="account_status"),
        default=AccountStatus.ACTIVE,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User | None"] = relationship(back_populates="account")
    ledger_entries: Mapped[list["LedgerEntry"]] = relationship(back_populates="account")


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    idempotency_key: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    type: Mapped[TransactionType] = mapped_column(
        SQLEnum(TransactionType, name="transaction_type"), nullable=False
    )
    status: Mapped[TransactionStatus] = mapped_column(
        SQLEnum(TransactionStatus, name="transaction_status"),
        default=TransactionStatus.PENDING,
        nullable=False,
    )
    fraud_status: Mapped[FraudStatus | None] = mapped_column(
        SQLEnum(FraudStatus, name="fraud_status"), nullable=True, default=None
    )
    """Fraud screening outcome. NULL for flows not screened (e.g. deposits) and legacy rows."""

    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    """Amount in cents (integer) - never use float for money."""

    extra_data: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    """Arbitrary context: pix txid, chave pix, counterparty account, etc. Column name: metadata."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    ledger_entries: Mapped[list["LedgerEntry"]] = relationship(back_populates="transaction")

    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (CheckConstraint("amount > 0", name="ck_transactions_amount_positive"),)


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False, index=True
    )
    entry_type: Mapped[LedgerEntryType] = mapped_column(
        SQLEnum(LedgerEntryType, name="ledger_entry_type"), nullable=False
    )
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    """Always positive. Direction is given by entry_type, never by sign."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    transaction: Mapped["Transaction"] = relationship(back_populates="ledger_entries")
    account: Mapped["Account"] = relationship(back_populates="ledger_entries")

    __table_args__ = (CheckConstraint("amount > 0", name="ck_ledger_entries_amount_positive"),)
