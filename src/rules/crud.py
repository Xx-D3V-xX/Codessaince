"""
crud.py — Phase 4's "Rule CRUD (create/edit/deactivate), each edit creating
a new version row" TODO.md bullet.

Never mutates a past `rules` row in place. Editing means: insert a new row
with the same rule_code and version+1, close out the previous row's
effective_to/active. This is what keeps the "change one threshold during
judging" demo (PS-1 scenario 5) safe — a Decision that already snapshotted
(rule_code, version) in its rule_version_snapshot/triggered_rules keeps
pointing at a row that never changes underneath it (see models.py's Rule
and Decision docstrings).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.audit import write_audit_log
from src.db.models import ApplicantPipeline, ExceptionLevel, Rule, RuleOperator, RuleOutcome


def _latest_version(session: Session, rule_code: str) -> Rule | None:
    stmt = select(Rule).where(Rule.rule_code == rule_code).order_by(Rule.version.desc()).limit(1)
    return session.execute(stmt).scalars().first()


def _snapshot(rule: Rule) -> dict:
    return {
        "rule_code": rule.rule_code,
        "version": rule.version,
        "pipeline": rule.pipeline.value,
        "field": rule.field,
        "operator": rule.operator.value,
        "value": rule.value,
        "outcome": rule.outcome.value,
        "severity": rule.severity.value if rule.severity else None,
        "reason_code": rule.reason_code,
        "priority": rule.priority,
        "rule_group": rule.rule_group,
        "active": rule.active,
    }


def create_rule(
    session: Session,
    *,
    rule_code: str,
    pipeline: ApplicantPipeline,
    field: str,
    operator: RuleOperator,
    value: dict,
    outcome: RuleOutcome,
    reason_code: str,
    rule_group: str,
    priority: int = 100,
    severity: ExceptionLevel | None = None,
    created_by: str,
) -> Rule:
    """first version (version=1) of a new rule_code — use edit_rule() to change an existing one."""
    if _latest_version(session, rule_code) is not None:
        raise ValueError(f"rule_code {rule_code!r} already exists — use edit_rule() to version it")

    rule = Rule(
        rule_code=rule_code,
        version=1,
        pipeline=pipeline,
        field=field,
        operator=operator,
        value=value,
        outcome=outcome,
        severity=severity,
        reason_code=reason_code,
        priority=priority,
        rule_group=rule_group,
        active=True,
        created_by=created_by,
    )
    session.add(rule)
    session.flush()
    write_audit_log(session, actor=created_by, action="RULE_CREATED", entity_type="rule", entity_id=rule_code, after=_snapshot(rule))
    return rule


def edit_rule(session: Session, *, rule_code: str, updates: dict[str, Any], edited_by: str) -> Rule:
    """
    inserts version = current.version + 1 with `updates` layered over the
    current row's fields, then closes the current row out. `updates` keys
    match Rule's own field names (field, operator, value, outcome, severity,
    reason_code, priority, rule_group, pipeline) — anything omitted carries
    over from the current version unchanged.
    """
    current = _latest_version(session, rule_code)
    if current is None:
        raise ValueError(f"rule_code {rule_code!r} does not exist — use create_rule() first")

    before = _snapshot(current)
    now = datetime.now(timezone.utc)

    new_version = Rule(
        rule_code=current.rule_code,
        version=current.version + 1,
        pipeline=updates.get("pipeline", current.pipeline),
        field=updates.get("field", current.field),
        operator=updates.get("operator", current.operator),
        value=updates.get("value", current.value),
        outcome=updates.get("outcome", current.outcome),
        severity=updates.get("severity", current.severity),
        reason_code=updates.get("reason_code", current.reason_code),
        priority=updates.get("priority", current.priority),
        rule_group=updates.get("rule_group", current.rule_group),
        active=True,
        effective_from=now,
        created_by=edited_by,
    )
    session.add(new_version)
    current.effective_to = now
    current.active = False
    session.flush()

    write_audit_log(
        session, actor=edited_by, action="RULE_EDITED", entity_type="rule",
        entity_id=rule_code, before=before, after=_snapshot(new_version),
    )
    return new_version


def deactivate_rule(session: Session, *, rule_code: str, deactivated_by: str) -> Rule:
    """retires a rule_code without deleting its history — active=False, effective_to set on the current row."""
    current = _latest_version(session, rule_code)
    if current is None:
        raise ValueError(f"rule_code {rule_code!r} does not exist")

    before = _snapshot(current)
    current.active = False
    current.effective_to = datetime.now(timezone.utc)
    session.flush()

    write_audit_log(
        session, actor=deactivated_by, action="RULE_DEACTIVATED", entity_type="rule",
        entity_id=rule_code, before=before, after=_snapshot(current),
    )
    return current


def active_rules_for_pipeline(session: Session, pipeline: ApplicantPipeline) -> list[Rule]:
    stmt = select(Rule).where(Rule.pipeline == pipeline, Rule.active.is_(True))
    return list(session.execute(stmt).scalars().all())
