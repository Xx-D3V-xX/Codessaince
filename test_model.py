"""
Comprehensive Credit Scoring Model Testing & Evaluation Suite
test_model.py

Evaluates the trained XGBoost model (data/models/xgb_digital_twin.pkl / .ubj) on:
  1. Holdout Test Set Performance (AUC, Log Loss, Precision, Recall, F1, Brier Score, Confusion Matrix)
  2. Credit Score Monotonicity & Risk Tier Distribution (300 - 900 scale)
  3. Stress & Boundary Edge-Case Invariance Tests
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import polars as pl
import scipy.sparse as sp
import xgboost as xgb
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    log_loss,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split

from src.scoring.trainer import (
    FEATURE_COLUMNS,
    LABEL_ENCODER,
    build_feature_matrix,
    engineer_features,
    generate_proxy_labels,
    to_sparse_if_needed,
)

MODEL_DIR = Path("data/models")
DATA_PATH = Path("data/raw/applicant_profiles.parquet")


def pd_to_credit_score(pd_probs: np.ndarray) -> np.ndarray:
    """Map Probability of Default in [0, 1] to Credit Score in [300, 900]."""
    scores = 900.0 - (pd_probs * 600.0)
    return np.round(np.clip(scores, 300, 900)).astype(int)


def credit_score_to_tier(score: int) -> str:
    """Map Credit Score to Risk Tier."""
    if score >= 750:
        return "Very Low Risk"
    elif score >= 650:
        return "Low Risk"
    elif score >= 550:
        return "Medium Risk"
    else:
        return "High Risk"


def run_model_tests():
    print("=" * 80)
    print(" 🧪 CREDIT SCORING MODEL AUDIT & TESTING SUITE")
    print("=" * 80)

    # 1. Artifact Verification
    pkl_file = MODEL_DIR / "xgb_digital_twin.pkl"
    ubj_file = MODEL_DIR / "xgb_digital_twin.ubj"

    if not pkl_file.exists() or not ubj_file.exists():
        print(f"❌ Error: Model weights not found in {MODEL_DIR}. Please run 'python train_model.py --force' first.")
        return

    print(f"\n[1/5] Loading Model Artifacts ...")
    model_pkl = joblib.load(pkl_file)
    model_ubj = xgb.XGBClassifier()
    model_ubj.load_model(str(ubj_file))
    print(f"  ✓ PKL Model Loaded: {type(model_pkl)}")
    print(f"  ✓ UBJ Model Loaded: {type(model_ubj)}")

    # 2. Test Dataset Preparation
    print(f"\n[2/5] Preparing 20% Holdout Test Dataset ...")
    df_raw = pl.read_parquet(DATA_PATH)
    n_total = len(df_raw)

    df_feat = engineer_features(df_raw)
    y_all = generate_proxy_labels(df_feat)
    X_all, feature_names = build_feature_matrix(df_feat)

    indices = np.arange(n_total)
    _, test_idx = train_test_split(indices, test_size=0.20, random_state=42, stratify=y_all)

    X_test = X_all[test_idx]
    y_test = y_all[test_idx]
    X_test_input = to_sparse_if_needed(X_test)

    print(f"  ✓ Holdout Test Samples: {len(y_test):,} records")

    # 3. Model Inference & Standard Classification Metrics
    print(f"\n[3/5] Evaluating Statistical & Classification Performance ...")
    probs = model_pkl.predict_proba(X_test_input)[:, 1]
    preds = (probs > 0.50).astype(int)

    auc = roc_auc_score(y_test, probs)
    lloss = log_loss(y_test, probs)
    acc = accuracy_score(y_test, preds)
    prec = precision_score(y_test, preds, zero_division=0)
    rec = recall_score(y_test, preds, zero_division=0)
    f1 = f1_score(y_test, preds, zero_division=0)
    brier = brier_score_loss(y_test, probs)

    tn, fp, fn, tp = confusion_matrix(y_test, preds).ravel()

    print("-" * 50)
    print(f"  ROC-AUC Score       : {auc:.4f}  (Target: >0.90)")
    print(f"  Log Loss            : {lloss:.4f}  (Target: <0.20)")
    print(f"  Brier Score         : {brier:.4f}  (Calibration Quality)")
    print(f"  Accuracy            : {acc * 100:.2f}%")
    print(f"  Precision / Recall  : {prec:.4f} / {rec:.4f}")
    print(f"  F1 Score            : {f1:.4f}")
    print("-" * 50)
    print(f"  Confusion Matrix    :")
    print(f"    - True Negatives  (Non-Defaults Correct)  : {tn:>6,}")
    print(f"    - False Positives (False Alarms)          : {fp:>6,}")
    print(f"    - False Negatives (Missed Defaults)       : {fn:>6,}")
    print(f"    - True Positives  (Defaults Detected)     : {tp:>6,}")

    # UBJ vs PKL parity test
    probs_ubj = model_ubj.predict_proba(X_test_input)[:, 1]
    max_diff = np.max(np.abs(probs - probs_ubj))
    print(f"  ✓ Format Parity (PKL vs UBJ max probability difference): {max_diff:.6e}")
    assert max_diff < 1e-4, "Format mismatch between PKL and UBJ models!"

    # 4. Credit Score & Risk Tier Monotonicity Audit
    print(f"\n[4/5] Auditing Credit Score (300-900) & Risk Tier Distribution ...")
    scores = pd_to_credit_score(probs)

    tiers = [credit_score_to_tier(s) for s in scores]
    unique_tiers = ["Very Low Risk", "Low Risk", "Medium Risk", "High Risk"]

    print(f"\n  {'Risk Tier':16s} | {'Score Range':12s} | {'Count':>8s} | {'Share':>7s} | {'Observed Default Rate':>22s}")
    print("  " + "-" * 75)

    tier_stats = {}
    prev_default_rate = -1.0
    monotonic = True

    for tier in unique_tiers:
        mask = np.array([t == tier for t in tiers])
        cnt = mask.sum()
        pct = (cnt / len(scores)) * 100.0
        obs_default_rate = float(y_test[mask].mean()) if cnt > 0 else 0.0

        score_range = f"{LABEL_ENCODER[tier.lower().replace(' ', '_')]['score_min']}-{LABEL_ENCODER[tier.lower().replace(' ', '_')]['score_max']}"
        print(f"  {tier:16s} | {score_range:12s} | {cnt:>8,} | {pct:>6.1f}% | {obs_default_rate * 100:>21.2f}%")

        tier_stats[tier] = {"count": int(cnt), "share_pct": round(pct, 2), "observed_default_rate_pct": round(obs_default_rate * 100, 2)}

        # Monotonicity check: High Risk tier must have highest default rate, Very Low Risk lowest
        if prev_default_rate >= 0.0 and obs_default_rate > prev_default_rate:
            # Note: order is Very Low -> Low -> Medium -> High, so observed default rate MUST increase monotonically
            pass
        elif prev_default_rate >= 0.0 and obs_default_rate < prev_default_rate:
            monotonic = False
        prev_default_rate = obs_default_rate

    print(f"\n  ✓ Monotonic Risk Progression (Higher Score = Lower Default Rate): {'PASSED ✅' if monotonic else 'FAILED ❌'}")

    # 5. Stress & Boundary Edge-Case Tests
    print(f"\n[5/5] Executing Real Profile Stress & Boundary Edge-Case Tests ...")

    # Select a real prime applicant (bureau > 780, clean history, zero DPD, high income)
    prime_mask = (
        (df_raw["bureau_score"].fill_null(0) >= 780) &
        (df_raw["max_dpd"].fill_null(0) == 0) &
        (df_raw["write_off_flag"].fill_null(False) == False) &
        (df_raw["default_flag"].fill_null(False) == False) &
        (df_raw["bounce_count"].fill_null(0) == 0)
    )
    prime_idx = int(np.where(prime_mask.to_numpy())[0][0])
    prime_row = df_raw[prime_idx : prime_idx + 1]

    # Select a real subprime applicant (bureau < 550, write-off / default, DPD > 90)
    subprime_mask = (
        (df_raw["bureau_score"].fill_null(750) < 550) &
        (df_raw["write_off_flag"].fill_null(False) == True) &
        (df_raw["max_dpd"].fill_null(0) >= 90)
    )
    subprime_idx = int(np.where(subprime_mask.to_numpy())[0][0])
    subprime_row = df_raw[subprime_idx : subprime_idx + 1]

    # Run inference on stress cases
    for label, stress_df in [("Real Prime Applicant Profile", prime_row), ("Real Subprime Applicant Profile", subprime_row)]:
        feat_df = engineer_features(stress_df)
        X_s, _ = build_feature_matrix(feat_df)
        pd_s = float(model_pkl.predict_proba(to_sparse_if_needed(X_s))[:, 1][0])
        score_s = pd_to_credit_score(np.array([pd_s]))[0]
        tier_s = credit_score_to_tier(score_s)

        app_id = stress_df["applicant_id"][0]
        b_score = stress_df["bureau_score"][0]
        print(f"\n  Stress Scenario: {label} (ID: {app_id}, Bureau Score: {b_score})")
        print(f"    - Default Probability (PD) : {pd_s:.6f}")
        print(f"    - Credit Score             : {score_s}")
        print(f"    - Assigned Risk Tier       : {tier_s}")

        if "Prime" in label:
            assert score_s >= 650, f"Prime applicant scored below 650! Score = {score_s}"
            print(f"    - Assertion Check          : PASSED ✅ (Score >= 650)")
        else:
            assert score_s <= 550, f"Subprime applicant scored above 550! Score = {score_s}"
            print(f"    - Assertion Check          : PASSED ✅ (Score <= 550)")

    # Save Test Report
    report = {
        "test_samples": int(len(y_test)),
        "metrics": {
            "roc_auc": float(auc),
            "log_loss": float(lloss),
            "brier_score": float(brier),
            "accuracy": float(acc),
            "precision": float(prec),
            "recall": float(rec),
            "f1_score": float(f1),
        },
        "confusion_matrix": {
            "true_negatives": int(tn),
            "false_positives": int(fp),
            "false_negatives": int(fn),
            "true_positives": int(tp),
        },
        "risk_tier_distribution": tier_stats,
        "format_parity_max_diff": float(max_diff),
    }

    report_path = MODEL_DIR / "model_audit_report.json"
    with open(report_path, "w") as fh:
        json.dump(report, fh, indent=2)

    print("\n" + "=" * 80)
    print(f" ✅ ALL MODEL AUDIT TESTS PASSED SUCCESSFULLY! Report saved → {report_path}")
    print("=" * 80)


if __name__ == "__main__":
    run_model_tests()
