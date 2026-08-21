"""
NBFC Seed Data — Synthetic Applicant Profile Generator
Phase 1 of the underwriting pipeline: writes chunked Parquet files to data/raw/

Toolchain (mirrors the Airavat / CreditIQ generator, one change):
  - Faker (en_IN) for names / business names / identifiers
  - SDV CopulaGANSynthesizer for cross-field correlated profile synthesis
    (swap-in replacement for GaussianCopulaSynthesizer — same seed → fit → sample
    pattern, but the copula-GAN captures non-Gaussian / multi-modal marginals
    better, which matters for skewed fields like turnover, DPD and utilization)
  - numpy for within-bucket jitter on continuous fields
  - Indian demographic priors (NBFC seed data) drive the *seed* rows that the
    synthesizer is fit on — bucketed distributions for age, geography, income,
    bureau score, DPD, utilization, MSME turnover, etc.
  - Polars DataFrames; Parquet chunks (10 000 rows each) → data/raw/

Applicant-type mix: SALARIED 50%, SELF_EMPLOYED 20%, MSME 27%, CORPORATE 3%
(matches applicant_type_applicability in the input-signal inventory doc)

Run as:  python -m src.ingestion.profile_generator
Outputs:
  data/raw/applicant_profiles_chunk_NNNN.parquet   (normalized applicant profile, one row per applicant)
  data/raw/gst_business_chunk_NNNN.parquet          (conditional — MSME/self-employed/corporate)
"""

from __future__ import annotations

import math
import random
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
from faker import Faker
from tqdm import tqdm
from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme
import logging

# ── logging setup ─────────────────────────────────────────────────────────────

custom_theme = Theme({
    "info": "cyan",
    "warning": "yellow",
    "error": "bold red",
    "success": "bold green",
    "simulation": "magenta",
})

console = Console(theme=custom_theme)
logging.basicConfig(
    level="INFO",
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(console=console, rich_tracebacks=True)],
)
logger = logging.getLogger("nbfc.profile_generator")

# ── global constants ───────────────────────────────────────────────────────────

N_SEED_ROWS: int = 900         # Faker-driven seed rows the synthesizer is fit on
N_PROFILES: int = 100_000         # final synthetic applicant profiles to sample
CHUNK_SIZE: int = 10_000
RAW_DATA_PATH: Path = Path("data/raw")
REFERENCE_DATE: datetime = datetime(2026, 4, 11)

STATE_CODES = [6, 7, 8, 9, 19, 22, 24, 27, 29, 33, 36]
STATE_NAMES = {
    6: "Haryana", 7: "Delhi", 8: "Rajasthan", 9: "Uttar Pradesh",
    19: "West Bengal", 22: "Chhattisgarh", 24: "Gujarat",
    27: "Maharashtra", 29: "Karnataka", 33: "Tamil Nadu", 36: "Telangana",
}

ITR_TYPE_BY_APPLICANT = {
    "SALARIED": ["ITR1", "ITR2"],
    "SELF_EMPLOYED": ["ITR3", "ITR4"],
    "MSME": ["ITR3", ITR4 := "ITR4"],
    "CORPORATE": ["ITR5", "ITR6"],
}

# ── Indian demographic priors (NBFC seed data) ─────────────────────────────────
# Each bucket table is (label, lo, hi, weight_pct). `weight_pct` need not sum to
# exactly 100 — random.choices normalizes weights internally.

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

AIS_ITR_CONSISTENCY_DIST = [
    ("HIGH", 75), ("MINOR_DISCREPANCY", 17), ("MODERATE_DISCREPANCY", 6),
    ("MAJOR_DISCREPANCY", 2),
]

GST_FILING_CONSISTENCY_BUCKETS = [
    ("0-50%", 0, 50, 3), ("50-75%", 50, 75, 7), ("75-90%", 75, 90, 12),
    ("90-95%", 90, 95, 15), ("95-100%", 95, 100, 63),
]

# ── transactional-layer constants (ported from the Airavat/CreditIQ generator) ─
# Same merchant templates, HSN sectors, and bank-handle pool as the original
# generator — only the seeding source changed (driven off applicant-profile
# aggregates rather than persona archetypes).

HISTORY_WINDOW_MONTHS: int = 12   # AA-style feeds are windowed, not full vintage

BANK_HANDLES: list[str] = ["okaxis", "okhdfcbank", "okicici", "oksbi", "ybl", "ibl", "paytm"]

MERCHANT_TEMPLATES: dict[str, list[str]] = {
    "SALARY": [
        "Tech Solutions Payroll Oct", "Monthly Remuneration Trans",
        "Infosys Ltd Salary", "NEFT-HDFC-Employer-Cr",
        "Payroll Disbursement Q3", "Wipro Salary Credit",
        "Tata Consultancy Wages", "HCL Technologies Pay",
    ],
    "BUSINESS_RECEIPT": [
        "Customer Payment NEFT", "Sales Collection UPI", "Distributor Remittance",
        "B2B Invoice Settlement", "Trade Receivable Credit", "Client Payment IMPS",
    ],
    "GROCERY": [
        "Reliance Fresh #44", "Mother Dairy Store", "Green Grocery Kirana",
        "DMart Retail Pvt Ltd", "Big Basket Order 99X", "Local Sabzi Mandi",
        "Spencers Supermart", "More Megastore", "Star Bazaar", "Nilgiris Fresh",
    ],
    "DINING": [
        "Blue Tokai Coffee Gurgaon", "Royal Biryani House", "Zomato-Order-992",
        "Swiggy Restro Deliv", "Dominos Pizza Outlet 12", "Chai Point Cafe",
        "McDonalds Drive Thru", "Burger King BLR", "KFC Outlet HSR Layout", "Barbeque Nation",
    ],
    "TRANSPORT": [
        "Ola Cab Booking", "Uber Technologies India", "BMTC Monthly Pass",
        "Indian Oil Petrol Pump", "Rapido Ride 87", "Metro Rail Recharge",
        "HP Fuel Station", "BPCL Pump Station", "Namma Metro Card Top-up", "IndiGo Flight Booking",
    ],
    "BILLS_UTILITIES": [
        "BSNL Broadband Bill", "Airtel Postpaid Recharge", "BESCOM Electricity Bill",
        "Tata Power Monthly", "Jio Recharge 599", "BWSSB Water Supply",
        "Gas Agency Monthly", "Mahanagar Gas", "MTNL Bill Payment", "Hathway Internet",
    ],
    "HEALTHCARE": [
        "Apollo Pharmacy #32", "Max Healthcare OPD", "Practo Consultation Fee",
        "1mg Medicine Order", "Fortis Lab Test", "Generic Medical Store",
        "Medplus Pharmacy", "Netmeds Order", "Narayana Health Consult", "Thyrocare Lab",
    ],
    "ENTERTAINMENT": [
        "Netflix Subscription", "BookMyShow Tickets", "Hotstar Premium Renewal",
        "Amazon Prime Video", "Spotify Premium Monthly", "PVR Cinemas Booking",
        "SonyLIV Plan", "Zee5 Subscription", "INOX Movie Tickets", "Lionsgate Play",
    ],
    "VENDOR_PAYMENT": [
        "Raw Material Supplier Pay", "Wholesale Purchase NEFT", "Vendor Settlement UPI",
        "Inventory Purchase Bill", "Supplier Advance Payment", "Trade Payable Debit",
    ],
    "TRANSFER": [
        "NEFT Transfer Personal", "IMPS To Friend", "UPI P2P Send",
        "Family Remittance", "Self Account Transfer", "RTGS Payment",
    ],
}

