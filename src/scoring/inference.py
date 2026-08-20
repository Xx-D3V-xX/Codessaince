"""
inference.py — loads a pipeline's trained model + feature column order for
scoring a single applicant (src/pricing/eligibility.py's risk-grade
computation), as opposed to trainer.py's batch training path. Small,
in-process cache so a request-serving process (Phase 8's API layer) doesn't
re-read the same .pkl off disk on every call.
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import xgboost as xgb

from src.db.models import ApplicantPipeline

DEFAULT_MODEL_DIR = Path("data/models")

_MODEL_CACHE: dict[ApplicantPipeline, tuple[xgb.XGBClassifier, list[str]]] = {}


def load_model(pipeline: ApplicantPipeline, model_dir: Path = DEFAULT_MODEL_DIR) -> tuple[xgb.XGBClassifier, list[str]]:
    """returns (model, feature_columns), cached per pipeline for the life of the process."""
    if pipeline in _MODEL_CACHE:
        return _MODEL_CACHE[pipeline]

    pipeline_dir = model_dir / pipeline.value.lower()
    # joblib.load() here is safe: this .pkl is written by src/scoring/trainer.py
    # in this same repo, not fetched from any untrusted external source.
    model = joblib.load(pipeline_dir / "model.pkl")
    with open(pipeline_dir / "feature_columns.json") as fh:
        feature_columns = json.load(fh)

    _MODEL_CACHE[pipeline] = (model, feature_columns)
    return model, feature_columns


def clear_cache() -> None:
    """for tests / after retraining a model, so a stale cached model isn't served."""
    _MODEL_CACHE.clear()
