import asyncio
import os
import secrets
import sys
import uuid
from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Account  # noqa: F401 - ensures all models register on Base.metadata
from app.schemas.auth import UserRegister
from app.services.auth import AuthService
from app.services.payment import PaymentService

# psycopg3 async is not compatible with Windows ProactorEventLoop (default in Python 3.8+).
# Must be set before any event loop is created.
if sys.platform == "win32":
    from asyncio.windows_events import _WindowsSelectorEventLoopPolicy

    asyncio.set_event_loop_policy(_WindowsSelectorEventLoopPolicy())  # noqa: ASYNC100


TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://paycore:paycore@localhost:5432/paycore_test",
)


def random_cpf() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(11))


def random_email() -> str:
    return f"user-{uuid.uuid4().hex[:10]}@test.dev"


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
def session_factory() -> async_sessionmaker[AsyncSession]:
    """Used by tests that need multiple independent sessions against the same DB
    (e.g. to exercise real row-locking under concurrent transfers)."""
    engine = create_async_engine(TEST_DATABASE_URL)
    return async_sessionmaker(bind=engine, expire_on_commit=False)


async def create_verified_account(session: AsyncSession, *, funded_cents: int = 0) -> Account:
    auth = AuthService(session)
    user = await auth.register(
        UserRegister(email=random_email(), cpf=random_cpf(), password="a-strong-password")
    )
    await auth.verify_user(user.id)

    from sqlalchemy import select

    result = await session.execute(select(Account).where(Account.user_id == user.id))
    account = result.scalar_one()

    if funded_cents:
        payment = PaymentService(session)
        deposit = await payment.create_deposit(
            account_id=account.id,
            amount_cents=funded_cents,
            idempotency_key=str(uuid.uuid4()),
        )
        await payment.confirm_deposit(deposit.id)

    return account
