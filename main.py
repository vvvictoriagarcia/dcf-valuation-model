"""
Run a DCF and save an Excel file + a PNG to output/.

    python main.py --csv data/msft_10k.csv --name MSFT --assumptions data/msft_assumptions.json
    python main.py --sample
    python main.py --ticker AAPL
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from dcf.charts import dashboard, historical_fcff
from dcf.export import to_excel
from dcf.valuation import assumptions_from_history, sensitivity, value_company, wacc

SAMPLE = Path(__file__).parent / "data" / "sample_company.csv"


def parse_args():
    p = argparse.ArgumentParser(description="DCF valuation from 10-K data")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--ticker", help="pull live from SEC EDGAR, e.g. AAPL")
    src.add_argument("--csv", help="historicals from a csv (same columns as data/sample_company.csv)")
    src.add_argument("--sample", action="store_true", help="fake company, works offline")
    p.add_argument("--name", help="label to use with --csv")
    p.add_argument("--assumptions", help="json file with assumptions and sources")
    p.add_argument("--years", type=int, help="forecast years (default 5)")
    p.add_argument("--growth", type=float, help="revenue growth, e.g. 0.06")
    p.add_argument("--margin", type=float, help="EBIT margin")
    p.add_argument("--beta", type=float)
    p.add_argument("--risk-free", type=float, help="10y Treasury yield")
    p.add_argument("--erp", type=float, help="equity risk premium")
    p.add_argument("--cost-of-debt", type=float, help="pre-tax")
    p.add_argument("--debt-weight", type=float, help="debt / (debt + equity)")
    p.add_argument("--terminal-growth", type=float)
    p.add_argument("--mid-year", action="store_true", help="mid-year discounting")
    p.add_argument("--output", help="excel path (default output/<NAME>_dcf.xlsx)")
    return p.parse_args()


def load(args):
    if args.sample:
        return "SAMPLE", pd.read_csv(SAMPLE, index_col="fiscal_year")
    if args.csv:
        name = (args.name or Path(args.csv).stem).upper()
        return name, pd.read_csv(args.csv, index_col="fiscal_year")
    from dcf.edgar import load_historicals
    return args.ticker.upper(), load_historicals(args.ticker)


def fmt(name, val):
    if name == "beta":
        return f"{val:.2f}"
    if name == "shares_outstanding" and val:
        return f"{val / 1e9:,.2f}B"
    if isinstance(val, float):
        return f"{val:.2%}"
    return str(val)


def main():
    args = parse_args()
    name, hist = load(args)

    # json file first, then anything typed on the command line on top
    params, sources = {}, None
    if args.assumptions:
        params = json.loads(Path(args.assumptions).read_text(encoding="utf-8"))
        sources = params.pop("sources", None)
    cli = {
        "revenue_growth": args.growth, "ebit_margin": args.margin, "beta": args.beta,
        "risk_free_rate": args.risk_free, "equity_risk_premium": args.erp,
        "pre_tax_cost_of_debt": args.cost_of_debt, "debt_to_capital": args.debt_weight,
        "terminal_growth": args.terminal_growth, "years": args.years,
    }
    params.update({k: v for k, v in cli.items() if v is not None})
    if args.mid_year:
        params["mid_year"] = True

    a = assumptions_from_history(hist, **params)
    cost = wacc(a)
    result = value_company(hist, a)
    sens = sensitivity(hist, a)

    pd.set_option("display.float_format", lambda x: f"{x:,.2f}")
    print(f"\n=== {name} historicals (USD millions) ===")
    print((hist / 1e6).round(1).T.to_string())
    print("\n=== Assumptions ===")
    for k, v in a.to_frame().itertuples(index=False):
        print(f"  {k:<22}{fmt(k, v)}")
    print(f"\n=== Projection (USD millions), WACC {result['wacc']:.2%} ===")
    print((result["projection"].drop(columns="discount_factor") / 1e6).round(1).T.to_string())
    print("\n=== Valuation ===")
    print(f"Enterprise value     ${result['enterprise_value'] / 1e9:,.2f}B")
    print(f"Equity value         ${result['equity_value'] / 1e9:,.2f}B")
    print(f"Value per share      ${result['value_per_share']:,.2f}")
    print(f"Terminal value       {result['terminal_share_of_ev']:.0%} of EV")
    print("\n=== Value per share: WACC (rows) x terminal growth (cols) ===")
    print(sens.to_string())

    out = Path(args.output or f"output/{name}_dcf.xlsx")
    out.parent.mkdir(parents=True, exist_ok=True)
    to_excel(str(out), name, hist, a, cost, result, sens, historical_fcff(hist, a.tax_rate), sources)
    png = out.with_suffix(".png")
    dashboard(str(png), name, hist, a.tax_rate, result, sens)
    print(f"\nSaved {out}")
    print(f"Saved {png}")


if __name__ == "__main__":
    main()
