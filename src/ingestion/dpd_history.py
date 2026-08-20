"""
dpd_history.py — 24-month DPD bucket history, generated as a deterministic
rule-based post-process keyed off each applicant's own max_dpd,
dpd_recency_months, and credit_history_type. NOT synthesized by CopulaGAN —
a structured monthly time series doesn't fit a flat-feature tabular model,
same reasoning CLAUDE.md §6 applies to layering GST invoices / bank
transactions onto a CopulaGAN-sampled profile in the old generator.

each entry in the returned 24-length list is either None (no reported data
that month — distinct from a genuinely clean month) or a dict with keys
d0_29/d30_59/d60_89/d90_plus, matching schemas.DPDMonth field names exactly.
list is chronological oldest -> newest (index 0 = 24 months ago, index 23 =
most recent month), matching engine.py's dpd_trend slope-over-index convention.
"""

from __future__ import annotations

import random

DPD_WINDOW_MONTHS = 24

# credit_history_type -> fraction of the 24-month window that has ANY reported
# data at all. NTC applicants have essentially no bureau history; thin-file
# applicants have a short recent tail; established applicants are ~fully covered.
HISTORY_COVERAGE_FRACTION = {
    "NTC": (0.0, 0.05),
    "THIN_FILE": (0.15, 0.40),
    "ESTABLISHED": (0.85, 1.0),
}

# max_dpd bucket label -> plausible trajectory shape. these are the same
# labels priors.MAX_DPD_BUCKETS produces, so callers can pass the bucket
# label straight through without a remapping step.
_CLEAN_LABELS = {"0"}
_MILD_LABELS = {"1-30"}
_MODERATE_LABELS = {"31-60"}
_SEVERE_LABELS = {"61-90", "91-180"}
_CRITICAL_LABELS = {"181+", "SERIOUS_WRITEOFF"}


def _bucket_for_dpd_value(dpd_days: int) -> dict:
    """split a single scalar DPD value into the four bucket counts — a real
    bureau report counts accounts in each bucket, but our applicants are
    modeled with one dominant delinquent obligation, so the peak bucket
    gets the count and the rest are zero for that month."""
    if dpd_days <= 0:
        return {"d0_29": 0, "d30_59": 0, "d60_89": 0, "d90_plus": 0}
    if dpd_days <= 29:
        return {"d0_29": 1, "d30_59": 0, "d60_89": 0, "d90_plus": 0}
    if dpd_days <= 59:
        return {"d0_29": 0, "d30_59": 1, "d60_89": 0, "d90_plus": 0}
    if dpd_days <= 89:
        return {"d0_29": 0, "d30_59": 0, "d60_89": 1, "d90_plus": 0}
    return {"d0_29": 0, "d30_59": 0, "d60_89": 0, "d90_plus": 1}


def _clean_month() -> dict:
    return {"d0_29": 0, "d30_59": 0, "d60_89": 0, "d90_plus": 0}


def _build_coverage_mask(credit_history_type: str) -> list[bool]:
    """which of the 24 months have ANY reported data — pre-history months
    (before the applicant's file existed) are True->False from the start
    of the window, i.e. coverage is a contiguous tail ending at month 23,
    matching how a real bureau report has a defined 'since' date."""
    lo, hi = HISTORY_COVERAGE_FRACTION.get(credit_history_type, (0.85, 1.0))
    coverage_frac = random.uniform(lo, hi)
    covered_months = max(0, min(DPD_WINDOW_MONTHS, round(coverage_frac * DPD_WINDOW_MONTHS)))
    start_idx = DPD_WINDOW_MONTHS - covered_months
    return [i >= start_idx for i in range(DPD_WINDOW_MONTHS)]


