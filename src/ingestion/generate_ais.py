"""
generate_ais.py — Generate AIS (Annual Information Statement) records.

Reads applicant_profiles.parquet and emits ais_chunk_*.parquet in data/raw/.
2 financial years per applicant (FY 2024-25 and FY 2023-24), consistent with
the existing income_fy1 / income_fy2 and ais_itr_consistency fields.

Schema: Section 12.5 AnnualInformationStatement
Fields: pan, financialYear, salaryReported, interestReported, dividendReported,
        capitalGainsReported, tdsAmount, tcsAmount, sftTransactionCount,
        otherReportedIncome, valueReportedBySource, valueProcessedBySystem,
        valueAcceptedOrConfirmed

Run:
    python -m src.ingestion.generate_ais
    -- or --
    python src/ingestion/generate_ais.py
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

# ── config ────────────────────────────────────────────────────────────────────
DATA_PATH  = Path("data/raw")
CHUNK_SIZE = 10_000
SEED       = 42

# Map FY label → which income_fy column to use from profiles
FY_SPEC = [
    ("2024-25", "income_fy1"),
    ("2023-24", "income_fy2"),
]

# ── load ──────────────────────────────────────────────────────────────────────
print("▶ Loading applicant_profiles.parquet …")
profiles = pd.read_parquet(DATA_PATH / "applicant_profiles.parquet")
n = len(profiles)
print(f"  {n:,} profiles loaded")

rng = np.random.default_rng(SEED)

# Pre-extract arrays used across both FYs
atype        = profiles["applicant_type"].to_numpy()
ais_consist  = profiles["ais_itr_consistency"].fillna("HIGH").to_numpy()
equity_val   = profiles["equity_value"].fillna(0).to_numpy(dtype=float)
mf_val       = profiles["mutual_fund_value"].fillna(0).to_numpy(dtype=float)
portfolio_val = profiles["total_portfolio_value"].fillna(0).to_numpy(dtype=float)
pan_arr      = profiles["pan"].to_numpy()
app_id_arr   = profiles["applicant_id"].to_numpy()

has_equity   = (equity_val + mf_val) > 0
salaried_m   = atype == "SALARIED"

# Consistency scale: how much AIS-reported values deviate from ITR-declared income
#   HIGH            → scale ≈ 1.0  (perfect match)
#   MINOR_DISC      → ±5–7%
#   MODERATE_DISC   → ±15–20%
#   MAJOR_DISC      → ±30–40% swing
_CONSIST_PARAMS = {
    "HIGH":                  (1.000, 0.01),
    "MINOR_DISCREPANCY":     (0.995, 0.04),
    "MODERATE_DISCREPANCY":  (0.985, 0.12),
    "MAJOR_DISCREPANCY":     (0.960, 0.22),
}

def _consist_scale(ais_arr: np.ndarray) -> np.ndarray:
    scale = np.ones(n)
    for label, (mu, sigma) in _CONSIST_PARAMS.items():
        mask = ais_arr == label
        if mask.any():
            scale[mask] = rng.normal(mu, sigma, mask.sum())
    return np.clip(scale, 0.50, 1.50)

all_chunks: list[pd.DataFrame] = []

for fy_label, income_col in FY_SPEC:
    print(f"\n▶ Generating AIS for FY {fy_label} (source: {income_col}) …")

    income = profiles[income_col].fillna(0).to_numpy(dtype=float)
    scale  = _consist_scale(ais_consist)

    # ── salary reported (Form 16 / 26AS from employer) ────────────────────────
    salary_reported = np.where(
        salaried_m,
        np.round(income * scale, 2),
        0.0
    )

    # ── interest reported (bank TDS returns / Form 26AS) ─────────────────────
    int_base = np.maximum(income * rng.uniform(0.01, 0.04, n), 500)
    interest_reported = np.round(int_base * scale, 2)

    # ── dividend reported (SFT from RTA / depositories) ──────────────────────
    div_base = np.where(has_equity, income * rng.uniform(0.005, 0.015, n), 0.0)
    dividend_reported = np.round(div_base * scale * has_equity, 2)

    # ── capital gains reported (SFT from broker / MF) ────────────────────────
    cg_trigger = (portfolio_val > 0) & (rng.random(n) < 0.30)
    cg_base    = np.maximum(portfolio_val * rng.uniform(0.02, 0.08, n), 1_000)
    capital_gains_reported = np.round(
        np.where(cg_trigger, cg_base * np.abs(scale), 0.0), 2
    )

    # ── TDS amount ────────────────────────────────────────────────────────────
    tds_on_salary   = np.where(salaried_m, np.round(salary_reported   * rng.uniform(0.05, 0.20, n), 2), 0.0)
    tds_on_interest = np.round(interest_reported * 0.10, 2)
    tds_on_div      = np.round(dividend_reported  * 0.10, 2)
    tds_amount      = np.round(tds_on_salary + tds_on_interest + tds_on_div, 2)

    # ── TCS amount (forex, high-value purchases) ──────────────────────────────
    tcs_prob   = rng.random(n)
    tcs_amount = np.where(
        (income > 1_000_000) & (tcs_prob < 0.12),
        np.round(rng.exponential(5_000, n), 2),
        0.0
    )

    # ── SFT transaction count (high-value items banks/brokers report) ─────────
    sft_count = np.where(
        portfolio_val > 500_000, rng.integers(2, 15, n),
        np.where(income > 600_000, rng.integers(0, 6, n), 0)
    ).astype(int)

    # ── other reported income (rent, commission, professional receipts) ───────
    other_prob     = rng.random(n)
    other_reported = np.where(
        other_prob < 0.25,
        np.round(rng.exponential(np.maximum(income * 0.02, 2_000), n), 2),
        0.0
    )

    # ── aggregate value fields ────────────────────────────────────────────────
    value_reported  = np.round(
        salary_reported + interest_reported + dividend_reported
        + capital_gains_reported + other_reported, 2
    )
    value_processed = np.round(value_reported * rng.uniform(0.98, 1.00, n), 2)

    # Accepted ← taxpayer confirmation rate depends on AIS consistency
    accept_base = np.where(
        ais_consist == "HIGH",             rng.uniform(0.97, 1.00, n),
        np.where(
        ais_consist == "MINOR_DISCREPANCY", rng.uniform(0.90, 0.98, n),
                                            rng.uniform(0.72, 0.93, n)
        )
    )
    value_accepted = np.round(value_processed * accept_base, 2)

    chunk_df = pd.DataFrame({
        "applicant_id":               app_id_arr,
        "pan":                        pan_arr,
        "financial_year":             fy_label,
        "salary_reported":            salary_reported,
        "interest_reported":          interest_reported,
        "dividend_reported":          dividend_reported,
        "capital_gains_reported":     capital_gains_reported,
        "tds_amount":                 tds_amount,
        "tcs_amount":                 tcs_amount,
        "sft_transaction_count":      sft_count,
        "other_reported_income":      other_reported,
        "value_reported_by_source":   value_reported,
        "value_processed_by_system":  value_processed,
        "value_accepted_or_confirmed": value_accepted,
        "ais_itr_consistency":        ais_consist,
        "synthetic_batch_id":         "batch_fix_001",
    })
    all_chunks.append(chunk_df)
    print(f"  ✓ {n:,} AIS records for FY {fy_label}")

# ── concat + write chunks ─────────────────────────────────────────────────────
ais_df   = pd.concat(all_chunks, ignore_index=True)
total    = len(ais_df)
n_chunks = math.ceil(total / CHUNK_SIZE)

print(f"\n▶ Writing {total:,} AIS rows → {n_chunks} chunk files …")
for i in range(n_chunks):
    chunk = ais_df.iloc[i * CHUNK_SIZE : (i + 1) * CHUNK_SIZE]
    out   = DATA_PATH / f"ais_chunk_{i:04d}.parquet"
    chunk.to_parquet(out, index=False)

print(f"✅ AIS data written: {n_chunks} files × up to {CHUNK_SIZE:,} rows each")
print(f"   Columns: {list(ais_df.columns)}")
