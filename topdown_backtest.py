"""
Top-down (multi-timeframe) backtest on the S&P 100. Standalone: not part of the bot.
Longs only.

  daily    bias: yesterday's close > 50-day SMA and 50-day SMA > 200-day SMA
  1-hour   setup ("armed"): the last 3 confirmed 1h swing highs are each lower than the one
           before (a pullback), the pullback has dipped to within 1% of the daily 20-EMA,
           the last 1h close is still above the daily 50-day SMA, and the first of the 3
           swing highs is no more than 60 hourly bars old. Trendline = line through the
           last two lower highs.
  trigger  on the 5-min (or 15-min) chart: a bar CLOSES above the 1h trendline AND above the
           last confirmed 5-min (15-min) swing high = structure shift. One trade per trendline.
  entry    next bar's open
  stop     pullback low (lowest low since the first lower high) minus 0.1%
           skip if the stop is < 0.3% or > 8% away
  target   2R, 3R, or the 20-day high (skipped if it's < 1.5R away)
  time     exit at the close of the 10th trading day if neither is hit
  money    1% of equity risked per trade, max 20% of equity per stock, max 5 open positions
           (never more than 100% invested, no leverage). 0.02% slippage per side.

Swing high = higher than 2 bars on each side; it's only "known" 2 bars after it formed.
Benchmarks: SPY and OEF (S&P 100 ETF) buy-and-hold over the same days.
SPY and QQQ are also run through the same rules on their own (R stats only) for comparison.
Caveat: today's S&P 100 list is used for the whole history (survivorship bias flatters longs).

Runs on GitHub Actions (.github/workflows/topdown-backtest.yml) or locally:
    ALPACA_KEY_ID=... ALPACA_SECRET_KEY=... YEARS=8 python topdown_backtest.py
Results go to backtest-results/ (summary.txt, trades.csv, equity.csv).
"""
import datetime as dt
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

import scanner

ET = ZoneInfo("America/New_York")
BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
CACHE_DIR = os.environ.get("CACHE_DIR", "price-cache-topdown")
OUT_DIR = os.environ.get("OUT_DIR", "backtest-results")
YEARS = int(os.environ.get("YEARS", "8"))
OOS_DAYS = 365
EXTRA = ["SPY", "QQQ", "OEF"]           # benchmarks / index comparison

# ------------------------------------------------------------------ rules
SWING_N = 2
LOWER_HIGHS = 3
SETUP_MAX_AGE = 60                      # hourly bars from the first lower high
ZONE = 0.01                             # pullback must reach within 1% of the daily 20-EMA
STOP_BUF = 0.001
MIN_STOP, MAX_STOP = 0.003, 0.08
MIN_SWING_R = 1.5
TIME_STOP_DAYS = 10
RISK = 0.01
MAX_WEIGHT = 0.20
MAX_POS = 5
SLIP = 0.0002
START_EQUITY = 100_000.0
TRIGGERS = ["5m", "15m"]
TARGETS = ["2R", "3R", "swing"]


