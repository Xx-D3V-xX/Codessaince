"""
feature engineering engine — creditgate underwriting BRE + ML
computes same-source derived features per the consolidated schema:
  bureau, bank statement / account aggregator, itr income
  (gst / e-way bill / e-invoice / alternate data are conditional, business-owner-only —
   stubbed as no-op branches here since salaried/self-employed applicants never populate them)
cross-source derived features (obligation_discrepancy, income_discrepancy, etc.)
are NOT computed here — they require the normalized applicant profile assembled
across all sources and belong in a separate cross_source.py stage, per the
project's own documented deferral of those features until every raw source
is finalized.

raw input shape: one row per applicant per source, NOT a transaction stream.
this is a structural difference from a transaction-velocity engine (e.g. gst/upi/ewb
signal streams) — bureau, bank statement, and itr sources are already applicant-level
aggregates or per-year filings, not raw event logs, so there is no ema/velocity
windowing here. dpd_history is the one field with internal time structure
(monthly buckets) and is handled accordingly.
"""

import glob
from datetime import datetime
from pathlib import Path

import numpy as np
import polars as pl
import psutil

from src.features.schemas import EngineeredApplicantFeatureVector


# ---------------------------------------------------------------------------
# configurable weights / thresholds
# per project convention: these are engine-level defaults only. the BRE's own
# rule store (not this file) is the source of truth for thresholds used in
# actual underwriting decisions — these defaults exist so the engine can run
# standalone (tests, local dev) without a live rules-engine connection.
# ---------------------------------------------------------------------------
DPD_BUCKET_WEIGHTS = {
    "d0_29": 0.0,
    "d30_59": 1.0,
    "d60_89": 3.0,
    "d90_plus": 8.0,
}
DPD_RECENCY_HALF_LIFE_MONTHS = 6.0
DPD_RECENT_WINDOW_MONTHS = 6
DPD_MIN_MONTHS_FOR_TREND = 3

BUREAU_SCORE_BANDS = [
    (0, 649, "poor"),
    (650, 699, "fair"),
    (700, 749, "good"),
    (750, 799, "very_good"),
    (800, 900, "excellent"),
]
MAX_DPD_BANDS = [
    (0, 0, "clean"),
    (1, 29, "mild"),
    (30, 59, "moderate"),
    (60, 89, "severe"),
    (90, 10_000, "critical"),
]
CREDIT_CARD_UTIL_BANDS = [
    (0.0, 0.30, "low"),
    (0.30, 0.60, "moderate"),
    (0.60, 0.90, "high"),
    (0.90, 10.0, "maxed"),
]
CASH_FLOW_VOLATILITY_BANDS = [
    (0.0, 0.15, "low"),
    (0.15, 0.40, "moderate"),
    (0.40, 10.0, "high"),
]
INCOME_TREND_BANDS = [
    (-10.0, -0.05, "declining"),
    (-0.05, 0.05, "flat"),
    (0.05, 10.0, "growing"),
]

BUREAU_RAW_FIELD_COUNT = 20
BANK_RAW_FIELD_COUNT = 22
ITR_PER_YEAR_FIELD_COUNT = 11


def _band(value: float | None, bands: list[tuple[float, float, str]]) -> str | None:
    """generic bucketer — returns label for first (lo, hi, label) range containing value"""
    if value is None:
        return None
    for lo, hi, label in bands:
        if lo <= value <= hi:
            return label
    return None


def _safe_div(numerator: float, denominator: float, floor: float = 1.0) -> float:
    """division with a floor on the denominator, matching reference engine's max(x, 1.0) pattern"""
    return numerator / max(denominator, floor)


def _non_null_fraction(row: dict, fields: list[str]) -> float:
    """fraction of the given fields that are non-null in row — used for *_data_completeness features"""
    if not fields:
        return 0.0
    present = sum(1 for f in fields if row.get(f) is not None)
    return present / len(fields)


