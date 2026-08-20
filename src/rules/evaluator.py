"""
evaluator.py — Phase 4 rule evaluation: given a normalized profile + derived
metrics, evaluate every active rule and return pass/fail + actual value +
threshold per rule (TODO.md Phase 4, first bullet), plus rule_group
AND-semantics (second bullet).

A rule's `condition_met` is a three-valued result, not a plain bool:
  - True  — the field's actual value satisfies the rule's operator/threshold
            (the rule "fires").
  - False — it doesn't.
  - None  — couldn't be evaluated because the referenced field is missing
            from the applicant's context. This is NOT the same as False:
            a rule author querying "did this fire" would want a different
            answer for "no, this borrower is fine" versus "unknown, we
            don't have this borrower's data" — same null-vs-zero discipline
            src/features/schemas.py applies throughout (CLAUDE.md §4.4),
            carried into rule evaluation here.

rule_group AND-semantics (CLAUDE.md §3.3): a group only "fires" if every
member's condition_met is True. If any member is False, the group
definitely doesn't fire (False) regardless of unknowns elsewhere — AND
short-circuits on a known False. If no member is False but at least one is
None, the group's fired status is None (indeterminate), never silently
coerced to True or False.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from src.db.models import Rule, RuleOperator

_COMPARISON_OPS = {
    RuleOperator.LT: lambda actual, threshold: actual < threshold,
    RuleOperator.LTE: lambda actual, threshold: actual <= threshold,
    RuleOperator.GT: lambda actual, threshold: actual > threshold,
    RuleOperator.GTE: lambda actual, threshold: actual >= threshold,
    RuleOperator.EQ: lambda actual, threshold: actual == threshold,
    RuleOperator.NEQ: lambda actual, threshold: actual != threshold,
}
_MEMBERSHIP_OPS = {RuleOperator.IN, RuleOperator.NOT_IN}
_NULL_CHECK_OPS = {RuleOperator.IS_NULL, RuleOperator.IS_NOT_NULL}


def _jsonable(value: Any) -> Any:
    """context values may carry datetime/date/Decimal (from model_dump()'d Pydantic sources) — JSONB needs plain types."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


@dataclass
class RuleEvalResult:
    rule_code: str
    version: int
    pipeline: str
    field_name: str
    operator: str
    actual_value: Any
    threshold: dict
    condition_met: bool | None
    outcome: str
    severity: str | None
    reason_code: str
    priority: int
    rule_group: str

    def to_dict(self) -> dict:
        return {
            "rule_code": self.rule_code,
            "version": self.version,
            "pipeline": self.pipeline,
            "field": self.field_name,
            "operator": self.operator,
            "actual_value": _jsonable(self.actual_value),
            "threshold": self.threshold,
            "condition_met": self.condition_met,
            "outcome": self.outcome,
            "severity": self.severity,
            "reason_code": self.reason_code,
            "priority": self.priority,
            "rule_group": self.rule_group,
        }


def evaluate_rule(rule: Rule, context: dict) -> RuleEvalResult:
    """one rule against one applicant's flat field-value context (see context.py's build_rule_context())."""
    actual_value = context.get(rule.field)
    operator = rule.operator

    if operator in _NULL_CHECK_OPS:
        condition_met = (actual_value is None) if operator == RuleOperator.IS_NULL else (actual_value is not None)
    elif actual_value is None:
        # every other operator needs a real value to compare against — missing data means "can't say", not "false"
        condition_met = None
    elif operator in _MEMBERSHIP_OPS:
        values = rule.value.get("values", [])
        condition_met = (actual_value in values) if operator == RuleOperator.IN else (actual_value not in values)
    else:
        threshold = rule.value.get("threshold")
        condition_met = None if threshold is None else _COMPARISON_OPS[operator](actual_value, threshold)

    return RuleEvalResult(
        rule_code=rule.rule_code,
        version=rule.version,
        pipeline=rule.pipeline.value,
        field_name=rule.field,
        operator=operator.value,
        actual_value=actual_value,
        threshold=rule.value,
        condition_met=condition_met,
        outcome=rule.outcome.value,
        severity=rule.severity.value if rule.severity else None,
        reason_code=rule.reason_code,
        priority=rule.priority,
        rule_group=rule.rule_group,
    )


def evaluate_rules(rules: list[Rule], context: dict) -> list[RuleEvalResult]:
    """every active rule for the applicant's pipeline, evaluated in priority order."""
    return [evaluate_rule(r, context) for r in sorted(rules, key=lambda r: r.priority)]


@dataclass
class RuleGroupResult:
    rule_group: str
    outcome: str
    severity: str | None
    fired: bool | None
    members: list[RuleEvalResult] = field(default_factory=list)


def evaluate_rule_groups(results: list[RuleEvalResult]) -> list[RuleGroupResult]:
    """
    groups evaluated rules by rule_group and applies AND-semantics (see
    module docstring). Assumes every rule sharing a rule_group agrees on
    outcome/severity (they describe one compound trigger's single
    consequence) — takes the first member's for the group's own outcome/severity.
    """
    groups: dict[str, list[RuleEvalResult]] = {}
    for r in results:
        groups.setdefault(r.rule_group, []).append(r)

    group_results: list[RuleGroupResult] = []
    for group_name, members in groups.items():
        conditions = [m.condition_met for m in members]
        if any(c is False for c in conditions):
            fired: bool | None = False
        elif any(c is None for c in conditions):
            fired = None
        else:
            fired = True
        group_results.append(
            RuleGroupResult(
                rule_group=group_name,
                outcome=members[0].outcome,
                severity=members[0].severity,
                fired=fired,
                members=members,
            )
        )
    return group_results
