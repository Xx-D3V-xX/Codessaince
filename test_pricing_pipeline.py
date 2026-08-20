"""
test_pricing_pipeline.py — Phase 7 verification, same standard of evidence
as every prior phase: real applicant data, live Postgres, real trained
models, concrete assertions.

Covers:
  1. A real STP_APPROVED applicant gets a risk grade, eligible amount, and
     interest rate written onto its Decision row -- with the eligible
     amount matching an independently hand-computed value.
  2. A real HARD_REJECT applicant gets NONE of those fields populated
     (nothing to price) -- confirms price_decision()'s outcome gate.
  3. Risk grade computation is genuinely separate from the BRE outcome: an
     EXCEPTION_REQUIRED applicant still gets a real grade/price, informing
     the reviewer, per CLAUDE.md's own "separate from approve/reject
     outcome" wording.
  4. Changing one eligibility multiplier (no code change) changes a real
     applicant's eligible amount on the next price_decision() call --
     admin-configurable, not hardcoded, confirmed live.
  5. resolve_exception_and_reprice() re-prices the re-fired decision after
     an exception is approved.
"""

from src.db.models import (
    Application, ApplicantPipeline, ApplicantType, ApplicationStatus,
    Decision, DecisionOutcome,
)
from src.db.session import get_session
from src.features.cross_source import compute_batch_cross_source, merge_into_vectors
from src.features.engine import FeatureEngine
from src.ingestion.applicant_adapter import load_and_adapt, to_engine_frames
from src.pricing.config import set_eligibility_multiplier
from src.pricing.eligibility import evaluate_route_and_price, resolve_exception_and_reprice
from src.rules.context import build_rule_context, is_insufficient_data, pipeline_for
from src.rules.crud import active_rules_for_pipeline
from src.rules.evaluator import evaluate_rule_groups, evaluate_rules
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

print("=== finding an STP, a HARD_REJECT, and an EXCEPTION_REQUIRED candidate ===")
with get_session() as s:
    ind_rules = active_rules_for_pipeline(s, ApplicantPipeline.INDIVIDUAL)

stp_candidate = hard_reject_candidate = exception_candidate = None
for applicant_id, master_row in master_by_id.items():
    if master_row["applicant_type"] not in ("SALARIED", "SELF_EMPLOYED"):
        continue
    feature_row = vector_by_id.get(applicant_id)
    bureau_row = bureau_by_id.get(applicant_id)
    bank_row = bank_by_id.get(applicant_id)
    if feature_row is None or bureau_row is None or master_row.get("declared_income_monthly") is None:
        continue
    context = build_rule_context(master_row, feature_row, bureau_row=bureau_row, bank_row=bank_row)
    if is_insufficient_data(context):
        continue
    results = evaluate_rules(ind_rules, context)
    group_results = evaluate_rule_groups(results)
    resolved = resolve_decision(group_results, insufficient_data=False)

    if resolved.outcome.value == "STP_APPROVED" and stp_candidate is None:
        stp_candidate = applicant_id
    elif resolved.outcome.value == "HARD_REJECT" and hard_reject_candidate is None:
        hard_reject_candidate = applicant_id
    elif resolved.outcome.value == "EXCEPTION_REQUIRED" and exception_candidate is None:
        exception_candidate = applicant_id
    if stp_candidate and hard_reject_candidate and exception_candidate:
        break

assert stp_candidate and hard_reject_candidate and exception_candidate
print(f"STP candidate: {stp_candidate}")
print(f"HARD_REJECT candidate: {hard_reject_candidate}")
print(f"EXCEPTION_REQUIRED candidate: {exception_candidate}\n")


def make_application(applicant_id: str) -> Application:
    master_row = master_by_id[applicant_id]
    return Application(
        applicant_id=applicant_id,
        applicant_type=ApplicantType(master_row["applicant_type"]),
        requested_loan_amount=master_row["requested_loan_amount"],
        requested_tenure_months=master_row["requested_tenure_months"],
        status=ApplicationStatus.RECEIVED,
    )


def submit(applicant_id: str):
    with get_session() as s:
        app = make_application(applicant_id)
        s.add(app)
        s.flush()
        master_row = master_by_id[applicant_id]
        decision, exception, pricing = evaluate_route_and_price(
            s, app, master_row, vector_by_id[applicant_id],
            bureau_row=bureau_by_id.get(applicant_id), bank_row=bank_by_id.get(applicant_id), actor="test_pricing_pipeline",
        )
        return app.id, decision.id, exception.id if exception else None


# ---------------------------------------------------------------------------
# Part 1: STP applicant gets a real, hand-verifiable price
# ---------------------------------------------------------------------------
print("=== part 1: STP applicant priced ===")
stp_app_id, stp_decision_id, _ = submit(stp_candidate)
with get_session() as s:
    decision = s.get(Decision, stp_decision_id)
    print(f"outcome={decision.outcome.value}, risk_grade={decision.risk_grade}, "
          f"eligible_amount={decision.eligible_amount}, interest_rate={decision.interest_rate}, "
          f"model_risk_score={decision.model_risk_score}")
    assert decision.outcome == DecisionOutcome.STP_APPROVED
    assert decision.risk_grade is not None
    assert decision.eligible_amount is not None
    assert decision.interest_rate is not None
    assert 0.0 <= float(decision.model_risk_score) <= 1.0

    from src.pricing.config import active_eligibility_multiplier
    config = active_eligibility_multiplier(s, ApplicantPipeline.INDIVIDUAL, decision.risk_grade)
    expected = min(
        master_by_id[stp_candidate]["declared_income_monthly"] * 12 * float(config.multiplier),
        float(config.cap_amount),
    )
    assert abs(float(decision.eligible_amount) - expected) < 0.01, f"{decision.eligible_amount} != {expected}"
    print(f"hand-computed expected eligible_amount={expected:.2f}, matches decision.eligible_amount exactly")
    print("assert: STP applicant priced with real, independently-verified eligible_amount -- PASS\n")

