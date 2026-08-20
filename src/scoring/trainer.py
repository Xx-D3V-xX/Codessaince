"""
Tier 4 — XGBoost Digital-Twin Credit Scorer & Model Trainer
src/scoring/trainer.py

Reads data/raw/applicant_profiles.parquet, engineers feature vectors and proxy default
labels across bureau, banking, ITR, CAS, and GST data, trains XGBoost credit scoring models,
evaluates performance metrics, and saves model weights (.pkl + .ubj) to data/models/.
"""

from __future__ import annotations

import glob
import json
import os
from pathlib import Path
from typing import Tuple, Dict, Any, List

import joblib
import numpy as np
import pandas as pd
import polars as pl
import scipy.sparse as sp
import xgboost as xgb
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split

# ── Feature Column Inventory Contract ──────────────────────────────────────────

FEATURE_COLUMNS: list[str] = [
    # Demographics & Loan Request
    "age",
    "employment_vintage_months",
    "business_vintage_months",
    "declared_income_monthly",
    "declared_existing_obligations",
    "requested_loan_amount",
    "requested_tenure_months",
    "dti_ratio",
    "loan_to_income_ratio",
    "emi_to_income_ratio",
    # Bureau / Credit Information
    "bureau_score",
    "active_loan_count",
    "closed_loan_count",
    "total_sanctioned_amount",
    "total_outstanding_amount",
    "secured_loan_count",
    "unsecured_loan_count",
    "recent_enquiry_count_30d",
    "recent_enquiry_count_90d",
    "recent_enquiry_count_180d",
    "overdue_amount",
    "max_dpd",
    "dpd_recency_months",
    "write_off_flag",
    "write_off_amount",
    "settlement_flag",
    "settlement_amount",
    "default_flag",
    "suit_filed_flag",
    "credit_card_utilization",
    "credit_utilization_ratio",
    "unsecured_share",
    "overdue_to_outstanding",
    # Account Aggregator / Bank Statement
    "num_accounts",
    "current_balance",
    "average_balance",
    "minimum_balance",
    "avg_monthly_credit_inflow",
    "avg_monthly_debit_outflow",
    "upi_inflow",
    "upi_outflow",
    "bounce_count",
    "overdraft_occurrence_count",
    "cash_flow_volatility",
    "salary_credit_detected",
    "business_credit_detected",
    "net_monthly_cashflow",
    "cashflow_margin",
    "balance_stress",
    "bounce_rate",
    # ITR / Income Verification
    "gross_total_income",
    "total_income",
    "income_fy1",
    "income_fy2",
    "income_verification_ratio",
    "itr_salary_income",
    "itr_professional_income",
    "itr_business_income",
    "itr_interest_income",
    "itr_dividend_income",
    "itr_capital_gains",
    "itr_other_income",
    "itr_deductions",
    "itr_tax_paid",
    "income_growth_rate",
    "effective_tax_rate",
    "deduction_ratio",
    # CAS / Investment Assets
    "mutual_fund_value",
    "equity_value",
    "liquid_asset_value",
    "total_portfolio_value",
    "cas_bond_value",
    "cas_etf_value",
    "cas_other_securities_value",
    "cas_recent_redemption_value",
    "cas_recent_purchase_value",
    "portfolio_to_income",
    # GST / Business Tax
    "turnover",
    "taxable_turnover",
    "gst_filing_consistency_percent",
    "gst_tax_liability",
    "gst_tax_paid",
    "gst_gstr1_filing_consistency_pct",
    "gst_gstr3b_filing_consistency_pct",
    "gst_tax_paid_ratio",
    # Categoricals (Encoded)
    "applicant_type_code",
    "employment_type_code",
    "geography_type_code",
    "itr_type_code",
    "inflow_trend_code",
    "ais_itr_consistency_code",
]

