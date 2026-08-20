"""
output schema for the feature engine.

optionality follows a deliberate per-field rule, not a blanket policy:
a field stays Optional[...] = None whenever a rule author would plausibly
write a DIFFERENT rule for "missing/unknown" versus "computed, genuinely
zero or false" — which is true for nearly every ratio, flag, and band in
this schema, since a false 0.0/False reading in those cases looks like a
favorable or neutral signal rather than an absence of data.

three exceptions get plain defaults instead:
  - *_data_completeness fields (float = 0.0): their entire purpose is to
    REPORT absence as a number, not hide it — a 0.0 here is informative,
    not misleading.
  - itr_filing_count (int = 0): passes the "same downstream treatment"
    test — whether ITR was never pulled or the applicant filed zero
    returns, a rule treats both as "no ITR history to draw on."

hard_negative_count was deliberately NOT made an exception (reversed from
an earlier draft) — a rule author plausibly wants "unknown hard-negative
status" to route to manual review, a different action than "confirmed
zero hard negatives," so it stays Optional despite being a count.
"""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# raw input schemas
#
# required/optional here follows the applicability matrix (mandatory /
# conditional / optional per source and applicant type), NOT the derived-
# field "missing vs zero" test above — that test only applies to computed
# outputs. A raw field being Optional here just means the source may not
# supply it; the engine's own null-safe compute methods are what translate
# "raw field absent" into the correct Optional[None] on the output side.
#
# these schemas are the contract for whatever your teammate's synthetic
# data generator produces. per your note, the consolidated feature schema
# doc is not expected to change for the foreseeable future — if it does,
# these classes need updating alongside the engine's dict-key lookups.
# ---------------------------------------------------------------------------


class DPDMonth(BaseModel):
    """
    one month's DPD bucket counts, as consumed by dpd_history.
    a month with no reported data should be represented as None in the
    dpd_history list itself, not as a DPDMonth with all-zero buckets —
    those are different facts (see engine.py's _compute_dpd_history_features).
    """

    d0_29: int = 0
    d30_59: int = 0
    d60_89: int = 0
    d90_plus: int = 0


class BureauRecord(BaseModel):
    """
    raw bureau / credit information record. one row per applicant.
    mandatory for all applicant types per the applicability matrix.
    dpd_history is chronological, oldest to newest; entries may be None
    for months lacking reported data (distinct from a genuinely clean
    all-zero month) — see DPDMonth docstring.
    """

    applicant_id: str
    bureau_score: Optional[int] = None
    active_loan_count: Optional[int] = None
    closed_loan_count: Optional[int] = None
    total_sanctioned_amount: Optional[float] = None
    total_outstanding_amount: Optional[float] = None
    secured_loan_count: Optional[int] = None
    unsecured_loan_count: Optional[int] = None
    recent_enquiry_count_30d: Optional[int] = None
    recent_enquiry_count_90d: Optional[int] = None
    recent_enquiry_count_180d: Optional[int] = None
    overdue_amount: Optional[float] = None
    max_dpd: Optional[int] = None
    dpd_history: Optional[list[DPDMonth | None]] = None
    dpd_recency_months: Optional[int] = None
    write_off_flag: Optional[bool] = None
    write_off_amount: Optional[float] = None
    settlement_flag: Optional[bool] = None
    settlement_amount: Optional[float] = None
    default_flag: Optional[bool] = None
    suit_filed_flag: Optional[bool] = None
    credit_card_utilization: Optional[float] = None


class BankStatementRecord(BaseModel):
    """
    raw bank statement / account aggregator record. one row per applicant.
    mandatory for all applicant types per the applicability matrix.
    statement_months is accompanying pull metadata (AA lookback window
    length), needed to normalize bounce_rate / overdraft_frequency —
    not part of the original source field list but required for those
    derivations to be computed correctly rather than assumed.
    """

    applicant_id: str
    number_of_accounts: Optional[int] = None
    account_type: Optional[list[str]] = None
    account_status: Optional[list[str]] = None
    account_opening_date: Optional[date] = None
    current_balance: Optional[float] = None
    average_balance: Optional[float] = None
    minimum_balance: Optional[float] = None
    current_od_limit: Optional[float] = None
    drawing_limit: Optional[float] = None
    avg_monthly_credit_inflow: Optional[float] = None
    avg_monthly_debit_outflow: Optional[float] = None
    inflow_trend: Optional[str] = None
    emi_like_recurring_debits: Optional[list[float]] = None
    salary_credit_detected: Optional[bool] = None
    business_credit_detected: Optional[bool] = None
    cash_deposit_amount: Optional[float] = None
    cash_withdrawal_amount: Optional[float] = None
    upi_inflow: Optional[float] = None
    upi_outflow: Optional[float] = None
    neft_rtgs_imps_inflow: Optional[float] = None
    neft_rtgs_imps_outflow: Optional[float] = None
    bounce_return_count: Optional[int] = None
    overdraft_occurrence_count: Optional[int] = None
    cash_flow_volatility: Optional[float] = None
    statement_months: Optional[int] = None


