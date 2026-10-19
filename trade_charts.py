"""
Pictures of a backtest's trades: one overview chart of the whole period, then a close-up of every
trade (daily candles, the strategy's lines, where it bought and sold), 12 trades per picture.

Run by .github/workflows/trade-charts.yml (Actions -> Trade charts), which commits the pictures to
reports/backtest/<folder>/charts/. Settings (environment variables):
    RESULTS   results folder in reports/backtest/   (default crypto-btc-strategies-btc_trend-8y)
    VERSION   start of the version's name in trades.csv (default: the 50 & 200-day Bitcoin rule)
    SYMBOL    coin                                   (default BTC/USD)
    RULE      optional: a btc50_*.py strategy file (e.g. btc50_uprise_ma_f1). Draws that rule's stop, the highest
              high since entry and the lower highs instead of the averages' exit line; the pictures go to
              charts/<RULE>/

Made for the Bitcoin trend rule (hold while the daily close is above the 50- and 200-day averages):
it has no stop-loss; it sells at the next open after a close below either average, so the HIGHER of
the two averages is its exit line, drawn red and dashed while the trade is open.
"""
import datetime as dt
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Rectangle

import crypto

# colors (reference palette: categorical slots 1-2 for the averages, red for the exit line)
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#8c8b85", "#e6e5e0"
SURFACE = "#fcfcfb"
SMA50_C, SMA200_C, EXIT_C = "#2a78d6", "#eb6834", "#e34948"
UP_C, DOWN_C = "#b9b8b1", "#52514e"          # candles stay neutral so the lines carry the meaning
WIN_BG, LOSS_BG = "#e3f4ec", "#fbe7e6"
PER_PAGE = 12
RULE = {"name": None, "st": None}        # set by main() for a btc50_*.py rule
STOP_C, PEAK_C, LH_C = "#e34948", "#8c8b85", "#6250d6"
SPIKE = 0.15                 # wicks more than 15% beyond the candle body are bad ticks: clipped

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": GRID,
                     "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
                     "axes.facecolor": SURFACE, "figure.facecolor": SURFACE})


def load_trades(results, version):
    t = pd.read_csv(os.path.join("reports", "backtest", results, "trades.csv"))
    t = t[t["version"].str.startswith(version)].copy()
    if t.empty:
        raise SystemExit(f"No trades of a version starting with {version!r} in {results}")
    t["entry_day"] = pd.to_datetime(t["entry_time"]).dt.normalize()
    t["exit_day"] = pd.to_datetime(t["exit_time"]).dt.normalize()
    t["ret"] = t["exit"] * (1 - 0.0025) / (t["entry"] * (1 + 0.0025)) - 1
    t["open"] = t["result"] == "still open"
    return t.sort_values("entry_day").reset_index(drop=True)


def load_prices(symbol, first):
    """Daily candles (UTC days) from hourly candles, like the backtest and the live scan."""
    now = dt.datetime.now(dt.timezone.utc)
    try:
        h = crypto.bars([symbol], "1Hour", first, now)[symbol]
        d = crypto.daily_from(h[["Open", "High", "Low", "Close", "Volume"]])
        print(f"Alpaca: {len(d)} days from {d.index[0]:%Y-%m-%d}", flush=True)
    except Exception as e:
        print(f"Alpaca failed ({e}); using Yahoo daily candles", flush=True)
        y = crypto.yahoo([symbol], "10y", "1d")[symbol]
        d = y.copy()
        d.index = d.index.tz_localize(None).normalize()
    # bad ticks: Alpaca's crypto history has a few wicks far outside the real price (e.g. ~$8k in Oct 2021
    # with BTC near $60k). The strategy only uses closes and opens, so only the pictures are cleaned up.
    body_lo, body_hi = d[["Open", "Close"]].min(axis=1), d[["Open", "Close"]].max(axis=1)
    bad = (d["Low"] < body_lo * (1 - SPIKE)) | (d["High"] > body_hi * (1 + SPIKE))
    d["Low"] = d["Low"].clip(lower=body_lo * (1 - SPIKE))
    d["High"] = d["High"].clip(upper=body_hi * (1 + SPIKE))
    print(f"Clipped {int(bad.sum())} daily candles with a wick more than {SPIKE:.0%} beyond the body: "
          + ", ".join(f"{x:%Y-%m-%d}" for x in d.index[bad]), flush=True)
    d.attrs["clipped"] = int(bad.sum())
    d["SMA50"] = d["Close"].rolling(50).mean()
    d["SMA200"] = d["Close"].rolling(200).mean()
    d["EXIT"] = d[["SMA50", "SMA200"]].max(axis=1)
    return d


