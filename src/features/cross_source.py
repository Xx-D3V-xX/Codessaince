"""
cross_source.py — Phase 2 cross-source derived features.

engine.py computes same-source features one applicant-level row at a time
(bureau OR bank OR itr) and explicitly defers anything that needs more than
one source at once — see engine.py's module docstring. This module is that
deferred stage: it combines the normalized master profile, bank statement,
ITR, and assets data assembled by applicant_adapter.py (Phase 1) into the
cross-source features documented in CLAUDE.md §4.5 and TODO.md Phase 2:

  - obligation_discrepancy: declared_existing_obligations (master, stated)
    vs. verified monthly EMI load (bank statement, observed) — CLAUDE.md
    §4.5 keeps declared and verified figures independent and treats their
    discrepancy as the informative signal, not a pick-one-source merge.
  - income_discrepancy: same pattern for declared_income_monthly (master)
    vs. ITR yr1 total_income (documented, most recent filed year).
  - asset_coverage_ratio: declared investable portfolio value (assets) as
    a multiple of requested_loan_amount (master) — how much of the loan a
    liquidation of declared assets could cover.

Same null-vs-zero discipline as schemas.py/engine.py throughout (CLAUDE.md
§4.4): a ratio stays None when an input source needed to compute it is
genuinely absent, since "unknown" and "computed, genuinely zero" would get
different rules from a BRE author. cross_source_data_completeness is the
one plain (non-Optional) exception, matching every other *_data_completeness
field's purpose of reporting absence as a number rather than hiding it.
"""

from __future__ import annotations

from src.features.engine import FeatureEngine, _safe_div
from src.features.schemas import EngineeredApplicantFeatureVector
from src.ingestion.applicant_adapter import AdapterResult, load_and_adapt, to_engine_frames

_CROSS_SOURCE_FEATURE_COUNT = 3  # obligation_discrepancy, income_discrepancy, asset_coverage_ratio


def _emi_total(bank_row: dict | None) -> float | None:
    """
    sum of emi_like_recurring_debits — the "verified" side of
    obligation_discrepancy. bank_row's list may be empty (no EMI-like
    debits detected, a genuine zero) or the field itself may be None
    (bank source unavailable for this applicant) — those are different
    facts, so an empty list returns 0.0 but a missing field returns None.
    """
    if bank_row is None:
        return None
    emi = bank_row.get("emi_like_recurring_debits")
    if emi is None:
        return None
    return float(sum(emi)) if isinstance(emi, list) else float(emi)


def compute_cross_source_features(
    master_row: dict | None,
    bank_row: dict | None,
    itr_years: dict[str, dict] | None,
    assets_row: dict | None,
) -> dict:
    """
    one applicant's worth of cross-source features, dict-in dict-out to
    match engine.py's _compute_*_features convention — model_dump() happens
    upstream in compute_batch_cross_source(), plain dict lookups happen here.
    """
    computed = 0

    declared_obligations = master_row.get("declared_existing_obligations") if master_row else None
    verified_obligations = _emi_total(bank_row)
    obligation_discrepancy = None
    if declared_obligations is not None and verified_obligations is not None:
        obligation_discrepancy = _safe_div(declared_obligations - verified_obligations, verified_obligations)
        computed += 1

    declared_income_monthly = master_row.get("declared_income_monthly") if master_row else None
    itr_yr1 = (itr_years or {}).get("yr1")
    itr_total_income = itr_yr1.get("total_income") if itr_yr1 else None
    income_discrepancy = None
    if declared_income_monthly is not None and itr_total_income is not None:
        declared_income_annual = declared_income_monthly * 12
        income_discrepancy = _safe_div(declared_income_annual - itr_total_income, itr_total_income)
        computed += 1

    requested_loan_amount = master_row.get("requested_loan_amount") if master_row else None
    asset_coverage_ratio = None
    if assets_row is not None and requested_loan_amount is not None:
        has_assets = assets_row.get("has_declared_assets")
        portfolio_value = assets_row.get("total_portfolio_value")
        # has_declared_assets=False is a stated "no assets" fact, not a
        # missing-source fact — genuinely zero coverage, not unknown.
        asset_coverage_ratio = (
            0.0 if has_assets is False or portfolio_value is None else _safe_div(portfolio_value, requested_loan_amount)
        )
        computed += 1

    return {
        "obligation_discrepancy": obligation_discrepancy,
        "income_discrepancy": income_discrepancy,
        "asset_coverage_ratio": asset_coverage_ratio,
        "cross_source_data_completeness": computed / _CROSS_SOURCE_FEATURE_COUNT,
    }


def compute_batch_cross_source(result: AdapterResult) -> dict[str, dict]:
    """
    cross-source features for every applicant_id present in any of the
    adapter's validated sources (union, matching engine.py's compute_batch
    union-not-intersection convention) — keyed by applicant_id for merging
    into engine.py's per-applicant vectors via merge_into_vectors().
    """
    master_by_id = {m.applicant_id: m.model_dump() for m in result.master}
    bank_by_id = {b.applicant_id: b.model_dump() for b in result.bank}
    assets_by_id = {a.applicant_id: a.model_dump() for a in result.assets}

    itr_by_id: dict[str, dict[str, dict]] = {}
    for r in result.itr:
        itr_by_id.setdefault(r.applicant_id, {})[r.year_label] = r.model_dump()

    applicant_ids = set(master_by_id) | set(bank_by_id) | set(assets_by_id) | set(itr_by_id)

    return {
        applicant_id: compute_cross_source_features(
            master_by_id.get(applicant_id),
            bank_by_id.get(applicant_id),
            itr_by_id.get(applicant_id),
            assets_by_id.get(applicant_id),
        )
        for applicant_id in applicant_ids
    }


def merge_into_vectors(
    vectors: list[EngineeredApplicantFeatureVector],
    cross_source_by_id: dict[str, dict],
) -> list[EngineeredApplicantFeatureVector]:
    """
    merges cross-source features into engine.py's same-source vectors.
    Returns new model instances via model_copy(update=...) rather than
    mutating vectors in place — an applicant present in bureau/bank/itr
    but absent from every cross-source input (e.g. no master row) simply
    gets the schema's all-None cross-source defaults, not a KeyError.
    """
    merged = []
    for vector in vectors:
        cross = cross_source_by_id.get(vector.applicant_id, {})
        merged.append(vector.model_copy(update=cross))
    return merged


def _run_cross_source_pipeline() -> None:
    """standalone entry point — Phase 1 adapter + Phase 0/1 engine features + Phase 2 cross-source, merged."""
    result = load_and_adapt()
    bureau_df, bank_df, itr_df = to_engine_frames(result)

    engine = FeatureEngine()
    vectors = engine.compute_batch(bureau_df, bank_df, itr_df)

    cross_source_by_id = compute_batch_cross_source(result)
    merged = merge_into_vectors(vectors, cross_source_by_id)

    print(f"cross-source features computed and merged for {len(merged)} of {len(vectors)} feature vectors")


if __name__ == "__main__":
    _run_cross_source_pipeline()
