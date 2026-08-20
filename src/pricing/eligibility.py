"""
eligibility.py — Phase 7: loan eligibility & pricing, per TODO.md Phase 7 /
CLAUDE.md functional requirement 5 ("for non-hard-reject cases, determine
eligible loan amount and applicable interest-rate/pricing band from
configured logic").

Three pieces, deliberately kept separate (TODO.md's own three bullets):
  - compute_risk_grade(): risk grade computation, SEPARATE from the BRE's
    approve/reject outcome (CLAUDE.md's own phrasing) — every applicant who
    reaches this stage gets a grade regardless of whether they're being
    auto-approved or still pending exception review, because a reviewer
    needs the grade to make that review decision in the first place.
  - compute_eligibility(): multiplier/cap lookup by (pipeline, risk_grade),
    src/pricing/config.py — never a hardcoded number in this module.
  - compute_pricing(): interest-rate band lookup, same lookup pattern.

price_decision() is the integration point: it only runs for decisions whose
BRE outcome is STP_APPROVED or EXCEPTION_REQUIRED (CLAUDE.md's own
"non-hard-reject" scope — HARD_REJECT and INSUFFICIENT_DATA have nothing to
price), and writes directly into the Decision row's own
risk_grade/eligible_amount/interest_rate/tenure_months/model_risk_score
columns (schema'd for exactly this in Phase 3, unused until now).

Layered on top of src/rules/exceptions.py (which itself sits on
src/rules/engine.py) rather than folded into either — same reasoning as
Phase 6's own layering: keeps each phase's module focused on its own
concern and avoids a circular import (this module needs the *result* of
evaluation/routing, not the other way around).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from sqlalchemy.orm import Session

from src.db.models import Application, ApplicantPipeline, Decision, DecisionOutcome, Exception_
from src.pricing.config import active_eligibility_multiplier, active_pricing_band
from src.rules.context import pipeline_for
from src.rules.exceptions import evaluate_and_route, resolve_application_exception
from src.scoring.feature_matrix import build_feature_row, extract_feature_vector_fields
from src.scoring.inference import load_model
from src.scoring.recalibration import active_offsets_for_pipeline, apply_recalibration
from src.scoring.weighted_deviation import active_weighted_fields_for_pipeline, compute_weighted_risk_signal

# functional requirement 2's "FOIR/obligation ratio" style base for
# eligibility — annualized declared income is the anchor multiplied by the
# risk-grade multiplier; both are exactly what the confirmed
# training-feature-architecture decision already treats as a first-class
# raw passthrough field, not a new concept introduced here.
_ELIGIBILITY_BASE_FIELD = "declared_income_monthly"
_ELIGIBILITY_BASE_MONTHS = 12


@dataclass
class RiskGradeResult:
    raw_probability: float
    recalibrated_probability: float
    risk_grade: str


@dataclass
class PricingResult:
    risk_grade_result: RiskGradeResult
    eligible_amount: float | None
    interest_rate: float | None
    tenure_months: int | None


def compute_risk_grade(session: Session, pipeline: ApplicantPipeline, context: dict) -> RiskGradeResult:
    """
    model's raw P(default) -> admin-weighted signal already folded into the
    same feature slot the model was trained on -> recalibration offset ->
    risk grade. context is the full flat merged context (build_rule_context()
    output / Application.rule_context_snapshot) — the engineered-vector
    slice and admin-weighted signal are both derived from it here, not
    passed in separately, so callers only ever need to keep track of one
    dict per applicant.
    """
    model, feature_columns = load_model(pipeline)

    weighted_fields = active_weighted_fields_for_pipeline(session, pipeline)
    admin_signal = compute_weighted_risk_signal(weighted_fields, context)

    feature_vector_row = extract_feature_vector_fields(context)
    encoded_row = build_feature_row(context, feature_vector_row, admin_signal)
    x = np.array([[encoded_row[c] for c in feature_columns]], dtype=np.float64)
    raw_probability = float(model.predict_proba(x)[0, 1])

    offsets = active_offsets_for_pipeline(session, pipeline)
    recalibrated_probability, risk_grade = apply_recalibration(raw_probability, offsets)

    return RiskGradeResult(raw_probability=raw_probability, recalibrated_probability=recalibrated_probability, risk_grade=risk_grade)


def compute_eligibility(session: Session, pipeline: ApplicantPipeline, risk_grade: str, context: dict) -> float | None:
    """min(annualized declared income * multiplier, cap_amount). None if no config exists for this (pipeline, risk_grade) — an honest gap, not a silent 0."""
    config = active_eligibility_multiplier(session, pipeline, risk_grade)
    if config is None:
        return None
    declared_income_monthly = context.get(_ELIGIBILITY_BASE_FIELD)
    if declared_income_monthly is None:
        return None
    base_amount = float(declared_income_monthly) * _ELIGIBILITY_BASE_MONTHS
    eligible = base_amount * float(config.multiplier)
    if config.cap_amount is not None:
        eligible = min(eligible, float(config.cap_amount))
    return eligible


def compute_pricing(session: Session, pipeline: ApplicantPipeline, risk_grade: str) -> float | None:
    """interest_rate lookup by (pipeline, risk_grade). None if no config exists — same honest-gap convention as compute_eligibility()."""
    config = active_pricing_band(session, pipeline, risk_grade)
    return float(config.interest_rate) if config is not None else None


def price_decision(session: Session, decision: Decision, application: Application) -> PricingResult | None:
    """
    only runs for STP_APPROVED / EXCEPTION_REQUIRED (CLAUDE.md's own
    "non-hard-reject" scope) — returns None and leaves the Decision's
    pricing columns untouched (still their NULL default) for HARD_REJECT /
    INSUFFICIENT_DATA, since there is nothing to price.
    """
    if decision.outcome not in (DecisionOutcome.STP_APPROVED, DecisionOutcome.EXCEPTION_REQUIRED):
        return None
    if application.rule_context_snapshot is None:
        raise ValueError("application.rule_context_snapshot is not set -- price_decision() requires a prior evaluate_application() run")

    context = application.rule_context_snapshot
    pipeline = pipeline_for(application.applicant_type)

    risk_grade_result = compute_risk_grade(session, pipeline, context)
    eligible_amount = compute_eligibility(session, pipeline, risk_grade_result.risk_grade, context)
    interest_rate = compute_pricing(session, pipeline, risk_grade_result.risk_grade)
    tenure_months = context.get("requested_tenure_months")

    decision.risk_grade = risk_grade_result.risk_grade
    decision.model_risk_score = risk_grade_result.raw_probability
    decision.eligible_amount = eligible_amount
    decision.interest_rate = interest_rate
    decision.tenure_months = tenure_months
    session.flush()

    return PricingResult(
        risk_grade_result=risk_grade_result, eligible_amount=eligible_amount,
        interest_rate=interest_rate, tenure_months=tenure_months,
    )


def evaluate_route_and_price(
    session: Session,
    application: Application,
    master_row: dict | None = None,
    feature_vector_row: dict | None = None,
    bureau_row: dict | None = None,
    bank_row: dict | None = None,
    actor: str = "engine",
) -> tuple[Decision, Exception_ | None, PricingResult | None]:
    """
    the full pipeline in one call: evaluate (Phase 4) -> route any exception
    (Phase 6) -> price (Phase 7). The natural single entry point Phase 8's
    API layer will call for both a fresh submission and any re-fire
    (threshold edit, exception resolution) — each layer only knows about
    the one below it, so this is the only place all three come together.
    """
    decision, exception = evaluate_and_route(session, application, master_row, feature_vector_row, bureau_row, bank_row, actor)
    pricing = price_decision(session, decision, application)
    return decision, exception, pricing


def resolve_exception_and_reprice(
    session: Session,
    exception: Exception_,
    action: Literal["APPROVE", "REJECT"],
    resolved_by: str,
    notes: str | None = None,
) -> tuple[Exception_, Decision, Exception_ | None, PricingResult | None]:
    """
    src/rules/exceptions.py::resolve_application_exception() re-fires the
    decision but can't itself call into this module (exceptions.py sits
    below pricing.py in the layering — engine -> exceptions -> pricing —
    so the dependency can't point the other way without a circular import).
    This wrapper is the Phase 7-aware version: resolve, re-fire, then price
    the re-fired decision exactly like evaluate_route_and_price() would for
    a fresh evaluation, so an exception resolution's resulting decision
    gets its eligible amount / interest rate too, not just its outcome.
    """
    resolved_exception, new_decision, new_exception = resolve_application_exception(session, exception, action, resolved_by, notes)
    application = session.get(Application, new_decision.application_id)
    pricing = price_decision(session, new_decision, application)
    return resolved_exception, new_decision, new_exception, pricing
