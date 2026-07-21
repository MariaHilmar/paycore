from fastapi import APIRouter

from app.api.deps import AdminGuard, SessionDep
from app.schemas.reconciliation import ReconciliationReportOut, TransactionDiscrepancyOut
from app.services.reconciliation import ReconciliationService

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[AdminGuard])


@router.get("/reconciliation", response_model=ReconciliationReportOut)
async def run_reconciliation(session: SessionDep) -> ReconciliationReportOut:
    """Ops endpoint: proves the ledger is internally consistent.

    Protected by the X-Admin-Key header. Returns a health report cross-checking
    every ledger entry against its transaction.
    """
    report = await ReconciliationService(session).run()
    return ReconciliationReportOut(
        is_healthy=report.is_healthy,
        is_balanced=report.is_balanced,
        total_accounts=report.total_accounts,
        total_transactions=report.total_transactions,
        transactions_by_status=report.transactions_by_status,
        total_debit_cents=report.total_debit_cents,
        total_credit_cents=report.total_credit_cents,
        discrepancies=[
            TransactionDiscrepancyOut(
                transaction_id=d.transaction_id,
                expected_amount_cents=d.expected_amount_cents,
                debit_total_cents=d.debit_total_cents,
                credit_total_cents=d.credit_total_cents,
            )
            for d in report.discrepancies
        ],
    )
