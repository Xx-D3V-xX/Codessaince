"""
engine.py (src/rules/) — Phase 4 orchestration: context -> pipeline ->
active rules -> evaluate -> resolve -> persist as a Decision, chaining a
re-run to whatever decision was previously current for the application
rather than overwriting it.

Named engine.py to mirror src/features/engine.py's role in its own layer —
this is the rules-engine equivalent, not a generic "orchestrator" catch-all.

Phase 6 addition: evaluate_application() can now run in two modes. Passing
master_row/feature_vector_row (the normal, "fresh evaluation" path) builds
the context and persists it to application.rule_context_snapshot. Omitting
them (master_row=None) re-fires purely from that stored snapshot — no
dependency on Phases 0-2's external storage still holding the same rows it
held at first evaluation — which is what src/rules/exceptions.py's exception
resolution re-fire uses. Returns (Decision, ResolvedDecision) as a pair now,
not just the Decision, so callers (exceptions.py in particular) get the
resolved severity/escalation directly instead of re-parsing triggered_rules
JSON to recover something already computed in this same call.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.audit import write_audit_log
from src.db.models import Application, ApplicationStatus, Decision
from src.rules.context import build_rule_context, is_insufficient_data, pipeline_for
from src.rules.crud import active_rules_for_pipeline
from src.rules.evaluator import evaluate_rule_groups, evaluate_rules, to_jsonable
from src.rules.resolver import ResolvedDecision, resolve_decision


def evaluate_application(
    session: Session,
    application: Application,
    master_row: dict | None = None,
    feature_vector_row: dict | None = None,
    bureau_row: dict | None = None,
    bank_row: dict | None = None,
    actor: str = "engine",
) -> tuple[Decision, ResolvedDecision]:
    """
    full rule-evaluation pipeline for one application against its pipeline's
    currently-active rules. Always inserts a new Decision row — if the
    application already had a current decision (this is a re-run, e.g. after
    a threshold edit or an exception resolution), that previous decision is
    chained via superseded_by_decision_id and marked is_current=False rather
    than overwritten, so every past decision stays independently queryable.

    master_row/feature_vector_row omitted (both None) re-fires from
    application.rule_context_snapshot instead of building a fresh context —
    raises if that snapshot doesn't exist yet (nothing to re-fire from).
    """
    if master_row is not None and feature_vector_row is not None:
        context = build_rule_context(master_row, feature_vector_row, bureau_row=bureau_row, bank_row=bank_row)
    elif application.rule_context_snapshot is not None:
        context = application.rule_context_snapshot
    else:
        raise ValueError(
            "no context available: pass master_row/feature_vector_row for a fresh evaluation, "
            "or ensure application.rule_context_snapshot was already set by a prior evaluation"
        )

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
    # keep the stored context in sync with whatever was actually used —
    # on a fresh evaluation this is the newly-built context, JSON-safe'd the
    # same way evaluator.py's per-rule actual_value is (dates/Decimals to
    # plain types); on a re-fire it's already jsonable (it came from this
    # same column), so this is a no-op re-write of the same value.
    application.rule_context_snapshot = {k: to_jsonable(v) for k, v in context.items()}
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
    return decision, resolved
