"""
recalibration.py — Phase 5's "lever 2" per CLAUDE.md §3.7: adjusting the
probability-to-score-band mapping, reviewed periodically, distinct from
both rules-layer weights (lever 1, src/scoring/weighted_deviation.py) and
full retraining (lever 3, explicitly out of scope for this build).

Why this has to live at the OUTPUT mapping stage and not the model's INPUT
(CLAUDE.md §3.7, reached after ruling out several input-multiplier
approaches): a multiplier on an input feature — in [0,1] or [-1,1], any
range — can only ever shrink or flip a value, never expand a shrunken one
back toward its old relative standing. A macro re-baseline (e.g. "average
incomes fell 15% this year, re-anchor what 'normal' means") needs to move
the ENTIRE distribution's mapping to risk grades, which only works by
adjusting the function applied AFTER the model produces its raw probability,
never by scaling what goes in.

apply_recalibration() is the one function that does this: raw XGBoost
output -> + admin-set, risk-grade-scoped offset -> risk grade via fixed
bands. Offset changes are read from recalibration_offsets (src/db/models.py,
already schema'd in Phase 3) and go through the same shared audit_log path
as every other config edit (write_audit_log()) — CLAUDE.md §3.7's "own audit
trail" means its own entity_type in that one shared table, not a separate
recalibration-specific log (see models.py's AuditLog docstring).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.audit import write_audit_log
from src.db.models import ApplicantPipeline, RecalibrationOffset

SEED_ACTOR = "system_seed"

# engine-level default risk-grade bands, same convention as
# src/features/engine.py's own *_BANDS module constants: real underwriting
# thresholds belong in an admin-configurable table (recalibration_offsets
# shifts WHERE a probability lands within these bands; the bands themselves
# could become their own config table in a later phase if judges want that
# knob too — out of scope for Phase 5's checklist as written).
RISK_GRADE_BANDS: list[tuple[float, float, str]] = [
    (0.00, 0.10, "A"),
    (0.10, 0.25, "B"),
    (0.25, 0.45, "C"),
    (0.45, 0.70, "D"),
    (0.70, 1.01, "E"),
]


def _grade_for_probability(probability: float) -> str:
    clipped = max(0.0, min(1.0, probability))
    for lo, hi, grade in RISK_GRADE_BANDS:
        if lo <= clipped < hi:
            return grade
    return RISK_GRADE_BANDS[-1][2]


def active_offsets_for_pipeline(session: Session, pipeline: ApplicantPipeline) -> dict[str, float]:
    """{risk_grade: offset_value} for every active recalibration_offsets row in this pipeline."""
    stmt = select(RecalibrationOffset).where(RecalibrationOffset.pipeline == pipeline, RecalibrationOffset.active.is_(True))
    return {row.risk_grade: float(row.offset_value) for row in session.execute(stmt).scalars().all()}


def apply_recalibration(raw_probability: float, offsets_by_grade: dict[str, float]) -> tuple[float, str]:
    """
    raw_probability -> (recalibrated_probability, risk_grade). Offset is
    looked up by the grade the RAW probability would land in — applying an
    offset for a grade requires first knowing roughly where the applicant
    sits, then nudging within/across that neighborhood, not a blind global
    shift applied before any grading has happened.
    """
    provisional_grade = _grade_for_probability(raw_probability)
    offset = offsets_by_grade.get(provisional_grade, 0.0)
    recalibrated = max(0.0, min(1.0, raw_probability + offset))
    return recalibrated, _grade_for_probability(recalibrated)


def latest_version(session: Session, pipeline: ApplicantPipeline, risk_grade: str) -> RecalibrationOffset | None:
    stmt = (
        select(RecalibrationOffset)
        .where(RecalibrationOffset.pipeline == pipeline, RecalibrationOffset.risk_grade == risk_grade)
        .order_by(RecalibrationOffset.version.desc())
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


def _snapshot(row: RecalibrationOffset) -> dict:
    return {
        "pipeline": row.pipeline.value, "risk_grade": row.risk_grade, "version": row.version,
        "offset_value": float(row.offset_value), "reason": row.reason, "active": row.active,
    }


def set_recalibration_offset(
    session: Session, *, pipeline: ApplicantPipeline, risk_grade: str, offset_value: float, reason: str, set_by: str,
) -> RecalibrationOffset:
    """inserts a new version (or the first one) for (pipeline, risk_grade) — never mutates a past row, same pattern as every other config table."""
    current = latest_version(session, pipeline, risk_grade)
    before = _snapshot(current) if current else None
    now = datetime.now(timezone.utc)

    new_version = RecalibrationOffset(
        pipeline=pipeline, risk_grade=risk_grade, version=(current.version + 1 if current else 1),
        offset_value=offset_value, reason=reason, active=True, effective_from=now, created_by=set_by,
    )
    session.add(new_version)
    if current:
        current.effective_to = now
        current.active = False
    session.flush()

    write_audit_log(
        session, actor=set_by, action="RECALIBRATION_OFFSET_SET", entity_type="recalibration_offset",
        entity_id=f"{pipeline.value}:{risk_grade}", before=before, after=_snapshot(new_version),
    )
    return new_version


def seed_default_recalibration_offsets(session: Session, created_by: str = SEED_ACTOR) -> list[str]:
    """offset=0.0 for every (pipeline, risk_grade) pair -- a neutral starting point, idempotent."""
    created: list[str] = []
    for pipeline in (ApplicantPipeline.INDIVIDUAL, ApplicantPipeline.MSME):
        for _, _, grade in RISK_GRADE_BANDS:
            if latest_version(session, pipeline, grade) is not None:
                continue
            set_recalibration_offset(
                session, pipeline=pipeline, risk_grade=grade, offset_value=0.0,
                reason="initial neutral seed", set_by=created_by,
            )
            created.append(f"{pipeline.value}:{grade}")
    return created


if __name__ == "__main__":
    from src.db.session import get_session

    with get_session() as s:
        created = seed_default_recalibration_offsets(s)
        print(f"seeded {len(created)} recalibration offsets: {created}" if created else "recalibration offsets already seeded, no changes")
