"""
What do the winning and losing deck trades have in common? For every trade of a universe backtest
(deck_universe_backtest.py trades.csv) this adds features known AT ENTRY and features of the FIRST DAYS of
the trade, so the losers can be told apart (a filter before buying, or an early exit).

    TRADES=reports/backtest/<folder>/trades.csv FILTER="ma50+gap2pct+reg200+noweak" python deck_trade_features.py
Result: reports/analysis/deck-trade-features.csv (one row per closed trade) - analysed by hand / with Claude.

At entry (signal day = the breakout close, entry = next day's open):
    stop_pct, stop_atr, atr_pct, gap_open, brk_body_atr, brk_range_atr, brk_close_pos, vol_ratio, vol10_ratio,
    dist_ma20/50/200, ma50_slope, ma200_slope, ret5/20/60/126, rs126 (vs SPY), dist_52wh, rsi14, up_days10,
    spy_dist_ma50/200, spy_ret20, spy_rsi14, weekday, month, family, pattern,
    beta_1y (252 daily returns vs SPY), beta_2y_w (104 weekly returns, Bloomberg default), beta_adj
    (Bloomberg adjusted: 0.67 * beta_2y_w + 0.33), corr_spy_1y (correlation of daily returns with SPY)
During the trade (day 1 = entry day; empty once the trade has ended):
    r_d{1,2,3,5,10,15,20}   close in R from the entry; mfe/mae_d{3,5,10} best/worst high/low in R;
    below_brk_d{1,2,3,5}    closed below the breakout close; below_entry_d{1,2,3,5}
"""
import datetime as dt
import os

import numpy as np
import pandas as pd

import deck_patterns as dp
import scanner

TRADES = os.environ.get("TRADES", "")
FILTER = os.environ.get("FILTER", "ma50+gap2pct+reg200+noweak")
LEVERAGE = float(os.environ.get("LEVERAGE", "2"))
OUT = os.environ.get("OUT", "reports/analysis/deck-trade-features.csv")
BETA_OUT = os.environ.get("BETA_OUT", "reports/analysis/deck-beta-buckets.csv")
DAYS = (1, 2, 3, 5, 10, 15, 20)


def rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def indicators(d):
    d = dp.add_atr(d.copy())
    c, v = d["Close"], d["Volume"]
    for n in (20, 50, 200):
        d[f"ma{n}"] = c.rolling(n).mean()
    d["v50"] = v.rolling(50).mean().shift(1)
    d["v10"] = v.rolling(10).mean().shift(1)
    d["hi252"] = d["High"].rolling(252).max()
    d["rsi"] = rsi(c)
    d["up10"] = (c.diff() > 0).rolling(10).sum()
    return d


def beta_series(stock_close, spy_close):
    """Rolling betas vs SPY. Daily: value at a day uses returns up to that day's close.
    Weekly: indexed by the week's last trading day, so .asof(day) only sees completed weeks."""
    px = pd.concat([stock_close, spy_close], axis=1, keys=["s", "m"]).dropna()
    r = px.pct_change()
    cov = r["s"].rolling(252, min_periods=200).cov(r["m"])
    var = r["m"].rolling(252, min_periods=200).var()
    daily = pd.DataFrame({"beta_1y": cov / var,
                          "corr_spy_1y": r["s"].rolling(252, min_periods=200).corr(r["m"])})
    wk = px.groupby(px.index.to_period("W-FRI")).tail(1)          # last trading day of each week
    rw = wk.pct_change()
    wb = rw["s"].rolling(104, min_periods=80).cov(rw["m"]) / rw["m"].rolling(104, min_periods=80).var()
    return daily, wb


