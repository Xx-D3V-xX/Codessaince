# TESTING.md — CreditGate

Every test case in this project, what it covers, and how it's actually verified. Written after the fact from the real test scripts in the repo — not aspirational, not a plan. If a claim below doesn't match a script's actual assertions, the script is the source of truth.

---

## Testing philosophy

**No mocks, ever.** Every test in this project runs against:
- a real, live Postgres instance (`creditgate-postgres`, `docker compose up -d`)
- real generated applicant data (the actual 8,000-applicant Phase 0 dataset, loaded through the actual Phase 1-2 adapter/feature pipeline)
- the actual trained XGBoost models on disk (`data/models/{individual,msme}/model.pkl`), where relevant

Scripts print their own narration and assertions as they go and raise `AssertionError` (non-zero exit) on failure — there's no separate test runner or fixture framework. Each one is a standalone, readable proof that a specific claim about the system is true, verified against the real thing, not a stand-in for it.

**Reused test data isn't reset between runs.** Rules, config, and applications created by one run stay in the database — that's a deliberate reflection of the project's own versioning design (edits insert new rows, they don't get cleaned up), not test pollution. Every test that mutates shared state (a rule threshold, an eligibility multiplier, a recalibration offset) reverts it to its canonical seeded value at the end of its own run, so the *next* run starts from the same policy baseline even though the version history keeps growing.

## Running the tests

```bash
docker compose up -d
python -m alembic upgrade head
python -m src.rules.seed_rules
python -m src.scoring.seed_weighted_scoring
python -m src.scoring.recalibration
python -m src.pricing.seed_pricing
python -m src.scoring.trainer        # writes data/models/ -- needed before test_scoring_pipeline.py / test_pricing_pipeline.py / test_api.py

python test_rules_engine.py
python test_scoring_pipeline.py
python test_exception_workflow.py
python test_pricing_pipeline.py
python test_api.py
```

Each one prints `ALL PHASE N ASSERTIONS PASSED` at the end on success.

---

## Test suite index

| File | Feature area | Phase | Real infra used |
|---|---|---|---|
| [`test_rules_engine.py`](test_rules_engine.py) | Rules engine — evaluation, conflict resolution, live threshold edit + re-run | 4 | Postgres, all 8,000 applicants |
| [`test_scoring_pipeline.py`](test_scoring_pipeline.py) | ML scoring — predictions, SHAP, recalibration, weighted-scoring signal | 5 | Postgres, both trained models, real applicants |
| [`test_exception_workflow.py`](test_exception_workflow.py) | Exception routing, resolution, honest audit trail | 6 | Postgres, all 8,000 applicants |
| [`test_pricing_pipeline.py`](test_pricing_pipeline.py) | Risk grade, eligibility, pricing | 7 | Postgres, both trained models, real applicants |
| [`test_api.py`](test_api.py) | Every HTTP endpoint | 8 | Postgres, real HTTP requests (FastAPI TestClient), full dataset |

