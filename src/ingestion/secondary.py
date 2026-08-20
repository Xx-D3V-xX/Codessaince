"""
secondary.py — ITR per-year row assembly, plus assets/CAS, alt-data, and GST
generators. per CLAUDE.md §3.9 / the task brief, GST/alt-data are explicitly
the first cluster to cut under time pressure — kept deliberately simple here:
a handful of rule-based fields seeded off the applicant's own master/bureau/
bank fields, no CopulaGAN, no invoice-level or E-Way-Bill machinery.
"""

from __future__ import annotations

import random
from datetime import timedelta

from src.ingestion.priors import (
    ALT_UTILITY_ONTIME_BUCKETS,
    GST_FILING_CONSISTENCY_BUCKETS,
    INVESTMENT_ASSETS_BUCKETS,
    MSME_CONSTITUTION_DIST,
    MSME_SECTOR_DIST,
    REFERENCE_DATE,
    clip,
    generate_gstin,
    sample_bucket,
    weighted_label,
)

# ── ITR per-year rows ─────────────────────────────────────────────────────

_ASSESSMENT_YEAR_BY_LABEL = {"yr1": "2025-26", "yr2": "2024-25"}


def _income_components(applicant_type: str, gross_total_income: float, total_income: float) -> dict:
    """split gross_total_income into components that sum back to it (modulo
    the deductions gap already captured by total_income) — components vary
    by applicant_type since a salaried applicant has near-zero business_income
    and vice versa, consistent with income_composition_shift_flag's intent."""
    if applicant_type == "SALARIED":
        salary_share = random.uniform(0.85, 0.97)
    elif applicant_type == "SELF_EMPLOYED":
        salary_share = random.uniform(0.0, 0.10)
    else:  # MSME / CORPORATE
        salary_share = random.uniform(0.0, 0.05)

    salary_income = round(gross_total_income * salary_share, 2)
    remaining = max(gross_total_income - salary_income, 0.0)

    if applicant_type in ("SALARIED",):
        business_share, professional_share = 0.0, 0.0
    elif applicant_type == "SELF_EMPLOYED":
        business_share = random.uniform(0.3, 0.6)
        professional_share = random.uniform(0.2, 0.5)
    else:
        business_share = random.uniform(0.7, 0.9)
        professional_share = random.uniform(0.0, 0.05)

    # business/professional get a bounded share of `remaining`; whatever's
    # left over (biz_prof_occupied < 1.0 by construction) falls through to
    # the investment-income split below — allocating as a single normalized
    # partition of `remaining`, not two independently-random draws, is what
    # keeps components guaranteed to sum back to gross_total_income exactly.
    norm = business_share + professional_share
    if norm > 0:
        biz_prof_occupied = min(norm, 1.0) * random.uniform(0.7, 0.95)
        business_income = round(remaining * biz_prof_occupied * (business_share / norm), 2)
        professional_income = round(remaining * biz_prof_occupied * (professional_share / norm), 2)
    else:
        business_income, professional_income = 0.0, 0.0

    remaining_after_biz = max(remaining - business_income - professional_income, 0.0)
    interest_share = random.uniform(0.3, 0.6)
    dividend_share = random.uniform(0.0, 0.2)
    capgains_share = random.uniform(0.0, 0.3)
    invest_norm = max(interest_share + dividend_share + capgains_share, 1e-9)
    invest_occupied = min(invest_norm, 1.0)
    interest_income = round(remaining_after_biz * invest_occupied * (interest_share / invest_norm), 2)
    dividend_income = round(remaining_after_biz * invest_occupied * (dividend_share / invest_norm), 2)
    capital_gains = round(remaining_after_biz * invest_occupied * (capgains_share / invest_norm), 2)
    other_income = round(
        max(remaining_after_biz - interest_income - dividend_income - capital_gains, 0.0), 2
    )

    deductions = round(max(gross_total_income - total_income, 0.0), 2)
    tax_paid = round(max(total_income * random.uniform(0.02, 0.22), 0.0), 2) if total_income > 0 else 0.0

    return {
        "salary_income": salary_income,
        "business_income": business_income,
        "professional_income": professional_income,
        "interest_income": interest_income,
        "dividend_income": dividend_income,
        "capital_gains": capital_gains,
        "other_income": other_income,
        "deductions": deductions,
        "tax_paid": tax_paid,
    }


