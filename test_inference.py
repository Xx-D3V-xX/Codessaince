import joblib
import polars as pl
from src.scoring.trainer import engineer_features, build_feature_matrix, to_sparse_if_needed

def test():
    model = joblib.load("data/models/xgb_digital_twin.pkl")
    df_raw = pl.read_parquet("data/raw/applicant_profiles.parquet").head(5)
    df_feat = engineer_features(df_raw)
    X, _ = build_feature_matrix(df_feat)
    probs = model.predict_proba(to_sparse_if_needed(X))[:, 1]

    print("\n" + "="*80)
    print(" SAMPLE INFERENCE OUTPUT FROM TRAINED MODEL (.pkl)")
    print("="*80)
    
    for i, r in enumerate(df_raw.to_dicts()):
        pd_val = float(probs[i])
        score = int(round(900 - pd_val * 600))
        if score >= 750:
            tier = "Very Low Risk"
        elif score >= 650:
            tier = "Low Risk"
        elif score >= 550:
            tier = "Medium Risk"
        else:
            tier = "High Risk"
            
        print(f"ID: {r['applicant_id']} | Type: {r['applicant_type']:13s} | Bureau: {str(r['bureau_score']):>4s} | DTI: {r['declared_existing_obligations']/max(r['declared_income_monthly'],1):.2f} | PD: {pd_val:.4f} | Credit Score: {score} | Tier: {tier}")

if __name__ == "__main__":
    test()
