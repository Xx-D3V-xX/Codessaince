"""
weighted_deviation.py — Phase 5's weighted-scoring layer per CLAUDE.md §3.6.

Deterministic, transparent, non-ML formula layer — explicitly NOT the ML
model's job. For each active WeightedScoringConfig row (src/db/models.py)
scoped to an applicant's pipeline:

  1. normalized_deviation = clip((actual_value - base_limit) / reference_range, -1, 1),
     sign-flipped first when direction is LOWER_IS_RISK so "deviation" always
     reads as "deviation toward risk" regardless of which side of base_limit
     is dangerous for that field. Computed independent of weight, so it's
     always bounded to [-1, 1] before weighting ever touches it — CLAUDE.md
     §3.6's explicit "normalize before weighting, not after" requirement.
  2. weighted_signal = normalized_deviation * admin_weight — weight applied
     strictly after normalization.

Every active row's weighted_signal for a pipeline sums into ONE scalar,
`admin_weighted_risk_signal` — the single new engineered feature fed to that
pipeline's XGBoost model (src/scoring/trainer.py). An admin changing a
weight changes only the value flowing into that one already-existing model
input slot; it never adds or removes a slot, so no retraining is needed
purely to pick up a weight change (CLAUDE.md §3.6).

Known, deliberately accepted limitation (documented in CLAUDE.md §3.6): this
creates a "double-weighting" effect once the composite signal is one of
several inputs XGBoost learns its own implicit importance over — the
model's own SHAP-based weighting and the admin's explicit weight compound
rather than being cleanly separable from outside the model. This module
only computes the admin's own, explicit contribution; src/scoring/explain.py
surfaces the model's SHAP-based contribution separately rather than
pretending the two can be merged into one honest number.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.audit import write_audit_log
from src.db.models import ApplicantPipeline, RiskDirection, WeightedScoringConfig


def latest_version(session: Session, field_code: str) -> WeightedScoringConfig | None:
    stmt = (
        select(WeightedScoringConfig)
        .where(WeightedScoringConfig.field_code == field_code)
        .order_by(WeightedScoringConfig.version.desc())
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


def _snapshot(row: WeightedScoringConfig) -> dict:
    return {
        "field_code": row.field_code,
        "version": row.version,
        "pipeline": row.pipeline.value,
        "source_field": row.source_field,
        "direction": row.direction.value,
        "base_limit": float(row.base_limit),
        "reference_range": float(row.reference_range),
        "weight": float(row.weight),
        "active": row.active,
    }


def create_weighted_field(
    session: Session,
    *,
    field_code: str,
    pipeline: ApplicantPipeline,
    source_field: str,
    direction: RiskDirection,
    base_limit: float,
    reference_range: float,
    weight: float,
    created_by: str,
) -> WeightedScoringConfig:
    """first version of a new field_code — use edit_weighted_field() to change an existing one."""
    if reference_range <= 0:
        raise ValueError("reference_range must be > 0 -- it's a normalization denominator")
    if latest_version(session, field_code) is not None:
        raise ValueError(f"field_code {field_code!r} already exists — use edit_weighted_field() to version it")

    row = WeightedScoringConfig(
        field_code=field_code, version=1, pipeline=pipeline, source_field=source_field,
        direction=direction, base_limit=base_limit, reference_range=reference_range,
        weight=weight, active=True, created_by=created_by,
    )
    session.add(row)
    session.flush()
    write_audit_log(session, actor=created_by, action="WEIGHTED_FIELD_CREATED", entity_type="weighted_scoring_config",
                     entity_id=field_code, after=_snapshot(row))
    return row


def edit_weighted_field(session: Session, *, field_code: str, updates: dict, edited_by: str) -> WeightedScoringConfig:
    """
    same insert-new-version, close-out-the-old-row pattern as rules/eligibility_multipliers/
    pricing_bands/recalibration_offsets (CLAUDE.md §3.5) — most commonly used to change `weight`
    alone, which is the entire point of "admin can change weights without retraining."
    """
    current = latest_version(session, field_code)
    if current is None:
        raise ValueError(f"field_code {field_code!r} does not exist — use create_weighted_field() first")

    before = _snapshot(current)
    now = datetime.now(timezone.utc)

    new_version = WeightedScoringConfig(
        field_code=current.field_code, version=current.version + 1,
        pipeline=updates.get("pipeline", current.pipeline),
        source_field=updates.get("source_field", current.source_field),
        direction=updates.get("direction", current.direction),
        base_limit=updates.get("base_limit", current.base_limit),
        reference_range=updates.get("reference_range", current.reference_range),
        weight=updates.get("weight", current.weight),
        active=True, effective_from=now, created_by=edited_by,
    )
    session.add(new_version)
    current.effective_to = now
    current.active = False
    session.flush()

    write_audit_log(session, actor=edited_by, action="WEIGHTED_FIELD_EDITED", entity_type="weighted_scoring_config",
                     entity_id=field_code, before=before, after=_snapshot(new_version))
    return new_version


def active_weighted_fields_for_pipeline(session: Session, pipeline: ApplicantPipeline) -> list[WeightedScoringConfig]:
    stmt = select(WeightedScoringConfig).where(WeightedScoringConfig.pipeline == pipeline, WeightedScoringConfig.active.is_(True))
    return list(session.execute(stmt).scalars().all())


def normalized_deviation(actual_value: float, base_limit: float, reference_range: float, direction: RiskDirection) -> float:
    raw = (actual_value - base_limit) / reference_range
    if direction == RiskDirection.LOWER_IS_RISK:
        raw = -raw
    return max(-1.0, min(1.0, raw))


def compute_weighted_risk_signal(fields: list[WeightedScoringConfig], context: dict) -> float:
    """
    the ONE composite scalar per CLAUDE.md §3.6 — sums every active field's
    weighted_signal. A field whose source_field is missing from context
    contributes 0.0 (excluded from the sum, not guessed) rather than
    silently treating "no data" as "no deviation" — sum is over available
    fields only, same spirit as the null-vs-zero discipline elsewhere,
    applied here to an aggregate rather than a single field.
    """
    total = 0.0
    for cfg in fields:
        actual_value = context.get(cfg.source_field)
        if actual_value is None:
            continue
        deviation = normalized_deviation(float(actual_value), float(cfg.base_limit), float(cfg.reference_range), cfg.direction)
        total += deviation * float(cfg.weight)
    return total