# HSN sector families reused verbatim from the Airavat generator, plus a mapping
# from the NBFC applicant's declared MSME/business sector onto the closest HSN
# families for E-Way Bill item generation.
STATE_CODES_LOCAL = STATE_CODES  # alias kept for readability in eway/gst blocks

VEHICLE_PREFIXES = ["KA", "MH", "DL", "TN", "GJ", "RJ", "UP", "WB"]
DOC_TYPES = ["INV", "INV", "INV", "CHL", "BIL"]

HSN_SECTORS = {
    "iron_steel": ["7201", "7202", "7204", "7207", "7208", "7209", "7210", "7213"],
    "textiles": ["5208", "5209", "5210", "5211", "6001", "6002", "6006", "5201"],
    "food_grains": ["1001", "1002", "1003", "1004", "1005", "1006", "1007", "1008"],
    "chemicals": ["2801", "2802", "2803", "2804", "2901", "2902", "3801", "3802"],
    "machinery": ["8401", "8402", "8403", "8404", "8405", "8406", "8407", "8408"],
    "electronics": ["8501", "8502", "8503", "8504", "8505", "8506", "8507", "8508"],
    "plastics": ["3901", "3902", "3903", "3904", "3905", "3906", "3907", "3908"],
    "paper": ["4801", "4802", "4803", "4804", "4805", "4701", "4702", "4703"],
}
HSN_PRODUCT_MAP = {
    "7201": "pig iron", "7202": "ferro alloys", "7204": "ferrous scrap",
    "7207": "semi-finished steel", "7208": "flat-rolled steel", "7209": "cold-rolled steel",
    "7210": "coated steel", "7213": "steel wire rod", "5208": "woven cotton fabric",
    "5209": "heavy cotton fabric", "5210": "mixed cotton fabric", "5211": "denim fabric",
    "6001": "pile fabric", "6002": "knitted fabric", "6006": "technical fabric",
    "5201": "raw cotton", "1001": "wheat", "1002": "rye", "1003": "barley",
    "1004": "oats", "1005": "maize", "1006": "rice", "1007": "sorghum", "1008": "buckwheat",
    "2801": "fluorine chlorine", "2802": "sulphur", "2803": "carbon black", "2804": "hydrogen",
    "2901": "acyclic hydrocarbons", "2902": "cyclic hydrocarbons", "3801": "artificial graphite",
    "3802": "activated carbon", "8401": "nuclear reactor parts", "8402": "steam boilers",
    "8403": "heating boilers", "8404": "auxiliary plant", "8405": "gas generators",
    "8406": "steam turbines", "8407": "spark ignition engines", "8408": "compression engines",
    "8501": "electric motors", "8502": "generators", "8503": "motor parts",
    "8504": "transformers", "8505": "electromagnets", "8506": "primary cells",
    "8507": "batteries", "8508": "vacuum cleaners", "3901": "polyethylene",
    "3902": "polypropylene", "3903": "polystyrene", "3904": "pvc resin",
    "3905": "polyvinyl acetate", "3906": "acrylic polymers", "3907": "polyacetals",
    "3908": "polyamides", "4801": "newsprint", "4802": "writing paper",
    "4803": "tissue paper", "4804": "kraft paper", "4805": "other paper",
    "4701": "mechanical pulp", "4702": "chemical pulp", "4703": "kraft pulp",
}

# NBFC business_sector (from the demographic priors) → candidate HSN families
SECTOR_TO_HSN_SECTORS: dict[str, list[str]] = {
    "TRADE": ["textiles", "food_grains", "paper", "plastics"],
    "MANUFACTURING": ["iron_steel", "machinery", "electronics", "plastics", "chemicals"],
    "SERVICES": ["electronics", "paper"],
    "CONSTRUCTION": ["iron_steel", "chemicals"],
    "TRANSPORT": ["machinery", "iron_steel"],
    "AGRICULTURE": ["food_grains", "chemicals"],
    "OTHER": list(HSN_SECTORS.keys()),
}

# E-Way Bills per active month, by sector — trade/transport move more physical
# goods than services; matches the sector-weighted volumes in the priors doc.
SECTOR_EWAY_RATE: dict[str, tuple[int, int]] = {
    "TRADE": (2, 6), "MANUFACTURING": (2, 5), "CONSTRUCTION": (1, 3),
    "TRANSPORT": (4, 9), "AGRICULTURE": (1, 3), "SERVICES": (0, 1), "OTHER": (0, 2),
}

# UPI/bank daily transaction-rate priors by applicant type (mirrors the
# persona-aware `_rates` table in the original generator).
APPLICANT_TYPE_TXN_RATES: dict[str, dict[str, float]] = {
    "SALARIED":       {"upi_daily": 2.5, "bank_daily": 1.0, "p2m_weight": 0.65, "inbound_weight": 0.42},
    "SELF_EMPLOYED":  {"upi_daily": 5.0, "bank_daily": 2.0, "p2m_weight": 0.45, "inbound_weight": 0.52},
    "MSME":           {"upi_daily": 6.0, "bank_daily": 3.0, "p2m_weight": 0.35, "inbound_weight": 0.55},
    "CORPORATE":      {"upi_daily": 3.0, "bank_daily": 4.5, "p2m_weight": 0.25, "inbound_weight": 0.55},
}

# Credit-history depth scales transaction *volume*, not just bureau fields —
# NTC/thin-file applicants have shorter, sparser observable transaction history.
CREDIT_HISTORY_VOLUME_MULTIPLIER: dict[str, float] = {
    "NTC": 0.25, "THIN_FILE": 0.55, "ESTABLISHED": 1.0,
}

# Base UPI/bank transaction success rate by credit-history depth (thinner file
# → more failed/bounced attempts observed in the window).
STATUS_SUCCESS_RATE: dict[str, float] = {
    "NTC": 0.90, "THIN_FILE": 0.93, "ESTABLISHED": 0.97,
}


# ── bucket-sampling helpers ─────────────────────────────────────────────────────

def _weighted_label(dist: list[tuple]) -> str:
    labels = [d[0] for d in dist]
    weights = [d[-1] for d in dist]
    return random.choices(labels, weights=weights, k=1)[0]


def sample_bucket(buckets: list[tuple[str, float, float, float]]) -> tuple[str, float]:
    """Pick a bucket by weight, then jitter uniformly within [lo, hi]."""
    labels = [b[0] for b in buckets]
    weights = [b[3] for b in buckets]
    label = random.choices(labels, weights=weights, k=1)[0]
    lo, hi, _ = next((b[1], b[2], b[3]) for b in buckets if b[0] == label)
    value = random.uniform(lo, hi) if hi > lo else float(lo)
    return label, value


def _clip(x: float, lo: float, hi: float) -> float:
    return float(np.clip(x, lo, hi))


# ── seed profile construction (Faker + demographic priors) ─────────────────────

def _random_dob(age: int) -> date:
    years_back = age
    approx = REFERENCE_DATE.date() - timedelta(days=years_back * 365)
    jitter_days = random.randint(-150, 150)
    return approx + timedelta(days=jitter_days)


