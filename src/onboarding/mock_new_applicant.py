"""
mock_new_applicant.py — Phase 10-adjacent, additive-only. Generates one
statistically-plausible bureau/bank/ITR/assets record for a BRAND NEW
applicant_id at request time, so the "new account" demo path doesn't only
work against the 8,000 pre-seeded applicants src/api/dataset.py preloads.

Why this exists: PS-1 never requires live CIBIL/AA integration (impossible
to get in a hackathon anyway — those are licensed, regulated data sources),
but the demo should visibly show the underwriting ENGINE handling arbitrary
new profiles, not just replaying pre-generated rows. This module is the
disclosed, honest mock for that — same reasoning already applied to
src/api/deps.py's role-gating ("real authorization logic, demo-grade
identity") and this project's other documented mock boundaries.

Deliberately NOT CopulaGAN — that path needs a batch to fit a joint
distribution against; fitting one per single request is both slow and
meaningless (nothing to correlate against). Instead: the same weighted-
bucket sampling src/ingestion/priors.py already uses for seed rows, plus
src/ingestion/dpd_history.py's real trajectory builder unchanged, plus the
SAME coherence fixes PROGRESS.md documents already being necessary for the
CopulaGAN path (write-off/settlement/default/suit_filed forced False when
max_dpd<=90; requested_loan_amount clipped to the applicant-type ceiling).
Reusing those functions/rules directly (not reimplementing them) is what
keeps a freshly-onboarded applicant statistically consistent with the
seeded 8,000, rather than a second, drifting generation code path.

Output shape matches exactly what src/api/dataset.py's ApplicantDataset.get()
returns: (master_row, feature_vector_row, bureau_row, bank_row) — so a
newly-onboarded applicant can be registered into the SAME in-memory dataset
and submitted through the existing, unmodified POST /applications flow.

**demo_scenario / force_high_loan_amount**: two deliberate, named levers
(not a probabilistic risk_profile alone) so all four PS-1 minimum demo
outcomes (HARD_REJECT, STP_APPROVED, L1, L2) are individually, reliably
reachable on demand rather than hoped for from a random draw:
  - risk_profile ("clean"/"borderline"/"delinquent") drives the bureau-
    score/DPD axis — reaches HARD_REJECT, STP_APPROVED, and L1 (borderline
    bureau score / high utilization).
  - force_high_loan_amount is a SECOND, independent lever, because none of
    the seeded L2 rules (IND_HIGH_LOAN_AMOUNT, MSME_HIGH_LOAN_AMOUNT,
    MSME_SHORT_VINTAGE) are on the bureau-score/DPD axis at all — L2 is
    reached by pushing requested_loan_amount just over the pipeline's L2
    threshold instead, regardless of risk_profile.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass

from src.features.cross_source import compute_cross_source_features
from src.features.engine import FeatureEngine
from src.features.schemas import AssetsRecord, BankStatementRecord, BureauRecord, ITRRecord
from src.ingestion.dpd_history import generate_dpd_history
from src.ingestion.priors import (
    ACTIVE_LOANS_BUCKETS,
    BANK_ACCOUNTS_BUCKETS,
    BANK_BOUNCES_BUCKETS,
    BUREAU_SCORE_BUCKETS,
    CC_UTILIZATION_BUCKETS,
    ENQUIRY_30D_BUCKETS,
    INVESTMENT_ASSETS_BUCKETS,
    MAX_DPD_BUCKETS,
    REFERENCE_DATE,
    clip,
    sample_bucket,
)

_INDIVIDUAL_LOAN_CEILING = 2_000_000  # matches priors.py's personal-loan bucket ceiling
_ASSESSMENT_YEAR_BY_LABEL = {"yr1": "2025-26", "yr2": "2024-25"}

# seeded thresholds this module deliberately targets for force_high_loan_amount
# (see src/rules/seed_rules.py — IND_HIGH_LOAN_AMOUNT > 1,500,000, MSME_HIGH_LOAN_AMOUNT
# > 5,000,000). Set comfortably above the threshold, not just barely over it, so the
# demo isn't fragile to a future threshold edit landing exactly on the boundary.
_L2_LOAN_AMOUNT_INDIVIDUAL = 1_800_000  # > IND_HIGH_LOAN_AMOUNT (1.5M), still <= individual ceiling (2M)
_L2_LOAN_AMOUNT_MSME = 6_000_000  # > MSME_HIGH_LOAN_AMOUNT (5M)

_VALID_DEMO_SCENARIOS = {"hard_reject", "stp_approved", "l1_exception", "l2_exception"}

# n % 4 -> demo_scenario, in the exact order requested (reject, accept, L1, L2)
_SCENARIO_BY_MOD4 = {
    0: "hard_reject",
    1: "stp_approved",
    2: "l1_exception",
    3: "l2_exception",
}


def scenario_for_sequence_number(n: int) -> str:
    """the n%4 cycling behavior — call this to turn a running counter into
    a demo_scenario value. Kept as a pure function (no hidden global state)
    so the caller (the API layer) owns the counter, not this module."""
    return _SCENARIO_BY_MOD4[n % 4]


@dataclass
class NewApplicantInput:
    """
    the small set of fields the frontend's onboarding form actually needs
    to collect — deliberately not the full ~40-field raw schema. Everything
    else (bureau history, bank cash-flow detail, ITR components) is
    synthesized coherently from these, the same way a real applicant's
    stated basics plus a bureau/AA pull would produce a full profile.
    """

    applicant_type: str  # SALARIED | SELF_EMPLOYED | MSME | CORPORATE
    declared_income_monthly: float
    requested_loan_amount: float
    requested_tenure_months: int
    age: int = 30
    employment_vintage_months: int | None = None
    business_vintage_months: int | None = None
    declared_existing_obligations: float = 0.0
    # optional dial for the demo — lets the presenter pick a plausible
    # "risk shape" (clean file / borderline / delinquent) instead of
    # getting a fully random one on every take. None = fully random, per
    # the real bureau-score distribution.
    risk_profile: str | None = None  # "clean" | "borderline" | "delinquent" | None
    # second independent lever, needed because risk_profile alone can only
    # reach HARD_REJECT/STP/L1 (all driven by the bureau-score/DPD axis) —
    # none of the seeded L2 rules (IND_HIGH_LOAN_AMOUNT, MSME_HIGH_LOAN_AMOUNT,
    # MSME_SHORT_VINTAGE) are on that axis at all. force_high_loan_amount
    # pushes requested_loan_amount just over the pipeline's L2 threshold
    # regardless of what the caller passed in, so demo_scenario="l2_exception"
    # is reachable deterministically rather than by chance.
    force_high_loan_amount: bool = False

    @classmethod
    def apply_demo_scenario(cls, base: "NewApplicantInput", demo_scenario: str) -> "NewApplicantInput":
        """
        returns a NEW NewApplicantInput with risk_profile/force_high_loan_amount
        overridden to reliably reach the requested outcome, leaving every
        other field (applicant_type, income, tenure, etc.) exactly as given.
        Named scenario -> lever mapping, not a black box:
          hard_reject   -> risk_profile="delinquent"  (bureau score < 600 / hard-negative)
          stp_approved  -> risk_profile="clean"        (bureau score >= 700, max_dpd=0)
          l1_exception  -> risk_profile="borderline"   (bureau score 600-699)
          l2_exception  -> risk_profile="clean" + force_high_loan_amount=True
                           (clean bureau file, but loan amount alone trips
                           IND_HIGH_LOAN_AMOUNT/MSME_HIGH_LOAN_AMOUNT — isolates
                           the L2 lever from the bureau-score axis entirely,
                           so it's obvious in the demo which rule fired)
        """
        if demo_scenario not in _VALID_DEMO_SCENARIOS:
            raise ValueError(f"demo_scenario must be one of {sorted(_VALID_DEMO_SCENARIOS)}, got {demo_scenario!r}")

        overrides = {
            "hard_reject": {"risk_profile": "delinquent", "force_high_loan_amount": False},
            "stp_approved": {"risk_profile": "clean", "force_high_loan_amount": False},
            "l1_exception": {"risk_profile": "borderline", "force_high_loan_amount": False},
            "l2_exception": {"risk_profile": "clean", "force_high_loan_amount": True},
        }[demo_scenario]

        return cls(
            applicant_type=base.applicant_type,
            declared_income_monthly=base.declared_income_monthly,
            requested_loan_amount=base.requested_loan_amount,
            requested_tenure_months=base.requested_tenure_months,
            age=base.age,
            employment_vintage_months=base.employment_vintage_months,
            business_vintage_months=base.business_vintage_months,
            declared_existing_obligations=base.declared_existing_obligations,
            **overrides,
        )


def _new_applicant_id() -> str:
    return f"NEW{uuid.uuid4().hex[:9].upper()}"


def _sample_bureau_score(risk_profile: str | None) -> int:
    if risk_profile == "clean":
        return random.randint(750, 820)
    if risk_profile == "borderline":
        return random.randint(600, 699)
    if risk_profile == "delinquent":
        return random.randint(300, 579)
    _, value = sample_bucket(BUREAU_SCORE_BUCKETS)
    return int(round(value))


def _synthesize_bureau(applicant_id: str, risk_profile: str | None) -> BureauRecord:
    bureau_score = _sample_bureau_score(risk_profile)

    if risk_profile == "delinquent":
        max_dpd_label, max_dpd = "181+", random.randint(181, 365)
        write_off_flag = random.random() < 0.5
    elif risk_profile == "borderline":
        max_dpd_label, max_dpd = "31-60", random.randint(31, 60)
        write_off_flag = False
    elif risk_profile == "clean":
        max_dpd_label, max_dpd = "0", 0
        write_off_flag = False
    else:
        max_dpd_label, max_dpd_f = sample_bucket(MAX_DPD_BUCKETS)
        max_dpd = int(round(max_dpd_f))
        write_off_flag = random.random() < 0.5 if max_dpd_label == "SERIOUS_WRITEOFF" else False

    # same coherence rule PROGRESS.md documents fixing in synth.py: a
    # write-off/settlement/default/suit-filed flag with a barely-delinquent
    # max_dpd is incoherent (a write-off with 1 day past due). Forced False
    # below max_dpd<=90 here too, not just at seed-generation time.
    settlement_flag = (random.random() < 0.15) if max_dpd > 90 else False
    default_flag = (random.random() < 0.10) if max_dpd > 90 else False
    suit_filed_flag = (random.random() < 0.05) if max_dpd > 180 else False
    if max_dpd <= 90:
        write_off_flag = False

    dpd_recency_months = 0 if max_dpd > 90 else random.randint(6, 24)
    credit_history_type = "ESTABLISHED" if risk_profile != "clean" or random.random() < 0.85 else "THIN_FILE"

    dpd_history = generate_dpd_history(
        max_dpd=max_dpd,
        dpd_recency_months=dpd_recency_months,
        credit_history_type=credit_history_type,
        max_dpd_label=max_dpd_label,
    )

    _, active_loans_f = sample_bucket(ACTIVE_LOANS_BUCKETS)
    active_loan_count = int(round(active_loans_f))
    closed_loan_count = random.randint(0, 4)
    secured = random.randint(0, active_loan_count)
    unsecured = active_loan_count - secured

    sanctioned = round(random.uniform(50_000, 1_500_000), 2) if active_loan_count > 0 else 0.0
    outstanding = round(sanctioned * random.uniform(0.1, 0.9), 2) if sanctioned > 0 else 0.0
    overdue = round(outstanding * random.uniform(0.0, 0.3), 2) if max_dpd > 0 and outstanding > 0 else 0.0

    _, e30_f = sample_bucket(ENQUIRY_30D_BUCKETS)
    e30 = int(round(e30_f))
    e90 = e30 + random.randint(0, 2)
    e180 = e90 + random.randint(0, 3)

    _, cc_util = sample_bucket(CC_UTILIZATION_BUCKETS)

    return BureauRecord(
        applicant_id=applicant_id,
        bureau_score=bureau_score,
        active_loan_count=active_loan_count,
        closed_loan_count=closed_loan_count,
        total_sanctioned_amount=sanctioned,
        total_outstanding_amount=outstanding,
        secured_loan_count=secured,
        unsecured_loan_count=unsecured,
        recent_enquiry_count_30d=e30,
        recent_enquiry_count_90d=e90,
        recent_enquiry_count_180d=e180,
        overdue_amount=overdue,
        max_dpd=max_dpd,
        dpd_history=dpd_history,
        dpd_recency_months=dpd_recency_months,
        write_off_flag=write_off_flag,
        write_off_amount=round(sanctioned * random.uniform(0.1, 0.4), 2) if write_off_flag else 0.0,
        settlement_flag=settlement_flag,
        settlement_amount=round(sanctioned * random.uniform(0.05, 0.3), 2) if settlement_flag else 0.0,
        default_flag=default_flag,
        suit_filed_flag=suit_filed_flag,
        credit_card_utilization=round(clip(cc_util / 100.0, 0.0, 1.0), 4),
    )


def _synthesize_bank(applicant_id: str, declared_income_monthly: float, applicant_type: str) -> BankStatementRecord:
    _, num_accounts_f = sample_bucket(BANK_ACCOUNTS_BUCKETS)
    num_accounts = int(round(num_accounts_f))

    credit_inflow = round(declared_income_monthly * random.uniform(0.9, 1.15), 2)
    debit_outflow = round(credit_inflow * random.uniform(0.6, 0.95), 2)
    avg_balance = round(credit_inflow * random.uniform(0.3, 1.5), 2)
    min_balance = round(avg_balance * random.uniform(0.1, 0.6), 2)
    current_balance = round(avg_balance * random.uniform(0.5, 1.5), 2)

    emi_count = random.randint(0, 2)
    emi_debits = [round(credit_inflow * random.uniform(0.05, 0.20), 2) for _ in range(emi_count)]

    _, bounces_f = sample_bucket(BANK_BOUNCES_BUCKETS)
    bounce_count = int(round(bounces_f))

    is_salaried = applicant_type == "SALARIED"

    return BankStatementRecord(
        applicant_id=applicant_id,
        number_of_accounts=num_accounts,
        account_type=["SAVINGS"] * num_accounts,
        account_status=["Active"] * num_accounts,
        account_opening_date=REFERENCE_DATE.replace(year=REFERENCE_DATE.year - random.randint(1, 8)),
        current_balance=current_balance,
        average_balance=avg_balance,
        minimum_balance=min_balance,
        current_od_limit=0.0,
        drawing_limit=0.0,
        avg_monthly_credit_inflow=credit_inflow,
        avg_monthly_debit_outflow=debit_outflow,
        inflow_trend=random.choice(["STABLE", "GROWING", "DECLINING"]),
        emi_like_recurring_debits=emi_debits,
        salary_credit_detected=is_salaried,
        business_credit_detected=not is_salaried,
        cash_deposit_amount=round(credit_inflow * random.uniform(0.0, 0.15), 2),
        cash_withdrawal_amount=round(debit_outflow * random.uniform(0.0, 0.15), 2),
        upi_inflow=round(credit_inflow * random.uniform(0.1, 0.4), 2),
        upi_outflow=round(debit_outflow * random.uniform(0.1, 0.4), 2),
        neft_rtgs_imps_inflow=round(credit_inflow * random.uniform(0.1, 0.3), 2),
        neft_rtgs_imps_outflow=round(debit_outflow * random.uniform(0.1, 0.3), 2),
        bounce_return_count=bounce_count,
        overdraft_occurrence_count=random.randint(0, 1) if bounce_count > 0 else 0,
        cash_flow_volatility=round(random.uniform(0.05, 0.5), 4),
        statement_months=6,
    )


def _synthesize_itr(applicant_id: str, applicant_type: str, declared_income_monthly: float) -> list[ITRRecord]:
    annual = declared_income_monthly * 12
    rows: list[ITRRecord] = []
    for label, growth in (("yr1", 1.0), ("yr2", random.uniform(0.85, 1.0))):
        gross = round(annual * growth, 2)
        deductions = round(gross * random.uniform(0.02, 0.15), 2)
        total = round(max(gross - deductions, 0.0), 2)
        salary_share = 0.9 if applicant_type == "SALARIED" else 0.05
        rows.append(
            ITRRecord(
                applicant_id=applicant_id,
                year_label=label,
                assessment_year=_ASSESSMENT_YEAR_BY_LABEL[label],
                itr_type="ITR1" if applicant_type == "SALARIED" else "ITR3",
                gross_total_income=gross,
                total_income=total,
                salary_income=round(gross * salary_share, 2),
                business_income=round(gross * (1 - salary_share) * 0.8, 2) if applicant_type != "SALARIED" else 0.0,
                professional_income=0.0,
                interest_income=round(gross * 0.02, 2),
                dividend_income=0.0,
                capital_gains=0.0,
                other_income=0.0,
                deductions=deductions,
                tax_paid=round(total * random.uniform(0.02, 0.2), 2),
            )
        )
    return rows


def _synthesize_assets(applicant_id: str) -> AssetsRecord:
    _, investable = sample_bucket(INVESTMENT_ASSETS_BUCKETS)
    if investable <= 0:
        return AssetsRecord(applicant_id=applicant_id, has_declared_assets=False)
    mf = round(investable * random.uniform(0.2, 0.5), 2)
    eq = round(investable * random.uniform(0.0, 0.3), 2)
    other = round(investable * random.uniform(0.0, 0.1), 2)
    liquid = round(max(investable - mf - eq - other, 0.0), 2)
    return AssetsRecord(
        applicant_id=applicant_id,
        has_declared_assets=True,
        mutual_fund_value=mf,
        equity_value=eq,
        other_securities_value=other,
        liquid_asset_value=liquid,
        total_portfolio_value=round(mf + eq + other + liquid, 2),
        recent_redemption_value=0.0,
        recent_purchase_value=0.0,
        as_of_date=REFERENCE_DATE,
    )


def onboard_new_applicant(data: NewApplicantInput) -> tuple[dict, dict, dict, dict, list[dict], dict]:
    """
    generates one full, internally-consistent synthetic profile for a brand
    new applicant_id and runs it through the SAME FeatureEngine +
    cross_source pipeline every seeded applicant goes through — not a
    parallel/simplified feature computation. Returns
    (master_row, feature_vector_row, bureau_row, bank_row, itr_rows, assets_row).
    The first four match ApplicantDataset.get()'s return shape exactly, so
    the caller can register this applicant into the live dataset and hand
    it straight to the existing POST /applications flow unmodified; the
    last two (itr_rows, assets_row) are additionally returned so callers
    that want to DISPLAY the full generated profile (e.g. the demo-section
    'AA fetch' response) aren't missing two of the six raw sources this
    module actually generates.
    """
    applicant_id = _new_applicant_id()
    applicant_type = data.applicant_type

    # same coherence fix PROGRESS.md documents for the CopulaGAN path:
    # personal-loan applicants clipped to the individual ceiling, so a
    # SALARIED applicant can't end up with an MSME-scale requested amount.
    requested_loan_amount = data.requested_loan_amount
    if data.force_high_loan_amount:
        # deliberately OVERRIDES whatever the caller passed, rather than
        # just raising it, so demo_scenario="l2_exception" is reliable
        # regardless of what loan amount the presenter typed into the form.
        requested_loan_amount = (
            _L2_LOAN_AMOUNT_MSME if applicant_type in ("MSME", "CORPORATE") else _L2_LOAN_AMOUNT_INDIVIDUAL
        )
    elif applicant_type in ("SALARIED", "SELF_EMPLOYED"):
        requested_loan_amount = min(requested_loan_amount, _INDIVIDUAL_LOAN_CEILING)

    bureau = _synthesize_bureau(applicant_id, data.risk_profile)
    bank = _synthesize_bank(applicant_id, data.declared_income_monthly, applicant_type)
    itr_rows = _synthesize_itr(applicant_id, applicant_type, data.declared_income_monthly)
    assets = _synthesize_assets(applicant_id)

    master_row = {
        "applicant_id": applicant_id,
        "age": data.age,
        "applicant_type": applicant_type,
        "employment_type": "SALARIED" if applicant_type == "SALARIED" else "SELF_EMPLOYED",
        "geography_type": "URBAN",
        "state_code": 27,
        "state_name": "Maharashtra",
        "credit_history_type": "ESTABLISHED",
        "employment_vintage_months": data.employment_vintage_months,
        "business_vintage_months": data.business_vintage_months,
        "declared_income_monthly": data.declared_income_monthly,
        "declared_existing_obligations": data.declared_existing_obligations,
        "requested_loan_amount": requested_loan_amount,
        "requested_tenure_months": data.requested_tenure_months,
    }

    engine = FeatureEngine()
    itr_years = {r.year_label: r.model_dump() for r in itr_rows}
    vector = engine.compute_features(
        applicant_id=applicant_id,
        bureau_row=bureau.model_dump(),
        bank_row=bank.model_dump(),
        itr_years=itr_years,
        skip_cache=True,  # per-request — no data/features/ cache write for a one-off demo applicant
    )

    assets_row = assets.model_dump()
    cross = compute_cross_source_features(master_row, bank.model_dump(), itr_years, assets_row)
    vector.update(cross)

    itr_row_dicts = [r.model_dump() for r in itr_rows]

    return master_row, vector, bureau.model_dump(), bank.model_dump(), itr_row_dicts, assets_row
