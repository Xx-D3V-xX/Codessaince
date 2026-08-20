# PROGRESS.md — CreditGate

Last updated: current planning session. Reflects actual verified state, not intended state — see CLAUDE.md §5 for the reasoning behind each line.

---

## Done and verified

- **Design phase** — PS-1 requirements analyzed, CreditIQ reviewed and deliberately not copied wholesale, full data schema designed and cross-checked (raw fields, applicability matrix, derived features, duplicate resolution) across all planned sources
- **`src/features/schemas.py`** — raw input Pydantic schemas (`BureauRecord`, `BankStatementRecord`, `ITRRecord`, `DPDMonth`) and output schema (`EngineeredApplicantFeatureVector`, `FeatureBatch`), with the null-vs-zero optionality rule applied field-by-field
- **`src/features/engine.py`** — same-source derived feature computation for Bureau, Bank Statement, ITR. Verified via smoke tests (hand-built data + schema-validated data), including a genuine bug catch (`dpd_history_completeness` optionality mismatch between engine and schema, found and fixed)
- **Architectural decisions locked in**: two-model split (individual/MSME), rules-first/ML-secondary, flat-rule design with `rule_group` AND-semantics, conflict resolution precedence, weighted-scoring layer design (normalize-before-weight), recalibration as a separate lever from rules-weighting and from full retraining (explicitly out of scope)
- **C4 Context + Container diagrams** and **DFD** produced (Eraser.io format), cross-checked against each other for consistency
- **Numbered end-to-end sequence flow** diagrammed, resolving earlier confusion about execution order vs. topology
- **Synthetic data generator rebuild (Phase 0) — done and verified.** Rebuilt from scratch under `src/ingestion/` as `priors.py` (Faker + Indian demographic bucket priors, sampling helpers — reused/trimmed from the old generator per CLAUDE.md §6), `synth.py` (CopulaGAN seed→fit→sample for master/bureau/bank/ITR scalar fields, field names matching `src/features/schemas.py` exactly), `dpd_history.py` (24-month DPD bucket history as a deterministic rule-based post-process, NOT CopulaGAN-synthesized — trajectory shape keyed off each row's own `max_dpd`/`dpd_recency_months`/`credit_history_type`), `secondary.py` (ITR per-year income-component split, assets/CAS, alt-data, and a deliberately minimal GST summary generator), and `run_generation.py` (orchestrator, chunked parquet output matching `engine.py`'s `bureau_chunk_*`/`bank_statement_chunk_*`/`itr_chunk_*` glob pattern). Old `generate_ais.py`, `fix_profiles.py`, `check_data.py`, `check_data_fast.py` deleted (fully superseded — all tied to the old flat `applicant_profiles.parquet` shape). `src/ingestion/generator.py` (old 3-way generator) left in place for reference but no longer run.
  - **Verified**: generated 8,000 applicants (seed=42, reference_date=2026-04-11) — applicant_type mix SALARIED ~41% / MSME ~29% / SELF_EMPLOYED ~25% / CORPORATE ~4% (CopulaGAN resampling drifts from the exact 50/20/27/3 seed-row target since it resamples correlated distributions rather than preserving category weights verbatim — noted as expected, not a bug). New `src/ingestion/check_data.py` actually instantiates `BureauRecord`/`BankStatementRecord`/`ITRRecord` per row (not a column eyeball) — **all 8,000 bureau rows, all 8,000 bank rows, and all 16,000 ITR rows (yr1+yr2) validate cleanly against the Pydantic schemas**. Schema coverage check found no near-empty columns. Internal integrity checks (DPD history vs. max_dpd/credit_history_type, ITR income components summing to gross_total_income, asset components summing to total_portfolio_value, hard-negative flags vs. max_dpd) all pass with 0 problems on the final run. `FeatureEngine.compute_batch()` (`src/features/engine.py`) run against the real generated bureau/bank/itr parquets end-to-end — all 8,000 feature vectors computed without error.
  - **Two real bugs caught and fixed during hand-inspection** (not just eyeballing — this is exactly the standard TODO.md's last Phase 0 checklist item calls for): (1) `dpd_history.py`'s trajectory builder let a GAN-decorrelated `max_dpd_label` (an independently-sampled categorical column) override a nonzero numeric `max_dpd`, producing all-clean 24-month histories for applicants with e.g. `max_dpd=293` — fixed by treating the numeric `max_dpd` as ground truth and only using the label to pick trajectory shape, never to suppress it. (2) `write_off_flag`/`settlement_flag`/`default_flag`/`suit_filed_flag` — also independently-sampled categoricals — could come back `True` with `max_dpd` in single digits (a write-off with 1 day past due). Fixed with a post-sample coherence pass in `synth.py` forcing these flags `False` whenever `max_dpd <= 90`, ordered to run after the NTC hard-identity reset so it sees the final `max_dpd`, not a pre-override value. Also fixed a smaller bug in `secondary.py`'s ITR income-component split where two independently-random shares (business + professional) could jointly overshoot `remaining`, breaking the sum-to-gross_total_income invariant on ~0.02% of rows — fixed by allocating as one normalized partition instead of two independent draws.

## In progress

- Nothing currently in progress — Phase 0 complete, Phase 1 not yet started.

## Known issues / mismatches identified (not yet resolved)

1. **`src/scoring/trainer.py` is fully disconnected from `src/features/engine.py`.** It has its own hand-rolled feature engineering and its own hardcoded-threshold proxy-label logic (`risk_score = np.where(bureau_score < 600, ...)`), reproducing the exact anti-pattern this project was designed to avoid. **Confirmed: `trainer.py` is being ignored/rebuilt from scratch, not patched (see Phase 5).**
2. **No rules engine, orchestrator, API, frontend, or database schema exist as code.** All of Section 3's architectural decisions in CLAUDE.md are design-complete, implementation-not-started.
3. **Applicant-type mix drifts from the 50/20/27/3 target after CopulaGAN resampling** (observed ~41/25/29/4 on the last full run). The Faker/prior seed rows are generated at the exact target weights, but CopulaGAN samples from the fitted joint distribution rather than preserving marginal category weights exactly — expected GAN behavior, not a generator bug, but worth knowing if Phase 5 model training wants closer-to-target class balance (could reweight or stratify-resample post-generation if it matters later).

## Not started

- Everything in TODO.md Phases 1 through 10.

## Decisions made, not yet reflected in any code beyond Phase 0

- DPD history window: **24 months** (not 12) — matches real bureau reporting convention, was 12 in the old generator only because it borrowed `HISTORY_WINDOW_MONTHS` from the AA/bank-statement constant, not a deliberate choice for bureau data specifically. Implemented in Phase 0.
- Two-model split confirmed: individual/salaried pipeline vs. MSME pipeline, **both ML model and rules engine branch by applicant type**. Not yet implemented — applies from Phase 4/5 onward.
- Training feature architecture: ML consumes `EngineeredApplicantFeatureVector` (derived) + a small set of raw passthrough fields (declared income, requested amount, age) + cross-source features once Phase 2 exists — not a fully separate hand-rolled feature set like the old `trainer.py`. Not yet implemented.
