"""
schemas.py (src/api/) — request/response contracts for the HTTP API.
Deliberately separate from src/features/schemas.py (raw/engineered feature
contracts) and src/db/models.py (persistence) — this layer's job is what a
client sends and receives over HTTP, which is neither of those verbatim
(e.g. UUIDs serialize as strings, Decimal as float, an ORM relationship
isn't a wire format).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class SubmitApplicationRequest(BaseModel):
    applicant_id: str
    # a given ingested applicant's master data already carries a requested
    # amount/tenure (Phases 0-2) -- these let a demo submit a DIFFERENT
    # loan request for the same underlying applicant without needing new
    # synthetic data, matching the real-world case of one applicant
    # applying more than once.
    requested_loan_amount: float | None = None
    requested_tenure_months: int | None = None


class SubmitApplicationResponse(BaseModel):
    application_id: UUID
    status: str
    message: str = "application received, evaluation in progress"


class RuleTraceEntry(BaseModel):
    rule_code: str
    version: int
    pipeline: str
    field: str
    operator: str
    actual_value: Any
    threshold: dict
    condition_met: bool | None
    outcome: str
    severity: str | None
    reason_code: str
    priority: int
    rule_group: str


class ExceptionSummary(BaseModel):
    id: UUID
    level: str
    status: str
    assigned_to: str | None
    resolved_by: str | None
    resolved_at: datetime | None
    notes: str | None


class DecisionResponse(BaseModel):
    application_id: UUID
    application_status: str
    decision_id: UUID | None = None
    outcome: str | None = None
    effective_outcome: str | None = None
    risk_grade: str | None = None
    eligible_amount: float | None = None
    interest_rate: float | None = None
    tenure_months: int | None = None
    model_risk_score: float | None = None
    decided_at: datetime | None = None
    is_current: bool | None = None
    rule_version_snapshot: dict | None = None
    triggered_rules: list[RuleTraceEntry] | None = None
    exception: ExceptionSummary | None = None


class RerunResponse(BaseModel):
    application_id: UUID
    status: str
    message: str = "re-evaluation in progress"


class RuleUpdateRequest(BaseModel):
    """
    partial update -- any field omitted carries over from the rule's
    current version unchanged (matches edit_rule()'s own semantics).
    value/operator/outcome/severity mirror src/db/models.py's Rule columns.
    """
    edited_by: str
    field: str | None = None
    operator: Literal["LT", "LTE", "GT", "GTE", "EQ", "NEQ", "IN", "NOT_IN", "IS_NULL", "IS_NOT_NULL"] | None = None
    value: dict | None = None
    outcome: Literal["HARD_REJECT", "EXCEPTION"] | None = None
    severity: Literal["L1", "L2", "CREDIT_HEAD"] | None = None
    reason_code: str | None = None
    priority: int | None = None
    rule_group: str | None = None


class RuleResponse(BaseModel):
    id: UUID
    rule_code: str
    version: int
    pipeline: str
    field: str
    operator: str
    value: dict
    outcome: str
    severity: str | None
    reason_code: str
    priority: int
    rule_group: str
    active: bool
    effective_from: datetime
    effective_to: datetime | None


class ExceptionQueueEntry(BaseModel):
    """one row in the exception queue view — enriched with just enough decision/application context for a reviewer to triage without a second request per row."""
    id: UUID
    level: str
    status: str
    assigned_to: str | None
    application_id: UUID
    applicant_id: str
    decision_outcome: str
    risk_grade: str | None
    eligible_amount: float | None
    created_at: datetime


class ExceptionResolutionRequest(BaseModel):
    resolved_by: str
    notes: str | None = None


class ExceptionResolutionResponse(BaseModel):
    exception: ExceptionSummary
    decision: DecisionResponse


class AuditLogEntry(BaseModel):
    id: UUID
    actor: str
    action: str
    entity_type: str
    entity_id: str
    before: dict | None
    after: dict | None
    timestamp: datetime


class AuditTrailResponse(BaseModel):
    application_id: UUID
    entries: list[AuditLogEntry]
