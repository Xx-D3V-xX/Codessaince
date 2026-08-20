"""
priors.py — Indian demographic bucket priors + bucket-sampling helpers.

Reused/adapted from the old src/ingestion/generator.py (that machinery was
genuinely reusable per CLAUDE.md §6 — the row SHAPE is what changed, not the
distributions). Trimmed to drop everything tied to the old flat-profile /
transaction-event-stream shape (merchant templates, HSN sectors, E-Way Bill
constants, UPI simulation rates) — those belonged to a per-transaction layer
this rebuild does not produce; engine.py wants applicant-level aggregates.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

import numpy as np

REFERENCE_DATE = date(2026, 4, 11)

STATE_CODES = [6, 7, 8, 9, 19, 22, 24, 27, 29, 33, 36]
STATE_NAMES = {
    6: "Haryana", 7: "Delhi", 8: "Rajasthan", 9: "Uttar Pradesh",
    19: "West Bengal", 22: "Chhattisgarh", 24: "Gujarat",
    27: "Maharashtra", 29: "Karnataka", 33: "Tamil Nadu", 36: "Telangana",
}

ITR_TYPE_BY_APPLICANT = {
    "SALARIED": ["ITR1", "ITR2"],
    "SELF_EMPLOYED": ["ITR3", "ITR4"],
    "MSME": ["ITR3", "ITR4"],
    "CORPORATE": ["ITR5", "ITR6"],
}

# ── bucket tables: (label, lo, hi, weight_pct) ──────────────────────────────

APPLICANT_TYPE_DIST = [
    ("SALARIED", 50), ("SELF_EMPLOYED", 20), ("MSME", 27), ("CORPORATE", 3),
]

GEOGRAPHY_DIST = [
    ("METRO", 20), ("URBAN", 20), ("SEMI_URBAN", 32), ("RURAL", 28),
]

AGE_BUCKETS = [
    ("18-24", 18, 24, 8), ("25-29", 25, 29, 17), ("30-34", 30, 34, 20),
    ("35-39", 35, 39, 17), ("40-44", 40, 44, 14), ("45-49", 45, 49, 10),
    ("50-54", 50, 54, 7), ("55-59", 55, 59, 4), ("60+", 60, 75, 3),
]

CREDIT_HISTORY_DIST = [
    ("NTC", 15), ("THIN_FILE", 10), ("ESTABLISHED", 75),
]

SALARIED_INCOME_BUCKETS = [
    ("<15K", 8_000, 15_000, 20), ("15-25K", 15_000, 25_000, 27),
    ("25-40K", 25_000, 40_000, 24), ("40-60K", 40_000, 60_000, 13),
    ("60K-1L", 60_000, 100_000, 10), ("1-2L", 100_000, 200_000, 4.5),
    (">2L", 200_000, 450_000, 1.5),
]

SELF_EMPLOYED_INCOME_BUCKETS = [
    ("<15K", 8_000, 15_000, 25), ("15-30K", 15_000, 30_000, 27),
    ("30-50K", 30_000, 50_000, 20), ("50K-1L", 50_000, 100_000, 16),
    ("1-2L", 100_000, 200_000, 8), ("2-5L", 200_000, 500_000, 3),
    (">5L", 500_000, 1_200_000, 1),
]

PERSONAL_LOAN_AMOUNT_BUCKETS = [
    ("<50K", 10_000, 50_000, 20), ("50K-1L", 50_000, 100_000, 30),
    ("1-3L", 100_000, 300_000, 30), ("3-5L", 300_000, 500_000, 12),
    ("5-10L", 500_000, 1_000_000, 7), ("10-20L", 1_000_000, 2_000_000, 1),
]

PERSONAL_LOAN_TENURE_BUCKETS = [
    ("3-6M", 3, 6, 3), ("7-12M", 7, 12, 15), ("13-24M", 13, 24, 35),
    ("25-36M", 25, 36, 27), ("37-48M", 37, 48, 12), ("49-60M", 49, 60, 7),
    (">60M", 61, 84, 1),
]

MSME_CONSTITUTION_DIST = [
    ("PROPRIETORSHIP", 70), ("PARTNERSHIP", 15), ("PVT_LTD", 10), ("OTHER", 5),
]

MSME_SECTOR_DIST = [
    ("TRADE", 50), ("MANUFACTURING", 22), ("SERVICES", 15),
    ("CONSTRUCTION", 5), ("TRANSPORT", 4), ("AGRICULTURE", 2), ("OTHER", 2),
]

MSME_LOAN_AMOUNT_BUCKETS = [
    ("<=5L", 100_000, 500_000, 25), ("5-10L", 500_000, 1_000_000, 35),
    ("10-25L", 1_000_000, 2_500_000, 20), ("25-50L", 2_500_000, 5_000_000, 12),
    ("50L-1Cr", 5_000_000, 10_000_000, 8),
]

MSME_TURNOVER_BUCKETS = [
    ("<10L", 300_000, 1_000_000, 10), ("10-25L", 1_000_000, 2_500_000, 15),
    ("25-50L", 2_500_000, 5_000_000, 15), ("50L-1Cr", 5_000_000, 10_000_000, 20),
    ("1-2Cr", 10_000_000, 20_000_000, 15), ("2-5Cr", 20_000_000, 50_000_000, 12),
    ("5-10Cr", 50_000_000, 100_000_000, 8), (">10Cr", 100_000_000, 300_000_000, 5),
]

BUREAU_SCORE_BUCKETS = [
    ("300-579", 300, 579, 7), ("580-644", 580, 644, 10), ("645-680", 645, 680, 12),
    ("681-730", 681, 730, 22), ("731-770", 731, 770, 27), ("771-790", 771, 790, 11),
    ("791-900", 791, 900, 11),
]

ACTIVE_LOANS_BUCKETS = [
    ("0", 0, 0, 20), ("1", 1, 1, 32), ("2", 2, 2, 25),
    ("3", 3, 3, 12), ("4", 4, 4, 6), ("5+", 5, 10, 5),
]

ENQUIRY_30D_BUCKETS = [
    ("0", 0, 0, 68), ("1", 1, 1, 20), ("2", 2, 2, 7),
    ("3", 3, 3, 3), ("4+", 4, 10, 2),
]

# max_dpd bucket label doubles as the delinquency-severity tier used to drive
# dpd_history trajectory shape in dpd_history.py — keep labels stable.
MAX_DPD_BUCKETS = [
    ("0", 0, 0, 78), ("1-30", 1, 30, 10), ("31-60", 31, 60, 4),
    ("61-90", 61, 90, 2.5), ("91-180", 91, 180, 3), ("181+", 181, 365, 1.5),
    ("SERIOUS_WRITEOFF", 366, 900, 1.0),
]

CC_UTILIZATION_BUCKETS = [
    ("0%", 0, 0, 10), ("1-20%", 1, 20, 25), ("20-40%", 20, 40, 25),
    ("40-60%", 40, 60, 18), ("60-80%", 60, 80, 12), ("80-100%", 80, 100, 10),
]

BANK_ACCOUNTS_BUCKETS = [
    ("1", 1, 1, 55), ("2", 2, 2, 30), ("3", 3, 3, 10), ("4+", 4, 6, 5),
]

BANK_BOUNCES_BUCKETS = [
    ("0", 0, 0, 82), ("1", 1, 1, 10), ("2", 2, 2, 4),
    ("3", 3, 3, 2), ("4+", 4, 8, 2),
]

INVESTMENT_ASSETS_BUCKETS = [
    ("NONE", 0, 0, 45), ("<1L", 1, 100_000, 20), ("1-5L", 100_000, 500_000, 18),
    ("5-15L", 500_000, 1_500_000, 9), ("15-50L", 1_500_000, 5_000_000, 5),
    ("50L+", 5_000_000, 20_000_000, 3),
]

INCOME_VERIFICATION_RATIO_BUCKETS = [
    ("<0.50", 0.20, 0.50, 3), ("0.50-0.75", 0.50, 0.75, 7),
    ("0.75-0.90", 0.75, 0.90, 10), ("0.90-1.10", 0.90, 1.10, 55),
    ("1.10-1.25", 1.10, 1.25, 15), ("1.25-1.50", 1.25, 1.50, 7),
    (">1.50", 1.50, 2.20, 3),
]

GST_FILING_CONSISTENCY_BUCKETS = [
    ("0-50%", 0, 50, 3), ("50-75%", 50, 75, 7), ("75-90%", 75, 90, 12),
    ("90-95%", 90, 95, 15), ("95-100%", 95, 100, 63),
]

ALT_UTILITY_ONTIME_BUCKETS = [
    ("<50%", 0, 50, 5), ("50-75%", 50, 75, 10), ("75-90%", 75, 90, 20),
    ("90-100%", 90, 100, 65),
]


# ── sampling helpers ─────────────────────────────────────────────────────────

def weighted_label(dist: list[tuple]) -> str:
    labels = [d[0] for d in dist]
    weights = [d[-1] for d in dist]
    return random.choices(labels, weights=weights, k=1)[0]


def sample_bucket(buckets: list[tuple[str, float, float, float]]) -> tuple[str, float]:
    """pick a bucket by weight, then jitter uniformly within [lo, hi]"""
    labels = [b[0] for b in buckets]
    weights = [b[3] for b in buckets]
    label = random.choices(labels, weights=weights, k=1)[0]
    lo, hi, _ = next((b[1], b[2], b[3]) for b in buckets if b[0] == label)
    value = random.uniform(lo, hi) if hi > lo else float(lo)
    return label, value


def clip(x: float, lo: float, hi: float) -> float:
    return float(np.clip(x, lo, hi))


def random_dob(age: int) -> date:
    approx = REFERENCE_DATE - timedelta(days=age * 365)
    jitter_days = random.randint(-150, 150)
    return approx + timedelta(days=jitter_days)


def generate_gstin(state_code: int) -> str:
    """valid-format synthetic GSTIN for a given state code"""
    pan_letters_a = "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ", k=5))
    pan_digits = "".join(random.choices("0123456789", k=4))
    pan_letter_b = random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    pan_like = pan_letters_a + pan_digits + pan_letter_b
    entity = str(random.randint(1, 9))
    checksum = str(random.randint(0, 9))
    return f"{state_code:02d}{pan_like}{entity}Z{checksum}"
