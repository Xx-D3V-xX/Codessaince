"""
models.py — Phase 3 database schema (Postgres).

Seven tables per TODO.md Phase 3 / CLAUDE.md §3.4-3.7:
  applications, rules, decisions, exceptions, audit_log,
  eligibility_multipliers, pricing_bands, recalibration_offsets

Design notes that carry CLAUDE.md's architectural decisions into DDL:

- **Versioning by insert, not update** (§3.5): `rules`, `eligibility_multipliers`,
  `pricing_bands`, and `recalibration_offsets` all share one shape — a stable
  logical `*_code` string plus an incrementing `version` int, unique together.
  Editing one of these means inserting a new row with the same code and
  version+1, and setting the old row's `effective_to`/`active=False` — never
  mutating a past row in place. This is what makes "change one threshold
  during judging, re-run, see both decisions" (PS-1 demo scenario 5) hold up:
  the row a past decision snapshot pointed at never changes underneath it.
  The actual CRUD/versioning *logic* is Phase 4's job (rule engine, "Rule
  CRUD" TODO item) — this module only defines the shape that logic writes to.

- **`decisions` links back to itself via `superseded_by_decision_id`** — a
  re-run against a changed rule doesn't overwrite the original decision row,
  it creates a new one and chains it, so "both decision versions are
  queryable" (Phase 4's explicit test) is a schema guarantee, not an
  application-layer promise.

- **`rule_version_snapshot` / `triggered_rules` on `decisions` are JSONB, not
  normalized join tables** — a decision must remain reconstructable exactly
  as it was even after the `rules` table has moved on to newer versions of
  the same rule_codes. Snapshotting the fired rules' full state at decision
  time (not just FK references to current rows) is what makes a decision
  audit-reproducible independent of the rules table's current contents.

- **`audit_log` is one shared table with a generic `entity_type`/`entity_id`
  pair, not a per-entity audit table** — per TODO.md's explicit instruction
  ("one shared write path, not per-endpoint ad hoc writes"). Every
  state-changing action across every table above, including
  `recalibration_offsets`, writes here — that table's "own audit trail"
  (CLAUDE.md §3.7) means its own `entity_type` value in this shared log,
  not a separate `recalibration_audit_log` table.

- **Two-pipeline scoping via `ApplicantPipeline` (INDIVIDUAL / MSME)** on
  `rules`/`eligibility_multipliers`/`pricing_bands`/`recalibration_offsets`
  is deliberately a different, coarser enum than `applications.applicant_type`
  (SALARIED / SELF_EMPLOYED / MSME / CORPORATE, matching the generator's
  categories). Which raw applicant_type maps to which pipeline (e.g. whether
  a given SELF_EMPLOYED applicant runs the individual or MSME ruleset) is a
  rules-engine decision, not a schema-level constant — CLAUDE.md itself notes
  self-employed is "conditional" depending on business ownership, so baking
  a fixed mapping into the schema would misrepresent that as settled here.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# enums
# ---------------------------------------------------------------------------

class ApplicantType(str, enum.Enum):
    SALARIED = "SALARIED"
    SELF_EMPLOYED = "SELF_EMPLOYED"
    MSME = "MSME"
    CORPORATE = "CORPORATE"


class ApplicantPipeline(str, enum.Enum):
    """the two rulesets/models per CLAUDE.md §3.1 — coarser than ApplicantType, see module docstring."""
    INDIVIDUAL = "INDIVIDUAL"
    MSME = "MSME"


class ApplicationStatus(str, enum.Enum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    DECISIONED = "DECISIONED"
    FAILED = "FAILED"


class RuleOperator(str, enum.Enum):
    LT = "LT"
    LTE = "LTE"
    GT = "GT"
    GTE = "GTE"
    EQ = "EQ"
    NEQ = "NEQ"
    IN = "IN"
    NOT_IN = "NOT_IN"
    IS_NULL = "IS_NULL"
    IS_NOT_NULL = "IS_NOT_NULL"


class RuleOutcome(str, enum.Enum):
    """what firing this rule means for the decision — conflict resolution precedence lives in CLAUDE.md §3.4, not here."""
    HARD_REJECT = "HARD_REJECT"
    EXCEPTION = "EXCEPTION"


class ExceptionLevel(str, enum.Enum):
    L1 = "L1"
    L2 = "L2"
    CREDIT_HEAD = "CREDIT_HEAD"


class ExceptionStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class RiskDirection(str, enum.Enum):
    """which side of a WeightedScoringConfig row's base_limit reads as risk — see that class's docstring."""
    HIGHER_IS_RISK = "HIGHER_IS_RISK"
    LOWER_IS_RISK = "LOWER_IS_RISK"


class DecisionOutcome(str, enum.Enum):
    """mirrors Phase 6's decision state machine exactly — see TODO.md Phase 6."""
    STP_APPROVED = "STP_APPROVED"
    HARD_REJECT = "HARD_REJECT"
    EXCEPTION_REQUIRED = "EXCEPTION_REQUIRED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