# ------------------------------------------------------------------ data
def _headers():
    key, secret = os.environ.get("ALPACA_KEY_ID"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        sys.exit("ALPACA_KEY_ID / ALPACA_SECRET_KEY are not set (add them as repository secrets).")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def _get(params, headers):
    out = {}
    while True:
        r = None
        for _ in range(10):
            try:
                r = requests.get(BARS_URL, headers=headers, params=params, timeout=60)
            except requests.RequestException:
                time.sleep(5)
                continue
            if r.status_code == 429:
                time.sleep(15)
                continue
            break
        if r is None or not r.ok:
            raise RuntimeError(f"{params['symbols']}: {r.status_code if r is not None else 'no connection'} "
                               f"{r.text[:200] if r is not None else ''}")
        js = r.json()
        for s, b in (js.get("bars") or {}).items():
            out.setdefault(s, []).extend(b)
        if not js.get("next_page_token"):
            return out
        params["page_token"] = js["next_page_token"]


def _frame(bars):
    df = pd.DataFrame(bars)
    df.index = pd.DatetimeIndex(pd.to_datetime(df["t"], utc=True)).tz_convert(ET)
    df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close"})
    return df[["Open", "High", "Low", "Close"]].astype(float)


def _fmt(ts):
    return pd.Timestamp(ts).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(sym, tf, start, end, headers):
    """One symbol, one timeframe, downloaded in yearly pieces."""
    edges = list(pd.date_range(start, end, freq="365D")) + [end]
    parts = [(a, b) for a, b in zip(edges[:-1], edges[1:]) if b > a]
    frames = []
    for a, b in parts:
        params = {"symbols": sym.replace("-", "."), "timeframe": tf, "start": _fmt(a), "end": _fmt(b),
                  "limit": 10000, "adjustment": "all", "feed": "sip"}
        bars = _get(params, headers).get(sym.replace("-", "."), [])
        if bars:
            frames.append(_frame(bars))
    if not frames:
        return None
    df = pd.concat(frames)
    df = df[~df.index.duplicated()].sort_index()
    if tf == "5Min":
        m = df.index.hour * 60 + df.index.minute
        df = df[(m >= 570) & (m < 960)]
    else:
        df.index = pd.DatetimeIndex(df.index.date)
    return df


def load_all(syms, start, end):
    os.makedirs(CACHE_DIR, exist_ok=True)
    headers = _headers()

    def one(sym):
        out = {}
        for tf, tag, s0 in (("5Min", "5m", start), ("1Day", "1d", start - pd.Timedelta(days=420))):
            path = os.path.join(CACHE_DIR, f"{sym}_{tag}.pkl.gz")
            df = None
            try:
                old = pd.read_pickle(path)
                if tag == "5m":
                    first, frm = old.index[0], old.index[-1] + pd.Timedelta(minutes=5)
                else:
                    first = pd.Timestamp(old.index[0]).tz_localize(ET)
                    frm = pd.Timestamp(old.index[-1]).tz_localize(ET) + pd.Timedelta(days=1)
                if first <= s0 + pd.Timedelta(days=10):
                    new = fetch(sym, tf, frm, end, headers) if frm < end else None
                    df = old if new is None else pd.concat([old, new])
                    df = df[~df.index.duplicated(keep="last")].sort_index()
            except Exception:
                pass
            if df is None:
                df = fetch(sym, tf, s0, end, headers)
            if df is not None:
                df.to_pickle(path)
            out[tag] = df
        return sym, out

    data = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=6) as pool:
        for i, (sym, d) in enumerate(pool.map(one, syms), 1):
            if d["5m"] is not None and d["1d"] is not None:
                data[sym] = d
            if i % 10 == 0:
                print(f"  data: {i}/{len(syms)} symbols ({time.time() - t0:.0f}s)", flush=True)
    return data


# ------------------------------------------------------------------ signals
def swing_highs(h):
    n = len(h)
    sw = np.zeros(n, dtype=bool)
    if n < 2 * SWING_N + 1:
        return sw
    mid = h[SWING_N:n - SWING_N]
    ok = np.ones(len(mid), dtype=bool)
    for k in range(1, SWING_N + 1):
        ok &= mid > h[SWING_N - k:n - SWING_N - k]
        ok &= mid > h[SWING_N + k:n - SWING_N + k]
    sw[SWING_N:n - SWING_N] = ok
    return sw


def resample(df5, minutes):
    """Session-anchored bars (first bar starts 9:30). Index = bar start."""
    if minutes == 5:
        return df5
    mins = df5.index.hour * 60 + df5.index.minute
    start = df5.index.normalize() + pd.to_timedelta(570 + ((mins - 570) // minutes) * minutes, unit="m")
    g = df5.groupby(start)
    return pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(),
                         "Low": g["Low"].min(), "Close": g["Close"].last()}).sort_index()


def daily_context(d1):
    c = d1["Close"]
    ctx = pd.DataFrame({
        "sma50": c.rolling(50).mean(), "sma200": c.rolling(200).mean(),
        "ema20": c.ewm(span=20, adjust=False).mean(), "hi20": d1["High"].rolling(20).max(),
        "close": c})
    ctx["bias"] = (ctx["close"] > ctx["sma50"]) & (ctx["sma50"] > ctx["sma200"])
    return ctx.shift(1)                 # day d only knows yesterday's completed values


