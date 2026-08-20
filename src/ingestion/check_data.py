"""
final_report.py - Comprehensive schema coverage report across all 9 source schemas.
Checks applicant_profiles.parquet and all chunk files in data/raw/.
"""
import pandas as pd
import numpy as np
import os
from pathlib import Path

DATA = Path("data/raw")

print("Loading data...")

# Load profiles
df = pd.read_parquet(DATA / "applicant_profiles.parquet")
n = len(df)
cols = set(df.columns)

# Load AIS
ais_files = sorted(f for f in os.listdir(DATA) if f.startswith("ais_chunk"))
ais = pd.concat([pd.read_parquet(DATA / f) for f in ais_files], ignore_index=True) if ais_files else pd.DataFrame()

# Load GST invoices sample
gst_files = sorted(f for f in os.listdir(DATA) if f.startswith("gst_invoices"))
gst = pd.read_parquet(DATA / gst_files[0]) if gst_files else pd.DataFrame()

# Load bank transactions sample
bank_files = sorted(f for f in os.listdir(DATA) if f.startswith("bank_transactions"))
bank = pd.read_parquet(DATA / bank_files[0]) if bank_files else pd.DataFrame()

# Load eway bills sample
ewb_files = sorted(f for f in os.listdir(DATA) if f.startswith("eway_bills"))
ewb = pd.read_parquet(DATA / ewb_files[0]) if ewb_files else pd.DataFrame()

# Load UPI sample
upi_files = sorted(f for f in os.listdir(DATA) if f.startswith("upi_transactions"))
upi = pd.read_parquet(DATA / upi_files[0]) if upi_files else pd.DataFrame()

print(f"Profiles : {n:,} rows x {len(df.columns)} cols")
print(f"AIS      : {len(ais):,} rows x {len(ais.columns)} cols  ({len(ais_files)} chunks)")
print(f"GST inv  : {len(gst_files)} chunks, sample cols: {list(gst.columns)[:6]}...")
print(f"Bank txn : {len(bank_files)} chunks, sample cols: {list(bank.columns)}")
print(f"EWB      : {len(ewb_files)} chunks, sample cols: {list(ewb.columns)[:6]}...")
print(f"UPI      : {len(upi_files)} chunks, sample cols: {list(upi.columns)}")

sep = "\n" + "="*70

# ── SCHEMA CHECKS ──────────────────────────────────────────────────────────

def check(label, present):
    return f"  {'[OK]' if present else '[MISS]':7s} {label}"

print(sep)
print("12.1  APPLICANT / KYC")
checks = [
    ("applicantId",                  "applicant_id" in cols),
    ("pan",                          "pan" in cols),
    ("dateOfBirth",                  "date_of_birth" in cols),
    ("age",                          "age" in cols),
    ("applicantType",                "applicant_type" in cols),
    ("employmentType",               "employment_type" in cols),
    ("employmentVintageMonths",      "employment_vintage_months" in cols),
    ("businessVintageMonths",        "business_vintage_months" in cols),
    ("requestedLoanAmount",          "requested_loan_amount" in cols),
    ("requestedTenureMonths",        "requested_tenure_months" in cols),
    ("declaredIncome",               "declared_income_monthly" in cols),
    ("declaredExistingObligations",  "declared_existing_obligations" in cols),
]
for lbl, ok in checks:
    print(check(lbl, ok))
ok_count = sum(o for _,o in checks)
print(f"  => {ok_count}/{len(checks)} fields present")

