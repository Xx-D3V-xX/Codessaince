"""
fix_profiles.py â€” Patch applicant_profiles.parquet with missing schema fields.

Adds (in-place, no re-generation):
  ITR  : itr_assessment_year_fy1/fy2, itr_salary_income, itr_business_income,
         itr_professional_income, itr_interest_income, itr_dividend_income,
         itr_capital_gains, itr_other_income, itr_deductions, itr_tax_paid
  CAS  : cas_bond_value, cas_etf_value, cas_other_securities_value,
         cas_recent_redemption_value, cas_recent_purchase_value
  GST  : gst_registration_date, gst_taxpayer_type, gst_tax_liability,
         gst_tax_paid, gst_gstr1_filing_consistency_pct,
         gst_gstr3b_filing_consistency_pct

Run:
    python -m src.ingestion.fix_profiles
    -- or --
    python src/ingestion/fix_profiles.py
"""

from __future__ import annotations

import math
import shutil
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# â”€â”€ config â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
REFERENCE_DATE = date(2026, 4, 11)
DATA_PATH = Path("data/raw")
PROFILES_PATH = DATA_PATH / "applicant_profiles.parquet"
BACKUP_PATH = PROFILES_PATH.with_suffix(".parquet.bak")
SEED = 42

# â”€â”€ load â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
print("â–¶ Loading applicant_profiles.parquet â€¦")
df = pd.read_parquet(PROFILES_PATH)
n = len(df)
print(f"  {n:,} rows Ã— {len(df.columns)} columns loaded")

# Backup (skip if already backed up from a previous run)
if not BACKUP_PATH.exists():
    shutil.copy2(PROFILES_PATH, BACKUP_PATH)
    print(f"  Backup â†’ {BACKUP_PATH}")

rng = np.random.default_rng(SEED)

# â”€â”€ helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _clip(arr: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return np.clip(arr, lo, hi)


def _round(arr: np.ndarray) -> np.ndarray:
    return np.round(arr, 2)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# 1.  ITR fields
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
print("\nâ–¶ [ITR] Adding assessment years and income breakdown â€¦")

# REFERENCE_DATE = 2026-04-11  â†’  we are in FY 2026-27
# Most-recent filed ITR = AY 2025-26 (FY 2024-25)  â†’ income_fy1
# Prior year ITR        = AY 2024-25 (FY 2023-24)  â†’ income_fy2
df["itr_assessment_year_fy1"] = "2025-26"
df["itr_assessment_year_fy2"] = "2024-25"

gti   = df["gross_total_income"].fillna(0).to_numpy(dtype=float)
atype = df["applicant_type"].to_numpy()

salaried_m = atype == "SALARIED"
se_m       = atype == "SELF_EMPLOYED"
biz_m      = np.isin(atype, ["MSME", "CORPORATE"])

# â”€â”€ Proportional split that GUARANTEES sum == gross_total_income â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#
# Strategy (all working in fractions of GTI, then scale back):
#   Step 1: assign random fractions to secondary heads, then cap so they
#           never collectively exceed MAX_SECONDARY_FRAC (30% of GTI).
#   Step 2: primary head = GTI - sum(secondaries)  [exact by construction].
#   Step 3: absorb any Â+/-1 rupee rounding error into other_income.
#
# This ensures:  primary + interest + dividend + capital_gains + other == GTI
# with a max abs error of â‰¤ n_rows Ã— 0.005 rupees (well within tolerance).

MAX_SECONDARY_FRAC = 0.30   # secondaries collectively â‰¤ 30% of GTI

has_equity = (
    df["equity_value"].fillna(0).to_numpy(dtype=float)
    + df["mutual_fund_value"].fillna(0).to_numpy(dtype=float)
) > 0
total_pv = df["total_portfolio_value"].fillna(0).to_numpy(dtype=float)

# Raw fractions (uncapped)
frac_interest  = rng.uniform(0.005, 0.06, n)            # 0.5â€“6 %
frac_dividend  = np.where(has_equity, rng.uniform(0.002, 0.015, n), 0.0)  # only investors
frac_cg        = np.where(
    (total_pv > 0) & (rng.random(n) < 0.35),
    rng.uniform(0.005, 0.08, n), 0.0
)                                                         # ~35% of investors
frac_other     = rng.uniform(0.001, 0.025, n)

frac_secondary_raw = frac_interest + frac_dividend + frac_cg + frac_other