# ---------------------------------------------------------------------------
# applications
# ---------------------------------------------------------------------------

class Application(Base):
    """
    one loan application instance. applicant_id references the
    synthetic-data-generator/ingestion-layer applicant (src/ingestion), not
    a row in this database — Phases 0-2's applicant data and this schema
    are deliberately separate concerns until Phase 8's API layer bridges them.
    """

    __tablename__ = "applications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    applicant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    applicant_type: Mapped[ApplicantType] = mapped_column(Enum(ApplicantType, name="applicant_type"), nullable=False)
    requested_loan_amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    requested_tenure_months: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(ApplicationStatus, name="application_status"), nullable=False, default=ApplicationStatus.RECEIVED
    )
    # point-in-time snapshots of the normalized profile / engineered feature
    # vector this application was evaluated against — reproducibility for
    # audit and for Phase 4's re-run flow, independent of Phases 0-2's own
    # storage (which is not versioned per-application).
    normalized_profile_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    feature_vector_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    decisions: Mapped[list["Decision"]] = relationship(back_populates="application")


# ---------------------------------------------------------------------------
# rules — flat, single-condition rows per CLAUDE.md §3.3, versioned by insert
# ---------------------------------------------------------------------------

class Rule(Base):
    """
    one flat, single-field, single-condition rule row. compound conditions
    are expressed by giving multiple rows the same rule_group (implicit AND)
    — see CLAUDE.md §3.3. rule_code is the stable logical identity across
    versions; (rule_code, version) is what a decision's rule_version_snapshot
    references, never the surrogate `id` alone, so a snapshot stays valid
    even if this row is later superseded.
    """

    __tablename__ = "rules"
    __table_args__ = (UniqueConstraint("rule_code", "version", name="uq_rules_code_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    rule_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    pipeline: Mapped[ApplicantPipeline] = mapped_column(Enum(ApplicantPipeline, name="applicant_pipeline"), nullable=False)

    field: Mapped[str] = mapped_column(String(128), nullable=False)
    operator: Mapped[RuleOperator] = mapped_column(Enum(RuleOperator, name="rule_operator"), nullable=False)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)  # numeric/string/list, operator-dependent

    outcome: Mapped[RuleOutcome] = mapped_column(Enum(RuleOutcome, name="rule_outcome"), nullable=False)
    severity: Mapped[ExceptionLevel | None] = mapped_column(Enum(ExceptionLevel, name="exception_level"), nullable=True)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    priority: Mapped[int] = mapped_column(nullable=False, default=100)
    rule_group: Mapped[str] = mapped_column(String(64), nullable=False)

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


# ---------------------------------------------------------------------------
# decisions
# ---------------------------------------------------------------------------

class Decision(Base):
    """
    one decision run for an application against a specific rule set. a
    re-run (Phase 4's threshold-change demo, Phase 8's rerun endpoint)
    inserts a new row and chains it via superseded_by_decision_id rather
    than overwriting — both versions stay independently queryable.
    """

    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), nullable=False, index=True)

    outcome: Mapped[DecisionOutcome] = mapped_column(Enum(DecisionOutcome, name="decision_outcome"), nullable=False)
    risk_grade: Mapped[str | None] = mapped_column(String(8), nullable=True)
    eligible_amount: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    interest_rate: Mapped[float | None] = mapped_column(Numeric(6, 3), nullable=True)
    tenure_months: Mapped[int | None] = mapped_column(nullable=True)

    model_risk_score: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)  # Phase 5's ML input, once wired

    # {rule_code: version} for every rule considered — not just fired ones —
    # so "which rule set produced this decision" is fully reconstructable.
    rule_version_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # full per-rule evaluation trail: [{rule_code, version, field, actual_value,
    # threshold, passed, outcome, severity, reason_code}, ...] — this IS the
    # explainability output PS-1 asks for (CLAUDE.md functional requirement 7).
    triggered_rules: Mapped[list] = mapped_column(JSONB, nullable=False)

    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    superseded_by_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("decisions.id"), nullable=True
    )

    application: Mapped["Application"] = relationship(back_populates="decisions")
    exceptions: Mapped[list["Exception_"]] = relationship(back_populates="decision")


