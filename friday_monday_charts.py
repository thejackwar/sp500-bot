"""
Charts of sample trades from the Friday high -> Monday short backtest.
Picks N winners and N losers at random (fixed seed) for one variant and draws Friday + Monday
candles with Friday's high, the entry, stop, target and exit.

    VARIANT_TF=15 VARIANT_ENTRY=C VARIANT_RR=2 python friday_monday_charts.py
Needs the saved prices (price-cache-fhm, from the backtest) and the backtest's trades.csv.gz.
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import friday_monday_backtest as fm

TF = int(os.environ.get("VARIANT_TF", "15"))
ENTRY = os.environ.get("VARIANT_ENTRY", "C")
RR = float(os.environ.get("VARIANT_RR", "2"))
N = int(os.environ.get("N", "5"))
SEED = int(os.environ.get("SEED", "1"))
TRADES = os.environ.get("TRADES", "reports/backtest/friday-monday-v1/trades.csv.gz")
OUT = os.environ.get("OUT_DIR", "backtest-results/charts")

GREEN, RED, GREY = "#2e9e6b", "#d64545", "#888888"


def bars_for(sym, day):
    df = pd.read_pickle(os.path.join(fm.CACHE_DIR, f"{sym}_5min.pkl.gz"))
    days, daily = fm.split_days(df)
    dates = sorted(days)
    k = dates.index(day)
    fri = dates[k - 1]
    return fri, fm.resample(days[fri], TF), fm.resample(days[day], TF), daily.at[fri, "High"]


def draw(ax, t):
    fri_d, fri, mon, FH = bars_for(t.sym, t.date)
    m = np.r_[fri[0], mon[0]]
    o, h, l, c = (np.r_[fri[i], mon[i]] for i in (1, 2, 3, 4))
    nf, n = len(fri[0]), len(m)
    x = np.arange(n)
    up = c >= o
    ax.vlines(x, l, h, color=np.where(up, GREEN, RED), lw=0.8)
    ax.bar(x, np.abs(c - o) + 1e-9, bottom=np.minimum(o, c), width=0.7,
           color=np.where(up, GREEN, RED), edgecolor="none")
    ax.axvline(nf - 0.5, color=GREY, lw=0.8, ls=":")
    ax.axhline(FH, color="#e0a100", lw=1.2, label=f"Friday high {FH:.2f}")

    def ix(hhmm):
        hh, mm = map(int, hhmm.split(":"))
        return nf + int(np.flatnonzero(mon[0] <= hh * 60 + mm)[-1])

    ei, xi = ix(t.entry_time), ix(t.exit_time)
    ax.hlines(t.stop, ei, xi, color=RED, lw=1, ls="--", label=f"stop {t.stop:.2f}")
    ax.hlines(t.target, ei, xi, color=GREEN, lw=1, ls="--", label=f"target {t.target:.2f}")
    ax.scatter([ei], [t.entry], marker="v", s=70, color="black", zorder=5, label=f"short {t.entry:.2f}")
    ax.scatter([xi], [t.exit], marker="x", s=70, color="black", zorder=5, label=f"exit {t.exit:.2f} ({t.reason})")
    ticks = [i for i in range(n) if m[i] in (570, 720, 900)]
    ax.set_xticks(ticks)
    ax.set_xticklabels([("Fri " if i < nf else "Mon ") + fm._hhmm(m[i]) for i in ticks], fontsize=8)
    ax.set_xlim(-1, n)
    col = GREEN if t.R > 0 else RED
    ax.set_title(f"{t.sym}  {fri_d:%a %d %b} -> {t.date:%a %d %b %Y}   {t.R:+.2f}R",
                 fontsize=11, color=col, loc="left", fontweight="bold")
    ax.legend(fontsize=7, loc="best", framealpha=0.8)
    ax.tick_params(labelsize=8)
    ax.grid(alpha=0.2)


def page(rows, title, path):
    fig, axes = plt.subplots(len(rows), 1, figsize=(9, 3.6 * len(rows)))
    for ax, t in zip(np.atleast_1d(axes), rows.itertuples()):
        draw(ax, t)
    fig.suptitle(title, fontsize=13, fontweight="bold", y=0.995)
    fig.tight_layout(rect=(0, 0, 1, 0.985))
    fig.savefig(path, dpi=110)
    plt.close(fig)


def main():
    t = pd.read_csv(TRADES, parse_dates=["date"])
    t["date"] = t["date"].dt.date
    v = t[(t.tf == TF) & (t.entry_type == ENTRY) & (t.rr == RR)]
    wins = v[v.R > 0].sample(N, random_state=SEED).sort_values("date")
    losses = v[v.R <= 0].sample(N, random_state=SEED).sort_values("date")
    os.makedirs(OUT, exist_ok=True)
    name = f"{TF}m {ENTRY} 1:{RR:g}"
    page(wins, f"5 random WINNING trades - {name}", os.path.join(OUT, "wins.png"))
    page(losses, f"5 random LOSING trades - {name}", os.path.join(OUT, "losses.png"))
    print("Charts written to", OUT)


if __name__ == "__main__":
    main()
