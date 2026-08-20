"""
test_rules_engine.py — Phase 4's explicit test, per TODO.md: "change one
rule's threshold, re-run a stored application, confirm the decision changes
and both decision versions are queryable — this is the PS-1 demo scenario 5
requirement, test it now, not the night before judging."

Runs against real Phase 0-2 generated data and a real live Postgres
instance (not fabricated dicts, not mocks) — same standard of evidence as
every prior phase's verification in PROGRESS.md.

Part 1: batch-evaluates every real applicant through the seeded policy and
reports the outcome distribution, as a sanity check that the policy
produces a genuine mix of outcomes rather than degenerating to all-STP or
all-hard-reject.

Part 2: the explicit re-run test. Finds a real applicant whose only fired
rule is the individual-pipeline bureau-score hard-reject gate, confirms the
HARD_REJECT decision, edits that one rule's threshold live (mirroring "a
judge changes a threshold during judging"), re-runs the same stored
application, and asserts: the decision changes, both decision versions are
independently queryable from a fresh DB session, and the rules table shows
two versions with the old one preserved (not mutated).
"""

from collections import Counter

from sqlalchemy import select

from src.db.models import (
    Application, ApplicantPipeline, ApplicantType, ApplicationStatus, Decision, DecisionOutcome, Rule,
)
from src.db.session import get_session
from src.features.cross_source import compute_batch_cross_source, merge_into_vectors
from src.features.engine import FeatureEngine
from src.ingestion.applicant_adapter import load_and_adapt, to_engine_frames
from src.rules.context import build_rule_context, is_insufficient_data, pipeline_for
from src.rules.crud import active_rules_for_pipeline, edit_rule
from src.rules.engine import evaluate_application
from src.rules.evaluator import evaluate_rule_groups, evaluate_rules
from src.rules.resolver import resolve_decision

print("=== loading real applicant data (Phase 0-2 pipeline) ===")
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
# Part 1: batch outcome distribution sanity check (no DB writes)
# ---------------------------------------------------------------------------
print("=== part 1: batch outcome distribution across all applicants (in-memory, no DB writes) ===")
outcome_counts: Counter = Counter()
with get_session() as s:
    rules_by_pipeline = {pl: active_rules_for_pipeline(s, pl) for pl in ApplicantPipeline}

for applicant_id, master_row in master_by_id.items():
    feature_row = vector_by_id.get(applicant_id)
    bureau_row = bureau_by_id.get(applicant_id)
    bank_row = bank_by_id.get(applicant_id)
    if feature_row is None:
        continue
    applicant_type = ApplicantType(master_row["applicant_type"])
    pipeline = pipeline_for(applicant_type)
    context = build_rule_context(master_row, feature_row, bureau_row=bureau_row, bank_row=bank_row)
    insufficient = is_insufficient_data(context)
    results = evaluate_rules(rules_by_pipeline[pipeline], context)
    group_results = evaluate_rule_groups(results)
    resolved = resolve_decision(group_results, insufficient_data=insufficient)
    outcome_counts[resolved.outcome.value] += 1

print(f"outcome distribution across {sum(outcome_counts.values())} applicants:")
for outcome, count in outcome_counts.most_common():
    print(f"  {outcome}: {count} ({100 * count / sum(outcome_counts.values()):.1f}%)")
assert len(outcome_counts) >= 3, "expected a genuine mix of outcomes, not a degenerate policy"
print("assert: policy produces a genuine mix of outcomes (not degenerate) -- PASS\n")

# ---------------------------------------------------------------------------
# Part 2: the explicit re-run / threshold-change test (real DB writes)
# ---------------------------------------------------------------------------
print("=== part 2: find a real applicant who hard-rejects solely on IND_MIN_BUREAU_SCORE ===")
candidate_id = None
with get_session() as s:
    ind_rules = active_rules_for_pipeline(s, ApplicantPipeline.INDIVIDUAL)

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
    # a bureau_score < 600 always also trips the borderline-exception rule
    # (< 700) by construction -- that's expected overlap, not disqualifying.
    # what matters is that bureau_hard_gate is the ONLY fired HARD_REJECT
    # group, so loosening its threshold alone is guaranteed to flip the
    # outcome away from HARD_REJECT.
    fired_hard_reject_groups = [g.rule_group for g in group_results if g.outcome == "HARD_REJECT" and g.fired is True]
    if fired_hard_reject_groups == ["bureau_hard_gate"]:
        candidate_id = applicant_id
        break

