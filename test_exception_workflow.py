"""
test_exception_workflow.py — Phase 6 verification, same standard of
evidence as every prior phase: real applicant data, a live Postgres
instance, concrete assertions.

Covers:
  1. A real applicant whose decision resolves to EXCEPTION_REQUIRED gets an
     Exception_ row auto-routed to the correct level/queue (including a
     genuinely escalated case, not just a plain L1).
  2. Resolving an exception (APPROVE) re-fires the decision purely from the
     application's stored rule_context_snapshot (no master_row/
     feature_vector_row passed at all) -- confirms Phase 6's snapshot-based
     re-fire actually works standalone, not just "in theory".
  3. Re-firing produces a new, properly chained Decision -- and confirms the
     original Decision.outcome is NEVER retroactively relabeled (still
     literally EXCEPTION_REQUIRED, never rewritten to STP_APPROVED) even
     after human approval, per exceptions.py's honesty-of-the-audit-trail
     design.
  4. effective_outcome() composes Decision + Exception_ correctly across
     all four cases (STP, HARD_REJECT, pending exception, resolved exception).
  5. Resolving an already-resolved exception raises, rather than silently
     double-processing it.
"""

from sqlalchemy import select

from src.db.models import (
    Application, ApplicantPipeline, ApplicantType, ApplicationStatus,
    Decision, DecisionOutcome, Exception_, ExceptionLevel, ExceptionStatus,
)
from src.db.session import get_session
from src.features.cross_source import compute_batch_cross_source, merge_into_vectors
from src.features.engine import FeatureEngine
from src.ingestion.applicant_adapter import load_and_adapt, to_engine_frames
from src.rules.context import build_rule_context, is_insufficient_data, pipeline_for
from src.rules.crud import active_rules_for_pipeline
from src.rules.engine import evaluate_application
from src.rules.evaluator import evaluate_rule_groups, evaluate_rules
from src.rules.exceptions import (
    QUEUE_BY_LEVEL,
    effective_outcome,
    evaluate_and_route,
    resolve_application_exception,
)
from src.rules.resolver import resolve_decision

print("=== loading real applicant data ===")
result = load_and_adapt()
bureau_df, bank_df, itr_df = to_engine_frames(result)
engine = FeatureEngine()
vectors = engine.compute_batch(bureau_df, bank_df, itr_df)
cross_source_by_id = compute_batch_cross_source(result)
vectors = merge_into_vectors(vectors, cross_source_by_id)

master_by_id = {m.applicant_id: m.model_dump() for m in result.master}
bureau_by_id = {b.applicant_id: b.model_dump() for b in result.bureau}
bank_by_id = {b.applicant_id: b.model_dump() for b in result.bank}
vector_by_id = {v.applicant_id: v.model_dump() for v in vectors}
print(f"loaded {len(master_by_id)} applicants\n")

# ---------------------------------------------------------------------------
# find a plain L1 candidate and an escalated (CREDIT_HEAD) candidate
# ---------------------------------------------------------------------------
print("=== finding a plain-L1 candidate and an escalated candidate ===")
with get_session() as s:
    ind_rules = active_rules_for_pipeline(s, ApplicantPipeline.INDIVIDUAL)

l1_candidate = None
escalated_candidate = None
for applicant_id, master_row in master_by_id.items():
    if master_row["applicant_type"] not in ("SALARIED", "SELF_EMPLOYED"):
        continue
    feature_row = vector_by_id.get(applicant_id)
    bureau_row = bureau_by_id.get(applicant_id)
    bank_row = bank_by_id.get(applicant_id)
    if feature_row is None or bureau_row is None:
        continue
    context = build_rule_context(master_row, feature_row, bureau_row=bureau_row, bank_row=bank_row)
    if is_insufficient_data(context):
        continue
    results = evaluate_rules(ind_rules, context)
    group_results = evaluate_rule_groups(results)
    resolved = resolve_decision(group_results, insufficient_data=False)

    if resolved.outcome.value == "EXCEPTION_REQUIRED":
        if not resolved.escalated and resolved.severity.value == "L1" and l1_candidate is None:
            l1_candidate = applicant_id
        if resolved.escalated and escalated_candidate is None:
            escalated_candidate = applicant_id
    if l1_candidate and escalated_candidate:
        break

assert l1_candidate is not None, "no plain L1 candidate found"
assert escalated_candidate is not None, "no escalated candidate found"
print(f"L1 candidate: {l1_candidate}")
print(f"escalated candidate: {escalated_candidate}\n")


def make_application(applicant_id: str) -> Application:
    master_row = master_by_id[applicant_id]
    return Application(
        applicant_id=applicant_id,
        applicant_type=ApplicantType(master_row["applicant_type"]),
        requested_loan_amount=master_row["requested_loan_amount"],
        requested_tenure_months=master_row["requested_tenure_months"],
        status=ApplicationStatus.RECEIVED,
    )


# ---------------------------------------------------------------------------
# Part 1: routing -- plain L1 and escalated cases
# ---------------------------------------------------------------------------
print("=== part 1: exception routing ===")
with get_session() as s:
    app = make_application(l1_candidate)
    s.add(app)
    s.flush()
    master_row = master_by_id[l1_candidate]
    decision, exception = evaluate_and_route(
        s, app, master_row, vector_by_id[l1_candidate],
        bureau_row=bureau_by_id.get(l1_candidate), bank_row=bank_by_id.get(l1_candidate), actor="test_exception_workflow",
    )
    assert decision.outcome == DecisionOutcome.EXCEPTION_REQUIRED
    assert exception is not None
    assert exception.level == ExceptionLevel.L1
    assert exception.status == ExceptionStatus.PENDING
    assert exception.assigned_to == QUEUE_BY_LEVEL[ExceptionLevel.L1]
    print(f"L1 case: decision={decision.outcome.value}, exception level={exception.level.value}, assigned_to={exception.assigned_to}")
    print("assert: plain L1 exception routed correctly -- PASS")

    l1_app_id, l1_exception_id, l1_decision_id = app.id, exception.id, decision.id

