"""
resolver.py — Phase 4's conflict resolution function, per CLAUDE.md §3.4's
exact precedence order, kept as one clearly named function per TODO.md
Phase 4 ("not scattered logic"):

  1. Insufficient data (checked before any rule result is consulted)
  2. Any HARD_REJECT group fired → HARD_REJECT, unconditionally
  3. Else any EXCEPTION group fired → EXCEPTION_REQUIRED, routed to the
     highest severity among fired groups
  4. If the count of distinct fired exception groups exceeds a configurable
     threshold, escalate one level regardless of individual severities
     (serves PS-1 demo scenario 4 — "multiple deviations")
  5. Else every rule passed → STP_APPROVED
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.db.models import DecisionOutcome, ExceptionLevel, RuleOutcome
from src.rules.evaluator import RuleGroupResult

_SEVERITY_ORDER = [ExceptionLevel.L1, ExceptionLevel.L2, ExceptionLevel.CREDIT_HEAD]

# admin-configurable in spirit (CLAUDE.md's "exception level and approval
# authority limits" configurable-parameters entry) — a plain constant here
# since Phase 4 has no admin-config table for it yet; a future phase can
# promote this to a config row without changing resolve_decision()'s shape.
DEFAULT_EXCEPTION_ESCALATION_THRESHOLD = 3


@dataclass
class ResolvedDecision:
    outcome: DecisionOutcome
    severity: ExceptionLevel | None
    fired_groups: list[RuleGroupResult] = field(default_factory=list)
    escalated: bool = False


def resolve_decision(
    group_results: list[RuleGroupResult],
    insufficient_data: bool,
    exception_escalation_threshold: int = DEFAULT_EXCEPTION_ESCALATION_THRESHOLD,
) -> ResolvedDecision:
    if insufficient_data:
        return ResolvedDecision(DecisionOutcome.INSUFFICIENT_DATA, None)

    hard_reject_groups = [g for g in group_results if g.outcome == RuleOutcome.HARD_REJECT.value and g.fired is True]
    if hard_reject_groups:
        return ResolvedDecision(DecisionOutcome.HARD_REJECT, None, hard_reject_groups)

    exception_groups = [g for g in group_results if g.outcome == RuleOutcome.EXCEPTION.value and g.fired is True]
    if exception_groups:
        severities = [ExceptionLevel(g.severity) for g in exception_groups if g.severity]
        highest = max(severities, key=_SEVERITY_ORDER.index) if severities else ExceptionLevel.L1

        escalated = False
        if len(exception_groups) > exception_escalation_threshold:
            escalated_idx = min(_SEVERITY_ORDER.index(highest) + 1, len(_SEVERITY_ORDER) - 1)
            highest = _SEVERITY_ORDER[escalated_idx]
            escalated = True

        return ResolvedDecision(DecisionOutcome.EXCEPTION_REQUIRED, highest, exception_groups, escalated)

    return ResolvedDecision(DecisionOutcome.STP_APPROVED, None)
