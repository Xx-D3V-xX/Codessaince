"""
check_data_fast.py - Ultra-fast schema & dataset verification using Polars.
"""
import polars as pl
import os
from pathlib import Path

DATA = Path("data/raw")

print("▶ Loading applicant_profiles.parquet ...")
df = pl.read_parquet(DATA / "applicant_profiles.parquet")
n = len(df)
cols = set(df.columns)

ais_files = sorted(f for f in os.listdir(DATA) if f.startswith("ais_chunk"))
ais_df = pl.read_parquet([DATA / f for f in ais_files]) if ais_files else pl.DataFrame()

gst_files = sorted(f for f in os.listdir(DATA) if f.startswith("gst_invoices"))
gst = pl.read_parquet(DATA / gst_files[0]) if gst_files else pl.DataFrame()

bank_files = sorted(f for f in os.listdir(DATA) if f.startswith("bank_transactions"))
bank = pl.read_parquet(DATA / bank_files[0]) if bank_files else pl.DataFrame()

ewb_files = sorted(f for f in os.listdir(DATA) if f.startswith("eway_bills"))
ewb = pl.read_parquet(DATA / ewb_files[0]) if ewb_files else pl.DataFrame()

upi_files = sorted(f for f in os.listdir(DATA) if f.startswith("upi_transactions"))
upi = pl.read_parquet(DATA / upi_files[0]) if upi_files else pl.DataFrame()

sep = "\n" + "="*70

print(sep)
print("SCHEMA COVERAGE & INTEGRITY REPORT")
print(sep)

def check(label, present):
    return f"  {'[OK]':7s} {label}" if present else f"  {'[MISS]':7s} {label}"

# 12.1 APPLICANT / KYC
print("\n12.1 APPLICANT / KYC")
kyc_fields = [
    ("applicantId", "applicant_id"), ("pan", "pan"), ("dateOfBirth", "date_of_birth"),
    ("age", "age"), ("applicantType", "applicant_type"), ("employmentType", "employment_type"),
    ("employmentVintageMonths", "employment_vintage_months"), ("businessVintageMonths", "business_vintage_months"),
    ("requestedLoanAmount", "requested_loan_amount"), ("requestedTenureMonths", "requested_tenure_months"),
    ("declaredIncome", "declared_income_monthly"), ("declaredExistingObligations", "declared_existing_obligations")
]
for lbl, c in kyc_fields:
    print(check(lbl, c in cols))

# 12.2 BUREAU
print("\n12.2 BUREAU / CREDIT INFORMATION")
bureau_fields = [
    ("bureauScore", "bureau_score"), ("activeLoanCount", "active_loan_count"),
    ("closedLoanCount", "closed_loan_count"), ("totalSanctionedAmount", "total_sanctioned_amount"),
    ("totalOutstandingAmount", "total_outstanding_amount"), ("securedLoanCount", "secured_loan_count"),
    ("unsecuredLoanCount", "unsecured_loan_count"), ("recentEnquiryCount30D", "recent_enquiry_count_30d"),
    ("recentEnquiryCount90D", "recent_enquiry_count_90d"), ("recentEnquiryCount180D", "recent_enquiry_count_180d"),
    ("overdueAmount", "overdue_amount"), ("maxDpd", "max_dpd"),
    ("dpdRecencyMonths", "dpd_recency_months"), ("writeOffFlag", "write_off_flag"),
    ("writeOffAmount", "write_off_amount"), ("settlementFlag", "settlement_flag"),
    ("settlementAmount", "settlement_amount"), ("defaultFlag", "default_flag"),
    ("suitFiledFlag", "suit_filed_flag"), ("creditCardUtilization", "credit_card_utilization")
]
for lbl, c in bureau_fields:
    print(check(lbl, c in cols))

# 12.3 BANKING / AA
print("\n12.3 ACCOUNT AGGREGATOR / BANK DEPOSIT")
aa_fields = [
    ("accountType", "account_type"), ("currentBalance", "current_balance"),
    ("averageBalance", "average_balance"), ("minimumBalance", "minimum_balance"),
    ("avgMonthlyCreditInflow", "avg_monthly_credit_inflow"), ("avgMonthlyDebitOutflow", "avg_monthly_debit_outflow"),
    ("inflowTrend", "inflow_trend"), ("salaryCreditDetected", "salary_credit_detected"),
    ("businessCreditDetected", "business_credit_detected"), ("bounceCount", "bounce_count"),
    ("overdraftOccurrenceCount", "overdraft_occurrence_count"), ("cashFlowVolatility", "cash_flow_volatility")
]
for lbl, c in aa_fields:
    print(check(lbl, c in cols))

bank_cols = set(bank.columns)
print("  -- transactions level --")
for lbl, c in [("amount", "amount"), ("type", "channel"), ("narration", "merchant_name"), ("reference", "reference_id"), ("transactionTimestamp", "timestamp"), ("transactionalBalance", "balance_after")]:
    print(check(lbl, c in bank_cols))

# 12.4 ITR
print("\n12.4 ITR / INCOME")
itr_fields = [
    ("assessmentYear (FY1)", "itr_assessment_year_fy1"), ("assessmentYear (FY2)", "itr_assessment_year_fy2"),
    ("itrType", "itr_type"), ("grossTotalIncome", "gross_total_income"),
    ("totalIncome", "total_income"), ("salaryIncome", "itr_salary_income"),
    ("businessIncome", "itr_business_income"), ("professionalIncome", "itr_professional_income"),
    ("interestIncome", "itr_interest_income"), ("dividendIncome", "itr_dividend_income"),
    ("capitalGains", "itr_capital_gains"), ("otherIncome", "itr_other_income"),
    ("deductions", "itr_deductions"), ("taxPaid", "itr_tax_paid"),
    ("incomeFY1", "income_fy1"), ("incomeFY2", "income_fy2")
]
for lbl, c in itr_fields:
    print(check(lbl, c in cols))