Frontend (Phase 9) has no automated test — see [Frontend verification](#frontend-verification-phase-9---manual-in-a-real-browser) below for why and what was actually checked.

---

## `test_rules_engine.py` — Rules engine (Phase 4)

**What it's proving:** the rule evaluator, `rule_group` AND-semantics, and conflict resolution work correctly against real data, and — the single highest-stakes claim in the whole project — that editing a rule's threshold live and re-running a stored application actually changes the outcome, with both decision versions independently queryable afterward. This is PS-1 demo scenario 5.

### Part 1 — Batch outcome distribution (no DB writes)

| # | Test case | Method | Assertion |
|---|---|---|---|
| 1.1 | The seeded policy produces a genuine mix of outcomes, not a degenerate always-approve or always-reject policy | Evaluate all 8,000 real applicants in-memory through the seeded rules for their pipeline | At least 3 distinct outcome values appear across the batch |

Observed distribution (printed, not asserted exactly, since it can shift as the seeded policy or data changes): HARD_REJECT ~2%, STP_APPROVED ~9%, EXCEPTION_REQUIRED ~89% — noted in `PROGRESS.md` as skewed toward exception by the seeded thresholds, not a bug.

### Part 2 — The re-run / threshold-edit test (real DB writes)

| # | Test case | Method | Assertion |
|---|---|---|---|
| 2.1 | Find a real applicant who hard-rejects *solely* on the bureau-score gate | Scan real applicants for one whose only fired `HARD_REJECT`-outcome group is `bureau_hard_gate` | A candidate is found (fails loudly if the seeded policy changes enough that none exists) |
| 2.2 | First decision is HARD_REJECT | Create a real `Application` for that applicant, run `evaluate_application()` | `decision1.outcome == HARD_REJECT` |
| 2.3 | Judge lowers `IND_MIN_BUREAU_SCORE`'s threshold live | `edit_rule()` — inserts a new version, closes out the old one | New version's `value` reflects the lowered threshold |
| 2.4 | Re-running the *same stored application* against the new threshold changes the decision | `evaluate_application()` again on the same `Application` row | `decision2.outcome != HARD_REJECT` |
| 2.5 | Both decision versions are independently queryable, correctly chained | Re-query both decisions from a **fresh DB session** | `decision1.is_current == False`, `decision1.superseded_by_decision_id == decision2.id`, `decision2.is_current == True` |
| 2.6 | The old rule version is preserved untouched, not mutated | Query all versions of `IND_MIN_BUREAU_SCORE`, ordered | The second-to-last version is `active=False` with the original `{"threshold": 600}`; the latest is `active=True` with the new threshold |

The rule is left at whatever threshold the test set it to — subsequent runs (or a human) are expected to restore it to 600 if a clean baseline matters for a demo; the test itself doesn't auto-revert (unlike the config-editing tests in later phases), since demonstrating the *unrevoked* live edit is the point of this particular scenario.

---

## `test_scoring_pipeline.py` — ML scoring (Phase 5)

**What it's proving:** both trained models produce valid predictions on real applicants, SHAP's explanation is a *faithful reconstruction* of the model's real output (not just plausible-looking numbers), recalibration genuinely moves a probability across a risk-grade boundary, and the admin-weighted composite signal is both meaningfully variable across real applicants and live-editable without retraining.

| # | Test case | Method | Assertion |
|---|---|---|---|
| 1 | Both pipeline models produce valid probabilities | Load `data/models/{individual,msme}/model.pkl`, predict on one real applicant per pipeline | `0.0 <= model_probability <= 1.0` |
| 2 | SHAP's decomposition exactly reconstructs the model's real prediction | `explain_applicant()` sums `base_value + shap_values` into a probability, compared against the same model's own `predict_proba()` for the same applicant | `abs(shap_probability - model_probability) < 1e-4` |
| 3 | Recalibration offset changes the risk grade at a band boundary | `apply_recalibration(0.24, {})` vs. `apply_recalibration(0.24, {"B": 0.02})` | Grade flips from `B` to `C` purely from the offset |
| 4 | A recalibration offset change persists through the real DB, versioned, and reverts cleanly | `set_recalibration_offset()` to 0.02, then back to 0.0 | Final row has `offset_value == 0.0` at `version == original + 1` |
| 5 | The admin-weighted composite signal varies meaningfully across real applicants | Compute `compute_weighted_risk_signal()` for 200 real applicants | More than 10 distinct rounded values appear (not a near-constant) |
| 6 | Editing one field's weight changes the composite for the same applicant — no retraining | `edit_weighted_field()` on `IND_bureau_score`'s weight (1.5 → 5.0), recompute the same applicant's signal | `signal_before != signal_after`; weight is reverted to 1.5 afterward |

---

## `test_exception_workflow.py` — Exception workflow (Phase 6)

**What it's proving:** exceptions route to the correct level (including a genuinely *escalated* case, not just a synthetic one), resolving an exception re-fires the decision from nothing but the application's own stored snapshot, the original decision's outcome is never retroactively rewritten to look like an automated approval, and the system won't silently double-process an already-resolved exception.

| # | Test case | Method | Assertion |
|---|---|---|---|
| 1 | A plain L1 case routes correctly | Find and submit a real applicant whose decision resolves to `EXCEPTION_REQUIRED` at plain L1 | `exception.level == L1`, `status == PENDING`, `assigned_to == "credit_ops_l1"` |
| 2 | A genuinely escalated case routes to a higher level | Find a real applicant whose fired-exception-group count triggers count-based escalation | `exception.level != L1` (escalated) |
| 3 | Approving an exception re-fires the decision from the stored snapshot alone | `resolve_application_exception()` — **no `master_row`/`feature_vector_row` passed at all** | A new, distinct `Decision` is created |
| 4 | The original decision's outcome is never retroactively relabeled | Re-query the original decision from a fresh session after approval | `original_decision.outcome` is unchanged (still literally `EXCEPTION_REQUIRED`, never rewritten to `STP_APPROVED`); correctly chained via `superseded_by_decision_id` |
| 5 | `effective_outcome()` composes `Decision` + `Exception_` correctly across all cases | Call it with an unresolved exception, a resolved (approved) one, and bare `STP_APPROVED`/`HARD_REJECT`/`INSUFFICIENT_DATA` decision stubs | Returns `PENDING_EXCEPTION`, `APPROVED`, `APPROVED`, `REJECTED`, `INSUFFICIENT_DATA` respectively |
| 6 | An already-resolved exception can't be double-processed | Call `resolve_application_exception()` a second time on the same (now-approved) exception | Raises `ValueError` |

---

## `test_pricing_pipeline.py` — Eligibility & pricing (Phase 7)

**What it's proving:** a real approved applicant's eligible amount matches an independently hand-computed value exactly, a hard-rejected applicant correctly gets nothing priced, risk grading happens even for a pending-exception case (genuinely separate from the automated outcome, per CLAUDE.md's own wording), an admin can change a multiplier live with zero code change or retraining, and re-pricing correctly happens again after an exception is resolved.

| # | Test case | Method | Assertion |
|---|---|---|---|
| 1 | An STP applicant's `eligible_amount` matches an independently hand-computed value | Submit a real STP-outcome applicant; separately recompute `min(declared_income_monthly × 12 × multiplier, cap)` from the same config row | `abs(decision.eligible_amount - hand_computed) < 0.01` (observed: ₹612,535.68, exact match) |
| 2 | A HARD_REJECT applicant gets nothing priced | Submit a real hard-rejected applicant | `risk_grade`, `eligible_amount`, `interest_rate` are all `None` |
| 3 | An EXCEPTION_REQUIRED applicant still gets a real grade and price | Submit a real applicant whose decision is `EXCEPTION_REQUIRED` | `risk_grade` and `eligible_amount` are both populated despite no automated approval |
| 4 | Editing one eligibility multiplier live changes a real applicant's `eligible_amount` | `set_eligibility_multiplier()` to `1.0`/cap `100,000`, re-run `price_decision()` on the same stored decision | `eligible_amount` changes to exactly `100,000`; reverted to the seeded value afterward |
| 5 | Resolving an exception re-prices the re-fired decision | `resolve_exception_and_reprice()` on a real pending exception | If the re-fired decision is priceable, it has a real `risk_grade` and a non-`None` pricing result |

---

## `test_api.py` — API layer (Phase 8)

**What it's proving:** every documented endpoint works via real HTTP requests (FastAPI's `TestClient` — genuine ASGI request/response cycle, not a mocked client) against the live system, covering the async submit-and-poll saga, a live rule edit, the re-run flow, the full audit trail, and role-gated exception approval (both the reject and accept paths).

| # | Test case | Method | Assertion |
|---|---|---|---|
| 1 | Health check | `GET /health` | `200`, `{"status": "ok"}` |
| 2 | Submit + async saga + poll | `POST /applications` for a real STP-outcome applicant, then poll `GET /applications/{id}/decision` until terminal | `202` on submit; polled result is `DECISIONED`, `STP_APPROVED`, has a real `eligible_amount`, and a non-empty `triggered_rules` list |
| 3 | Live rule threshold edit | `PATCH /rules/IND_MIN_BUREAU_SCORE` with a new `value` | `200`; `version` incremented by 1; `value` matches the patch; reverted to the original afterward |
| 4 | Re-run produces a new, distinct decision | `POST /applications/{id}/rerun`, poll again | `202` on trigger; new `decision_id != original decision_id` |
| 5 | Full, chronologically-ordered audit trail | `GET /applications/{id}/audit` | `200`; `DECISION_MADE` appears in the actions; entries are sorted by `timestamp` |
| 6a | Role-gating rejects the wrong role | `POST /exceptions/{id}/approve` with `X-User-Role: credit_ops_l2` on a real L1 exception | `403` |
| 6b | Role-gating accepts the right role | Same request with `X-User-Role: credit_ops_l1` | `200`; `exception.status == "APPROVED"` |

---

## PS-1 demo scenario coverage

CLAUDE.md's five minimum demo scenarios, mapped to what actually exercises them:

| # | Scenario | Covered by |
|---|---|---|
| 1 | Strong profile → straight-through approval with loan amount and rate | `test_pricing_pipeline.py` Part 1 (STP + real priced amount/rate), `test_api.py` #2 |
| 2 | Serious delinquency/write-off → hard reject even if income is strong | `test_pricing_pipeline.py` Part 2 (HARD_REJECT applicant, nothing priced) |
| 3 | Borderline bureau score but strong cash flow/assets → L1 exception | `test_exception_workflow.py` Part 1 (plain L1 routing) |
| 4 | Multiple deviations or high loan amount → L2/Credit Head exception | `test_exception_workflow.py` Part 1 (escalated candidate, count-based escalation) |
| 5 | **Change one threshold during judging and re-run the same application** | `test_rules_engine.py` Part 2 (the dedicated, explicit test for exactly this) and `test_api.py` #3+#4 (the same flow, over HTTP) — plus manually re-verified in a real browser through the frontend's Rules Admin → Decision → Re-run flow (see below) |

---

## Data & pipeline verification (Phases 0-3)

These predate the `test_*.py` convention and aren't re-run as part of the suite above, but are real, committed (or documented) verification work:

- **Phase 0 (synthetic data generation):** [`src/ingestion/check_data.py`](src/ingestion/check_data.py) — every generated bureau/bank/ITR row is actually instantiated against its Pydantic schema (not a column eyeball), schema coverage is checked field-by-field, and internal integrity is verified (DPD history consistent with `max_dpd`, ITR income components sum to gross/total, asset components sum to portfolio value). Two real generator bugs were caught and fixed this way (documented in `PROGRESS.md`).
- **Phase 1 (ingestion adapter):** [`src/ingestion/applicant_adapter.py`](src/ingestion/applicant_adapter.py)'s own `__main__` block — validates every row against its Pydantic contract, reports per-source failure counts (0 failures on the current dataset across master/bureau/bank/ITR).
- **Phase 2 (cross-source features):** verified inline as part of `src/features/cross_source.py`'s development — hand-checked `obligation_discrepancy`/`income_discrepancy`/`asset_coverage_ratio` against independently computed expected values for sample applicants (documented in `PROGRESS.md`, not a standalone script).
- **Phase 3 (database schema):** migration round-trip verified manually (`alembic downgrade base` → `upgrade head`, confirming no orphaned Postgres ENUM types survive a downgrade — a real bug was caught and fixed this way) plus an ad hoc smoke test exercising rule versioning, decision chaining, and the exception workflow against the live DB. Documented in `PROGRESS.md`; not a committed script (superseded by `test_rules_engine.py` etc. once those existed).

---

## Frontend verification (Phase 9) — manual, in a real browser

The frontend (`frontend/`) has **no automated test** — it's plain vanilla JS with no test runner, and per its own explicit scope ("basic frontend for testing purposes, will improve later") that wasn't invested in. Instead, per this project's own guidance for UI changes, it was verified by actually driving it in a real running browser session, end to end:

1. Submitted a real applicant via the Submit tab.
2. Loaded its decision on the Decision tab — confirmed the full rule-by-rule trace table renders correctly, including a real case where `bureau_score` was `null` for the applicant and the UI correctly showed "unknown" (not a false "clear") for both bureau-dependent rules.
3. Edited `IND_MIN_BUREAU_SCORE`'s threshold live on the Rules Admin tab — confirmed the version incremented in the UI.
4. Returned to the Decision tab and used Re-run — confirmed the before/after side-by-side comparison panel rendered with the correct old/new decision IDs. **This is PS-1 demo scenario 5's UI flow, tested explicitly as its own step**, not assumed to work because the backend test passed.
5. Reverted the rule threshold the same way.
6. Loaded the L1 Exception Queue — attempted approval with the wrong role (`credit_ops_l2`) and confirmed a clean "forbidden" message; switched to the correct role (`credit_ops_l1`) and confirmed a successful approval, with the row disappearing from the pending queue afterward.
7. Loaded the Audit tab for the submitted application and confirmed a chronological, readable trail.

**Two real bugs were found this way that no script would have caught**, since they only manifest in the browser's actual `fetch()`/DOM behavior:
- An XSS exposure from echoing user/DB strings into `innerHTML` without escaping (caught and fixed before manual testing began, once flagged).
- A header-merging bug in the frontend's shared `api()` helper (`{ headers: {...}, ...options }` let a caller's own headers silently *replace*, not merge with, the default `Content-Type`) that broke exactly the exception approve/reject calls — invisible from reading the code, only surfaced by submitting the real form and reading the real network response.

Both are documented in detail in `PROGRESS.md`'s Phase 9 entry.

---

## What isn't covered

Documented honestly, not hidden:

- **No unit tests in the traditional sense** — every test here is closer to an integration/acceptance test against the real stack. There's no fast, isolated unit-test layer (e.g. testing `resolve_decision()` against hand-built `RuleGroupResult` objects in milliseconds without a DB). This was a deliberate tradeoff given the project's own emphasis on "verified against real data, not fabricated dicts" — see `PROGRESS.md` for that standard being applied consistently since Phase 0.
- **No load/performance testing.** Nothing in this suite measures throughput, latency under concurrency, or behavior under the in-memory dataset preload's real-world limits (see `src/api/dataset.py`'s own documented scaling caveat).
- **No automated frontend test** — see above.
- **No negative-path exhaustive testing of every Pydantic validation error** on the API — `test_api.py` checks the happy paths and the specific 403 role-gating case, not every possible malformed request body.
