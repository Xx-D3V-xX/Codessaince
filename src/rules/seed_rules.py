"""
seed_rules.py — a sensible synthetic underwriting policy, per PS-1's brief
("teams should create a sensible synthetic policy... not reproduce a real
NBFC's actual credit policy"). Not a TODO.md Phase 4 checklist bullet by
name, but without any rules in the `rules` table there is nothing for
evaluate_application() to evaluate — this makes the engine demonstrable
against the real Phase 0-2 generated data end-to-end.

17 rules across the two pipelines (CLAUDE.md §3.1), covering the demo
scenarios' shape: hard-reject gates on bureau score / hard-negative history
/ chronic delinquency; L1 exceptions on borderline bureau score, elevated
utilization/FOIR, recent delinquency; L2 exceptions on high requested
amount, declining income, short business vintage, cash-flow volatility.
Thresholds are deliberately round, easy-to-explain numbers — a judge should
be able to look at any one of these and immediately understand what it's
protecting against, which is the point of a *synthetic*, explainable policy.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import ApplicantPipeline, ExceptionLevel, Rule, RuleOperator, RuleOutcome
from src.rules.crud import create_rule

SEED_ACTOR = "system_seed"

_INDIVIDUAL_RULES = [
    dict(rule_code="IND_MIN_BUREAU_SCORE", field="bureau_score", operator=RuleOperator.LT,
         value={"threshold": 600}, outcome=RuleOutcome.HARD_REJECT, reason_code="BUREAU_SCORE_BELOW_MINIMUM",
         rule_group="bureau_hard_gate", priority=10),
    dict(rule_code="IND_HARD_NEGATIVE", field="hard_negative_flag", operator=RuleOperator.EQ,
         value={"threshold": True}, outcome=RuleOutcome.HARD_REJECT, reason_code="WRITE_OFF_SETTLEMENT_OR_DEFAULT",
         rule_group="delinquency_hard_gate", priority=10),
    dict(rule_code="IND_CHRONIC_DELINQUENCY", field="chronic_delinquency_flag", operator=RuleOperator.EQ,
         value={"threshold": True}, outcome=RuleOutcome.HARD_REJECT, reason_code="CHRONIC_DELINQUENCY_PATTERN",
         rule_group="chronic_delinquency_hard_gate", priority=10),
    dict(rule_code="IND_BORDERLINE_BUREAU", field="bureau_score", operator=RuleOperator.LT,
         value={"threshold": 700}, outcome=RuleOutcome.EXCEPTION, severity=ExceptionLevel.L1,
         reason_code="BUREAU_SCORE_BELOW_PREFERRED_BAND", rule_group="borderline_bureau_l1", priority=50),
    dict(rule_code="IND_HIGH_UTILIZATION", field="credit_utilization", operator=RuleOperator.GT,
         value={"threshold": 0.75}, outcome=RuleOutcome.EXCEPTION, severity=ExceptionLevel.L1,
         reason_code="HIGH_CREDIT_UTILIZATION", rule_group="high_utilization_l1", priority=50),
    dict(rule_code="IND_ELEVATED_FOIR", field="emi_to_inflow_ratio", operator=RuleOperator.GT,
         value={"threshold": 0.50}, outcome=RuleOutcome.EXCEPTION, severity=ExceptionLevel.L1,
         reason_code="ELEVATED_FOIR", rule_group="elevated_foir_l1", priority=50),
    dict(rule_code="IND_RECENT_DELINQUENCY", field="is_recently_delinquent", operator=RuleOperator.EQ,
         value={"threshold": True}, outcome=RuleOutcome.EXCEPTION, severity=ExceptionLevel.L1,
         reason_code="RECENT_DELINQUENCY", rule_group="recent_delinquency_l1", priority=50),
    dict(rule_code="IND_HIGH_LOAN_AMOUNT", field="requested_loan_amount", operator=RuleOperator.GT,
         value={"threshold": 1_500_000}, outcome=RuleOutcome.EXCEPTION, severity=ExceptionLevel.L2,
         reason_code="HIGH_REQUESTED_AMOUNT", rule_group="high_loan_amount_l2", priority=60),
    dict(rule_code="IND_INCOME_DECLINE", field="income_trend_itr", operator=RuleOperator.LT,
         value={"threshold": -0.10}, outcome=RuleOutcome.EXCEPTION, severity=ExceptionLevel.L2,
         reason_code="DECLINING_INCOME_TREND", rule_group="income_decline_l2", priority=60),
]

_MSME_RULES = [
    dict(rule_code="MSME_MIN_BUREAU_SCORE", field="bureau_score", operator=RuleOperator.LT,
         value={"threshold": 600}, outcome=RuleOutcome.HARD_REJECT, reason_code="BUREAU_SCORE_BELOW_MINIMUM",
         rule_group="bureau_hard_gate", priority=10),
    dict(rule_code="MSME_HARD_NEGATIVE", field="hard_negative_flag", operator=RuleOperator.EQ,
         value={"threshold": True}, outcome=RuleOutcome.HARD_REJECT, reason_code="WRITE_OFF_SETTLEMENT_OR_DEFAULT",
         rule_group="delinquency_hard_gate", priority=10),
    dict(rule_code="MSME_CHRONIC_DELINQUENCY", field="chronic_delinquency_flag", operator=RuleOperator.EQ,
         value={"threshold": True}, outcome=RuleOutcome.HARD_REJECT, reason_code="CHRONIC_DELINQUENCY_PATTERN",
         rule_group="chronic_delinquency_hard_gate", priority=10),
    dict(rule_code="MSME_BORDERLINE_BUREAU", field="bureau_score", operator=RuleOperator.LT,
         value={"threshold": 700}, outcome=RuleOutcome.EXCEPTION, severity=ExceptionLevel.L1,
         reason_code="BUREAU_SCORE_BELOW_PREFERRED_BAND", rule_group="borderline_bureau_l1", priority=50),
    dict(rule_code="MSME_ELEVATED_FOIR", field="emi_to_inflow_ratio", operator=RuleOperator.GT,
         value={"threshold": 0.55}, outcome=RuleOutcome.EXCEPTION, severity=ExceptionLevel.L1,
         reason_code="ELEVATED_FOIR", rule_group="elevated_foir_l1", priority=50),
    dict(rule_code="MSME_HIGH_VOLATILITY", field="cash_flow_volatility_band", operator=RuleOperator.EQ,
         value={"threshold": "high"}, outcome=RuleOutcome.EXCEPTION, severity=ExceptionLevel.L1,
         reason_code="HIGH_CASH_FLOW_VOLATILITY", rule_group="cash_flow_volatility_l1", priority=50),
    dict(rule_code="MSME_SHORT_VINTAGE", field="business_vintage_months", operator=RuleOperator.LT,
         value={"threshold": 24}, outcome=RuleOutcome.EXCEPTION, severity=ExceptionLevel.L2,
         reason_code="SHORT_BUSINESS_VINTAGE", rule_group="short_vintage_l2", priority=60),
    dict(rule_code="MSME_HIGH_LOAN_AMOUNT", field="requested_loan_amount", operator=RuleOperator.GT,
         value={"threshold": 5_000_000}, outcome=RuleOutcome.EXCEPTION, severity=ExceptionLevel.L2,
         reason_code="HIGH_REQUESTED_AMOUNT", rule_group="high_loan_amount_l2", priority=60),
]


def seed_default_policy(session: Session, created_by: str = SEED_ACTOR) -> list[str]:
    """
    idempotent: skips any rule_code that already exists (checked via
    create_rule()'s own guard) rather than raising, so re-running this
    against an already-seeded database is a no-op, not an error.
    """
    created: list[str] = []
    for pipeline, rules in ((ApplicantPipeline.INDIVIDUAL, _INDIVIDUAL_RULES), (ApplicantPipeline.MSME, _MSME_RULES)):
        for spec in rules:
            rule_code = spec["rule_code"]
            existing = session.execute(select(Rule).where(Rule.rule_code == rule_code)).scalars().first()
            if existing is not None:
                continue
            create_rule(session, pipeline=pipeline, created_by=created_by, **spec)
            created.append(rule_code)
    return created


if __name__ == "__main__":
    from src.db.session import get_session

    with get_session() as s:
        created = seed_default_policy(s)
        print(f"seeded {len(created)} new rules: {created}" if created else "policy already seeded, no changes")
