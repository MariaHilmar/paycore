import uuid

from fastapi import APIRouter

from app.api.deps import AdminGuard, SessionDep
from app.schemas.fraud import FraudReviewItem, FraudReviewList, FraudReviewResolution
from app.schemas.reconciliation import ReconciliationReportOut, TransactionDiscrepancyOut
from app.services.payment import PaymentService
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


def _resolution(transaction) -> FraudReviewResolution:
    return FraudReviewResolution(
        transaction_id=transaction.id,
        status=transaction.status,
        fraud_status=transaction.fraud_status,
    )


@router.get("/fraud/reviews", response_model=FraudReviewList)
async def list_fraud_reviews(session: SessionDep) -> FraudReviewList:
    """Ops endpoint: transactions held by fraud screening awaiting a manual decision."""
    reviews = await PaymentService(session).list_pending_reviews()
    return FraudReviewList(
        items=[
            FraudReviewItem(
                transaction_id=tx.id,
                type=tx.type,
                amount_cents=tx.amount,
                created_at=tx.created_at,
            )
            for tx in reviews
        ],
        total=len(reviews),
    )


@router.post("/fraud/reviews/{transaction_id}/approve", response_model=FraudReviewResolution)
async def approve_fraud_review(
    transaction_id: uuid.UUID, session: SessionDep
) -> FraudReviewResolution:
    """Release a held transaction, settling it now (funds re-checked at this moment)."""
    transaction = await PaymentService(session).approve_review(transaction_id)
    return _resolution(transaction)


@router.post("/fraud/reviews/{transaction_id}/reject", response_model=FraudReviewResolution)
async def reject_fraud_review(
    transaction_id: uuid.UUID, session: SessionDep
) -> FraudReviewResolution:
    """Reject a held transaction: no money moves, recorded as FAILED."""
    transaction = await PaymentService(session).reject_review(transaction_id)
    return _resolution(transaction)
