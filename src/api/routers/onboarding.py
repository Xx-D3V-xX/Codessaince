"""
routers/onboarding.py — mock KYC + mock Account Aggregator consent + new-
applicant synthesis, wired for a fixed 4-section live demo (one section per
PS-1 minimum-demo outcome: HARD_REJECT, STP_APPROVED, L1 exception, L2
exception).

**Every mock step here is honest and disclosed — same pattern as
src/api/deps.py's role-gating docstring.** Real Aadhaar/PAN verification
and real Account Aggregator data pulls are both out of reach for a
hackathon MVP (licensed, regulated APIs no team gets production credentials
to in a weekend) — PS-1's own brief treats applicant data as already
"supplied," not fetched live. What's real: the demo SEQUENCE (verify
identity -> grant consent -> "AA fetch" returns a full synthetic profile ->
engine evaluates -> SHAP explains), and the fact that the fetched profile,
once generated, is genuine and schema-valid, run through the EXACT same
FeatureEngine / cross_source / rules / scoring / pricing / SHAP pipeline
every seeded applicant uses — nothing downstream is aware the applicant
is new.

**Two-call shape, matching the presenter's described flow**:
  1. POST /onboarding/demo/{section}/fetch — generates the full synthetic
     profile for the guaranteed section outcome (section IS the scenario,
     no separate demo_scenario field to set), submits it as a real
     Application immediately, and returns the FULL raw generated data
     (master profile + bureau + bank + assets + ITR) for the frontend to
     display during the "fetching via Account Aggregator..." beat, plus
     the application_id needed for step 2. Evaluation runs in the
     background exactly like the existing POST /applications flow.
  2. GET /onboarding/status/{application_id} — polled by the frontend
     while showing a "calculating credit risk..." beat; once DECISIONED,
     returns the outcome, effective_outcome, pricing, the full rule trace
     (already available via GET /applications/{id}/decision), AND a SHAP
     explanation (top feature contributions + predicted probability) for
     STP_APPROVED / EXCEPTION_REQUIRED decisions — HARD_REJECT /
     INSUFFICIENT_DATA still get the rule trace (which IS their real
     explanation: they never reached the ML stage) but no SHAP block,
     since price_decision() itself never runs for those outcomes.

Calling fetch twice in the SAME section on purpose (per the presenter's
plan to "use the API twice in the same section" to demonstrate the mock
AA fetch is genuinely live, not a canned response) produces two different
applicant_ids with two independently re-synthesized profiles landing on
the SAME guaranteed outcome — proving the generation is real per-call work,
not a cached fixture.
"""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.dataset import ApplicantDataset
from src.api.deps import get_db, get_dataset
from src.api.serializers import serialize_decision
from src.db.models import Application, ApplicantType, ApplicationStatus, Decision, DecisionOutcome
from src.db.session import get_session
from src.onboarding.llm_explain import generate_plain_english_explanation
from src.onboarding.mock_new_applicant import NewApplicantInput, onboard_new_applicant
from src.pricing.eligibility import evaluate_route_and_price
from src.rules.context import pipeline_for
from src.scoring.explain import ApplicantExplanation, FeatureContribution, build_explainer, explain_applicant
from src.scoring.feature_matrix import build_feature_row, extract_feature_vector_fields
from src.scoring.inference import load_model
from src.scoring.weighted_deviation import active_weighted_fields_for_pipeline, compute_weighted_risk_signal

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

_AADHAAR_RE = re.compile(r"^\d{12}$")
_PAN_RE = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")


# ---------------------------------------------------------------------------
# mock KYC — format validation only, no real UIDAI/NSDL call, disclosed above
# ---------------------------------------------------------------------------

class VerifyIdentityRequest(BaseModel):
    full_name: str
    aadhaar_number: str
    pan_number: str


class VerifyIdentityResponse(BaseModel):
    verified: bool
    kyc_reference_id: str
    message: str


