"""
synth.py — master/bureau/bank/ITR scalar-field generation: Faker + demographic
priors build seed rows, SDV CopulaGANSynthesizer fits and samples correlated
synthetic applicant profiles from them, per CLAUDE.md §6 / the old generator's
build_profiles_copulagan pattern. dpd_history is deliberately NOT part of this
model — see dpd_history.py — it's layered on as a separate deterministic
post-process after sampling, keyed off each row's own max_dpd/dpd_recency/
credit_history_type.

output field names match src/features/schemas.py exactly (BureauRecord,
BankStatementRecord, ITRRecord) — this generator IS the contract, not a
shape to be adapted later. see PROGRESS.md known-issue #5 for why this
matters: the old generator's bounce_count / num_accounts naming drift is
exactly what broke the old engine.py hookup.
"""

from __future__ import annotations

import logging
import math
import random
from datetime import timedelta

import numpy as np
from faker import Faker

from src.ingestion.priors import (
    ACTIVE_LOANS_BUCKETS,
    AGE_BUCKETS,
    APPLICANT_TYPE_DIST,
    BANK_ACCOUNTS_BUCKETS,
    BANK_BOUNCES_BUCKETS,
    BUREAU_SCORE_BUCKETS,
    CC_UTILIZATION_BUCKETS,
    CREDIT_HISTORY_DIST,
    ENQUIRY_30D_BUCKETS,
    GEOGRAPHY_DIST,
    INCOME_VERIFICATION_RATIO_BUCKETS,
    ITR_TYPE_BY_APPLICANT,
    MAX_DPD_BUCKETS,
    MSME_LOAN_AMOUNT_BUCKETS,
    PERSONAL_LOAN_AMOUNT_BUCKETS,
    PERSONAL_LOAN_TENURE_BUCKETS,
    REFERENCE_DATE,
    SALARIED_INCOME_BUCKETS,
    SELF_EMPLOYED_INCOME_BUCKETS,
    STATE_CODES,
    STATE_NAMES,
    clip,
    random_dob,
    sample_bucket,
    weighted_label,
)

logger = logging.getLogger("creditgate.synth")

# columns fed to the CopulaGAN — identifiers / free text excluded, a GAN will
# happily "learn" a PAN-shaped string but it isn't a statistically meaningful
# field, and re-attaching fresh Faker identifiers after sampling keeps every
# row unique and well-formed (same reasoning as the old generator).
_IDENTIFIER_COLS = [
    "applicant_id", "name", "full_name", "email", "pan", "city", "date_of_birth", "account_opening_date",
]

_CATEGORICAL_COLS = [
    "applicant_type", "employment_type", "geography_type", "credit_history_type",
    "state_code", "itr_type", "primary_account_type", "inflow_trend",
    "salary_credit_detected", "business_credit_detected",
    "write_off_flag", "settlement_flag", "default_flag", "suit_filed_flag",
    "max_dpd_label",
]