def build_seed_profiles(fake: Faker, n_rows: int = N_SEED_ROWS) -> list[dict]:
    """
    Build Faker + demographic-prior seed rows. These are the "archetype"
    rows the CopulaGAN synthesizer is fit on — analogous to the small
    per-persona seed_df in the original generator, except every row here
    is drawn directly from the Indian NBFC distribution priors rather than
    from a handful of hand-coded archetypes.
    """
    logger.info(f"[seed] building {n_rows} Faker-driven seed profiles from demographic priors")
    rows: list[dict] = []

    for i in tqdm(range(n_rows), desc="Seed profiles", unit="row"):
        applicant_type = _weighted_label(APPLICANT_TYPE_DIST)
        geography = _weighted_label(GEOGRAPHY_DIST)
        _, age_f = sample_bucket(AGE_BUCKETS)
        age = int(round(age_f))
        credit_history = _weighted_label(CREDIT_HISTORY_DIST)
        state_code = random.choice(STATE_CODES)

        is_business = applicant_type in ("MSME", "CORPORATE")
        is_self_or_msme = applicant_type in ("SELF_EMPLOYED", "MSME")

        # ── income ──
        if applicant_type == "SALARIED":
            _, declared_income = sample_bucket(SALARIED_INCOME_BUCKETS)
            employment_type = "SALARIED"
        elif applicant_type == "SELF_EMPLOYED":
            _, declared_income = sample_bucket(SELF_EMPLOYED_INCOME_BUCKETS)
            employment_type = "SELF_EMPLOYED"
        else:
            # MSME / CORPORATE — derive a monthly-income proxy from annual turnover
            _, turnover = sample_bucket(MSME_TURNOVER_BUCKETS)
            declared_income = _clip(turnover * random.uniform(0.02, 0.06) / 12, 10_000, 2_000_000)
            employment_type = "BUSINESS_OWNER"

        # ── credit-history-gated bureau depth ──
        if credit_history == "NTC":
            bureau_score = None
            active_loans = 0
            closed_loans = 0
            enquiries_30d_label, enquiries_30d = "0", 0
            max_dpd_label, max_dpd = "0", 0
            cc_util_label, cc_util = "0%", 0
        else:
            _, bureau_score_f = sample_bucket(BUREAU_SCORE_BUCKETS)
            bureau_score = int(round(bureau_score_f))
            _, active_loans_f = sample_bucket(ACTIVE_LOANS_BUCKETS)
            active_loans = int(round(active_loans_f))
            closed_loans = max(0, int(round(active_loans * random.uniform(0.3, 1.5))))
            if credit_history == "THIN_FILE":
                active_loans = min(active_loans, 1)
                closed_loans = min(closed_loans, 1)
            enquiries_30d_label, enq_f = sample_bucket(ENQUIRY_30D_BUCKETS)
            enquiries_30d = int(round(enq_f))
            max_dpd_label, max_dpd_f = sample_bucket(MAX_DPD_BUCKETS)
            max_dpd = int(round(max_dpd_f))
            cc_util_label, cc_util_f = sample_bucket(CC_UTILIZATION_BUCKETS)
            cc_util = _clip(cc_util_f, 0, 100)

        write_off_flag = max_dpd_label == "SERIOUS_WRITEOFF" and random.random() < 0.6
        settlement_flag = max_dpd_label == "SERIOUS_WRITEOFF" and not write_off_flag and random.random() < 0.5
        default_flag = write_off_flag or (max_dpd_label == "181+" and random.random() < 0.3)
        suit_filed_flag = write_off_flag and random.random() < 0.15

        enquiries_90d = enquiries_30d + int(np.random.poisson(1.2))
        enquiries_180d = enquiries_90d + int(np.random.poisson(1.5))

        overdue_amount = 0.0
        if max_dpd > 0:
            overdue_amount = round(declared_income * random.uniform(0.1, 1.2), 2)

        total_sanctioned = round((active_loans + closed_loans) * declared_income * random.uniform(2, 8), 2)
        total_outstanding = round(total_sanctioned * random.uniform(0.1, 0.7), 2)
        secured_loan_count = int(round(active_loans * random.uniform(0.0, 0.6)))
        unsecured_loan_count = max(0, active_loans - secured_loan_count)
        dpd_recency_months = 0 if max_dpd == 0 else random.randint(0, 24)

        # ── banking (AA) ──
        _, n_accounts_f = sample_bucket(BANK_ACCOUNTS_BUCKETS)
        n_accounts = int(round(n_accounts_f))
        _, bounces_f = sample_bucket(BANK_BOUNCES_BUCKETS)
        bounces = int(round(bounces_f))
        avg_credit_inflow = round(declared_income * random.uniform(0.9, 1.3), 2)
        avg_debit_outflow = round(avg_credit_inflow * random.uniform(0.6, 1.05), 2)
        avg_balance = round(declared_income * random.uniform(0.3, 2.5), 2)
        min_balance = round(avg_balance * random.uniform(0.05, 0.4), 2)
        current_balance = round(avg_balance * random.uniform(0.5, 1.5), 2)
        cash_flow_volatility = round(declared_income * random.uniform(0.02, 0.35), 2)
        inflow_trend = random.choices(["GROWING", "FLAT", "DECLINING"], weights=[0.35, 0.45, 0.20])[0]
        emi_like_recurring = round(declared_income * random.uniform(0.0, 0.45), 2)
        upi_inflow = round(avg_credit_inflow * random.uniform(0.1, 0.6), 2)
        upi_outflow = round(avg_debit_outflow * random.uniform(0.2, 0.7), 2)
        overdraft_occurrences = int(np.random.poisson(0.3)) if bounces > 0 else 0

        # ── ITR / AIS / income verification ──
        _, iv_ratio = sample_bucket(INCOME_VERIFICATION_RATIO_BUCKETS)
        verified_income_annual = round(declared_income * 12 * iv_ratio, 2)
        declared_income_annual = round(declared_income * 12, 2)
        ais_consistency = _weighted_label(AIS_ITR_CONSISTENCY_DIST)
        itr_type = random.choice(ITR_TYPE_BY_APPLICANT[applicant_type])
        income_fy1 = verified_income_annual
        income_fy2 = round(income_fy1 * random.uniform(0.75, 1.15), 2)

        # ── investment assets ──
        _, investable = sample_bucket(INVESTMENT_ASSETS_BUCKETS)
        mutual_fund_value = round(investable * random.uniform(0.2, 0.6), 2)
        equity_value = round(investable * random.uniform(0.0, 0.4), 2)
        liquid_asset_value = round(investable * random.uniform(0.1, 0.5), 2)
        total_portfolio_value = round(mutual_fund_value + equity_value + liquid_asset_value, 2)

        # ── requested loan ──
        if is_business:
            _, requested_amount = sample_bucket(MSME_LOAN_AMOUNT_BUCKETS)
            requested_tenure = random.randint(12, 84)
        else:
            _, requested_amount = sample_bucket(PERSONAL_LOAN_AMOUNT_BUCKETS)
            _, tenure_f = sample_bucket(PERSONAL_LOAN_TENURE_BUCKETS)
            requested_tenure = int(round(tenure_f))

        declared_obligations = round(declared_income * random.uniform(0.0, 0.5), 2)

        row: dict = {
            # identity / master
            "applicant_id": f"APP{i:06d}",
            "name": fake.name(),
            "email": fake.email(),
            "pan": fake.bothify(text="?????####?").upper(),
            "date_of_birth": _random_dob(age).isoformat(),
            "age": age,
            "applicant_type": applicant_type,
            "employment_type": employment_type,
            "geography_type": geography,
            "state_code": state_code,
            "state_name": STATE_NAMES.get(state_code, "Other"),
            "city": fake.city(),
            "residential_status": random.choices(["RESIDENT", "NRI", "OTHER"], weights=[0.94, 0.04, 0.02])[0],
            "employment_vintage_months": random.randint(1, 240) if applicant_type == "SALARIED" else 0,
            "business_vintage_months": random.randint(1, 300) if is_business or applicant_type == "SELF_EMPLOYED" else 0,
            "declared_income_monthly": round(declared_income, 2),
            "declared_existing_obligations": declared_obligations,
            "income_source": {"SALARIED": "SALARY", "SELF_EMPLOYED": "PROFESSIONAL",
                               "MSME": "BUSINESS", "CORPORATE": "BUSINESS"}[applicant_type],
            "requested_loan_amount": round(requested_amount, 2),
            "requested_tenure_months": requested_tenure,
            "credit_history_type": credit_history,

            # bureau
            "bureau_score": bureau_score,
            "active_loan_count": active_loans,
            "closed_loan_count": closed_loans,
            "total_sanctioned_amount": total_sanctioned,
            "total_outstanding_amount": total_outstanding,
            "secured_loan_count": secured_loan_count,
            "unsecured_loan_count": unsecured_loan_count,
            "recent_enquiry_count_30d": enquiries_30d,
            "recent_enquiry_count_90d": enquiries_90d,
            "recent_enquiry_count_180d": enquiries_180d,
            "overdue_amount": overdue_amount,
            "max_dpd": max_dpd,
            "dpd_recency_months": dpd_recency_months,
            "write_off_flag": bool(write_off_flag),
            "write_off_amount": round(total_outstanding * 0.4, 2) if write_off_flag else 0.0,
            "settlement_flag": bool(settlement_flag),
            "settlement_amount": round(total_outstanding * 0.25, 2) if settlement_flag else 0.0,
            "default_flag": bool(default_flag),
            "suit_filed_flag": bool(suit_filed_flag),
            "credit_card_utilization": round(cc_util, 2),

            # banking / AA
            "num_accounts": n_accounts,
            "account_type": random.choices(["SAVINGS", "CURRENT", "SALARY"],
                                            weights=[0.55, 0.25, 0.20] if not is_business else [0.15, 0.80, 0.05])[0],
            "current_balance": current_balance,
            "average_balance": avg_balance,
            "minimum_balance": min_balance,
            "avg_monthly_credit_inflow": avg_credit_inflow,
            "avg_monthly_debit_outflow": avg_debit_outflow,
            "inflow_trend": inflow_trend,
            "emi_like_recurring_debits": emi_like_recurring,
            "salary_credit_detected": applicant_type == "SALARIED",
            "business_credit_detected": is_business or applicant_type == "SELF_EMPLOYED",
            "upi_inflow": upi_inflow,
            "upi_outflow": upi_outflow,
            "bounce_count": bounces,
            "overdraft_occurrence_count": overdraft_occurrences,
            "cash_flow_volatility": cash_flow_volatility,

            # ITR / AIS
            "itr_type": itr_type,
            "gross_total_income": declared_income_annual,
            "total_income": verified_income_annual,
            "income_fy1": income_fy1,
            "income_fy2": income_fy2,
            "income_verification_ratio": round(iv_ratio, 3),
            "ais_itr_consistency": ais_consistency,

            # assets
            "mutual_fund_value": mutual_fund_value,
            "equity_value": equity_value,
            "liquid_asset_value": liquid_asset_value,
            "total_portfolio_value": total_portfolio_value,

            "created_at": REFERENCE_DATE.isoformat(),
        }

        # ── conditional GST block (MSME / self-employed / corporate) ──
        if is_business or applicant_type == "SELF_EMPLOYED":
            constitution = ("PROPRIETORSHIP" if applicant_type == "SELF_EMPLOYED"
                             else _weighted_label(MSME_CONSTITUTION_DIST))
            sector = _weighted_label(MSME_SECTOR_DIST)
            _, turnover = sample_bucket(MSME_TURNOVER_BUCKETS) if is_business else (None, declared_income_annual * random.uniform(1.1, 2.0))
            _, filing_consistency = sample_bucket(GST_FILING_CONSISTENCY_BUCKETS)
            row.update({
                "gstin": generate_gstin(state_code, fake),
                "business_name": fake.company(),
                "business_constitution": constitution,
                "business_sector": sector,
                "turnover": round(turnover, 2),
                "taxable_turnover": round(turnover * random.uniform(0.85, 0.99), 2),
                "gst_filing_consistency_percent": round(filing_consistency, 2),
                "turnover_trend": random.choices(["GROWING", "FLAT", "DECLINING"], weights=[0.4, 0.4, 0.2])[0],
            })
        else:
            row.update({
                "gstin": None, "business_name": None, "business_constitution": None,
                "business_sector": None, "turnover": 0.0, "taxable_turnover": 0.0,
                "gst_filing_consistency_percent": None, "turnover_trend": None,
            })

        rows.append(row)

    return rows


