"""
explain.py — SHAP explainability, wired to both pipeline models (TODO.md
Phase 5's "SHAP explainability wired to both models" bullet).

Deliberately separate from src/scoring/weighted_deviation.py's admin-set
weights, never merged into one number. CLAUDE.md §3.6's own resolution to
the double-weighting limitation: XGBoost has its own *implicit*, trained-in
feature importance (what SHAP surfaces), and the admin's *explicit*
rules-layer weight compounds with it rather than replacing it — that
compounding can't be undone from outside the model, so the honest move is
to show both contributions side by side, not pretend they can be collapsed
into one "true" importance number.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import shap
import xgboost as xgb


@dataclass
class FeatureContribution:
    feature: str
    value: float | None
    shap_value: float


@dataclass
class ApplicantExplanation:
    base_value: float
    predicted_probability: float
    top_contributions: list[FeatureContribution]


def build_explainer(model: xgb.XGBClassifier) -> shap.TreeExplainer:
    """one explainer per trained model — cheap to build, callers may cache it themselves if explaining many applicants."""
    return shap.TreeExplainer(model)


def explain_applicant(
    explainer: shap.TreeExplainer,
    feature_row: dict[str, float],
    feature_columns: list[str],
    top_n: int = 10,
) -> ApplicantExplanation:
    """
    the model's own SHAP-based attribution for one applicant, ranked by
    |shap_value| — this is the "model's own SHAP-based weighting" CLAUDE.md
    §3.6 says must be surfaced separately from the admin's explicit weight.
    """
    x = np.array([[feature_row.get(col, np.nan) for col in feature_columns]], dtype=np.float64)
    shap_values = explainer.shap_values(x)
    row_shap = shap_values[0] if shap_values.ndim == 2 else shap_values[0, :, 1]

    base_value = explainer.expected_value
    if isinstance(base_value, (list, np.ndarray)):
        base_value = float(np.asarray(base_value).reshape(-1)[-1])

    predicted_probability = float(1.0 / (1.0 + np.exp(-(base_value + row_shap.sum()))))

    contributions = [
        FeatureContribution(feature=col, value=feature_row.get(col), shap_value=float(sv))
        for col, sv in zip(feature_columns, row_shap)
    ]
    contributions.sort(key=lambda c: abs(c.shap_value), reverse=True)

    return ApplicantExplanation(
        base_value=float(base_value),
        predicted_probability=predicted_probability,
        top_contributions=contributions[:top_n],
    )
