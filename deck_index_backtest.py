"""
Backtest of the Fidelity-deck patterns (deck_patterns.py) on ONE symbol - by default OEF, the ETF of the
S&P 100 index - on daily, 4-hour and 2-hour candles, compared with buying and holding it.

Candles: 4-hour = 9:30-13:30 and 13:30-16:00, 2-hour = 9:30, 11:30, 13:30, 15:30 (last one 30 min),
built from Alpaca's 15-minute candles (regular session), like TradingView does for US stocks.

Rules (same as the daily S&P 100 test, on that timeframe's candles):
    signal  each candle, the detector sees the last 290 candles; a charted pattern whose breakout candle
            is that candle (close beyond the line by 0.2 x ATR) -> trade at the NEXT candle's open
    stop    the deck's protective stop (outside the breakout candle); checked on every candle's low/high,
            a gap through it fills at the open
    exit    A/C: the deck's target (2 x the risk if the deck has none)
            B:   no target, sold at the next open after a close below the lowest low of the last 20
                 candles (above the highest high for shorts)
    if one candle touches both stop and target, the stop counts (cautious)
    money   the whole account in the trade (no leverage), one trade at a time; 0.02% slippage per side
Versions: A long, deck target · B long, trailing exit (= daily version 6) · C long + short, deck target.

    python deck_index_backtest.py      (SYMBOL=OEF YEARS=8 TIMEFRAMES="1d 4h 2h"; needs ALPACA keys)

Bitcoin (SYMBOL=BTC, 22 Oct 2026): daily candles only, from Yahoo/Coinbase like the main backtest's "daily (from
2015)" source (crypto.daily_long), START = first test day (e.g. 2015-01-01 or 2021-01-01). Costs 0.05% fee +
0.05% slippage per side (like the BTC strategy tests). Versions: B (= daily version 6), D = B + the live stock
trader's 50-day filter (buy only if the breakout candle closes above its 50-day average), and R = the live
Bitcoin bot's 50-day rule (strategies/btc50_uprise_ma_f1.py) in the same simulator, for reference. With a
START before 2021, the summary also splits each version into before / from 2021.
Results: reports/backtest/index-deck-<symbol>-<years>y/ (summary.txt, trades.csv, equity.csv, chart.png)
"""
import datetime as dt
import os
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import backtest
import deck_patterns as dp
import pattern_scan

SYMBOL = os.environ.get("SYMBOL", "OEF").upper().replace("/USD", "")
CRYPTO = SYMBOL in ("BTC", "ETH")
START = os.environ.get("START", "").strip()       # fixed first test day (YYYY-MM-DD); overrides YEARS
YEARS = float(os.environ.get("YEARS", "8"))
TIMEFRAMES = os.environ.get("TIMEFRAMES", "1d" if CRYPTO else "1d 4h 2h").split()
WINDOW = 290            # candles the detector sees each time (like the live scan on daily candles)
TRAIL = 20              # trailing exit: lowest low of the last 20 candles of that timeframe
NO_TARGET_R = 2.0
SLIP = 0.001 if CRYPTO else 0.0002     # crypto: 0.05% fee + 0.05% slippage per side
OFFSET = {"4h": "90min", "2h": "90min"}          # bins start at 9:30 New York
MINUTES = {"4h": 240, "2h": 120}
VERSIONS = {"A": ("Long, deck stop + deck target", False, "target"),
            "B": ("Long, deck stop + trailing exit (20 candles)", False, "trail"),
            "C": ("Long + short, deck stop + deck target", True, "target"),
            "D": ("Long, deck stop + trailing exit + 50-day filter (= live stock trader, no leverage)", False, "trail50"),
            "R": ("Reference: the live Bitcoin bot's 50-day rule (btc50_uprise_ma_f1)", False, "rule")}
CRYPTO_INTRADAY = CRYPTO and TIMEFRAMES != ["1d"]