# ---------------------------------------------------------------------------
# exceptions
# ---------------------------------------------------------------------------

class Exception_(Base):
    """
    one exception-approval workflow item, raised against a decision whose
    outcome is EXCEPTION_REQUIRED. named Exception_ (trailing underscore) —
    `Exception` shadows the Python builtin.
    """

    __tablename__ = "exceptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    decision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("decisions.id"), nullable=False, index=True)

    level: Mapped[ExceptionLevel] = mapped_column(Enum(ExceptionLevel, name="exception_level"), nullable=False)
    status: Mapped[ExceptionStatus] = mapped_column(
        Enum(ExceptionStatus, name="exception_status"), nullable=False, default=ExceptionStatus.PENDING
    )
    assigned_to: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    decision: Mapped["Decision"] = relationship(back_populates="exceptions")


# ---------------------------------------------------------------------------
# audit_log — one shared write path for every state-changing action
# ---------------------------------------------------------------------------

class AuditLog(Base):
    """
    entity-agnostic audit trail. entity_id is stored as text (not a typed FK)
    deliberately — it must reference rows across applications/rules/decisions/
    exceptions/eligibility_multipliers/pricing_bands/recalibration_offsets,
    which don't share a PK type or table, and a generic audit log shouldn't
    need a schema migration every time a new auditable entity type is added.
    Write via src/db/audit.py's write_audit_log() — the "one shared write
    path" TODO.md's audit_log bullet calls for — not by inserting here directly.
    """

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    before: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False, index=True)


# ---------------------------------------------------------------------------
# eligibility_multipliers / pricing_bands — admin-configurable, same
# versioning pattern as rules (CLAUDE.md configurable-parameters list)
# ---------------------------------------------------------------------------

