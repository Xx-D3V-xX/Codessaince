# TODO.md — CreditGate

Sequenced by dependency, not by section number. Each phase should be genuinely working before the next begins — this project's own design principle (rules-first, explainable, no hardcoded thresholds) is easy to violate under time pressure if later phases get built on an unverified foundation.

---

## Phase 0 — Data generation (rebuild, done)

- [x] Design final raw table shapes: `bureau_profiles`, `bank_statement_profiles`, `itr_records` (per-year), `master_data`, `assets_profiles`, `alt_data_profiles`, `gst_profiles` (MSME/self-employed conditional) — implemented as `master_data`, `bureau`, `bank_statement`, `itr`, `assets`, `alt_data`, `gst` chunked parquets under `data/raw/`
- [x] Implement `dpd_history` generation: 24-month chronological bucket sequences, `None` for pre-history months, severity trajectory consistent with `max_dpd`/`dpd_recency_months`/`credit_history_type` — `src/ingestion/dpd_history.py`
- [x] Rebuild generator from scratch (not a patch) covering every raw field in the consolidated schema, split logically (not one monolith, not three disconnected files) — `src/ingestion/priors.py`, `synth.py`, `dpd_history.py`, `secondary.py`, `run_generation.py`
- [x] CopulaGAN synthesis path (seed rows → fit → sample), fixed seed 42, same applicant-type mix as before — `src/ingestion/synth.py`; applicant-type mix drifts post-GAN-resampling from the exact 50/20/27/3 seed weights, noted in PROGRESS.md as expected GAN behavior
- [x] Write verification/check script: schema coverage per source, internal integrity checks (income components sum to gross total, DPD history internally consistent, CAS asset components sum to portfolio total) — `src/ingestion/check_data.py`
- [x] Run generation, inspect output by hand for at least a few applicants per type (salaried, self-employed, MSME, corporate, NTC/thin-file) before treating it as trustworthy — done; caught and fixed 2 real bugs (dpd_history label override, hard-negative flag/max_dpd decorrelation) plus an ITR income-component overshoot bug, all documented in PROGRESS.md

## Phase 1 — Ingestion/adapter layer (done)

- [x] Build `src/ingestion/applicant_adapter.py`: reads generator output, reshapes into exactly the three input shapes `engine.py` requires (`BureauRecord`, `BankStatementRecord`, `ITRRecord`×2 rows/applicant) — also produces `NormalizedApplicantProfile` (master-data fields engine.py's raw schemas don't cover) for downstream BRE use
- [x] Validate adapter output against the Pydantic raw schemas (not just "runs without error" — actually confirm every field lands correctly) — every row instantiated against its Pydantic model; hand-verified `credit_utilization` and `bureau_thin_file_flag` trace correctly from raw inputs
- [x] Run `FeatureEngine.compute_batch()` against real adapted data (not hand-built smoke-test dicts); hand-check derived features for a sample of applicants across all types and thin-file/complete-file cases — 8,000 vectors computed cleanly, NTC/thin-file and complete-file cases hand-checked

## Phase 2 — Cross-source derived features (done)

- [x] Build `src/features/cross_source.py`: `obligation_discrepancy`, `income_discrepancy`, `asset_coverage_ratio`, and the other cross-source features documented but not yet implemented (requires the normalized profile from Phase 1) — `compute_cross_source_features()` per-applicant, `compute_batch_cross_source()` + `merge_into_vectors()` for batch merge into engine.py's vectors
- [x] Extend `EngineeredApplicantFeatureVector` with these fields, same null-vs-zero discipline as existing fields — added `obligation_discrepancy`, `income_discrepancy`, `asset_coverage_ratio` (all `Optional[float] = None`) plus `cross_source_data_completeness` (`float = 0.0`, matching the `*_data_completeness` exception pattern)

## Phase 3 — Database schema (Postgres) (done)

- [x] `applications` table — `src/db/models.py::Application`
- [x] `rules` table (condition, threshold, outcome, severity, reason_code, priority, rule_group, active, version, effective_from) — versioned by insert-new-row-on-edit, not in-place mutation — `src/db/models.py::Rule`, `(rule_code, version)` unique
- [x] `decisions` table (outcome, risk_grade, eligible_amount, interest_rate, rule_version_snapshot) — `src/db/models.py::Decision`, chains re-runs via `superseded_by_decision_id` rather than overwriting
- [x] `exceptions` table (level, status, assigned_to, resolved_by, resolved_at, notes) — `src/db/models.py::Exception_`
- [x] `audit_log` table (actor, action, entity_type, entity_id, before, after, timestamp) — one shared write path, not per-endpoint ad hoc writes — `src/db/models.py::AuditLog` + `src/db/audit.py::write_audit_log()`
- [x] `eligibility_multipliers` / `pricing_bands` tables (admin-configurable, same versioning pattern as rules) — `src/db/models.py::EligibilityMultiplier`, `PricingBand`
- [x] `recalibration_offsets` table (per risk-grade, admin-set, own audit trail — see CLAUDE.md §3.7) — `src/db/models.py::RecalibrationOffset`, `(pipeline, risk_grade, version)` unique