def run_versions(tf):
    if not CRYPTO:
        return ["A", "B", "C"]
    if CRYPTO_INTRADAY:                              # timeframe test: version 6, + the 50-day rule on 1d for reference
        return ["B", "R"] if tf == "1d" else ["B"]
    return ["B", "D", "R"]


def crypto_candles(h, tf):
    """Crypto trades 24/7: candles of tf built from 15-min candles, aligned to 00:00 UTC (45 min divides a day)."""
    if tf == "15m":
        return h.copy()
    rule = tf.replace("m", "min").replace("h", "h")
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    return h.resample(rule, origin="start_day").agg(agg).dropna()


def clean_spikes(h, limit=0.05):
    """Alpaca's crypto history has a few bad ticks (a wick far outside every price around it). A 15-min high
    (low) more than `limit` above (below) the candle's open/close AND the neighbours' closes and opens is cut back
    to that range, so a bad tick can't set off a stop. Returns the cleaned candles and how many were cut."""
    o, c = h["Open"], h["Close"]
    near = pd.concat([o, c, c.shift(1), o.shift(-1)], axis=1)
    top, bot = near.max(axis=1), near.min(axis=1)
    hi_bad = h["High"] > top * (1 + limit)
    lo_bad = h["Low"] < bot * (1 - limit)
    h = h.copy()
    h.loc[hi_bad, "High"] = top[hi_bad]
    h.loc[lo_bad, "Low"] = bot[lo_bad]
    return h, int(hi_bad.sum() + lo_bad.sum())


def candles(h, tf):
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    out = []
    for day, g in h.groupby(h.index.date):                   # never mix two days in one candle
        out.append(g.resample(f"{MINUTES[tf]}min", offset=OFFSET[tf]).agg(agg).dropna())
    d = pd.concat(out)
    d.index = d.index.tz_localize(None)
    return d


def signals(df):
    """[(bar of the breakout candle, dir, stop, target or nan, pattern name)] - each pattern once."""
    df = dp.add_atr(df)
    out, seen = [], set()
    t0 = time.time()
    for i in range(150, len(df) - 1):
        w = df.iloc[max(0, i - WINDOW + 1):i + 1]
        try:
            pats = dp.detect(w)
        except Exception:
            continue
        for p in pats:
            b = p.get("brk")
            if p["stage"] != "breakout" or not b or b["bar"] != len(w) - 1 or pattern_scan.mode_of(p) != "chart":
                continue
            key = (p["name"], b["dir"], w.index[p["start"]])
            if key in seen:
                continue
            seen.add(key)
            out.append((i, b["dir"], b["stop"], b["target"] if b.get("target") is not None else np.nan,
                        p["name"].split(" (")[0]))
    print(f"  {len(out)} breakouts in {time.time() - t0:.0f}s", flush=True)
    return out


def simulate_rule(df, start_bar):
    """The Bitcoin bot's rule: signals on daily closes, bought / sold at the next open, whole account."""
    from strategies import btc50_uprise_ma_f1 as rule
    from strategies import _structure
    r = _structure.run(df[["Open", "High", "Low", "Close"]], **rule.KW)
    ent, ex = r["entry"].values, r["exit"].values
    o, c = df["Open"].values, df["Close"].values
    cash, eq, trades, pos = 100_000.0, np.full(len(df), np.nan), [], None
    for i in range(start_bar, len(df)):
        if pos is not None and ex[i - 1] and i - 1 >= pos["j"]:
            x = o[i] * (1 - SLIP)
            pnl = (x - pos["entry"]) * pos["qty"]
            cash += pnl
            trades.append({"pattern": "50-day rule", "entry_time": df.index[pos["j"]], "exit_time": df.index[i],
                           "entry": pos["entry"], "stop": np.nan, "target": np.nan, "exit": x, "result": "rule exit",
                           "R": np.nan, "pct": (x / pos["entry"] - 1) * 100, "pnl": pnl, "candles": i - pos["j"]})
            pos = None
        if pos is None and i - 1 >= start_bar and ent[i - 1]:
            e = o[i] * (1 + SLIP)
            pos = dict(j=i, entry=e, qty=cash / e)
        eq[i] = cash if pos is None else cash + (c[i] - pos["entry"]) * pos["qty"]
    return pd.DataFrame(trades), pd.Series(eq, index=df.index).ffill().dropna()