def generate_gstin(state_code: int, fake: Faker) -> str:
    """Valid-format synthetic GSTIN for a given state code."""
    pan_letters_a = "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ", k=5))
    pan_digits = "".join(random.choices("0123456789", k=4))
    pan_letter_b = random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    pan_like = pan_letters_a + pan_digits + pan_letter_b
    entity = str(random.randint(1, 9))
    checksum = str(random.randint(0, 9))
    return f"{state_code:02d}{pan_like}{entity}Z{checksum}"


def generate_vehicle_no() -> str:
    """Synthetic Indian vehicle registration number (for E-Way Bill transMode=1)."""
    prefix = random.choice(VEHICLE_PREFIXES)
    district = f"{random.randint(10, 99)}"
    series = "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ", k=2))
    number = f"{random.randint(1000, 9999)}"
    return f"{prefix}{district}{series}{number}"


def _gen_upi_id(name: str, applicant_id: str) -> str:
    slug = name.lower().replace(" ", "").replace(".", "")[:10]
    return f"{slug}{applicant_id[-3:]}@{random.choice(BANK_HANDLES)}"


def _pick(category: str) -> str:
    return random.choice(MERCHANT_TEMPLATES.get(category, ["Unknown"]))


def _lognormal(mu: float, sigma: float, lo: float = 10.0, hi: float = 5_000_000.0) -> float:
    return float(np.clip(np.random.lognormal(mu, sigma), lo, hi))


def _active_period(active_months: int) -> tuple[datetime, datetime]:
    """AA-style feeds are windowed to the trailing HISTORY_WINDOW_MONTHS, not
    the applicant's full vintage — mirrors real bureau/AA data-pull behaviour."""
    end_dt = REFERENCE_DATE
    start_dt = end_dt - timedelta(days=max(active_months, 1) * 30)
    return start_dt, end_dt


def _sample_timestamps(start: datetime, end: datetime, n: int, burst: bool = False) -> list[datetime]:
    """
    Exponential inter-arrivals (genuine behaviour) or Gaussian burst clusters
    (used sparingly here — kept for parity with the original generator's
    sampling primitive; NBFC applicants default to burst=False).
    """
    if n <= 0:
        return []
    total_seconds = (end - start).total_seconds()

    if burst:
        n_bursts = random.randint(2, 3)
        centers = sorted(random.uniform(0.15, 0.85) * total_seconds for _ in range(n_bursts))
        burst_width = total_seconds * 0.04
        ts_list: list[datetime] = []
        for k in range(n):
            center = centers[k % n_bursts]
            offset = center + random.gauss(0, burst_width)
            offset = max(0.0, min(total_seconds - 1.0, offset))
            ts_list.append(start + timedelta(seconds=offset))
        return sorted(ts_list)

    intervals = np.random.exponential(scale=total_seconds / max(n, 1), size=n)
    cumulative = np.cumsum(intervals)
    if cumulative[-1] > 0:
        cumulative = cumulative * (total_seconds / cumulative[-1])
    return sorted(start + timedelta(seconds=float(s)) for s in cumulative)


