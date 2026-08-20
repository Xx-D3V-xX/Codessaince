# CLAUDE.md — CreditGate Project Context

This document is the canonical context for the CreditGate project. It exists so that any session — human or AI — picking this project up has the full picture without needing to reconstruct decisions from scratch.

---

## 1. What this project is

**CreditGate** is a submission for **Codeissance PS-1: Smart Credit Underwriting & Configurable BRE for NBFC Domain**.

It is a from-scratch, independently-designed project — not a reskin of any prior project. An earlier project (CreditIQ, an MSME credit-scoring engine built for a different hackathon) was reviewed for architectural patterns and documentation conventions, and select infrastructure ideas were consciously salvaged, but CreditGate's data model, decision logic, and scope were designed fresh against PS-1's actual requirements.

---

## 2. The problem statement (PS-1), in full

**Theme:** Lending / Credit Decision engine, rule orchestration, explainability and exception workflow.

**Business context:** An NBFC receives a loan application along with multiple financial documents and data points. Credit teams need a consistent way to evaluate repayment capacity and risk. Some cases should be approved automatically, some must be rejected immediately, while borderline cases may be commercially acceptable only after an authorized exception approval.

**Common technical expectations:**
- Functional end-to-end MVP with frontend, backend and persistence (or an equivalent demonstrable architecture)
- Business logic configurable wherever practical; critical thresholds must not be scattered as hard-coded values
- Clear APIs/data contracts between major components
- Reason/explanation trail for automated decisions and recommendations
- Basic auditability: important decisions, rule triggers, events, or user actions must be traceable
- Graceful handling of missing, duplicate, inconsistent, or invalid data
- AI/ML/LLMs are optional. Technology choice must be justified by the problem — an API wrapper alone is not a strong solution
- Teams should describe how their MVP could handle higher volumes, multiple users, and future rule changes

**Problem to be solved:** Build an end-to-end underwriting MVP that converts applicant financial information into a transparent credit decision. Teams should create a sensible synthetic policy and demonstrate a robust engine capable of changing that policy — not reproduce a real NBFC's actual credit policy.

**Indicative input data:**
- Applicant/master data: age, employment/business type, vintage, requested loan amount, tenure, declared income, existing obligations
- CIBIL/bureau-like data: score, active loans, enquiries, overdue amounts, DPD history, write-off/settlement/default indicators
- Bank statement data or summarized transactions: monthly credits, average balance, EMI debits, bounces, cash-flow volatility, large obligations
- ITR/income information for the last two financial years, including income trend
- Assets/CAS-style data: mutual funds, equities, other declared financial assets
- Optional alternate data: employment stability, business vintage, utility/payment behaviour, synthetic behavioural indicators

**Detailed functional requirements:**
1. **Data ingestion & normalization** — accept supplied data, convert into one normalized applicant profile, identify missing/inconsistent critical fields
2. **Derived metrics** — FOIR/obligation ratio, income trend, average balance, bounce count, credit utilization, other team-defined indicators
3. **Configurable BRE** — rules maintained centrally, each with condition, threshold/value, outcome/severity, and (preferably) a reason code; a rule must be changeable without modifying core decision code
4. **Decision hierarchy** — Straight-through Approval, Hard Reject, Exception Required (at least L1 and L2; a higher Credit Head/Committee level may also be modeled)
5. **Loan eligibility & pricing** — for non-hard-reject cases, determine eligible loan amount and applicable interest-rate/pricing band from configured logic
6. **Conflict handling** — define how multiple triggered rules interact (e.g. a pricing rule cannot override a hard-reject rule; multiple exceptions may escalate to a higher authority)
7. **Explainability** — show which rules passed/failed, the key calculated values, and the reasons behind the final outcome
8. **Audit trail** — persist application status, decision, triggered rules, rule version, and exception approvals/actions

**Configurable parameters/rules:** minimum bureau/CIBIL score and bands, maximum FOIR by segment, income/income-stability thresholds, DPD/bounce/write-off/settlement rules, loan eligibility multiplier/cap, risk-grade and interest-rate mapping, hard-reject vs exception-eligible rules, exception level and approval authority limits.

**Expected output/screens:** application summary and normalized profile; rule-by-rule Pass/Fail/Exception result; final decision (Approved/Hard Reject/Exception/Insufficient Data); eligible loan amount, tenure, interest rate; risk grade and major positive/negative factors; exception approval queue and audit history.