# Check ITR sum formula integrity
itr_sum = (df["itr_salary_income"].fill_null(0) + df["itr_professional_income"].fill_null(0) +
           df["itr_business_income"].fill_null(0) + df["itr_interest_income"].fill_null(0) +
           df["itr_dividend_income"].fill_null(0) + df["itr_capital_gains"].fill_null(0) +
           df["itr_other_income"].fill_null(0))
gti = df["gross_total_income"].fill_null(0)
diff = (itr_sum - gti).abs().max()
print(f"  [INTEGRITY] Max |sum(ITR heads) - GrossTotalIncome| = {diff:.4f} INR")

# 12.5 AIS
print("\n12.5 AIS / TIS")
ais_cols = set(ais_df.columns) if len(ais_df) > 0 else set()
ais_fields = [
    ("pan", "pan"), ("financialYear", "financial_year"), ("salaryReported", "salary_reported"),
    ("interestReported", "interest_reported"), ("dividendReported", "dividend_reported"),
    ("capitalGainsReported", "capital_gains_reported"), ("tdsAmount", "tds_amount"),
    ("tcsAmount", "tcs_amount"), ("sftTransactionCount", "sft_transaction_count"),
    ("otherReportedIncome", "other_reported_income"), ("valueReportedBySource", "value_reported_by_source"),
    ("valueProcessedBySystem", "value_processed_by_system"), ("valueAcceptedOrConfirmed", "value_accepted_or_confirmed")
]
for lbl, c in ais_fields:
    print(check(lbl, c in ais_cols))
print(f"  [DATASET] AIS records generated: {len(ais_df):,} rows across {len(ais_files)} chunks")

# 12.6 CAS
print("\n12.6 CAS / INVESTMENT ASSETS")
cas_fields = [
    ("mutualFundValue", "mutual_fund_value"), ("equityValue", "equity_value"),
    ("liquidAssetValue", "liquid_asset_value"), ("totalPortfolioValue", "total_portfolio_value"),
    ("bondValue", "cas_bond_value"), ("etfValue", "cas_etf_value"),
    ("otherSecuritiesValue", "cas_other_securities_value"), ("recentRedemptionValue", "cas_recent_redemption_value"),
    ("recentPurchaseValue", "cas_recent_purchase_value")
]
for lbl, c in cas_fields:
    print(check(lbl, c in cols))

cas_total = (df["mutual_fund_value"].fill_null(0) + df["equity_value"].fill_null(0) +
             df["liquid_asset_value"].fill_null(0) + df["cas_bond_value"].fill_null(0) +
             df["cas_etf_value"].fill_null(0) + df["cas_other_securities_value"].fill_null(0))
cas_diff = (cas_total - df["total_portfolio_value"].fill_null(0)).abs().max()
print(f"  [INTEGRITY] Max |sum(CAS components) - TotalPortfolioValue| = {cas_diff:.4f} INR")

# 12.7 GST
print("\n12.7 GST / BUSINESS TAX")
gst_fields = [
    ("gstin", "gstin"), ("businessConstitution", "business_constitution"),
    ("turnover", "turnover"), ("taxableTurnover", "taxable_turnover"),
    ("filingConsistencyPercent", "gst_filing_consistency_percent"),
    ("gstr1FilingConsistencyPercent", "gst_gstr1_filing_consistency_pct"),
    ("gstr3bFilingConsistencyPercent", "gst_gstr3b_filing_consistency_pct"),
    ("turnoverTrend", "turnover_trend"), ("registrationDate", "gst_registration_date"),
    ("taxpayerType", "gst_taxpayer_type"), ("taxLiability", "gst_tax_liability"),
    ("taxPaid", "gst_tax_paid")
]
for lbl, c in gst_fields:
    print(check(lbl, c in cols))

# 12.8 EWB
print("\n12.8 E-WAY BILL")
ewb_cols = set(ewb.columns)
ewb_fields = [
    ("supplyType", "supplyType"), ("subSupplyType", "subSupplyType"), ("docType", "docType"),
    ("docNo", "docNo"), ("docDate", "docDate"), ("fromGstin", "fromGstin"), ("toGstin", "toGstin"),
    ("totalValue", "tot_inv_value"), ("cgstValue", "cgstValue"), ("sgstValue", "sgstValue"),
    ("igstValue", "igstValue"), ("transporterId", "transporterId"), ("transDistance", "transDistance"),
    ("vehicleNumber", "vehicleNo"), ("itemList.productName", "itemList_productName"),
    ("itemList.hsnCode", "itemList_hsnCode"), ("itemList.quantity", "itemList_quantity"),
    ("itemList.qtyUnit", "itemList_qtyUnit"), ("itemList.taxableAmount", "itemList_taxableAmount")
]
for lbl, c in ewb_fields:
    print(check(lbl, c in ewb_cols))

print(sep)
print("DATASET FILE INVENTORY (data/raw/)")
print(sep)
chunk_groups = {}
for f in os.listdir(DATA):
    if f.endswith(".parquet"):
        if "_chunk_" in f:
            k = f.split("_chunk_")[0]
            chunk_groups[k] = chunk_groups.get(k, 0) + 1
        else:
            print(f"  {f:35s} : 1 file    | {n:>10,} rows | {len(df.columns)} cols")

for k in sorted(chunk_groups):
    files = [f for f in os.listdir(DATA) if f.startswith(k + "_chunk_")]
    sample_p = pl.read_parquet(DATA / files[0])
    print(f"  {k:35s} : {len(files):>3d} chunks | sample schema: {len(sample_p.columns)} cols")

print("\nCOMPLETE.")