# ── CopulaGAN synthesis (replaces GaussianCopulaSynthesizer) ───────────────────

# Columns the synthesizer is fit on. Free-text / identifier columns (name, pan,
# gstin, business_name, city, applicant_id, dates) are deliberately excluded —
# a GAN will happily "learn" a PAN-shaped string, but it isn't a statistically
# meaningful field to model, and re-attaching fresh Faker identifiers after
# sampling keeps every row unique and well-formed.
_IDENTIFIER_COLS = [
    "applicant_id", "name", "pan", "gstin", "business_name", "city",
    "date_of_birth", "created_at", "state_name",
]

_CATEGORICAL_COLS = [
    "applicant_type", "employment_type", "geography_type", "residential_status",
    "income_source", "credit_history_type", "account_type", "inflow_trend",
    "itr_type", "ais_itr_consistency", "business_constitution", "business_sector",
    "turnover_trend", "write_off_flag", "settlement_flag", "default_flag",
    "suit_filed_flag", "salary_credit_detected", "business_credit_detected",
    "state_code",
]


def build_profiles_copulagan(fake: Faker, n_profiles: int = N_PROFILES,
                              n_seed_rows: int = N_SEED_ROWS) -> list[dict]:
    """
    Faker + demographic-prior seed rows → SDV CopulaGANSynthesizer → n_profiles
    correlated synthetic applicant profiles. Falls back to sampling directly
    from the seed rows (no GAN) if SDV isn't installed.
    """
    try:
        import pandas as pd
        from sdv.single_table import CopulaGANSynthesizer
        from sdv.metadata import Metadata
        return _build_profiles_copulagan_sdv(
            fake, n_profiles, n_seed_rows, pd, CopulaGANSynthesizer, Metadata
        )
    except ImportError:
        logger.warning("[generator] sdv not available — falling back to resampling seed rows")
        return _build_profiles_manual(fake, n_profiles, n_seed_rows)


def _build_profiles_copulagan_sdv(fake, n_profiles, n_seed_rows, pd,
                                   CopulaGANSynthesizer, Metadata) -> list[dict]:
    logger.info("[generator] building profiles with SDV CopulaGAN synthesis")

    seed_rows = build_seed_profiles(fake, n_rows=n_seed_rows)
    seed_df_full = pd.DataFrame(seed_rows)

    model_cols = [c for c in seed_df_full.columns if c not in _IDENTIFIER_COLS]
    seed_df = seed_df_full[model_cols].copy()

    # Nulls (e.g. bureau_score for NTC applicants, GST fields for salaried) are
    # sentinel-filled for GAN training and mapped back to null post-sample.
    numeric_cols = seed_df.select_dtypes(include=["number", "bool"]).columns.tolist()
    numeric_cols = [c for c in numeric_cols if c not in _CATEGORICAL_COLS]
    for c in numeric_cols:
        seed_df[c] = seed_df[c].fillna(-1)
    for c in _CATEGORICAL_COLS:
        if c in seed_df.columns:
            seed_df[c] = seed_df[c].astype(str).fillna("NA")

    metadata = Metadata()
    metadata.detect_table_from_dataframe(table_name="applicant_profiles", data=seed_df)
    for c in _CATEGORICAL_COLS:
        if c in seed_df.columns:
            metadata.update_column(column_name=c, table_name="applicant_profiles", sdtype="categorical")

    import joblib
    with joblib.parallel_backend("threading"):
        synth = CopulaGANSynthesizer(metadata, epochs=100)
        synth.fit(seed_df)
        sampled = synth.sample(num_rows=n_profiles)
    logger.info(f"[generator] CopulaGAN sampled {len(sampled)} profiles")

    # undo sentinel fill
    for c in numeric_cols:
        sampled[c] = sampled[c].where(sampled[c] > -1, None)

    profiles: list[dict] = []
    for idx, row in sampled.iterrows():
        d = row.to_dict()
        applicant_type = str(d.get("applicant_type", "SALARIED"))
        is_business_or_se = applicant_type in ("MSME", "CORPORATE", "SELF_EMPLOYED")

        name = fake.name()
        d["applicant_id"] = f"APP{idx:06d}"
        d["name"] = name
        d["pan"] = fake.bothify(text="?????####?").upper()
        d["city"] = fake.city()
        state_code = int(float(d.get("state_code", random.choice(STATE_CODES))))
        if state_code not in STATE_CODES:
            state_code = random.choice(STATE_CODES)
        d["state_code"] = state_code
        d["state_name"] = STATE_NAMES.get(state_code, "Other")
        d["created_at"] = REFERENCE_DATE.isoformat()
        age = int(_clip(float(d.get("age", 30)), 18, 75))
        d["age"] = age
        d["date_of_birth"] = _random_dob(age).isoformat()

        if is_business_or_se:
            d["gstin"] = generate_gstin(state_code, fake)
            d["business_name"] = fake.company()
        else:
            d["gstin"] = None
            d["business_name"] = None

        # re-clip a few fields the GAN can push slightly out of valid range
        bureau_score = d.get("bureau_score")

        if bureau_score is not None and not math.isnan(float(bureau_score)):
            d["bureau_score"] = int(_clip(float(bureau_score), 300, 900))
        else:
            d["bureau_score"] = None
        d["credit_card_utilization"] = _clip(float(d.get("credit_card_utilization", 0) or 0), 0, 100)
        if d.get("gst_filing_consistency_percent") is not None and is_business_or_se:
            d["gst_filing_consistency_percent"] = _clip(float(d["gst_filing_consistency_percent"]), 0, 100)
        d["income_verification_ratio"] = _clip(float(d.get("income_verification_ratio", 1.0) or 1.0), 0.1, 2.5)

        for flag in ("write_off_flag", "settlement_flag", "default_flag", "suit_filed_flag",
                     "salary_credit_detected", "business_credit_detected"):
            d[flag] = str(d.get(flag)).lower() in ("true", "1", "1.0", "yes")

        profiles.append(d)

    return profiles


def _build_profiles_manual(fake: Faker, n_profiles: int = N_PROFILES,
                            n_seed_rows: int = N_SEED_ROWS) -> list[dict]:
    """Fallback: directly sample fresh rows from the demographic priors (no GAN)."""
    logger.info(f"[generator] building {n_profiles} profiles directly from priors (no SDV)")
    return build_seed_profiles(fake, n_rows=n_profiles)


# ── GST invoices (conditional business source, reused/adapted) ─────────────────

