"""
check_data.py — verification for the Phase 0 synthetic data rebuild.
replaces the old check_data.py / check_data_fast.py, which were built against
the superseded flat applicant_profiles.parquet shape and are no longer
applicable to the three-table-per-applicant shape this generator produces.

checks:
  1. schema validation — every bureau/bank/itr row actually instantiates the
     corresponding Pydantic model from src/features/schemas.py (not just
     "columns look right")
  2. schema coverage — every raw field is populated in a reasonable fraction
     of rows, not always None
  3. internal integrity — dpd_history consistent with max_dpd/dpd_recency/
     credit_history_type; ITR income components sum to gross/total; asset
     components sum to portfolio total
  4. hand-inspectable example rows per applicant type, incl. one NTC/thin-file

run:
    python -m src.ingestion.check_data
"""

from __future__ import annotations

import glob
from pathlib import Path

import polars as pl
from pydantic import ValidationError

from src.features.schemas import BankStatementRecord, BureauRecord, ITRRecord

RAW_DIR = Path("data/raw")


def _load(prefix: str) -> pl.DataFrame:
    files = sorted(glob.glob(str(RAW_DIR / f"{prefix}_chunk_*.parquet")))
    if not files:
        return pl.DataFrame()
    return pl.read_parquet(files)


def _validate_schema(df: pl.DataFrame, model, name: str, sample: int | None = None) -> tuple[int, int, list[str]]:
    rows = df.to_dicts() if sample is None else df.head(sample).to_dicts()
    ok, fail = 0, 0
    errors: list[str] = []
    for row in rows:
        try:
            model(**row)
            ok += 1
        except ValidationError as e:
            fail += 1
            if len(errors) < 5:
                errors.append(f"{row.get('applicant_id')}: {e.errors()[0]['msg']}")
    print(f"  [{name}] validated {ok}/{ok + fail} rows" + (f"  ({fail} FAILED)" if fail else "  OK"))
    for err in errors:
        print(f"      - {err}")
    return ok, fail, errors


def _coverage(df: pl.DataFrame, name: str, min_fraction: float = 0.05) -> list[str]:
    """flag any column that's always-null or near-always-null — that's a sign
    a field was never wired up rather than a genuinely sparse real signal."""
    n = len(df)
    warnings: list[str] = []
    if n == 0:
        return warnings
    for col in df.columns:
        non_null = df[col].drop_nulls().len()
        frac = non_null / n
        if frac < min_fraction:
            warnings.append(f"{name}.{col}: only {frac:.1%} populated ({non_null}/{n})")
    return warnings


def _check_dpd_integrity(bureau_df: pl.DataFrame, master_df: pl.DataFrame) -> list[str]:
    problems: list[str] = []
    history_by_id = {row["applicant_id"]: row.get("credit_history_type") for row in master_df.to_dicts()}

    for row in bureau_df.to_dicts():
        aid = row["applicant_id"]
        dpd_history = row.get("dpd_history")
        max_dpd = row.get("max_dpd")
        credit_history_type = history_by_id.get(aid)

        if dpd_history is None:
            continue

        reported = [m for m in dpd_history if m is not None]

        if credit_history_type == "NTC" and len(reported) > 2:
            problems.append(f"{aid}: NTC but {len(reported)}/24 months reported (expected ~0)")

        if (max_dpd or 0) == 0:
            nonzero_months = [
                m for m in reported
                if (m.get("d0_29", 0) + m.get("d30_59", 0) + m.get("d60_89", 0) + m.get("d90_plus", 0)) > 0
            ]
            if nonzero_months:
                problems.append(f"{aid}: max_dpd=0 but {len(nonzero_months)} non-clean reported months")

        if len(dpd_history) != 24:
            problems.append(f"{aid}: dpd_history length {len(dpd_history)} != 24")

    return problems


def _check_hard_negative_coherence(bureau_df: pl.DataFrame) -> list[str]:
    """write_off/settlement/default/suit_filed are outcomes of a genuinely
    serious delinquency (>90 DPD) — flag any row where one of these fired
    with a low max_dpd, since that combination is inconsistent (a GAN
    cross-field decorrelation failure mode caught during hand-inspection)."""
    problems: list[str] = []
    for row in bureau_df.to_dicts():
        max_dpd = row.get("max_dpd") or 0
        if max_dpd > 90:
            continue
        for flag in ("write_off_flag", "settlement_flag", "default_flag", "suit_filed_flag"):
            if row.get(flag):
                problems.append(f"{row['applicant_id']}: {flag}=True but max_dpd={max_dpd}")
    return problems


def _check_itr_integrity(itr_df: pl.DataFrame, tolerance: float = 1.0) -> list[str]:
    problems: list[str] = []
    for row in itr_df.to_dicts():
        gross = row.get("gross_total_income") or 0.0
        components_sum = sum(
            row.get(f) or 0.0
            for f in ("salary_income", "business_income", "professional_income",
                       "interest_income", "dividend_income", "capital_gains", "other_income")
        )
        if gross > 0 and abs(components_sum - gross) > max(tolerance, gross * 0.02):
            problems.append(
                f"{row['applicant_id']}/{row['year_label']}: components sum {components_sum:.2f} "
                f"!= gross {gross:.2f}"
            )
    return problems


