"""
routers/applications.py — POST /applications, GET /applications/{id}/decision,
POST /applications/{id}/rerun, GET /applications/{id}/audit.

POST /applications follows the async saga pattern TODO.md Phase 8 calls
for: the request only creates the Application row and returns 202 +
application_id immediately; the actual evaluate -> route -> price pipeline
(potentially the slowest part — a model inference call, several DB round
trips) runs in a FastAPI BackgroundTask AFTER the response is sent, using
its own fresh DB session (the request-scoped one is already closed by
then). GET .../decision is the poll endpoint a client uses to find out
when that background work finished.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.api.dataset import ApplicantDataset
from src.api.deps import get_dataset, get_db
from src.api.schemas import AuditLogEntry, AuditTrailResponse, DecisionResponse, RerunResponse, SubmitApplicationRequest, SubmitApplicationResponse
from src.api.serializers import serialize_decision
from src.db.audit import write_audit_log
from src.db.models import AuditLog, Application, ApplicantType, ApplicationStatus, Decision, Exception_
from src.db.session import get_session
from src.pricing.eligibility import evaluate_route_and_price

router = APIRouter(prefix="/applications", tags=["applications"])


def _run_evaluation(application_id: UUID, master_row: dict, feature_vector_row: dict, bureau_row: dict | None, bank_row: dict | None) -> None:
    """the background task body — its own session, independent of the request that triggered it."""
    with get_session() as session:
        application = session.get(Application, application_id)
        if application is None:
            return
        try:
            evaluate_route_and_price(session, application, master_row, feature_vector_row, bureau_row, bank_row, actor="api")
        except Exception as exc:  # noqa: BLE001 -- a background task's only recourse is to record the failure, not raise into nowhere
            application.status = ApplicationStatus.FAILED
            write_audit_log(session, actor="api", action="EVALUATION_FAILED", entity_type="application",
                             entity_id=str(application_id), after={"error": str(exc)})


@router.post("", response_model=SubmitApplicationResponse, status_code=202)
def submit_application(
    request: SubmitApplicationRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    dataset: ApplicantDataset = Depends(get_dataset),
) -> SubmitApplicationResponse:
    found = dataset.get(request.applicant_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"applicant_id {request.applicant_id!r} not found in the ingested dataset")
    master_row, feature_vector_row, bureau_row, bank_row = found

    requested_loan_amount = request.requested_loan_amount if request.requested_loan_amount is not None else master_row["requested_loan_amount"]
    requested_tenure_months = request.requested_tenure_months if request.requested_tenure_months is not None else master_row["requested_tenure_months"]
    if requested_loan_amount != master_row["requested_loan_amount"] or requested_tenure_months != master_row["requested_tenure_months"]:
        master_row = {**master_row, "requested_loan_amount": requested_loan_amount, "requested_tenure_months": requested_tenure_months}

    application = Application(
        applicant_id=request.applicant_id,
        applicant_type=ApplicantType(master_row["applicant_type"]),
        requested_loan_amount=requested_loan_amount,
        requested_tenure_months=requested_tenure_months,
        status=ApplicationStatus.RECEIVED,
        normalized_profile_snapshot=master_row,
    )
    db.add(application)
    db.flush()
    application_id = application.id
    db.commit()

    background_tasks.add_task(_run_evaluation, application_id, master_row, feature_vector_row, bureau_row, bank_row)

    return SubmitApplicationResponse(application_id=application_id, status=ApplicationStatus.RECEIVED.value)


@router.get("/{application_id}/decision", response_model=DecisionResponse)
def get_decision(application_id: UUID, db: Session = Depends(get_db)) -> DecisionResponse:
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="application not found")

    current_decision = db.execute(
        select(Decision).where(Decision.application_id == application_id, Decision.is_current.is_(True))
    ).scalars().first()

    return serialize_decision(db, application, current_decision)


@router.post("/{application_id}/rerun", response_model=RerunResponse, status_code=202)
def rerun_application(application_id: UUID, background_tasks: BackgroundTasks, db: Session = Depends(get_db)) -> RerunResponse:
    """re-evaluates the stored application (Application.rule_context_snapshot) against whatever rules are active NOW — the PS-1 demo scenario 5 flow, exposed over HTTP."""
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="application not found")
    if application.rule_context_snapshot is None:
        raise HTTPException(status_code=409, detail="application has no prior evaluation to re-run from")

    application.status = ApplicationStatus.PROCESSING
    db.commit()

    background_tasks.add_task(_run_evaluation, application_id, None, None, None, None)
    return RerunResponse(application_id=application_id, status=ApplicationStatus.PROCESSING.value)


@router.get("/{application_id}/audit", response_model=AuditTrailResponse)
def get_audit_trail(application_id: UUID, db: Session = Depends(get_db)) -> AuditTrailResponse:
    """
    the full audit trail for one application: every audit_log row that
    touches the application itself, any of its decisions, or any of their
    exceptions -- audit entries are entity-agnostic (src/db/models.py's
    AuditLog), so this aggregates across the three entity_types a single
    application's history actually spans.
    """
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="application not found")

    decision_ids = [str(d.id) for d in db.execute(select(Decision).where(Decision.application_id == application_id)).scalars().all()]
    exception_ids = [str(e.id) for e in db.execute(select(Exception_).where(Exception_.decision_id.in_(decision_ids))).scalars().all()] if decision_ids else []

    conditions = [(AuditLog.entity_type == "application") & (AuditLog.entity_id == str(application_id))]
    if decision_ids:
        conditions.append((AuditLog.entity_type == "decision") & (AuditLog.entity_id.in_(decision_ids)))
    if exception_ids:
        conditions.append((AuditLog.entity_type == "exception") & (AuditLog.entity_id.in_(exception_ids)))

    entries = db.execute(select(AuditLog).where(or_(*conditions)).order_by(AuditLog.timestamp)).scalars().all()

    return AuditTrailResponse(
        application_id=application_id,
        entries=[
            AuditLogEntry(id=e.id, actor=e.actor, action=e.action, entity_type=e.entity_type, entity_id=e.entity_id,
                          before=e.before, after=e.after, timestamp=e.timestamp)
            for e in entries
        ],
    )