def simulate(df, sigs, shorts, exit_mode, start_bar):
    if exit_mode == "rule":
        return simulate_rule(df, start_bar)
    o, h, l, c = (df[k].values for k in ("Open", "High", "Low", "Close"))
    ma50 = pd.Series(c).rolling(50).mean().values
    if exit_mode == "trail50":                           # live stock trader: breakout close above its 50-day
        sigs = [s for s in sigs if s[1] != "LONG" or (np.isfinite(ma50[s[0]]) and c[s[0]] > ma50[s[0]])]
        exit_mode = "trail"
    low_n = pd.Series(l).rolling(TRAIL).min().values
    high_n = pd.Series(h).rolling(TRAIL).max().values
    by_bar = {}
    for s in sigs:
        if s[0] >= start_bar and (shorts or s[1] == "LONG"):
            by_bar.setdefault(s[0], s)                       # first pattern of that candle
    cash, eq, trades = 100_000.0, np.full(len(df), np.nan), []
    pos = None
    i = start_bar
    while i < len(df):
        if pos is None:
            eq[i] = cash
            s = by_bar.get(i)
            if s and i + 1 < len(df):
                j = i + 1
                sign = 1 if s[1] == "LONG" else -1
                entry = o[j] * (1 + sign * SLIP)
                risk = sign * (entry - s[2])
                tgt = s[3] if np.isfinite(s[3]) else entry + sign * NO_TARGET_R * risk
                if risk > 0 and (exit_mode == "trail" or sign * (tgt - entry) > 0):
                    pos = dict(j=j, sign=sign, entry=entry, stop=s[2], tgt=tgt if exit_mode == "target" else None,
                               qty=cash / entry, risk=risk, name=s[4], dir=s[1], pending=False)
                    i = j
                    continue
            i += 1
            continue
        # in a trade: candle i
        sg, x, why = pos["sign"], None, None
        if pos["pending"]:                                   # trailing exit decided at the last close
            x, why = o[i] * (1 - sg * SLIP), "trend exit"
        elif sg * (o[i] - pos["stop"]) <= 0 and i > pos["j"]:
            x, why = o[i] * (1 - sg * SLIP), "stop (gap)"
        elif pos["tgt"] is not None and sg * (o[i] - pos["tgt"]) >= 0 and i > pos["j"]:
            x, why = o[i], "target (gap)"
        elif sg * ((l[i] if sg > 0 else h[i]) - pos["stop"]) <= 0:
            x, why = pos["stop"] * (1 - sg * SLIP), "stop"
        elif pos["tgt"] is not None and sg * ((h[i] if sg > 0 else l[i]) - pos["tgt"]) >= 0:
            x, why = pos["tgt"], "target"
        if x is not None:
            pnl = sg * (x - pos["entry"]) * pos["qty"]
            cash += pnl
            trades.append({"pattern": f"{pos['name']} {'↑' if sg > 0 else '↓'}", "entry_time": df.index[pos["j"]],
                           "exit_time": df.index[i], "entry": pos["entry"], "stop": pos["stop"], "target": pos["tgt"],
                           "exit": x, "result": why, "R": sg * (x - pos["entry"]) / pos["risk"],
                           "pct": sg * (x / pos["entry"] - 1) * 100, "pnl": pnl, "candles": i - pos["j"]})
            eq[i] = cash
            pos = None
            i += 1
            continue
        eq[i] = cash + sg * (c[i] - pos["entry"]) * pos["qty"]
        if pos["tgt"] is None and i > pos["j"] and np.isfinite(low_n[i - 1]):
            lvl = low_n[i - 1] if sg > 0 else high_n[i - 1]
            if sg * (c[i] - lvl) < 0:
                pos["pending"] = True                        # sell at the next open
        i += 1
    eq = pd.Series(eq, index=df.index).ffill().dropna()
    return pd.DataFrame(trades), eq


