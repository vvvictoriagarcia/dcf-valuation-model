"""
DCF math: forecast -> free cash flow -> discount at WACC -> terminal value -> per share.

FCFF = EBIT * (1 - tax) + D&A - CapEx - increase in working capital
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass
class Assumptions:
    # all rates as decimals (0.05 = 5%)
    # growth, margin, D&A and capex can be a single number or one value per year
    revenue_growth: float | list[float]
    ebit_margin: float | list[float]
    tax_rate: float
    da_pct_revenue: float | list[float]
    capex_pct_revenue: float | list[float]
    nwc_pct_revenue: float

    risk_free_rate: float = 0.043
    beta: float = 1.0
    equity_risk_premium: float = 0.05
    pre_tax_cost_of_debt: float = 0.055
    debt_to_capital: float = 0.15

    terminal_growth: float = 0.025
    years: int = 5
    mid_year: bool = False
    shares_outstanding: float | None = None  # if None, uses last year's diluted shares

    def __post_init__(self):
        for name in ("revenue_growth", "ebit_margin", "da_pct_revenue", "capex_pct_revenue"):
            v = getattr(self, name)
            if isinstance(v, (list, tuple)) and len(v) != self.years:
                raise ValueError(f"{name} has {len(v)} values but years = {self.years}")

    def at(self, name: str, t: int) -> float:
        v = getattr(self, name)
        return float(v[t - 1]) if isinstance(v, (list, tuple)) else float(v)

    def to_frame(self) -> pd.DataFrame:
        rows = []
        for k, v in asdict(self).items():
            if isinstance(v, (list, tuple)):
                v = ", ".join(f"{x:.2%}" for x in v)
            rows.append((k, v))
        return pd.DataFrame(rows, columns=["assumption", "value"])


def working_capital(hist: pd.DataFrame) -> pd.Series:
    # current assets minus cash, minus current liabilities
    # (cash gets added separately at the end, so leave it out here)
    return (hist["current_assets"] - hist["cash"]) - hist["current_liabilities"]


def assumptions_from_history(hist: pd.DataFrame, **overrides) -> Assumptions:
    """Historical averages as a first guess. Anything passed in overrides wins."""
    rev = hist["revenue"]
    n = len(rev) - 1
    cagr = (rev.iloc[-1] / rev.iloc[0]) ** (1 / n) - 1 if n > 0 else 0.03
    tax = (hist["income_tax"] / hist["pretax_income"]).clip(0, 0.35).mean()

    base = dict(
        revenue_growth=float(cagr),
        ebit_margin=float((hist["ebit"] / rev).mean()),
        tax_rate=float(tax) if pd.notna(tax) else 0.21,
        da_pct_revenue=float((hist["da"] / rev).mean()),
        capex_pct_revenue=float((hist["capex"] / rev).mean()),
        nwc_pct_revenue=float((working_capital(hist) / rev).mean()),
    )
    base.update(overrides)
    return Assumptions(**base)


def wacc(a: Assumptions) -> dict:
    cost_of_equity = a.risk_free_rate + a.beta * a.equity_risk_premium  # CAPM
    cost_of_debt = a.pre_tax_cost_of_debt * (1 - a.tax_rate)  # interest is tax deductible
    wd = a.debt_to_capital
    return {
        "cost_of_equity": cost_of_equity,
        "after_tax_cost_of_debt": cost_of_debt,
        "weight_equity": 1 - wd,
        "weight_debt": wd,
        "wacc": (1 - wd) * cost_of_equity + wd * cost_of_debt,
    }


def project_fcf(hist: pd.DataFrame, a: Assumptions, rate: float) -> pd.DataFrame:
    year0 = int(hist.index[-1])
    revenue = float(hist["revenue"].iloc[-1])
    # start working capital at the assumed % so year 1 doesn't get a fake jump
    prev_nwc = revenue * a.nwc_pct_revenue

    rows = []
    for t in range(1, a.years + 1):
        revenue *= 1 + a.at("revenue_growth", t)
        ebit = revenue * a.at("ebit_margin", t)
        nopat = ebit * (1 - a.tax_rate)
        da = revenue * a.at("da_pct_revenue", t)
        capex = revenue * a.at("capex_pct_revenue", t)
        nwc = revenue * a.nwc_pct_revenue
        change_nwc = nwc - prev_nwc
        prev_nwc = nwc

        fcff = nopat + da - capex - change_nwc
        period = t - 0.5 if a.mid_year else t
        df = 1 / (1 + rate) ** period
        rows.append(dict(fiscal_year=year0 + t, revenue=revenue, ebit=ebit, nopat=nopat,
                         da=da, capex=capex, change_in_nwc=change_nwc, fcff=fcff,
                         discount_factor=df, pv_fcff=fcff * df))
    return pd.DataFrame(rows).set_index("fiscal_year")


def value_company(hist: pd.DataFrame, a: Assumptions,
                  discount_rate: float | None = None,
                  terminal_growth: float | None = None) -> dict:
    r = wacc(a)["wacc"] if discount_rate is None else discount_rate
    g = a.terminal_growth if terminal_growth is None else terminal_growth
    if r <= g:
        raise ValueError(f"WACC ({r:.2%}) has to be higher than terminal growth ({g:.2%})")

    proj = project_fcf(hist, a, r)
    tv = proj["fcff"].iloc[-1] * (1 + g) / (r - g)  # Gordon growth
    pv_tv = tv / (1 + r) ** a.years
    ev = proj["pv_fcff"].sum() + pv_tv

    last = hist.iloc[-1]
    debt = float(last.get("total_debt", 0) or 0)
    leases = last.get("finance_lease_liabilities", np.nan)
    if pd.notna(leases):
        debt += float(leases)  # finance leases work like debt (leased data centers)
    cash = float(last.get("cash", 0) or 0)
    equity = ev - debt + cash
    shares = a.shares_outstanding or float(last["diluted_shares"])

    return {
        "projection": proj,
        "wacc": r,
        "terminal_growth": g,
        "terminal_value": tv,
        "pv_terminal_value": pv_tv,
        "pv_forecast_fcff": proj["pv_fcff"].sum(),
        "enterprise_value": ev,
        "total_debt": debt,
        "cash": cash,
        "equity_value": equity,
        "shares": shares,
        "value_per_share": equity / shares,
        "terminal_share_of_ev": pv_tv / ev,
    }


def sensitivity(hist: pd.DataFrame, a: Assumptions, step: float = 0.005, n: int = 2) -> pd.DataFrame:
    """Value per share for WACC (rows) x terminal growth (columns)."""
    base_r, base_g = wacc(a)["wacc"], a.terminal_growth
    moves = np.arange(-n, n + 1) * step
    table = {}
    for dg in moves:
        g = base_g + dg
        table[f"{g:.2%}"] = {
            f"{base_r + dr:.2%}": (value_company(hist, a, base_r + dr, g)["value_per_share"]
                                   if base_r + dr > g else np.nan)
            for dr in moves
        }
    out = pd.DataFrame(table)
    out.index.name = "WACC \\ g"
    return out
