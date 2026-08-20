"""
context.py — branches by applicant type into the two rulesets/models per
CLAUDE.md §3.1 (TODO.md Phase 4's "branch by applicant type" bullet), builds
the flat field-value context rules evaluate against, and implements the
"critical data missing" check that CLAUDE.md §3.4 item 5 runs before any
rule.
"""

from __future__ import annotations

from src.db.models import ApplicantPipeline, ApplicantType

_INDIVIDUAL_TYPES = {ApplicantType.SALARIED, ApplicantType.SELF_EMPLOYED}
_MSME_TYPES = {ApplicantType.MSME, ApplicantType.CORPORATE}

# a mandatory source (per the applicability matrix, CLAUDE.md §4.3) whose
# completeness is exactly 0.0 means the entire source is absent for this
# applicant, not just partially filled — that's what blocks evaluation.
# itr_data_completeness_yr2 is deliberately excluded: only the last TWO
# years are required overall (CLAUDE.md §4.1 item 4), and yr1 alone missing
# already trips this before yr2 would ever matter on its own.
_MANDATORY_COMPLETENESS_FIELDS = [
    "bureau_data_completeness",
    "banking_data_completeness",
    "itr_data_completeness_yr1",
]


def pipeline_for(applicant_type: ApplicantType) -> ApplicantPipeline:
    """
    CLAUDE.md §3.1's two-pipeline split, made concrete. SALARIED and
    SELF_EMPLOYED default to the INDIVIDUAL ruleset (self-employed defaults
    to non-business-owner, consistent with GST being "conditional-optional"
    rather than mandatory for that type per CLAUDE.md §4.1 item 7); MSME and
    CORPORATE both run the MSME ruleset (both are business-owner entities).

    Deliberately a function, not a schema column (see models.py's
    ApplicantPipeline docstring) — this is a rules-engine policy call, not a
    fixed fact about an applicant_type, so a later phase can make it
    data-driven (e.g. an explicit business-ownership flag overriding the
    default for self-employed) without a schema migration.
    """
    if applicant_type in _INDIVIDUAL_TYPES:
        return ApplicantPipeline.INDIVIDUAL
    return ApplicantPipeline.MSME


def build_rule_context(
    master_row: dict,
    feature_vector_row: dict,
    bureau_row: dict | None = None,
    bank_row: dict | None = None,
) -> dict:
    """
    flat dict of every field a rule might reference. EngineeredApplicantFeatureVector
    only carries *derived* bureau/bank fields (bands, ratios, flags) — CLAUDE.md's
    own configurable-parameters list ("minimum bureau/CIBIL score", "DPD/bounce/
    write-off/settlement rules") names raw fields like bureau_score and max_dpd
    directly, so raw bureau_row/bank_row are merged in too when available, not
    just the engineered vector. Merge order (master, raw bureau, raw bank,
    engineered) never collides in practice — engine.py's raw and derived field
    names are deliberately distinct throughout (credit_card_utilization vs.
    credit_utilization, average_balance vs. balance_stress_ratio, etc.) — but
    engineered fields are merged last regardless, so a rule referencing a
    derived name can never be shadowed by a same-named raw one.
    """
    context = dict(master_row)
    if bureau_row:
        context.update(bureau_row)
    if bank_row:
        context.update(bank_row)
    context.update(feature_vector_row)
    return context


def is_insufficient_data(context: dict) -> bool:
    """
    CLAUDE.md §3.4 item 5, checked before any rule runs: a mandatory
    source being entirely absent (completeness == 0.0) is different from an
    optional source being absent, which per the applicability matrix (§4.3)
    just reduces completeness without blocking evaluation outright.
    """
    return any(context.get(f) == 0.0 for f in _MANDATORY_COMPLETENESS_FIELDS)