def build_itr_rows(profiles: list[dict]) -> list[dict]:
    """two rows per applicant (yr1, yr2), every field per-year not just income,
    matching ITRRecord field names exactly."""
    rows: list[dict] = []
    for p in profiles:
        applicant_id = p["applicant_id"]
        applicant_type = p["applicant_type"]
        itr_type = p.get("itr_type")

        for label in ("yr1", "yr2"):
            gross = float(p.get(f"gross_total_income_{label}") or 0.0)
            total = float(p.get(f"total_income_{label}") or 0.0)
            components = _income_components(applicant_type, gross, total)
            rows.append({
                "applicant_id": applicant_id,
                "year_label": label,
                "assessment_year": _ASSESSMENT_YEAR_BY_LABEL[label],
                "itr_type": itr_type,
                "gross_total_income": round(gross, 2),
                "total_income": round(total, 2),
                **components,
            })
    return rows


# ── assets / CAS-style investment data (optional, all types) ───────────────

def build_asset_rows(profiles: list[dict]) -> list[dict]:
    """one row per applicant, optional per CLAUDE.md §4.1 item 5 — a
    meaningful fraction of applicants have zero/no declared assets, matching
    INVESTMENT_ASSETS_BUCKETS' NONE weight. no formal Pydantic schema exists
    yet for this source, so this is a flat, self-consistent structure:
    mutual_fund_value + equity_value + other_securities_value +
    liquid_asset_value sums to total_portfolio_value by construction."""
    rows: list[dict] = []
    for p in profiles:
        _, investable = sample_bucket(INVESTMENT_ASSETS_BUCKETS)
        has_assets = investable > 0

        if not has_assets:
            rows.append({
                "applicant_id": p["applicant_id"],
                "has_declared_assets": False,
                "mutual_fund_value": None,
                "equity_value": None,
                "other_securities_value": None,
                "liquid_asset_value": None,
                "total_portfolio_value": None,
                "recent_redemption_value": None,
                "recent_purchase_value": None,
                "as_of_date": None,
            })
            continue

        mf_share = random.uniform(0.2, 0.55)
        eq_share = random.uniform(0.0, 0.35)
        other_share = random.uniform(0.0, 0.15)
        liquid_share = max(0.0, 1 - mf_share - eq_share - other_share)

        mutual_fund_value = round(investable * mf_share, 2)
        equity_value = round(investable * eq_share, 2)
        other_securities_value = round(investable * other_share, 2)
        liquid_asset_value = round(investable * liquid_share, 2)
        total_portfolio_value = round(
            mutual_fund_value + equity_value + other_securities_value + liquid_asset_value, 2
        )

        rows.append({
            "applicant_id": p["applicant_id"],
            "has_declared_assets": True,
            "mutual_fund_value": mutual_fund_value,
            "equity_value": equity_value,
            "other_securities_value": other_securities_value,
            "liquid_asset_value": liquid_asset_value,
            "total_portfolio_value": total_portfolio_value,
            "recent_redemption_value": round(total_portfolio_value * random.uniform(0.0, 0.1), 2)
            if random.random() < 0.3 else 0.0,
            "recent_purchase_value": round(total_portfolio_value * random.uniform(0.0, 0.15), 2)
            if random.random() < 0.4 else 0.0,
            "as_of_date": REFERENCE_DATE,
        })
    return rows


# ── alt-data (optional, all types) ──────────────────────────────────────────

