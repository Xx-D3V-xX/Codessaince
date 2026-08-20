"""
run_generation.py — orchestrator entry point for CreditGate's synthetic data
generation (Phase 0 rebuild). Produces separate per-source Parquet files under
data/raw/, chunked to match engine.py's _run_feature_pipeline glob pattern
(bureau_chunk_*.parquet, bank_statement_chunk_*.parquet, itr_chunk_*.parquet).

run:
    python -m src.ingestion.run_generation
    python -m src.ingestion.run_generation --n-profiles 8000 --force
"""

from __future__ import annotations

import argparse
import logging
import math
import random
import shutil
from pathlib import Path

import numpy as np
import polars as pl
from faker import Faker

from src.ingestion.dpd_history import generate_dpd_history
from src.ingestion.priors import REFERENCE_DATE
from src.ingestion.secondary import build_alt_data_rows, build_asset_rows, build_gst_rows, build_itr_rows
from src.ingestion.synth import build_profiles_copulagan

logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("creditgate.run_generation")

SEED = 42
N_PROFILES = 8_000
N_SEED_ROWS = 900
CHUNK_SIZE = 2_000
RAW_DATA_PATH = Path("data/raw")

# fields carried on `profiles` purely to build other tables (master-data
# helper columns, ITR intermediate yr1/yr2 scalars, the dpd_history seed
# label) — excluded from the master_data parquet itself so that table only
# has genuine master/applicant fields, not leftover plumbing.
_MASTER_EXCLUDE_FIELDS = {
    # bureau fields (BureauRecord's own contract, live in bureau parquet)
    "bureau_score", "active_loan_count", "closed_loan_count", "total_sanctioned_amount",
    "total_outstanding_amount", "secured_loan_count", "unsecured_loan_count",
    "recent_enquiry_count_30d", "recent_enquiry_count_90d", "recent_enquiry_count_180d",
    "overdue_amount", "max_dpd", "max_dpd_label", "dpd_recency_months", "write_off_flag",
    "write_off_amount", "settlement_flag", "settlement_amount", "default_flag",
    "suit_filed_flag", "credit_card_utilization",
    # bank fields
    "number_of_accounts", "account_type", "account_status", "account_opening_date",
    "account_opening_vintage_months", "current_balance", "average_balance", "minimum_balance",
    "current_od_limit", "drawing_limit", "avg_monthly_credit_inflow", "avg_monthly_debit_outflow",
    "inflow_trend", "emi_like_recurring_debits", "salary_credit_detected", "business_credit_detected",
    "cash_deposit_amount", "cash_withdrawal_amount", "upi_inflow", "upi_outflow",
    "neft_rtgs_imps_inflow", "neft_rtgs_imps_outflow", "bounce_return_count",
    "overdraft_occurrence_count", "cash_flow_volatility", "statement_months",
    # itr intermediate scalars
    "itr_type", "gross_total_income_yr1", "total_income_yr1", "gross_total_income_yr2",
    "total_income_yr2", "income_verification_ratio",
    # internal bookkeeping
    "is_business", "is_business_or_se",
}

_BUREAU_FIELDS = [
    "applicant_id", "bureau_score", "active_loan_count", "closed_loan_count",
    "total_sanctioned_amount", "total_outstanding_amount", "secured_loan_count",
    "unsecured_loan_count", "recent_enquiry_count_30d", "recent_enquiry_count_90d",
    "recent_enquiry_count_180d", "overdue_amount", "max_dpd", "dpd_recency_months",
    "write_off_flag", "write_off_amount", "settlement_flag", "settlement_amount",
    "default_flag", "suit_filed_flag", "credit_card_utilization",
]

_BANK_FIELDS = [
    "applicant_id", "number_of_accounts", "account_type", "account_status",
    "account_opening_date", "current_balance", "average_balance", "minimum_balance",
    "current_od_limit", "drawing_limit", "avg_monthly_credit_inflow", "avg_monthly_debit_outflow",
    "inflow_trend", "emi_like_recurring_debits", "salary_credit_detected", "business_credit_detected",
    "cash_deposit_amount", "cash_withdrawal_amount", "upi_inflow", "upi_outflow",
    "neft_rtgs_imps_inflow", "neft_rtgs_imps_outflow", "bounce_return_count",
    "overdraft_occurrence_count", "cash_flow_volatility", "statement_months",
]