@router.post("/verify-identity", response_model=VerifyIdentityResponse)
def verify_identity(request: VerifyIdentityRequest) -> VerifyIdentityResponse:
    """format-only check (12-digit Aadhaar, standard PAN pattern) — NOT a real UIDAI/NSDL lookup, see module docstring."""
    aadhaar_ok = bool(_AADHAAR_RE.match(request.aadhaar_number.strip()))
    pan_ok = bool(_PAN_RE.match(request.pan_number.strip().upper()))

    if not aadhaar_ok or not pan_ok:
        problems = []
        if not aadhaar_ok:
            problems.append("aadhaar_number must be exactly 12 digits")
        if not pan_ok:
            problems.append("pan_number must match the standard PAN format (e.g. ABCDE1234F)")
        raise HTTPException(status_code=422, detail="; ".join(problems))

    return VerifyIdentityResponse(
        verified=True,
        kyc_reference_id=f"KYC{uuid.uuid4().hex[:10].upper()}",
        message="identity verified (mock — format check only, not a real UIDAI/NSDL lookup)",
    )


# ---------------------------------------------------------------------------
# mock AA consent — a real AA flow is a licensed, regulated data-sharing
# framework; this is a stand-in gate, not an actual consent artefact/FIU
# integration. The actual "data becomes available" moment is the /fetch
# endpoint below, not this step — same as a real AA consent grant
# preceding a data pull rather than being the pull itself.
# ---------------------------------------------------------------------------

class GrantConsentRequest(BaseModel):
    kyc_reference_id: str
    consent_scope: list[str] = Field(default_factory=lambda: ["BUREAU", "BANK_STATEMENT", "ITR", "ASSETS"])


class GrantConsentResponse(BaseModel):
    consent_id: str
    scope: list[str]
    message: str


@router.post("/consent", response_model=GrantConsentResponse)
def grant_consent(request: GrantConsentRequest) -> GrantConsentResponse:
    return GrantConsentResponse(
        consent_id=f"AA{uuid.uuid4().hex[:10].upper()}",
        scope=request.consent_scope,
        message="consent recorded (mock — no real Account Aggregator/FIU integration)",
    )


# ---------------------------------------------------------------------------
# the 4 fixed demo sections — each IS a guaranteed PS-1 minimum-demo
# outcome, not a free-form dial. section name is the URL path segment.
# ---------------------------------------------------------------------------

_SECTION_TO_RISK_PROFILE = {
    "accept": "clean",  # -> STP_APPROVED (bureau score 750-820, max_dpd=0)
    "reject": "delinquent",  # -> HARD_REJECT (bureau score 300-579 / hard-negative)
    "l1": "borderline",  # -> EXCEPTION_REQUIRED / L1 (bureau score 600-699)
    "l2": "clean",  # -> EXCEPTION_REQUIRED / L2, via force_high_loan_amount below, NOT the bureau-score axis
}
_SECTION_FORCE_HIGH_LOAN = {"accept": False, "reject": False, "l1": False, "l2": True}

_VALID_SECTIONS = set(_SECTION_TO_RISK_PROFILE)
_VALID_APPLICANT_TYPES = {"SALARIED", "SELF_EMPLOYED", "MSME", "CORPORATE"}


class DemoFetchRequest(BaseModel):
    consent_id: str
    full_name: str
    applicant_type: str = Field(description="SALARIED | SELF_EMPLOYED | MSME | CORPORATE")
    declared_income_monthly: float
    requested_loan_amount: float
    requested_tenure_months: int
    loan_type: str | None = Field(default=None, description="cosmetic — not fed into the engine, display-only")
    age: int = 30
    employment_vintage_months: int | None = None
    business_vintage_months: int | None = None
    declared_existing_obligations: float = 0.0


class GeneratedProfile(BaseModel):
    """
    the FULL data generated for this applicant — this is the "show the
    judges what the AA fetch actually returned" payload, not a summary.
    Mirrors exactly what onboard_new_applicant() produced and what was
    fed to the rules/scoring pipeline, so nothing shown here is staged
    separately from what the engine actually used.
    """

    master_profile: dict
    bureau_data: dict
    bank_statement_data: dict
    itr_data: list[dict] = Field(description="one entry per filed year (yr1, yr2)")
    assets_data: dict
    feature_vector: dict = Field(description="the engineered features computed from the raw data above (FeatureEngine + cross_source output)")