def signals(sym, df5, d1, trig):
    """Entries for one symbol and one trigger timeframe (target-independent).
    Returns (list of signals, trigger-timeframe bars)."""
    ctx = daily_context(d1)
    h1 = resample(df5, 60)
    lt_min = 5 if trig == "5m" else 15
    lt = resample(df5, lt_min)
    if len(h1) < 300:
        return [], lt

    H, L, C = h1["High"].to_numpy(), h1["Low"].to_numpy(), h1["Close"].to_numpy()
    close16 = (h1.index.normalize() + pd.Timedelta(hours=16)).asi8
    h_end = np.minimum(h1.index.asi8 + 60 * 60 * 10**9, close16)
    sh = swing_highs(H)
    cx = ctx.reindex(pd.DatetimeIndex(h1.index.date))
    bias = cx["bias"].fillna(False).to_numpy(bool)
    ema20, sma50 = cx["ema20"].to_numpy(), cx["sma50"].to_numpy()

    lH, lL, lC, lO = (lt[c].to_numpy() for c in ("High", "Low", "Close", "Open"))
    l_start = lt.index.asi8
    l_days = pd.DatetimeIndex(lt.index.date)
    l_bias = ctx["bias"].reindex(l_days).fillna(False).to_numpy(bool)
    l_sw = swing_highs(lH)

    # last confirmed trigger-timeframe swing high, as known at the close of each bar
    last_lsh = np.full(len(lH), np.nan)
    cur = np.nan
    for i in range(len(lH)):
        j = i - SWING_N
        if j >= 0 and l_sw[j]:
            cur = lH[j]
        last_lsh[i] = cur

    out, confirmed, used = [], [], set()
    for k in range(len(H) - 1):
        j = k - SWING_N
        if j >= 0 and sh[j]:
            confirmed.append((j, H[j]))
        if len(confirmed) < LOWER_HIGHS or not bias[k]:
            continue
        last = confirmed[-LOWER_HIGHS:]
        if not all(last[i][1] > last[i + 1][1] for i in range(LOWER_HIGHS - 1)):
            continue
        i1, key = last[0][0], last[-1][0]
        if k - i1 > SETUP_MAX_AGE or key in used:
            continue
        if not (C[k] > sma50[k]):
            continue
        pb_low = L[i1:k + 1].min()
        if not (pb_low <= ema20[k] * (1 + ZONE)):
            continue
        (x2, y2), (x3, y3) = last[-2], last[-1]
        tl = y3 + (y3 - y2) / (x3 - x2) * (k + 1 - x3)     # trendline during the next hourly bar

        a = np.searchsorted(l_start, h_end[k], side="left")
        b = np.searchsorted(l_start, h_end[k + 1], side="left")
        for i in range(a, min(b, len(lO) - 1)):
            if not l_bias[i]:
                continue
            if lC[i] > tl and lC[i] > last_lsh[i]:          # NaN compares False
                used.add(key)
                entry = lO[i + 1] * (1 + SLIP)
                stop = min(pb_low, lL[a:i + 1].min()) * (1 - STOP_BUF)
                risk = (entry - stop) / entry
                if MIN_STOP <= risk <= MAX_STOP:
                    hi20 = ctx["hi20"].get(l_days[i + 1], np.nan)
                    out.append(dict(pos=i + 1, entry=entry, stop=stop, hi20=hi20))
                break
    return out, lt