assert candidate_id is not None, "no applicant found who hard-rejects solely on the bureau-score gate -- policy or data changed"
candidate_master = master_by_id[candidate_id]
candidate_bureau = bureau_by_id[candidate_id]
print(f"candidate: {candidate_id}, bureau_score={candidate_bureau['bureau_score']}, requested_loan_amount={candidate_master['requested_loan_amount']}\n")

print("=== creating application + first decision (expect HARD_REJECT) ===")
with get_session() as s:
    app = Application(
        applicant_id=candidate_id,
        applicant_type=ApplicantType(candidate_master["applicant_type"]),
        requested_loan_amount=candidate_master["requested_loan_amount"],
        requested_tenure_months=candidate_master["requested_tenure_months"],
        status=ApplicationStatus.RECEIVED,
        normalized_profile_snapshot=candidate_master,
        feature_vector_snapshot={k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in vector_by_id[candidate_id].items()},
    )
    s.add(app)
    s.flush()

    decision1 = evaluate_application(
        s, app, candidate_master, vector_by_id[candidate_id],
        bureau_row=candidate_bureau, bank_row=bank_by_id.get(candidate_id), actor="test_rules_engine",
    )
    assert decision1.outcome == DecisionOutcome.HARD_REJECT, f"expected HARD_REJECT, got {decision1.outcome}"
    print(f"decision1 = {decision1.id}: outcome={decision1.outcome.value}, is_current={decision1.is_current}")
    print("assert: first decision is HARD_REJECT -- PASS\n")

    app_id = app.id
    decision1_id = decision1.id

print("=== judge lowers IND_MIN_BUREAU_SCORE's threshold live (edit_rule -- new version, old preserved) ===")
new_threshold = candidate_bureau["bureau_score"] - 10
with get_session() as s:
    new_rule = edit_rule(
        s, rule_code="IND_MIN_BUREAU_SCORE",
        updates={"value": {"threshold": new_threshold}},
        edited_by="judge",
    )
    print(f"IND_MIN_BUREAU_SCORE now v{new_rule.version}, threshold={new_rule.value['threshold']} (was 600)")

print("\n=== re-running the SAME stored application against the new threshold ===")
with get_session() as s:
    app = s.get(Application, app_id)
    decision2 = evaluate_application(
        s, app, candidate_master, vector_by_id[candidate_id],
        bureau_row=candidate_bureau, bank_row=bank_by_id.get(candidate_id), actor="test_rules_engine",
    )
    decision2_id = decision2.id
    print(f"decision2 = {decision2.id}: outcome={decision2.outcome.value}, is_current={decision2.is_current}")
    assert decision2.outcome != DecisionOutcome.HARD_REJECT, "decision should have changed away from HARD_REJECT"
    print("assert: decision CHANGED after the threshold edit -- PASS\n")

print("=== verifying via a FRESH session (genuine DB round-trip) ===")
with get_session() as s:
    d1 = s.get(Decision, decision1_id)
    d2 = s.get(Decision, decision2_id)
    assert d1.is_current is False, "old decision should no longer be current"
    assert d1.superseded_by_decision_id == decision2_id, "old decision should chain to the new one"
    assert d2.is_current is True, "new decision should be current"
    print(f"decision1 (old): outcome={d1.outcome.value} is_current={d1.is_current} superseded_by={d1.superseded_by_decision_id}")
    print(f"decision2 (new): outcome={d2.outcome.value} is_current={d2.is_current}")
    print("assert: both decision versions independently queryable with correct chaining -- PASS\n")

    both_versions = s.execute(select(Rule).where(Rule.rule_code == "IND_MIN_BUREAU_SCORE").order_by(Rule.version)).scalars().all()
    assert len(both_versions) == 2
    assert both_versions[0].active is False and both_versions[0].value == {"threshold": 600}
    assert both_versions[1].active is True and both_versions[1].value == {"threshold": new_threshold}
    print(f"rule v1: threshold={both_versions[0].value['threshold']} active={both_versions[0].active}")
    print(f"rule v2: threshold={both_versions[1].value['threshold']} active={both_versions[1].active}")
    print("assert: old rule version preserved untouched, new version active -- PASS")

print("\nALL PHASE 4 DEMO-SCENARIO-5 ASSERTIONS PASSED")
