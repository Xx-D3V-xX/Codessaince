"""
routers/rules.py — PATCH /rules/{rule_code}: the live threshold edit
endpoint, TODO.md Phase 8's exposure of PS-1 demo scenario 5 over HTTP.

Path param is `rule_code` (the stable logical identity, e.g.
IND_MIN_BUREAU_SCORE), not the surrogate `id` TODO.md's shorthand names —
each edit creates a brand-new row with its own `id`, so referencing a rule
by its ever-changing surrogate key would break the moment it's edited. See
src/db/models.py's Rule docstring for why rule_code+version is the stable
identity throughout this project.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.deps import get_db
from src.api.schemas import RuleResponse, RuleUpdateRequest
from src.api.serializers import serialize_rule
from src.db.models import ApplicantPipeline, ExceptionLevel, Rule, RuleOperator, RuleOutcome
from src.rules.crud import active_rules_for_pipeline, edit_rule

router = APIRouter(prefix="/rules", tags=["rules"])


@router.get("", response_model=list[RuleResponse])
def list_rules(pipeline: ApplicantPipeline, db: Session = Depends(get_db)) -> list[RuleResponse]:
    """
    active rules for one pipeline, ordered by priority — the "editable
    threshold table, per applicant-type ruleset" Phase 9's admin console
    needs. Not a TODO.md Phase 8 checklist bullet by name, but a small,
    necessary extension: you can't build a table over an API that only
    supports single-item lookup.
    """
    rules = active_rules_for_pipeline(db, pipeline)
    return [serialize_rule(r) for r in sorted(rules, key=lambda r: r.priority)]


@router.get("/{rule_code}", response_model=RuleResponse)
def get_rule(rule_code: str, db: Session = Depends(get_db)) -> RuleResponse:
    stmt = select(Rule).where(Rule.rule_code == rule_code).order_by(Rule.version.desc()).limit(1)
    rule = db.execute(stmt).scalars().first()
    if rule is None:
        raise HTTPException(status_code=404, detail=f"rule_code {rule_code!r} not found")
    return serialize_rule(rule)


@router.patch("/{rule_code}", response_model=RuleResponse)
def patch_rule(rule_code: str, request: RuleUpdateRequest, db: Session = Depends(get_db)) -> RuleResponse:
    updates: dict = {}
    if request.field is not None:
        updates["field"] = request.field
    if request.operator is not None:
        updates["operator"] = RuleOperator(request.operator)
    if request.value is not None:
        updates["value"] = request.value
    if request.outcome is not None:
        updates["outcome"] = RuleOutcome(request.outcome)
    if request.severity is not None:
        updates["severity"] = ExceptionLevel(request.severity)
    if request.reason_code is not None:
        updates["reason_code"] = request.reason_code
    if request.priority is not None:
        updates["priority"] = request.priority
    if request.rule_group is not None:
        updates["rule_group"] = request.rule_group

    try:
        new_version = edit_rule(db, rule_code=rule_code, updates=updates, edited_by=request.edited_by)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return serialize_rule(new_version)