print(sep)
print("12.2  BUREAU / CREDIT INFORMATION")
bureau_fields = [
    ("bureauScore",              "bureau_score"),
    ("activeLoanCount",          "active_loan_count"),
    ("closedLoanCount",          "closed_loan_count"),
    ("totalSanctionedAmount",    "total_sanctioned_amount"),
    ("totalOutstandingAmount",   "total_outstanding_amount"),
    ("securedLoanCount",         "secured_loan_count"),
    ("unsecuredLoanCount",       "unsecured_loan_count"),
    ("recentEnquiryCount30D",    "recent_enquiry_count_30d"),
    ("recentEnquiryCount90D",    "recent_enquiry_count_90d"),
    ("recentEnquiryCount180D",   "recent_enquiry_count_180d"),
    ("overdueAmount",            "overdue_amount"),
    ("maxDpd",                   "max_dpd"),
    ("dpdRecencyMonths",         "dpd_recency_months"),
    ("writeOffFlag",             "write_off_flag"),
    ("writeOffAmount",           "write_off_amount"),
    ("settlementFlag",           "settlement_flag"),
    ("settlementAmount",         "settlement_amount"),
    ("defaultFlag",              "default_flag"),
    ("suitFiledFlag",            "suit_filed_flag"),
    ("creditCardUtilization",    "credit_card_utilization"),
]
for lbl, col in bureau_fields:
    print(check(lbl, col in cols))
ok_count = sum(col in cols for _,col in bureau_fields)
print(f"  => {ok_count}/{len(bureau_fields)} fields present")

print(sep)
print("12.3  ACCOUNT AGGREGATOR / BANK DEPOSIT (profile-level)")
aa_profile = [
    ("accountType",               "account_type"),
    ("currentBalance",            "current_balance"),
    ("averageBalance",            "average_balance"),
    ("minimumBalance",            "minimum_balance"),
    ("avgMonthlyCreditInflow",    "avg_monthly_credit_inflow"),
    ("avgMonthlyDebitOutflow",    "avg_monthly_debit_outflow"),
    ("inflowTrend",               "inflow_trend"),
    ("salaryCreditDetected",      "salary_credit_detected"),
    ("businessCreditDetected",    "business_credit_detected"),
    ("bounceCount",               "bounce_count"),
    ("overdraftOccurrenceCount",  "overdraft_occurrence_count"),
    ("cashFlowVolatility",        "cash_flow_volatility"),
    ("accountStatus",             "account_status"),
    ("accountOpeningDate",        "account_opening_date"),
    ("currentODLimit",            "current_od_limit"),
    ("drawingLimit",              "drawing_limit"),
]
for lbl, col in aa_profile:
    print(check(lbl, col in cols))
ok_count = sum(col in cols for _,col in aa_profile)
print(f"  => {ok_count}/{len(aa_profile)} profile fields present")

bank_cols = set(bank.columns) if not bank.empty else set()
print("  -- transactions[] level --")
txn_fields = [
    ("amount",                  "amount"),
    ("type (CREDIT/DEBIT)",     "type"),
    ("mode",                    "channel"),
    ("narration",               "merchant_name"),
    ("reference",               "reference_id"),
    ("transactionTimestamp",    "timestamp"),
    ("transactionalBalance",    "balance_after"),
]
for lbl, col in txn_fields:
    print(check(lbl, col in bank_cols))
ok_txn = sum(col in bank_cols for _,col in txn_fields)
print(f"  => {ok_txn}/{len(txn_fields)} transaction fields present")

print(sep)
print("12.4  ITR / INCOME")
itr_fields = [
    ("assessmentYear (fy1)",  "itr_assessment_year_fy1"),
    ("assessmentYear (fy2)",  "itr_assessment_year_fy2"),
    ("itrType",               "itr_type"),
    ("grossTotalIncome",      "gross_total_income"),
    ("totalIncome",           "total_income"),
    ("salaryIncome",          "itr_salary_income"),
    ("businessIncome",        "itr_business_income"),
    ("professionalIncome",    "itr_professional_income"),
    ("interestIncome",        "itr_interest_income"),
    ("dividendIncome",        "itr_dividend_income"),
    ("capitalGains",          "itr_capital_gains"),
    ("otherIncome",           "itr_other_income"),
    ("deductions",            "itr_deductions"),
    ("taxPaid",               "itr_tax_paid"),
    ("incomeFY1",             "income_fy1"),
    ("incomeFY2",             "income_fy2"),
]
for lbl, col in itr_fields:
    print(check(lbl, col in cols))