**Minimum demo scenarios:**
1. Strong profile → straight-through approval with loan amount and rate
2. Serious delinquency/write-off → hard reject even if income is strong
3. Borderline bureau score but strong cash flow/assets → L1 exception
4. Multiple deviations or high loan amount → L2/Credit Head exception
5. **Change one threshold during judging and re-run the same application to demonstrate configurability** — this is the single highest-stakes demo requirement and must be bulletproof

**Bonus/differentiators (explicitly not core):** visual/no-code rule builder, rule versioning and effective dates, what-if simulator, document extraction from sample PDFs, unit tests for rule combinations/decision tables, API-first/asynchronous underwriting workflow.

---

## 3. Core architectural decisions (in order they were made, with reasoning)

### 3.1 Two-model architecture
CreditGate runs **two parallel pipelines**, not one unified model/ruleset:
- **Individual pipeline** — salaried and non-business applicants
- **MSME pipeline** — business-owner applicants (proprietorship/partnership/company)

Both the ML scoring model *and* the rules engine branch by applicant type. This is not a stylistic choice — MSME applicants have a structurally different, larger input surface (GST turnover, filing consistency, business vintage) that individual applicants never populate, and a shared ruleset would either force GST-dependent rules onto individuals (nonsensical) or dilute MSME-specific policy into a generic ruleset. Two applicant-type-specific rule sets and two models keep each pipeline's logic legible and independently auditable.

### 3.2 Rules-first, ML-secondary
The **rules engine makes the decision**. XGBoost (one model per pipeline above) produces a risk signal that becomes **one input the rules engine can reference** — it never bypasses the rules engine to make a decision directly. This was a deliberate, discussed decision: PS-1 explicitly penalizes "an API wrapper alone" and rewards explainability; a black-box model making the final call would work against both.

### 3.3 Flat rules, not nested condition trees
Rules are stored as **flat, single-field, single-condition rows** (`field, operator, value, outcome, severity, reason_code, priority, rule_group`), not a nested AND/OR expression tree. Compound conditions ("reject if bureau score < 650 AND FOIR > 55%") are expressed via a shared `rule_group` — rules in the same group are implicitly AND'd. This gets most of the expressiveness of a real rules engine at a fraction of the implementation risk, and critically keeps the "change one threshold live during judging" demo scenario bulletproof — it's always just editing one row's `value` field, never navigating a nested JSON tree under time pressure.

### 3.4 Conflict resolution precedence
1. Any `hard_reject` rule firing → final decision is `Hard Reject`, unconditionally, regardless of anything else
2. Else, any `exception` rule/group firing → `Exception Required`, routed to the **highest** severity level among all fired exception rules
3. Optional: if the *count* of distinct fired exception rules exceeds a configurable threshold, escalate one level regardless of individual severities (serves demo scenario 4)
4. Else, all rules pass → `Straight-through Approval`
5. Critical data missing (checked before any of the above) → `Insufficient Data`

### 3.5 Rules storage and versioning
Real database (Postgres/SQLite confirmed — Postgres for the actual build), not in-memory/file store. Editing a rule inserts a new version rather than mutating in place, so rule-versioning and audit-trail requirements fall out of the storage design rather than being bolted on separately.