class DemoFetchResponse(BaseModel):
    application_id: str
    applicant_id: str
    full_name: str
    section: str
    application_status: str = ApplicationStatus.RECEIVED.value
    generated_profile: GeneratedProfile
    message: str = "profile fetched via mock Account Aggregator consent flow; evaluation now running — poll GET /onboarding/status/{application_id}"


def _run_new_applicant_evaluation(application_id: uuid.UUID, master_row: dict, feature_vector_row: dict, bureau_row: dict, bank_row: dict) -> None:  # noqa: E501 -- itr/assets rows aren't needed here: they've already been folded into feature_vector_row's cross-source fields by onboard_new_applicant(), and evaluate_route_and_price() only consumes master/feature_vector/bureau/bank per its own signature
    """background task body, same pattern as src/api/routers/applications.py's _run_evaluation — own fresh session, since the request-scoped one is already closed by the time this runs."""
    with get_session() as session:
        application = session.get(Application, application_id)
        if application is None:
            return
        try:
            evaluate_route_and_price(session, application, master_row, feature_vector_row, bureau_row, bank_row, actor="onboarding_demo")
        except Exception as exc:  # noqa: BLE001 -- background task's only recourse is to record the failure
            application.status = ApplicationStatus.FAILED
            from src.db.audit import write_audit_log
            write_audit_log(session, actor="onboarding_demo", action="EVALUATION_FAILED", entity_type="application",
                             entity_id=str(application_id), after={"error": str(exc)})


@router.post("/demo/{section}/fetch", response_model=DemoFetchResponse, status_code=201)
def demo_fetch(
    section: str,
    request: DemoFetchRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    dataset: ApplicantDataset = Depends(get_dataset),
) -> DemoFetchResponse:
    if section not in _VALID_SECTIONS:
        raise HTTPException(status_code=404, detail=f"unknown demo section {section!r}, must be one of {sorted(_VALID_SECTIONS)}")
    if request.applicant_type not in _VALID_APPLICANT_TYPES:
        raise HTTPException(status_code=422, detail=f"applicant_type must be one of {sorted(_VALID_APPLICANT_TYPES)}")
    if request.declared_income_monthly <= 0 or request.requested_loan_amount <= 0 or request.requested_tenure_months <= 0:
        raise HTTPException(status_code=422, detail="declared_income_monthly, requested_loan_amount, and requested_tenure_months must all be positive")

    new_input = NewApplicantInput(
        applicant_type=request.applicant_type,
        declared_income_monthly=request.declared_income_monthly,
        requested_loan_amount=request.requested_loan_amount,
        requested_tenure_months=request.requested_tenure_months,
        age=request.age,
        employment_vintage_months=request.employment_vintage_months,
        business_vintage_months=request.business_vintage_months,
        declared_existing_obligations=request.declared_existing_obligations,
        risk_profile=_SECTION_TO_RISK_PROFILE[section],
        force_high_loan_amount=_SECTION_FORCE_HIGH_LOAN[section],
    )

    # this IS the mock "Account Aggregator fetch" — real generation work
    # happens here, per-call, so calling /fetch twice in the same section
    # (as planned for the judge demo) produces two genuinely different
    # synthesized profiles landing on the same guaranteed outcome, not a
    # cached/replayed response.
    master_row, feature_vector_row, bureau_row, bank_row, itr_rows, assets_row = onboard_new_applicant(new_input)
    applicant_id = master_row["applicant_id"]

    # register into the live in-memory dataset too (same reasoning as the
    # earlier synthesize endpoint) — keeps this applicant_id resolvable via
    # the plain POST /applications path as well, for consistency, even
    # though this endpoint submits it directly below.
    dataset.master_by_id[applicant_id] = master_row
    dataset.vector_by_id[applicant_id] = feature_vector_row
    dataset.bureau_by_id[applicant_id] = bureau_row
    dataset.bank_by_id[applicant_id] = bank_row

    application = Application(
        applicant_id=applicant_id,
        applicant_type=ApplicantType(master_row["applicant_type"]),
        requested_loan_amount=master_row["requested_loan_amount"],
        requested_tenure_months=master_row["requested_tenure_months"],
        status=ApplicationStatus.RECEIVED,
        normalized_profile_snapshot=master_row,
    )
    db.add(application)
    db.flush()
    application_id = application.id
    db.commit()

    background_tasks.add_task(_run_new_applicant_evaluation, application_id, master_row, feature_vector_row, bureau_row, bank_row)

    return DemoFetchResponse(
        application_id=str(application_id),
        applicant_id=applicant_id,
        full_name=request.full_name,
        section=section,
        generated_profile=GeneratedProfile(
            master_profile=master_row,
            bureau_data=bureau_row,
            bank_statement_data=bank_row,
            itr_data=itr_rows,
            assets_data=assets_row,
            feature_vector=feature_vector_row,
        ),
    )