def generate_gst_invoices(profiles: list[dict], fake: Faker) -> pl.DataFrame:
    """GST invoice records for MSME/self-employed/corporate applicants only."""
    business_profiles = [p for p in profiles if p.get("gstin")]
    all_gstins = [p["gstin"] for p in business_profiles]
    records: list[dict] = []

    logger.info("[generator] generating GST invoices for business applicants")
    for p in tqdm(business_profiles, desc="GST invoices", unit="applicant"):
        vintage_months = max(1, int(p.get("business_vintage_months") or 12))
        n_invoices = min(vintage_months, 36) * random.randint(1, 4)
        end_dt = REFERENCE_DATE
        start_dt = end_dt - timedelta(days=min(vintage_months, 36) * 30)
        turnover = float(p.get("turnover") or 500_000)
        mean_invoice = max(5_000, turnover / max(n_invoices, 1))

        filing_pct_raw = p.get("gst_filing_consistency_percent")
        try:
            filing_pct = float(filing_pct_raw)
            if np.isnan(filing_pct):
                filing_pct = 90.0
        except (TypeError, ValueError):
            filing_pct = 90.0

        ontime_w = filing_pct / 100
        weights = [ontime_w, max(0.0, 1 - ontime_w) * 0.7, max(0.0, 1 - ontime_w) * 0.3]

        for _ in range(n_invoices):
            ts = start_dt + timedelta(seconds=random.uniform(0, (end_dt - start_dt).total_seconds()))
            taxable_value = round(float(np.random.lognormal(np.log(max(mean_invoice, 1)), 0.6)), 2)
            gst_amount = round(taxable_value * 0.18, 2)
            buyer_gstin = "URP" if random.random() < 0.25 else random.choice(
                [g for g in all_gstins if g != p["gstin"]] or ["URP"]
            )
            filing_status = random.choices(["ontime", "delayed", "missing"], weights=weights)[0]
            filing_delay_days = 0 if filing_status == "ontime" else int(
                np.random.exponential(scale=12 if filing_status == "delayed" else 30)
            )
            records.append({
                "applicant_id": p["applicant_id"],
                "gstin": p["gstin"],
                "invoice_id": f"INV{int(ts.timestamp() * 1000)}",
                "timestamp": ts.isoformat(),
                "taxable_value": taxable_value,
                "gst_amount": gst_amount,
                "buyer_gstin": buyer_gstin,
                "filing_status": filing_status,
                "filing_delay_days": filing_delay_days,
                "synthetic_batch_id": "batch_001",
            })

    df = pl.DataFrame(records)
    logger.info(f"[generator] GST invoices: {len(df):,} rows")
    return df


# ── bank transactions (AA-style, windowed to HISTORY_WINDOW_MONTHS) ────────────

def generate_bank_transactions(profiles: list[dict]) -> pl.DataFrame:
    """
    Per-applicant bank-statement events. Salary/business credits land monthly;
    expense debits are sampled with exponential inter-arrivals and sized so
    their aggregate roughly reproduces the applicant's own
    avg_monthly_credit_inflow / avg_monthly_debit_outflow — i.e. the
    transaction layer is seeded FROM the profile's own AA summary fields
    rather than from a separate persona table.
    """
    records: list[dict] = []
    logger.info("[generator] generating bank transactions")

    for p in tqdm(profiles, desc="Bank Simulation", unit="applicant"):
        applicant_type = p["applicant_type"]
        credit_history = p["credit_history_type"]
        vintage = int(p.get("employment_vintage_months") or p.get("business_vintage_months") or 6)
        active_months = max(1, min(vintage, HISTORY_WINDOW_MONTHS))
        start_dt, end_dt = _active_period(active_months)

        rates = APPLICANT_TYPE_TXN_RATES.get(applicant_type, APPLICANT_TYPE_TXN_RATES["SALARIED"])
        vol_mult = CREDIT_HISTORY_VOLUME_MULTIPLIER.get(credit_history, 1.0)
        success_rate = STATUS_SUCCESS_RATE.get(credit_history, 0.95)

        income = float(p.get("declared_income_monthly") or 20_000)
        avg_credit_inflow = float(p.get("avg_monthly_credit_inflow") or income)
        avg_debit_outflow = float(p.get("avg_monthly_debit_outflow") or income * 0.8)
        bounce_count = int(p.get("bounce_count") or 0)
        balance = float(p.get("current_balance") or income)

        # ── monthly salary / business credit ──
        if p.get("salary_credit_detected") or p.get("business_credit_detected"):
            cur = start_dt.replace(day=min(random.randint(1, 5), 28))
            cat = "SALARY" if p.get("salary_credit_detected") else "BUSINESS_RECEIPT"
            months_done = 0
            while cur < end_dt and months_done < active_months:
                credit_amt = round(avg_credit_inflow * random.uniform(0.90, 1.08), 2)
                balance += credit_amt
                records.append({
                    "applicant_id": p["applicant_id"],
                    "event_id": f"BTX{p['applicant_id']}{months_done:02d}",
                    "timestamp": (cur + timedelta(hours=random.uniform(9, 12))).isoformat(),
                    "amount": credit_amt,
                    "merchant_name": _pick(cat),
                    "channel": "BANK_TRANSFER",
                    "balance_after": round(balance, 2),
                    "reference_id": f"CR{cur.strftime('%Y%m')}",
                    "source_provenance": "bank_api",
                    "status": "SUCCESS",
                    "recurrence_flag": False,
                })
                months_done += 1
                cur = (cur.replace(year=cur.year + 1, month=1) if cur.month == 12
                       else cur.replace(month=cur.month + 1))

        # ── expense debits ──
        n_exp = max(1, int(active_months * rates["bank_daily"] * 30 * vol_mult / 10))
        timestamps = _sample_timestamps(start_dt, end_dt, n_exp)
        mean_txn_amt = max(50.0, avg_debit_outflow / max(n_exp / max(active_months, 1), 1))
        exp_cats = ["GROCERY", "BILLS_UTILITIES", "TRANSPORT", "HEALTHCARE", "DINING",
                    "ENTERTAINMENT"] if applicant_type == "SALARIED" else \
                   ["VENDOR_PAYMENT", "BILLS_UTILITIES", "TRANSPORT", "GROCERY"]

        # bounce_count observed over the window sets the failure rate directly
        fail_rate = _clip((bounce_count / max(active_months, 1)) * 0.6 + (1 - success_rate) * 0.5, 0.01, 0.35)

        for ts in timestamps:
            cat = random.choice(exp_cats)
            amt = _lognormal(math.log(mean_txn_amt), 0.7, 50, 500_000)
            balance -= amt
            status = random.choices(["SUCCESS", "FAILED"], weights=[1 - fail_rate, fail_rate])[0]
            records.append({
                "applicant_id": p["applicant_id"],
                "event_id": f"BTX{p['applicant_id']}{len(records):04d}",
                "timestamp": ts.isoformat(),
                "amount": -round(amt, 2),
                "merchant_name": _pick(cat),
                "channel": random.choices(["CARD", "BANK_TRANSFER"], weights=[0.55, 0.45])[0],
                "balance_after": round(max(balance, 0), 2),
                "reference_id": f"TXN{random.randint(10_000_000, 99_999_999)}",
                "source_provenance": "bank_api",
                "status": status,
                "recurrence_flag": False,
            })

    df = pl.DataFrame(records)
    logger.info(f"[generator] bank transactions: {len(df):,} rows")
    return df


# ── UPI transactions (P2P/P2M mix seeded off applicant_type + AA aggregates) ───