### 3.6 Weighted-scoring layer (admin-configurable, non-ML)
Beyond hard-reject rules, the admin can also set **relative field importance weights**. This is explicitly **not** the ML model's job — it's a deterministic, transparent formula layer:
- Per field: `normalized_deviation = clip((actual_value - base_limit) / reference_range, -1, 1)` — computed independent of weight, so it's always in a fixed, bounded range
- `weighted_signal = normalized_deviation × admin_weight` — weight applied *after* normalization, so the signal fed onward is never scale-inconsistent with what any downstream consumer expects
- This weighted signal becomes one XGBoost input feature (admin can change weights without retraining, since the model's *input schema* never changes shape — only the values flowing into an already-existing slot do)

**Known limitation, accepted deliberately:** this creates a "double-weighting" effect — XGBoost has its own *implicit*, trained-in feature importance, and the admin's *explicit* rules-layer weight compounds with it rather than replacing it. This cannot be fully separated from outside the model. The resolution: the explainability output surfaces **both** contributions separately (model's own SHAP-based weighting vs. admin's explicit rule-layer weight) rather than pretending the admin has full, isolated control over a field's importance — an honest framing beats a false guarantee.

### 3.7 Recalibration (lever 2) — explicitly scoped, lever 3 explicitly out
Three possible levers for adapting to changing conditions (e.g. a recession shifting what "normal" income looks like) were identified from real credit-risk-modeling practice:
1. **Rules-layer weights** (3.6 above) — instant, admin-set, no model involvement — **in scope**
2. **Model recalibration** — adjusting the probability-to-score mapping (e.g. a `recalibration_offset` applied between XGBoost's output and the score-band mapping), reviewed periodically, distinct from full retraining — **in scope**
3. **Full retraining** — **explicitly out of scope** for this build; acknowledged in documentation as future work

Lever 2 is NOT solved by scaling the *input* to XGBoost (a multiplier — in any range, `[0,1]` or `[-1,1]` — can only ever shrink or flip a value, never expand a shrunken one back toward its old relative standing, which is what a macro re-baseline actually requires). It works on the *output mapping*, not the input features. This distinction was reached after explicitly ruling out several multiplier-based approaches that don't actually solve the stated problem.

### 3.8 Fraud/cyclic-transfer detection — explicitly deferred
Real graph-based fraud detection (circular fund transfers, A→B→C→A patterns) was considered and **deliberately excluded** from the current schema and build. Reasoning: (a) not requested anywhere in PS-1's brief, (b) it's the one area most likely to silently reproduce architecture from the unrelated prior CreditIQ project rather than being independently justified, (c) even the lightweight version requires a new raw field (`counterparty_ids[]`) that doesn't exist in the current schema, and full graph detection requires an entirely different transaction-level data shape. If revisited, scoped as one new field + one boolean derived flag + one rule — not a new subsystem.

### 3.9 GST/E-Way Bill/E-Invoice — scope flagged, not necessarily cut
Three full business-tax-document sources (GST returns, E-Way Bills, E-Invoices) were fully schema'd and feature-engineered during design, but were **explicitly flagged twice** as representing scope beyond PS-1's core brief — real engineering weight that could pull build time away from the graded centerpiece (the BRE). Kept in the design because MSME applicants are genuinely in scope and GST data is a legitimate signal for them, but noted as the first cluster to cut if timeline pressure hits.

---

## 4. Finalized data schema

### 4.1 Six raw input sources (individual + MSME shared), one conditional (GST, MSME-only)
1. **Applicant/master data** — mandatory, all types. No document can supply this (requested amount, tenure, declared income are stated, not documented).
2. **Bureau/credit information** — mandatory, all types. Includes `dpd_history` as **24 months** of chronological monthly bucket counts (`d0_29, d30_59, d60_89, d90_plus`), matching real bureau reporting convention. Months before an applicant's actual credit history began are `None`, not zero — a missing month and a clean month are different facts.
3. **Bank statement/Account Aggregator data** — mandatory, all types, elevated to first-class priority (primary lever for thin-file/no-bureau-history applicants).
4. **ITR/income data** — mandatory, all types, **per-year structure** (`yr1`/`yr2`/extensible), covering the last 2 financial years as required by the brief. Every field (not just income) is captured per year, not just a bolted-on `income_fy1`/`fy2` pair.
5. **Assets/CAS-style investment data** — optional, all types. Primarily a borderline/exception-strengthening signal, not a repayment-capacity substitute.
6. **Alternate data** — optional, all types (digital payments, utility payments, employment/business stability). Digital payment and utility fields required real reconstruction from prose category descriptions into typed fields — documented with explicit assumption flags.
7. **GST/business tax data** — mandatory for MSME/business-owner applicants, N/A for salaried, conditional-optional for self-employed.

### 4.2 Explicitly excluded from raw input
Social media business presence, customer review ratings — unstructured, not cleanly thresholdable for BRE rule design.

### 4.3 Applicability matrix
A field marked N/A for an applicant's type is never treated as missing (excluded from completeness scoring entirely). A field marked mandatory that's absent triggers `Insufficient Data` before any rule runs. A field marked optional that's absent reduces the completeness score without blocking evaluation.

### 4.4 Derived features — the null-vs-zero discipline
Every derived feature's optionality follows one test: **would a rule author write a different rule for "missing/unknown" versus "computed, genuinely zero or false"?** If yes, the field must be `Optional[None]` — a false `0.0`/`False` reading would look like a favorable or neutral signal rather than an absence of data. This was applied field-by-field across all ~47 derived features in the Bureau/Bank/ITR sections. Only two categories get plain (non-Optional) defaults:
- `*_data_completeness` fields (`float = 0.0`) — their entire purpose is to *report* absence as a number, not hide it
- `itr_filing_count` (`int = 0`) — passes the test because "never pulled" and "confirmed zero filings" lead to the same downstream treatment

This discipline caught a real bug during implementation (`dpd_history_completeness` was inconsistently `Optional` in one code path vs. non-Optional in the schema) — the schema tightening surfaced a genuine engine/schema drift, not just theoretical rigor.

### 4.5 Known overlapping/duplicate fields, resolved
- **Business vintage** — three candidate sources (self-declared master data, GST-registration-derived, alt-data). GST-registration-derived is authoritative (harder to falsify); others retained in raw schema but unused for computation, available for future triangulation.
- **GST filing consistency** — raw `filing_consistency_pct` excluded in favor of a computed composite from GSTR-1/GSTR-3B consistency (avoids two independently-sourced numbers disagreeing).
- **CGST/SGST/IGST** across E-Way Bill and E-Invoice — kept independent (not merged), since they may describe different transactions; reconciled via a cross-source check where they overlap, not collapsed to one source.
- **Declared vs. verified figures** (income, obligations) — never merged; the *discrepancy* between them is itself a derived feature (`income_discrepancy`, `obligation_discrepancy`), which is more informative than picking one source as "correct."

---

## 5. What exists as code today (as of this planning pass)

- `src/features/engine.py` + `schemas.py` — feature engineering engine for Bureau/Bank/ITR (same-source derived features only; cross-source features like `obligation_discrepancy` are designed but not yet implemented — they require the ingestion/normalization layer below). Verified working via smoke tests against hand-built and schema-validated data.
- `src/ingestion/generator.py`, `generate_ais.py`, `fix_profiles.py` — a synthetic data generator (CopulaGAN-based, Faker + demographic-prior seeded) producing a **flat** `applicant_profiles.parquet` plus conditional GST/E-Way Bill/UPI/bank-transaction event streams. **This is being rebuilt from scratch** (see Section 6) — the flat, single-table shape does not match what `engine.py` requires (separate Bureau/Bank/ITR-per-year tables), and `dpd_history` was never implemented in any prior version.
- `src/scoring/trainer.py`, `train_model.py`, `test_model.py`, `test_inference.py` — an existing ML training pipeline. **Explicitly disconnected from `engine.py`** — it has its own separate, hand-rolled feature engineering and its own hardcoded-threshold proxy-label generation (the exact anti-pattern this project's design was meant to avoid). To be rebuilt to consume `engine.py`'s output once the new generator and ingestion layer exist.
- **Nothing else exists as code**: no rules engine, no orchestrator, no API layer, no frontend, no Postgres schema/migrations. All of Section 3's architectural decisions are design-complete but implementation-not-started.

---

## 6. Current rebuild in progress

The synthetic data generator is being rewritten from scratch (not patched) to:
- Produce the exact three-table-per-applicant shape `engine.py`'s raw schemas require (Bureau, Bank Statement, ITR-per-year), rather than one flat table
- Include real `dpd_history` generation — 24 months, chronological, `None` for pre-history months, a plausible severity trajectory (not a single fabricated spike) for delinquent applicants, consistent with each applicant's `max_dpd`/`dpd_recency_months`
- Cover every raw field in the finalized consolidated schema (Section 4.1) without omission
- Continue using CopulaGAN-based synthesis (not manual sampling) for genuine cross-field correlation, since generation time is not a binding constraint
- Fixed seed (42), fixed reference date, same applicant-type mix as the original (Salaried 50% / Self-employed 20% / MSME 27% / Corporate 3%)
- Ship alongside a verification/check script (successor to `check_data.py`/`check_data_fast.py`) validating schema coverage and internal integrity (e.g. income-component sums reconciling to gross totals, DPD history internal consistency) against the new shape

See `TODO.md` for the concrete task breakdown and `PROGRESS.md` for current status.
