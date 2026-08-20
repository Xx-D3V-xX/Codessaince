"""
test_scoring_pipeline.py — Phase 5 verification, same standard of evidence
as every prior phase: run against the real trained models (not mocks) and
real applicant data, assert concrete outcomes, not just "ran without error".

Covers:
  1. Both pipeline models load and produce probabilities in [0, 1] for real
     applicants.
  2. SHAP explainability (src/scoring/explain.py) produces a top-feature
     breakdown whose implied probability matches the model's own
     predict_proba() for the same applicant, within floating-point tolerance
     -- confirms the SHAP decomposition is actually reconstructing the
     model's real output, not just returning plausible-looking numbers.
  3. Recalibration (src/scoring/recalibration.py) changes the risk grade a
     probability near a band boundary lands in, and the change is
     attributable to the offset alone (same raw probability in, different
     grade out) -- and confirms it does NOT change when the offset is 0.0.
  4. The weighted-scoring composite (src/scoring/weighted_deviation.py)
     actually varies across real applicants (not a constant), and changing
     one field's weight changes the composite for the same applicant.
"""

import joblib
import numpy as np

from src.db.models import ApplicantPipeline
from src.db.session import get_session
from src.rules.context import build_rule_context
from src.scoring.explain import build_explainer, explain_applicant
from src.scoring.recalibration import apply_recalibration, set_recalibration_offset
from src.scoring.trainer import build_pipeline_rows, load_dataset
from src.scoring.weighted_deviation import (
    active_weighted_fields_for_pipeline,
    compute_weighted_risk_signal,
    edit_weighted_field,
)

print("=== loading real dataset + both trained models ===")
master_by_id, bureau_by_id, bank_by_id, vectors = load_dataset()

models = {}
feature_columns_by_pipeline = {}
for pipeline in (ApplicantPipeline.INDIVIDUAL, ApplicantPipeline.MSME):
    model_dir = f"data/models/{pipeline.value.lower()}"
    # joblib.load() here is safe: these .pkl files were trained and written
    # by src/scoring/trainer.py in this same repo, not fetched from any
    # untrusted external source.
    models[pipeline] = joblib.load(f"{model_dir}/model.pkl")
    import json
    with open(f"{model_dir}/feature_columns.json") as fh:
        feature_columns_by_pipeline[pipeline] = json.load(fh)
    print(f"loaded {pipeline.value} model, {len(feature_columns_by_pipeline[pipeline])} features")

# ---------------------------------------------------------------------------
# Part 1 + 2: real predictions + SHAP reconstruction, for one applicant per pipeline
# ---------------------------------------------------------------------------
print("\n=== part 1+2: predictions + SHAP reconstruction ===")
for pipeline in (ApplicantPipeline.INDIVIDUAL, ApplicantPipeline.MSME):
    applicant_ids, encoded_rows, raw_vector_rows = build_pipeline_rows(pipeline, master_by_id, bureau_by_id, bank_by_id, vectors)
    feature_columns = feature_columns_by_pipeline[pipeline]
    model = models[pipeline]

    sample_idx = 0
    sample_row = encoded_rows[sample_idx]
    x = np.array([[sample_row[c] for c in feature_columns]], dtype=np.float64)
    model_probability = float(model.predict_proba(x)[0, 1])
    assert 0.0 <= model_probability <= 1.0
    print(f"{pipeline.value} applicant {applicant_ids[sample_idx]}: model P(default)={model_probability:.4f}")

    explainer = build_explainer(model)
    explanation = explain_applicant(explainer, sample_row, feature_columns, top_n=5)
    print(f"  SHAP-reconstructed P(default)={explanation.predicted_probability:.4f}")
    print(f"  top SHAP contributions: {[(c.feature, round(c.shap_value, 4)) for c in explanation.top_contributions]}")
    assert abs(explanation.predicted_probability - model_probability) < 1e-4, (
        f"SHAP reconstruction {explanation.predicted_probability} doesn't match model output {model_probability}"
    )
    print(f"  assert: SHAP decomposition reconstructs the model's real prediction (within 1e-4) -- PASS")

# ---------------------------------------------------------------------------
# Part 3: recalibration changes the risk grade
# ---------------------------------------------------------------------------
print("\n=== part 3: recalibration offset changes risk grade near a band boundary ===")
# band boundary at 0.25 (C starts at 0.25) -- pick a probability just below it
boundary_probability = 0.24
grade_before, offsets_zero = None, {}
grade_before_prob, grade_before_grade = apply_recalibration(boundary_probability, offsets_zero)
print(f"raw_probability={boundary_probability}, offset=0.0 -> recalibrated={grade_before_prob:.4f}, grade={grade_before_grade}")
assert grade_before_grade == "B"

