"""Centralized mapping of domain exceptions to HTTP responses.

Each service layer raises rich, transport-agnostic domain errors. Instead of
repeating the same try/except -> HTTPException translation in every route, we
register one handler per domain error here and let FastAPI turn them into
consistent JSON responses. Routes stay focused on the happy path.
"""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.services.auth import (
    CpfAlreadyExistsError,
    EmailAlreadyExistsError,
    InvalidCredentialsError,
)
from app.services.ledger import InsufficientBalanceError
from app.services.payment import (
    AccountNotFoundError,
    FraudBlockedError,
    ReviewNotPendingError,
    SelfTransferError,
    TransactionNotFoundError,
)

# Domain error -> (HTTP status, detail). When ``detail`` is None the exception's
# own message (``str(exc)``) is surfaced - used for errors that already carry a
# validation-specific, user-safe message.
_ERROR_MAP: dict[type[Exception], tuple[int, str | None]] = {
    EmailAlreadyExistsError: (status.HTTP_409_CONFLICT, "email already registered"),
    CpfAlreadyExistsError: (status.HTTP_409_CONFLICT, "cpf already registered"),
    InvalidCredentialsError: (status.HTTP_401_UNAUTHORIZED, "invalid email or password"),
    SelfTransferError: (status.HTTP_400_BAD_REQUEST, "cannot transfer to your own account"),
    AccountNotFoundError: (status.HTTP_404_NOT_FOUND, "destination account not found"),
    FraudBlockedError: (status.HTTP_403_FORBIDDEN, "transaction blocked by fraud screening"),
    InsufficientBalanceError: (status.HTTP_422_UNPROCESSABLE_ENTITY, "insufficient balance"),
    TransactionNotFoundError: (status.HTTP_404_NOT_FOUND, "transaction not found"),
    ReviewNotPendingError: (status.HTTP_409_CONFLICT, "transaction is not awaiting review"),
}


def _domain_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    status_code, detail = _ERROR_MAP[type(exc)]
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail if detail is not None else str(exc)},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Wire every domain error to its HTTP response. Call once at app startup."""
    for exc_type in _ERROR_MAP:
        app.add_exception_handler(exc_type, _domain_error_handler)
