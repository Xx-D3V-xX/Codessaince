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

## Phase 4 — Rules engine (the graded centerpiece — build and test this before UI polish) (done)

- [x] Rule evaluation function: given a normalized profile + derived metrics, evaluate every active rule, return pass/fail + actual value + threshold per rule — `src/rules/evaluator.py::evaluate_rule()`/`evaluate_rules()`, three-valued `condition_met` (True/False/None-for-missing-data)
- [x] `rule_group` AND-semantics implementation — `src/rules/evaluator.py::evaluate_rule_groups()`
- [x] Conflict resolution function (`resolve_decision()`) per CLAUDE.md §3.4 precedence order — one clearly named function, not scattered logic — `src/rules/resolver.py::resolve_decision()`, all 5 precedence steps including count-based escalation
- [x] Branch by applicant type (individual ruleset vs. MSME ruleset) per the confirmed two-pipeline decision — `src/rules/context.py::pipeline_for()`
- [x] Rule CRUD (create/edit/deactivate), each edit creating a new version row — `src/rules/crud.py`
- [x] **Explicit test**: change one rule's threshold, re-run a stored application, confirm the decision changes and both decision versions are queryable — this is the PS-1 demo scenario 5 requirement, test it now, not the night before judging — `test_rules_engine.py`, run against real generated applicant data and a live Postgres instance, all assertions pass
- [x] (not on the original checklist, added as a prerequisite) a sensible synthetic default policy — `src/rules/seed_rules.py`, 17 rules across both pipelines, idempotent

## Phase 5 — Weighted-scoring layer + ML integration (done)

- [x] Weighted-deviation formula implementation per CLAUDE.md §3.6 (normalize before weighting, not after) — `src/scoring/weighted_deviation.py`, new `weighted_scoring_config` DB table (Alembic migration `9b6a5cb597ee`, added since Phase 3 predated this layer), CRUD following the same insert-new-version pattern as `rules`
- [x] Two XGBoost models (individual, MSME), each consuming `EngineeredApplicantFeatureVector` + cross-source features + raw passthrough fields per the confirmed training-architecture decision — `src/scoring/trainer.py` + `src/scoring/feature_matrix.py`
- [x] Rebuild `trainer.py` from scratch to consume the new feature pipeline instead of its own hand-rolled one; replace hardcoded proxy-label rules with something that doesn't reproduce the CreditIQ anti-pattern — consumes the real adapter/engine/cross_source pipeline; `src/scoring/proxy_labels.py` is a disclosed, centralized, continuous+probabilistic proxy label (not a duplicated deterministic threshold formula)
- [x] SHAP explainability wired to both models — `src/scoring/explain.py`, verified to reconstruct the model's real `predict_proba()` output exactly (within float tolerance) for real applicants in both pipelines
- [x] Recalibration offset applied at the probability-to-score mapping stage (§3.7), separately logged from rules-layer weight changes — `src/scoring/recalibration.py::apply_recalibration()`, own `recalibration_offset` audit_log entity_type

## Phase 6 — Decision hierarchy & exception workflow (done)

- [x] Decision state machine: `STP_APPROVED | HARD_REJECT | EXCEPTION_REQUIRED | INSUFFICIENT_DATA` — the enum + `resolve_decision()` (Phase 4); Phase 6 adds `effective_outcome()` (`src/rules/exceptions.py`) composing it with an exception's resolution into one "cleared to proceed" answer, without retroactively relabeling the automated decision
- [x] Exception routing (L1/L2/Credit Head) including count-based escalation — `src/rules/exceptions.py::route_exception()`, auto-creates an `Exception_` row at `resolve_decision()`'s already-computed (and possibly escalated) severity, assigned to a level-scoped queue
- [x] Exception resolution endpoint (approve/reject by authorized role), re-fires decision, audit-logged — `src/rules/exceptions.py::resolve_application_exception()`; re-fire runs from `Application.rule_context_snapshot` alone (new column, added since Phase 3's two snapshot fields didn't include raw bureau/bank data rules also need)

## Phase 7 — Loan eligibility & pricing (done)

- [x] Eligibility calculation (multiplier/cap per risk grade, configurable, not hardcoded) — `src/pricing/eligibility.py::compute_eligibility()`, config via `src/pricing/config.py` (new CRUD for the `eligibility_multipliers` table, schema'd in Phase 3, unused until now)
- [x] Pricing band lookup (configurable, not hardcoded) — `compute_pricing()`, same CRUD pattern against `pricing_bands`
- [x] Risk grade computation, separate from approve/reject outcome — `compute_risk_grade()`: model P(default) → Phase 5's recalibration offset → risk grade, computed for STP_APPROVED **and** EXCEPTION_REQUIRED decisions alike (never gated on the BRE's own verdict), so a pending exception's reviewer has a grade to work from

## Phase 8 — API layer (done)

- [x] `POST /applications` → normalize, validate, return 202 + application_id (async saga pattern) — `src/api/routers/applications.py`, `FastAPI BackgroundTasks` runs `evaluate_route_and_price()` after the response is sent
- [x] `GET /applications/{id}/decision` → poll for result — returns current `application_status` + full decision/pricing/exception detail once `DECISIONED`
- [x] `PATCH /rules/{id}` → live threshold edit — keyed by `rule_code` (the stable identity), not the surrogate `id`, which changes on every edit — see router docstring for why
- [x] `POST /applications/{id}/rerun` → re-evaluate stored profile against current rules — re-fires from `Application.rule_context_snapshot` (Phase 6), also async/202
- [x] `GET /applications/{id}/audit` → full audit trail — aggregates `audit_log` across the application/decision/exception entity_types a single application's history spans
- [x] Exception approval endpoints, role-gated — `POST /exceptions/{id}/approve|reject`, real authorization logic keyed off `X-User-Role` (see `src/api/deps.py` for the honest disclosure of what "role-gated" means without a real auth system)

## Phase 9 — Frontend (basic version done — plain HTML/JS test console, not a production UI; explicitly scoped down per instruction to revisit later)

- [x] Application submission / intake form — `frontend/index.html`'s Submit tab
- [x] Decision result view: rule-by-rule pass/fail, reason codes, final outcome, eligible amount/rate — Decision tab, full `triggered_rules` table with a three-state condition badge (fired/clear/unknown)
- [x] Rules admin console: editable threshold table, per applicant-type ruleset — Rules Admin tab; required a small, additive `GET /rules?pipeline=` list endpoint (not in Phase 8's original bullets — you can't build a table over single-item-lookup-only)
- [x] Exception queue (L1/L2/Credit Head views, role-gated) — Exception Queue tab; required a small, additive `GET /exceptions?level=&status=` list endpoint, same reasoning
- [x] Audit history view — Audit tab
- [x] Re-run flow UI: edit threshold → re-run → side-by-side decision comparison (**tested this exact flow explicitly in a real browser** — edited `IND_MIN_BUREAU_SCORE` live via the admin console, returned to the Decision tab, re-ran, and confirmed the before/after comparison panel renders correctly)

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
