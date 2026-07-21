import secrets
import uuid
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import decode_access_token
from app.db.models import Account, User
from app.db.session import get_db

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

SessionDep = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    session: SessionDep,
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    subject = decode_access_token(token)
    if subject is None:
        raise credentials_error

    try:
        user_id = uuid.UUID(subject)
    except ValueError as exc:
        # A well-formed JWT whose `sub` is not a valid UUID is still invalid.
        raise credentials_error from exc

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_error
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_account(
    user: CurrentUser,
    session: SessionDep,
) -> Account:
    result = await session.execute(select(Account).where(Account.user_id == user.id))
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="account not found for user"
        )
    return account


CurrentAccount = Annotated[Account, Depends(get_current_account)]


async def get_verified_account(user: CurrentUser, account: CurrentAccount) -> Account:
    """Guards every money-moving endpoint: the user must have passed KYC.

    Centralizes the verification rule in one place so PIX and transfer routes
    cannot drift apart in how they enforce it.
    """
    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="account must be verified before moving money; call /dev/verify-me first",
        )
    return account


VerifiedAccount = Annotated[Account, Depends(get_verified_account)]


async def get_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    """Required on every money-moving POST so retries never double-charge."""
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key header is required",
        )
    return idempotency_key


IdempotencyKey = Annotated[str, Depends(get_idempotency_key)]


async def require_admin(
    admin_key: Annotated[str | None, Header(alias="X-Admin-Key")] = None,
) -> None:
    """Guards internal/ops endpoints with a service-to-service key.

    Uses a constant-time comparison to avoid leaking the key via timing.
    """
    expected = get_settings().ADMIN_API_KEY
    if not admin_key or not secrets.compare_digest(admin_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing admin key"
        )


AdminGuard = Depends(require_admin)
