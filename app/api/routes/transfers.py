import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentAccount,
    IdempotencyKey,
    PaymentServiceDep,
    SessionDep,
    VerifiedAccount,
)
from app.db.models import Account, TransactionType
from app.schemas.transaction import TransferCreate, TransferOut

router = APIRouter(prefix="/transfers", tags=["transfers"])


async def _account_number(session: AsyncSession, account_id: uuid.UUID) -> str:
    result = await session.execute(select(Account.account_number).where(Account.id == account_id))
    return result.scalar_one()


@router.post("", response_model=TransferOut, status_code=status.HTTP_201_CREATED)
async def create_transfer(
    data: TransferCreate,
    account: VerifiedAccount,
    service: PaymentServiceDep,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
) -> TransferOut:
    transaction = await service.create_transfer(
        from_account_id=account.id,
        to_account_number=data.to_account_number,
        amount_cents=data.amount_cents,
        idempotency_key=idempotency_key,
    )

    to_account_id = uuid.UUID(transaction.extra_data["to_account_id"])
    return TransferOut(
        id=transaction.id,
        status=transaction.status,
        fraud_status=transaction.fraud_status,
        amount_cents=transaction.amount,
        from_account_number=account.account_number,
        to_account_number=await _account_number(session, to_account_id),
        created_at=transaction.created_at,
    )


@router.get("/{transaction_id}", response_model=TransferOut)
async def get_transfer(
    transaction_id: uuid.UUID,
    account: CurrentAccount,
    service: PaymentServiceDep,
    session: SessionDep,
) -> TransferOut:
    transaction = await service.get_transaction(transaction_id)

    if transaction.type != TransactionType.P2P:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="transfer not found")

    from_account_id = uuid.UUID(transaction.extra_data["from_account_id"])
    to_account_id = uuid.UUID(transaction.extra_data["to_account_id"])

    if account.id not in (from_account_id, to_account_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="transfer not found")

    return TransferOut(
        id=transaction.id,
        status=transaction.status,
        fraud_status=transaction.fraud_status,
        amount_cents=transaction.amount,
        from_account_number=await _account_number(session, from_account_id),
        to_account_number=await _account_number(session, to_account_id),
        created_at=transaction.created_at,
    )