with get_session() as s:
    app = make_application(escalated_candidate)
    s.add(app)
    s.flush()
    master_row = master_by_id[escalated_candidate]
    decision, exception = evaluate_and_route(
        s, app, master_row, vector_by_id[escalated_candidate],
        bureau_row=bureau_by_id.get(escalated_candidate), bank_row=bank_by_id.get(escalated_candidate), actor="test_exception_workflow",
    )
    assert exception is not None
    assert exception.level != ExceptionLevel.L1, f"expected an escalated level, got {exception.level}"
    print(f"escalated case: decision={decision.outcome.value}, exception level={exception.level.value} (escalated from a lower severity), assigned_to={exception.assigned_to}")
    print("assert: count-based escalation reflected correctly in routing -- PASS\n")

# ---------------------------------------------------------------------------
# Part 2+3: resolve (APPROVE) via snapshot-only re-fire, honest audit trail
# ---------------------------------------------------------------------------
print("=== part 2+3: resolving the L1 exception (APPROVE), re-fired from stored snapshot only ===")
with get_session() as s:
    exception = s.get(Exception_, l1_exception_id)
    original_decision = s.get(Decision, l1_decision_id)
    original_outcome = original_decision.outcome

    resolved_exception, new_decision, new_exception = resolve_application_exception(
        s, exception, action="APPROVE", resolved_by="credit_ops_reviewer", notes="strong compensating cash flow, approved",
    )
    print(f"exception resolved: status={resolved_exception.status.value}, resolved_by={resolved_exception.resolved_by}")
    print(f"re-fired decision: {new_decision.id}, outcome={new_decision.outcome.value}, superseded {l1_decision_id}")

    assert resolved_exception.status == ExceptionStatus.APPROVED
    assert resolved_exception.resolved_by == "credit_ops_reviewer"
    assert new_decision.id != l1_decision_id
    print("assert: exception approved and a new, distinct Decision was created by the re-fire -- PASS")

    new_decision_id = new_decision.id

with get_session() as s:
    original_decision = s.get(Decision, l1_decision_id)
    refired_decision = s.get(Decision, new_decision_id)
    assert original_decision.outcome == original_outcome, "original decision's outcome must NEVER be retroactively relabeled"
    assert original_decision.is_current is False
    assert original_decision.superseded_by_decision_id == new_decision_id
    print(f"original decision {l1_decision_id}: outcome STILL {original_decision.outcome.value} (never relabeled) -- PASS")
    print(f"re-fired decision {new_decision_id}: is_current={refired_decision.is_current}")
    print("assert: honest audit trail -- human approval never rewrote the automated decision's outcome -- PASS\n")

# ---------------------------------------------------------------------------
# Part 4: effective_outcome() composition
# ---------------------------------------------------------------------------
print("=== part 4: effective_outcome() composition ===")
with get_session() as s:
    exception = s.get(Exception_, l1_exception_id)
    original_decision = s.get(Decision, l1_decision_id)

    assert effective_outcome(original_decision, None) == "PENDING_EXCEPTION"
    assert effective_outcome(original_decision, exception) == "APPROVED"
    print(f"EXCEPTION_REQUIRED decision, no exception record -> {effective_outcome(original_decision, None)}")
    print(f"EXCEPTION_REQUIRED decision, approved exception -> {effective_outcome(original_decision, exception)}")

    stp_stub = Decision(application_id=original_decision.application_id, outcome=DecisionOutcome.STP_APPROVED,
                         rule_version_snapshot={}, triggered_rules=[])
    reject_stub = Decision(application_id=original_decision.application_id, outcome=DecisionOutcome.HARD_REJECT,
                            rule_version_snapshot={}, triggered_rules=[])
    insufficient_stub = Decision(application_id=original_decision.application_id, outcome=DecisionOutcome.INSUFFICIENT_DATA,
                                  rule_version_snapshot={}, triggered_rules=[])
    assert effective_outcome(stp_stub) == "APPROVED"
    assert effective_outcome(reject_stub) == "REJECTED"
    assert effective_outcome(insufficient_stub) == "INSUFFICIENT_DATA"
    print("STP_APPROVED -> APPROVED, HARD_REJECT -> REJECTED, INSUFFICIENT_DATA -> INSUFFICIENT_DATA")
    print("assert: effective_outcome() composes correctly across all four Decision.outcome values -- PASS\n")

# ---------------------------------------------------------------------------
# Part 5: resolving an already-resolved exception raises
# ---------------------------------------------------------------------------
print("=== part 5: resolving an already-resolved exception raises ===")
with get_session() as s:
    exception = s.get(Exception_, l1_exception_id)
    try:
        resolve_application_exception(s, exception, action="APPROVE", resolved_by="someone_else")
        raise AssertionError("expected ValueError for an already-resolved exception")
    except ValueError as e:
        print(f"raised as expected: {e}")
        print("assert: cannot double-process an already-resolved exception -- PASS")

print("\nALL PHASE 6 ASSERTIONS PASSED")