## Phase 4 — Rules engine (the graded centerpiece — build and test this before UI polish)

- [ ] Rule evaluation function: given a normalized profile + derived metrics, evaluate every active rule, return pass/fail + actual value + threshold per rule
- [ ] `rule_group` AND-semantics implementation
- [ ] Conflict resolution function (`resolve_decision()`) per CLAUDE.md §3.4 precedence order — one clearly named function, not scattered logic
- [ ] Branch by applicant type (individual ruleset vs. MSME ruleset) per the confirmed two-pipeline decision
- [ ] Rule CRUD (create/edit/deactivate), each edit creating a new version row
- [ ] **Explicit test**: change one rule's threshold, re-run a stored application, confirm the decision changes and both decision versions are queryable — this is the PS-1 demo scenario 5 requirement, test it now, not the night before judging

## Phase 5 — Weighted-scoring layer + ML integration

- [ ] Weighted-deviation formula implementation per CLAUDE.md §3.6 (normalize before weighting, not after)
- [ ] Two XGBoost models (individual, MSME), each consuming `EngineeredApplicantFeatureVector` + cross-source features + raw passthrough fields per the confirmed training-architecture decision
- [ ] Rebuild `trainer.py` from scratch to consume the new feature pipeline instead of its own hand-rolled one; replace hardcoded proxy-label rules with something that doesn't reproduce the CreditIQ anti-pattern
- [ ] SHAP explainability wired to both models
- [ ] Recalibration offset applied at the probability-to-score mapping stage (§3.7), separately logged from rules-layer weight changes

## Phase 6 — Decision hierarchy & exception workflow

- [ ] Decision state machine: `STP_APPROVED | HARD_REJECT | EXCEPTION_REQUIRED | INSUFFICIENT_DATA`
- [ ] Exception routing (L1/L2/Credit Head) including count-based escalation
- [ ] Exception resolution endpoint (approve/reject by authorized role), re-fires decision, audit-logged

## Phase 7 — Loan eligibility & pricing

- [ ] Eligibility calculation (multiplier/cap per risk grade, configurable, not hardcoded)
- [ ] Pricing band lookup (configurable, not hardcoded)
- [ ] Risk grade computation, separate from approve/reject outcome

## Phase 8 — API layer

- [ ] `POST /applications` → normalize, validate, return 202 + application_id (async saga pattern)
- [ ] `GET /applications/{id}/decision` → poll for result
- [ ] `PATCH /rules/{id}` → live threshold edit
- [ ] `POST /applications/{id}/rerun` → re-evaluate stored profile against current rules
- [ ] `GET /applications/{id}/audit` → full audit trail
- [ ] Exception approval endpoints, role-gated

## Phase 9 — Frontend

- [ ] Application submission / intake form
- [ ] Decision result view: rule-by-rule pass/fail, reason codes, final outcome, eligible amount/rate
- [ ] Rules admin console: editable threshold table, per applicant-type ruleset
- [ ] Exception queue (L1/L2/Credit Head views, role-gated)
- [ ] Audit history view
- [ ] Re-run flow UI: edit threshold → re-run → side-by-side decision comparison (**test this exact flow explicitly — it's the single highest-stakes demo moment**)

## Phase 10 — Explicitly deferred / stretch (do not start before Phases 0–9 are solid)

- [ ] Fraud/cyclic-transfer detection (§3.8) — not started, scoped only
- [ ] No-code rule builder (bonus)
- [ ] What-if simulator (bonus, cheap extension of the re-run endpoint)
- [ ] PDF/document extraction (bonus, real effort, lowest priority)
- [ ] Decision-table unit tests (bonus, but cheap given rules-as-data — worth doing if any spare time exists)
- [ ] Full model retraining automation (§3.7 lever 3) — explicitly out of scope

---

## Cross-cutting reminders (apply throughout, not a phase)

- Every new threshold/limit goes in a config table, never hardcoded in application code
- Every state-changing action writes to the shared audit log path
- Null vs. zero discipline applies to every new derived feature, not just the existing ones
- GST/E-Way Bill/E-Invoice work (already built) is the first thing to deprioritize if time runs short — confirmed out-of-core-scope twice already