# Scale down if raw secondaries would exceed MAX_SECONDARY_FRAC
overflow = frac_secondary_raw > MAX_SECONDARY_FRAC
scale_down = np.where(overflow, MAX_SECONDARY_FRAC / frac_secondary_raw, 1.0)
frac_interest = frac_interest * scale_down
frac_dividend = frac_dividend * scale_down
frac_cg       = frac_cg       * scale_down
frac_other    = frac_other    * scale_down

# Convert fractions â†’ rupee amounts (rounded to nearest rupee)
interest_income = np.round(gti * frac_interest, 0)
dividend_income = np.round(gti * frac_dividend, 0)
capital_gains   = np.round(gti * frac_cg,       0)
other_income    = np.round(gti * frac_other,     0)

secondary_sum = interest_income + dividend_income + capital_gains + other_income

# Primary income = exact residual (GTI - secondaries), no rounding yet
primary_exact = gti - secondary_sum          # guaranteed â‰¥ 70% of GTI

# Rounding error correction: after rounding secondaries, primary_exact
# may not be a whole rupee â†’ absorb into other_income
primary_floored = np.floor(primary_exact)    # truncate toward zero
rounding_carry  = primary_exact - primary_floored   # always in [0, 1)
other_income    = other_income + np.round(rounding_carry, 0)  # push rounding into other_income
primary_exact   = np.floor(gti - (interest_income + dividend_income + capital_gains + other_income))

# Assign primary to the correct income head
salary_income       = np.zeros(n)
professional_income = np.zeros(n)
business_income     = np.zeros(n)
salary_income[salaried_m]       = primary_exact[salaried_m]
professional_income[se_m]       = primary_exact[se_m]
business_income[biz_m]          = primary_exact[biz_m]
# Applicants with no type match (edge case) get it in other_income
no_type_m = ~(salaried_m | se_m | biz_m)
other_income[no_type_m] += primary_exact[no_type_m]

# â”€â”€ Verify: max absolute error should be 0 â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
check_sum = salary_income + professional_income + business_income \
            + interest_income + dividend_income + capital_gains + other_income
max_err = np.max(np.abs(check_sum - gti))
assert max_err < 1.0, f"ITR income split error: max |error| = {max_err:.4f} > 1 rupee!"
print(f"  âœ“ Income split verified: max |sum - GTI| = {max_err:.6f} rupees")

# â”€â”€ deductions (80C/80D/NPS/HRA) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Cap deductions at GTI to avoid negative taxable income
deduction_cap = np.minimum(
    np.where(salaried_m, 350_000.0, 200_000.0),
    gti  # never more than gross income itself
)
low_income_m  = gti < 300_000
deductions    = rng.uniform(50_000, 350_000, n)
deductions    = np.minimum(deductions, deduction_cap)
deductions[low_income_m] = rng.uniform(0, 50_000, low_income_m.sum())
deductions = np.round(deductions, 0)

# â”€â”€ tax paid (new-regime slabs + 4% cess) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
taxable_income = np.maximum(gti - deductions, 0)

def _compute_tax(ti: np.ndarray) -> np.ndarray:
    t  = np.maximum(0, np.minimum(ti, 700_000)   - 300_000) * 0.05
    t += np.maximum(0, np.minimum(ti, 1_000_000)  - 700_000) * 0.10
    t += np.maximum(0, np.minimum(ti, 1_200_000) - 1_000_000) * 0.15
    t += np.maximum(0, np.minimum(ti, 1_500_000) - 1_200_000) * 0.20
    t += np.maximum(0, ti - 1_500_000) * 0.30
    return np.round(t * 1.04, 0)   # 4% cess; round to nearest rupee

tax_liability  = _compute_tax(taxable_income)
tax_paid_ratio = rng.uniform(0.92, 1.05, n)
tax_paid       = np.round(tax_liability * tax_paid_ratio, 0)

df["itr_salary_income"]       = salary_income
df["itr_professional_income"] = professional_income
df["itr_business_income"]     = business_income
df["itr_interest_income"]     = interest_income
df["itr_dividend_income"]     = dividend_income
df["itr_capital_gains"]       = capital_gains
df["itr_other_income"]        = other_income
df["itr_deductions"]          = deductions
df["itr_tax_paid"]            = tax_paid
print(f"  ITR columns added: assessment years + 9 income/tax fields")

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# 2.  CAS / Investment asset fields
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
print("\nâ–¶ [CAS] Adding bond, ETF, other securities, redemption, purchase â€¦")

mf  = df["mutual_fund_value"].fillna(0).to_numpy(dtype=float)
eq  = df["equity_value"].fillna(0).to_numpy(dtype=float)
liq = df["liquid_asset_value"].fillna(0).to_numpy(dtype=float)