def build_seed_rows(fake: Faker, n_rows: int) -> list[dict]:
    """Faker + demographic-prior seed rows the CopulaGAN synthesizer is fit
    on. every field name here already matches the target schemas — no rename
    layer between generation and the Pydantic contract."""
    rows: list[dict] = []

    for i in range(n_rows):
        applicant_type = weighted_label(APPLICANT_TYPE_DIST)
        geography = weighted_label(GEOGRAPHY_DIST)
        _, age_f = sample_bucket(AGE_BUCKETS)
        age = int(round(age_f))
        credit_history_type = weighted_label(CREDIT_HISTORY_DIST)
        state_code = random.choice(STATE_CODES)

        is_business = applicant_type in ("MSME", "CORPORATE")

        # ── master: income ──
        if applicant_type == "SALARIED":
            _, declared_income = sample_bucket(SALARIED_INCOME_BUCKETS)
            employment_type = "SALARIED"
        elif applicant_type == "SELF_EMPLOYED":
            _, declared_income = sample_bucket(SELF_EMPLOYED_INCOME_BUCKETS)
            employment_type = "SELF_EMPLOYED"
        else:
            from src.ingestion.priors import MSME_TURNOVER_BUCKETS
            _, turnover = sample_bucket(MSME_TURNOVER_BUCKETS)
            declared_income = clip(turnover * random.uniform(0.02, 0.06) / 12, 10_000, 2_000_000)
            employment_type = "BUSINESS_OWNER"

        if is_business:
            _, requested_amount = sample_bucket(MSME_LOAN_AMOUNT_BUCKETS)
            requested_tenure = random.randint(12, 84)
        else:
            _, requested_amount = sample_bucket(PERSONAL_LOAN_AMOUNT_BUCKETS)
            _, tenure_f = sample_bucket(PERSONAL_LOAN_TENURE_BUCKETS)
            requested_tenure = int(round(tenure_f))

        declared_obligations = round(declared_income * random.uniform(0.0, 0.5), 2)
        employment_vintage_months = random.randint(1, 240) if applicant_type == "SALARIED" else 0
        business_vintage_months = (
            random.randint(1, 300) if is_business or applicant_type == "SELF_EMPLOYED" else 0
        )

        # ── bureau ──
        if credit_history_type == "NTC":
            bureau_score = None
            active_loans, closed_loans = 0, 0
            enquiries_30d = 0
            max_dpd_label, max_dpd = "0", 0
            cc_util = 0.0
        else:
            _, bureau_score_f = sample_bucket(BUREAU_SCORE_BUCKETS)
            bureau_score = int(round(bureau_score_f))
            _, active_loans_f = sample_bucket(ACTIVE_LOANS_BUCKETS)
            active_loans = int(round(active_loans_f))
            closed_loans = max(0, int(round(active_loans * random.uniform(0.3, 1.5))))
            if credit_history_type == "THIN_FILE":
                active_loans = min(active_loans, 1)
                closed_loans = min(closed_loans, 1)
            _, enq_f = sample_bucket(ENQUIRY_30D_BUCKETS)
            enquiries_30d = int(round(enq_f))
            max_dpd_label, max_dpd_f = sample_bucket(MAX_DPD_BUCKETS)
            max_dpd = int(round(max_dpd_f))
            _, cc_util_f = sample_bucket(CC_UTILIZATION_BUCKETS)
            cc_util = clip(cc_util_f, 0, 100)

        write_off_flag = max_dpd_label == "SERIOUS_WRITEOFF" and random.random() < 0.6
        settlement_flag = (
            max_dpd_label == "SERIOUS_WRITEOFF" and not write_off_flag and random.random() < 0.5
        )
        default_flag = write_off_flag or (max_dpd_label == "181+" and random.random() < 0.3)
        suit_filed_flag = write_off_flag and random.random() < 0.15

        enquiries_90d = enquiries_30d + int(np.random.poisson(1.2))
        enquiries_180d = enquiries_90d + int(np.random.poisson(1.5))

        overdue_amount = 0.0
        if max_dpd > 0:
            overdue_amount = round(declared_income * random.uniform(0.1, 1.2), 2)

        total_sanctioned = round((active_loans + closed_loans) * declared_income * random.uniform(2, 8), 2)
        total_outstanding = round(total_sanctioned * random.uniform(0.1, 0.7), 2)
        secured_loan_count = int(round(active_loans * random.uniform(0.0, 0.6)))
        unsecured_loan_count = max(0, active_loans - secured_loan_count)
        dpd_recency_months = 0 if max_dpd == 0 else random.randint(0, 24)
        write_off_amount = round(total_outstanding * 0.4, 2) if write_off_flag else 0.0
        settlement_amount = round(total_outstanding * 0.25, 2) if settlement_flag else 0.0

        # ── bank statement / AA ──
        _, n_accounts_f = sample_bucket(BANK_ACCOUNTS_BUCKETS)
        num_accounts = int(round(n_accounts_f))
        _, bounces_f = sample_bucket(BANK_BOUNCES_BUCKETS)
        bounce_return_count = int(round(bounces_f))
        avg_monthly_credit_inflow = round(declared_income * random.uniform(0.9, 1.3), 2)
        avg_monthly_debit_outflow = round(avg_monthly_credit_inflow * random.uniform(0.6, 1.05), 2)
        average_balance = round(declared_income * random.uniform(0.3, 2.5), 2)
        minimum_balance = round(average_balance * random.uniform(0.05, 0.4), 2)
        current_balance = round(average_balance * random.uniform(0.5, 1.5), 2)
        cash_flow_volatility = round(declared_income * random.uniform(0.02, 0.35), 2)
        inflow_trend = random.choices(["GROWING", "FLAT", "DECLINING"], weights=[0.35, 0.45, 0.20])[0]
        emi_like_recurring_debits_sum = round(declared_income * random.uniform(0.0, 0.45), 2)
        upi_inflow = round(avg_monthly_credit_inflow * random.uniform(0.1, 0.6), 2)
        upi_outflow = round(avg_monthly_debit_outflow * random.uniform(0.2, 0.7), 2)
        neft_rtgs_imps_inflow = round(avg_monthly_credit_inflow * random.uniform(0.1, 0.5), 2)
        neft_rtgs_imps_outflow = round(avg_monthly_debit_outflow * random.uniform(0.1, 0.5), 2)
        cash_deposit_amount = round(average_balance * random.uniform(0.0, 0.2), 2)
        cash_withdrawal_amount = round(average_balance * random.uniform(0.0, 0.25), 2)
        overdraft_occurrence_count = int(np.random.poisson(0.3)) if bounce_return_count > 0 else 0
        current_od_limit = round(average_balance * random.uniform(0.0, 0.5), 2) if random.random() < 0.3 else 0.0
        drawing_limit = round(current_od_limit * random.uniform(1.0, 1.5), 2) if current_od_limit > 0 else 0.0
        statement_months = min(business_vintage_months or employment_vintage_months or 12, 12) or 12
        primary_account_type = random.choices(
            ["SAVINGS", "CURRENT", "SALARY"],
            weights=[0.55, 0.25, 0.20] if not is_business else [0.15, 0.80, 0.05],
        )[0]
        salary_credit_detected = applicant_type == "SALARIED"
        business_credit_detected = is_business or applicant_type == "SELF_EMPLOYED"
        account_opening_vintage_months = employment_vintage_months or business_vintage_months or random.randint(1, 120)

        # ── ITR (yr1 scalar seed — yr2 derived post-sample per applicant, see build_itr_years) ──
        _, iv_ratio = sample_bucket(INCOME_VERIFICATION_RATIO_BUCKETS)
        gross_total_income_yr1 = round(declared_income * 12, 2)
        total_income_yr1 = round(gross_total_income_yr1 * iv_ratio, 2)
        itr_type = random.choice(ITR_TYPE_BY_APPLICANT[applicant_type])

        name = fake.name()
        row: dict = {
            "applicant_id": f"APP{i:06d}",
            "name": name,
            "full_name": name,
            "email": fake.free_email(),
            "pan": fake.bothify(text="?????####?").upper(),
            "city": fake.city(),
            "date_of_birth": random_dob(age),
            "age": age,
            "applicant_type": applicant_type,
            "employment_type": employment_type,
            "geography_type": geography,
            "state_code": state_code,
            "credit_history_type": credit_history_type,
            "employment_vintage_months": employment_vintage_months,
            "business_vintage_months": business_vintage_months,
            "declared_income_monthly": round(declared_income, 2),
            "declared_existing_obligations": declared_obligations,
            "requested_loan_amount": round(requested_amount, 2),
            "requested_tenure_months": requested_tenure,

            # bureau (BureauRecord field names)
            "bureau_score": bureau_score,
            "active_loan_count": active_loans,
            "closed_loan_count": closed_loans,
            "total_sanctioned_amount": total_sanctioned,
            "total_outstanding_amount": total_outstanding,
            "secured_loan_count": secured_loan_count,
            "unsecured_loan_count": unsecured_loan_count,
            "recent_enquiry_count_30d": enquiries_30d,
            "recent_enquiry_count_90d": enquiries_90d,
            "recent_enquiry_count_180d": enquiries_180d,
            "overdue_amount": overdue_amount,
            "max_dpd": max_dpd,
            "max_dpd_label": max_dpd_label,  # dropped before writing bureau parquet — feeds dpd_history.py only
            "dpd_recency_months": dpd_recency_months,
            "write_off_flag": bool(write_off_flag),
            "write_off_amount": write_off_amount,
            "settlement_flag": bool(settlement_flag),
            "settlement_amount": settlement_amount,
            "default_flag": bool(default_flag),
            "suit_filed_flag": bool(suit_filed_flag),
            "credit_card_utilization": round(cc_util, 2),

            # bank statement (BankStatementRecord field names)
            "number_of_accounts": num_accounts,
            "primary_account_type": primary_account_type,  # expanded into account_type list[str] post-sample
            "account_opening_vintage_months": account_opening_vintage_months,
            "current_balance": current_balance,
            "average_balance": average_balance,
            "minimum_balance": minimum_balance,
            "current_od_limit": current_od_limit,
            "drawing_limit": drawing_limit,
            "avg_monthly_credit_inflow": avg_monthly_credit_inflow,
            "avg_monthly_debit_outflow": avg_monthly_debit_outflow,
            "inflow_trend": inflow_trend,
            "emi_like_recurring_debits_sum": emi_like_recurring_debits_sum,
            "salary_credit_detected": salary_credit_detected,
            "business_credit_detected": business_credit_detected,
            "cash_deposit_amount": cash_deposit_amount,
            "cash_withdrawal_amount": cash_withdrawal_amount,
            "upi_inflow": upi_inflow,
            "upi_outflow": upi_outflow,
            "neft_rtgs_imps_inflow": neft_rtgs_imps_inflow,
            "neft_rtgs_imps_outflow": neft_rtgs_imps_outflow,
            "bounce_return_count": bounce_return_count,
            "overdraft_occurrence_count": overdraft_occurrence_count,
            "cash_flow_volatility": cash_flow_volatility,
            "statement_months": statement_months,

            # itr yr1 (ITRRecord field names, unprefixed — yr2 built post-sample)
            "itr_type": itr_type,
            "gross_total_income_yr1": gross_total_income_yr1,
            "total_income_yr1": total_income_yr1,
            "income_verification_ratio": round(iv_ratio, 3),
        }
        rows.append(row)

    return rows