def simulate_exit(sig, lt, target_kind, day_idx):
    """Walk 5/15-min bars from entry; returns exit dict or None if the trade is skipped."""
    O, H, L, C = (lt[c].to_numpy() for c in ("Open", "High", "Low", "Close"))
    p, entry, stop = sig["pos"], sig["entry"], sig["stop"]
    R = entry - stop
    if target_kind == "2R":
        tgt = entry + 2 * R
    elif target_kind == "3R":
        tgt = entry + 3 * R
    else:
        tgt = sig["hi20"]
        if not np.isfinite(tgt) or tgt < entry + MIN_SWING_R * R:
            return None
    d0 = day_idx[p]
    last_day = d0 + TIME_STOP_DAYS - 1
    n = len(O)
    end = np.searchsorted(day_idx, last_day, side="right")
    end = min(end, n)
    if end <= p:
        return None
    lo, hi = L[p:end], H[p:end]
    hit_s = np.argmax(lo <= stop) if (lo <= stop).any() else None
    hit_t = np.argmax(hi >= tgt) if (hi >= tgt).any() else None
    if hit_s is not None and (hit_t is None or hit_s <= hit_t):
        i = p + hit_s
        px = min(stop, O[i]) * (1 - SLIP)
        reason = "stop"
    elif hit_t is not None:
        i = p + hit_t
        px = max(tgt, O[i])
        reason = "target"
    else:
        i = end - 1
        px = C[i] * (1 - SLIP)
        reason = "time"
    return dict(exit_pos=i, exit=px, reason=reason, target=tgt, R=(px - entry) / R)


# ------------------------------------------------------------------ portfolio
def portfolio(trades, closes, days):
    """trades: candidate trades (all symbols). Taken in time order: max MAX_POS open, one per
    symbol, 1% risk sized on realized equity, max 20% weight. Returns (taken, daily equity)."""
    trades = trades.sort_values(["entry_time", "sym"]).reset_index(drop=True)
    realized, open_pos, taken = START_EQUITY, [], []
    for t in trades.itertuples(index=False):
        keep = []
        for o in open_pos:
            if o[1] <= t.entry_time:
                realized += o[2]
            else:
                keep.append(o)
        open_pos = keep
        if len(open_pos) >= MAX_POS or any(o[0] == t.sym for o in open_pos):
            continue
        sh = np.floor(min(RISK * realized / (t.entry - t.stop), MAX_WEIGHT * realized / t.entry))
        if sh <= 0:
            continue
        pnl = sh * (t.exit - t.entry)
        open_pos.append((t.sym, t.exit_time, pnl))
        taken.append(dict(t._asdict(), shares=sh, pnl=pnl))
    taken = pd.DataFrame(taken)
    dn = np.array(days, dtype="datetime64[D]")
    eq = np.full(len(dn), START_EQUITY)
    cache = {}
    for t in taken.itertuples(index=False):
        if t.sym not in cache:
            c = closes[t.sym]
            cache[t.sym] = pd.Series(c.to_numpy(), index=pd.DatetimeIndex(c.index)).reindex(
                pd.DatetimeIndex(dn)).ffill().to_numpy()
        a = np.searchsorted(dn, np.datetime64(t.entry_time.date()), side="left")
        b = np.searchsorted(dn, np.datetime64(t.exit_time.date()), side="left")
        if b > a:
            eq[a:b] += (cache[t.sym][a:b] - t.entry) * t.shares
        eq[b:] += t.pnl
    return taken, pd.Series(eq, index=pd.DatetimeIndex(dn))


def stats_eq(eq):
    e = eq.to_numpy()
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1 / 365)
    cagr = (e[-1] / e[0]) ** (1 / yrs) - 1 if e[-1] > 0 else -1
    dd = float(((np.maximum.accumulate(e) - e) / np.maximum.accumulate(e)).max())
    r = np.diff(e) / e[:-1]
    sharpe = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else np.nan
    return dict(ret=e[-1] / e[0] - 1, cagr=cagr, dd=dd, sharpe=sharpe)


def stats_r(t):
    if not len(t):
        return dict(n=0, win=np.nan, avgR=np.nan, pf=np.nan, totR=0.0)
    R = t["R"].to_numpy()
    g, l = R[R > 0].sum(), -R[R < 0].sum()
    return dict(n=len(R), win=(R > 0).mean(), avgR=R.mean(), pf=g / l if l > 0 else np.nan, totR=R.sum())


