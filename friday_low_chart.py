"""
Chart of example trades from friday_low_test.py: Thursday + Friday + Monday (15-min candles),
Thursday's high, Friday's high/low, the short at Monday's open, target = Friday's low, and two
stops: above Friday's high (shown only when that is already >= 2:1) and the forced 2:1 stop.

    N=2 SEED=3 python friday_low_chart.py      (needs price-cache-fhm and the rule test's days.csv.gz)
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import friday_monday_backtest as fm

DAYS = os.environ.get("DAYS", "reports/backtest/friday-low-sp100-stops/days.csv.gz")
OUT = os.environ.get("OUT_DIR", "backtest-results/charts")
N = int(os.environ.get("N", "2"))
SEED = int(os.environ.get("SEED", "3"))
TF = 15
GREEN, RED = "#2e9e6b", "#d64545"


def draw(ax, sym, mon_d):
    df = pd.read_pickle(os.path.join(fm.CACHE_DIR, f"{sym}_5min.pkl.gz"))
    days, daily = fm.split_days(df)
    dates = sorted(days)
    k = dates.index(mon_d)
    thu_d, fri_d = dates[k - 2], dates[k - 1]
    bars = [fm.resample(days[d], TF) for d in (thu_d, fri_d, mon_d)]
    m = np.concatenate([b[0] for b in bars])
    o, h, l, c = (np.concatenate([b[i] for b in bars]) for i in (1, 2, 3, 4))
    n1, n2 = len(bars[0][0]), len(bars[0][0]) + len(bars[1][0])
    x = np.arange(len(m))
    up = c >= o
    ax.vlines(x, l, h, color=np.where(up, GREEN, RED), lw=0.8)
    ax.bar(x, np.abs(c - o) + 1e-9, bottom=np.minimum(o, c), width=0.7, color=np.where(up, GREEN, RED))
    for b in (n1, n2):
        ax.axvline(b - 0.5, color="#999", lw=0.8, ls=":")

    thu_h, fri_h, fri_l = daily.at[thu_d, "High"], daily.at[fri_d, "High"], daily.at[fri_d, "Low"]
    entry = o[n2]
    reward = entry - fri_l
    stop_fh = fri_h + fm.STOP_BUFFER
    stop_forced = entry + reward / 2
    ax.axhline(thu_h, color="#7b61ff", lw=1, ls="-.", label=f"Thursday high {thu_h:.2f}")
    ax.axhline(fri_h, color="#e0a100", lw=1.2, label=f"Friday high {fri_h:.2f}")
    xe = np.arange(n2, len(m))
    ax.fill_between(xe, fri_l, entry, color=GREEN, alpha=0.12)
    ax.plot(xe, [fri_l] * len(xe), color=GREEN, lw=1.6, label=f"TARGET = Friday low {fri_l:.2f}  (reward {reward:.2f})")
    ax.fill_between(xe, entry, stop_forced, color=RED, alpha=0.12)
    ax.plot(xe, [stop_forced] * len(xe), color=RED, lw=1.6,
            label=f"stop, forced 2:1 {stop_forced:.2f}  (risk {stop_forced - entry:.2f})")
    rr_fh = reward / (stop_fh - entry) if stop_fh > entry else np.nan
    if rr_fh >= 2:
        ax.plot(xe, [stop_fh] * len(xe), color=RED, lw=1.2, ls="--",
                label=f"stop, above Friday high {stop_fh:.2f}  (risk {stop_fh - entry:.2f} -> {rr_fh:.1f}:1)")
    ax.scatter([n2], [entry], marker="v", s=90, color="black", zorder=5, label=f"SHORT at Monday open {entry:.2f}")

    # outcome of the forced 2:1 trade
    mh, ml = h[n2:], l[n2:]
    s = np.flatnonzero(mh >= stop_forced)
    t = np.flatnonzero(ml <= fri_l - fm.TICK)
    s = s[0] if len(s) else 10 ** 9
    t = t[0] if len(t) else 10 ** 9
    if s <= t and s < len(mh):
        res, xi, px = "stopped out: -1R", n2 + s, stop_forced
    elif t < len(mh):
        res, xi, px = "target hit: +2R", n2 + t, fri_l
    else:
        px = c[-1]
        res, xi = f"closed 16:00: {(entry - px) / (stop_forced - entry):+.2f}R", len(m) - 1
    ax.scatter([xi], [px], marker="X", s=90, color="black", zorder=5)
    ticks = [n1 // 2, n1 + (n2 - n1) // 2, n2 + (len(m) - n2) // 2]
    ax.set_xticks(ticks)
    ax.set_xticklabels([f"Thu {thu_d:%d %b}", f"Fri {fri_d:%d %b}", f"Mon {mon_d:%d %b %Y}"])
    ax.set_title(f"{sym}  -  forced 2:1 trade {res}  (before costs)", loc="left", fontweight="bold")
    ax.legend(fontsize=7.5, loc="best", framealpha=0.85)
    ax.grid(alpha=0.2)


def main():
    t = pd.read_csv(DAYS, parse_dates=["date"])
    t["date"] = t["date"].dt.date
    pick = t[t.monday & t.cond & (t["fri_high:rr"] >= 2) & ~t["gap_below"]].sample(N, random_state=SEED)
    os.makedirs(OUT, exist_ok=True)
    fig, axes = plt.subplots(N, 1, figsize=(10, 5.2 * N))
    for ax, r in zip(np.atleast_1d(axes), pick.itertuples()):
        draw(ax, r.sym, r.date)
    fig.suptitle("Friday high < Thursday high  ->  short Monday open, target Friday's low", fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.98))
    fig.savefig(os.path.join(OUT, "rule_2to1_example.png"), dpi=110)
    print("saved")


if __name__ == "__main__":
    main()