LABEL_ENCODER: dict = {
    "very_low_risk": {"score_min": 750, "score_max": 900, "pd_max": 0.25, "wc_max_lakh": 50, "term_max_lakh": 100},
    "low_risk":      {"score_min": 650, "score_max": 749, "pd_max": 0.42, "wc_max_lakh": 25, "term_max_lakh": 50},
    "medium_risk":   {"score_min": 550, "score_max": 649, "pd_max": 0.58, "wc_max_lakh": 10, "term_max_lakh": 25},
    "high_risk":     {"score_min": 300, "score_max": 549, "pd_max": 1.00, "wc_max_lakh": 5,  "term_max_lakh": 0},
}


# ── Utility Helpers ─────────────────────────────────────────────────────────────

def sanitize_feature_name(name: str) -> str:
    for ch in ("<", ">", "[", "]", " ", "-"):
        name = name.replace(ch, "_")
    return name


def safe_div(num: np.ndarray, den: np.ndarray, floor: float = 1.0) -> np.ndarray:
    """Vectorized element-wise safe division."""
    den_safe = np.maximum(np.nan_to_num(den, nan=0.0), floor)
    num_safe = np.nan_to_num(num, nan=0.0)
    return np.round(num_safe / den_safe, 4)


# ── Feature Engineering Pipeline ─────────────────────────────────────────────────

def engineer_features(df_raw: pl.DataFrame) -> pl.DataFrame:
    """
    Computes derived ratios, risk signals, and categorical encodings from applicant profiles.
    """
    print(f"[trainer] engineering feature matrix from {len(df_raw):,} raw profiles ...")
    
    # Convert to pandas for vectorized feature generation
    df = df_raw.to_pandas()
    n = len(df)

    def _get_col(col_name: str, default: float = 0.0) -> np.ndarray:
        if col_name in df.columns:
            return df[col_name].fillna(default).to_numpy(dtype=float)
        return np.full(n, default, dtype=float)
    
    # 1. Demographics & Loan Ratios
    inc_m = np.maximum(_get_col("declared_income_monthly", 0.0), 1.0)
    ob_m  = _get_col("declared_existing_obligations", 0.0)
    loan_amt = _get_col("requested_loan_amount", 0.0)
    tenure_m = np.maximum(_get_col("requested_tenure_months", 12.0), 1.0)
    
    # Approx EMI on requested loan (assume 14% p.a. interest)
    r = 0.14 / 12.0
    req_emi = loan_amt * r * ((1 + r) ** tenure_m) / np.maximum(((1 + r) ** tenure_m) - 1, 1e-5)
    
    df["dti_ratio"] = safe_div(ob_m, inc_m)
    df["loan_to_income_ratio"] = safe_div(loan_amt, inc_m * 12.0)
    df["emi_to_income_ratio"] = safe_div(ob_m + req_emi, inc_m)
    
    # 2. Bureau Ratios
    sanct = _get_col("total_sanctioned_amount", 0.0)
    outst = _get_col("total_outstanding_amount", 0.0)
    overd = _get_col("overdue_amount", 0.0)
    active_l = _get_col("active_loan_count", 0.0)
    closed_l = _get_col("closed_loan_count", 0.0)
    unsec_l  = _get_col("unsecured_loan_count", 0.0)
    
    df["credit_utilization_ratio"] = safe_div(outst, sanct)
    df["unsecured_share"] = safe_div(unsec_l, active_l + closed_l)
    df["overdue_to_outstanding"] = safe_div(overd, outst)
    
    # 3. Banking Ratios
    cr_in = _get_col("avg_monthly_credit_inflow", 0.0)
    db_out = _get_col("avg_monthly_debit_outflow", 0.0)
    avg_bal = _get_col("average_balance", 0.0)
    min_bal = _get_col("minimum_balance", 0.0)
    bounces = _get_col("bounce_count", 0.0)
    
    df["net_monthly_cashflow"] = cr_in - db_out
    df["cashflow_margin"] = safe_div(cr_in - db_out, cr_in)
    df["balance_stress"] = safe_div(min_bal, avg_bal)
    df["bounce_rate"] = safe_div(bounces, np.full_like(bounces, 12.0))
    
    # 4. ITR Ratios
    gti = _get_col("gross_total_income", 0.0)
    tot_inc = _get_col("total_income", 0.0)
    fy1 = _get_col("income_fy1", 0.0)
    fy2 = _get_col("income_fy2", 0.0)
    tax_pd = _get_col("itr_tax_paid", 0.0)
    deduct = _get_col("itr_deductions", 0.0)
    
    df["income_growth_rate"] = safe_div(fy1 - fy2, fy2)
    df["effective_tax_rate"] = safe_div(tax_pd, tot_inc)
    df["deduction_ratio"] = safe_div(deduct, gti)
    
    # 5. CAS Ratios
    port_val = _get_col("total_portfolio_value", 0.0)
    df["portfolio_to_income"] = safe_div(port_val, gti)
    
    # 6. GST Ratios
    gst_liab = _get_col("gst_tax_liability", 0.0)
    gst_pd   = _get_col("gst_tax_paid", 0.0)
    df["gst_tax_paid_ratio"] = safe_div(gst_pd, gst_liab)
    
    # 7. Categorical Label Encodings
    cat_mappings = {
        "applicant_type": {"SALARIED": 0, "SELF_EMPLOYED": 1, "MSME": 2, "CORPORATE": 3},
        "employment_type": {"SALARIED": 0, "SELF_EMPLOYED": 1, "BUSINESS_OWNER": 2},
        "geography_type": {"METRO": 0, "URBAN": 1, "SEMI_URBAN": 2, "RURAL": 3},
        "itr_type": {"ITR1": 0, "ITR2": 1, "ITR3": 2, "ITR4": 3, "ITR5": 4, "ITR6": 5},
        "inflow_trend": {"GROWING": 0, "FLAT": 1, "DECLINING": 2},
        "ais_itr_consistency": {"HIGH": 0, "MINOR_DISCREPANCY": 1, "MODERATE_DISCREPANCY": 2, "MAJOR_DISCREPANCY": 3},
    }
    
    for src_col, mapping in cat_mappings.items():
        out_col = f"{src_col}_code"
        if src_col in df.columns:
            df[out_col] = df[src_col].map(mapping).fillna(-1).astype(int)
        else:
            df[out_col] = -1
            
    return pl.DataFrame(df)


