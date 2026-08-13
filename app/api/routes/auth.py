from fastapi import APIRouter, Request, status

from app.api.deps import CurrentUser, DebugOnly, SessionDep
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.core.security import create_access_token
from app.schemas.auth import Token, UserLogin, UserOut, UserRegister
from app.services.auth import AuthService

router = APIRouter(tags=["auth"])


@router.post("/auth/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
@limiter.limit(lambda: get_settings().RATE_LIMIT_AUTH)
async def register(request: Request, data: UserRegister, session: SessionDep) -> UserOut:
    user = await AuthService(session).register(data)
    return UserOut.model_validate(user)


@router.post("/auth/login", response_model=Token)
@limiter.limit(lambda: get_settings().RATE_LIMIT_AUTH)
async def login(request: Request, data: UserLogin, session: SessionDep) -> Token:
    user = await AuthService(session).authenticate(data.email, data.password)
    return Token(access_token=create_access_token(subject=str(user.id)))


@router.post("/dev/verify-me", response_model=UserOut, dependencies=[DebugOnly])
async def verify_me(user: CurrentUser, session: SessionDep) -> UserOut:
    """Dev-only shortcut: marks the current user as KYC-verified without a document upload.

    Gated behind DEBUG: unreachable (404) in a production deployment.
    """
    verified_user = await AuthService(session).verify_user(user.id)
    return UserOut.model_validate(verified_user)