def beta_report(out):
    """Results by beta bucket: does market sensitivity at entry matter for the deck trades?"""
    o = out.dropna(subset=["beta_adj"]).copy()
    if o.empty:
        return
    o["bucket_fixed"] = pd.cut(o.beta_adj, [-np.inf, 0.8, 1.0, 1.2, 1.4, np.inf],
                               labels=["<0.8", "0.8-1.0", "1.0-1.2", "1.2-1.4", ">1.4"])
    o["bucket_tercile"] = pd.qcut(o.beta_adj, 3, labels=["low third", "mid third", "high third"])
    o["spy_trend"] = np.where(o.spy_dist_ma200 > 0, "SPY>200MA", "SPY<200MA")
    o["year"] = pd.to_datetime(o.entry_date).dt.year
    rows = []

    def summ(g):
        gross_win = g.R[g.R > 0].sum()
        gross_loss = -g.R[g.R < 0].sum()
        return pd.Series({"trades": len(g), "win_rate": g.win.mean(), "avg_R": g.R.mean(),
                          "median_R": g.R.median(), "total_R": g.R.sum(), "total_pnl": g.pnl.sum(),
                          "profit_factor": gross_win / gross_loss if gross_loss else np.nan,
                          "avg_days": g.days.mean(), "beta_min": g.beta_adj.min(), "beta_max": g.beta_adj.max()})

    for by in (["bucket_fixed"], ["bucket_tercile"], ["spy_trend", "bucket_tercile"],
               ["family", "bucket_tercile"]):
        t = o.groupby(by, observed=True).apply(summ, include_groups=False).reset_index()
        t.insert(0, "split", "+".join(by))
        t = t.rename(columns={by[-1]: "bucket"})
        if len(by) == 2:
            t["bucket"] = t[by[0]].astype(str) + " | " + t["bucket"].astype(str)
            t = t.drop(columns=by[0])
        rows.append(t)
    # per-year: high third minus low third avg R (is the effect stable or one regime?)
    y = o.groupby(["year", "bucket_tercile"], observed=True).R.agg(["mean", "count"]).unstack()
    y.columns = [f"{a}_{b}" for a, b in y.columns]
    res = pd.concat(rows, ignore_index=True)
    os.makedirs(os.path.dirname(BETA_OUT), exist_ok=True)
    res.to_csv(BETA_OUT, index=False)
    y.to_csv(BETA_OUT.replace(".csv", "-by-year.csv"))
    pd.set_option("display.width", 200)
    print("\n=== Results by beta at entry (beta_adj) ===")
    print(res.round(3).to_string(index=False))
    print("\n=== Avg R by year and beta third ===")
    print(y.round(3).to_string())