def stats(trades, eq, bh):
    daily = eq.groupby(eq.index.normalize()).last()
    yrs = max((daily.index[-1] - daily.index[0]).days / 365.25, 1e-9)
    cagr = (daily.iat[-1] / daily.iat[0]) ** (1 / yrs) - 1
    dd = (daily / daily.cummax() - 1).min()
    n = len(trades)
    win = (trades["pnl"] > 0).mean() if n else 0
    gp, gl = trades.loc[trades.pnl > 0, "pnl"].sum() if n else 0, -trades.loc[trades.pnl < 0, "pnl"].sum() if n else 0
    inv = 0.0
    if n:
        held = sum((t.exit_time - t.entry_time).total_seconds() for t in trades.itertuples())
        inv = held / max((eq.index[-1] - eq.index[0]).total_seconds(), 1)
    by_year = daily.groupby(daily.index.year).last()
    by_year = by_year / pd.concat([pd.Series([daily.iat[0]]), by_year.iloc[:-1]]).values - 1
    return dict(cagr=cagr, dd=dd, final=daily.iat[-1], n=n, win=win, avgR=trades["R"].mean() if n else 0,
                pf=gp / gl if gl else np.inf, inv=inv, years=by_year)


def period_text(eq, trades):
    d = eq.groupby(eq.index.normalize()).last()
    yrs = max((d.index[-1] - d.index[0]).days / 365.25, 1e-9)
    cagr = (d.iat[-1] / d.iat[0]) ** (1 / yrs) - 1
    dd = (d / d.cummax() - 1).min()
    if trades is None:
        return f"{backtest.pct(cagr)}/yr, max drop {backtest.pct(dd)}"
    win = (trades["pnl"] > 0).mean() if len(trades) else 0
    return f"{backtest.pct(cagr)}/yr, max drop {backtest.pct(dd)}, {len(trades)} trades, win {win:.0%}"


