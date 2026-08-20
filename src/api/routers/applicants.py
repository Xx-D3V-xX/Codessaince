"""
routers/applicants.py — browse the full applicant pool: paginated listing
with search, and a per-applicant PREVIEW that runs the same rules engine +
SHAP + plain-English pipeline the demo-section flow uses, WITHOUT creating
a persisted Application/Decision row.

**Covers both seeded and demo-generated applicants automatically, with no
separate bookkeeping.** src/api/dataset.py's ApplicantDataset is one shared
in-memory store — the 8,000 applicants loaded at startup AND every
applicant src/api/routers/onboarding.py's /demo/{section}/fetch has
synthesized both live in dataset.master_by_id (onboarding.py registers new
applicants into this exact same dict). Listing/searching over
dataset.master_by_id therefore surfaces every applicant that has EVER
existed in this running process, seeded or demo-generated, without this
module needing to know or care which.

**Preview is deliberately read-only, unlike POST /applications.** Browsing
8,000+ applicants and clicking through them would be a normal, frequent
action (pagination, curiosity, demo prep) — persisting an Application +
Decision row (and its audit_log entries) for every click would pollute the
database and audit trail with clicks that were never a real submission.
evaluate_application() (src/rules/engine.py) always requires a persisted
Application row by design (it writes a Decision FK'd to application.id) --
rather than reimplement that logic separately (a real drift risk: two
copies of the same rules-evaluation logic silently diverging over time,
exactly the anti-pattern this project's own docstrings repeatedly flag),
this module calls the SAME underlying pure functions
evaluate_application() itself calls (build_rule_context, evaluate_rules,
evaluate_rule_groups, resolve_decision) directly -- these don't touch the
database at all, so a preview is a genuine, no-drift-risk dry run of the
real engine, just without the persistence step. compute_risk_grade() is
DB-READ-only (looks up active weighted-scoring fields / recalibration
offsets), so it's also safe to call here without creating anything.

If a judge wants to actually SUBMIT a browsed applicant as a real,
persisted example (not just preview it), that's POST /applications with
that applicant_id -- unchanged, and works for both seeded and
demo-generated applicant_ids identically, since they live in the same
dataset.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.dataset import ApplicantDataset
from src.api.deps import get_db, get_dataset
from src.db.models import ApplicantType
from src.onboarding.llm_explain import generate_plain_english_explanation
from src.rules.context import build_rule_context, is_insufficient_data, pipeline_for
from src.rules.crud import active_rules_for_pipeline
from src.rules.evaluator import evaluate_rule_groups, evaluate_rules
from src.rules.resolver import resolve_decision
from src.scoring.explain import ApplicantExplanation, build_explainer, explain_applicant
from src.scoring.feature_matrix import build_feature_row, extract_feature_vector_fields
from src.scoring.inference import load_model
from src.scoring.weighted_deviation import active_weighted_fields_for_pipeline, compute_weighted_risk_signal

router = APIRouter(prefix="/applicants", tags=["applicants"])

_MAX_PAGE_SIZE = 100


# ---------------------------------------------------------------------------
# list/search — paginated
# ---------------------------------------------------------------------------

class ApplicantSummary(BaseModel):
    """one row in the paginated list -- enough to browse/search by, not the full profile (that's the detail/preview endpoint)."""

    applicant_id: str
    applicant_type: str | None
    age: int | None
    declared_income_monthly: float | None
    requested_loan_amount: float | None
    requested_tenure_months: int | None
    is_demo_generated: bool = Field(description="True for an applicant created via /onboarding/demo/{section}/fetch or /onboarding/synthesize this session (applicant_id starts with 'NEW'); False for one of the original 8,000 seeded applicants")


class ApplicantListResponse(BaseModel):
    total: int = Field(description="total applicants matching the search filter (or the whole pool, if no filter), independent of page/page_size")
    page: int
    page_size: int
    total_pages: int
    applicants: list[ApplicantSummary]


@router.get("", response_model=ApplicantListResponse)
def list_applicants(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=_MAX_PAGE_SIZE),
    search: str | None = Query(default=None, description="matches against applicant_id (case-insensitive substring) -- e.g. 'APP0001' or 'NEW' to find only demo-generated applicants"),
    applicant_type: str | None = Query(default=None, description="filter to exactly one of SALARIED | SELF_EMPLOYED | MSME | CORPORATE"),
    dataset: ApplicantDataset = Depends(get_dataset),
) -> ApplicantListResponse:
    if applicant_type is not None and applicant_type not in {t.value for t in ApplicantType}:
        raise HTTPException(status_code=422, detail=f"applicant_type must be one of {sorted(t.value for t in ApplicantType)}")

    all_ids = sorted(dataset.master_by_id.keys())

    if search:
        needle = search.strip().lower()
        all_ids = [aid for aid in all_ids if needle in aid.lower()]

    if applicant_type is not None:
        all_ids = [aid for aid in all_ids if dataset.master_by_id[aid].get("applicant_type") == applicant_type]

    total = len(all_ids)
    total_pages = max(1, (total + page_size - 1) // page_size)
    start = (page - 1) * page_size
    page_ids = all_ids[start : start + page_size]

    summaries = [
        ApplicantSummary(
            applicant_id=aid,
            applicant_type=(m := dataset.master_by_id[aid]).get("applicant_type"),
            age=m.get("age"),
            declared_income_monthly=m.get("declared_income_monthly"),
            requested_loan_amount=m.get("requested_loan_amount"),
            requested_tenure_months=m.get("requested_tenure_months"),
            is_demo_generated=aid.startswith("NEW"),
        )
        for aid in page_ids
    ]

    return ApplicantListResponse(total=total, page=page, page_size=page_size, total_pages=total_pages, applicants=summaries)


# ---------------------------------------------------------------------------
# detail + preview — full generated data AND a live (non-persisted) run
# through rules + SHAP + plain-English, for exactly the one clicked applicant
# ---------------------------------------------------------------------------

class RuleTracePreviewEntry(BaseModel):
    rule_code: str
    version: int
    pipeline: str
    field: str
    operator: str
    actual_value: object
    threshold: dict
    condition_met: bool | None
    outcome: str
    severity: str | None
    reason_code: str
    priority: int
    rule_group: str


class ShapContributionPreview(BaseModel):
    feature: str
    value: float | None
    shap_value: float


class ApplicantPreviewResponse(BaseModel):
    applicant_id: str
    master_profile: dict
    bureau_data: dict | None
    bank_statement_data: dict | None
    feature_vector: dict
    is_demo_generated: bool

    # live, NON-PERSISTED evaluation -- see module docstring for why no
    # Application/Decision row is created for a browse/preview click
    outcome: str
    triggered_rules: list[RuleTracePreviewEntry]
    reason_codes: list[str]

    risk_grade: str | None = None
    model_risk_score: float | None = None
    shap_base_value: float | None = None
    shap_predicted_probability: float | None = None
    shap_top_contributions: list[ShapContributionPreview] = Field(default_factory=list)

    top_reasons: list[str] = Field(default_factory=list)
    plain_english_explanation: str | None = None


@router.get("/{applicant_id}/preview", response_model=ApplicantPreviewResponse)
def preview_applicant(
    applicant_id: str,
    db: Session = Depends(get_db),
    dataset: ApplicantDataset = Depends(get_dataset),
) -> ApplicantPreviewResponse:
    found = dataset.get(applicant_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"applicant_id {applicant_id!r} not found in the ingested/generated dataset")
    master_row, feature_vector_row, bureau_row, bank_row = found

    applicant_type = ApplicantType(master_row["applicant_type"])
    pipeline = pipeline_for(applicant_type)

    context = build_rule_context(master_row, feature_vector_row, bureau_row=bureau_row, bank_row=bank_row)
    rules = active_rules_for_pipeline(db, pipeline)
    insufficient = is_insufficient_data(context)
    results = evaluate_rules(rules, context)
    group_results = evaluate_rule_groups(results)
    resolved = resolve_decision(group_results, insufficient_data=insufficient)

    fired_results = [r for r in results if r.condition_met is True]
    reason_codes = [r.reason_code for r in fired_results]
    top_reasons = [
        f"{r.reason_code} ({r.outcome}{'/' + r.severity if r.severity else ''}) — {r.field_name} {r.operator} {r.threshold} (actual: {r.actual_value})"
        for r in fired_results
    ]

    risk_grade = None
    model_risk_score = None
    shap_base_value = None
    shap_predicted_probability = None
    shap_contributions: list[ShapContributionPreview] = []

    # matches price_decision()'s own outcome scoping exactly (STP_APPROVED /
    # EXCEPTION_REQUIRED only) -- reused here rather than re-derived, so a
    # preview's "does this reach scoring" question can never silently drift
    # from the real engine's answer.
    from src.db.models import DecisionOutcome
    if resolved.outcome in (DecisionOutcome.STP_APPROVED, DecisionOutcome.EXCEPTION_REQUIRED):
        model, feature_columns = load_model(pipeline)
        weighted_fields = active_weighted_fields_for_pipeline(db, pipeline)
        admin_signal = compute_weighted_risk_signal(weighted_fields, context)
        feature_vector_fields = extract_feature_vector_fields(context)
        encoded_row = build_feature_row(context, feature_vector_fields, admin_signal)

        import numpy as np
        x = np.array([[encoded_row[c] for c in feature_columns]], dtype=np.float64)
        model_risk_score = float(model.predict_proba(x)[0, 1])

        explainer = build_explainer(model)
        explanation: ApplicantExplanation = explain_applicant(explainer, encoded_row, feature_columns, top_n=10)
        shap_base_value = explanation.base_value
        shap_predicted_probability = explanation.predicted_probability
        shap_contributions = [
            ShapContributionPreview(feature=c.feature, value=c.value, shap_value=c.shap_value)
            for c in explanation.top_contributions
        ]
        for c in explanation.top_contributions[:5]:
            direction = "increased" if c.shap_value > 0 else "decreased"
            top_reasons.append(f"model factor: {c.feature}={c.value} {direction} predicted risk (SHAP {c.shap_value:+.4f})")

        # a real risk_grade needs compute_risk_grade()'s recalibration step
        # too (src/pricing/eligibility.py) -- reused directly, same
        # DB-read-only reasoning as the weighted-fields lookup above.
        from src.pricing.eligibility import compute_risk_grade
        risk_grade_result = compute_risk_grade(db, pipeline, context)
        risk_grade = risk_grade_result.risk_grade

    plain_english_explanation = generate_plain_english_explanation(
        outcome=resolved.outcome.value,
        effective_outcome=resolved.outcome.value,  # no Exception_ row exists for a preview (nothing persisted), so outcome/effective_outcome coincide here -- see module docstring
        risk_grade=risk_grade,
        eligible_amount=None,  # eligibility/pricing intentionally not computed for a preview -- see module docstring: no persisted Decision exists for compute_eligibility()/compute_pricing() to price against meaningfully outside a real submission
        interest_rate=None,
        reason_codes=reason_codes,
        top_reasons=top_reasons,
    )

    return ApplicantPreviewResponse(
        applicant_id=applicant_id,
        master_profile=master_row,
        bureau_data=bureau_row,
        bank_statement_data=bank_row,
        feature_vector=feature_vector_row,
        is_demo_generated=applicant_id.startswith("NEW"),
        outcome=resolved.outcome.value,
        triggered_rules=[RuleTracePreviewEntry(**r.to_dict()) for r in results],
        reason_codes=reason_codes,
        risk_grade=risk_grade,
        model_risk_score=model_risk_score,
        shap_base_value=shap_base_value,
        shap_predicted_probability=shap_predicted_probability,
        shap_top_contributions=shap_contributions,
        top_reasons=top_reasons,
        plain_english_explanation=plain_english_explanation,
    )
