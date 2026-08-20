"""
routers/exceptions.py — exception approval endpoints, role-gated per
TODO.md Phase 8's last bullet. See src/api/deps.py's module docstring for
what "role-gated" honestly means here (real authorization logic, demo-grade
identity).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.api.deps import get_db, require_role_for_level, user_role_header
from src.api.schemas import ExceptionResolutionRequest, ExceptionResolutionResponse
from src.api.serializers import serialize_decision, serialize_exception
from src.db.models import Application, Exception_
from src.pricing.eligibility import resolve_exception_and_reprice

router = APIRouter(prefix="/exceptions", tags=["exceptions"])


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
