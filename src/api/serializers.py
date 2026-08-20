"""serializers.py — ORM row -> API response model, shared across routers."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.schemas import DecisionResponse, ExceptionSummary, RuleResponse
from src.db.models import Application, Decision, Exception_
from src.rules.exceptions import effective_outcome


def serialize_exception(exc: Exception_) -> ExceptionSummary:
    return ExceptionSummary(
        id=exc.id, level=exc.level.value, status=exc.status.value, assigned_to=exc.assigned_to,
        resolved_by=exc.resolved_by, resolved_at=exc.resolved_at, notes=exc.notes,
    )


def get_exception_for_decision(session: Session, decision_id) -> Exception_ | None:
    stmt = select(Exception_).where(Exception_.decision_id == decision_id)
    return session.execute(stmt).scalars().first()


def serialize_decision(session: Session, application: Application, decision: Decision | None) -> DecisionResponse:
    if decision is None:
        return DecisionResponse(application_id=application.id, application_status=application.status.value)

    exception = get_exception_for_decision(session, decision.id)
    return DecisionResponse(
        application_id=application.id,
        application_status=application.status.value,
        decision_id=decision.id,
        outcome=decision.outcome.value,
        effective_outcome=effective_outcome(decision, exception),
        risk_grade=decision.risk_grade,
        eligible_amount=float(decision.eligible_amount) if decision.eligible_amount is not None else None,
        interest_rate=float(decision.interest_rate) if decision.interest_rate is not None else None,
        tenure_months=decision.tenure_months,
        model_risk_score=float(decision.model_risk_score) if decision.model_risk_score is not None else None,
        decided_at=decision.decided_at,
        is_current=decision.is_current,
        rule_version_snapshot=decision.rule_version_snapshot,
        triggered_rules=decision.triggered_rules,
        exception=serialize_exception(exception) if exception else None,
    )


def serialize_rule(rule) -> RuleResponse:
    return RuleResponse(
        id=rule.id, rule_code=rule.rule_code, version=rule.version, pipeline=rule.pipeline.value,
        field=rule.field, operator=rule.operator.value, value=rule.value, outcome=rule.outcome.value,
        severity=rule.severity.value if rule.severity else None, reason_code=rule.reason_code,
        priority=rule.priority, rule_group=rule.rule_group, active=rule.active,
        effective_from=rule.effective_from, effective_to=rule.effective_to,
    )