class EligibilityMultiplier(Base):
    """eligible_amount = min(income-derived base * multiplier, cap_amount) for non-hard-reject cases, by risk grade."""

    __tablename__ = "eligibility_multipliers"
    __table_args__ = (UniqueConstraint("config_code", "version", name="uq_elig_mult_code_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    config_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    pipeline: Mapped[ApplicantPipeline] = mapped_column(Enum(ApplicantPipeline, name="applicant_pipeline"), nullable=False)
    risk_grade: Mapped[str] = mapped_column(String(8), nullable=False)

    multiplier: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False)
    cap_amount: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class PricingBand(Base):
    """interest-rate lookup by risk grade, for non-hard-reject cases."""

    __tablename__ = "pricing_bands"
    __table_args__ = (UniqueConstraint("config_code", "version", name="uq_pricing_band_code_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    config_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    pipeline: Mapped[ApplicantPipeline] = mapped_column(Enum(ApplicantPipeline, name="applicant_pipeline"), nullable=False)
    risk_grade: Mapped[str] = mapped_column(String(8), nullable=False)

    interest_rate: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False)

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


# ---------------------------------------------------------------------------
# recalibration_offsets — lever 2 of 3, per CLAUDE.md §3.7. instant
# rules-layer weights (lever 1) live inside rule/feature config, not here;
# full retraining (lever 3) is explicitly out of scope for this build.
# ---------------------------------------------------------------------------

class RecalibrationOffset(Base):
    """
    admin-set offset applied at the probability-to-score mapping stage
    (between XGBoost's output and the score-band mapping), reviewed
    periodically — distinct from both rules-layer weights and full
    retraining. Same versioning pattern as rules/eligibility/pricing; its
    "own audit trail" (CLAUDE.md §3.7) is this table's own entity_type rows
    in the shared audit_log, not a dedicated audit table — see AuditLog's docstring.
    """

    __tablename__ = "recalibration_offsets"
    __table_args__ = (
        UniqueConstraint("pipeline", "risk_grade", "version", name="uq_recal_offset_pipeline_grade_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    pipeline: Mapped[ApplicantPipeline] = mapped_column(Enum(ApplicantPipeline, name="applicant_pipeline"), nullable=False)
    risk_grade: Mapped[str] = mapped_column(String(8), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False, default=1)

    offset_value: Mapped[float] = mapped_column(Numeric(9, 6), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


# ---------------------------------------------------------------------------
# weighted_scoring_config — Phase 5's admin-configurable field-importance
# weights, per CLAUDE.md §3.6. Not part of Phase 3's original seven tables
# (that phase predates the weighted-scoring layer being built); added here
# rather than overloading `rules` because a weight isn't a trigger condition
# — it never fires a HARD_REJECT/EXCEPTION outcome on its own, it only scales
# one field's contribution to a single composite ML input feature. Same
# versioning pattern as rules/eligibility_multipliers/pricing_bands regardless.
# ---------------------------------------------------------------------------

class WeightedScoringConfig(Base):
    """
    one field's contribution to the admin-weighted composite risk signal
    (CLAUDE.md §3.6): normalized_deviation = clip((actual_value - base_limit)
    / reference_range, -1, 1), sign-flipped first when direction is
    LOWER_IS_RISK so "deviation" always means "deviation toward risk"
    regardless of whether high or low values are dangerous for this
    particular field (see src/scoring/weighted_deviation.py). weighted_signal
    = normalized_deviation * weight; every active row's weighted_signal for
    a pipeline sums into ONE new engineered feature fed to that pipeline's
    XGBoost model — the model's input schema never gains or loses a slot
    when an admin changes a weight, only the value flowing into that one
    slot changes (§3.6's explicit design point).
    """

    __tablename__ = "weighted_scoring_config"
    __table_args__ = (UniqueConstraint("field_code", "version", name="uq_weighted_scoring_field_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    field_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    pipeline: Mapped[ApplicantPipeline] = mapped_column(Enum(ApplicantPipeline, name="applicant_pipeline"), nullable=False)

    source_field: Mapped[str] = mapped_column(String(128), nullable=False)  # the rule-context field name to read
    direction: Mapped[RiskDirection] = mapped_column(Enum(RiskDirection, name="risk_direction"), nullable=False)
    base_limit: Mapped[float] = mapped_column(Numeric(14, 6), nullable=False)
    reference_range: Mapped[float] = mapped_column(Numeric(14, 6), nullable=False)  # must be > 0
    weight: Mapped[float] = mapped_column(Numeric(9, 6), nullable=False)

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