# ── Noisy Rule-Based Risk Target Generation ──────────────────────────────────────

def generate_proxy_labels(df: pl.DataFrame) -> np.ndarray:
    """
    Generates realistic credit default target labels y in {0, 1}.
    Anchored on empirical bureau events (write-offs, defaults, DPD, bounces) + stress signals.
    """
    n = len(df)
    print(f"[trainer] generating target risk labels for {n:,} applicants ...")
    
    def _col(name: str, default: float = 0.0) -> np.ndarray:
        if name in df.columns:
            return df[name].fill_null(default).to_numpy().astype(float)
        return np.full(n, default)
    
    def _bool_col(name: str) -> np.ndarray:
        if name in df.columns:
            vals = df[name].to_numpy()
            return np.array([True if str(v).lower() in ("true", "1", "1.0") else False for v in vals])
        return np.zeros(n, dtype=bool)

    # Base default risk starts at 0.18
    risk_score = np.full(n, 0.18, dtype=float)

    # 1. Hard Bureau Negatives (strongest credit risk signals)
    write_off   = _bool_col("write_off_flag")
    settlement  = _bool_col("settlement_flag")
    default_flg = _bool_col("default_flag")
    suit_filed  = _bool_col("suit_filed_flag")

    hard_negative = write_off | settlement | default_flg | suit_filed
    risk_score = np.where(hard_negative, risk_score + 0.45, risk_score)

    # 2. Bureau Score & Delinquency
    bureau_score = _col("bureau_score", default=750.0)
    max_dpd      = _col("max_dpd")
    overdue_amt  = _col("overdue_amount")

    risk_score = np.where(bureau_score < 600, risk_score + 0.25, risk_score)
    risk_score = np.where(bureau_score < 650, risk_score + 0.12, risk_score)
    risk_score = np.where(bureau_score > 750, risk_score - 0.15, risk_score)

    risk_score = np.where(max_dpd >= 90, risk_score + 0.30, risk_score)
    risk_score = np.where((max_dpd >= 30) & (max_dpd < 90), risk_score + 0.15, risk_score)
    risk_score = np.where(overdue_amt > 0, risk_score + 0.18, risk_score)

    # 3. Banking Distress & Bounces
    bounces     = _col("bounce_count")
    net_cf      = _col("net_monthly_cashflow")
    bal_stress  = _col("balance_stress")
    cash_vol    = _col("cash_flow_volatility")

    risk_score = np.where(bounces >= 3, risk_score + 0.22, risk_score)
    risk_score = np.where(bounces == 1, risk_score + 0.08, risk_score)
    risk_score = np.where(net_cf < 0, risk_score + 0.15, risk_score)
    risk_score = np.where(bal_stress < 0.1, risk_score + 0.10, risk_score)
    risk_score = np.where(cash_vol > 50000, risk_score + 0.10, risk_score)

    # 4. Income Verification & Obligations (DTI)
    dti         = _col("dti_ratio")
    iv_ratio    = _col("income_verification_ratio", default=1.0)
    ais_consist = _col("ais_itr_consistency_code")

    risk_score = np.where(dti > 0.60, risk_score + 0.20, risk_score)
    risk_score = np.where(dti > 0.40, risk_score + 0.10, risk_score)
    risk_score = np.where(iv_ratio < 0.70, risk_score + 0.15, risk_score)
    risk_score = np.where(ais_consist >= 2, risk_score + 0.12, risk_score)  # moderate/major discrepancy

    # 5. GST / Business Risk
    gst_filing  = _col("gst_filing_consistency_percent", default=100.0)
    risk_score = np.where(gst_filing < 80.0, risk_score + 0.14, risk_score)

    # Add Gaussian noise (sigma = 0.05) to model natural variance
    rng = np.random.default_rng(42)
    noise = rng.normal(0, 0.05, n)
    risk_score = np.clip(risk_score + noise, 0.02, 0.98)

    # Convert to binary target (Default = 1, Non-Default = 0)
    y_binary = (risk_score > 0.50).astype(np.int32)
    pos_pct = (y_binary.sum() / n) * 100.0
    print(f"[trainer] target labels generated — defaults (positives): {y_binary.sum():,} ({pos_pct:.1f}%), non-defaults: {(y_binary == 0).sum():,}")
    
    return y_binary