class FeatureEngine:
    """
    stateful feature engine, per-applicant parquet cache, memory pressure guard.
    compute methods are null-safe and return None (not 0.0) for genuinely missing
    inputs — this is a deliberate departure from the reference engine's zero-fill
    convention. zero and "no data" are different facts throughout this schema
    (see bureau_thin_file_flag, dpd_history_completeness, etc.), and silently
    zero-filling would misrepresent missing data as a favorable clean signal.
    """

    def __init__(self, cache_dir: str = "data/features", spill_threshold_gb: float = 3.0) -> None:
        self.cache_dir = cache_dir
        self.spill_threshold_gb = spill_threshold_gb

    def _check_memory_pressure(self) -> bool:
        proc = psutil.Process()
        rss_gb = proc.memory_info().rss / (1024 ** 3)
        return rss_gb >= (self.spill_threshold_gb * 0.9)

    def _load_cached_features(self, applicant_id: str) -> pl.DataFrame | None:
        path = Path(self.cache_dir) / f"applicant_id={applicant_id}" / "features.parquet"
        if not path.exists():
            return None
        return pl.read_parquet(path)

    def _save_cached_features(self, applicant_id: str, df: pl.DataFrame) -> None:
        path = Path(self.cache_dir) / f"applicant_id={applicant_id}" / "features.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(path)

    # ------------------------------------------------------------------
    # bureau / credit information — derived features
    # ------------------------------------------------------------------
    def _compute_bureau_features(self, bureau_row: dict | None) -> dict:
        """
        all bureau-section derived features per consolidated schema section 2.
        bureau_row is a single dict of raw bureau fields for one applicant
        (bureau data is applicant-level, not a transaction stream).
        returns an all-None dict if bureau_row is None (source entirely absent).
        """
        empty = {
            "bureau_score_band": None,
            "has_credit_history": None,
            "bureau_thin_file_flag": None,
            "credit_utilization": None,
            "secured_unsecured_ratio": None,
            "unsecured_exposure_share": None,
            "overdue_to_outstanding_ratio": None,
            "avg_exposure_per_active_loan": None,
            "closed_to_total_loan_ratio": None,
            "writeoff_to_sanctioned_ratio": None,
            "settlement_to_sanctioned_ratio": None,
            "enquiry_rate_30d": None,
            "enquiry_rate_90d": None,
            "enquiry_rate_180d": None,
            "enquiry_acceleration": None,
            "enquiry_acceleration_90_180": None,
            "enquiries_per_active_loan": None,
            "max_dpd_band": None,
            "dpd_severity_score": None,
            "dpd_recency_decay": None,
            "is_recently_delinquent": None,
            "dpd_trend": None,
            "chronic_delinquency_flag": None,
            "time_since_last_clean_period": None,
            "dpd_history_completeness": 0.0,
            "hard_negative_flag": None,
            "hard_negative_count": None,
            "negative_severity_amount": None,
            "negative_severity_ratio": None,
            "credit_card_utilization_band": None,
            "bureau_data_completeness": 0.0,
        }
        if bureau_row is None:
            return empty

        r = bureau_row
        active_loans = r.get("active_loan_count") or 0
        closed_loans = r.get("closed_loan_count") or 0
        secured = r.get("secured_loan_count") or 0
        unsecured = r.get("unsecured_loan_count") or 0
        sanctioned = r.get("total_sanctioned_amount")
        outstanding = r.get("total_outstanding_amount")
        overdue = r.get("overdue_amount")
        writeoff_amt = r.get("write_off_amount") or 0.0
        settlement_amt = r.get("settlement_amount") or 0.0

        has_credit_history = (active_loans + closed_loans) > 0
        bureau_thin_file_flag = (active_loans == 0 and closed_loans == 0)

        credit_utilization = (
            _safe_div(outstanding, sanctioned) if outstanding is not None and sanctioned is not None else None
        )
        secured_unsecured_ratio = _safe_div(secured, unsecured) if (secured or unsecured) else None
        unsecured_exposure_share = _safe_div(unsecured, active_loans) if active_loans else None
        overdue_to_outstanding_ratio = (
            _safe_div(overdue, outstanding) if overdue is not None and outstanding is not None else None
        )
        avg_exposure_per_active_loan = _safe_div(outstanding, active_loans) if outstanding is not None else None
        closed_to_total_loan_ratio = _safe_div(closed_loans, active_loans + closed_loans)
        writeoff_to_sanctioned_ratio = _safe_div(writeoff_amt, sanctioned) if sanctioned is not None else None
        settlement_to_sanctioned_ratio = _safe_div(settlement_amt, sanctioned) if sanctioned is not None else None

        e30 = r.get("recent_enquiry_count_30d")
        e90 = r.get("recent_enquiry_count_90d")
        e180 = r.get("recent_enquiry_count_180d")
        rate_30 = _safe_div(e30, 30.0) if e30 is not None else None
        rate_90 = _safe_div(e90, 90.0) if e90 is not None else None
        rate_180 = _safe_div(e180, 180.0) if e180 is not None else None
        enquiry_acceleration = _safe_div(rate_30, rate_180, floor=0.001) if rate_30 is not None and rate_180 is not None else None
        enquiry_acceleration_90_180 = _safe_div(rate_90, rate_180, floor=0.001) if rate_90 is not None and rate_180 is not None else None
        enquiries_per_active_loan = _safe_div(e180, active_loans) if e180 is not None else None

        max_dpd = r.get("max_dpd")
        dpd_recency = r.get("dpd_recency_months")
        dpd_history = r.get("dpd_history")  # expected: list[dict] with keys d0_29..d90_plus, one per month, chronological

        dpd_severity_score, dpd_trend, chronic_flag, time_since_clean, dpd_completeness = (
            self._compute_dpd_history_features(dpd_history)
        )
        dpd_recency_decay = (
            max_dpd * float(np.exp(-dpd_recency / DPD_RECENCY_HALF_LIFE_MONTHS))
            if max_dpd is not None and dpd_recency is not None
            else None
        )
        is_recently_delinquent = (
            (dpd_recency <= DPD_RECENT_WINDOW_MONTHS and max_dpd > 0)
            if dpd_recency is not None and max_dpd is not None
            else None
        )

        flags = [r.get("write_off_flag"), r.get("settlement_flag"), r.get("default_flag"), r.get("suit_filed_flag")]
        known_flags = [f for f in flags if f is not None]
        hard_negative_flag = any(known_flags) if known_flags else None
        hard_negative_count = sum(1 for f in known_flags if f) if known_flags else None
        negative_severity_amount = writeoff_amt + settlement_amt
        negative_severity_ratio = _safe_div(negative_severity_amount, sanctioned) if sanctioned is not None else None

        cc_util = r.get("credit_card_utilization")

        return {
            "bureau_score_band": _band(r.get("bureau_score"), BUREAU_SCORE_BANDS),
            "has_credit_history": has_credit_history,
            "bureau_thin_file_flag": bureau_thin_file_flag,
            "credit_utilization": credit_utilization,
            "secured_unsecured_ratio": secured_unsecured_ratio,
            "unsecured_exposure_share": unsecured_exposure_share,
            "overdue_to_outstanding_ratio": overdue_to_outstanding_ratio,
            "avg_exposure_per_active_loan": avg_exposure_per_active_loan,
            "closed_to_total_loan_ratio": closed_to_total_loan_ratio,
            "writeoff_to_sanctioned_ratio": writeoff_to_sanctioned_ratio,
            "settlement_to_sanctioned_ratio": settlement_to_sanctioned_ratio,
            "enquiry_rate_30d": rate_30,
            "enquiry_rate_90d": rate_90,
            "enquiry_rate_180d": rate_180,
            "enquiry_acceleration": enquiry_acceleration,
            "enquiry_acceleration_90_180": enquiry_acceleration_90_180,
            "enquiries_per_active_loan": enquiries_per_active_loan,
            "max_dpd_band": _band(max_dpd, MAX_DPD_BANDS),
            "dpd_severity_score": dpd_severity_score,
            "dpd_recency_decay": dpd_recency_decay,
            "is_recently_delinquent": is_recently_delinquent,
            "dpd_trend": dpd_trend,
            "chronic_delinquency_flag": chronic_flag,
            "time_since_last_clean_period": time_since_clean,
            "dpd_history_completeness": dpd_completeness,
            "hard_negative_flag": hard_negative_flag,
            "hard_negative_count": hard_negative_count,
            "negative_severity_amount": negative_severity_amount,
            "negative_severity_ratio": negative_severity_ratio,
            "credit_card_utilization_band": _band(cc_util, CREDIT_CARD_UTIL_BANDS) if cc_util is not None else None,
            "bureau_data_completeness": _non_null_fraction(
                r,
                [
                    "bureau_score", "active_loan_count", "closed_loan_count", "total_sanctioned_amount",
                    "total_outstanding_amount", "secured_loan_count", "unsecured_loan_count",
                    "recent_enquiry_count_30d", "recent_enquiry_count_90d", "recent_enquiry_count_180d",
                    "overdue_amount", "max_dpd", "dpd_history", "dpd_recency_months", "write_off_flag",
                    "write_off_amount", "settlement_flag", "settlement_amount", "default_flag",
                    "suit_filed_flag", "credit_card_utilization",
                ][:BUREAU_RAW_FIELD_COUNT],
            ),
        }

    @staticmethod
    def _compute_dpd_history_features(
        dpd_history: list[dict] | None,
    ) -> tuple[float | None, float | None, bool | None, int | None, float]:
        """
        option-1 hand-summarized dpd_history features, per project decision:
        dpd_severity_score, dpd_trend, chronic_delinquency_flag, time_since_last_clean_period,
        dpd_history_completeness. dpd_history expected as a chronological list of monthly dicts:
        [{"d0_29": int, "d30_59": int, "d60_89": int, "d90_plus": int}, ...] oldest to newest,
        with None entries for months lacking reported data (distinct from a genuinely clean
        all-zero month).
        """
        if not dpd_history:
            return None, None, None, None, 0.0

        n_months = len(dpd_history)
        reported_months = [m for m in dpd_history if m is not None]
        completeness = len(reported_months) / n_months if n_months else 0.0

        monthly_severity: list[float | None] = []
        for month in dpd_history:
            if month is None:
                monthly_severity.append(None)
                continue
            severity = sum(
                (month.get(bucket) or 0) * weight for bucket, weight in DPD_BUCKET_WEIGHTS.items()
            )
            monthly_severity.append(severity)

        known_severities = [s for s in monthly_severity if s is not None]
        dpd_severity_score = float(sum(known_severities)) if known_severities else None

        # trend: linear regression slope over months with known severity, only if enough data
        known_indexed = [(i, s) for i, s in enumerate(monthly_severity) if s is not None]
        if len(known_indexed) >= DPD_MIN_MONTHS_FOR_TREND:
            xs = np.array([i for i, _ in known_indexed], dtype=np.float64)
            ys = np.array([s for _, s in known_indexed], dtype=np.float64)
            # simple OLS slope, no external dependency
            x_mean, y_mean = xs.mean(), ys.mean()
            denom = float(np.sum((xs - x_mean) ** 2))
            dpd_trend = float(np.sum((xs - x_mean) * (ys - y_mean)) / denom) if denom > 0 else 0.0
        else:
            dpd_trend = None  # insufficient history — null, not zero, per project convention

        # chronic: 2+ non-adjacent months with any non-zero bucket count
        delinquent_month_idxs = [
            i for i, month in enumerate(dpd_history)
            if month is not None and sum(month.get(b) or 0 for b in DPD_BUCKET_WEIGHTS) > 0
        ]
        chronic_flag = None
        if reported_months:
            non_adjacent = 0
            last_idx = None
            for idx in delinquent_month_idxs:
                if last_idx is None or idx - last_idx > 1:
                    non_adjacent += 1
                last_idx = idx
            chronic_flag = non_adjacent >= 2

        # time since last clean period: months since most recent all-zero reported month
        time_since_clean = None
        for offset, month in enumerate(reversed(dpd_history)):
            if month is None:
                continue
            if sum(month.get(b) or 0 for b in DPD_BUCKET_WEIGHTS) == 0:
                time_since_clean = offset
                break

        return dpd_severity_score, dpd_trend, chronic_flag, time_since_clean, completeness

    # ------------------------------------------------------------------
    # bank statement / account aggregator — derived features
    # ------------------------------------------------------------------
    def _compute_bank_statement_features(self, bank_row: dict | None) -> dict:
        """all bank-statement-section derived features per consolidated schema section 3"""
        empty = {k: None for k in [
            "banking_vintage_months", "active_account_ratio", "net_monthly_cash_flow",
            "cash_flow_margin_ratio", "balance_stress_ratio", "current_to_average_balance_ratio",
            "od_utilization", "emi_to_inflow_ratio", "bounce_rate", "overdraft_frequency",
            "bounce_severity_flag", "cash_dependency_ratio", "digital_payment_share",
            "formal_channel_share", "cash_flow_volatility_band", "income_type_consistency_flag",
        ]}
        empty["banking_data_completeness"] = 0.0
        if bank_row is None:
            return empty

        r = bank_row
        credit_inflow = r.get("avg_monthly_credit_inflow")
        debit_outflow = r.get("avg_monthly_debit_outflow")
        avg_balance = r.get("average_balance")
        min_balance = r.get("minimum_balance")
        current_balance = r.get("current_balance")
        od_limit = r.get("current_od_limit")
        drawing_limit = r.get("drawing_limit")
        emi_debits = r.get("emi_like_recurring_debits")  # expected list[float] or pre-summed float
        statement_months = r.get("statement_months")  # accompanying pull metadata, see schema note
        bounce_count = r.get("bounce_return_count")
        od_occurrences = r.get("overdraft_occurrence_count")
        cash_deposit = r.get("cash_deposit_amount") or 0.0
        cash_withdrawal = r.get("cash_withdrawal_amount") or 0.0
        upi_inflow = r.get("upi_inflow") or 0.0
        upi_outflow = r.get("upi_outflow") or 0.0
        neft_inflow = r.get("neft_rtgs_imps_inflow") or 0.0
        neft_outflow = r.get("neft_rtgs_imps_outflow") or 0.0
        cash_flow_volatility = r.get("cash_flow_volatility")

        account_opening_date = r.get("account_opening_date")
        banking_vintage_months = None
        if account_opening_date is not None:
            delta_days = (datetime.utcnow().date() - account_opening_date).days
            banking_vintage_months = delta_days / 30.44

        account_status = r.get("account_status")  # expected list[str] across linked accounts, or single str
        num_accounts = r.get("number_of_accounts")
        active_account_ratio = None
        if isinstance(account_status, list) and num_accounts:
            active_count = sum(1 for s in account_status if s == "Active")
            active_account_ratio = _safe_div(active_count, num_accounts)

        net_monthly_cash_flow = (
            credit_inflow - debit_outflow if credit_inflow is not None and debit_outflow is not None else None
        )
        cash_flow_margin_ratio = (
            _safe_div(net_monthly_cash_flow, credit_inflow) if net_monthly_cash_flow is not None and credit_inflow else None
        )
        balance_stress_ratio = _safe_div(min_balance, avg_balance) if min_balance is not None and avg_balance is not None else None
        current_to_average_balance_ratio = (
            _safe_div(current_balance, avg_balance) if current_balance is not None and avg_balance is not None else None
        )
        od_utilization = _safe_div(od_limit, drawing_limit) if od_limit is not None and drawing_limit else None

        emi_total = sum(emi_debits) if isinstance(emi_debits, list) else emi_debits
        emi_to_inflow_ratio = _safe_div(emi_total, credit_inflow) if emi_total is not None and credit_inflow is not None else None

        bounce_rate = _safe_div(bounce_count, statement_months) if bounce_count is not None and statement_months else None
        overdraft_frequency = _safe_div(od_occurrences, statement_months) if od_occurrences is not None and statement_months else None
        bounce_severity_flag = (bounce_count >= 3) if bounce_count is not None else None  # configurable threshold, default shown

        total_activity = (credit_inflow or 0.0) + (debit_outflow or 0.0)
        cash_dependency_ratio = _safe_div(cash_deposit + cash_withdrawal, total_activity) if total_activity else None
        digital_payment_share = _safe_div(upi_inflow + upi_outflow, total_activity) if total_activity else None
        formal_channel_share = _safe_div(neft_inflow + neft_outflow, total_activity) if total_activity else None

        salary_detected = r.get("salary_credit_detected")
        business_detected = r.get("business_credit_detected")
        income_type_consistency_flag = None
        if salary_detected is not None and business_detected is not None:
            income_type_consistency_flag = (salary_detected != business_detected)

        return {
            "banking_vintage_months": banking_vintage_months,
            "active_account_ratio": active_account_ratio,
            "net_monthly_cash_flow": net_monthly_cash_flow,
            "cash_flow_margin_ratio": cash_flow_margin_ratio,
            "balance_stress_ratio": balance_stress_ratio,
            "current_to_average_balance_ratio": current_to_average_balance_ratio,
            "od_utilization": od_utilization,
            "emi_to_inflow_ratio": emi_to_inflow_ratio,
            "bounce_rate": bounce_rate,
            "overdraft_frequency": overdraft_frequency,
            "bounce_severity_flag": bounce_severity_flag,
            "cash_dependency_ratio": cash_dependency_ratio,
            "digital_payment_share": digital_payment_share,
            "formal_channel_share": formal_channel_share,
            "cash_flow_volatility_band": _band(cash_flow_volatility, CASH_FLOW_VOLATILITY_BANDS),
            "income_type_consistency_flag": income_type_consistency_flag,
            "banking_data_completeness": _non_null_fraction(
                r,
                [
                    "number_of_accounts", "account_type", "account_status", "account_opening_date",
                    "current_balance", "average_balance", "minimum_balance", "current_od_limit",
                    "drawing_limit", "avg_monthly_credit_inflow", "avg_monthly_debit_outflow",
                    "inflow_trend", "emi_like_recurring_debits", "salary_credit_detected",
                    "business_credit_detected", "cash_deposit_amount", "cash_withdrawal_amount",
                    "upi_inflow", "upi_outflow", "neft_rtgs_imps_inflow", "neft_rtgs_imps_outflow",
                    "cash_flow_volatility",
                ][:BANK_RAW_FIELD_COUNT],
            ),
        }

    # ------------------------------------------------------------------
    # itr / income — derived features (per-year structure, yr1/yr2)
    # ------------------------------------------------------------------
    def _compute_itr_features(self, itr_years: dict[str, dict] | None) -> dict:
        """
        all itr-section derived features per consolidated schema section 4.
        itr_years expected as {"yr1": {...raw fields...}, "yr2": {...}, ...},
        each value a dict of that year's raw itr fields (or None if that year absent).
        """
        empty_scalar = {
            "income_trend_itr": None, "income_trend_direction": None,
            "income_cagr_2yr": None, "income_cagr_nyr": None,
            "income_composition_shift_flag": None, "itr_filing_count": 0,
        }
        if not itr_years:
            return empty_scalar

        yr1 = itr_years.get("yr1")
        yr2 = itr_years.get("yr2")
        filing_count = sum(1 for y in itr_years.values() if y is not None)

        def composition(y: dict | None) -> dict:
            if y is None:
                return {"salary": None, "business": None, "investment": None}
            gross = y.get("gross_total_income")
            if not gross:
                return {"salary": None, "business": None, "investment": None}
            salary = _safe_div(y.get("salary_income") or 0.0, gross)
            business = _safe_div((y.get("business_income") or 0.0) + (y.get("professional_income") or 0.0), gross)
            investment = _safe_div(
                (y.get("interest_income") or 0.0) + (y.get("dividend_income") or 0.0) + (y.get("capital_gains") or 0.0),
                gross,
            )
            return {"salary": salary, "business": business, "investment": investment}

        comp_yr1 = composition(yr1)
        comp_yr2 = composition(yr2)

        income_trend_itr = None
        income_cagr_2yr = None
        income_trend_direction = None
        if yr1 is not None and yr2 is not None:
            income_yr1 = yr1.get("total_income")
            income_yr2 = yr2.get("total_income")
            if income_yr1 is not None and income_yr2 is not None:
                income_trend_itr = _safe_div(income_yr1 - income_yr2, income_yr2)
                income_cagr_2yr = _safe_div(income_yr1, income_yr2) - 1.0
                income_trend_direction = _band(income_trend_itr, INCOME_TREND_BANDS)

        income_composition_shift_flag = None
        if all(v is not None for v in comp_yr1.values()) and all(v is not None for v in comp_yr2.values()):
            shift_threshold = 0.20  # configurable — 20 percentage points, see schema doc
            income_composition_shift_flag = any(
                abs(comp_yr1[k] - comp_yr2[k]) > shift_threshold for k in comp_yr1
            )

        result = {
            "income_trend_itr": income_trend_itr,
            "income_trend_direction": income_trend_direction,
            "income_cagr_2yr": income_cagr_2yr,
            "income_cagr_nyr": None,  # requires yr3+, not computed in this pass
            "income_composition_shift_flag": income_composition_shift_flag,
            "itr_filing_count": filing_count,
        }

        for label, year_data, comp in (("yr1", yr1, comp_yr1), ("yr2", yr2, comp_yr2)):
            result[f"income_composition_ratio_salary_{label}"] = comp["salary"]
            result[f"income_composition_ratio_business_{label}"] = comp["business"]
            result[f"income_composition_ratio_investment_{label}"] = comp["investment"]
            if all(v is not None for v in comp.values()):
                result[f"income_diversification_flag_{label}"] = max(comp.values()) <= 0.90
            else:
                result[f"income_diversification_flag_{label}"] = None

            if year_data is not None:
                gross = year_data.get("gross_total_income")
                total = year_data.get("total_income")
                tax_paid = year_data.get("tax_paid")
                deductions = year_data.get("deductions")
                result[f"effective_tax_rate_{label}"] = _safe_div(tax_paid, total) if tax_paid is not None and total else None
                result[f"deduction_ratio_{label}"] = _safe_div(deductions, gross) if deductions is not None and gross else None
                result[f"gross_to_taxable_gap_{label}"] = (
                    gross - total if gross is not None and total is not None else None
                )
                result[f"itr_data_completeness_{label}"] = _non_null_fraction(
                    year_data,
                    [
                        "assessment_year", "itr_type", "gross_total_income", "total_income",
                        "salary_income", "business_income", "professional_income", "interest_income",
                        "dividend_income", "capital_gains", "other_income", "deductions", "tax_paid",
                    ][:ITR_PER_YEAR_FIELD_COUNT],
                )
            else:
                result[f"effective_tax_rate_{label}"] = None
                result[f"deduction_ratio_{label}"] = None
                result[f"gross_to_taxable_gap_{label}"] = None
                result[f"itr_data_completeness_{label}"] = 0.0

        return result

    # ------------------------------------------------------------------
    # orchestration
    # ------------------------------------------------------------------
    def compute_features(
        self,
        applicant_id: str,
        bureau_row: dict | None,
        bank_row: dict | None,
        itr_years: dict[str, dict] | None,
        skip_cache: bool = False,
    ) -> EngineeredApplicantFeatureVector | dict:
        """
        full feature vector computation for a single applicant.
        merges bureau, bank statement, and itr derived dicts into one flat vector.
        gst / e-way bill / e-invoice / alternate-data sections are conditional
        (business-owner applicants only) and are intentionally NOT computed here —
        see module docstring. cross-source features (obligation_discrepancy etc.)
        are also intentionally excluded — they require the assembled normalized
        applicant profile from all sources at once and belong to a later pipeline
        stage per this project's own documented deferral.
        """
        print(f"computing features for applicant {applicant_id}")

        bureau_features = self._compute_bureau_features(bureau_row)
        bank_features = self._compute_bank_statement_features(bank_row)
        itr_features = self._compute_itr_features(itr_years)

        all_features: dict = {
            "applicant_id": applicant_id,
            "computed_at": datetime.utcnow(),
            **bureau_features,
            **bank_features,
            **itr_features,
        }

        if not skip_cache:
            vector = EngineeredApplicantFeatureVector(**all_features)
            cache_row = {k: [v] for k, v in all_features.items()}
            feature_df = pl.DataFrame(cache_row, strict=False)
            self._save_cached_features(applicant_id, feature_df)
            return vector

        return all_features

    def compute_batch(
        self,
        bureau_df: pl.DataFrame,
        bank_df: pl.DataFrame,
        itr_df: pl.DataFrame,
    ) -> list[EngineeredApplicantFeatureVector]:
        """
        batch feature computation over unique applicant_ids found across the three
        input frames (union, not intersection — an applicant missing one source
        entirely still gets a feature vector with that source's fields null,
        consistent with the null-safe design of the compute_* methods above).

        bureau_df, bank_df: one row per applicant_id (applicant-level, not per-transaction).
        itr_df: one row per (applicant_id, year_label) pair, expected columns include
        'applicant_id' and 'year_label' ('yr1'/'yr2'/...) alongside the raw itr fields.
        """
        applicant_ids: set[str] = set()
        for df in (bureau_df, bank_df, itr_df):
            if df.height > 0 and "applicant_id" in df.columns:
                applicant_ids.update(df["applicant_id"].unique().to_list())

        total = len(applicant_ids)
        print(f"starting batch feature computation for {total} applicants")

        results: list[EngineeredApplicantFeatureVector] = []

        for idx, applicant_id in enumerate(sorted(applicant_ids)):
            if self._check_memory_pressure():
                print("memory pressure near spill threshold — proceeding with caution")

            if idx > 0 and idx % 50 == 0:
                print(f"processed {idx} of {total} applicants")

            bureau_row = self._first_row_as_dict(bureau_df, applicant_id)
            bank_row = self._first_row_as_dict(bank_df, applicant_id)
            itr_years = self._itr_years_as_dict(itr_df, applicant_id)

            vector = self.compute_features(applicant_id, bureau_row, bank_row, itr_years, skip_cache=False)
            results.append(vector)

        print(f"batch complete {total} feature vectors computed and cached")
        return results

    @staticmethod
    def _first_row_as_dict(df: pl.DataFrame, applicant_id: str) -> dict | None:
        if df.height == 0 or "applicant_id" not in df.columns:
            return None
        matched = df.filter(pl.col("applicant_id") == applicant_id)
        if matched.height == 0:
            return None
        return matched.to_dicts()[0]

    @staticmethod
    def _itr_years_as_dict(itr_df: pl.DataFrame, applicant_id: str) -> dict[str, dict] | None:
        if itr_df.height == 0 or "applicant_id" not in itr_df.columns:
            return None
        matched = itr_df.filter(pl.col("applicant_id") == applicant_id)
        if matched.height == 0:
            return None
        years: dict[str, dict] = {}
        for row in matched.to_dicts():
            label = row.get("year_label")
            if label:
                years[label] = row
        return years or None


