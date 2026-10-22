"""
Example charts of backtest trades of the deck pattern trader (version 6 + 50-day MA filter, 2x leverage):
winners, losers, and breakouts the 50-day filter skipped - each with the pattern found again on the
breakout day, the 50-day MA, the entry, the stop, the 20-day-low exit line and the exit.

    PICKS="win:INTC:2026-04-02 loss:PM:2026-06-11 skip:ANET:2019-10-08" python deck_example_charts.py
Trades come from TRADES (version with the filter, LEVERAGE rows) and, for "skip", from NOFILTER (the
no-filter run). Charts: reports/backtest/<TRADES folder>/examples/.
"""
import datetime as dt
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import deck_patterns as dp
import deck_trader
import scanner
from fmt import px

TRADES = os.environ.get("TRADES", "reports/backtest/universe-deck-v6-lev-1-1.5-2-2.5-3-ma-ma50-sp100-8y/trades.csv")
NOFILTER = os.environ.get("NOFILTER", "reports/backtest/universe-deck-v6-ma-none-ma50-ma200-ma50_200-both-ma50_rising-sp100-8y/trades.csv")
LEVERAGE = float(os.environ.get("LEVERAGE", "2"))
PICKS = os.environ.get("PICKS", "").split()
UP, DOWN, PAT, MA_C, TRAIL_C = "#26a69a", "#ef5350", "#ffd54f", "#ff9800", "#26c6da"


def find_pattern(d, bo, name):
    """The deck pattern of this name whose breakout is candle bo (as the trader saw it that day)."""
    w0 = max(0, bo - 289)
    w = dp.add_atr(d.iloc[w0:bo + 1])
    for p in dp.detect(w):
        b = p.get("brk")
        if p["stage"] == "breakout" and b and b["bar"] == len(w) - 1 and p["name"].split(" (")[0] == name:
            return p, w0
    return None, w0