# ── Feature Matrix Extraction ─────────────────────────────────────────────────────

def build_feature_matrix(df: pl.DataFrame) -> Tuple[np.ndarray, List[str]]:
    """Extract ordered feature matrix matching FEATURE_COLUMNS contract."""
    existing = set(df.columns)
    exprs = []

    # Handle boolean column casts
    for col in FEATURE_COLUMNS:
        if col in existing and df[col].dtype == pl.Boolean:
            exprs.append(pl.col(col).cast(pl.Int32))
        elif col not in existing:
            exprs.append(pl.lit(0.0).alias(col))

    if exprs:
        df = df.with_columns(exprs)

    # Select features in exact contract order
    cols = [c for c in FEATURE_COLUMNS if c in df.columns]
    matrix = df.select(cols).to_numpy().astype(np.float32)
    
    # Fill any remaining NaNs / Inf with 0.0
    matrix = np.nan_to_num(matrix, nan=0.0, posinf=0.0, neginf=0.0)
    sanitized = [sanitize_feature_name(c) for c in cols]
    return matrix, sanitized


def to_sparse_if_needed(X: np.ndarray, threshold: float = 0.5) -> np.ndarray | sp.csr_matrix:
    """Converts dense matrix to SciPy CSR matrix if sparsity exceeds threshold."""
    sparsity = float(np.sum(X == 0)) / float(X.size)
    if sparsity > threshold:
        return sp.csr_matrix(X)
    return X


