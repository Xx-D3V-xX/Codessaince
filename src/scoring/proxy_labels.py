"""
proxy_labels.py — Phase 5's training labels.

Disclosure, up front: this dataset has no real repayment/default outcome
column (it was never in scope — CreditGate is a synthetic-policy demo per
PS-1's own brief, not a real NBFC's book of business), so there is no way
to train a supervised model against a ground-truth label. Every synthetic
credit-scoring demo faces this same problem; the honest response is to
disclose it, not hide it behind a plausible-looking pipeline.

This module exists because the ORIGINAL src/scoring/trainer.py (see
PROGRESS.md's "Known issues" #1) got this wrong in a specific, fixable way:
it silently hardcoded a proxy formula directly inline
(`risk_score = np.where(bureau_score < 600, ...)`) using the exact same
step-threshold shape as a real BRE rule, with no disclosure and no
separation from the actual features the model also consumed. Two things
are fixed here, not "avoid using any proxy at all" (impossible without real
outcome data):

  1. **Disclosed and centralized.** This is the one place a training-label
     heuristic lives, explicitly named and explained as non-authoritative —
     the BRE (src/rules/) remains the sole real decision-maker; this exists
     only so the ML component has something non-trivial to learn against
     for demonstration purposes.
  2. **Continuous + probabilistic, not a deterministic step function.**
     Signals are combined into a continuous latent risk score (population
     z-scored, not arbitrary hardcoded cutoffs) and the binary label is a
     Bernoulli DRAW from sigmoid(risk score), not a hard `> 0.5` cut. This
     means two applicants with an identical risk profile can get different
     labels — genuine label noise, which is what real-world default
     outcomes actually look like (not everyone below some purported
     "risk threshold" actually defaults), and which prevents the model
     from trivially memorizing a deterministic formula 1:1.

Consumes the REAL engineered feature pipeline (EngineeredApplicantFeatureVector
+ cross-source fields, same vectors src/scoring/trainer.py trains on) — not
a separate hand-rolled recomputation, fixing the original trainer's other
flaw (its own disconnected feature engineering).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SEED = 42

# signal_field: (weight, invert). invert=True means "lower values are risk"
# (mirrored sign before z-scoring) -- e.g. income_trend_itr and
# asset_coverage_ratio are protective, so a LOW value should raise risk.
# Weights are deliberately NOT the same numbers as any BRE rule threshold or
# any WeightedScoringConfig weight (see weighted_deviation.py) -- this is an
# independent, if inevitably correlated, formula, not a restatement of either.
_SIGNAL_WEIGHTS: dict[str, tuple[float, bool]] = {
    "dpd_severity_score": (1.4, False),
    "negative_severity_ratio": (1.6, False),
    "bounce_rate": (1.0, False),
    "credit_utilization": (0.9, False),
    "emi_to_inflow_ratio": (1.1, False),
    "income_trend_itr": (0.7, True),
    "obligation_discrepancy": (0.6, False),
    "asset_coverage_ratio": (0.5, True),
}

RISK_SCALE = 1.3  # spreads the sigmoid so labels aren't clustered near 0.5 for every applicant


def _zscore(series: pd.Series) -> pd.Series:
    """population z-score; missing values imputed to the population mean (contributes 0 to z, not silently dropped to 0 raw)."""
    filled = series.astype(float)
    mean = filled.mean(skipna=True)
    std = filled.std(skipna=True)
    filled = filled.fillna(mean)
    if not std or np.isnan(std) or std == 0:
        return pd.Series(0.0, index=series.index)
    return (filled - mean) / std


def compute_default_probability(feature_rows: list[dict]) -> np.ndarray:
    """
    continuous P(default) proxy per applicant, batch-relative (z-scored
    against the batch it's called with — same batch trainer.py builds the
    feature matrix from, so this is internally consistent per training run).
    """
    df = pd.DataFrame(feature_rows)
    latent = pd.Series(0.0, index=df.index)
    for field, (weight, invert) in _SIGNAL_WEIGHTS.items():
        if field not in df.columns:
            continue
        z = _zscore(df[field])
        if invert:
            z = -z
        latent += weight * z

    probability = 1.0 / (1.0 + np.exp(-RISK_SCALE * latent.to_numpy()))
    return probability


def sample_proxy_labels(feature_rows: list[dict], seed: int = SEED) -> tuple[np.ndarray, np.ndarray]:
    """
    returns (labels, probabilities). labels[i] ~ Bernoulli(probabilities[i])
    — a genuine stochastic draw, not a threshold cut, so the label carries
    irreducible noise the model cannot fully recover (same as real default
    outcomes: identical risk profiles don't all resolve the same way).
    """
    probabilities = compute_default_probability(feature_rows)
    rng = np.random.default_rng(seed)
    draws = rng.random(len(probabilities))
    labels = (draws < probabilities).astype(np.int32)
    return labels, probabilities
