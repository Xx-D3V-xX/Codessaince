"""
feature_matrix.py — encodes one applicant's EngineeredApplicantFeatureVector
(already carrying Phase 2's cross-source fields) plus a small set of raw
passthrough fields and the admin-weighted risk signal (src/scoring/
weighted_deviation.py) into a flat numeric row XGBoost can train on.

Per the confirmed training-feature-architecture decision (PROGRESS.md):
ML consumes the engineered vector + cross-source features + raw passthrough
(age, declared_income_monthly, requested_loan_amount) — not a hand-rolled
separate feature set recomputed from scratch, which was the old trainer.py's
central flaw (see its module docstring / PROGRESS.md Known Issues #1).

Categorical band fields reuse src/features/engine.py's own *_BANDS lists for
ordinal encoding, so band ordering ("poor" < "fair" < "good" < ...) is
defined in exactly one place, never duplicated here as a second, driftable
copy. Missing values encode to NaN throughout — XGBoost's native
missing-value handling (it learns a default split direction per node) means
no field needs a fabricated 0-fill that would misrepresent absence as a
favorable/neutral reading, the same null-vs-zero discipline the rest of
this project applies (CLAUDE.md §4.4), carried into the ML layer.
"""

from __future__ import annotations

import numpy as np

from src.features.engine import (
    BUREAU_SCORE_BANDS,
    CASH_FLOW_VOLATILITY_BANDS,
    CREDIT_CARD_UTIL_BANDS,
    INCOME_TREND_BANDS,
    MAX_DPD_BANDS,
)
from src.features.schemas import EngineeredApplicantFeatureVector

_ENGINEERED_VECTOR_FIELD_NAMES = set(EngineeredApplicantFeatureVector.model_fields)

RAW_PASSTHROUGH_FIELDS = ["age", "declared_income_monthly", "requested_loan_amount"]

_BAND_ORDER = {
    "bureau_score_band": [b[2] for b in BUREAU_SCORE_BANDS],
    "max_dpd_band": [b[2] for b in MAX_DPD_BANDS],
    "credit_card_utilization_band": [b[2] for b in CREDIT_CARD_UTIL_BANDS],
    "cash_flow_volatility_band": [b[2] for b in CASH_FLOW_VOLATILITY_BANDS],
    "income_trend_direction": [b[2] for b in INCOME_TREND_BANDS],
}

_BOOL_FIELDS = [
    "has_credit_history", "bureau_thin_file_flag", "is_recently_delinquent",
    "chronic_delinquency_flag", "hard_negative_flag", "income_composition_shift_flag",
    "bounce_severity_flag", "income_type_consistency_flag",
    "income_diversification_flag_yr1", "income_diversification_flag_yr2",
]

_EXCLUDED_FIELDS = {"applicant_id", "computed_at"}


def _numeric_field_names(feature_vector_row: dict) -> list[str]:
    """every EngineeredApplicantFeatureVector field except identifiers/metadata and the band/bool fields encoded separately below."""
    band_fields = set(_BAND_ORDER)
    bool_fields = set(_BOOL_FIELDS)
    return [k for k in feature_vector_row if k not in _EXCLUDED_FIELDS and k not in band_fields and k not in bool_fields]


def build_feature_row(master_row: dict, feature_vector_row: dict, admin_weighted_risk_signal: float) -> dict[str, float]:
    """
    one applicant's fully-encoded, flat numeric feature row. Field
    iteration order is stable across calls (Pydantic model_dump() preserves
    declaration order for every instance of the same model class), so this
    dict's key order is consistent from row to row — get_feature_columns()
    below reflects that same order for persisting alongside a trained model.
    """
    row: dict[str, float] = {}

    for field in RAW_PASSTHROUGH_FIELDS:
        value = master_row.get(field)
        row[field] = float(value) if value is not None else np.nan

    for field in _numeric_field_names(feature_vector_row):
        value = feature_vector_row.get(field)
        row[field] = float(value) if value is not None else np.nan

    for field, order in _BAND_ORDER.items():
        value = feature_vector_row.get(field)
        row[field] = float(order.index(value)) if value in order else np.nan

    for field in _BOOL_FIELDS:
        value = feature_vector_row.get(field)
        row[field] = np.nan if value is None else float(bool(value))

    row["admin_weighted_risk_signal"] = admin_weighted_risk_signal
    return row


def get_feature_columns(sample_feature_vector_row: dict) -> list[str]:
    """the exact, stable column order build_feature_row() produces for vectors shaped like the sample given."""
    numeric_fields = _numeric_field_names(sample_feature_vector_row)
    return [*RAW_PASSTHROUGH_FIELDS, *numeric_fields, *_BAND_ORDER.keys(), *_BOOL_FIELDS, "admin_weighted_risk_signal"]


def extract_feature_vector_fields(context: dict) -> dict:
    """
    reconstructs the pure EngineeredApplicantFeatureVector-shaped dict out of
    a flat rule-evaluation context (src/rules/context.py's build_rule_context()
    output — master fields + raw bureau/bank fields + the engineered vector,
    all merged). Used by src/pricing/eligibility.py's inference path, which
    only has the merged context (from Application.rule_context_snapshot) on
    hand, not the original separate model_dump() dict trainer.py built the
    feature matrix from. Filtering by field NAME (not by re-deriving values)
    is exact and driftless: engineered field names never collide with raw
    bureau/bank/master field names (see build_rule_context()'s own docstring),
    so this recovers exactly the same key set trainer.py trained against.
    """
    return {k: v for k, v in context.items() if k in _ENGINEERED_VECTOR_FIELD_NAMES}