ok_count = sum(col in cols for _,col in itr_fields)
print(f"  => {ok_count}/{len(itr_fields)} fields present")

# ITR integrity check
itr_sum = (df["itr_salary_income"].fillna(0)
         + df["itr_professional_income"].fillna(0)
         + df["itr_business_income"].fillna(0)
         + df["itr_interest_income"].fillna(0)
         + df["itr_dividend_income"].fillna(0)
         + df["itr_capital_gains"].fillna(0)
         + df["itr_other_income"].fillna(0))
gti = df["gross_total_income"].fillna(0)
max_err = (itr_sum - gti).abs().max()
print(f"  [INTEGRITY] max |sum(income heads) - gross_total_income| = {max_err:.4f} rupees")

print(sep)
print("12.5  AIS / TIS")
ais_cols = set(ais.columns) if not ais.empty else set()
ais_fields = [
    ("pan",                       "pan"),
    ("financialYear",             "financial_year"),
    ("salaryReported",            "salary_reported"),
    ("interestReported",          "interest_reported"),
    ("dividendReported",          "dividend_reported"),
    ("capitalGainsReported",      "capital_gains_reported"),
    ("tdsAmount",                 "tds_amount"),
    ("tcsAmount",                 "tcs_amount"),
    ("sftTransactionCount",       "sft_transaction_count"),
    ("otherReportedIncome",       "other_reported_income"),
    ("valueReportedBySource",     "value_reported_by_source"),
    ("valueProcessedBySystem",    "value_processed_by_system"),
    ("valueAcceptedOrConfirmed",  "value_accepted_or_confirmed"),
]
for lbl, col in ais_fields:
    print(check(lbl, col in ais_cols))
ok_count = sum(col in ais_cols for _,col in ais_fields)
print(f"  => {ok_count}/{len(ais_fields)} fields present")
if not ais.empty:
    print(f"  [DATASET]   {len(ais):,} records | {ais['financial_year'].nunique()} FYs | chunks: {len(ais_files)}")

print(sep)
print("12.6  CAS / INVESTMENT ASSETS")
cas_fields = [
    ("mutualFundValue",          "mutual_fund_value"),
    ("equityValue",              "equity_value"),
    ("liquidAssetValue",         "liquid_asset_value"),
    ("totalPortfolioValue",      "total_portfolio_value"),
    ("bondValue",                "cas_bond_value"),
    ("etfValue",                 "cas_etf_value"),
    ("otherSecuritiesValue",     "cas_other_securities_value"),
    ("recentRedemptionValue",    "cas_recent_redemption_value"),
    ("recentPurchaseValue",      "cas_recent_purchase_value"),
]
for lbl, col in cas_fields:
    print(check(lbl, col in cols))
ok_count = sum(col in cols for _,col in cas_fields)
print(f"  => {ok_count}/{len(cas_fields)} fields present")

cas_total = (df["mutual_fund_value"].fillna(0)
           + df["equity_value"].fillna(0)
           + df["liquid_asset_value"].fillna(0)
           + df["cas_bond_value"].fillna(0)
           + df["cas_etf_value"].fillna(0)
           + df["cas_other_securities_value"].fillna(0))
cas_err = (cas_total - df["total_portfolio_value"].fillna(0)).abs().max()
print(f"  [INTEGRITY] max |sum(asset classes) - total_portfolio_value| = {cas_err:.4f}")

