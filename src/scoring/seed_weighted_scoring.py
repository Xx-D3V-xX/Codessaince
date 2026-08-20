"""
seed_weighted_scoring.py — default admin field-importance weights for
CLAUDE.md §3.6's weighted-scoring layer, mirroring src/rules/seed_rules.py's
role for the BRE: without any WeightedScoringConfig rows, there is nothing
for compute_weighted_risk_signal() to sum, so the model's composite feature
would always be 0.0. Six fields per pipeline, deliberately overlapping with
(but numerically distinct from) the seeded BRE's own thresholds — this is
a continuous, admin-tunable signal, not a restatement of the BRE's flat
trigger conditions.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from src.db.models import ApplicantPipeline, RiskDirection
from src.scoring.weighted_deviation import latest_version, create_weighted_field

SEED_ACTOR = "system_seed"

# field_code is unique on (field_code, version) alone -- not scoped by
# pipeline in the DB constraint -- so each pipeline needs its own field_code,
# same IND_/MSME_ prefix convention src/rules/seed_rules.py already uses.
_FIELD_SPECS = [
    dict(field_code="bureau_score", source_field="bureau_score", direction=RiskDirection.LOWER_IS_RISK,
         base_limit=700.0, reference_range=150.0, weight=1.5),
    dict(field_code="credit_utilization", source_field="credit_utilization", direction=RiskDirection.HIGHER_IS_RISK,
         base_limit=0.5, reference_range=0.5, weight=1.0),
    dict(field_code="income_trend_itr", source_field="income_trend_itr", direction=RiskDirection.LOWER_IS_RISK,
         base_limit=0.0, reference_range=0.2, weight=0.8),
    dict(field_code="asset_coverage_ratio", source_field="asset_coverage_ratio", direction=RiskDirection.LOWER_IS_RISK,
         base_limit=1.0, reference_range=1.0, weight=0.5),
    dict(field_code="obligation_discrepancy", source_field="obligation_discrepancy", direction=RiskDirection.HIGHER_IS_RISK,
         base_limit=0.0, reference_range=0.5, weight=0.7),
]
# emi_to_inflow_ratio (FOIR proxy) differs by pipeline, same rationale as the
# seeded BRE's IND_ELEVATED_FOIR vs MSME_ELEVATED_FOIR (business cash flow
# is naturally more variable than salaried income).
_FOIR_BY_PIPELINE = {
    ApplicantPipeline.INDIVIDUAL: dict(field_code="emi_to_inflow_ratio", source_field="emi_to_inflow_ratio",
                                        direction=RiskDirection.HIGHER_IS_RISK, base_limit=0.35, reference_range=0.30, weight=1.2),
    ApplicantPipeline.MSME: dict(field_code="emi_to_inflow_ratio", source_field="emi_to_inflow_ratio",
                                  direction=RiskDirection.HIGHER_IS_RISK, base_limit=0.40, reference_range=0.35, weight=1.0),
}


def seed_default_weighted_fields(session: Session, created_by: str = SEED_ACTOR) -> list[str]:
    """idempotent, same pattern as seed_default_policy()."""
    created: list[str] = []
    for pipeline in (ApplicantPipeline.INDIVIDUAL, ApplicantPipeline.MSME):
        prefix = "IND_" if pipeline == ApplicantPipeline.INDIVIDUAL else "MSME_"
        for spec in [*_FIELD_SPECS, _FOIR_BY_PIPELINE[pipeline]]:
            field_code = prefix + spec["field_code"]
            if latest_version(session, field_code) is not None:
                continue
            create_weighted_field(session, pipeline=pipeline, created_by=created_by, **{**spec, "field_code": field_code})
            created.append(field_code)
    return created


if __name__ == "__main__":
    from src.db.session import get_session

    with get_session() as s:
        created = seed_default_weighted_fields(s)
        print(f"seeded {len(created)} new weighted-scoring fields: {created}" if created else "weighted-scoring config already seeded, no changes")