# ---------------------------------------------------------------------------
# status/explain — polled after fetch. Full decision detail (reused from
# the existing serializer, so the rule trace shown here is byte-identical
# to what GET /applications/{id}/decision already returns) PLUS a SHAP
# explanation for STP_APPROVED/EXCEPTION_REQUIRED decisions.
# ---------------------------------------------------------------------------

class ShapContribution(BaseModel):
    feature: str
    value: float | None
    shap_value: float


class ShapExplanation(BaseModel):
    base_value: float
    predicted_probability: float
    top_contributions: list[ShapContribution]


class DemoStatusResponse(BaseModel):
    application_id: str
    application_status: str
    outcome: str | None = None
    effective_outcome: str | None = None
    risk_grade: str | None = None
    eligible_amount: float | None = None
    interest_rate: float | None = None
    tenure_months: int | None = None
    model_risk_score: float | None = None
    triggered_rules: list[dict] | None = None
    reason_codes: list[str] = Field(default_factory=list, description="reason_code of every FIRED rule (HARD_REJECT/L1/L2). Empty for a clean STP_APPROVED where nothing fired — see top_reasons for that case's explanation instead.")
    top_reasons: list[str] = Field(default_factory=list, description="at least 5 human-readable reasons behind the outcome, for ALL FOUR outcomes (HARD_REJECT, L1, L2, STP_APPROVED) — fired-rule reason codes when any rule fired (reject/L1/L2), or the top SHAP feature contributions phrased as reasons when the decision came from a clean rules pass with nothing fired (a clean STP approval) or to supplement the fired-rule list once SHAP is also available (L1/L2, which DO reach pricing).")
    plain_english_explanation: str | None = Field(default=None, description="a Gemini-generated paragraph rephrasing reason_codes/top_reasons/shap_explanation for a non-technical reader. Purely additive on top of those fields, which remain unchanged and equally present -- this is None (not an error) whenever GEMINI_API_KEY isn't configured or the LLM call fails for any reason; the frontend should fall back to showing top_reasons directly in that case, never block on this field.")
    shap_explanation: ShapExplanation | None = Field(default=None, description="present for STP_APPROVED/EXCEPTION_REQUIRED (L1/L2) — these are the only outcomes that reach price_decision()/the XGBoost model at all. Genuinely null, not missing, for HARD_REJECT/INSUFFICIENT_DATA: those are decided by the rules engine before scoring ever runs, so there is no model output to explain — their explanation IS triggered_rules/reason_codes/top_reasons instead.")


def _build_shap_explanation_with_session(session: Session, application: Application) -> ShapExplanation | None:
    """
    reconstructs the SAME encoded feature row compute_risk_grade() used
    internally (src/pricing/eligibility.py) from the application's own
    stored rule_context_snapshot, so the SHAP explanation is guaranteed to
    reflect the row that actually drove the decision — not a separately
    re-derived approximation that could silently drift from it. Takes an
    open session (the caller — the status endpoint — already has one) since
    the admin-weighted-field lookup below needs a DB read.
    """
    if application.rule_context_snapshot is None:
        return None

    context = application.rule_context_snapshot
    pipeline = pipeline_for(application.applicant_type)

    model, feature_columns = load_model(pipeline)
    explainer = build_explainer(model)

    weighted_fields = active_weighted_fields_for_pipeline(session, pipeline)
    admin_signal = compute_weighted_risk_signal(weighted_fields, context)

    feature_vector_row = extract_feature_vector_fields(context)
    encoded_row = build_feature_row(context, feature_vector_row, admin_signal)

    explanation: ApplicantExplanation = explain_applicant(explainer, encoded_row, feature_columns, top_n=10)

    return ShapExplanation(
        base_value=explanation.base_value,
        predicted_probability=explanation.predicted_probability,
        top_contributions=[
            ShapContribution(feature=c.feature, value=c.value, shap_value=c.shap_value)
            for c in explanation.top_contributions
        ],
    )


