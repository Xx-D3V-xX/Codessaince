"""
routers/exceptions.py — exception approval endpoints, role-gated per
TODO.md Phase 8's last bullet. See src/api/deps.py's module docstring for
what "role-gated" honestly means here (real authorization logic, demo-grade
identity).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.deps import get_db, require_role_for_level, user_role_header
from src.api.schemas import ExceptionQueueEntry, ExceptionResolutionRequest, ExceptionResolutionResponse
from src.api.serializers import serialize_decision, serialize_exception
from src.db.models import Application, Decision, Exception_, ExceptionLevel, ExceptionStatus
from src.pricing.eligibility import resolve_exception_and_reprice

router = APIRouter(prefix="/exceptions", tags=["exceptions"])


@router.get("", response_model=list[ExceptionQueueEntry])
def list_exceptions(level: ExceptionLevel, status: ExceptionStatus = ExceptionStatus.PENDING, db: Session = Depends(get_db)) -> list[ExceptionQueueEntry]:
    """
    the exception queue view Phase 9's UI needs (L1/L2/Credit Head, role-
    gated on the frontend side by only showing the queue for the role
    selected — the actual authorization check still happens server-side on
    approve/reject, this listing is not itself a sensitive action).
    """
    stmt = (
        select(Exception_, Decision, Application)
        .join(Decision, Exception_.decision_id == Decision.id)
        .join(Application, Decision.application_id == Application.id)
        .where(Exception_.level == level, Exception_.status == status)
        .order_by(Exception_.created_at)
    )
    rows = db.execute(stmt).all()
    return [
        ExceptionQueueEntry(
            id=exc.id, level=exc.level.value, status=exc.status.value, assigned_to=exc.assigned_to,
            application_id=app.id, applicant_id=app.applicant_id, decision_outcome=decision.outcome.value,
            risk_grade=decision.risk_grade,
            eligible_amount=float(decision.eligible_amount) if decision.eligible_amount is not None else None,
            created_at=exc.created_at,
        )
        for exc, decision, app in rows
    ]


def _resolve(exception_id: UUID, action: str, request: ExceptionResolutionRequest, db: Session, x_user_role: str | None) -> ExceptionResolutionResponse:
    exception = db.get(Exception_, exception_id)
    if exception is None:
        raise HTTPException(status_code=404, detail="exception not found")

    require_role_for_level(exception.level, x_user_role)

    try:
        resolved_exception, new_decision, _new_exception, _pricing = resolve_exception_and_reprice(
            db, exception, action=action, resolved_by=request.resolved_by, notes=request.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    application = db.get(Application, new_decision.application_id)
    return ExceptionResolutionResponse(
        exception=serialize_exception(resolved_exception),
        decision=serialize_decision(db, application, new_decision),
    )


@router.post("/{exception_id}/approve", response_model=ExceptionResolutionResponse)
def approve_exception(
    exception_id: UUID, request: ExceptionResolutionRequest, db: Session = Depends(get_db), x_user_role: str | None = Depends(user_role_header),
) -> ExceptionResolutionResponse:
    return _resolve(exception_id, "APPROVE", request, db, x_user_role)


@router.post("/{exception_id}/reject", response_model=ExceptionResolutionResponse)
def reject_exception(
    exception_id: UUID, request: ExceptionResolutionRequest, db: Session = Depends(get_db), x_user_role: str | None = Depends(user_role_header),
) -> ExceptionResolutionResponse:
    return _resolve(exception_id, "REJECT", request, db, x_user_role)