def _trajectory_peaks(
    n_months: int,
    max_dpd: int,
    dpd_recency_months: int,
    severity_label: str,
) -> list[int]:
    """
    build a plausible per-month DPD-days trajectory (peak-DPD-that-month, one
    scalar per covered month) ending consistently with dpd_recency_months
    (months since the account was last in the reported max_dpd state) and
    peaking at max_dpd itself somewhere in the window — not a single spike
    in an otherwise-clean history.
    """
    if n_months == 0:
        return []

    # max_dpd (numeric) is ground truth — severity_label only selects a
    # trajectory SHAPE below, it must never override max_dpd>0 into an
    # all-clean history. a label/numeric mismatch (e.g. from independently
    # sampled GAN columns decorrelating) falls back to deriving shape from
    # the numeric value instead of trusting a contradictory label.
    if max_dpd <= 0:
        return [0] * n_months
    if severity_label in _CLEAN_LABELS:
        severity_label = (
            "1-30" if max_dpd <= 30 else
            "31-60" if max_dpd <= 60 else
            "61-90" if max_dpd <= 90 else
            "91-180" if max_dpd <= 180 else
            "181+"
        )

    # index (from the end, i.e. most-recent-first) where the applicant was
    # last at their reported max_dpd / recency state.
    recency_idx_from_end = max(0, min(n_months - 1, dpd_recency_months))
    peak_idx = n_months - 1 - recency_idx_from_end

    # roll-forward (worsening then recovering) or roll-off (pure recovery)
    # trajectory around peak_idx — a gradual ramp up to max_dpd, then a
    # gradual step-down back toward 0/clean, consistent with real bureau
    # delinquency-then-cure or delinquency-then-write-off patterns.
    ramp_up_months = random.randint(1, max(1, min(4, peak_idx + 1)))
    ramp_down_months = random.randint(1, max(1, min(5, n_months - peak_idx)))

    trajectory = [0] * n_months

    # ramp up into the peak
    for k in range(ramp_up_months):
        idx = peak_idx - (ramp_up_months - k)
        if 0 <= idx < n_months:
            frac = (k + 1) / (ramp_up_months + 1)
            trajectory[idx] = max(trajectory[idx], round(max_dpd * frac))

    trajectory[peak_idx] = max_dpd

    if severity_label in _CRITICAL_LABELS:
        # critical/write-off severity: does not fully recover within window —
        # stays elevated (chronic) rather than curing back to a clean month.
        for idx in range(peak_idx + 1, n_months):
            decay = max(0.55, 1.0 - 0.05 * (idx - peak_idx))
            trajectory[idx] = round(max_dpd * decay)
    else:
        # mild/moderate/severe: gradual recovery (cure) back toward clean.
        for k in range(1, ramp_down_months + 1):
            idx = peak_idx + k
            if 0 <= idx < n_months:
                frac = max(0.0, 1 - k / (ramp_down_months + 1))
                trajectory[idx] = round(max_dpd * frac)

    return [max(0, min(v, 900)) for v in trajectory]


def generate_dpd_history(
    max_dpd: int | None,
    dpd_recency_months: int | None,
    credit_history_type: str | None,
    max_dpd_label: str | None = None,
) -> list[dict | None]:
    """
    24-month chronological dpd_history for one applicant. entries are None
    for uncovered (pre-history / unreported) months, else a dict with keys
    matching schemas.DPDMonth. max_dpd_label, if provided, is the priors
    MAX_DPD_BUCKETS label the applicant's max_dpd was sampled from — used to
    pick a trajectory shape (recovery vs. chronic) without re-deriving it
    from the raw number; falls back to deriving it from max_dpd if absent.
    """
    credit_history_type = credit_history_type or "ESTABLISHED"
    coverage = _build_coverage_mask(credit_history_type)
    covered_idxs = [i for i, c in enumerate(coverage) if c]
    n_covered = len(covered_idxs)

    if n_covered == 0:
        return [None] * DPD_WINDOW_MONTHS

    max_dpd = int(max_dpd or 0)
    dpd_recency_months = int(dpd_recency_months if dpd_recency_months is not None else 0)

    if max_dpd_label is None:
        if max_dpd <= 0:
            max_dpd_label = "0"
        elif max_dpd <= 30:
            max_dpd_label = "1-30"
        elif max_dpd <= 60:
            max_dpd_label = "31-60"
        elif max_dpd <= 90:
            max_dpd_label = "61-90"
        elif max_dpd <= 180:
            max_dpd_label = "91-180"
        else:
            max_dpd_label = "181+"

    peaks = _trajectory_peaks(n_covered, max_dpd, dpd_recency_months, max_dpd_label)

    history: list[dict | None] = [None] * DPD_WINDOW_MONTHS
    for local_idx, global_idx in enumerate(covered_idxs):
        dpd_val = peaks[local_idx]
        history[global_idx] = _bucket_for_dpd_value(dpd_val) if dpd_val > 0 else _clean_month()

    return history
