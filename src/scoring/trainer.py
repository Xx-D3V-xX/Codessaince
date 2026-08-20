"""
trainer.py — Phase 5 model training, rebuilt from scratch (TODO.md Phase 5 /
PROGRESS.md Known Issues #1: the original trainer.py had its own hand-rolled
feature engineering, fully disconnected from src/features/engine.py, and its
own inline hardcoded-threshold proxy-label logic — the exact anti-pattern
this project was designed to avoid). This version:

  - consumes the REAL Phase 0-2 pipeline (src/ingestion/applicant_adapter.py
    + src/features/engine.py + src/features/cross_source.py) for features,
    not a separate recomputation
  - uses src/scoring/proxy_labels.py's disclosed, centralized proxy label
    (continuous + probabilistic, not a duplicated deterministic threshold
    formula) instead of inline magic numbers
  - trains TWO XGBoost models, one per pipeline (CLAUDE.md §3.1), each
    consuming EngineeredApplicantFeatureVector + cross-source features +
    raw passthrough fields (age, declared_income_monthly,
    requested_loan_amount) + the admin-weighted composite risk signal
    (src/scoring/weighted_deviation.py) — the confirmed training-feature
    architecture decision.

Rules-first, ML-secondary (CLAUDE.md §3.2): nothing here writes to the
decisions table or makes a final call. These models produce a risk SIGNAL
the rules engine can reference as one input in a later phase — they never
bypass it.
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import xgboost as xgb
from sklearn.metrics import accuracy_score, f1_score, log_loss, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import train_test_split

from src.db.models import ApplicantPipeline, ApplicantType
from src.db.session import get_session
from src.features.cross_source import compute_batch_cross_source, merge_into_vectors
from src.features.engine import FeatureEngine
from src.ingestion.applicant_adapter import load_and_adapt, to_engine_frames
from src.rules.context import build_rule_context, pipeline_for
from src.scoring.feature_matrix import build_feature_row, get_feature_columns
from src.scoring.proxy_labels import sample_proxy_labels
from src.scoring.weighted_deviation import active_weighted_fields_for_pipeline, compute_weighted_risk_signal

DEFAULT_MODEL_DIR = Path("data/models")


def load_dataset() -> tuple[dict[str, dict], dict[str, dict], dict[str, dict], list]:
    """
    real Phase 0-2 pipeline output, computed once (not once per pipeline —
    FeatureEngine.compute_batch() over 8,000 applicants isn't cheap, and
    both pipeline models are trained from the same underlying batch, just
    filtered differently below). Returns (master_by_id, bureau_by_id,
    bank_by_id, engineered_vectors) for callers to slice per pipeline.
    """
    result = load_and_adapt()
    bureau_df, bank_df, itr_df = to_engine_frames(result)
    engine = FeatureEngine()
    vectors = engine.compute_batch(bureau_df, bank_df, itr_df)
    cross_source_by_id = compute_batch_cross_source(result)
    vectors = merge_into_vectors(vectors, cross_source_by_id)

    master_by_id = {m.applicant_id: m.model_dump() for m in result.master}
    bureau_by_id = {b.applicant_id: b.model_dump() for b in result.bureau}
    bank_by_id = {b.applicant_id: b.model_dump() for b in result.bank}
    return master_by_id, bureau_by_id, bank_by_id, vectors


def build_pipeline_rows(
    pipeline: ApplicantPipeline,
    master_by_id: dict[str, dict],
    bureau_by_id: dict[str, dict],
    bank_by_id: dict[str, dict],
    vectors: list,
) -> tuple[list[str], list[dict], list[dict]]:
    """
    filters the shared dataset to one applicant-type pipeline
    (src/rules/context.py::pipeline_for(), the same function the rules
    engine uses — one place the mapping is defined, not a second copy here)
    and encodes each row. Returns (applicant_ids, encoded_rows, raw_vector_rows):
    encoded_rows are XGBoost-ready (src/scoring/feature_matrix.py),
    raw_vector_rows are the un-encoded engineered dicts proxy_labels.py
    needs (it references engineered field names directly, e.g. dpd_severity_score).
    """
    with get_session() as s:
        weighted_fields = active_weighted_fields_for_pipeline(s, pipeline)

    applicant_ids: list[str] = []
    encoded_rows: list[dict] = []
    raw_vector_rows: list[dict] = []

    for vector in vectors:
        master_row = master_by_id.get(vector.applicant_id)
        if master_row is None:
            continue
        if pipeline_for(ApplicantType(master_row["applicant_type"])) != pipeline:
            continue

        feature_vector_row = vector.model_dump()
        bureau_row = bureau_by_id.get(vector.applicant_id)
        bank_row = bank_by_id.get(vector.applicant_id)
        context = build_rule_context(master_row, feature_vector_row, bureau_row=bureau_row, bank_row=bank_row)
        admin_signal = compute_weighted_risk_signal(weighted_fields, context)

        applicant_ids.append(vector.applicant_id)
        encoded_rows.append(build_feature_row(master_row, feature_vector_row, admin_signal))
        raw_vector_rows.append(feature_vector_row)

    return applicant_ids, encoded_rows, raw_vector_rows


def train_pipeline_model(
    pipeline: ApplicantPipeline,
    master_by_id: dict[str, dict],
    bureau_by_id: dict[str, dict],
    bank_by_id: dict[str, dict],
    vectors: list,
    model_dir: Path = DEFAULT_MODEL_DIR,
) -> dict:
    print(f"=== training {pipeline.value} model ===")
    applicant_ids, encoded_rows, raw_vector_rows = build_pipeline_rows(pipeline, master_by_id, bureau_by_id, bank_by_id, vectors)
    n = len(applicant_ids)
    print(f"{pipeline.value}: {n} applicants")

    feature_columns = get_feature_columns(raw_vector_rows[0])
    X = np.array([[row[col] for col in feature_columns] for row in encoded_rows], dtype=np.float64)
    y, proxy_probabilities = sample_proxy_labels(raw_vector_rows)
    print(f"{pipeline.value}: feature matrix {X.shape}, positive rate {y.mean():.3f}")

    can_stratify = min(np.bincount(y)) >= 2
    train_idx, val_idx = train_test_split(
        np.arange(n), test_size=0.2, random_state=42, stratify=y if can_stratify else None
    )
    X_train, X_val = X[train_idx], X[val_idx]
    y_train, y_val = y[train_idx], y[val_idx]

    model = xgb.XGBClassifier(
        tree_method="hist", max_depth=5, learning_rate=0.08, n_estimators=300,
        eval_metric=["auc", "logloss"], early_stopping_rounds=25,
        subsample=0.85, colsample_bytree=0.85, random_state=42, objective="binary:logistic",
    )
    model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)

    val_probs = model.predict_proba(X_val)[:, 1]
    val_preds = (val_probs > 0.5).astype(int)
    metrics = {
        "pipeline": pipeline.value,
        "n_samples": n,
        "n_features": len(feature_columns),
        "positive_rate": float(y.mean()),
        "val_auc": float(roc_auc_score(y_val, val_probs)),
        "val_log_loss": float(log_loss(y_val, val_probs)),
        "val_accuracy": float(accuracy_score(y_val, val_preds)),
        "val_precision": float(precision_score(y_val, val_preds, zero_division=0)),
        "val_recall": float(recall_score(y_val, val_preds, zero_division=0)),
        "val_f1": float(f1_score(y_val, val_preds, zero_division=0)),
    }

    importances = model.feature_importances_
    top_features = sorted(zip(feature_columns, importances.tolist()), key=lambda t: t[1], reverse=True)[:10]
    metrics["top_features"] = [{"feature": f, "importance": imp} for f, imp in top_features]

    for k in ("val_auc", "val_log_loss", "val_accuracy", "val_precision", "val_recall", "val_f1"):
        print(f"  {k}: {metrics[k]:.4f}")
    print("  top features:", ", ".join(f for f, _ in top_features[:5]))

    pipeline_dir = model_dir / pipeline.value.lower()
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, pipeline_dir / "model.pkl")
    with open(pipeline_dir / "feature_columns.json", "w") as fh:
        json.dump(feature_columns, fh, indent=2)
    with open(pipeline_dir / "metrics.json", "w") as fh:
        json.dump(metrics, fh, indent=2)

    print(f"  saved -> {pipeline_dir}")
    return metrics


def run_training_pipeline(model_dir: Path = DEFAULT_MODEL_DIR) -> dict[str, dict]:
    master_by_id, bureau_by_id, bank_by_id, vectors = load_dataset()
    return {
        pipeline.value: train_pipeline_model(pipeline, master_by_id, bureau_by_id, bank_by_id, vectors, model_dir)
        for pipeline in (ApplicantPipeline.INDIVIDUAL, ApplicantPipeline.MSME)
    }


if __name__ == "__main__":
    run_training_pipeline()