def build_alt_data_rows(profiles: list[dict]) -> list[dict]:
    """digital payments / utility payments / employment-stability indicators,
    optional per CLAUDE.md §4.1 item 6. deliberately shallow — not every
    applicant has alt-data coverage (utility bill reporting isn't universal),
    matching the applicability-matrix rule that optional-absent just reduces
    completeness rather than blocking evaluation."""
    rows: list[dict] = []
    for p in profiles:
        applicant_type = p["applicant_type"]
        has_alt_data = random.random() < 0.65  # not every applicant has alt-data coverage

        if not has_alt_data:
            rows.append({
                "applicant_id": p["applicant_id"],
                "utility_payment_ontime_pct": None,
                "utility_payment_months_observed": None,
                "digital_payment_txn_count_monthly": None,
                "digital_payment_volume_monthly": None,
                "employment_stability_flag": None,
                "business_stability_flag": None,
            })
            continue

        _, ontime_pct = sample_bucket(ALT_UTILITY_ONTIME_BUCKETS)
        months_observed = random.randint(3, 24)
        avg_credit_inflow = float(p.get("avg_monthly_credit_inflow") or p.get("declared_income_monthly") or 20_000)

        rows.append({
            "applicant_id": p["applicant_id"],
            "utility_payment_ontime_pct": round(clip(ontime_pct, 0, 100), 2),
            "utility_payment_months_observed": months_observed,
            "digital_payment_txn_count_monthly": int(round(random.uniform(5, 60))),
            "digital_payment_volume_monthly": round(avg_credit_inflow * random.uniform(0.1, 0.5), 2),
            "employment_stability_flag": (
                bool((p.get("employment_vintage_months") or 0) >= 12)
                if applicant_type == "SALARIED" else None
            ),
            "business_stability_flag": (
                bool((p.get("business_vintage_months") or 0) >= 24)
                if applicant_type in ("SELF_EMPLOYED", "MSME", "CORPORATE") else None
            ),
        })
    return rows


# ── GST / business tax summary (conditional, MSME/self-employed/business) ──

def build_gst_rows(profiles: list[dict]) -> list[dict]:
    """MSME/self-employed/business-owner applicants only — N/A for salaried.
    kept deliberately minimal per CLAUDE.md §3.9: registration vintage,
    turnover, and filing consistency only, NOT the old generator's full
    GST-invoice / E-Way-Bill / HSN-sector machinery (explicitly flagged as
    out-of-core-scope). only one row per business-eligible applicant."""
    rows: list[dict] = []
    for p in profiles:
        applicant_type = p["applicant_type"]
        if applicant_type == "CORPORATE":
            is_gst_eligible = True
        elif applicant_type == "MSME":
            is_gst_eligible = True
        elif applicant_type == "SELF_EMPLOYED":
            is_gst_eligible = random.random() < 0.55  # conditional-optional per CLAUDE.md §4.1 item 7
        else:
            continue  # salaried — N/A, no row at all (not a missing row, a non-applicable one)

        if not is_gst_eligible:
            continue

        state_code = p.get("state_code") or 27
        business_vintage_months = int(p.get("business_vintage_months") or random.randint(6, 240))
        registration_date = REFERENCE_DATE - timedelta(days=business_vintage_months * 30)

        constitution = (
            "PROPRIETORSHIP" if applicant_type == "SELF_EMPLOYED"
            else weighted_label(MSME_CONSTITUTION_DIST)
        )
        sector = weighted_label(MSME_SECTOR_DIST)

        declared_income_annual = float(p.get("declared_income_monthly") or 20_000) * 12
        if applicant_type in ("MSME", "CORPORATE"):
            turnover = declared_income_annual * random.uniform(15, 40)
        else:
            turnover = declared_income_annual * random.uniform(1.1, 2.0)

        _, filing_consistency = sample_bucket(GST_FILING_CONSISTENCY_BUCKETS)

        rows.append({
            "applicant_id": p["applicant_id"],
            "gstin": generate_gstin(int(state_code)),
            "business_constitution": constitution,
            "business_sector": sector,
            "gst_registration_date": registration_date,
            "turnover_annual": round(turnover, 2),
            "taxable_turnover_annual": round(turnover * random.uniform(0.85, 0.99), 2),
            "gst_filing_consistency_pct": round(clip(filing_consistency, 0, 100), 2),
            "turnover_trend": random.choices(["GROWING", "FLAT", "DECLINING"], weights=[0.4, 0.4, 0.2])[0],
        })
    return rows