def _write_chunks(rows: list[dict], prefix: str, chunk_size: int = CHUNK_SIZE) -> int:
    RAW_DATA_PATH.mkdir(parents=True, exist_ok=True)
    if not rows:
        logger.warning("%s: 0 rows — skipping", prefix)
        return 0
    df = pl.DataFrame(rows, strict=False)
    n_chunks = math.ceil(len(df) / chunk_size)
    for i in range(n_chunks):
        chunk = df.slice(i * chunk_size, chunk_size)
        chunk.write_parquet(RAW_DATA_PATH / f"{prefix}_chunk_{i:04d}.parquet")
    logger.info("%s: %d rows -> %d chunk(s)", prefix, len(df), n_chunks)
    return n_chunks


def _attach_dpd_history(profiles: list[dict]) -> None:
    """mutates profiles in place, adding dpd_history — see dpd_history.py."""
    for p in profiles:
        p["dpd_history"] = generate_dpd_history(
            max_dpd=p.get("max_dpd"),
            dpd_recency_months=p.get("dpd_recency_months"),
            credit_history_type=p.get("credit_history_type"),
            max_dpd_label=p.get("max_dpd_label"),
        )


def _split_bureau_rows(profiles: list[dict]) -> list[dict]:
    rows = []
    for p in profiles:
        row = {k: p.get(k) for k in _BUREAU_FIELDS}
        row["dpd_history"] = p.get("dpd_history")
        rows.append(row)
    return rows


def _split_bank_rows(profiles: list[dict]) -> list[dict]:
    return [{k: p.get(k) for k in _BANK_FIELDS} for p in profiles]


def _split_master_rows(profiles: list[dict]) -> list[dict]:
    rows = []
    for p in profiles:
        row = {k: v for k, v in p.items() if k not in _MASTER_EXCLUDE_FIELDS}
        rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="CreditGate synthetic data generation (Phase 0)")
    parser.add_argument("--force", action="store_true", help="wipe existing data/raw and regenerate")
    parser.add_argument("--n-profiles", type=int, default=N_PROFILES)
    parser.add_argument("--n-seed-rows", type=int, default=N_SEED_ROWS)
    args = parser.parse_args()

    sentinel = RAW_DATA_PATH / "master_data_chunk_0000.parquet"
    if sentinel.exists() and not args.force:
        logger.warning("data/raw already exists — pass --force to regenerate. Exiting.")
        return

    if RAW_DATA_PATH.exists():
        shutil.rmtree(RAW_DATA_PATH)
        logger.info("wiped %s", RAW_DATA_PATH)

    Faker.seed(SEED)
    np.random.seed(SEED)
    random.seed(SEED)
    fake = Faker("en_IN")

    logger.info(
        "generating %d applicant profiles (seed=%d, reference_date=%s)",
        args.n_profiles, SEED, REFERENCE_DATE,
    )
    profiles = build_profiles_copulagan(fake, n_profiles=args.n_profiles, n_seed_rows=args.n_seed_rows)
    logger.info("scalar profile synthesis complete — %d rows", len(profiles))

    _attach_dpd_history(profiles)
    logger.info("dpd_history attached to all profiles")

    master_rows = _split_master_rows(profiles)
    bureau_rows = _split_bureau_rows(profiles)
    bank_rows = _split_bank_rows(profiles)
    itr_rows = build_itr_rows(profiles)
    asset_rows = build_asset_rows(profiles)
    alt_data_rows = build_alt_data_rows(profiles)
    gst_rows = build_gst_rows(profiles)

    _write_chunks(master_rows, "master_data")
    _write_chunks(bureau_rows, "bureau")
    _write_chunks(bank_rows, "bank_statement")
    _write_chunks(itr_rows, "itr")
    _write_chunks(asset_rows, "assets")
    _write_chunks(alt_data_rows, "alt_data")
    _write_chunks(gst_rows, "gst")

    by_type: dict[str, int] = {}
    for p in profiles:
        by_type[p["applicant_type"]] = by_type.get(p["applicant_type"], 0) + 1
    logger.info("applicant_type mix: %s", by_type)
    logger.info(
        "generation complete — %d applicants, %d ITR rows, %d GST rows (business-eligible only)",
        len(profiles), len(itr_rows), len(gst_rows),
    )


if __name__ == "__main__":
    main()