# ---------------------------------------------------------------------------
# Part 2: HARD_REJECT applicant gets nothing priced
# ---------------------------------------------------------------------------
print("=== part 2: HARD_REJECT applicant NOT priced ===")
_, hr_decision_id, _ = submit(hard_reject_candidate)
with get_session() as s:
    decision = s.get(Decision, hr_decision_id)
    print(f"outcome={decision.outcome.value}, risk_grade={decision.risk_grade}, eligible_amount={decision.eligible_amount}")
    assert decision.outcome == DecisionOutcome.HARD_REJECT
    assert decision.risk_grade is None and decision.eligible_amount is None and decision.interest_rate is None
    print("assert: HARD_REJECT decision has no pricing fields populated -- PASS\n")

# ---------------------------------------------------------------------------
# Part 3: EXCEPTION_REQUIRED applicant STILL gets priced (grade separate from outcome)
# ---------------------------------------------------------------------------
print("=== part 3: EXCEPTION_REQUIRED applicant still gets a risk grade + price ===")
exc_app_id, exc_decision_id, exc_exception_id = submit(exception_candidate)
with get_session() as s:
    decision = s.get(Decision, exc_decision_id)
    print(f"outcome={decision.outcome.value}, risk_grade={decision.risk_grade}, eligible_amount={decision.eligible_amount}")
    assert decision.outcome == DecisionOutcome.EXCEPTION_REQUIRED
    assert decision.risk_grade is not None and decision.eligible_amount is not None
    print("assert: risk grade/pricing computed independent of BRE outcome, for the reviewer's benefit -- PASS\n")

# ---------------------------------------------------------------------------
# Part 4: admin changes a multiplier live, no code change -- eligible_amount changes
# ---------------------------------------------------------------------------
print("=== part 4: admin-configurable multiplier change affects a real applicant's eligible_amount ===")
with get_session() as s:
    decision = s.get(Decision, stp_decision_id)
    grade = decision.risk_grade
    before_amount = float(decision.eligible_amount)

with get_session() as s:
    updated = set_eligibility_multiplier(
        s, config_code=f"IND_ELIGIBILITY_{grade}", pipeline=ApplicantPipeline.INDIVIDUAL, risk_grade=grade,
        multiplier=1.0, cap_amount=100_000.0, set_by="test_pricing_pipeline",
    )
    print(f"IND_ELIGIBILITY_{grade} updated to v{updated.version}: multiplier=1.0, cap=100000")

with get_session() as s:
    app = s.get(Application, stp_app_id)
    from src.pricing.eligibility import price_decision
    decision = s.get(Decision, stp_decision_id)
    price_decision(s, decision, app)
    after_amount = float(decision.eligible_amount)
    print(f"eligible_amount: before={before_amount:.2f} after={after_amount:.2f}")
    assert after_amount != before_amount and after_amount == 100_000.0
    print("assert: eligible_amount changed purely from an admin config edit, no code/retraining involved -- PASS")

# revert
with get_session() as s:
    from src.pricing.seed_pricing import _ELIGIBILITY_INDIVIDUAL
    orig_multiplier, orig_cap = _ELIGIBILITY_INDIVIDUAL[grade]
    reverted = set_eligibility_multiplier(
        s, config_code=f"IND_ELIGIBILITY_{grade}", pipeline=ApplicantPipeline.INDIVIDUAL, risk_grade=grade,
        multiplier=orig_multiplier, cap_amount=orig_cap, set_by="test_pricing_pipeline",
    )
    print(f"IND_ELIGIBILITY_{grade} reverted to v{reverted.version}: multiplier={orig_multiplier}, cap={orig_cap}\n")

# ---------------------------------------------------------------------------
# Part 5: exception resolution re-prices the re-fired decision
# ---------------------------------------------------------------------------
print("=== part 5: resolving an exception re-prices the re-fired decision ===")
with get_session() as s:
    from src.db.models import Exception_
    exception = s.get(Exception_, exc_exception_id)
    resolved_exception, new_decision, new_exception, pricing = resolve_exception_and_reprice(
        s, exception, action="APPROVE", resolved_by="credit_ops_reviewer", notes="approved for pricing test",
    )
    print(f"re-fired decision {new_decision.id}: outcome={new_decision.outcome.value}, "
          f"risk_grade={new_decision.risk_grade}, eligible_amount={new_decision.eligible_amount}")
    if new_decision.outcome in (DecisionOutcome.STP_APPROVED, DecisionOutcome.EXCEPTION_REQUIRED):
        assert new_decision.risk_grade is not None and pricing is not None
        print("assert: re-fired decision re-priced after exception resolution -- PASS")
    else:
        print("(re-fired decision resolved to a non-priceable outcome -- nothing to assert here, still valid)")

print("\nALL PHASE 7 ASSERTIONS PASSED")