def build_profiles_copulagan(fake: Faker, n_profiles: int, n_seed_rows: int) -> list[dict]:
    """seed rows -> SDV CopulaGANSynthesizer -> n_profiles correlated synthetic
    applicant profiles. no manual fallback — sdv is a hard requirement listed
    in requirements.txt, consistent with CLAUDE.md §6 confirming CopulaGAN as
    the real path (not a fallback)."""
    import pandas as pd
    from sdv.metadata import Metadata
    from sdv.single_table import CopulaGANSynthesizer

    logger.info("building %d seed rows from Faker + demographic priors", n_seed_rows)
    seed_rows = build_seed_rows(fake, n_seed_rows)
    seed_df_full = pd.DataFrame(seed_rows)

    model_cols = [c for c in seed_df_full.columns if c not in _IDENTIFIER_COLS]
    seed_df = seed_df_full[model_cols].copy()

    # sentinel-fill nulls (e.g. bureau_score for NTC applicants) for GAN
    # training, restore to null post-sample — same pattern as the old generator.
    numeric_cols = seed_df.select_dtypes(include=["number", "bool"]).columns.tolist()
    numeric_cols = [c for c in numeric_cols if c not in _CATEGORICAL_COLS]
    for c in numeric_cols:
        seed_df[c] = seed_df[c].fillna(-1)
    for c in _CATEGORICAL_COLS:
        if c in seed_df.columns:
            seed_df[c] = seed_df[c].astype(str).fillna("NA")

    metadata = Metadata()
    metadata.detect_table_from_dataframe(table_name="applicant_profiles", data=seed_df)
    for c in _CATEGORICAL_COLS:
        if c in seed_df.columns:
            metadata.update_column(column_name=c, table_name="applicant_profiles", sdtype="categorical")

    logger.info("fitting CopulaGAN on %d seed rows (%d columns)", len(seed_df), len(seed_df.columns))
    synth = CopulaGANSynthesizer(metadata, epochs=100)
    synth.fit(seed_df)
    sampled = synth.sample(num_rows=n_profiles)
    logger.info("CopulaGAN sampled %d profiles", len(sampled))

    for c in numeric_cols:
        sampled[c] = sampled[c].where(sampled[c] > -1, None)

    profiles: list[dict] = []
    for idx, row in sampled.iterrows():
        d = row.to_dict()
        applicant_type = str(d.get("applicant_type", "SALARIED"))
        is_business_or_se = applicant_type in ("MSME", "CORPORATE", "SELF_EMPLOYED")
        is_business = applicant_type in ("MSME", "CORPORATE")

        age = int(clip(float(d.get("age", 30)), 18, 75))
        d["age"] = age
        d["applicant_id"] = f"APP{idx:06d}"
        name = fake.name()
        d["name"] = name
        d["full_name"] = name
        d["email"] = fake.free_email()
        d["pan"] = fake.bothify(text="?????####?").upper()
        d["city"] = fake.city()
        d["date_of_birth"] = random_dob(age)

        state_code_raw = d.get("state_code", random.choice(STATE_CODES))
        try:
            state_code = int(float(state_code_raw))
        except (TypeError, ValueError):
            state_code = random.choice(STATE_CODES)
        if state_code not in STATE_CODES:
            state_code = random.choice(STATE_CODES)
        d["state_code"] = state_code
        d["state_name"] = STATE_NAMES.get(state_code, "Other")

        # re-clip fields the GAN can push slightly out of valid range
        bureau_score = d.get("bureau_score")
        if bureau_score is not None and not (isinstance(bureau_score, float) and math.isnan(bureau_score)):
            d["bureau_score"] = int(clip(float(bureau_score), 300, 900))
        else:
            d["bureau_score"] = None
        d["credit_card_utilization"] = clip(float(d.get("credit_card_utilization", 0) or 0), 0, 100)
        d["income_verification_ratio"] = clip(float(d.get("income_verification_ratio", 1.0) or 1.0), 0.1, 2.5)

        for flag in (
            "write_off_flag", "settlement_flag", "default_flag", "suit_filed_flag",
            "salary_credit_detected", "business_credit_detected",
        ):
            d[flag] = str(d.get(flag)).lower() in ("true", "1", "1.0", "yes")

        for count_field in (
            "active_loan_count", "closed_loan_count", "secured_loan_count", "unsecured_loan_count",
            "recent_enquiry_count_30d", "recent_enquiry_count_90d", "recent_enquiry_count_180d",
            "max_dpd", "dpd_recency_months", "number_of_accounts", "bounce_return_count",
            "overdraft_occurrence_count", "requested_tenure_months", "employment_vintage_months",
            "business_vintage_months", "statement_months", "account_opening_vintage_months",
        ):
            v = d.get(count_field)
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                d[count_field] = int(round(float(v)))

        # NTC applicants should have zero active/closed loans and no bureau
        # score regardless of what the GAN's cross-field correlation drifted
        # toward — this is a hard identity, not a soft prior.
        if d.get("credit_history_type") == "NTC":
            d["bureau_score"] = None
            d["active_loan_count"] = 0
            d["closed_loan_count"] = 0
            d["max_dpd"] = 0
            d["max_dpd_label"] = "0"
            d["dpd_recency_months"] = 0

        if not is_business_or_se:
            d["business_vintage_months"] = 0

        # write_off/settlement/default/suit_filed are hard-negative outcomes
        # of a genuinely serious delinquency — they were sampled as their own
        # independent categorical column, so the GAN can decorrelate them
        # from max_dpd (e.g. write_off_flag=True with max_dpd=1). run this
        # AFTER the NTC hard-identity reset above so it sees the final,
        # post-override max_dpd — these flags can never be True unless
        # max_dpd is itself in serious delinquency territory (>90 days),
        # matching the seed-generation rule (write_off/settlement only ever
        # set from SERIOUS_WRITEOFF / 181+ buckets in build_seed_rows).
        max_dpd_val = d.get("max_dpd")
        max_dpd_num = int(max_dpd_val) if max_dpd_val is not None else 0
        if max_dpd_num <= 90:
            d["write_off_flag"] = False
            d["settlement_flag"] = False
            d["default_flag"] = False
            d["suit_filed_flag"] = False

        # requested_loan_amount was sampled from its own applicant-type-keyed
        # bucket table at seed time (personal-loan buckets top out at 20L,
        # MSME buckets run up to 1Cr — see priors.py), but it's an independent
        # numeric column to the GAN, so it can decorrelate from applicant_type
        # after resampling (same class of bug as the DPD/hard-negative-flag
        # decorrelation above) — e.g. a SALARIED row requesting a 53L,
        # MSME-scale loan. Clip back to the personal-loan ceiling for non-
        # business applicant types.
        if applicant_type in ("SALARIED", "SELF_EMPLOYED"):
            requested = d.get("requested_loan_amount")
            if requested is not None:
                d["requested_loan_amount"] = clip(float(requested), 10_000, 2_000_000)

        account_opening_date = REFERENCE_DATE - timedelta(
            days=int(d.get("account_opening_vintage_months") or 12) * 30
        )
        d["account_opening_date"] = account_opening_date

        # expand scalar seed fields into the list-shaped BankStatementRecord
        # fields the schema actually requires (account_type / account_status
        # are per-linked-account lists; emi_like_recurring_debits is a list
        # of individual recurring debit amounts, not a pre-summed float).
        num_accounts_i = int(d.get("number_of_accounts") or 1)
        primary_type = d.pop("primary_account_type", "SAVINGS")
        other_types = ["SAVINGS", "CURRENT", "SALARY"]
        d["account_type"] = [primary_type] + [
            random.choice(other_types) for _ in range(max(0, num_accounts_i - 1))
        ]
        d["account_status"] = ["Active"] * num_accounts_i
        if random.random() < 0.05 and num_accounts_i > 1:
            d["account_status"][-1] = "Dormant"

        emi_sum = float(d.get("emi_like_recurring_debits_sum") or 0.0)
        if emi_sum > 0:
            n_emis = random.randint(1, 3)
            shares = [random.random() for _ in range(n_emis)]
            total_share = sum(shares) or 1.0
            d["emi_like_recurring_debits"] = [round(emi_sum * (s / total_share), 2) for s in shares]
        else:
            d["emi_like_recurring_debits"] = []
        d.pop("emi_like_recurring_debits_sum", None)

        # ITR yr2 built from yr1 with a plausible growth/decline pattern
        # consistent with applicant_type — MSME/self-employed income is more
        # volatile year-to-year than salaried.
        gross_yr1 = float(d.get("gross_total_income_yr1") or 0.0)
        total_yr1 = float(d.get("total_income_yr1") or 0.0)
        if applicant_type == "SALARIED":
            growth = random.uniform(0.95, 1.12)
        elif applicant_type in ("SELF_EMPLOYED",):
            growth = random.uniform(0.75, 1.25)
        else:
            growth = random.uniform(0.70, 1.35)
        gross_yr2 = round(gross_yr1 / growth, 2) if growth > 0 else gross_yr1
        total_yr2 = round(total_yr1 / growth, 2) if growth > 0 else total_yr1
        d["gross_total_income_yr2"] = gross_yr2
        d["total_income_yr2"] = total_yr2

        d["is_business"] = is_business
        d["is_business_or_se"] = is_business_or_se

        profiles.append(d)

    return profiles
