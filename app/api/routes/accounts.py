from fastapi import APIRouter, Query

from app.api.deps import CurrentAccount, SessionDep
from app.schemas.account import AccountOut, StatementEntry, StatementOut
from app.services.ledger import LedgerService

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("/me", response_model=AccountOut)
async def get_my_account(account: CurrentAccount, session: SessionDep) -> AccountOut:
    ledger = LedgerService(session)
    balance = await ledger.get_balance(account.id)
    return AccountOut(
        id=account.id,
        account_number=account.account_number,
        status=account.status,
        balance_cents=balance,
    )


@router.get("/me/statement", response_model=StatementOut)
async def get_my_statement(
    account: CurrentAccount,
    session: SessionDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> StatementOut:
    ledger = LedgerService(session)
    entries, total = await ledger.get_statement(account.id, page=page, page_size=page_size)
    return StatementOut(
        items=[
            StatementEntry(
                transaction_id=entry.transaction_id,
                transaction_type=entry.transaction.type,
                entry_type=entry.entry_type,
                amount_cents=entry.amount,
                created_at=entry.created_at,
            )
            for entry in entries
        ],
        page=page,
        page_size=page_size,
        total=total,
    )
