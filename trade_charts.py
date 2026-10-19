"""
Pictures of a backtest's trades: one overview chart of the whole period, then close-ups of the trades
(candles, the strategy's lines, where it bought/sold or shorted/covered), 12 trades per picture.

Run by .github/workflows/trade-charts.yml (Actions -> Trade charts), which commits the pictures to
reports/backtest/<folder>/charts/. Settings (environment variables):
    RESULTS   results folder in reports/backtest/   (default crypto-btc-strategies-btc_trend-8y)
    VERSION   start of the version's name in trades.csv (default: the 50 & 200-day Bitcoin rule)
    SYMBOL    coin                                   (default BTC/USD)
    RULE      optional: a btc50_*.py strategy file (e.g. btc50_uprise_ma_f1). Draws that rule's stop, the highest
              high since entry and the lower highs (lowest low / higher lows for shorts) instead of the
              averages' exit line; the pictures go to charts/<RULE>/. If the file has TF (e.g. "2h"), the
              close-ups use candles of that size, like the backtest did.
    PICK      optional: which trades get close-ups (default: all, 12 per picture). Space-separated:
                extremes:6           the 6 best and the 6 worst trades in one picture
                random:5             5 random winners and 5 random losers (one row each; random:5:7 = seed 7)
                from:2022-05-01:24   24 trades in a row from that date (12 per picture)

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
SHORTS = [False]                         # set by load_trades(): every trade is a short (labels, markers)
TF = {"rule": None, "unit": pd.Timedelta(days=1), "label": "daily", "candle": "day"}   # candle size of the close-ups
STOP_C, PEAK_C, LH_C = "#e34948", "#8c8b85", "#6250d6"
SPIKE = 0.15                 # wicks more than 15% beyond the candle body are bad ticks: clipped

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": GRID,
                     "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
                     "axes.facecolor": SURFACE, "figure.facecolor": SURFACE})


def set_tf(rule):
    """Candle size from a strategy's TF ("2h", "1h", "30min", "15min"); None = daily."""
    if not rule:
        return
    unit = pd.Timedelta(rule)
    mins = int(unit.total_seconds() // 60)
    label = f"{mins // 60}-hour" if mins % 60 == 0 else f"{mins}-minute"
    TF.update(rule=rule, unit=unit, label=label, candle=f"{mins // 60}h" if mins % 60 == 0 else f"{mins}m")


def load_trades(results, version):
    t = pd.read_csv(os.path.join("reports", "backtest", results, "trades.csv"))
    t = t[t["version"].str.startswith(version)].copy()
    if t.empty:
        raise SystemExit(f"No trades of a version starting with {version!r} in {results}")
    et, xt = pd.to_datetime(t["entry_time"]), pd.to_datetime(t["exit_time"])
    if et.dt.tz is not None:
        et, xt = et.dt.tz_convert("UTC").dt.tz_localize(None), xt.dt.tz_convert("UTC").dt.tz_localize(None)
    # the candle the trade starts in (filled at its open) and the candle it ends in; exit_time is the end of
    # the 15-min candle whose open filled the exit. For daily candles these are the entry and exit days.
    t["entry_day"] = et.dt.floor(TF["unit"])
    t["exit_day"] = (xt - pd.Timedelta(minutes=15)).dt.floor(TF["unit"])
    t["held"] = xt - pd.Timedelta(minutes=15) - et
    t["ret"] = t["exit"] * (1 - 0.0025) / (t["entry"] * (1 + 0.0025)) - 1
    short = t["direction"] == "SHORT"                  # shorts win when the price falls
    t.loc[short, "ret"] = (t["pnl"] / (t["qty"] * t["entry"]))[short]
    SHORTS[0] = bool(short.all())
    t["open"] = t["result"] == "still open"
    t = t.sort_values("entry_day").reset_index(drop=True)
    t["n"] = np.arange(1, len(t) + 1)                  # trade number in the full list
    return t


def clean(d, name):
    # bad ticks: Alpaca's crypto history has a few wicks far outside the real price (e.g. ~$8k in Oct 2021
    # with BTC near $60k). The strategy only uses closes and opens, so only the pictures are cleaned up.
    body_lo, body_hi = d[["Open", "Close"]].min(axis=1), d[["Open", "Close"]].max(axis=1)
    bad = (d["Low"] < body_lo * (1 - SPIKE)) | (d["High"] > body_hi * (1 + SPIKE))
    d["Low"] = d["Low"].clip(lower=body_lo * (1 - SPIKE))
    d["High"] = d["High"].clip(upper=body_hi * (1 + SPIKE))
    print(f"Clipped {int(bad.sum())} {name} candles with a wick more than {SPIKE:.0%} beyond the body", flush=True)
    d.attrs["clipped"] = int(bad.sum())
    d["SMA50"] = d["Close"].rolling(50).mean()
    d["SMA200"] = d["Close"].rolling(200).mean()
    d["EXIT"] = d[["SMA50", "SMA200"]].max(axis=1)
    return d


def load_prices(symbol, first):
    """Daily candles (UTC days) from hourly candles, like the backtest and the live scan, and the close-ups'
    candles: the same daily candles, or TF-sized candles (UTC-aligned) when the rule has TF."""
    now = dt.datetime.now(dt.timezone.utc)
    h = None
    try:
        small = TF["rule"] and TF["unit"] < pd.Timedelta(hours=1)      # 30/15-min candles need 15-min prices
        h = crypto.bars([symbol], "15Min" if small else "1Hour", first, now)[symbol][
            ["Open", "High", "Low", "Close", "Volume"]]
        d = crypto.daily_from(h)
        print(f"Alpaca: {len(d)} days from {d.index[0]:%Y-%m-%d}", flush=True)
    except Exception as e:
        if TF["rule"]:
            raise SystemExit(f"Alpaca failed ({e}); {TF['label']} candles need Alpaca's hourly prices")
        print(f"Alpaca failed ({e}); using Yahoo daily candles", flush=True)
        y = crypto.yahoo([symbol], "10y", "1d")[symbol]
        d = y.copy()
        d.index = d.index.tz_localize(None).normalize()
    if not TF["rule"] and d.index[0] > pd.Timestamp(first).tz_localize(None) + pd.Timedelta(days=30):
        # trades before Alpaca's crypto history (2021): daily candles from Yahoo/Coinbase, like the backtest
        dl = crypto.daily_long(symbol, first)
        dl.index = dl.index.tz_localize(None)
        d = dl[["Open", "High", "Low", "Close", "Volume"]].copy()
    daily = clean(d, "daily")
    if not TF["rule"]:
        return daily, daily
    b = crypto.resample(h, TF["rule"])
    b.index = b.index.tz_convert("UTC").tz_localize(None)
    return daily, clean(b, TF["label"])


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


def duration(td):
    if TF["rule"] is None:
        days = td.days
        return f"{days} day{'' if days == 1 else 's'}"
    m = int(round(td.total_seconds() / 60))
    if m < 120:
        return f"{m} min"
    h = int(round(m / 60))
    return f"{h}h" if h < 48 else f"{h // 24}d {h % 24}h"


def overview(d, t, path, title):
    fig, ax = plt.subplots(figsize=(14, 6.2), dpi=130)
    start = t["entry_day"].min().normalize() - pd.Timedelta(days=60)
    w = d[d.index >= start]
    for _, r in t.iterrows():
        end = w.index[-1] if r["open"] else r["exit_day"] + (TF["unit"] if TF["rule"] else pd.Timedelta(0))
        ax.axvspan(r["entry_day"], end, color=WIN_BG if r["ret"] > 0 else LOSS_BG, zorder=0, lw=0)
    ax.plot(w.index, w["Close"], color=INK, lw=1.2, label="Bitcoin daily close", zorder=3)
    ax.plot(w.index, w["SMA50"], color=SMA50_C, lw=1.4, label="50-day average", zorder=2)
    ax.plot(w.index, w["SMA200"], color=SMA200_C, lw=1.4, label="200-day average", zorder=2)
    for _, r in t.iterrows():                    # number the big trades (|return| >= 9%) above the line
        if abs(r["ret"]) >= 0.09:
            seg = w.loc[r["entry_day"].normalize():(w.index[-1] if r["open"] else r["exit_day"])]
            if len(seg):
                ax.annotate(f"#{r['n']}\n{r['ret'] * 100:+.0f}%", (seg["High"].idxmax(), seg["High"].max()),
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


def trade_panel(ax, d, r):
    unit = TF["unit"]
    end = d.index[-1] if r["open"] else r["exit_day"]
    i0, i1 = d.index.searchsorted(r["entry_day"]), d.index.searchsorted(end)
    held_c = i1 - i0                                    # candles held
    pad_before = max(25, min(60, held_c))
    w = d.iloc[max(0, i0 - pad_before):i1 + max(10, held_c // 4) + 1]
    ax.axvspan(r["entry_day"], end, color=WIN_BG if r["ret"] > 0 else LOSS_BG, zorder=0, lw=0)
    candles(ax, w, width=(0.7 if len(w) < 120 else 0.5) * unit / pd.Timedelta(days=1))
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
            if k >= 0:                            # shorts: the higher low that covered
                y = w["Low"].iat[k] if r["direction"] == "SHORT" else w["High"].iat[k]
                ax.scatter([w.index[k]], [y], marker="x", s=45, color=LH_C, lw=1.8, zorder=6)
        sig_day = r["exit_day"] - unit                  # the close that gave the exit signal
        if not r["open"] and sig_day in RULE["st"].index:
            why = RULE["st"].at[sig_day, "reason"]
            if TF["rule"]:
                why = why.replace("50-day", "50-candle average")
    is_short = r["direction"] == "SHORT"
    in_m, out_m = ("v", "^") if is_short else ("^", "v")
    in_w, out_w = ("Short", "Cover") if is_short else ("Buy", "Sell")
    ax.scatter([r["entry_day"]], [r["entry"]], marker=in_m, s=70, color=INK, edgecolor=SURFACE, lw=1.2, zorder=6)
    ax.annotate(f"{in_w} {money(r['entry'])}", (r["entry_day"], r["entry"]), xytext=(-6, -10),
                textcoords="offset points", ha="right", va="top", fontsize=7.5, color=INK,
                bbox=dict(boxstyle="square,pad=0.15", fc=SURFACE, ec="none", alpha=0.85), zorder=7)
    if not r["open"]:
        ax.scatter([r["exit_day"]], [r["exit"]], marker=out_m, s=70, color=INK, edgecolor=SURFACE, lw=1.2, zorder=6)
        ax.annotate(f"{out_w} {money(r['exit'])}", (r["exit_day"], r["exit"]), xytext=(6, 10),
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
    fmt = "%d %b %Y" if TF["rule"] is None else "%d %b %Y %H:%M"
    sold = "still open" if r["open"] else f"{r['exit_day']:{fmt}}"
    ax.set_title(f"#{r['n']}  {r['entry_day']:{fmt}} → {sold}\n{duration(r['held'])}  ·  "
                 f"{r['ret'] * 100:+.1f}%" + (f"  ·  {why}" if why else ""),
                 loc="left", fontsize=9, color=INK)


def page(d, chunk, path, heading, cols=3):
    rows = int(np.ceil(len(chunk) / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(5 * cols, 3.5 * rows + 1.2), dpi=120)
    axes = np.atleast_2d(axes)
    for k, ax in enumerate(axes.flat):
        if k < len(chunk):
            trade_panel(ax, d, chunk.iloc[k])
        else:
            ax.axis("off")
    avg = "day" if TF["rule"] is None else f"candle ({TF['candle']})"
    handles = [plt.Line2D([], [], color=SMA50_C, lw=1.6, label=f"50-{avg} average"),
               plt.Line2D([], [], color=SMA200_C, lw=1.6, label=f"200-{avg} average")]
    if RULE["st"] is None:
        handles.append(plt.Line2D([], [], color=EXIT_C, lw=1.8, ls=(0, (4, 2)),
                                  label="Exit line (higher average; a close below it = sell next open)"))
    else:
        sh = SHORTS[0]
        handles += [plt.Line2D([], [], color=STOP_C, lw=1.8, ls=(0, (4, 2)),
                               label="Stop (a close above = cover)" if sh else "Stop (a close below = sell)"),
                    plt.Line2D([], [], color=PEAK_C, lw=1.4, ls=(0, (1, 1.5)),
                               label="Lowest low since entry" if sh else "Highest high since entry"),
                    plt.Line2D([], [], marker="x", color=LH_C, ls="", mew=1.8, label="Higher low" if sh else "Lower high")]
    nxt = "next 00:00 UTC open" if TF["rule"] is None else "next 15-min open"
    if SHORTS[0]:
        handles += [plt.Line2D([], [], marker="v", color=INK, ls="", label=f"Sell short ({nxt})"),
                    plt.Line2D([], [], marker="^", color=INK, ls="", label="Cover")]
    else:
        handles += [plt.Line2D([], [], marker="^", color=INK, ls="", label=f"Buy ({nxt})"),
                    plt.Line2D([], [], marker="v", color=INK, ls="", label="Sell")]
    H = fig.get_figheight()
    note = f"; {d.attrs['clipped']} bad price spikes clipped" if d.attrs.get("clipped") else ""
    closes = "daily closes" if TF["rule"] is None else f"{TF['label']} closes"
    what = "no stop-loss: the red line is the exit" if RULE["st"] is None else \
        f"decisions on {closes}, carried out at the {nxt.replace('next ', 'next ')}"
    fig.suptitle(f"{heading}\n({what}{note})", x=0.01, y=1 - 0.12 / H, ha="left", va="top", fontsize=11, color=INK)
    fig.legend(handles=handles, loc="upper left", ncol=len(handles), frameon=False, fontsize=9,
               bbox_to_anchor=(0.005, 1 - 0.62 / H))
    fig.tight_layout(rect=(0, 0, 1, 1 - 1.0 / H))
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


def picks(t, pick, title):
    """(file name, heading, trades) for each close-up picture."""
    out = []
    if not pick:
        for p in range(0, len(t), PER_PAGE):
            chunk = t.iloc[p:p + PER_PAGE]
            last = min(p + PER_PAGE, len(t))
            out.append((f"trades_{p + 1:02d}-{last:02d}.png", f"{title} · trades {p + 1}-{last} of {len(t)}", chunk))
        return out
    for item in pick.split():
        kind, _, rest = item.partition(":")
        done = t[~t["open"]]
        if kind == "extremes":
            n = int(rest or 6)
            best = done.sort_values("ret", ascending=False).head(n)
            worst = done.sort_values("ret").head(n)
            out.append((f"best{n}_worst{n}.png",
                        f"{title} · the {n} best trades (first rows) and the {n} worst (last rows) of {len(t)}",
                        pd.concat([best, worst])))
        elif kind == "random":
            # n random winners (first row) and n random losers (second row); fixed seed = same picks every run
            n, _, seed = rest.partition(":")
            n, rng = int(n or 5), np.random.default_rng(int(seed or 1))
            wins, losses = done[done["ret"] > 0], done[done["ret"] <= 0]
            pw = wins.iloc[np.sort(rng.choice(len(wins), min(n, len(wins)), replace=False))]
            pl = losses.iloc[np.sort(rng.choice(len(losses), min(n, len(losses)), replace=False))]
            out.append((f"random{n}_wins_losses.png",
                        f"{title} · {len(pw)} random winners (top row) and {len(pl)} random losers (bottom row) "
                        f"of {len(t)} trades ({len(wins)} won, {len(losses)} lost)",
                        pd.concat([pw, pl]), n))
        elif kind == "from":
            day, _, n = rest.rpartition(":")
            n = int(n)
            seq = t[t["entry_day"] >= pd.Timestamp(day)].head(n)
            if seq.empty:
                print(f"No trades from {day}", flush=True)
                continue
            for p in range(0, len(seq), PER_PAGE):
                chunk = seq.iloc[p:p + PER_PAGE]
                out.append((f"from_{day}_{p + 1:02d}-{p + len(chunk):02d}.png",
                            f"{title} · {len(seq)} trades in a row from {day} "
                            f"(#{chunk['n'].iat[0]}-#{chunk['n'].iat[-1]} of {len(t)})", chunk))
        else:
            raise SystemExit(f"Unknown PICK {item!r} (use extremes:6, random:5 or from:2022-05-01:24)")
    return out


def main():
    results = os.environ.get("RESULTS") or "crypto-btc-strategies-btc_trend-8y"
    version = os.environ.get("VERSION") or "Hold BTC while above BOTH"
    symbol = os.environ.get("SYMBOL") or "BTC/USD"
    rule = os.environ.get("RULE")
    mod = None
    if rule:
        import importlib
        mod = importlib.import_module(f"strategies.{rule}")
        set_tf(getattr(mod, "TF", None))
    t = load_trades(results, version)
    first = (t["entry_day"].min().normalize() - pd.Timedelta(days=330)).to_pydatetime().replace(tzinfo=dt.timezone.utc)
    daily, d = load_prices(symbol, first)
    title = f"{t['version'].iat[0]} — {symbol}"
    folder = os.path.join("reports", "backtest", results, "charts")
    if mod is not None:
        from strategies import _structure
        RULE["name"], RULE["st"] = rule, _structure.run(d[["Open", "High", "Low", "Close"]], **mod.KW)
        RULE["right"] = mod.KW.get("lh_right")
        folder = os.path.join(folder, rule)
    os.makedirs(folder, exist_ok=True)
    overview(daily, t, os.path.join(folder, "overview.png"), title)
    print("saved", os.path.join(folder, "overview.png"), flush=True)
    for name, heading, chunk, *cols in picks(t, os.environ.get("PICK", "").strip(), title):
        print("saved", page(d, chunk, os.path.join(folder, name), heading, *cols), flush=True)


if __name__ == "__main__":
    main()
