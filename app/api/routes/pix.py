import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import IdempotencyKey, SessionDep, VerifiedAccount
from app.schemas.transaction import (
    PixDepositCreate,
    PixDepositOut,
    PixWithdrawCreate,
    PixWithdrawOut,
)
from app.services.payment import (
    FraudBlockedError,
    InsufficientBalanceError,
    PaymentService,
    TransactionNotFoundError,
)

router = APIRouter(prefix="/pix", tags=["pix"])


def _to_out(transaction) -> PixDepositOut:
    return PixDepositOut(
        txid=str(transaction.id),
        status=transaction.status,
        amount_cents=transaction.amount,
        qr_code=f"00020126PAYCORE-MOCK-QR-{transaction.id}",
        created_at=transaction.created_at,
    )


@router.post("/deposit", response_model=PixDepositOut, status_code=status.HTTP_201_CREATED)
async def create_deposit(
    data: PixDepositCreate,
    account: VerifiedAccount,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
) -> PixDepositOut:
    service = PaymentService(session)
    transaction = await service.create_deposit(
        account_id=account.id,
        amount_cents=data.amount_cents,
        idempotency_key=idempotency_key,
    )
    return _to_out(transaction)


@router.post("/withdraw", response_model=PixWithdrawOut, status_code=status.HTTP_201_CREATED)
async def create_withdrawal(
    data: PixWithdrawCreate,
    account: VerifiedAccount,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
) -> PixWithdrawOut:
    service = PaymentService(session)
    try:
        transaction = await service.create_withdrawal(
            account_id=account.id,
            amount_cents=data.amount_cents,
            idempotency_key=idempotency_key,
        )
    except FraudBlockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="transaction blocked by fraud screening"
        ) from exc
    except InsufficientBalanceError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="insufficient balance"
        ) from exc
    return PixWithdrawOut(
        id=transaction.id,
        status=transaction.status,
        fraud_status=transaction.fraud_status,
        amount_cents=transaction.amount,
        created_at=transaction.created_at,
    )


@router.post("/deposit/{txid}/pay", response_model=PixDepositOut)
async def pay_deposit(txid: uuid.UUID, session: SessionDep) -> PixDepositOut:
    """Simulates the PIX network settling the payment - normally an async webhook.

    Intentionally unauthenticated: it stands in for a server-to-server callback
    from the PIX provider, not a user-facing action.
    """
    service = PaymentService(session)
    try:
        transaction = await service.confirm_deposit(txid)
    except TransactionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="deposit not found"
        ) from exc
    return _to_out(transaction)