print(sep)
print("12.7  GST / BUSINESS TAX")
gst_fields = [
    ("gstin",                          "gstin"),
    ("businessConstitution",           "business_constitution"),
    ("turnover",                       "turnover"),
    ("taxableTurnover",                "taxable_turnover"),
    ("filingConsistencyPercent",       "gst_filing_consistency_percent"),
    ("gstr1FilingConsistencyPercent",  "gst_gstr1_filing_consistency_pct"),
    ("gstr3bFilingConsistencyPercent", "gst_gstr3b_filing_consistency_pct"),
    ("turnoverTrend",                  "turnover_trend"),
    ("registrationDate",               "gst_registration_date"),
    ("taxpayerType",                   "gst_taxpayer_type"),
    ("taxLiability",                   "gst_tax_liability"),
    ("taxPaid",                        "gst_tax_paid"),
]
for lbl, col in gst_fields:
    print(check(lbl, col in cols))
ok_count = sum(col in cols for _,col in gst_fields)
print(f"  => {ok_count}/{len(gst_fields)} fields present")
biz = df[df["gstin"].notna()]
print(f"  [STATS]  Business applicants: {len(biz):,}  |  Taxpayer types: {biz['gst_taxpayer_type'].value_counts().to_dict()}")

print(sep)
print("12.8  E-WAY BILL")
ewb_cols = set(ewb.columns) if not ewb.empty else set()
ewb_fields = [
    ("supplyType",          "supplyType"),
    ("subSupplyType",       "subSupplyType"),
    ("docType",             "docType"),
    ("docNo",               "docNo"),
    ("docDate",             "docDate"),
    ("fromGstin",           "fromGstin"),
    ("toGstin",             "toGstin"),
    ("totalValue",          "tot_inv_value"),
    ("cgstValue",           "cgstValue"),
    ("sgstValue",           "sgstValue"),
    ("igstValue",           "igstValue"),
    ("transporterId",       "transporterId"),
    ("transDistance",       "transDistance"),
    ("vehicleNumber",       "vehicleNo"),
    ("itemList.productName","itemList_productName"),
    ("itemList.hsnCode",    "itemList_hsnCode"),
    ("itemList.quantity",   "itemList_quantity"),
    ("itemList.qtyUnit",    "itemList_qtyUnit"),
    ("itemList.taxableAmount","itemList_taxableAmount"),
    ("deliveryLocation",    "toPlace"),
]
for lbl, col in ewb_fields:
    print(check(lbl, col in ewb_cols))
ok_count = sum(col in ewb_cols for _,col in ewb_fields)
dup = "tot_inv_value" in ewb_cols and "totInvValue" in ewb_cols
print(f"  => {ok_count}/{len(ewb_fields)} fields present")
print(f"  [NOTE] Duplicate totalValue field (tot_inv_value + totInvValue): {dup}")

print(sep)
print("12.9  GSTN E-INVOICE")
einv_files = [f for f in os.listdir(DATA) if "einvoice" in f.lower()]
print(f"  E-Invoice chunks in data/raw: {len(einv_files)}")
if einv_files:
    ei = pd.read_parquet(DATA / einv_files[0])
    print(f"  [OK] Dataset exists: {len(ei):,} rows")
else:
    print("  [MISS] No e-invoice dataset generated (LOW priority item)")

print(sep)
print("DATA INVENTORY SUMMARY")
chunk_types = {}
for f in os.listdir(DATA):
    if "_chunk_" in f:
        key = f.split("_chunk_")[0]
        chunk_types[key] = chunk_types.get(key, 0) + 1
print(f"  applicant_profiles.parquet : 1 file  | {n:,} rows | {len(df.columns)} columns")
for k in sorted(chunk_types):
    sample = pd.read_parquet(DATA / f"{k}_chunk_0000.parquet")
    total_rows = sum(len(pd.read_parquet(DATA/f)) for f in os.listdir(DATA) if f.startswith(k+"_chunk"))
    print(f"  {k:35s}: {chunk_types[k]:3d} chunks | {total_rows:>10,} rows | {len(sample.columns)} cols")