def generate_upi_transactions(profiles: list[dict]) -> pl.DataFrame:
    """
    P2M/P2P mix, direction, and volume are seeded off:
      - APPLICANT_TYPE_TXN_RATES (p2m_weight / inbound_weight / upi_daily rate)
      - the applicant's own upi_inflow / upi_outflow AA aggregates for sizing
      - CREDIT_HISTORY_VOLUME_MULTIPLIER + STATUS_SUCCESS_RATE for volume/quality
    Same seeding pattern as the original persona-keyed generator, re-keyed to
    real per-applicant fields instead of five hardcoded personas.
    """
    records: list[dict] = []
    logger.info("[generator] generating UPI transactions")

    upi_ids = {p["applicant_id"]: _gen_upi_id(p["name"], p["applicant_id"]) for p in profiles}
    all_upi_ids = list(upi_ids.values())

    for p in tqdm(profiles, desc="UPI Simulation", unit="applicant"):
        applicant_type = p["applicant_type"]
        credit_history = p["credit_history_type"]
        vintage = int(p.get("employment_vintage_months") or p.get("business_vintage_months") or 6)
        active_months = max(1, min(vintage, HISTORY_WINDOW_MONTHS))
        start_dt, end_dt = _active_period(active_months)

        rates = APPLICANT_TYPE_TXN_RATES.get(applicant_type, APPLICANT_TYPE_TXN_RATES["SALARIED"])
        vol_mult = CREDIT_HISTORY_VOLUME_MULTIPLIER.get(credit_history, 1.0)
        success_rate = STATUS_SUCCESS_RATE.get(credit_history, 0.95)

        n_txns = max(1, int(active_months * rates["upi_daily"] * 30 * vol_mult / 6))
        timestamps = _sample_timestamps(start_dt, end_dt, n_txns)

        upi_inflow = float(p.get("upi_inflow") or p.get("declared_income_monthly", 20_000) * 0.3)
        upi_outflow = float(p.get("upi_outflow") or p.get("declared_income_monthly", 20_000) * 0.4)
        p2m_weight = rates["p2m_weight"]
        inbound_weight = rates["inbound_weight"]
        my_upi_id = upi_ids[p["applicant_id"]]

        fail_weight = 1 - success_rate
        sw = [success_rate, fail_weight * 0.6, fail_weight * 0.4]  # success / failed_technical / failed_funds

        for ts in timestamps:
            direction = random.choices(["inbound", "outbound"], weights=[inbound_weight, 1 - inbound_weight])[0]
            mean_amt = max(10.0, (upi_inflow if direction == "inbound" else upi_outflow)
                           / max(n_txns / max(active_months, 1), 1))
            amt = _lognormal(math.log(mean_amt), 0.8, 10, 500_000)

            txn_type = random.choices(["p2m", "p2p"], weights=[p2m_weight, 1 - p2m_weight])[0]
            if txn_type == "p2m":
                cat = random.choices(
                    ["GROCERY", "DINING", "TRANSPORT", "ENTERTAINMENT", "BILLS_UTILITIES",
                     "VENDOR_PAYMENT"],
                    weights=[0.20, 0.15, 0.15, 0.10, 0.20, 0.20]
                )[0]
                counterparty = f"{cat.lower()}@{random.choice(BANK_HANDLES)}"
            else:
                cat = "TRANSFER"
                counterparty = random.choice([u for u in all_upi_ids if u != my_upi_id] or [my_upi_id])

            status = random.choices(["success", "failed_technical", "failed_funds"], weights=sw)[0]
            signed_amt = round(amt if direction == "inbound" else -amt, 2)

            records.append({
                "applicant_id": p["applicant_id"],
                "event_id": f"UTX{p['applicant_id']}{len(records):04d}",
                "timestamp": ts.isoformat(),
                "amount": signed_amt,
                "direction": direction,
                "merchant_name": _pick(cat),
                "txn_type": txn_type,
                "vpa": my_upi_id,
                "counterparty_upi": counterparty,
                "status": status,
                "source_provenance": "upi_api",
                "recurrence_flag": False,
            })

    df = pl.DataFrame(records)
    logger.info(f"[generator] UPI transactions: {len(df):,} rows")
    return df


# ── E-Way Bills (conditional business source: MSME / self-employed / corporate) ─

def generate_eway_bills(profiles: list[dict], fake: Faker) -> pl.DataFrame:
    """
    Official-schema E-Way Bill records (same field structure as the original
    generator) for applicants with a GSTIN. Bill volume, item HSN, and
    transport distance are seeded off business_sector via SECTOR_EWAY_RATE /
    SECTOR_TO_HSN_SECTORS, and taxable value off the applicant's own turnover
    — mirroring the original's paper-trader/shell-style seeding but keyed to
    real declared business attributes instead of five fixed personas.
    """
    business_profiles = [p for p in profiles if p.get("gstin")]
    all_gstins = [p["gstin"] for p in business_profiles]
    gstin_to_state = {p["gstin"]: p["state_code"] for p in business_profiles}
    gstin_to_name = {p["gstin"]: p["business_name"] for p in business_profiles}

    records: list[dict] = []
    logger.info("[generator] generating E-Way Bills for business applicants")

    for p in tqdm(business_profiles, desc="E-Way Bills", unit="applicant"):
        sector = p.get("business_sector") or "OTHER"
        vintage = int(p.get("business_vintage_months") or 12)
        active_months = max(1, min(vintage, HISTORY_WINDOW_MONTHS))
        start_dt, end_dt = _active_period(active_months)

        lo, hi = SECTOR_EWAY_RATE.get(sector, (0, 2))
        n_bills = active_months * random.randint(lo, hi)
        if n_bills == 0:
            continue

        timestamps = _sample_timestamps(start_dt, end_dt, n_bills)
        turnover = float(p.get("turnover") or 1_000_000)
        mean_txbl = max(1_000.0, turnover / max(n_bills * random.uniform(3, 8), 1))

        candidate_hsn_sectors = SECTOR_TO_HSN_SECTORS.get(sector, list(HSN_SECTORS.keys()))
        is_transport = sector == "TRANSPORT"

        for ts in timestamps:
            chosen_sector = (random.choice(list(HSN_SECTORS.keys())) if random.random() < 0.10
                              else random.choice(candidate_hsn_sectors))
            hsn_code = random.choice(HSN_SECTORS[chosen_sector])
            product_name = HSN_PRODUCT_MAP.get(hsn_code, "goods")

            if random.random() < 0.20:
                to_gstin, to_trd_name, to_state_code = "URP", "unregistered person", p["state_code"]
            else:
                others = [g for g in all_gstins if g != p["gstin"]]
                to_gstin = random.choice(others) if others else "URP"
                to_trd_name = gstin_to_name.get(to_gstin, "buyer")
                to_state_code = gstin_to_state.get(to_gstin, p["state_code"])

            intra_state = p["state_code"] == to_state_code
            trans_mode = random.choices([1, 2, 3, 4], weights=[0.70, 0.20, 0.05, 0.05])[0]
            trans_distance = random.randint(200, 2500) if is_transport else random.randint(20, 1500)

            taxable_amount = round(float(np.random.lognormal(math.log(mean_txbl), 0.6)), 2)
            cgst_value = round(taxable_amount * 0.09, 2) if intra_state else 0.0
            sgst_value = round(taxable_amount * 0.09, 2) if intra_state else 0.0
            igst_value = 0.0 if intra_state else round(taxable_amount * 0.18, 2)
            tot_inv_value = round(taxable_amount + cgst_value + sgst_value + igst_value, 2)

            records.append({
                "applicant_id": p["applicant_id"],
                "gstin": p["gstin"],
                "eway_id": f"EWB{int(ts.timestamp())}",
                "timestamp": ts.isoformat(),
                "userGstin": p["gstin"],
                "supplyType": random.choices(["O", "I"], weights=[0.8, 0.2])[0],
                "subSupplyType": random.choices([1, 4, 9, 1], weights=[0.7, 0.1, 0.1, 0.1])[0],
                "subSupplyDesc": "",
                "docType": random.choice(DOC_TYPES),
                "docNo": fake.bothify(text="???###??##"),
                "docDate": ts.strftime("%d/%m/%Y"),
                "transType": random.choices([1, 2], weights=[0.8, 0.2])[0],
                "fromGstin": p["gstin"],
                "fromTrdName": p.get("business_name") or "business",
                "fromAddr1": fake.street_address()[:120],
                "fromAddr2": "",
                "fromPlace": p.get("city") or fake.city(),
                "fromPincode": random.randint(100000, 999999),
                "fromStateCode": p["state_code"],
                "actualFromStateCode": p["state_code"],
                "toGstin": to_gstin,
                "toTrdName": to_trd_name,
                "toAddr1": fake.street_address()[:120],
                "toAddr2": "",
                "toPlace": fake.city()[:50],
                "toPincode": random.randint(100000, 999999),
                "toStateCode": to_state_code,
                "actualToStateCode": to_state_code,
                "transMode": trans_mode,
                "transDistance": trans_distance,
                "transporterName": fake.company()[:25] if trans_mode in (2, 3, 4) else "",
                "transporterId": "",
                "transDocNo": fake.bothify(text="TRN#####") if trans_mode in (2, 3) else "",
                "transDocDate": ts.strftime("%d/%m/%Y") if trans_mode in (2, 3) else "",
                "vehicleNo": generate_vehicle_no() if trans_mode == 1 else "",
                "vehicle_type": "R",
                "main_hsn_code": hsn_code,
                "tot_inv_value": tot_inv_value,
                "itemList_itemNo": 1,
                "itemList_hsnCode": hsn_code,
                "itemList_productName": product_name,
                "itemList_productDesc": product_name,
                "itemList_quantity": random.randint(1, 500),
                "itemList_qtyUnit": "KGS",
                "itemList_taxableAmount": taxable_amount,
                "itemList_sgstRate": 9.0 if intra_state else 0.0,
                "itemList_cgstRate": 9.0 if intra_state else 0.0,
                "itemList_igstRate": 0.0 if intra_state else 18.0,
                "itemList_cessRate": 0.0,
                "itemList_cessNonAdvol": 0.0,
                "totalValue": taxable_amount,
                "cgstValue": cgst_value,
                "sgstValue": sgst_value,
                "igstValue": igst_value,
                "cessValue": 0.0,
                "TotNonAdvolVal": 0.0,
                "OthValue": 0.0,
                "totInvValue": tot_inv_value,
                "synthetic_batch_id": "batch_001",
            })

    df = pl.DataFrame(records)
    logger.info(f"[generator] E-Way Bills: {len(df):,} rows")
    return df


