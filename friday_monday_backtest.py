"""
Friday High -> Monday Short: backtest on the S&P 100, 5-minute bars (Alpaca, all exchanges),
2021 -> today. Standalone: not part of the bot.

THE RULES (every assumption the video leaves open is written here, nothing else is tuned)
  week       "Friday" = last trading day of a week, "Monday" = first trading day of the next
             week (so Good Friday / Memorial Day weeks still count). Regular hours only (9:30-16:00).
  level      FH = Friday's regular-session high. ATR = 14-day ATR at Friday's close.
  sweep      a Monday bar trades at least $0.01 above FH (a gap open above FH counts).
  entries    (one trade per stock per Monday, the first signal only; no entries in bars that
             start at 15:00 or later; every trade is closed at Monday's 16:00 close at the latest)
    A        sell limit at FH, filled when price trades $0.01 through it (or at the open if
             Monday opens above FH). The sweep high isn't known yet, so stop = entry + 0.25 ATR.
    B        after the sweep, the first bar that CLOSES back below FH; short at the next bar's
             open. Stop = highest high since the sweep + $0.02.
    C        after the sweep, the first bar that closes below the last confirmed swing low
             (a low below the 2 bars on each side, Friday's bars included) that formed before
             the sweep high - and below FH. Short at the next bar's open. Stop = sweep high + $0.02.
    ctrl     CONTROL, not a strategy: short every Monday at the open, stop = open + 0.25 ATR.
             It tells you how much of any result is just "shorting on Mondays".
  min stop   any stop closer than 0.1 ATR is widened to 0.1 ATR (otherwise a few-cent stop on
             a $300 stock is pure noise and costs eat it).
  target     entry - RR x risk, RR = 1, 1.5, 2, 3. Limit order: fills when price trades $0.01
             through it. Stop and target in the same bar = stop (worst case). For A, the
             target can't fill in the entry bar (the low may have come before the fill).
  costs      $0.005/share commission each side; 0.3 bp regulatory fee on the short sale;
             2 bp (half spread + slippage) on market entries (B, C, ctrl) and stop exits;
             1 bp on the 16:00 exit; stop gaps fill at the worse open. Limit fills: no slippage.
  money      1% of equity risked per trade, position capped at 4x equity (intraday buying power),
             and MAX ONE TRADE PER MONDAY for the whole account: the earliest signal of the day
             (ties broken by a fixed pseudo-random order). R statistics use every stock's signal.
  timeframes 5, 15 and 60 minutes (60-min bars start at 9:30). Same rules on each.
  samples    in-sample 2021-01-01 to 2024-12-31; out-of-sample 2025-01-01 to today. The best
             variant is picked on in-sample only, then checked out-of-sample.
  universe   today's S&P 100 (survivorship bias: stocks that dropped out since 2021 are missing).

Runs on GitHub Actions (.github/workflows/friday-monday-backtest.yml) or locally:
    ALPACA_KEY_ID=... ALPACA_SECRET_KEY=... python friday_monday_backtest.py
    FAKE=1 python friday_monday_backtest.py      # random-walk test data: no edge should appear
Results go to backtest-results/ (summary.txt, trades.csv, portfolio.csv).
"""
import datetime as dt
import os
import sys
import time
import zlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

ET = ZoneInfo("America/New_York")
SP100 = """AAPL ABBV ABT ACN ADBE AMAT AMD AMGN AMT AMZN ANET AVGO AXP BA BAC BKNG BLK BMY BNY BRK.B
C CAT CMCSA COF COP COST CRM CSCO CVS CVX DE DELL DHR DIS DUK EMR FDX GD GE GEV GILD GM GOOGL GS HD
IBM INTC INTU ISRG JNJ JPM KO LIN LLY LMT LOW LRCX MA MCD MDLZ MDT META MMM MO MRK MS MSFT MU NEE
NFLX NOW NVDA ORCL PANW PEP PFE PG PLTR PM QCOM RTX SBUX SCHW SNDK SO T TMO TMUS TSLA TXN UBER UNH
UNP UPS USB V VZ WFC WMT XOM""".split()
SP100 = [x.replace(".", "-") for x in SP100]          # BRK.B -> BRK-B (the name the downloader returns)
SYMBOLS = os.environ.get("SYMBOLS", "").split() or SP100
START = pd.Timestamp(os.environ.get("START", "2021-01-01")).date()
OOS_START = pd.Timestamp(os.environ.get("OOS_START", "2025-01-01")).date()
TIMEFRAMES = [int(x) for x in os.environ.get("TIMEFRAMES", "5 15 60").split()]
ENTRIES = ["A", "B", "C", "ctrl"]
RRS = [1.0, 1.5, 2.0, 3.0]
FAKE = os.environ.get("FAKE") == "1"
CACHE_DIR = os.environ.get("CACHE_DIR", "price-cache-fhm")
OUT_DIR = os.environ.get("OUT_DIR", "backtest-results")
BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"