def candles(ax, d, width=0.7):
    for day, r in d.iterrows():
        up = r.Close >= r.Open
        c = UP_C if up else DOWN_C
        x = mdates.date2num(day)
        ax.vlines(x, r.Low, r.High, color=c, lw=0.8, zorder=2)
        lo, hi = sorted((r.Open, r.Close))
        ax.add_patch(Rectangle((x - width / 2, lo), width, max(hi - lo, r.Close * 1e-4),
                               facecolor=c, edgecolor=c, lw=0.5, zorder=3))


def money(x):
    return f"${x:,.0f}"


def overview(d, t, path, title):
    fig, ax = plt.subplots(figsize=(14, 6.2), dpi=130)
    start = t["entry_day"].min() - pd.Timedelta(days=60)
    w = d[d.index >= start]
    for _, r in t.iterrows():
        end = w.index[-1] if r["open"] else r["exit_day"]
        ax.axvspan(r["entry_day"], end, color=WIN_BG if r["ret"] > 0 else LOSS_BG, zorder=0, lw=0)
    ax.plot(w.index, w["Close"], color=INK, lw=1.2, label="Bitcoin daily close", zorder=3)
    ax.plot(w.index, w["SMA50"], color=SMA50_C, lw=1.4, label="50-day average", zorder=2)
    ax.plot(w.index, w["SMA200"], color=SMA200_C, lw=1.4, label="200-day average", zorder=2)
    for i, r in t.iterrows():                    # number the big trades (|return| >= 5%) above the line
        if abs(r["ret"]) >= 0.09:
            seg = w.loc[r["entry_day"]:(w.index[-1] if r["open"] else r["exit_day"])]
            ax.annotate(f"#{i + 1}\n{r['ret'] * 100:+.0f}%", (seg["High"].idxmax(), seg["High"].max()),
                        xytext=(0, 6), textcoords="offset points", ha="center", va="bottom",
                        fontsize=8, color=INK2)
    ax.set_yscale("log")
    lo, hi = w["Low"].min(), w["High"].max()
    ticks = [v for v in (5e3, 7e3, 10e3, 15e3, 20e3, 30e3, 40e3, 50e3, 70e3, 100e3, 150e3, 200e3, 300e3)
             if lo * 0.9 <= v <= hi * 1.15]
    ax.yaxis.set_major_locator(matplotlib.ticker.FixedLocator(ticks))
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"${v / 1000:,.0f}k"))
    ax.yaxis.set_minor_locator(matplotlib.ticker.NullLocator())
    ax.grid(True, color=GRID, lw=0.6, zorder=0)
    ax.set_xlim(w.index[0], w.index[-1])
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    wins = int((t["ret"] > 0).sum())
    ax.set_title(f"{title}\n{len(t)} trades, {wins} winners · green = winning trade, red = losing trade, "
                 f"white = in cash (log price scale)", loc="left", fontsize=11, color=INK)
    ax.legend(loc="upper left", frameon=False, fontsize=9)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def trade_panel(ax, d, r, n):
    end = d.index[-1] if r["open"] else r["exit_day"]
    held = (end - r["entry_day"]).days
    pad_before = max(25, min(60, held))
    w = d[(d.index >= r["entry_day"] - pd.Timedelta(days=pad_before)) &
          (d.index <= end + pd.Timedelta(days=max(10, held // 4)))]
    ax.axvspan(r["entry_day"], end, color=WIN_BG if r["ret"] > 0 else LOSS_BG, zorder=0, lw=0)
    candles(ax, w, width=0.7 if len(w) < 120 else 0.5)
    ax.plot(w.index, w["SMA50"], color=SMA50_C, lw=1.3, zorder=4)
    ax.plot(w.index, w["SMA200"], color=SMA200_C, lw=1.3, zorder=4)
    held_w = w.loc[r["entry_day"]:end]
    why = ""
    if RULE["st"] is None:
        ax.plot(held_w.index, held_w["EXIT"], color=EXIT_C, lw=1.6, ls=(0, (4, 2)), zorder=5)
    else:                                         # the rule's stop, the high to beat, the lower highs
        st = RULE["st"].reindex(w.index)
        sh = st.loc[r["entry_day"]:end]
        ax.plot(sh.index, sh["stop"], color=STOP_C, lw=1.6, ls=(0, (4, 2)), zorder=5, drawstyle="steps-post")
        ax.plot(sh.index, sh["peak"], color=PEAK_C, lw=1.1, ls=(0, (1, 1.5)), zorder=5, drawstyle="steps-post")
        from strategies import _structure
        for day in sh.index[sh["lh"].fillna(False).astype(bool)]:
            k = w.index.get_loc(day) - (RULE.get("right") or _structure.PIVOT)
            if k >= 0:
                ax.scatter([w.index[k]], [w["High"].iat[k]], marker="x", s=45, color=LH_C, lw=1.8, zorder=6)
        sig_day = r["exit_day"] - pd.Timedelta(days=1)
        if not r["open"] and sig_day in RULE["st"].index:
            why = RULE["st"].at[sig_day, "reason"]
    ax.scatter([r["entry_day"]], [r["entry"]], marker="^", s=70, color=INK, edgecolor=SURFACE, lw=1.2, zorder=6)
    ax.annotate(f"Buy {money(r['entry'])}", (r["entry_day"], r["entry"]), xytext=(-6, -10),
                textcoords="offset points", ha="right", va="top", fontsize=7.5, color=INK,
                bbox=dict(boxstyle="square,pad=0.15", fc=SURFACE, ec="none", alpha=0.85), zorder=7)
    if not r["open"]:
        ax.scatter([r["exit_day"]], [r["exit"]], marker="v", s=70, color=INK, edgecolor=SURFACE, lw=1.2, zorder=6)
        ax.annotate(f"Sell {money(r['exit'])}", (r["exit_day"], r["exit"]), xytext=(6, 10),
                    textcoords="offset points", ha="left", va="bottom", fontsize=7.5, color=INK,
                    bbox=dict(boxstyle="square,pad=0.15", fc=SURFACE, ec="none", alpha=0.85), zorder=7)
    lo = min(w["Low"].min(), r["entry"], r["exit"])
    hi = max(w["High"].max(), r["entry"], r["exit"])
    ax.set_ylim(lo - (hi - lo) * 0.12, hi + (hi - lo) * 0.16)          # room for the labels
    ax.grid(True, color=GRID, lw=0.5, zorder=0)
    digits = 1 if hi - lo < 8000 else 0
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"${v / 1000:,.{digits}f}k"))
    loc = mdates.AutoDateLocator(minticks=3, maxticks=5)
    ax.xaxis.set_major_locator(loc)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    sold = "still open" if r["open"] else f"{r['exit_day']:%d %b %Y}"
    ax.set_title(f"#{n}  {r['entry_day']:%d %b %Y} → {sold}  ·  {held} day{'' if held == 1 else 's'}  ·  "
                 f"{r['ret'] * 100:+.1f}%" + (f"  ·  {why}" if why else ""),
                 loc="left", fontsize=9, color=INK)


def pages(d, t, folder, title):
    paths = []
    for p in range(0, len(t), PER_PAGE):
        chunk = t.iloc[p:p + PER_PAGE]
        rows = int(np.ceil(len(chunk) / 3))
        fig, axes = plt.subplots(rows, 3, figsize=(15, 3.3 * rows + 0.9), dpi=120)
        axes = np.atleast_2d(axes)
        for k, ax in enumerate(axes.flat):
            if k < len(chunk):
                trade_panel(ax, d, chunk.iloc[k], p + k + 1)
            else:
                ax.axis("off")
        handles = [plt.Line2D([], [], color=SMA50_C, lw=1.6, label="50-day average"),
                   plt.Line2D([], [], color=SMA200_C, lw=1.6, label="200-day average")]
        if RULE["st"] is None:
            handles.append(plt.Line2D([], [], color=EXIT_C, lw=1.8, ls=(0, (4, 2)),
                                      label="Exit line (higher average; a close below it = sell next open)"))
        else:
            handles += [plt.Line2D([], [], color=STOP_C, lw=1.8, ls=(0, (4, 2)), label="Stop (a close below = sell)"),
                        plt.Line2D([], [], color=PEAK_C, lw=1.4, ls=(0, (1, 1.5)), label="Highest high since entry"),
                        plt.Line2D([], [], marker="x", color=LH_C, ls="", mew=1.8, label="Lower high")]
        handles += [plt.Line2D([], [], marker="^", color=INK, ls="", label="Buy (next 00:00 UTC open)"),
                    plt.Line2D([], [], marker="v", color=INK, ls="", label="Sell")]
        last = min(p + PER_PAGE, len(t))
        H = fig.get_figheight()
        note = f"; {d.attrs['clipped']} bad price spikes clipped" if d.attrs.get("clipped") else ""
        what = "no stop-loss: the red line is the exit" if RULE["st"] is None else \
            "decisions on daily closes, carried out at the next open"
        fig.suptitle(f"{title} · trades {p + 1}-{last} of {len(t)}  ({what}{note})",
                     x=0.01, y=1 - 0.12 / H, ha="left", va="top", fontsize=11, color=INK)
        fig.legend(handles=handles, loc="upper left", ncol=len(handles), frameon=False, fontsize=9,
                   bbox_to_anchor=(0.005, 1 - 0.42 / H))
        fig.tight_layout(rect=(0, 0, 1, 1 - 0.8 / H))
        path = os.path.join(folder, f"trades_{p + 1:02d}-{last:02d}.png")
        fig.savefig(path, facecolor=SURFACE)
        plt.close(fig)
        paths.append(path)
    return paths


def make(d, t, folder, title):
    os.makedirs(folder, exist_ok=True)
    overview(d, t, os.path.join(folder, "overview.png"), title)
    return [os.path.join(folder, "overview.png")] + pages(d, t, folder, title)


def main():
    results = os.environ.get("RESULTS") or "crypto-btc-strategies-btc_trend-8y"
    version = os.environ.get("VERSION") or "Hold BTC while above BOTH"
    symbol = os.environ.get("SYMBOL") or "BTC/USD"
    t = load_trades(results, version)
    first = (t["entry_day"].min() - pd.Timedelta(days=330)).to_pydatetime().replace(tzinfo=dt.timezone.utc)
    d = load_prices(symbol, first)
    title = f"{t['version'].iat[0]} — {symbol}"
    folder = os.path.join("reports", "backtest", results, "charts")
    rule = os.environ.get("RULE")
    if rule:
        import importlib
        mod = importlib.import_module(f"strategies.{rule}")
        from strategies import _structure
        RULE["name"], RULE["st"] = rule, _structure.run(d[["Open", "High", "Low", "Close"]], **mod.KW)
        RULE["right"] = mod.KW.get("lh_right")
        folder = os.path.join(folder, rule)
    for p in make(d, t, folder, title):
        print("saved", p, flush=True)


if __name__ == "__main__":
    main()