offsets_nudge = {"B": 0.02}  # nudges 0.24 -> 0.26, crossing into C
recalibrated_prob, grade_after = apply_recalibration(boundary_probability, offsets_nudge)
print(f"raw_probability={boundary_probability}, offset=+0.02 -> recalibrated={recalibrated_prob:.4f}, grade={grade_after}")
assert grade_after == "C" and grade_after != grade_before_grade
print("assert: a nonzero offset changed the risk grade for the same raw probability -- PASS")

with get_session() as s:
    updated = set_recalibration_offset(
        s, pipeline=ApplicantPipeline.INDIVIDUAL, risk_grade="B", offset_value=0.02,
        reason="test_scoring_pipeline verification", set_by="test_scoring_pipeline",
    )
    print(f"persisted offset: INDIVIDUAL/B v{updated.version} = {updated.offset_value}")
    # revert immediately so this test doesn't leave a permanent policy change behind
    reverted = set_recalibration_offset(
        s, pipeline=ApplicantPipeline.INDIVIDUAL, risk_grade="B", offset_value=0.0,
        reason="revert test_scoring_pipeline verification", set_by="test_scoring_pipeline",
    )
    print(f"reverted offset: INDIVIDUAL/B v{reverted.version} = {reverted.offset_value}")
assert reverted.offset_value == 0.0 and reverted.version == updated.version + 1
print("assert: recalibration offset change went through the real DB (versioned, audit-logged) and was cleanly reverted -- PASS")

# ---------------------------------------------------------------------------
# Part 4: weighted-scoring composite varies across applicants and reacts to a weight change
# ---------------------------------------------------------------------------
print("\n=== part 4: admin-weighted risk signal varies across real applicants ===")
with get_session() as s:
    ind_fields = active_weighted_fields_for_pipeline(s, ApplicantPipeline.INDIVIDUAL)

applicant_ids, _, raw_vector_rows = build_pipeline_rows(ApplicantPipeline.INDIVIDUAL, master_by_id, bureau_by_id, bank_by_id, vectors)
signals = []
for applicant_id, feature_vector_row in list(zip(applicant_ids, raw_vector_rows))[:200]:
    master_row = master_by_id[applicant_id]
    bureau_row = bureau_by_id.get(applicant_id)
    bank_row = bank_by_id.get(applicant_id)
    context = build_rule_context(master_row, feature_vector_row, bureau_row=bureau_row, bank_row=bank_row)
    signals.append(compute_weighted_risk_signal(ind_fields, context))

print(f"admin_weighted_risk_signal over 200 real applicants: min={min(signals):.3f} max={max(signals):.3f} distinct_values={len(set(round(s, 3) for s in signals))}")
assert len(set(round(s, 3) for s in signals)) > 10, "expected genuine variation across applicants, not a near-constant signal"
print("assert: composite signal varies meaningfully across real applicants -- PASS")

sample_applicant_id = applicant_ids[0]
sample_context = build_rule_context(
    master_by_id[sample_applicant_id], raw_vector_rows[0],
    bureau_row=bureau_by_id.get(sample_applicant_id), bank_row=bank_by_id.get(sample_applicant_id),
)
signal_before = compute_weighted_risk_signal(ind_fields, sample_context)
with get_session() as s:
    edited = edit_weighted_field(s, field_code="IND_bureau_score", updates={"weight": 5.0}, edited_by="test_scoring_pipeline")
    print(f"IND_bureau_score weight edited to {edited.weight} (v{edited.version})")
    ind_fields_after = active_weighted_fields_for_pipeline(s, ApplicantPipeline.INDIVIDUAL)
signal_after = compute_weighted_risk_signal(ind_fields_after, sample_context)
print(f"same applicant's composite signal: before={signal_before:.4f} after={signal_after:.4f}")
assert signal_before != signal_after, "changing one field's weight should change the composite for the same applicant"
print("assert: editing one field's weight (no retraining) changes the composite signal for the same applicant -- PASS")

with get_session() as s:
    reverted_field = edit_weighted_field(s, field_code="IND_bureau_score", updates={"weight": 1.5}, edited_by="test_scoring_pipeline")
    print(f"IND_bureau_score weight reverted to {reverted_field.weight} (v{reverted_field.version})")

print("\nALL PHASE 5 ASSERTIONS PASSED")