# ------------------------------------------------------------------ rules
RTH_START, RTH_END = 570, 960      # minutes after midnight ET
ENTRY_CUTOFF = 900                 # no entries in bars starting 15:00 or later
TICK = 0.01
EPS = 1e-9
STOP_BUFFER = 0.02
A_STOP_ATR = 0.25                  # stop distance for A and the control
MIN_RISK_ATR = 0.10                # stops closer than this are widened to it
SWING_N = 2
ATR_N = 14
TREND_MA = 20                      # downtrend filters, measured at Friday's close
TREND_SLOPE = 5                    # "falling" = lower than 5 trading days earlier
FILTERS = ["none", "below_ma", "ma_down", "down_week", "both_ma"]
RACE_ATR = 0.5                     # reversal question: which comes first, FH -/+ 0.5 ATR

COMMISSION = 0.005                 # $ per share per side
MKT_BPS = 2.0                      # half spread + slippage, market entries and stop exits
CLOSE_BPS = 1.0                    # 16:00 exit
SELL_FEE_BPS = 0.3                 # regulatory fees on the short sale
RISK = 0.01
MAX_NOTIONAL = 4.0
START_EQUITY = 100_000.0
MIN_TRADES_TO_PICK = 200           # a variant needs this many in-sample trades to be picked


