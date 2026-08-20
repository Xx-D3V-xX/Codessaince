"""
train_model.py — Standalone executable script to train XGBoost Credit Scoring models.
"""

from src.scoring.trainer import run_training_pipeline

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Train XGBoost Credit Scoring Model")
    parser.add_argument("--force", action="store_true", help="Force retrain models even if data/models/ exists")
    parser.add_argument("--data-path", type=str, default="data/raw/applicant_profiles.parquet")
    parser.add_argument("--model-dir", type=str, default="data/models")
    args = parser.parse_args()

    run_training_pipeline(
        data_path=args.data_path,
        model_dir=args.model_dir,
        force_retrain=args.force,
    )
