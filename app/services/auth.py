import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.db.models import Account, User
from app.schemas.auth import UserRegister


class AuthError(Exception):
    """Base class for auth-related domain errors."""


class EmailAlreadyExistsError(AuthError):
    pass


class CpfAlreadyExistsError(AuthError):
    pass


class InvalidCredentialsError(AuthError):
    pass


class UserNotFoundError(AuthError):
    pass


def generate_account_number() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(10))


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _get_by_email(self, email: str) -> User | None:
        result = await self.session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def _get_by_cpf(self, cpf: str) -> User | None:
        result = await self.session.execute(select(User).where(User.cpf == cpf))
        return result.scalar_one_or_none()

    async def register(self, data: UserRegister) -> User:
        if await self._get_by_email(data.email) is not None:
            raise EmailAlreadyExistsError(data.email)
        if await self._get_by_cpf(data.cpf) is not None:
            raise CpfAlreadyExistsError(data.cpf)

        user = User(
            email=data.email,
            cpf=data.cpf,
            password_hash=hash_password(data.password),
        )
        self.session.add(user)
        await self.session.flush()

        account_number = generate_account_number()
        for _ in range(5):
            existing = await self.session.execute(
                select(Account).where(Account.account_number == account_number)
            )
            if existing.scalar_one_or_none() is None:
                break
            account_number = generate_account_number()

        account = Account(user_id=user.id, account_number=account_number)
        self.session.add(account)

        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def authenticate(self, email: str, password: str) -> User:
        user = await self._get_by_email(email)
        if user is None or not verify_password(password, user.password_hash):
            raise InvalidCredentialsError()
        return user

    async def get_by_id(self, user_id: uuid.UUID) -> User:
        result = await self.session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise UserNotFoundError(str(user_id))
        return user

    async def verify_user(self, user_id: uuid.UUID) -> User:
        """Dev-only shortcut standing in for a real KYC document review flow."""
        user = await self.get_by_id(user_id)
        user.is_verified = True
        await self.session.commit()
        await self.session.refresh(user)
        return user