class ITRRecord(BaseModel):
    """
    raw ITR / income record. one row per (applicant_id, year_label) pair —
    e.g. two rows per applicant for yr1 and yr2. mandatory for all applicant
    types per the applicability matrix.
    """

    applicant_id: str
    year_label: str  # "yr1", "yr2", extensible to "yr3"+
    assessment_year: Optional[str] = None
    itr_type: Optional[str] = None
    gross_total_income: Optional[float] = None
    total_income: Optional[float] = None
    salary_income: Optional[float] = None
    business_income: Optional[float] = None
    professional_income: Optional[float] = None
    interest_income: Optional[float] = None
    dividend_income: Optional[float] = None
    capital_gains: Optional[float] = None
    other_income: Optional[float] = None
    deductions: Optional[float] = None
    tax_paid: Optional[float] = None


class EngineeredApplicantFeatureVector(BaseModel):
    applicant_id: str
    computed_at: datetime

    # --- bureau / credit information ---
    bureau_score_band: Optional[str] = None
    has_credit_history: Optional[bool] = None
    bureau_thin_file_flag: Optional[bool] = None
    credit_utilization: Optional[float] = None
    secured_unsecured_ratio: Optional[float] = None
    unsecured_exposure_share: Optional[float] = None
    overdue_to_outstanding_ratio: Optional[float] = None
    avg_exposure_per_active_loan: Optional[float] = None
    closed_to_total_loan_ratio: Optional[float] = None
    writeoff_to_sanctioned_ratio: Optional[float] = None
    settlement_to_sanctioned_ratio: Optional[float] = None
    enquiry_rate_30d: Optional[float] = None
    enquiry_rate_90d: Optional[float] = None
    enquiry_rate_180d: Optional[float] = None
    enquiry_acceleration: Optional[float] = None
    enquiry_acceleration_90_180: Optional[float] = None
    enquiries_per_active_loan: Optional[float] = None
    max_dpd_band: Optional[str] = None
    dpd_severity_score: Optional[float] = None
    dpd_recency_decay: Optional[float] = None
    is_recently_delinquent: Optional[bool] = None
    dpd_trend: Optional[float] = None
    chronic_delinquency_flag: Optional[bool] = None
    time_since_last_clean_period: Optional[int] = None
    dpd_history_completeness: float = 0.0
    hard_negative_flag: Optional[bool] = None
    hard_negative_count: Optional[int] = None
    negative_severity_amount: Optional[float] = None
    negative_severity_ratio: Optional[float] = None
    credit_card_utilization_band: Optional[str] = None
    bureau_data_completeness: float = 0.0

    # --- bank statement / account aggregator ---
    banking_vintage_months: Optional[float] = None
    active_account_ratio: Optional[float] = None
    net_monthly_cash_flow: Optional[float] = None
    cash_flow_margin_ratio: Optional[float] = None
    balance_stress_ratio: Optional[float] = None
    current_to_average_balance_ratio: Optional[float] = None
    od_utilization: Optional[float] = None
    emi_to_inflow_ratio: Optional[float] = None
    bounce_rate: Optional[float] = None
    overdraft_frequency: Optional[float] = None
    bounce_severity_flag: Optional[bool] = None
    cash_dependency_ratio: Optional[float] = None
    digital_payment_share: Optional[float] = None
    formal_channel_share: Optional[float] = None
    cash_flow_volatility_band: Optional[str] = None
    income_type_consistency_flag: Optional[bool] = None
    banking_data_completeness: float = 0.0

    # --- itr / income (yr1/yr2 structure) ---
    income_trend_itr: Optional[float] = None
    income_trend_direction: Optional[str] = None
    income_cagr_2yr: Optional[float] = None
    income_cagr_nyr: Optional[float] = None
    income_composition_shift_flag: Optional[bool] = None
    itr_filing_count: int = 0

    income_composition_ratio_salary_yr1: Optional[float] = None
    income_composition_ratio_business_yr1: Optional[float] = None
    income_composition_ratio_investment_yr1: Optional[float] = None
    income_diversification_flag_yr1: Optional[bool] = None
    effective_tax_rate_yr1: Optional[float] = None
    deduction_ratio_yr1: Optional[float] = None
    gross_to_taxable_gap_yr1: Optional[float] = None
    itr_data_completeness_yr1: float = 0.0

    income_composition_ratio_salary_yr2: Optional[float] = None
    income_composition_ratio_business_yr2: Optional[float] = None
    income_composition_ratio_investment_yr2: Optional[float] = None
    income_diversification_flag_yr2: Optional[bool] = None
    effective_tax_rate_yr2: Optional[float] = None
    deduction_ratio_yr2: Optional[float] = None
    gross_to_taxable_gap_yr2: Optional[float] = None
    itr_data_completeness_yr2: float = 0.0


class FeatureBatch(BaseModel):
    """
    batch wrapper around list of engineered feature vectors.
    used for bulk serialization / API transport when scoring multiple
    applicants in one request-response cycle.
    """

    vectors: list[EngineeredApplicantFeatureVector]