def draw(sym, row, kind, d, out):
    entry_day = pd.Timestamp(row["entry_date"]).normalize()
    exit_day = pd.Timestamp(row["exit_date"]).normalize()
    ei = int(d.index.searchsorted(entry_day))
    xi = min(int(d.index.searchsorted(exit_day)), len(d) - 1)
    bo = ei - 1
    p, w0 = find_pattern(d, bo, row["pattern"])
    start = max(0, min(bo - 70, (p["start"] + w0 - 10) if p else bo - 70))
    end = min(len(d) - 1, xi + 15)
    v = d.iloc[start:end + 1]
    x = np.arange(start, end + 1)
    o, h, l, c = (v[k].values for k in ("Open", "High", "Low", "Close"))
    ma50 = d["Close"].rolling(50).mean().values[start:end + 1]
    low20 = d["Low"].rolling(20).min().shift(1).values

    fig, ax = plt.subplots(figsize=(12, 6.4), dpi=110)
    fig.patch.set_facecolor("black"); ax.set_facecolor("black")
    for s in ax.spines.values():
        s.set_color("#444")
    ax.tick_params(colors="#bbbbbb", labelsize=8); ax.grid(color="#222", lw=0.6)
    win = row["pnl"] > 0
    # holding period
    ax.axvspan(ei - 0.5, xi + 0.5, color=(UP if win else DOWN), alpha=0.10 if kind != "skip" else 0.06, zorder=0)
    col = np.where(c >= o, UP, DOWN)
    ax.vlines(x, l, h, color=col, lw=0.8, zorder=2)
    span = float(h.max() - l.min()) or 1
    ax.bar(x, np.maximum(np.abs(c - o), span * 0.002), 0.62, bottom=np.minimum(o, c), color=col, edgecolor=col, zorder=3)
    ax.plot(x, ma50, color=MA_C, lw=1.6, label="50-day moving average", zorder=4)
    if p:
        for ln, xa, xb in p["lines"]:
            xa2, xb2 = xa + w0, (bo if xb is None else xb + w0)
            ax.plot([xa2, xb2 + 1], [ln(xa), ln(xb2 + 1 - w0)], color=PAT, lw=1.7, zorder=5)
        if p.get("shape"):
            xs, ys = zip(*p["shape"])
            ax.plot([q + w0 for q in xs], ys, color=PAT, lw=1.0, ls="--", alpha=0.7, zorder=5)
        if p.get("curve"):
            ax.plot(np.array(p["curve"][0]) + w0, p["curve"][1], color=PAT, lw=1.5, zorder=5)
        if p.get("pole"):
            (xa, ya), (xb, yb) = p["pole"]
            ax.plot([xa + w0, xb + w0], [ya, yb], color=PAT, lw=2.2, zorder=5)
        if p.get("box"):
            b0, b1 = p["box"]
            bl, bh = float(d["Low"].values[b0 + w0:b1 + w0 + 1].min()), float(d["High"].values[b0 + w0:b1 + w0 + 1].max())
            ax.add_patch(plt.Rectangle((b0 + w0 - 0.6, bl - 0.02 * span), b1 - b0 + 1.2, bh - bl + 0.04 * span,
                                       fill=False, edgecolor=PAT, lw=1.6, zorder=6))
        for i, price, lab in p["points"]:
            ax.scatter([i + w0], [price], color=PAT, s=22, zorder=6)
            if lab:
                ax.annotate(lab, (i + w0, price), textcoords="offset points", xytext=(0, 8), ha="center",
                            fontsize=8, color="white", zorder=7)
    # breakout candle
    ax.annotate("breakout", (bo, d["High"].iat[bo]), textcoords="offset points", xytext=(0, 14), ha="center",
                fontsize=8, color=UP, fontweight="bold", arrowprops=dict(arrowstyle="-|>", color=UP), zorder=8)
    # stop, 20-day-low exit line, entry, exit
    ls = ":" if kind == "skip" else "-"
    ax.plot([ei, xi], [row["stop"], row["stop"]], color=DOWN, lw=1.3, ls="--", zorder=6, label="stop")
    tr = np.arange(ei, xi + 1)
    ax.step(tr, low20[ei:xi + 1], where="mid", color=TRAIL_C, lw=1.2, ls=ls, zorder=6, label="20-day low (exit line)")
    ax.scatter([ei], [row["entry"]], marker="^", s=150, color=UP if kind != "skip" else "#888",
               edgecolor="white", zorder=10)
    ax.scatter([xi], [row["exit"]], marker="X", s=150, color="white", edgecolor="black", zorder=10)
    ax.plot([ei, xi], [row["entry"], row["exit"]], color="white", lw=0.9, ls=":", zorder=9)
    ax.annotate(f"buy {px(row['entry'])}" if kind != "skip" else f"would buy {px(row['entry'])}", (ei, row["entry"]),
                textcoords="offset points", xytext=(-6, -16), ha="right", fontsize=8, color="white", zorder=10)
    ax.annotate(f"sell {px(row['exit'])}", (xi, row["exit"]), textcoords="offset points", xytext=(8, 0),
                fontsize=8, color="white", fontweight="bold", zorder=10)
    why = {"stop": "stop hit", "stop (gap)": "opened below the stop", "trend exit": "close below the 20-day low",
           "still open": "still open at the end of the test", "margin call": "margin call"}.get(row["result"], row["result"])
    ma_bo = d["Close"].rolling(50).mean().iat[bo]
    if kind == "skip":
        title = f"{sym} · {row['pattern']} ↑ · SKIPPED by the 50-day filter (would have lost {row['R']:+.1f}R)"
        info = [f"Breakout close {px(d['Close'].iat[bo])} was BELOW the 50-day MA {px(ma_bo)} → not bought",
                f"Without the filter: buy {px(row['entry'])}, stop {px(row['stop'])}, sold {px(row['exit'])} "
                f"({why}) after {row['days']} days",
                f"Result avoided: {row['R']:+.2f}R"]
        box = "#2b2b2b"
    else:
        pct = (row["exit"] / row["entry"] - 1) * 100
        title = (f"{sym} · {row['pattern']} ↑ · {'WIN' if win else 'LOSS'} {'+' if win else '−'}${abs(row['pnl']):,.0f} "
                 f"({row['R']:+.1f}R) · {LEVERAGE:g}x")
        info = [f"Breakout close {px(d['Close'].iat[bo])} above the 50-day MA {px(ma_bo)} → bought next open",
                f"Buy {px(row['entry'])} on {entry_day:%d %b %Y} · stop {px(row['stop'])} "
                f"({(row['stop'] / row['entry'] - 1) * 100:+.1f}%)",
                f"Sold {px(row['exit'])} on {exit_day:%d %b %Y} ({why}) after {row['days']} days: {pct:+.1f}% on the stock",
                f"P&L {'+' if win else '−'}${abs(row['pnl']):,.0f} = {row['R']:+.2f}R (backtest account at that time, {LEVERAGE:g}x)"]
        box = "#0d2b26" if win else "#3a1414"
    ax.set_title(title, color="white", fontsize=11.5)
    ax.text(0.01, 0.98, "\n".join(info), transform=ax.transAxes, va="top", fontsize=8.5, color="white",
            linespacing=1.5, zorder=12, bbox=dict(facecolor=box, edgecolor="#666", pad=6))
    lo = min(l.min(), row["stop"], row["exit"]); hi = max(h.max(), row["exit"])
    ax.set_ylim(lo - 0.05 * (hi - lo), hi + 0.22 * (hi - lo))
    ax.set_xlim(start - 1, end + 2)
    step = max(1, len(v) // 8)
    ticks = list(range(start, end + 1, step))
    ax.set_xticks(ticks); ax.set_xticklabels([d.index[i].strftime("%d %b %y") for i in ticks])
    leg = ax.legend(loc="lower left", fontsize=7.5, facecolor="black", edgecolor="#444")
    for t in leg.get_texts():
        t.set_color("#ddd")
    fig.tight_layout()
    fn = os.path.join(out, f"{kind}-{sym}-{entry_day:%Y-%m-%d}.png")
    fig.savefig(fn, facecolor="black"); plt.close(fig)
    print(f"saved {fn} (pattern {'found' if p else 'NOT found'})", flush=True)


def main():
    t = pd.read_csv(TRADES)
    if "leverage" in t:
        t = t[t["leverage"] == LEVERAGE]
    nf = pd.read_csv(NOFILTER)
    nf = nf[nf["filter"] == "none"] if "filter" in nf else nf
    out = os.path.join(os.path.dirname(TRADES), "examples")
    os.makedirs(out, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20)
    for pick in PICKS:
        kind, sym, day = pick.split(":")
        src = nf if kind == "skip" else t
        rows = src[(src.symbol == sym) & (src.entry_date.astype(str).str[:10] == day)]
        if rows.empty:
            print(f"{pick}: no such trade", flush=True)
            continue
        row = rows.iloc[0].to_dict()
        start = pd.Timestamp(day) - pd.Timedelta(days=500)
        end = min(pd.Timestamp(row["exit_date"]) + pd.Timedelta(days=40), pd.Timestamp(now.date()))
        bars = scanner.alpaca_bars([sym], "1Day", start.tz_localize("UTC").to_pydatetime(),
                                   min(end.tz_localize("UTC").to_pydatetime(), now)).get(sym)
        if bars is None:
            print(f"{pick}: no prices", flush=True)
            continue
        bars.index = bars.index.tz_convert(scanner.ET).tz_localize(None).normalize()
        draw(sym, row, kind, bars[["Open", "High", "Low", "Close", "Volume"]], out)


if __name__ == "__main__":
    main()