def _check_asset_integrity(assets_df: pl.DataFrame, tolerance: float = 1.0) -> list[str]:
    problems: list[str] = []
    for row in assets_df.to_dicts():
        if not row.get("has_declared_assets"):
            continue
        total = row.get("total_portfolio_value") or 0.0
        components_sum = sum(
            row.get(f) or 0.0
            for f in ("mutual_fund_value", "equity_value", "other_securities_value", "liquid_asset_value")
        )
        if abs(components_sum - total) > max(tolerance, total * 0.02):
            problems.append(f"{row['applicant_id']}: asset components {components_sum:.2f} != total {total:.2f}")
    return problems


def main() -> None:
    print("=" * 70)
    print("CreditGate Phase 0 data verification")
    print("=" * 70)

    master_df = _load("master_data")
    bureau_df = _load("bureau")
    bank_df = _load("bank_statement")
    itr_df = _load("itr")
    assets_df = _load("assets")
    alt_df = _load("alt_data")
    gst_df = _load("gst")

    print(f"\nRow counts: master={len(master_df)} bureau={len(bureau_df)} bank={len(bank_df)} "
          f"itr={len(itr_df)} assets={len(assets_df)} alt_data={len(alt_df)} gst={len(gst_df)}")

    if len(master_df) == 0:
        print("\nNo data found in data/raw — run `python -m src.ingestion.run_generation` first.")
        return

    print("\n--- 1. Pydantic schema validation ---")
    _, bureau_fail, _ = _validate_schema(bureau_df, BureauRecord, "bureau")
    _, bank_fail, _ = _validate_schema(bank_df, BankStatementRecord, "bank_statement")
    _, itr_fail, _ = _validate_schema(itr_df, ITRRecord, "itr")

    print("\n--- 2. Schema coverage (fields populated in <5% of rows flagged) ---")
    all_warnings = []
    for df, name in [(master_df, "master"), (bureau_df, "bureau"), (bank_df, "bank"),
                      (itr_df, "itr"), (assets_df, "assets"), (alt_df, "alt_data"), (gst_df, "gst")]:
        warnings = _coverage(df, name)
        all_warnings.extend(warnings)
    if all_warnings:
        for w in all_warnings:
            print(f"  ! {w}")
    else:
        print("  no near-empty columns found")

    print("\n--- 3. Internal integrity checks ---")
    dpd_problems = _check_dpd_integrity(bureau_df, master_df)
    print(f"  dpd_history consistency: {len(dpd_problems)} problem(s)")
    for p in dpd_problems[:10]:
        print(f"      - {p}")

    hard_neg_problems = _check_hard_negative_coherence(bureau_df)
    print(f"  hard-negative flag / max_dpd coherence: {len(hard_neg_problems)} problem(s)")
    for p in hard_neg_problems[:10]:
        print(f"      - {p}")

    itr_problems = _check_itr_integrity(itr_df)
    print(f"  ITR income-component sums: {len(itr_problems)} problem(s)")
    for p in itr_problems[:10]:
        print(f"      - {p}")

    asset_problems = _check_asset_integrity(assets_df)
    print(f"  asset-component sums: {len(asset_problems)} problem(s)")
    for p in asset_problems[:10]:
        print(f"      - {p}")

    print("\n--- 4. Applicant-type breakdown ---")
    breakdown = master_df.group_by("applicant_type").len().sort("applicant_type")
    for row in breakdown.to_dicts():
        print(f"  {row['applicant_type']:<15} {row['len']}")

    print("\n--- 5. Hand-inspectable example rows ---")
    master_rows = master_df.to_dicts()
    bureau_by_id = {r["applicant_id"]: r for r in bureau_df.to_dicts()}

    seen_types: set[str] = set()
    ntc_shown = False
    for row in master_rows:
        atype = row["applicant_type"]
        chist = row.get("credit_history_type")
        show_as_type = None
        if chist == "NTC" and not ntc_shown:
            show_as_type = "NTC/thin-file"
            ntc_shown = True
        elif atype not in seen_types:
            show_as_type = atype
            seen_types.add(atype)
        if show_as_type is None:
            continue

        print(f"\n  [{show_as_type}] applicant_id={row['applicant_id']}  "
              f"age={row['age']}  income={row.get('declared_income_monthly')}  "
              f"requested_amount={row.get('requested_loan_amount')}  "
              f"credit_history={chist}")
        bureau_row = bureau_by_id.get(row["applicant_id"], {})
        print(f"      bureau_score={bureau_row.get('bureau_score')}  "
              f"max_dpd={bureau_row.get('max_dpd')}  "
              f"active_loans={bureau_row.get('active_loan_count')}  "
              f"write_off={bureau_row.get('write_off_flag')}")
        dh = bureau_row.get("dpd_history")
        if dh is not None:
            reported = sum(1 for m in dh if m is not None)
            print(f"      dpd_history: {reported}/24 months reported, last 6: {dh[-6:]}")

    print("\n" + "=" * 70)
    total_fail = bureau_fail + bank_fail + itr_fail
    total_problems = len(dpd_problems) + len(hard_neg_problems) + len(itr_problems) + len(asset_problems)
    if total_fail == 0 and total_problems == 0:
        print("RESULT: PASS — all schemas validate, no integrity problems found")
    else:
        print(f"RESULT: ISSUES FOUND — {total_fail} schema validation failures, "
              f"{total_problems} integrity problems")
    print("=" * 70)


if __name__ == "__main__":
    main()