# ── Parquet chunk writer ─────────────────────────────────────────────────────

def write_chunks(df: pl.DataFrame, prefix: str, chunk_size: int = CHUNK_SIZE, start_idx: int = 0) -> int:
    RAW_DATA_PATH.mkdir(parents=True, exist_ok=True)
    n_rows = len(df)
    if n_rows == 0:
        logger.warning(f"[generator] {prefix}: 0 rows — skipping")
        return start_idx
    n_chunks = math.ceil(n_rows / chunk_size)
    for i in range(n_chunks):
        chunk = df.slice(i * chunk_size, chunk_size)
        out_path = RAW_DATA_PATH / f"{prefix}_chunk_{start_idx + i:04d}.parquet"
        chunk.write_parquet(out_path)
    logger.info(f"[generator] {prefix}: {n_rows:,} rows → {n_chunks} chunks (starting at index {start_idx})")
    return start_idx + n_chunks


def write_profiles(profiles: list[dict]) -> None:
    RAW_DATA_PATH.mkdir(parents=True, exist_ok=True)
    df = pl.DataFrame(profiles)
    df.write_parquet(RAW_DATA_PATH / "applicant_profiles.parquet")
    logger.info(f"[generator] applicant_profiles.parquet written ({len(profiles)} profiles)")


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NBFC Seed Data — Applicant Profile Generation")
    parser.add_argument("--force", action="store_true",
                         help="Wipe existing data/raw and regenerate from scratch")
    parser.add_argument("--n-profiles", type=int, default=N_PROFILES,
                         help="Number of final synthetic profiles to sample")
    parser.add_argument("--n-seed-rows", type=int, default=N_SEED_ROWS,
                         help="Number of Faker-driven seed rows to fit the CopulaGAN on")
    args = parser.parse_args()

    console.print("=" * 60, style="bold cyan")
    console.print(" NBFC Seed Data — Applicant Profile Generation", style="bold green")
    console.print("=" * 60, style="bold cyan")

    sentinel = RAW_DATA_PATH / "applicant_profiles.parquet"
    if sentinel.exists() and not args.force:
        console.print(
            "[yellow]⚡ data/raw already exists. Skipping generation. "
            "Pass --force to regenerate.[/yellow]"
        )
        raise SystemExit(0)

    for d in [RAW_DATA_PATH, Path("data/features"), Path("data/models")]:
        if d.exists():
            shutil.rmtree(d)
            logger.info(f"[generator] wiped {d}")

    Faker.seed(42)
    np.random.seed(42)
    random.seed(42)
    fake = Faker("en_IN")

    logger.info(f"\n[generator] building {args.n_profiles} applicant profiles via Faker + CopulaGAN")
    profiles = build_profiles_copulagan(fake, n_profiles=args.n_profiles, n_seed_rows=args.n_seed_rows)
    write_profiles(profiles)

    batch_size = 200
    n_batches = math.ceil(len(profiles) / batch_size)
    
    global_chunk_idx = {
        "gst_invoices": 0,
        "eway_bills": 0,
        "bank_transactions": 0,
        "upi_transactions": 0
    }
    
    total_gst, total_ewb, total_bank, total_upi = 0, 0, 0, 0

    for i in range(n_batches):
        logger.info(f"\n[generator] processing batch {i+1}/{n_batches}")
        batch_profiles = profiles[i * batch_size : (i + 1) * batch_size]
        
        # conditional business sources — only applicants with a GSTIN
        gst_df = generate_gst_invoices(batch_profiles, fake)
        ewb_df = generate_eway_bills(batch_profiles, fake)

        # transactional AA sources — every applicant
        bank_df = generate_bank_transactions(batch_profiles)
        upi_df = generate_upi_transactions(batch_profiles)

        global_chunk_idx["gst_invoices"] = write_chunks(gst_df, "gst_invoices", start_idx=global_chunk_idx["gst_invoices"])
        global_chunk_idx["eway_bills"] = write_chunks(ewb_df, "eway_bills", start_idx=global_chunk_idx["eway_bills"])
        global_chunk_idx["bank_transactions"] = write_chunks(bank_df, "bank_transactions", start_idx=global_chunk_idx["bank_transactions"])
        global_chunk_idx["upi_transactions"] = write_chunks(upi_df, "upi_transactions", start_idx=global_chunk_idx["upi_transactions"])
        
        total_gst += len(gst_df)
        total_ewb += len(ewb_df)
        total_bank += len(bank_df)
        total_upi += len(upi_df)

    logger.info(f"\n[generator] [bold green]✓ generation complete — {len(profiles):,} applicant profiles[/bold green]",
                extra={"markup": True})
    by_type: dict[str, int] = {}
    for p in profiles:
        by_type[p["applicant_type"]] = by_type.get(p["applicant_type"], 0) + 1
    logger.info(f"[generator] applicant_type mix: {by_type}")
    logger.info(f"[generator] gst_invoices: {total_gst:,}  eway_bills: {total_ewb:,}  "
                f"bank_transactions: {total_bank:,}  upi_transactions: {total_upi:,}")