def fmt(x, k):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "-"
    return {"p": f"{x:+.1%}", "p0": f"{x:.0%}", "r": f"{x:+.2f}", "f": f"{x:.2f}", "n": f"{x:,}"}[k]


# ------------------------------------------------------------------ main
def main():
    end = pd.Timestamp.now(tz=ET).normalize() - pd.Timedelta(days=1)
    start = end - pd.Timedelta(days=int(365.25 * YEARS))
    universe = scanner.get_tickers()
    syms = universe + [s for s in EXTRA if s not in universe]
    print(f"{len(universe)} S&P 100 symbols + {EXTRA}; {start.date()} to {end.date()}", flush=True)
    data = load_all(syms, start, end)
    print(f"Loaded {len(data)} symbols", flush=True)

    split = (end - pd.Timedelta(days=OOS_DAYS)).date()
    spy_days = [d.date() for d in data["SPY"]["1d"].index if d.date() >= start.date()]
    closes = {s: pd.Series(d["1d"]["Close"].to_numpy(), index=[x.date() for x in d["1d"].index])
              for s, d in data.items()}

    cands = {(tr, tg): [] for tr in TRIGGERS for tg in TARGETS}
    t0 = time.time()
    for n, (sym, d) in enumerate(data.items(), 1):
        if sym == "OEF":
            continue
        for trig in TRIGGERS:
            sigs, lt = signals(sym, d["5m"], d["1d"], trig)
            if not sigs:
                continue
            day_idx = pd.factorize(pd.Index(lt.index.date))[0]
            times = lt.index
            for tg in TARGETS:
                busy = -1
                for s in sigs:
                    if s["pos"] <= busy:
                        continue
                    ex = simulate_exit(s, lt, tg, day_idx)
                    if ex is None:
                        continue
                    busy = ex["exit_pos"]
                    cands[(trig, tg)].append(dict(
                        sym=sym, entry_time=times[s["pos"]], exit_time=times[ex["exit_pos"]] + pd.Timedelta(minutes=5 if trig == "5m" else 15),
                        entry=s["entry"], stop=s["stop"], target=ex["target"], exit=ex["exit"],
                        reason=ex["reason"], R=ex["R"]))
        if n % 10 == 0:
            print(f"  signals: {n}/{len(data)} ({time.time() - t0:.0f}s)", flush=True)

    os.makedirs(OUT_DIR, exist_ok=True)
    days = spy_days
    lines = ["TOP-DOWN MULTI-TIMEFRAME BACKTEST - S&P 100, longs only",
             f"Data {days[0]} to {days[-1]} (Alpaca, all exchanges, split+dividend adjusted).",
             f"In-sample to {split}; out-of-sample = last 12 months.",
             "Daily bias (close>50SMA>200SMA) -> 1h pullback (3 lower highs, near daily 20-EMA) ->",
             "LTF trigger (close above 1h trendline + last LTF swing high). Entry next bar open.",
             f"1% risk/trade, max 20% per stock, max {MAX_POS} positions, no leverage, 0.02% slippage/side,",
             f"time stop {TIME_STOP_DAYS} trading days. Today's S&P 100 list used throughout (survivorship bias).",
             ""]

    def bench(sym, d0, d1):
        c = closes[sym]
        c = c[(c.index >= d0) & (c.index <= d1)]
        return stats_eq(c / c.iloc[0] * START_EQUITY)

    all_trades, all_eq = [], {}
    for per_name, d0, d1 in (("FULL PERIOD", days[0], days[-1]),
                             ("IN-SAMPLE", days[0], split),
                             ("OUT-OF-SAMPLE (last 12 months)", next(d for d in days if d > split), days[-1])):
        pdays = [d for d in days if d0 <= d <= d1]
        lines.append(f"=== {per_name}: {pdays[0]} to {pdays[-1]} ===")
        lines.append(f"{'variant':<16}{'trades':>7}{'win%':>6}{'avgR':>7}{'PF':>6}{'return':>9}{'CAGR':>8}"
                     f"{'maxDD':>7}{'Sharpe':>7}")
        for (trig, tg), rows in cands.items():
            df = pd.DataFrame(rows)
            if len(df):
                df = df[(df["entry_time"].dt.date >= d0) & (df["entry_time"].dt.date <= d1)]
                df = df[~df["sym"].isin(["SPY", "QQQ"])]
            taken, eq = portfolio(df, closes, pdays) if len(df) else (pd.DataFrame(), pd.Series(START_EQUITY, index=pdays))
            sr, se = stats_r(taken), stats_eq(eq)
            name = f"{trig} / {tg}"
            lines.append(f"{name:<16}{fmt(sr['n'], 'n'):>7}{fmt(sr['win'], 'p0'):>6}{fmt(sr['avgR'], 'r'):>7}"
                         f"{fmt(sr['pf'], 'f'):>6}{fmt(se['ret'], 'p'):>9}{fmt(se['cagr'], 'p'):>8}"
                         f"{fmt(se['dd'], 'p0'):>7}{fmt(se['sharpe'], 'f'):>7}")
            if per_name == "FULL PERIOD" and len(taken):
                all_trades.append(taken.assign(variant=name))
                all_eq[name] = eq
        for b in ("SPY", "OEF"):
            se = bench(b, pdays[0], pdays[-1])
            lines.append(f"{b + ' buy&hold':<16}{'':>7}{'':>6}{'':>7}{'':>6}{fmt(se['ret'], 'p'):>9}"
                         f"{fmt(se['cagr'], 'p'):>8}{fmt(se['dd'], 'p0'):>7}{fmt(se['sharpe'], 'f'):>7}")
        lines.append("")

    # yearly returns, full period
    if all_eq:
        eqdf = pd.DataFrame(all_eq)
        eqdf.index = pd.DatetimeIndex(eqdf.index)
        for b in ("SPY", "OEF"):
            c = closes[b].copy()
            c.index = pd.DatetimeIndex(c.index)
            eqdf[b + " B&H"] = c.reindex(eqdf.index).ffill()
        yr = eqdf.groupby(eqdf.index.year).agg(lambda s: s.iloc[-1] / s.iloc[0] - 1)
        lines.append("=== CALENDAR-YEAR RETURNS (partial first/last year) ===")
        lines.append(f"{'year':<6}" + "".join(f"{c:>12}" for c in yr.columns))
        for y, row in yr.iterrows():
            lines.append(f"{y:<6}" + "".join(f"{fmt(v, 'p'):>12}" for v in row))
        lines.append("")
        eqdf.to_csv(os.path.join(OUT_DIR, "equity.csv"))

    # exits breakdown + index-only R stats
    lines.append("=== EXIT REASONS (full period, trades actually taken) ===")
    for t in all_trades:
        v = t["variant"].iloc[0]
        rc = t["reason"].value_counts(normalize=True)
        lines.append(f"{v:<16}" + "  ".join(f"{k} {rc.get(k, 0):.0%}" for k in ("target", "stop", "time")))
    lines.append("")
    lines.append("=== SPY / QQQ ALONE (every signal, no portfolio limits; R per trade) ===")
    lines.append(f"{'variant':<22}{'trades':>7}{'win%':>6}{'avgR':>7}{'PF':>6}{'totalR':>8}")
    for (trig, tg), rows in cands.items():
        df = pd.DataFrame(rows)
        for s in ("SPY", "QQQ"):
            sub = df[df["sym"] == s] if len(df) else pd.DataFrame(columns=["R"])
            sr = stats_r(sub)
            lines.append(f"{s + ' ' + trig + ' / ' + tg:<22}{fmt(sr['n'], 'n'):>7}{fmt(sr['win'], 'p0'):>6}"
                         f"{fmt(sr['avgR'], 'r'):>7}{fmt(sr['pf'], 'f'):>6}{sr['totR']:>+8.1f}")

    txt = "\n".join(lines)
    print("\n" + txt, flush=True)
    with open(os.path.join(OUT_DIR, "summary.txt"), "w") as f:
        f.write(txt + "\n")
    if all_trades:
        pd.concat(all_trades).to_csv(os.path.join(OUT_DIR, "trades.csv"), index=False)


if __name__ == "__main__":
    main()
