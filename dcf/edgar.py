"""
Pulls annual numbers from SEC EDGAR (XBRL company facts API).

SEC blocks requests without a User-Agent that has a name and email:
    PowerShell:  $env:SEC_USER_AGENT = "Your Name you@email.com"
    Mac/Linux:   export SEC_USER_AGENT="Your Name you@email.com"
"""
from __future__ import annotations

import os
from datetime import date

import pandas as pd
import requests

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# companies don't all use the same tag for the same line, so try them in order
FLOW_TAGS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"],
    "ebit": ["OperatingIncomeLoss"],
    "pretax_income": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ],
    "income_tax": ["IncomeTaxExpenseBenefit"],
    "da": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
           "DepreciationAmortizationAndAccretionNet"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "diluted_shares": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
}
BALANCE_TAGS = {
    "cash": ["CashCashEquivalentsAndShortTermInvestments", "CashAndCashEquivalentsAtCarryingValue"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "debt_current": ["LongTermDebtCurrent", "DebtCurrent"],
    "debt_noncurrent": ["LongTermDebtNoncurrent"],
    "debt_total": ["LongTermDebt"],
    "finance_lease_liabilities": ["FinanceLeaseLiability"],
}


def _headers() -> dict:
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        raise RuntimeError('Set SEC_USER_AGENT first, e.g. "Your Name you@email.com"')
    return {"User-Agent": ua}


def ticker_to_cik(ticker: str) -> int:
    data = requests.get(TICKERS_URL, headers=_headers(), timeout=30).json()
    for row in data.values():
        if row["ticker"].upper() == ticker.upper():
            return int(row["cik_str"])
    raise ValueError(f"{ticker} not found on EDGAR")


def fetch_company_facts(ticker: str) -> dict:
    resp = requests.get(FACTS_URL.format(cik=ticker_to_cik(ticker)), headers=_headers(), timeout=60)
    resp.raise_for_status()
    return resp.json()


def _annual(facts: dict, tags: list[str], point_in_time: bool) -> pd.Series:
    gaap = facts.get("facts", {}).get("us-gaap", {})
    for tag in tags:
        if tag not in gaap:
            continue
        units = gaap[tag]["units"]
        found = {}
        for r in units.get("USD") or units.get("shares") or []:
            if not r.get("form", "").startswith("10-K"):
                continue
            end = date.fromisoformat(r["end"])
            # 10-Ks also include quarterly numbers; keep only full years
            if not point_in_time:
                days = (end - date.fromisoformat(r["start"])).days
                if not 350 <= days <= 380:
                    continue
            # same year shows up again in later 10-Ks; keep the newest filing
            if end not in found or r["filed"] > found[end][1]:
                found[end] = (r["val"], r["filed"])
        if found:
            return pd.Series({k: v[0] for k, v in found.items()}, dtype="float64")
    return pd.Series(dtype="float64")


def parse_historicals(facts: dict, years: int = 5) -> pd.DataFrame:
    cols = {k: _annual(facts, tags, False) for k, tags in FLOW_TAGS.items()}
    cols |= {k: _annual(facts, tags, True) for k, tags in BALANCE_TAGS.items()}
    df = pd.DataFrame(cols).sort_index()
    df = df[df["revenue"].notna()].tail(years)

    split = df["debt_current"].fillna(0) + df["debt_noncurrent"].fillna(0)
    df["total_debt"] = df["debt_total"].fillna(split)
    df["capex"] = df["capex"].abs()  # reported as a negative cash flow
    df.index = [d.year for d in df.index]
    df.index.name = "fiscal_year"
    return df.drop(columns=["debt_current", "debt_noncurrent", "debt_total"])


def load_historicals(ticker: str, years: int = 5) -> pd.DataFrame:
    return parse_historicals(fetch_company_facts(ticker), years)
