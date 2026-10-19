"""2x2 dashboard image: revenue, FCFF, EV-to-equity bridge, sensitivity heatmap."""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # save to file without opening a window
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HIST = "#2a78d6"      # historical = blue
PROJ = "#eb6834"      # projected = orange
NEUTRAL = "#8a8984"   # adjustments (debt / cash)
INK, INK_2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95"]


def historical_fcff(hist: pd.DataFrame, tax_rate: float) -> pd.Series:
    # same formula as the forecast, applied to past years
    nwc = (hist["current_assets"] - hist["cash"]) - hist["current_liabilities"]
    return hist["ebit"] * (1 - tax_rate) + hist["da"] - hist["capex"] - nwc.diff().fillna(0)


def _axes_style(ax, title):
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", fontsize=12, color=INK, fontweight="bold", pad=10)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9, length=0)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def _hist_vs_proj(ax, hist_vals, proj_vals, title, unit="$B"):
    _axes_style(ax, title)
    years = [str(y) for y in list(hist_vals.index) + list(proj_vals.index)]
    vals = np.concatenate([hist_vals.values, proj_vals.values]) / 1e9
    colors = [HIST] * len(hist_vals) + [PROJ] * len(proj_vals)
    bars = ax.bar(years, vals, color=colors, width=0.7, edgecolor=SURFACE, linewidth=2)
    # only label a few bars so it does not get cluttered
    for i in {0, len(hist_vals) - 1, len(vals) - 1}:
        b = bars[i]
        if np.isnan(vals[i]):
            continue
        ax.annotate(f"{vals[i]:,.1f}", (b.get_x() + b.get_width() / 2, b.get_height()),
                    ha="center", va="bottom", fontsize=9, color=INK, xytext=(0, 3),
                    textcoords="offset points")
    ax.set_ylabel(unit, color=INK_2, fontsize=9)
    ax.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=HIST),
                       plt.Rectangle((0, 0), 1, 1, color=PROJ)],
              labels=["Historical (10-K)", "Projected"], frameon=False,
              fontsize=9, loc="upper left", labelcolor=INK_2)


def _bridge(ax, r):
    _axes_style(ax, "From enterprise value to equity value ($B)")
    steps = [("PV of\nforecast FCFF", r["pv_forecast_fcff"], HIST),
             ("PV of\nterminal value", r["pv_terminal_value"], HIST),
             ("Enterprise\nvalue", None, HIST),
             ("(-) Debt", -r["total_debt"], NEUTRAL),
             ("(+) Cash", r["cash"], NEUTRAL),
             ("Equity\nvalue", None, PROJ)]
    running = 0.0
    for i, (label, delta, color) in enumerate(steps):
        if delta is None:  # subtotal, starts at zero
            bottom, height, shown = 0, running, running
        else:
            bottom = running if delta >= 0 else running + delta
            height, shown = abs(delta), delta
            running += delta
        ax.bar(i, height / 1e9, bottom=bottom / 1e9, color=color, width=0.7,
               edgecolor=SURFACE, linewidth=2)
        ax.annotate(f"{shown / 1e9:,.1f}", (i, (bottom + height) / 1e9), ha="center",
                    va="bottom", fontsize=9, color=INK, xytext=(0, 3), textcoords="offset points")
    ax.set_xticks(range(len(steps)), [s[0] for s in steps], fontsize=8.5)


def _heatmap(ax, sens: pd.DataFrame, base_row: int, base_col: int):
    ax.set_title("Value per share: WACC vs terminal growth", loc="left", fontsize=12,
                 color=INK, fontweight="bold", pad=10)
    vals = sens.values.astype(float)
    cmap = matplotlib.colors.LinearSegmentedColormap.from_list("seq", SEQ)
    ax.imshow(vals, cmap=cmap, aspect="auto")
    mid = np.nanmean(vals)
    for (i, j), v in np.ndenumerate(vals):
        ax.text(j, i, f"${v:,.0f}", ha="center", va="center", fontsize=9.5,
                color="white" if v > mid else INK,
                fontweight="bold" if (i, j) == (base_row, base_col) else "normal")
    ax.add_patch(plt.Rectangle((base_col - 0.5, base_row - 0.5), 1, 1, fill=False,
                               edgecolor=PROJ, linewidth=2.5))
    ax.set_xticks(range(vals.shape[1]), sens.columns, fontsize=9, color=INK_2)
    ax.set_yticks(range(vals.shape[0]), sens.index, fontsize=9, color=INK_2)
    ax.set_xlabel("Terminal growth", color=INK_2, fontsize=9)
    ax.set_ylabel("WACC", color=INK_2, fontsize=9)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(length=0)


def dashboard(path: str, ticker: str, hist: pd.DataFrame, tax_rate: float,
              result: dict, sens: pd.DataFrame) -> None:
    proj = result["projection"]
    fig, axes = plt.subplots(2, 2, figsize=(14, 9), facecolor=SURFACE)
    _hist_vs_proj(axes[0, 0], hist["revenue"], proj["revenue"], "Revenue")
    _hist_vs_proj(axes[0, 1], historical_fcff(hist, tax_rate), proj["fcff"],
                  "Unlevered free cash flow (FCFF)")
    _bridge(axes[1, 0], result)
    centre = sens.shape[0] // 2
    _heatmap(axes[1, 1], sens, centre, sens.shape[1] // 2)

    fig.suptitle(f"{ticker} — DCF valuation:  ${result['value_per_share']:,.2f} per share",
                 x=0.015, ha="left", fontsize=16, fontweight="bold", color=INK)
    fig.text(0.015, 0.935,
             f"WACC {result['wacc']:.2%}  ·  terminal growth {result['terminal_growth']:.2%}  ·  "
             f"terminal value = {result['terminal_share_of_ev']:.0%} of EV",
             fontsize=10.5, color=INK_2)
    fig.tight_layout(rect=(0, 0, 1, 0.92), h_pad=3, w_pad=3)
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
