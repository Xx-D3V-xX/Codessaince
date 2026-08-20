"""
seed_pricing.py — sensible synthetic default eligibility multipliers and
pricing bands, one row per (pipeline, risk_grade) — mirrors src/rules/
seed_rules.py's and src/scoring/seed_weighted_scoring.py's role: without
these, compute_eligibility()/compute_pricing() have nothing to look up.

Eligible amount = min(annual declared income * multiplier, cap_amount).
MSME multipliers/caps run higher than individual (business loans scale to
₹1Cr per src/ingestion/priors.py's MSME loan-amount ceiling, vs ₹20L for
personal loans) — same reasoning as the seeded BRE's pipeline-specific
thresholds. Risk grades below the seeded BRE's practical reach at STP/
EXCEPTION_REQUIRED (grade D/E would almost always also trip a hard-reject
or heavy-exception rule first) still get real rows — informational
completeness, not a gap silently left for a case that turns out to matter.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from src.db.models import ApplicantPipeline
from src.pricing.config import latest_eligibility_version, latest_pricing_version, set_eligibility_multiplier, set_pricing_band
from src.scoring.recalibration import RISK_GRADE_BANDS

SEED_ACTOR = "system_seed"

# (multiplier, cap_amount) by risk grade
_ELIGIBILITY_INDIVIDUAL = {
    "A": (6.0, 10_000_000.0),
    "B": (5.0, 7_500_000.0),
    "C": (3.5, 4_000_000.0),
    "D": (2.0, 1_500_000.0),
    "E": (1.0, 500_000.0),
}
_ELIGIBILITY_MSME = {
    "A": (8.0, 20_000_000.0),
    "B": (6.5, 15_000_000.0),
    "C": (4.5, 8_000_000.0),
    "D": (2.5, 3_000_000.0),
    "E": (1.5, 1_000_000.0),
}

# interest_rate (annual %) by risk grade
_PRICING_INDIVIDUAL = {"A": 10.5, "B": 12.0, "C": 14.0, "D": 16.5, "E": 19.0}
_PRICING_MSME = {"A": 11.5, "B": 13.0, "C": 15.5, "D": 18.0, "E": 21.0}


def seed_default_pricing(session: Session, created_by: str = SEED_ACTOR) -> list[str]:
    """idempotent, same pattern as every other seed script."""
    created: list[str] = []
    grades = [grade for _, _, grade in RISK_GRADE_BANDS]

    for pipeline, elig_table, pricing_table in (
        (ApplicantPipeline.INDIVIDUAL, _ELIGIBILITY_INDIVIDUAL, _PRICING_INDIVIDUAL),
        (ApplicantPipeline.MSME, _ELIGIBILITY_MSME, _PRICING_MSME),
    ):
        prefix = "IND_" if pipeline == ApplicantPipeline.INDIVIDUAL else "MSME_"
        for grade in grades:
            elig_code = f"{prefix}ELIGIBILITY_{grade}"
            if latest_eligibility_version(session, elig_code) is None:
                multiplier, cap_amount = elig_table[grade]
                set_eligibility_multiplier(
                    session, config_code=elig_code, pipeline=pipeline, risk_grade=grade,
                    multiplier=multiplier, cap_amount=cap_amount, set_by=created_by,
                )
                created.append(elig_code)

            pricing_code = f"{prefix}PRICING_{grade}"
            if latest_pricing_version(session, pricing_code) is None:
                set_pricing_band(
                    session, config_code=pricing_code, pipeline=pipeline, risk_grade=grade,
                    interest_rate=pricing_table[grade], set_by=created_by,
                )
                created.append(pricing_code)

    return created


if __name__ == "__main__":
    from src.db.session import get_session

    with get_session() as s:
        created = seed_default_pricing(s)
        print(f"seeded {len(created)} eligibility/pricing rows: {created}" if created else "eligibility/pricing config already seeded, no changes")