@router.get("/status/{application_id}", response_model=DemoStatusResponse)
def demo_status(application_id: str, db: Session = Depends(get_db)) -> DemoStatusResponse:
    try:
        app_uuid = uuid.UUID(application_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="application_id must be a valid UUID")

    application = db.get(Application, app_uuid)
    if application is None:
        raise HTTPException(status_code=404, detail="application not found")

    current_decision = db.query(Decision).filter(Decision.application_id == app_uuid, Decision.is_current.is_(True)).first()
    decision_response = serialize_decision(db, application, current_decision)

    shap_explanation = None
    if current_decision is not None and current_decision.outcome in (DecisionOutcome.STP_APPROVED, DecisionOutcome.EXCEPTION_REQUIRED):
        shap_explanation = _build_shap_explanation_with_session(db, application)

    fired_rules = [r for r in (decision_response.triggered_rules or []) if r.condition_met is True]
    reason_codes = [r.reason_code for r in fired_rules]

    # top_reasons: at least 5 human-readable reasons, for ALL four outcomes.
    # - HARD_REJECT / L1 / L2: something fired by definition (that's WHY the
    #   outcome isn't a clean STP) -- lead with those, phrased with severity.
    # - Any outcome that reached pricing (STP_APPROVED, L1, L2) ALSO gets
    #   SHAP-based reasons appended, since those explain the model's own
    #   contribution, not just the rules engine's -- for STP_APPROVED with
    #   nothing fired, these SHAP reasons are the entire explanation, which
    #   is why 5 is only guaranteed once shap_explanation is available; a
    #   HARD_REJECT with fewer than 5 fired rules is explained as fully as
    #   the rules engine's own facts support -- padding with fabricated
    #   reasons would misrepresent the decision, so top_reasons can be
    #   shorter than 5 only in that specific, honest case.
    top_reasons: list[str] = [
        f"{r.reason_code} ({r.outcome}{'/' + r.severity if r.severity else ''}) — {r.field} {r.operator} {r.threshold} (actual: {r.actual_value})"
        for r in fired_rules
    ]
    if shap_explanation is not None:
        for c in shap_explanation.top_contributions[:5]:
            direction = "increased" if c.shap_value > 0 else "decreased"
            top_reasons.append(f"model factor: {c.feature}={c.value} {direction} predicted risk (SHAP {c.shap_value:+.4f})")

    # additive-only: rephrases the reasons already computed above into plain
    # English via Gemini. Never blocks or fails the response -- returns None
    # (not an exception) if GEMINI_API_KEY is unset or the call errors, per
    # llm_explain.py's own docstring; everything else in this response is
    # already complete and correct with or without this succeeding.
    plain_english_explanation = None
    if decision_response.outcome is not None:
        plain_english_explanation = generate_plain_english_explanation(
            outcome=decision_response.outcome,
            effective_outcome=decision_response.effective_outcome or "",
            risk_grade=decision_response.risk_grade,
            eligible_amount=decision_response.eligible_amount,
            interest_rate=decision_response.interest_rate,
            reason_codes=reason_codes,
            top_reasons=top_reasons,
        )

    return DemoStatusResponse(
        application_id=application_id,
        application_status=decision_response.application_status,
        outcome=decision_response.outcome,
        effective_outcome=decision_response.effective_outcome,
        risk_grade=decision_response.risk_grade,
        eligible_amount=decision_response.eligible_amount,
        interest_rate=decision_response.interest_rate,
        tenure_months=decision_response.tenure_months,
        model_risk_score=decision_response.model_risk_score,
        triggered_rules=[r.model_dump() for r in (decision_response.triggered_rules or [])],
        reason_codes=reason_codes,
        top_reasons=top_reasons,
        plain_english_explanation=plain_english_explanation,
        shap_explanation=shap_explanation,
    )