def main():
    end = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20)
    start = end - dt.timedelta(days=round(365.25 * YEARS))
    if START:
        start = dt.datetime.fromisoformat(START).replace(tzinfo=dt.timezone.utc)
    t0 = time.time()
    if CRYPTO:                                       # daily candles back to 2015 (Yahoo, Coinbase as backup)
        import crypto
        d1 = crypto.daily_long(f"{SYMBOL}/USD", start - dt.timedelta(days=450))
        d1.index = d1.index.tz_localize(None)
        d1 = d1[["Open", "High", "Low", "Close", "Volume"]].astype(float)
        h15 = None
        t_start = max(pd.Timestamp(start.date()), d1.index[0] + pd.Timedelta(days=21))
        print(f"{SYMBOL}: {len(d1)} daily candles from {d1.index[0]:%Y-%m-%d}, test from {t_start:%Y-%m-%d}", flush=True)
        if CRYPTO_INTRADAY:                          # Alpaca's 15-min candles (from 2021) for the shorter candles
            h15 = crypto.bars([f"{SYMBOL}/USD"], "15Min", start - dt.timedelta(days=60), end)[f"{SYMBOL}/USD"]
            h15 = h15[["Open", "High", "Low", "Close", "Volume"]].astype(float)
            h15.index = h15.index.tz_convert("UTC").tz_localize(None)
            h15, n_cut = clean_spikes(h15)
            print(f"{SYMBOL}: {len(h15)} 15-min candles from {h15.index[0]:%Y-%m-%d %H:%M} (Alpaca), "
                  f"{n_cut} bad wicks cut", flush=True)
            # every timeframe starts on the same day: 4h needs 150 candles (25 days) before the first test day
            t_start = max(t_start, (h15.index[0] + pd.Timedelta(days=30)).normalize())
    else:
        headers = backtest._headers()
        d1 = backtest.fetch(SYMBOL, "1Day", start - dt.timedelta(days=500), end, headers)
        h15 = backtest.fetch(SYMBOL, "15Min", start - dt.timedelta(days=200), end, headers)
        print(f"{SYMBOL}: {len(d1)} daily, {len(h15)} 15-min candles ({time.time() - t0:.0f}s)", flush=True)
        t_start = pd.Timestamp(start).tz_convert(backtest.ET).tz_localize(None)

    bh_d = d1[d1.index >= t_start.normalize()]["Close"]
    bh = bh_d / bh_d.iat[0] * 100_000
    b_yrs = (bh.index[-1] - bh.index[0]).days / 365.25
    b_cagr = (bh.iat[-1] / 100_000) ** (1 / b_yrs) - 1
    b_dd = (bh / bh.cummax() - 1).min()
    b_year = bh.groupby(bh.index.year).last()
    b_year = b_year / pd.concat([pd.Series([100_000.0]), b_year.iloc[:-1]]).values - 1

    span = f"from{START}" if START else f"{YEARS:g}y"
    if CRYPTO_INTRADAY:
        span += "-" + "-".join(TIMEFRAMES)
    out = os.path.join("reports", "backtest", f"index-deck-{SYMBOL.lower()}-{span}")
    os.makedirs(out, exist_ok=True)
    what = (f"candles {' / '.join(TIMEFRAMES)} (1d: Yahoo/Coinbase; shorter: Alpaca 15-min, built 00:00 UTC-aligned, "
            f"{n_cut} bad wicks cut)" if CRYPTO_INTRADAY else "daily candles (Yahoo/Coinbase)" if CRYPTO else
            "(S&P 100 index ETF): daily vs 4-hour vs 2-hour candles")
    cost = "0.05% fee + 0.05% slippage per side" if CRYPTO else "0.02% slippage per side"
    lines = [f"📊 Fidelity-deck patterns on {SYMBOL} alone {what}",
             f"{bh.index[0]:%d %b %Y} → {bh.index[-1]:%d %b %Y} ({b_yrs:.1f} years), $100,000, whole account in the trade, "
             f"one trade at a time, next-candle open fills, {cost}, no leverage.",
             f"Buy & hold {SYMBOL}: {backtest.pct(b_cagr)}/yr, max drop {backtest.pct(b_dd)}, final ${bh.iat[-1]:,.0f}", ""]
    all_trades, curves, rows = [], {f"Buy & hold {SYMBOL}": bh}, []
    for tf in TIMEFRAMES:
        df = d1.copy() if tf == "1d" else crypto_candles(h15, tf) if CRYPTO else candles(h15, tf)
        df = df[df.index >= t_start - pd.Timedelta(days=400 if tf == "1d" else 120)]
        start_bar = int(np.searchsorted(df.index.values, np.datetime64(t_start)))
        print(f"{tf}: {len(df)} candles, test from candle {start_bar}", flush=True)
        sigs = signals(df)
        lines.append(f"━━ {tf} candles ━━ ({sum(1 for s in sigs if s[0] >= start_bar)} pattern breakouts in the test period)")
        for key in run_versions(tf):
            name, shorts, mode = VERSIONS[key]
            tr, eq = simulate(df, sigs, shorts, mode, start_bar)
            st = stats(tr, eq, bh)
            rows.append({"tf": tf, "version": key, **{k: v for k, v in st.items() if k != "years"}})
            mark = "✅" if st["final"] >= bh.iat[-1] else "  "
            lines.append(f"{mark} {key}. {name}: {backtest.pct(st['cagr'])}/yr, max drop {backtest.pct(st['dd'])}, "
                         f"final ${st['final']:,.0f}")
            if len(tr) and t_start.year < 2021:       # before / from 2021 (is it luck?)
                for lbl, a, b in (("before 2021", None, "2020-12-31"), ("from 2021", "2021-01-01", None)):
                    e_ = eq.loc[a:b]
                    t_ = tr[(tr.entry_time >= a) if a else (tr.entry_time <= b)]
                    lines.append(f"     {lbl}: " + period_text(e_, t_))
            avg_r = "" if mode == "rule" else f", avg {st['avgR']:+.2f}R"
            lines.append(f"     {st['n']} trades, win {st['win']:.0%}{avg_r}, profit factor "
                         f"{st['pf']:.2f}, in the market {st['inv']:.0%} of the time")
            yr = "  ".join(f"{y}: {v * 100:+.0f}%" for y, v in st["years"].items())
            lines.append(f"     by year: {yr}")
            if len(tr):
                all_trades.append(tr.assign(timeframe=tf, version=key))
            curves[f"{tf} {key}"] = eq.groupby(eq.index.normalize()).last()
        lines.append("")
    if t_start.year < 2021:
        lines.append(f"Buy & hold {SYMBOL} before 2021: " + period_text(bh.loc[:"2020-12-31"], None))
        lines.append(f"Buy & hold {SYMBOL} from 2021: " + period_text(bh.loc["2021-01-01":], None))
    lines.append(f"{SYMBOL} buy & hold by year: " + "  ".join(f"{y}: {v * 100:+.0f}%" for y, v in b_year.items()))
    # per pattern, per timeframe (version C trades = every breakout taken, both directions)
    if all_trades:
        t = pd.concat(all_trades, ignore_index=True)
        t.to_csv(os.path.join(out, "trades.csv"), index=False)
    if all_trades and not CRYPTO:
        lines += ["", "Per pattern (version C: every breakout taken, both directions): trades / avg R"]
        c = t[t.version == "C"]
        g = c.groupby(["pattern", "timeframe"])["R"].agg(["size", "mean"]).unstack("timeframe")
        for pat, r in g.iterrows():
            cells = []
            for tf in TIMEFRAMES:
                n = r.get(("size", tf), np.nan)
                cells.append(f"{tf} {int(n):>3} {r[('mean', tf)]:+.2f}R" if n == n else f"{tf}   -       ")
            lines.append(f"  {pat:<34} " + "   ".join(cells))
    lines += ["", "Caveat: one symbol, so few trades per version: a handful of trades can change the result. "
              "Past results don't guarantee future ones."]
    text = "\n".join(lines)
    print("\n" + text)
    with open(os.path.join(out, "summary.txt"), "w") as f:
        f.write(text + "\n")
    pd.DataFrame(rows).to_csv(os.path.join(out, "results.csv"), index=False)
    eqs = pd.DataFrame(curves).ffill()
    eqs.round(0).to_csv(os.path.join(out, "equity.csv"), index_label="date")

    fig, ax = plt.subplots(figsize=(12, 6.5), dpi=110)
    fig.patch.set_facecolor("black"); ax.set_facecolor("black")
    for s in ax.spines.values():
        s.set_color("#444")
    ax.tick_params(colors="#bbbbbb"); ax.grid(color="#222")
    cols = {"1d": "#ffd54f", "4h": "#42a5f5", "2h": "#ef5350", "1h": "#66bb6a", "45m": "#ab47bc",
            "30m": "#ff7043", "15m": "#26c6da"}
    ls = {"A": "-", "B": "--", "C": ":", "D": "-.", "R": "-"}
    for name, s in curves.items():
        if name.startswith("Buy"):
            ax.plot(s.index, s.values, color="#bbbbbb", lw=2.2, label=name)
        else:
            tf, v = name.split()
            ax.plot(s.index, s.values, color=cols.get(tf, "#fff"), ls=ls[v], lw=1.3, label=f"{tf} {v}. {VERSIONS[v][0]}")
    ax.set_title(f"Deck patterns on {SYMBOL}" + ("" if CRYPTO else ": daily vs 4h vs 2h"), color="white")
    if CRYPTO:
        ax.set_yscale("log")
    ax.set_ylabel("Account value ($)", color="#bbbbbb")
    leg = ax.legend(fontsize=7.5, facecolor="black", edgecolor="#444", ncol=2)
    for x in leg.get_texts():
        x.set_color("#ddd")
    fig.tight_layout()
    fig.savefig(os.path.join(out, "chart.png"), facecolor="black")
    print(f"Saved in {out}")


if __name__ == "__main__":
    main()