# ── Model Training & Evaluation Engine ────────────────────────────────────────────

def train_and_save_model(
    X: np.ndarray | sp.csr_matrix,
    y: np.ndarray,
    feature_names: List[str],
    model_dir: Path,
    output_name: str = "xgb_digital_twin",
    early_stopping_rounds: int = 25,
) -> Tuple[xgb.XGBClassifier, Dict[str, Any]]:
    """
    Trains XGBoost hist binary classifier with 80/20 train-validation split.
    Saves model weights to .pkl (joblib) AND .ubj (XGBoost universal binary).
    Returns model and evaluation metrics dictionary.
    """
    print(f"\n▶ Training model '{output_name}' on {X.shape[0]:,} samples x {X.shape[1]} features ...")
    
    # 80/20 train-val split
    indices = np.arange(len(y))
    train_idx, val_idx = train_test_split(indices, test_size=0.20, random_state=42, stratify=y)

    def _prep(idx: np.ndarray) -> np.ndarray | sp.csr_matrix:
        sub = X[idx]
        dense = sub.toarray() if sp.issparse(sub) else sub
        return to_sparse_if_needed(dense)

    X_train = _prep(train_idx)
    X_val   = _prep(val_idx)
    y_train = y[train_idx]
    y_val   = y[val_idx]

    # Model configuration
    model = xgb.XGBClassifier(
        tree_method="hist",
        max_depth=6,
        learning_rate=0.08,
        n_estimators=350,
        eval_metric=["auc", "logloss"],
        early_stopping_rounds=early_stopping_rounds,
        subsample=0.85,
        colsample_bytree=0.85,
        scale_pos_weight=1.0,
        random_state=42,
        objective="binary:logistic",
        base_score=0.5,
    )

    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=False,
    )

    # Predict probabilities and evaluate
    val_probs = model.predict_proba(X_val)[:, 1]
    val_preds = (val_probs > 0.50).astype(int)

    auc      = roc_auc_score(y_val, val_probs)
    lloss    = log_loss(y_val, val_probs)
    accuracy = accuracy_score(y_val, val_preds)
    precision= precision_score(y_val, val_preds, zero_division=0)
    recall   = recall_score(y_val, val_preds, zero_division=0)
    f1       = f1_score(y_val, val_preds, zero_division=0)

    print(f"  ✓ Validation AUC      : {auc:.4f}")
    print(f"  ✓ Validation Log Loss : {lloss:.4f}")
    print(f"  ✓ Accuracy            : {accuracy * 100:.2f}%")
    print(f"  ✓ Precision / Recall  : {precision:.4f} / {recall:.4f}")
    print(f"  ✓ F1 Score            : {f1:.4f}")

    # Calculate Top 10 Feature Importances
    importances = model.feature_importances_
    sorted_idx = np.argsort(importances)[::-1]
    top_features = [
        {"feature": feature_names[i], "importance": float(importances[i])}
        for i in sorted_idx[:15]
    ]

    print("\n  Top 10 Feature Importances:")
    for tf in top_features[:10]:
        print(f"    - {tf['feature']:35s} : {tf['importance'] * 100:.2f}%")

    # Save artifacts
    model_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Save as .pkl (joblib) format
    pkl_path = model_dir / f"{output_name}.pkl"
    joblib.dump(model, pkl_path)
    print(f"  ✅ Saved PKL weights → {pkl_path}")

    # 2. Save as .ubj (XGBoost UBJ) format
    ubj_path = model_dir / f"{output_name}.ubj"
    model.save_model(str(ubj_path))
    print(f"  ✅ Saved UBJ model   → {ubj_path}")

    # 3. Save feature columns & label encoder metadata
    with open(model_dir / "feature_columns.json", "w") as fh:
        json.dump(feature_names, fh, indent=2)

    with open(model_dir / "label_encoder.json", "w") as fh:
        json.dump(LABEL_ENCODER, fh, indent=2)

    metrics = {
        "model_name": output_name,
        "n_samples": int(X.shape[0]),
        "n_features": int(X.shape[1]),
        "val_auc": float(auc),
        "val_log_loss": float(lloss),
        "val_accuracy": float(accuracy),
        "val_precision": float(precision),
        "val_recall": float(recall),
        "val_f1_score": float(f1),
        "top_features": top_features,
    }

    with open(model_dir / f"{output_name}_metrics.json", "w") as fh:
        json.dump(metrics, fh, indent=2)

    return model, metrics


