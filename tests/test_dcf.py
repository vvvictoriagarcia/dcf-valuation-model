import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dcf.edgar import parse_historicals
from dcf.valuation import Assumptions, assumptions_from_history, sensitivity, value_company, wacc


def flat_company():
    return pd.DataFrame(
        {"revenue": [1000.0], "cash": [100.0], "total_debt": [300.0], "diluted_shares": [10.0]},
        index=[2025],
    )


def flat_assumptions(**kw):
    base = dict(revenue_growth=0.0, ebit_margin=0.2, tax_rate=0.25, da_pct_revenue=0.05,
                capex_pct_revenue=0.05, nwc_pct_revenue=0.1, terminal_growth=0.0)
    base.update(kw)
    return Assumptions(**base)


def test_wacc_matches_hand_calculation():
    a = flat_assumptions(risk_free_rate=0.04, beta=1.2, equity_risk_premium=0.05,
                         pre_tax_cost_of_debt=0.06, debt_to_capital=0.2)
    r = wacc(a)
    assert r["cost_of_equity"] == pytest.approx(0.10)
    assert r["after_tax_cost_of_debt"] == pytest.approx(0.045)
    assert r["wacc"] == pytest.approx(0.089)


def test_flat_company_is_a_perpetuity():
    # no growth: FCFF = 1000*0.2*0.75 + 50 - 50 = 150 every year
    # so EV should be 150 / 10% = 1500 no matter how many years
    res = value_company(flat_company(), flat_assumptions(), discount_rate=0.10)
    assert res["projection"]["fcff"].tolist() == pytest.approx([150.0] * 5)
    assert res["enterprise_value"] == pytest.approx(1500.0)
    assert res["equity_value"] == pytest.approx(1500 - 300 + 100)
    assert res["value_per_share"] == pytest.approx(130.0)


def test_growth_ties_up_working_capital():
    res = value_company(flat_company(), flat_assumptions(revenue_growth=0.10), discount_rate=0.10,
                        terminal_growth=0.02)
    year1 = res["projection"].iloc[0]
    assert year1["revenue"] == pytest.approx(1100.0)
    assert year1["change_in_nwc"] == pytest.approx(10.0)  # 10% of the +100 revenue


def test_mid_year_raises_value():
    end = value_company(flat_company(), flat_assumptions(), discount_rate=0.10)
    mid = value_company(flat_company(), flat_assumptions(mid_year=True), discount_rate=0.10)
    assert mid["pv_forecast_fcff"] > end["pv_forecast_fcff"]


def test_wacc_must_exceed_terminal_growth():
    with pytest.raises(ValueError):
        value_company(flat_company(), flat_assumptions(terminal_growth=0.05), discount_rate=0.04)


def test_sensitivity_grid_shape_and_ordering():
    hist = pd.read_csv(Path(__file__).resolve().parents[1] / "data" / "sample_company.csv",
                       index_col="fiscal_year")
    grid = sensitivity(hist, assumptions_from_history(hist))
    assert grid.shape == (5, 5)
    # higher WACC -> lower value, higher growth -> higher value
    assert grid.iloc[0, 2] > grid.iloc[-1, 2]
    assert grid.iloc[2, -1] > grid.iloc[2, 0]


def _fact(start, end, val, form="10-K", filed="2025-02-01"):
    return {"start": start, "end": end, "val": val, "form": form, "filed": filed}


def test_parse_edgar_keeps_full_years_and_latest_filing():
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [
            _fact("2023-01-01", "2023-12-31", 100, filed="2024-02-01"),
            _fact("2023-01-01", "2023-12-31", 105, filed="2025-02-01"),  # restated
            _fact("2024-10-01", "2024-12-31", 30),                       # a quarter
            _fact("2024-01-01", "2024-12-31", 120),
            _fact("2024-01-01", "2024-12-31", 999, form="10-Q"),
        ]}},
        "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [
            _fact("2024-01-01", "2024-12-31", -15),
        ]}},
        "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [
            {"end": "2024-12-31", "val": 50, "form": "10-K", "filed": "2025-02-01"},
        ]}},
        "LongTermDebtCurrent": {"units": {"USD": [
            {"end": "2024-12-31", "val": 5, "form": "10-K", "filed": "2025-02-01"},
        ]}},
        "LongTermDebtNoncurrent": {"units": {"USD": [
            {"end": "2024-12-31", "val": 40, "form": "10-K", "filed": "2025-02-01"},
        ]}},
    }}}
    df = parse_historicals(facts)
    assert df.index.tolist() == [2023, 2024]
    assert df.loc[2023, "revenue"] == 105
    assert df.loc[2024, "revenue"] == 120
    assert df.loc[2024, "capex"] == 15
    assert df.loc[2024, "total_debt"] == 45


def test_finance_leases_count_as_debt():
    hist = flat_company()
    hist["finance_lease_liabilities"] = 200.0
    res = value_company(hist, flat_assumptions(), discount_rate=0.10)
    assert res["total_debt"] == pytest.approx(500.0)
    assert res["equity_value"] == pytest.approx(1500 - 500 + 100)


def test_per_year_list_must_match_years():
    with pytest.raises(ValueError):
        flat_assumptions(revenue_growth=[0.1, 0.1], years=5)