remaining = np.maximum(total_pv - mf - eq - liq, 0)

bond_frac = rng.uniform(0.00, 0.50, n)
etf_frac  = rng.uniform(0.00, 0.40, n)
bond_value    = _round(remaining * bond_frac)
etf_value     = _round(remaining * etf_frac * (1 - bond_frac))
other_sec     = _round(np.maximum(remaining - bond_value - etf_value, 0))

# Update total_portfolio_value to include new asset classes
df["total_portfolio_value"] = _round(mf + eq + liq + bond_value + etf_value + other_sec)

# Recent 3-month activity
active_investor = total_pv > 0
recent_redemption = _round(np.where(
    active_investor & (rng.random(n) < 0.30),
    _clip(rng.exponential(np.maximum(total_pv * 0.05, 5_000)), 0, total_pv * 0.20),
    0.0
))
recent_purchase = _round(np.where(
    active_investor & (rng.random(n) < 0.45),
    _clip(rng.exponential(np.maximum(total_pv * 0.06, 8_000)), 0, total_pv * 0.30),
    0.0
))

df["cas_bond_value"]               = bond_value
df["cas_etf_value"]                = etf_value
df["cas_other_securities_value"]   = other_sec
df["cas_recent_redemption_value"]  = recent_redemption
df["cas_recent_purchase_value"]    = recent_purchase
print(f"  CAS columns added: 5 fields (bond, ETF, other, redemption, purchase)")

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# 3.  GST / Business Tax fields
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
print("\nâ–¶ [GST] Adding registration_date, taxpayer_type, tax_liability, tax_paid, gstr1/3b â€¦")

has_gstin = df["gstin"].notna().to_numpy()

# Registration date â† back-date by business_vintage_months from reference
biz_vintage = df["business_vintage_months"].fillna(12).to_numpy(dtype=float)
reg_dates = pd.array([
    (REFERENCE_DATE - timedelta(days=int(biz_vintage[i] * 30))).isoformat()
    if has_gstin[i] else None
    for i in range(n)
], dtype=pd.StringDtype())
df["gst_registration_date"] = reg_dates

# Taxpayer type
tp_choices  = rng.choice([0, 1, 2], size=n, p=[0.72, 0.18, 0.10])
tp_labels   = np.array(["Regular", "Composite", "QRMP"])
df["gst_taxpayer_type"] = np.where(has_gstin, tp_labels[tp_choices], None)

# Tax liability â€” effective GST rate differs by taxpayer type
is_composite = df["gst_taxpayer_type"] == "Composite"
taxable_tv = df["taxable_turnover"].fillna(0).to_numpy(dtype=float)
gst_rate = np.where(
    is_composite.to_numpy(),
    rng.uniform(0.01, 0.06, n),    # composite: 1â€“6 %
    rng.uniform(0.10, 0.18, n)     # regular / QRMP: 10â€“18 %
)
gst_tax_liability = _round(taxable_tv * gst_rate * has_gstin)
df["gst_tax_liability"] = gst_tax_liability

# Tax paid â€” driven by filing consistency
filing_pct = df["gst_filing_consistency_percent"].fillna(90).to_numpy(dtype=float) / 100.0
payment_ratio = _clip(rng.normal(filing_pct, 0.05, n), 0.5, 1.02)
df["gst_tax_paid"] = _round(gst_tax_liability * payment_ratio * has_gstin)

# Separate GSTR-1 and GSTR-3B filing consistency
# Use np.nan for non-GST applicants so the column stays float64
filing_base = df["gst_filing_consistency_percent"].fillna(90).to_numpy(dtype=float)
gstr1_vals = np.where(has_gstin, np.round(_clip(filing_base + rng.uniform(-5, 5, n), 0, 100), 2), np.nan)
gstr3b_vals = np.where(has_gstin, np.round(_clip(filing_base + rng.uniform(-8, 3, n), 0, 100), 2), np.nan)
df["gst_gstr1_filing_consistency_pct"]  = gstr1_vals.astype(float)
df["gst_gstr3b_filing_consistency_pct"] = gstr3b_vals.astype(float)
print(f"  GST columns added: 6 fields")

# â”€â”€ write back â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
new_cols = [c for c in df.columns if c.startswith(("itr_", "cas_", "gst_"))]
print(f"\nâ–¶ Writing patched parquet ({n:,} rows, {len(df.columns)} columns) â€¦")
df.to_parquet(PROFILES_PATH, index=False)
print(f"âœ… applicant_profiles.parquet patched â€” {len(new_cols)} new columns added")
print(f"   {new_cols}")

