"""
applicant_adapter.py — Phase 1 ingestion/adapter layer.

Reads Phase 0's chunked parquet output under data/raw/ and reshapes it into
the exact three input shapes src/features/engine.py's FeatureEngine.compute_batch()
requires: BureauRecord (one row/applicant), BankStatementRecord (one row/applicant),
ITRRecord (one row per applicant_id x year_label).

engine.py's own _run_feature_pipeline() already globs bureau_chunk_*/
bank_statement_chunk_*/itr_chunk_* directly — this module exists as the
explicit, testable seam between "whatever the generator happens to produce"
and "what the engine's contract requires", per TODO.md Phase 1. It also
folds in master_data (declared income, requested amount, age, applicant_type,
etc.) into a NormalizedApplicantProfile — fields engine.py's raw schemas
don't cover but downstream phases (BRE, cross-source features) will need.
"""

from __future__ import annotations

import glob
from pathlib import Path

import polars as pl
from pydantic import BaseModel, ValidationError

from src.features.schemas import AssetsRecord, BankStatementRecord, BureauRecord, ITRRecord

RAW_DATA_PATH = Path("data/raw")


class NormalizedApplicantProfile(BaseModel):
    """
    one row per applicant, master/applicant-level fields only — the fields
    engine.py's raw schemas don't cover (they're bureau/bank/itr-specific).
    not fed into FeatureEngine directly; carried alongside the engineered
    feature vector for downstream phases (BRE thresholds reference these
    fields directly, e.g. requested_loan_amount, declared_income_monthly).
    """

    applicant_id: str
    age: int | None = None
    applicant_type: str | None = None
    employment_type: str | None = None
    geography_type: str | None = None
    state_code: int | None = None
    state_name: str | None = None
    credit_history_type: str | None = None
    employment_vintage_months: int | None = None
    business_vintage_months: int | None = None
    declared_income_monthly: float | None = None
    declared_existing_obligations: float | None = None
    requested_loan_amount: float | None = None
    requested_tenure_months: int | None = None


class AdapterResult(BaseModel):
    """validated, applicant-id-indexed adapter output plus a per-source error log."""

    master: list[NormalizedApplicantProfile]
    bureau: list[BureauRecord]
    bank: list[BankStatementRecord]
    itr: list[ITRRecord]
    assets: list[AssetsRecord]
    errors: dict[str, list[str]] = {}

    model_config = {"arbitrary_types_allowed": True}


def _load_chunks(prefix: str) -> pl.DataFrame:
    files = sorted(glob.glob(str(RAW_DATA_PATH / f"{prefix}_chunk_*.parquet")))
    if not files:
        return pl.DataFrame()
    return pl.concat([pl.read_parquet(f) for f in files], how="vertical_relaxed")


def _rows_to_models(
    df: pl.DataFrame,
    model_cls: type[BaseModel],
    source_name: str,
    errors: dict[str, list[str]],
) -> list[BaseModel]:
    """
    instantiate model_cls per row — a genuine validation pass, not a column
    eyeball. rows failing validation are logged to errors[source_name] and
    dropped rather than raising, so one bad applicant doesn't abort the batch
    (per PS-1's "graceful handling of missing/inconsistent/invalid data").
    """
    if df.height == 0:
        return []

    valid_field_names = set(model_cls.model_fields.keys())
    records: list[BaseModel] = []
    row_errors: list[str] = []

    for row in df.to_dicts():
        filtered = {k: v for k, v in row.items() if k in valid_field_names}
        try:
            records.append(model_cls(**filtered))
        except ValidationError as exc:
            applicant_id = row.get("applicant_id", "<unknown>")
            row_errors.append(f"{applicant_id}: {exc}")

    if row_errors:
        errors[source_name] = row_errors

    return records


def load_and_adapt() -> AdapterResult:
    """
    full Phase 1 adapter run: load every raw source, validate each row against
    its Pydantic contract, return the adapted, typed result.
    """
    errors: dict[str, list[str]] = {}

    master_df = _load_chunks("master_data")
    bureau_df = _load_chunks("bureau")
    bank_df = _load_chunks("bank_statement")
    itr_df = _load_chunks("itr")
    assets_df = _load_chunks("assets")

    master = _rows_to_models(master_df, NormalizedApplicantProfile, "master_data", errors)
    bureau = _rows_to_models(bureau_df, BureauRecord, "bureau", errors)
    bank = _rows_to_models(bank_df, BankStatementRecord, "bank_statement", errors)
    itr = _rows_to_models(itr_df, ITRRecord, "itr", errors)
    assets = _rows_to_models(assets_df, AssetsRecord, "assets", errors)

    return AdapterResult(master=master, bureau=bureau, bank=bank, itr=itr, assets=assets, errors=errors)


def to_engine_frames(result: AdapterResult) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """
    convert validated Pydantic records back to the three polars DataFrames
    FeatureEngine.compute_batch(bureau_df, bank_df, itr_df) expects. Round-
    tripping through the validated models (not just re-reading the raw parquet)
    is the point — this is what "adapted" means: only rows that survived
    Pydantic validation make it into the engine's input.
    """
    bureau_df = pl.DataFrame([r.model_dump() for r in result.bureau], strict=False) if result.bureau else pl.DataFrame()
    bank_df = pl.DataFrame([r.model_dump() for r in result.bank], strict=False) if result.bank else pl.DataFrame()
    itr_df = pl.DataFrame([r.model_dump() for r in result.itr], strict=False) if result.itr else pl.DataFrame()
    return bureau_df, bank_df, itr_df


if __name__ == "__main__":
    result = load_and_adapt()
    print(
        f"master: {len(result.master)} bureau: {len(result.bureau)} bank: {len(result.bank)} "
        f"itr: {len(result.itr)} assets: {len(result.assets)}"
    )
    if result.errors:
        for source, msgs in result.errors.items():
            print(f"{source}: {len(msgs)} validation failures (first: {msgs[0]})")
    else:
        print("no validation failures across any source")
