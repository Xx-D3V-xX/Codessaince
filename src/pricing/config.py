"""
config.py — CRUD + lookups for eligibility_multipliers and pricing_bands
(src/db/models.py, schema'd in Phase 3, unused until now). Same
insert-new-version, close-out-the-old-row pattern as rules/
weighted_scoring_config/recalibration_offsets (CLAUDE.md §3.5) — a
`config_code` here is scoped to (pipeline, risk_grade), analogous to how
recalibration_offsets scopes by (pipeline, risk_grade) directly rather than
via a code string, but eligibility_multipliers/pricing_bands were schema'd
with a `config_code` column in Phase 3, so that's the identity CRUD here
keys off (one config_code per (pipeline, risk_grade) pair by convention,
enforced by the seed script, not the DB — see seed_pricing.py).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.audit import write_audit_log
from src.db.models import ApplicantPipeline, EligibilityMultiplier, PricingBand

# ---------------------------------------------------------------------------
# eligibility multipliers
# ---------------------------------------------------------------------------

def latest_eligibility_version(session: Session, config_code: str) -> EligibilityMultiplier | None:
    stmt = select(EligibilityMultiplier).where(EligibilityMultiplier.config_code == config_code).order_by(EligibilityMultiplier.version.desc()).limit(1)
    return session.execute(stmt).scalars().first()


def _snapshot_eligibility(row: EligibilityMultiplier) -> dict:
    return {
        "config_code": row.config_code, "version": row.version, "pipeline": row.pipeline.value,
        "risk_grade": row.risk_grade, "multiplier": float(row.multiplier),
        "cap_amount": float(row.cap_amount) if row.cap_amount is not None else None, "active": row.active,
    }


def set_eligibility_multiplier(
    session: Session, *, config_code: str, pipeline: ApplicantPipeline, risk_grade: str,
    multiplier: float, cap_amount: float | None, set_by: str,
) -> EligibilityMultiplier:
    """inserts a new version (or the first one) — never mutates a past row."""
    current = latest_eligibility_version(session, config_code)
    before = _snapshot_eligibility(current) if current else None
    now = datetime.now(timezone.utc)

    new_version = EligibilityMultiplier(
        config_code=config_code, version=(current.version + 1 if current else 1), pipeline=pipeline,
        risk_grade=risk_grade, multiplier=multiplier, cap_amount=cap_amount, active=True,
        effective_from=now, created_by=set_by,
    )
    session.add(new_version)
    if current:
        current.effective_to = now
        current.active = False
    session.flush()

    write_audit_log(session, actor=set_by, action="ELIGIBILITY_MULTIPLIER_SET", entity_type="eligibility_multiplier",
                     entity_id=config_code, before=before, after=_snapshot_eligibility(new_version))
    return new_version


def active_eligibility_multiplier(session: Session, pipeline: ApplicantPipeline, risk_grade: str) -> EligibilityMultiplier | None:
    stmt = select(EligibilityMultiplier).where(
        EligibilityMultiplier.pipeline == pipeline, EligibilityMultiplier.risk_grade == risk_grade, EligibilityMultiplier.active.is_(True),
    )
    return session.execute(stmt).scalars().first()


# ---------------------------------------------------------------------------
# pricing bands
# ---------------------------------------------------------------------------

def latest_pricing_version(session: Session, config_code: str) -> PricingBand | None:
    stmt = select(PricingBand).where(PricingBand.config_code == config_code).order_by(PricingBand.version.desc()).limit(1)
    return session.execute(stmt).scalars().first()


def _snapshot_pricing(row: PricingBand) -> dict:
    return {
        "config_code": row.config_code, "version": row.version, "pipeline": row.pipeline.value,
        "risk_grade": row.risk_grade, "interest_rate": float(row.interest_rate), "active": row.active,
    }


def set_pricing_band(
    session: Session, *, config_code: str, pipeline: ApplicantPipeline, risk_grade: str,
    interest_rate: float, set_by: str,
) -> PricingBand:
    current = latest_pricing_version(session, config_code)
    before = _snapshot_pricing(current) if current else None
    now = datetime.now(timezone.utc)

    new_version = PricingBand(
        config_code=config_code, version=(current.version + 1 if current else 1), pipeline=pipeline,
        risk_grade=risk_grade, interest_rate=interest_rate, active=True, effective_from=now, created_by=set_by,
    )
    session.add(new_version)
    if current:
        current.effective_to = now
        current.active = False
    session.flush()

    write_audit_log(session, actor=set_by, action="PRICING_BAND_SET", entity_type="pricing_band",
                     entity_id=config_code, before=before, after=_snapshot_pricing(new_version))
    return new_version


def active_pricing_band(session: Session, pipeline: ApplicantPipeline, risk_grade: str) -> PricingBand | None:
    stmt = select(PricingBand).where(
        PricingBand.pipeline == pipeline, PricingBand.risk_grade == risk_grade, PricingBand.active.is_(True),
    )
    return session.execute(stmt).scalars().first()