# ------------------------------------------------------------------ data
def _headers():
    key, secret = os.environ.get("ALPACA_KEY_ID"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        sys.exit("ALPACA_KEY_ID / ALPACA_SECRET_KEY are not set (add them as repository secrets).")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def _utc(ts):
    return pd.Timestamp(ts).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def _download(syms, start, end, headers):
    """Raw 5-minute bars for a group of symbols (split-adjusted only, so real cent prices)."""
    params = {"symbols": ",".join(s.replace("-", ".") for s in syms), "timeframe": "5Min",
              "start": _utc(start), "end": _utc(end), "limit": 10000, "adjustment": "split",
              "feed": "sip", "sort": "asc"}
    out = defaultdict(list)
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
            raise RuntimeError(f"{syms[0]}..: {r.status_code if r is not None else 'no connection'} "
                               f"{r.text[:200] if r is not None else ''}")
        js = r.json()
        for s, bars in (js.get("bars") or {}).items():
            out[s.replace(".", "-")].extend(bars)
        if not js.get("next_page_token"):
            return out
        params["page_token"] = js["next_page_token"]


def _frame(bars):
    if not bars:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close"], dtype=float)
    df = pd.DataFrame(bars)
    df.index = pd.DatetimeIndex(pd.to_datetime(df["t"], utc=True)).tz_convert(ET)
    df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close"})
    df = df[["Open", "High", "Low", "Close"]].astype(float)
    m = df.index.hour * 60 + df.index.minute
    return df[(m >= RTH_START) & (m < RTH_END)]


def fetch(syms, start, end, headers):
    """{symbol: bars}, downloaded 10 symbols x 6 months at a time, 4 requests in parallel."""
    edges = list(pd.date_range(start, end, freq="180D")) + [end]
    jobs = [(syms[i:i + 10], a, b) for i in range(0, len(syms), 10)
            for a, b in zip(edges[:-1], edges[1:]) if b > a]
    with ThreadPoolExecutor(max_workers=4) as pool:
        parts = list(pool.map(lambda j: _download(j[0], j[1], j[2], headers), jobs))
    merged = defaultdict(list)
    for p in parts:
        for s, bars in p.items():
            merged[s].extend(bars)
    out = {}
    for s in syms:
        df = _frame(merged.get(s, []))
        out[s] = df[~df.index.duplicated()].sort_index()
    return out


def load_all(start, end):
    """Bars from the saved copies, downloading only what's missing."""
    headers = _headers()
    os.makedirs(CACHE_DIR, exist_ok=True)
    data, full, update = {}, [], defaultdict(list)
    for s in SYMBOLS:
        try:
            old = pd.read_pickle(os.path.join(CACHE_DIR, f"{s}_5min.pkl.gz"))
        except Exception:
            old = None
        if old is not None and len(old) and old.index[0] <= start + pd.Timedelta(days=10):
            data[s] = old
            update[(old.index[-1] + pd.Timedelta(minutes=5)).floor("D")].append(s)
        else:
            full.append(s)
    t0 = time.time()
    if full:
        print(f"Downloading {len(full)} symbols from {start.date()}...", flush=True)
        data.update(fetch(full, start, end, headers))
    for frm, syms in update.items():
        if frm < end:
            new = fetch(syms, frm, end, headers)
            for s in syms:
                df = pd.concat([data[s], new[s]])
                data[s] = df[~df.index.duplicated(keep="last")].sort_index()
    print(f"Prices ready in {time.time() - t0:.0f}s", flush=True)
    for s, df in data.items():
        df.to_pickle(os.path.join(CACHE_DIR, f"{s}_5min.pkl.gz"))
    return {s: df[df.index >= start] for s, df in data.items() if len(df)}


SUB = int(os.environ.get("FAKE_STEPS", "60"))         # price steps per 5-min bar in the fake data


def fake_data(start, end, n=30, seed=7):
    """Random-walk 5-minute bars: a correct backtest finds no edge here (gross R ~ 0)."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(pd.Timestamp(start).date(), pd.Timestamp(end).date())
    mins = np.arange(RTH_START, RTH_END, 5)
    out = {}
    for k in range(n):
        p0, vol = rng.uniform(50, 500), rng.uniform(0.0008, 0.002)
        steps = rng.normal(0, vol / np.sqrt(SUB / 5), (len(days), len(mins) * SUB))  # sub-bar log returns
        steps[:, 0] += rng.normal(0, vol * 6, len(days))                 # overnight gap
        path = p0 * np.exp(np.cumsum(steps.ravel())).reshape(steps.shape)
        flat = path.ravel()
        before = np.r_[p0, flat[:-1]].reshape(-1, SUB)[:, :1].copy()     # price before each bar
        before[::len(mins), 0] = path[:, 0]          # the day's first bar opens AFTER the overnight gap
        seg = np.c_[before, flat.reshape(-1, SUB)]
        idx = (pd.DatetimeIndex(np.repeat(days.values, len(mins))).tz_localize(ET)
               + pd.to_timedelta(np.tile(mins, len(days)), unit="min"))
        out[f"FAKE{k:02d}"] = pd.DataFrame({"Open": seg[:, 0], "High": seg.max(1), "Low": seg.min(1),
                                            "Close": seg[:, -1]}, index=idx).round(2)
    return out


# ------------------------------------------------------------------ preparation
def split_days(df):
    """{date: (mins, o, h, l, c)} of 5-minute bars, and the daily table with ATR."""
    m = (df.index.hour * 60 + df.index.minute).to_numpy()
    d = np.array(df.index.date)
    o, h, l, c = (df[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    st = np.flatnonzero(np.r_[True, d[1:] != d[:-1]])
    en = np.r_[st[1:], len(d)]
    days = {d[a]: (m[a:b], o[a:b], h[a:b], l[a:b], c[a:b]) for a, b in zip(st, en)}
    daily = pd.DataFrame({"High": [h[a:b].max() for a, b in zip(st, en)],
                          "Low": [l[a:b].min() for a, b in zip(st, en)],
                          "Close": [c[b - 1] for b in en]}, index=[d[a] for a in st])
    pc = daily["Close"].shift()
    tr = pd.concat([daily["High"] - daily["Low"], (daily["High"] - pc).abs(),
                    (daily["Low"] - pc).abs()], axis=1).max(axis=1)
    daily["atr"] = tr.rolling(ATR_N).mean()               # known at that day's close
    ma = daily["Close"].rolling(TREND_MA).mean()
    daily["below_ma"] = daily["Close"] < ma                 # trend features, all known at the close
    daily["ma_down"] = ma < ma.shift(TREND_SLOPE)
    daily["down_week"] = daily["Close"] < daily["Close"].shift(TREND_SLOPE)
    return days, daily


def resample(bars, tf):
    m, o, h, l, c = bars
    if tf == 5:
        return bars
    b = (m - RTH_START) // tf
    st = np.flatnonzero(np.r_[True, b[1:] != b[:-1]])
    en = np.r_[st[1:], len(b)] - 1
    return (RTH_START + b[st] * tf, o[st], np.maximum.reduceat(h, st), np.minimum.reduceat(l, st), c[en])


def week_pairs(dates):
    """(friday, monday): last trading day of a week and first trading day of the next week."""
    dates = sorted(dates)
    pairs = []
    for a, b in zip(dates[:-1], dates[1:]):
        if a.isocalendar()[:2] != b.isocalendar()[:2] and (b - a).days <= 4:
            pairs.append((a, b))
    return pairs


def swing_lows(L):
    n = len(L)
    sw = np.zeros(n, dtype=bool)
    if n < 2 * SWING_N + 1:
        return sw
    mid = L[SWING_N:n - SWING_N]
    ok = np.ones(len(mid), dtype=bool)
    for k in range(1, SWING_N + 1):
        ok &= mid < L[SWING_N - k:n - SWING_N - k]
        ok &= mid < L[SWING_N + k:n - SWING_N + k]
    sw[SWING_N:n - SWING_N] = ok
    return sw


# ------------------------------------------------------------------ signals
def signals(fri, mon, FH, atr):
    """{entry: (entry bar, entry price, stop, kind)} for one stock-Monday on one timeframe.
    kind: 'limit' (A: filled inside the bar) or 'mkt' (at the bar's open)."""
    mt, mo, mh, ml, mc = mon
    n = len(mt)
    out = {"ctrl": (0, mo[0], mo[0] + A_STOP_ATR * atr, "mkt")}

    # A: first touch (trades $0.01 through FH)
    hit = np.flatnonzero(mh >= FH + TICK - EPS)
    if len(hit) and mt[hit[0]] < ENTRY_CUTOFF:
        i = hit[0]
        entry = mo[i] if mo[i] >= FH else FH
        out["A"] = (i, entry, entry + A_STOP_ATR * atr, "limit")

    if not len(hit):
        return out
    s0 = hit[0]

    # B: first close back below FH after the sweep
    hh = -np.inf
    for i in range(s0, n):
        hh = max(hh, mh[i])
        if mc[i] < FH - EPS:
            if i + 1 < n and mt[i + 1] < ENTRY_CUTOFF and mo[i + 1] < hh + STOP_BUFFER:
                out["B"] = (i + 1, mo[i + 1], hh + STOP_BUFFER, "mkt")
            break
        if mt[i] >= ENTRY_CUTOFF:
            break

    # C: close below the last confirmed swing low before the sweep high (Friday bars included)
    nf = len(fri[0])
    L = np.r_[fri[3], ml]
    sw = swing_lows(L)
    hh, hh_g = -np.inf, -1
    for i in range(s0, n):
        g = nf + i
        if mh[i] > hh + EPS:
            hh, hh_g = mh[i], g
        ref = None
        for k in range(hh_g - 1, -1, -1):
            if sw[k] and k + SWING_N <= g:          # swing confirmed by this bar's close
                ref = k
                break
        if ref is not None and mc[i] < L[ref] - EPS and mc[i] < FH - EPS:
            if i + 1 < n and mt[i + 1] < ENTRY_CUTOFF and mo[i + 1] < hh + STOP_BUFFER:
                out["C"] = (i + 1, mo[i + 1], hh + STOP_BUFFER, "mkt")
            break
        if mt[i] >= ENTRY_CUTOFF:
            break
    return out


def exit_trade(mon, e, entry, stop, kind, rr):
    """Walk forward from the entry bar. Returns (exit price, reason, exit bar)."""
    mt, mo, mh, ml, mc = mon
    risk = stop - entry
    target = entry - rr * risk
    H, L, O = mh[e:], ml[e:], mo[e:]
    s = np.flatnonzero(H >= stop - EPS)
    t = np.flatnonzero(L <= target - TICK + EPS)
    if kind == "limit":
        t = t[t > 0]                                   # the low may have come before the fill
    s = s[0] if len(s) else 10 ** 9
    t = t[0] if len(t) else 10 ** 9
    if s <= t and s < len(H):
        px = max(stop, O[s]) if (s > 0 or kind == "mkt") else stop
        return px, "stop", e + s
    if t < len(H):
        px = min(target, O[t]) if t > 0 else target
        return px, "target", e + t
    return mc[-1], "close", len(mc) - 1


def costs(entry, exit_px, kind, reason):
    c = 2 * COMMISSION + entry * SELL_FEE_BPS / 1e4
    if kind == "mkt":
        c += entry * MKT_BPS / 1e4
    if reason == "stop":
        c += exit_px * MKT_BPS / 1e4
    elif reason == "close":
        c += exit_px * CLOSE_BPS / 1e4
    return c


def _hhmm(m):
    return f"{int(m) // 60:02d}:{int(m) % 60:02d}"


# ------------------------------------------------------------------ run
def run(data):
    trades, pattern, control = [], [], []
    for sym, df in data.items():
        days, daily = split_days(df)
        dates = sorted(days)
        # --- pattern statistics (5-min): Friday high -> Monday, and prior-day high on other days
        firsts = {b for _, b in week_pairs(dates)}
        for a, b in zip(dates[:-1], dates[1:]):
            atr = daily.at[a, "atr"]
            if not atr > 0 or (b - a).days > 4:
                continue
            row = describe(days[b], daily.at[a, "High"], atr)
            row.update(sym=sym, date=b)
            (pattern if b in firsts else control).append(row)
        # --- trades
        for fri_d, mon_d in week_pairs(dates):
            if mon_d < START:
                continue
            atr, FH = daily.at[fri_d, "atr"], daily.at[fri_d, "High"]
            if not atr > 0:
                continue
            tr_f = (bool(daily.at[fri_d, "below_ma"]), bool(daily.at[fri_d, "ma_down"]),
                    bool(daily.at[fri_d, "down_week"]))
            for tf in TIMEFRAMES:
                fri, mon = resample(days[fri_d], tf), resample(days[mon_d], tf)
                for ent, (e, entry, stop, kind) in signals(fri, mon, FH, atr).items():
                    stop = max(stop, entry + MIN_RISK_ATR * atr)
                    risk = stop - entry
                    for rr in RRS:
                        px, why, xb = exit_trade(mon, e, entry, stop, kind, rr)
                        gross = entry - px
                        net = gross - costs(entry, px, kind, why)
                        trades.append((mon_d, sym, tf, ent, rr, _hhmm(mon[0][e]), round(entry, 4),
                                       round(stop, 4), round(entry - rr * risk, 4), round(px, 4), why,
                                       _hhmm(mon[0][xb]), risk, risk / atr, gross / risk, net / risk) + tr_f)
    cols = ["date", "sym", "tf", "entry_type", "rr", "entry_time", "entry", "stop", "target", "exit",
            "reason", "exit_time", "risk", "risk_atr", "R_gross", "R", "below_ma", "ma_down", "down_week"]
    t = pd.DataFrame(trades, columns=cols)
    t["none"] = True
    t["both_ma"] = t["below_ma"] & t["ma_down"]
    return t, pd.DataFrame(pattern), pd.DataFrame(control)


def describe(mon5, level, atr):
    """What Monday (or any day) does around the previous high."""
    mt, mo, mh, ml, mc = mon5
    hit = np.flatnonzero(mh >= level + TICK - EPS)
    r = dict(touch=bool(len(hit)), gap_above=bool(mo[0] >= level + TICK - EPS),
             close_below=np.nan, race="")
    if not len(hit):
        return r
    i = hit[0]
    r["close_below"] = bool(mc[-1] < level)
    up, dn = level + RACE_ATR * atr, level - RACE_ATR * atr
    if mh[i] >= up:
        r["race"] = "up"
        return r
    u = np.flatnonzero(mh[i + 1:] >= up)
    d = np.flatnonzero(ml[i + 1:] <= dn)
    u = u[0] if len(u) else 10 ** 9
    d = d[0] if len(d) else 10 ** 9
    r["race"] = "neither" if u == d == 10 ** 9 else ("same bar" if u == d else ("down" if d < u else "up"))
    return r


# ------------------------------------------------------------------ statistics
def r_stats(t):
    if not len(t):
        return dict(n=0, win=np.nan, avg=np.nan, gross=np.nan, pf=np.nan, tot=0.0, dd=0.0, tstat=np.nan, cost=np.nan)
    R = t["R"].to_numpy(float)
    g, l = R[R > 0].sum(), -R[R < 0].sum()
    cum = np.r_[0, np.cumsum(R[np.argsort(t["date"].to_numpy(), kind="stable")])]
    wk = t.groupby("date")["R"].mean()                 # one number per Monday: trades the same
    ts = wk.mean() / (wk.std(ddof=1) / np.sqrt(len(wk))) if len(wk) > 2 and wk.std() > 0 else np.nan
    return dict(n=len(R), win=(R > 0).mean(), avg=R.mean(), gross=t["R_gross"].mean(),
                pf=g / l if l > 0 else np.nan, tot=R.sum(), dd=(np.maximum.accumulate(cum) - cum).max(),
                tstat=ts, cost=(t["R_gross"] - t["R"]).mean())


def portfolio(t, p0, p1):
    """Literal rule: one trade per Monday for the whole account (the earliest signal)."""
    t = t[(t["date"] >= p0) & (t["date"] <= p1)].copy()
    t["tie"] = [zlib.crc32(f"{s}{d}".encode()) for s, d in zip(t["sym"], t["date"])]
    t = t.sort_values(["date", "entry_time", "tie"]).groupby("date").head(1)
    eq, peak, dd, rows, capped = START_EQUITY, START_EQUITY, 0.0, [], 0
    for r in t.itertuples():
        sh_risk = RISK * eq / r.risk
        sh_cap = MAX_NOTIONAL * eq / r.entry
        capped += sh_cap < sh_risk
        shares = np.floor(min(sh_risk, sh_cap))
        eq += shares * r.risk * r.R
        peak = max(peak, eq)
        dd = max(dd, 1 - eq / peak)
        rows.append((r.date, r.sym, r.R, eq))
    curve = pd.DataFrame(rows, columns=["date", "sym", "R", "equity"])
    yrs = max((pd.Timestamp(p1) - pd.Timestamp(p0)).days / 365.25, 0.1)
    ret = eq / START_EQUITY - 1
    cagr = (eq / START_EQUITY) ** (1 / yrs) - 1 if eq > 0 else -1.0
    return dict(n=len(t), ret=ret, cagr=cagr, dd=dd, capped=capped / max(len(t), 1)), curve


def yearly(curve):
    if not len(curve):
        return {}
    c = curve.copy()
    c["year"] = pd.to_datetime(c["date"]).dt.year
    out, prev = {}, START_EQUITY
    for y, g in c.groupby("year"):
        out[y] = g["equity"].iloc[-1] / prev - 1
        prev = g["equity"].iloc[-1]
    return out


def _f(x, k):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "-"
    return {"pct": f"{x:.0%}", "pct1": f"{x:+.1%}", "r": f"{x:+.3f}", "r1": f"{x:+.1f}",
            "pf": f"{x:.2f}", "n": f"{x:,}", "t": f"{x:+.1f}"}[k]


def vname(tf, ent, rr):
    return f"{tf}m {ent} 1:{rr:g}"


def report(t, pat, ctl, first, last):
    split_end = OOS_START - dt.timedelta(days=1)
    IS = (t["date"] < OOS_START)
    out = []
    w = out.append
    w("FRIDAY HIGH -> MONDAY SHORT - S&P 100" + (" (FAKE RANDOM-WALK DATA)" if FAKE else ", 5-minute bars (Alpaca SIP)"))
    w(f"Mondays {first} to {last}. In-sample to {split_end}, out-of-sample from {OOS_START}.")
    w("R = net of costs (commission, fees, spread, slippage). 'gross' = before costs.")
    w("t = t-statistic of the average R, with trades on the same Monday averaged first (they move together).")
    w("")

    # ---------- 1. the pattern itself
    w("=== 1. HOW OFTEN DOES MONDAY REACH FRIDAY'S HIGH, AND WHAT HAPPENS NEXT? (5-min, regular hours) ===")
    w(f"Race = after the first touch, which comes first: high - {RACE_ATR} ATR (down) or high + {RACE_ATR} ATR (up).")
    w("Control = the same thing for every other day vs the previous day's high (is Friday->Monday special?).")
    head = f"{'':<34}{'days':>8}{'touch':>8}{'gap above':>11}{'close below':>13}{'down 1st':>10}{'up 1st':>9}{'same bar':>10}{'neither':>9}"
    w(head)
    for label, d in [("Friday high -> Monday", pat), ("Prev-day high -> other days", ctl)]:
        for per, m in [("all", None), ("IS", True), ("OOS", False)]:
            x = d if m is None else d[(d["date"] < OOS_START) == m]
            x = x[x["date"] >= START]
            tc = x[x["touch"]]
            rc = tc["race"].value_counts(normalize=True)
            w(f"{label + ' ' + per:<34}{len(x):>8,}{_f(x['touch'].mean(), 'pct'):>8}{_f(x['gap_above'].mean(), 'pct'):>11}"
              f"{_f(tc['close_below'].astype(float).mean(), 'pct'):>13}{_f(rc.get('down', 0.0), 'pct'):>10}"
              f"{_f(rc.get('up', 0.0), 'pct'):>9}{_f(rc.get('same bar', 0.0), 'pct'):>10}{_f(rc.get('neither', 0.0), 'pct'):>9}")
    w("'close below' and the race columns are shares of the days that touched.")
    w("")

    # ---------- 2. every variant
    hd = (f"{'variant':<16}{'IS n':>7}{'win':>6}{'gross':>8}{'R/trd':>8}{'PF':>6}{'t':>6}{'cost':>7}"
          f"{'  |':>3}{'OOS n':>7}{'win':>6}{'R/trd':>8}{'PF':>6}{'t':>6}{'maxDD R':>9}")
    stats = {}
    for tf in TIMEFRAMES:
        w(f"=== 2. {tf}-MINUTE BARS: every entry x RR (R statistics, every stock's signal) ===")
        w(hd)
        for ent in ENTRIES:
            for rr in RRS:
                v = t[(t.tf == tf) & (t.entry_type == ent) & (t.rr == rr)]
                a, b = r_stats(v[v["date"] < OOS_START]), r_stats(v[v["date"] >= OOS_START])
                stats[(tf, ent, rr)] = (a, b)
                w(f"{vname(tf, ent, rr):<16}{_f(a['n'], 'n'):>7}{_f(a['win'], 'pct'):>6}{_f(a['gross'], 'r'):>8}"
                  f"{_f(a['avg'], 'r'):>8}{_f(a['pf'], 'pf'):>6}{_f(a['tstat'], 't'):>6}{_f(a['cost'], 'r'):>7}"
                  f"{'  |':>3}{_f(b['n'], 'n'):>7}{_f(b['win'], 'pct'):>6}{_f(b['avg'], 'r'):>8}{_f(b['pf'], 'pf'):>6}"
                  f"{_f(b['tstat'], 't'):>6}{_f(a['dd'], 'r1'):>9}")
            w("")
    w("'cost' = average cost per trade in R. 'maxDD R' = in-sample drawdown of the summed R line.")
    w("")

    # ---------- 3. pick on in-sample, check out-of-sample
    w("=== 3. OUT-OF-SAMPLE CHECK: best variant chosen on IN-SAMPLE only ===")
    cands = {k: v for k, v in stats.items() if k[1] != "ctrl" and v[0]["n"] >= MIN_TRADES_TO_PICK}
    nvar = len(cands)
    if cands:
        best = max(cands, key=lambda k: np.nan_to_num(cands[k][0]["avg"], nan=-9))
        a, b = cands[best]
        w(f"Best in-sample: {vname(*best)}  IS {_f(a['avg'], 'r')}R/trade (t {_f(a['tstat'], 't')}, {a['n']} trades)"
          f"  ->  OOS {_f(b['avg'], 'r')}R/trade (t {_f(b['tstat'], 't')}, {b['n']} trades)")
        for ent in ("A", "B", "C"):
            ks = [k for k in cands if k[1] == ent]
            if ks:
                k = max(ks, key=lambda k: np.nan_to_num(cands[k][0]["avg"], nan=-9))
                a, b = cands[k]
                w(f"  best {ent}: {vname(*k):<16} IS {_f(a['avg'], 'r')} (t {_f(a['tstat'], 't')})  ->  OOS {_f(b['avg'], 'r')} (t {_f(b['tstat'], 't')})")
        w(f"{nvar} variants were compared. With that many tries, the best in-sample t-stat is often ~2.5")
        w("by pure luck, so treat in-sample t < 3 as no evidence; what matters is whether OOS holds up.")
    w("")

    # ---------- 4. money
    w("=== 4. MONEY: 1% risk/trade, max ONE trade per Monday for the account (earliest signal), $100k ===")
    w(f"{'variant':<16}{'IS trd':>7}{'IS CAGR':>9}{'IS maxDD':>10}{'OOS trd':>8}{'OOS ret':>9}{'OOS maxDD':>10}{'capped':>8}")
    curves, years = [], {}
    for tf in TIMEFRAMES:
        for ent in ENTRIES:
            for rr in RRS:
                v = t[(t.tf == tf) & (t.entry_type == ent) & (t.rr == rr)]
                a, ca = portfolio(v, START, split_end)
                b, cb = portfolio(v, OOS_START, last)
                full, cf = portfolio(v, START, last)
                for c, p in [(ca, "IS"), (cb, "OOS"), (cf, "ALL")]:
                    c["variant"], c["period"] = vname(tf, ent, rr), p
                    curves.append(c)
                years[vname(tf, ent, rr)] = yearly(cf)
                w(f"{vname(tf, ent, rr):<16}{a['n']:>7}{_f(a['cagr'], 'pct1'):>9}{_f(-a['dd'], 'pct1'):>10}"
                  f"{b['n']:>8}{_f(b['ret'], 'pct1'):>9}{_f(-b['dd'], 'pct1'):>10}{_f(a['capped'], 'pct'):>8}")
        w("")
    w("'capped' = share of in-sample trades where the 4x-equity limit cut the 1% risk (tight stops).")
    w("")

    # ---------- 5. year by year
    yrs = sorted({y for d in years.values() for y in d})
    w("=== 5. ANNUAL PERFORMANCE (account, one trade per Monday, whole period compounded) ===")
    w(f"{'variant':<16}" + "".join(f"{y:>9}" for y in yrs))
    for nm, d in years.items():
        w(f"{nm:<16}" + "".join(f"{_f(d.get(y, np.nan), 'pct1'):>9}" for y in yrs))
    w("")

    # ---------- 6. exits
    # ---------- 6. downtrend filters
    w("=== 6. DOWNTREND FILTER: only short when the stock is in a small downtrend at Friday's close ===")
    w("below_ma = close < 20-day MA | ma_down = 20-day MA lower than 5 days ago | down_week = close < close")
    w("5 days ago | both_ma = below_ma AND ma_down. Cells: net R/trade (t-stat), trades in brackets.")
    w("The ctrl rows (short every Monday) show how much is just 'shorting weak stocks'.")
    fstats = {}
    for tf in TIMEFRAMES:
        w("")
        w(f"--- {tf}-minute bars ---")
        w(f"{'variant':<16}{'filter':>11}{'IS R/trd':>10}{'t':>6}{'IS n':>7}{'OOS R/trd':>11}{'t':>6}{'OOS n':>7}{'OOS PF':>8}")
        for ent in ENTRIES:
            for rr in RRS:
                v = t[(t.tf == tf) & (t.entry_type == ent) & (t.rr == rr)]
                for fl in FILTERS:
                    x = v[v[fl]]
                    a, b = r_stats(x[x["date"] < OOS_START]), r_stats(x[x["date"] >= OOS_START])
                    fstats[(tf, ent, rr, fl)] = (a, b)
                    w(f"{vname(tf, ent, rr) if fl == 'none' else '':<16}{fl:>11}{_f(a['avg'], 'r'):>10}{_f(a['tstat'], 't'):>6}"
                      f"{a['n']:>7}{_f(b['avg'], 'r'):>11}{_f(b['tstat'], 't'):>6}{b['n']:>7}{_f(b['pf'], 'pf'):>8}")
    w("")
    w("Average over all A/B/C variants (net R/trade), per filter:")
    for fl in FILTERS:
        ks = [k for k in fstats if k[3] == fl and k[1] != "ctrl"]
        ia = np.nanmean([fstats[k][0]["avg"] for k in ks]); ob = np.nanmean([fstats[k][1]["avg"] for k in ks])
        ca = np.nanmean([fstats[k][0]["avg"] for k in fstats if k[3] == fl and k[1] == "ctrl"])
        cb = np.nanmean([fstats[k][1]["avg"] for k in fstats if k[3] == fl and k[1] == "ctrl"])
        w(f"  {fl:>10}: strategies IS {_f(ia, 'r')}  OOS {_f(ob, 'r')}   |   control IS {_f(ca, 'r')}  OOS {_f(cb, 'r')}")
    cand = {k: v for k, v in fstats.items() if k[1] != "ctrl" and k[3] != "none" and v[0]["n"] >= MIN_TRADES_TO_PICK}
    if cand:
        best = max(cand, key=lambda k: np.nan_to_num(cand[k][0]["avg"], nan=-9))
        a, b = cand[best]
        w(f"Best filtered variant in-sample: {vname(*best[:3])} + {best[3]}: IS {_f(a['avg'], 'r')} (t {_f(a['tstat'], 't')}, "
          f"{a['n']}) -> OOS {_f(b['avg'], 'r')} (t {_f(b['tstat'], 't')}, {b['n']}). {len(cand)} combinations compared.")
        best_money = best
        v = t[(t.tf == best[0]) & (t.entry_type == best[1]) & (t.rr == best[2]) & t[best[3]]]
        pa, _ = portfolio(v, START, split_end)
        pb, _ = portfolio(v, OOS_START, last)
        w(f"  account (1% risk, one trade per Monday): IS CAGR {_f(pa['cagr'], 'pct1')}, maxDD {_f(-pa['dd'], 'pct1')} | "
          f"OOS {_f(pb['ret'], 'pct1')}, maxDD {_f(-pb['dd'], 'pct1')}")
    w("")

    w("=== 7. HOW TRADES END (whole period, share of trades) ===")
    w(f"{'variant':<16}{'target':>8}{'stop':>8}{'16:00':>8}{'med risk/ATR':>14}")
    for tf in TIMEFRAMES:
        for ent in ENTRIES:
            for rr in RRS:
                v = t[(t.tf == tf) & (t.entry_type == ent) & (t.rr == rr)]
                if not len(v):
                    continue
                rc = v["reason"].value_counts(normalize=True)
                w(f"{vname(tf, ent, rr):<16}{_f(rc.get('target', 0.0), 'pct'):>8}{_f(rc.get('stop', 0.0), 'pct'):>8}"
                  f"{_f(rc.get('close', 0.0), 'pct'):>8}{v['risk_atr'].median():>14.2f}")
    return "\n".join(out), pd.concat(curves) if curves else pd.DataFrame()


def main():
    end = pd.Timestamp.now(tz=ET) - pd.Timedelta(minutes=20)      # free plan: SIP data 15+ min old
    load_from = pd.Timestamp(START, tz=ET) - pd.Timedelta(days=40)  # warm-up for the 14-day ATR
    data = fake_data(load_from, end) if FAKE else load_all(load_from, end)
    t0 = time.time()
    trades, pat, ctl = run(data)
    print(f"Simulated {len(data)} stocks in {time.time() - t0:.0f}s: {len(trades):,} trade rows", flush=True)
    if not len(trades):
        sys.exit("No trades: check the data.")
    text, curves = report(trades, pat, ctl, trades["date"].min(), trades["date"].max())
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "summary.txt"), "w") as f:
        f.write(text + "\n")
    trades.to_csv(os.path.join(OUT_DIR, "trades.csv"), index=False)
    curves.to_csv(os.path.join(OUT_DIR, "portfolio.csv"), index=False)
    print(text)


if __name__ == "__main__":
    main()
