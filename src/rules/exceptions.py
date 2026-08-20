"""
exceptions.py — Phase 6's exception workflow: routing a decision that
requires exception review to the right level/queue, and resolving that
review (approve/reject by an authorized role), per TODO.md Phase 6.

Layered ON TOP of src/rules/engine.py rather than folded into it, to keep
Phase 4's scope (evaluate a decision) and Phase 6's scope (what happens
next, given a decision that needs human review) as separate concerns —
also avoids a circular import, since resolving an exception needs to
re-fire evaluate_application(), and evaluate_application() itself has no
reason to know about the exceptions table.

**Honesty of the audit trail, deliberately preserved**: a Decision's
`outcome` is what the RULES ENGINE concluded and never changes retroactively
to reflect a human override — relabeling a human-approved exception as
STP_APPROVED would misrepresent it as never having needed review at all
(STP literally means "straight-through", no human touched it). The human
verdict lives on the Exception_ row's own `status` instead. Use
effective_outcome() below when a caller (Phase 7 eligibility/pricing, Phase
8 API, Phase 9 UI) needs one combined "is this application cleared to
proceed" answer — it composes the two facts rather than collapsing them
into a single possibly-misleading Decision.outcome value.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.audit import write_audit_log
from src.db.models import (
    Application,
    Decision,
    DecisionOutcome,
    Exception_,
    ExceptionLevel,
    ExceptionStatus,
)
from src.rules.engine import evaluate_application
from src.rules.resolver import ResolvedDecision

# placeholder role/queue names for a demo — a real deployment would route to
# named individuals or a proper queueing system; not this project's scope.
QUEUE_BY_LEVEL = {
    ExceptionLevel.L1: "credit_ops_l1",
    ExceptionLevel.L2: "credit_ops_l2",
    ExceptionLevel.CREDIT_HEAD: "credit_head",
}


def route_exception(session: Session, decision: Decision, resolved: ResolvedDecision, actor: str) -> Exception_ | None:
    """
    creates the Exception_ row for a decision that needs review, at the
    severity resolve_decision() already computed (including any
    count-based escalation) — no-op (returns None) for any other outcome.
    One Exception_ per Decision, not per fired rule_group: a credit officer
    reviews the whole flagged case at once, not rule-by-rule (matches
    CLAUDE.md §3.4's own "route to the highest severity among all fired
    exception rules" framing — one routing decision per case).
    """
    if decision.outcome != DecisionOutcome.EXCEPTION_REQUIRED or resolved.severity is None:
        return None

    exception = Exception_(
        decision_id=decision.id,
        level=resolved.severity,
        status=ExceptionStatus.PENDING,
        assigned_to=QUEUE_BY_LEVEL[resolved.severity],
    )
    session.add(exception)
    session.flush()

    write_audit_log(
        session, actor=actor, action="EXCEPTION_ROUTED", entity_type="exception", entity_id=str(exception.id),
        after={"decision_id": str(decision.id), "level": resolved.severity.value, "assigned_to": exception.assigned_to,
               "escalated": resolved.escalated},
    )
    return exception


def evaluate_and_route(
    session: Session,
    application: Application,
    master_row: dict | None = None,
    feature_vector_row: dict | None = None,
    bureau_row: dict | None = None,
    bank_row: dict | None = None,
    actor: str = "engine",
) -> tuple[Decision, Exception_ | None]:
    """evaluate_application() + route_exception() in one call — the normal entry point for a fresh (or re-fired) evaluation."""
    decision, resolved = evaluate_application(session, application, master_row, feature_vector_row, bureau_row, bank_row, actor)
    exception = route_exception(session, decision, resolved, actor)
    return decision, exception


def resolve_application_exception(
    session: Session,
    exception: Exception_,
    action: Literal["APPROVE", "REJECT"],
    resolved_by: str,
    notes: str | None = None,
) -> tuple[Exception_, Decision, Exception_ | None]:
    """
    the Phase 6 "exception resolution endpoint": approve/reject by an
    authorized role, then re-fires the decision for the same application
    from its stored rule_context_snapshot (no dependency on Phases 0-2's
    external storage still holding the same rows).

    Re-firing is unconditional (both APPROVE and REJECT) per TODO.md's own
    wording. On its own this doesn't change the re-fired Decision's outcome
    — the rules engine's facts haven't changed, only a human's judgment on
    them has, and that judgment is recorded on `exception`, not smuggled
    into a relabeled Decision.outcome (see module docstring). Its real value
    is a timestamped, chained confirmation that the automated facts were
    re-checked and are unchanged as of this resolution, plus (if the
    re-fired decision is itself EXCEPTION_REQUIRED again, e.g. a second,
    independent exception-worthy issue on the same application) a freshly
    routed Exception_ for whatever remains.
    """
    if exception.status != ExceptionStatus.PENDING:
        raise ValueError(f"exception {exception.id} is already {exception.status.value}, not PENDING")

    before = {"status": exception.status.value, "resolved_by": exception.resolved_by, "resolved_at": None}
    exception.status = ExceptionStatus.APPROVED if action == "APPROVE" else ExceptionStatus.REJECTED
    exception.resolved_by = resolved_by
    exception.resolved_at = datetime.now(timezone.utc)
    exception.notes = notes
    session.flush()

    write_audit_log(
        session, actor=resolved_by, action=f"EXCEPTION_{action}D", entity_type="exception", entity_id=str(exception.id),
        before=before, after={"status": exception.status.value, "resolved_by": resolved_by, "notes": notes},
    )

    decision = session.get(Decision, exception.decision_id)
    application = session.get(Application, decision.application_id)
    new_decision, new_exception = evaluate_and_route(session, application, actor=resolved_by)

    return exception, new_decision, new_exception


def effective_outcome(decision: Decision, exception: Exception_ | None = None) -> str:
    """
    composes Decision.outcome with an associated Exception_'s resolution
    into one "is this application cleared to proceed" answer, for later
    phases (Phase 7 eligibility/pricing, Phase 8 API, Phase 9 UI) — without
    collapsing the two facts into a misleading Decision.outcome value.
    Returns one of: "APPROVED", "REJECTED", "PENDING_EXCEPTION", "INSUFFICIENT_DATA".
    """
    if decision.outcome == DecisionOutcome.STP_APPROVED:
        return "APPROVED"
    if decision.outcome == DecisionOutcome.HARD_REJECT:
        return "REJECTED"
    if decision.outcome == DecisionOutcome.INSUFFICIENT_DATA:
        return "INSUFFICIENT_DATA"
    # EXCEPTION_REQUIRED
    if exception is None or exception.status == ExceptionStatus.PENDING:
        return "PENDING_EXCEPTION"
    return "APPROVED" if exception.status == ExceptionStatus.APPROVED else "REJECTED"


def pending_exceptions_for_level(session: Session, level: ExceptionLevel) -> list[Exception_]:
    stmt = select(Exception_).where(Exception_.level == level, Exception_.status == ExceptionStatus.PENDING)
    return list(session.execute(stmt).scalars().all())