def _run_feature_pipeline(raw_dir: str = "data/raw", features_dir: str = "data/features") -> None:
    """
    standalone entry point — batch feature computation over all raw parquets.
    expects three input files (schema per consolidated schema doc):
      bureau.parquet   — one row per applicant_id
      bank_statement.parquet — one row per applicant_id
      itr.parquet      — one row per (applicant_id, year_label)
    """
    raw = Path(raw_dir)
    bureau_files = sorted(glob.glob(str(raw / "bureau_chunk_*.parquet")))
    bank_files = sorted(glob.glob(str(raw / "bank_statement_chunk_*.parquet")))
    itr_files = sorted(glob.glob(str(raw / "itr_chunk_*.parquet")))

    print(f"bureau chunks {len(bureau_files)} bank chunks {len(bank_files)} itr chunks {len(itr_files)}")

    if not bureau_files:
        print("no raw bureau parquets found — run the applicant data ingestion step first")
        return

    bureau_df = pl.read_parquet(bureau_files) if bureau_files else pl.DataFrame()
    bank_df = pl.read_parquet(bank_files) if bank_files else pl.DataFrame()
    itr_df = pl.read_parquet(itr_files) if itr_files else pl.DataFrame()

    print(f"loaded bureau {bureau_df.height} bank {bank_df.height} itr {itr_df.height} rows")

    engine = FeatureEngine(cache_dir=features_dir)
    engine.compute_batch(bureau_df, bank_df, itr_df)
    print("feature pipeline complete")


if __name__ == "__main__":
    _run_feature_pipeline()
