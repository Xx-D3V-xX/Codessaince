"""
engine.py (src/rules/) — Phase 4 orchestration: context -> pipeline ->
active rules -> evaluate -> resolve -> persist as a Decision, chaining a
re-run to whatever decision was previously current for the application
rather than overwriting it.

Named engine.py to mirror src/features/engine.py's role in its own layer —
this is the rules-engine equivalent, not a generic "orchestrator" catch-all.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.audit import write_audit_log
from src.db.models import Application, ApplicationStatus, Decision
from src.rules.context import build_rule_context, is_insufficient_data, pipeline_for
from src.rules.crud import active_rules_for_pipeline
from src.rules.evaluator import evaluate_rule_groups, evaluate_rules
from src.rules.resolver import resolve_decision


def evaluate_application(
    session: Session,
    application: Application,
    master_row: dict,
    feature_vector_row: dict,
    bureau_row: dict | None = None,
    bank_row: dict | None = None,
    actor: str = "engine",
) -> Decision:
    """
    full rule-evaluation pipeline for one application against its pipeline's
    currently-active rules. Always inserts a new Decision row — if the
    application already had a current decision (this is a re-run, e.g. after
    a threshold edit), that previous decision is chained via
    superseded_by_decision_id and marked is_current=False rather than
    overwritten, so every past decision stays independently queryable.
    """
    context = build_rule_context(master_row, feature_vector_row, bureau_row=bureau_row, bank_row=bank_row)
    pipeline = pipeline_for(application.applicant_type)
    rules = active_rules_for_pipeline(session, pipeline)

    insufficient = is_insufficient_data(context)
    results = evaluate_rules(rules, context)
    group_results = evaluate_rule_groups(results)
    resolved = resolve_decision(group_results, insufficient_data=insufficient)

    decision = Decision(
        application_id=application.id,
        outcome=resolved.outcome,
        rule_version_snapshot={r.rule_code: r.version for r in results},
        triggered_rules=[r.to_dict() for r in results],
    )
    session.add(decision)
    session.flush()

    previously_current = session.execute(
        select(Decision).where(
            Decision.application_id == application.id,
            Decision.is_current.is_(True),
            Decision.id != decision.id,
        )
    ).scalars().all()
    for prev in previously_current:
        prev.is_current = False
        prev.superseded_by_decision_id = decision.id

    application.status = ApplicationStatus.DECISIONED
    session.flush()

    write_audit_log(
        session, actor=actor, action="DECISION_MADE", entity_type="decision",
        entity_id=str(decision.id),
        after={
            "outcome": resolved.outcome.value,
            "severity": resolved.severity.value if resolved.severity else None,
            "escalated": resolved.escalated,
            "insufficient_data": insufficient,
            "reran": bool(previously_current),
        },
    )
    return decision
