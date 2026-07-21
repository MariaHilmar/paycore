from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, SessionDep
from app.core.security import create_access_token
from app.schemas.auth import Token, UserLogin, UserOut, UserRegister
from app.services.auth import (
    AuthService,
    CpfAlreadyExistsError,
    EmailAlreadyExistsError,
    InvalidCredentialsError,
)

router = APIRouter(tags=["auth"])


@router.post("/auth/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(data: UserRegister, session: SessionDep) -> UserOut:
    service = AuthService(session)
    try:
        user = await service.register(data)
    except EmailAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="email already registered"
        ) from exc
    except CpfAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="cpf already registered"
        ) from exc
    return UserOut.model_validate(user)


@router.post("/auth/login", response_model=Token)
async def login(data: UserLogin, session: SessionDep) -> Token:
    service = AuthService(session)
    try:
        user = await service.authenticate(data.email, data.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid email or password"
        ) from exc
    return Token(access_token=create_access_token(subject=str(user.id)))


@router.post("/dev/verify-me", response_model=UserOut)
async def verify_me(user: CurrentUser, session: SessionDep) -> UserOut:
    """Dev-only shortcut: marks the current user as KYC-verified without a document upload."""
    service = AuthService(session)
    verified_user = await service.verify_user(user.id)
    return UserOut.model_validate(verified_user)