# ── Full Pipeline Entry Point ─────────────────────────────────────────────────────

def run_training_pipeline(
    data_path: str = "data/raw/applicant_profiles.parquet",
    model_dir: str = "data/models",
    force_retrain: bool = False,
) -> None:
    """Executes the complete model training workflow."""
    raw_path = Path(data_path)
    model_path = Path(model_dir)

    print("=" * 70)
    print(" XGBoost Digital-Twin Credit Scorer — Offline Model Trainer")
    print("=" * 70)

    sentinel_pkl = model_path / "xgb_digital_twin.pkl"
    if sentinel_pkl.exists() and not force_retrain:
        print(f"⚡ Model '{sentinel_pkl}' already exists. Pass --force to retrain.")
        return

    if not raw_path.exists():
        print(f"❌ Error: {raw_path} not found. Please run data generation phase first.")
        return

    print(f"[trainer] loading raw dataset: {raw_path} ...")
    df_raw = pl.read_parquet(raw_path)
    print(f"  Loaded {len(df_raw):,} applicant records")

    # Feature Engineering
    df_features = engineer_features(df_raw)

    # Label Generation
    y = generate_proxy_labels(df_features)

    # Feature Matrix Construction
    X, feature_names = build_feature_matrix(df_features)
    print(f"[trainer] final feature matrix shape: {X.shape}")

    X_input = to_sparse_if_needed(X)

    # 1. Main Model: Full Feature Set
    model_full, metrics_full = train_and_save_model(
        X_input, y, feature_names, model_path, output_name="xgb_digital_twin"
    )

    # 2. Variant Model: Income-Heavy (Zero out discretionary & GST features for baseline parity)
    X_income = X.copy()
    disc_cols = {
        "gst_30d_value", "ewb_30d_value", "gst_filing_compliance_rate",
        "gst_tax_liability", "gst_tax_paid", "gst_gstr1_filing_consistency_pct",
        "gst_gstr3b_filing_consistency_pct", "cas_recent_redemption_value"
    }
    for i, col in enumerate(feature_names):
        if col in disc_cols:
            X_income[:, i] = 0.0

    print("\n----------------------------------------------------------------------")
    print("[trainer] training secondary variant: Income-Heavy Model ...")
    train_and_save_model(
        to_sparse_if_needed(X_income), y, feature_names, model_path, output_name="xgb_digital_twin_income_heavy"
    )

    print("\n" + "=" * 70)
    print(" ✅ TRAINER COMPLETE — All models and weights saved to data/models/")
    print("=" * 70)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Credit Scoring Model Trainer")
    parser.add_argument("--force", action="store_true", help="Force retrain models")
    parser.add_argument("--data-path", type=str, default="data/raw/applicant_profiles.parquet")
    parser.add_argument("--model-dir", type=str, default="data/models")
    args = parser.parse_args()

    run_training_pipeline(
        data_path=args.data_path,
        model_dir=args.model_dir,
        force_retrain=args.force,
    )