def main():
    path = TRADES
    if not path:                                       # newest backtest that has this filter
        import glob
        for f in sorted(glob.glob("reports/backtest/universe-deck-v6-*/trades.csv")):   # (checkout: same mtimes)
            try:
                tf = pd.read_csv(f, usecols=["filter", "leverage"])
            except ValueError:                         # older reports without these columns
                continue
            if ((tf["filter"] == FILTER) & (tf["leverage"] == LEVERAGE)).any():
                path = f                               # the last match in name order: the newest dated folder
        if not path:
            raise SystemExit(f"No backtest trades with filter {FILTER} at {LEVERAGE:g}x")
    tr = pd.read_csv(path)
    tr = tr[(tr["filter"] == FILTER) & (tr["leverage"] == LEVERAGE) & ~tr.result.isin(["still open", "de-lever"])]
    if "universe" in tr:
        tr = tr[tr.universe == tr.universe.iloc[0]]
    print(f"{len(tr)} trades from {path}", flush=True)
    tr = tr.copy()
    tr["entry_date"] = pd.to_datetime(tr.entry_date)
    tr["exit_date"] = pd.to_datetime(tr.exit_date)
    syms = sorted(set(tr.symbol)) + ["SPY"]
    start = tr.entry_date.min() - pd.Timedelta(days=800)          # 104 weeks for the weekly beta
    end = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20)
    raw = scanner.alpaca_bars(syms, "1Day", start.to_pydatetime().replace(tzinfo=dt.timezone.utc), end,
                              chunk=50, workers=6)
    daily = {}
    for s, d in raw.items():
        d = d.copy()
        d.index = d.index.tz_convert(scanner.ET).tz_localize(None).normalize()
        daily[s] = indicators(d[["Open", "High", "Low", "Close", "Volume"]])
    spy = daily["SPY"]
    spy_r126 = spy["Close"] / spy["Close"].shift(126) - 1
    betas = {}
    rows = []
    for _, t in tr.iterrows():
        d = daily.get(t.symbol)
        if d is None or t.entry_date not in d.index:
            continue
        i = d.index.get_loc(t.entry_date)
        s = i - 1
        if s < 252:
            continue
        r, c = d.iloc[s], d["Close"]
        risk = t.entry - t.stop
        if risk <= 0:
            continue
        atr = r.ATR
        f = {"stop_pct": risk / t.entry, "stop_atr": risk / atr, "atr_pct": atr / r.Close,
             "gap_open": d["Open"].iat[i] / r.Close - 1,
             "brk_body_atr": (r.Close - r.Open) / atr, "brk_range_atr": (r.High - r.Low) / atr,
             "brk_close_pos": (r.Close - r.Low) / (r.High - r.Low) if r.High > r.Low else 1.0,
             "vol_ratio": r.Volume / r.v50 if r.v50 else np.nan, "vol10_ratio": r.v10 / r.v50 if r.v50 else np.nan,
             "dist_ma20": r.Close / r.ma20 - 1, "dist_ma50": r.Close / r.ma50 - 1, "dist_ma200": r.Close / r.ma200 - 1,
             "ma50_slope": r.ma50 / d["ma50"].iat[s - 10] - 1, "ma200_slope": r.ma200 / d["ma200"].iat[s - 20] - 1,
             "ret5": r.Close / c.iat[s - 5] - 1, "ret20": r.Close / c.iat[s - 20] - 1,
             "ret60": r.Close / c.iat[s - 60] - 1, "ret126": r.Close / c.iat[s - 126] - 1,
             "dist_52wh": r.Close / r.hi252 - 1, "rsi14": r.rsi, "up_days10": r.up10,
             "weekday": t.entry_date.weekday(), "month": t.entry_date.month,
             "family": dp.FAMILY.get(t.pattern, "multi")}
        sd = d.index[s]
        if sd in spy.index:
            q = spy.loc[sd]
            f.update(spy_dist_ma50=q.Close / q.ma50 - 1, spy_dist_ma200=q.Close / q.ma200 - 1,
                     spy_ret20=q.Close / spy["Close"].shift(20).loc[sd] - 1, spy_rsi14=q.rsi)
            f["rs126"] = f["ret126"] - spy_r126.loc[sd]
        if t.symbol not in betas:
            betas[t.symbol] = beta_series(d["Close"], spy["Close"])
        bd, bw = betas[t.symbol]
        if sd in bd.index:
            f.update(beta_1y=bd.at[sd, "beta_1y"], corr_spy_1y=bd.at[sd, "corr_spy_1y"])
        f["beta_2y_w"] = bw.asof(sd) if len(bw.dropna()) else np.nan
        f["beta_adj"] = 0.67 * f["beta_2y_w"] + 0.33 if pd.notna(f["beta_2y_w"]) else np.nan
        # the first days of the trade (only while it was still open at that day's close)
        n_open = int(np.searchsorted(d.index.values, np.datetime64(t.exit_date), side="right")) - i
        for k in DAYS:
            if k <= n_open and i + k - 1 < len(d):
                cl = d["Close"].iat[i + k - 1]
                f[f"r_d{k}"] = (cl - t.entry) / risk
                if k in (1, 2, 3, 5):
                    f[f"below_brk_d{k}"] = int(cl < r.Close)
                    f[f"below_entry_d{k}"] = int(cl < t.entry)
        for k in (3, 5, 10):
            j = min(i + k, i + max(n_open, 1), len(d))
            f[f"mfe_d{k}"] = (d["High"].iloc[i:j].max() - t.entry) / risk
            f[f"mae_d{k}"] = (d["Low"].iloc[i:j].min() - t.entry) / risk
            f[f"open_at_d{k}"] = int(n_open >= k)
        rows.append({**t.to_dict(), **f, "win": int(t.pnl > 0)})
    out = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    out.to_csv(OUT, index=False)
    print(f"Saved {len(out)} trades with {len(out.columns)} columns to {OUT}")
    num = out.select_dtypes("number").drop(columns=["win", "pnl", "R", "exit", "entry", "stop", "days",
                                                    "leverage"], errors="ignore")
    corr = num.rank().corrwith(out.R.rank()).sort_values()       # Spearman without scipy
    print("Spearman correlation with R:\n" + corr.round(3).to_string())
    beta_report(out)


if __name__ == "__main__":
    main()
