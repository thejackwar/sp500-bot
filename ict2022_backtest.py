"""
ICT 2022 model backtest on SPY and QQQ, 1-minute bars. Standalone: not part of the bot.

The rules are written out in claude/ict2022-spec.md (Project). Short version, for a long:
  levels   previous day high/low + pre-market high/low (4:00-9:30), fixed at 9:30
  sweep    a bar trades >= $0.01 below a low level between 9:30 and 11:00
  shift    a bar CLOSES above the last confirmed swing high (2 bars each side) before the
           sweep extreme, within 30 bars of the sweep
  gap      3-candle fair value gap between the extreme and the bar after the shift;
           middle candle body >= 1x ATR(14), gap >= 0.1x ATR(14)
  entry    limit at the gap's near edge or midpoint, valid 20 bars, cancelled at 11:00,
           if the target trades first, or if a new sweep happens; fills only if price
           trades $0.01 through it
  stop     $0.02 beyond the sweep extreme ($0.01 slippage); entry + stop in one bar = loss
  target   nearest untaken opposite level (skip if < 1.5R) or a fixed 2R; flat at 16:00
  money    1% risk per trade, max 2x equity per position (SPY + QQQ together stay inside
           Alpaca's 4x day-trading buying power), one trade per symbol per day
Shorts are the exact mirror image (the code flips prices and runs the long rules).

8 variants = gap (last / deepest) x entry (edge / mid) x target (liq / 2R).
In-sample = everything before the last 12 months; out-of-sample = the last 12 months.
Pick the winner on in-sample, then check it holds out-of-sample.

Runs on GitHub Actions (.github/workflows/ict2022-backtest.yml) or locally:
    ALPACA_KEY_ID=... ALPACA_SECRET_KEY=... YEARS=7 python ict2022_backtest.py
Results go to backtest-results/ (summary.txt, trades.csv, orders.csv, equity.csv).
"""
import datetime as dt
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

ET = ZoneInfo("America/New_York")
SYMBOLS = os.environ.get("SYMBOLS", "SPY QQQ").split()
BARS_URL = "https://data.alpaca.markets/v2/stocks/{}/bars"
CACHE_DIR = os.environ.get("CACHE_DIR", "price-cache-ict")
OUT_DIR = os.environ.get("OUT_DIR", "backtest-results")
YEARS = int(os.environ.get("YEARS", "7"))
OOS_DAYS = 365                     # out-of-sample = the last 12 months

# ------------------------------------------------------------------ rules
TICK = 0.01
EPS = 1e-6                         # float tolerance for price comparisons
PRE_START, RTH_START, WINDOW_END, RTH_END = 240, 570, 660, 960   # minutes after midnight ET
SWEEP_EXPIRY = 30                  # bars from the (latest) sweep to the shift
SWING_N = 2                        # swing = higher than 2 bars on each side
SWING_LOOKBACK = 30                # reference swing must be within 30 bars before the extreme
ATR_N = 14
BODY_ATR = 1.0                     # displacement: gap's middle candle body >= 1x ATR
GAP_MIN_ATR = 0.1                  # gap size >= 0.1x ATR
ORDER_BARS = 20                    # limit order stays valid 20 bars
STOP_BUFFER = 0.02
SLIPPAGE = 0.01                    # per share, on stop and market exits
MIN_R_LIQ = 1.5                    # liquidity target must be >= 1.5R away
FIXED_R = 2.0
RISK = 0.01                        # 1% of equity risked per trade
MAX_NOTIONAL = 2.0                 # max position size, x equity
START_EQUITY = 100_000.0

TREND_MA = 20                      # trend bias: yesterday's close vs its 20-day average
BIASES = os.environ.get("BIASES", "none ict trend").split()
VARIANTS = [dict(gap=g, entry=e, target=t, bias=b) for b in BIASES
            for g in ("last", "deepest") for e in ("edge", "mid") for t in ("liq", "2R")]


def vname(v):
    base = f"{v['gap']}/{v['entry']}/{v['target']}"
    return base if v.get("bias", "none") == "none" else f"{base} +{v['bias']}"


def ict_bias(y, p):
    """Daily bias from yesterday's candle y vs the day before p (each = (high, low, close)).
    +1 bullish, -1 bearish, 0 none (inside day, or both sides taken)."""
    took_h, took_l = y[0] > p[0], y[1] < p[1]
    if took_h and took_l:
        return 0
    if y[2] > p[0]:
        return 1                       # closed above the prior day's high
    if y[2] < p[1]:
        return -1                      # closed below the prior day's low
    if took_h:
        return -1                      # swept the prior high, closed back inside
    if took_l:
        return 1                       # swept the prior low, closed back inside
    return 0


# ------------------------------------------------------------------ data
def _headers():
    key, secret = os.environ.get("ALPACA_KEY_ID"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        sys.exit("ALPACA_KEY_ID / ALPACA_SECRET_KEY are not set (add them as repository secrets).")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def _utc(ts):
    return pd.Timestamp(ts).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def _download(sym, start, end, headers):
    """Raw 1-minute bars (all exchanges, split-adjusted only so real cent prices are kept)."""
    params = {"timeframe": "1Min", "start": _utc(start), "end": _utc(end), "limit": 10000,
              "adjustment": "split", "feed": "sip"}
    out = []
    while True:
        r = None
        for _ in range(10):
            try:
                r = requests.get(BARS_URL.format(sym), headers=headers, params=params, timeout=60)
            except requests.RequestException:
                time.sleep(5)
                continue
            if r.status_code == 429:               # too many requests: wait and retry
                time.sleep(15)
                continue
            break
        if r is None or not r.ok:
            raise RuntimeError(f"{sym}: {r.status_code if r is not None else 'no connection'} "
                               f"{r.text[:200] if r is not None else ''}")
        js = r.json()
        out.extend(js.get("bars") or [])
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
    mins = df.index.hour * 60 + df.index.minute
    return df[(mins >= PRE_START) & (mins < RTH_END)]          # 4:00-16:00 ET only


def fetch(sym, start, end, headers):
    """Download in ~3-month pieces, 4 at a time."""
    edges = list(pd.date_range(start, end, freq="90D")) + [end]
    parts = [(a, b) for a, b in zip(edges[:-1], edges[1:]) if b > a]
    with ThreadPoolExecutor(max_workers=4) as pool:
        frames = list(pool.map(lambda p: _frame(_download(sym, p[0], p[1], headers)), parts))
    frames = [f for f in frames if len(f)]
    if not frames:
        return _frame([])
    df = pd.concat(frames)
    return df[~df.index.duplicated()].sort_index()


def load(sym, start, end, headers):
    """Bars from the saved copy, downloading only what's new (or everything if it's too short)."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{sym}_1min.pkl.gz")
    old = None
    try:
        old = pd.read_pickle(path)
    except Exception:
        pass
    if old is not None and len(old) and old.index[0] <= start + pd.Timedelta(days=7):
        frm = old.index[-1] + pd.Timedelta(minutes=1)
        new = fetch(sym, frm, end, headers) if frm < end else old.iloc[:0]
        df = pd.concat([old, new])
        df = df[~df.index.duplicated(keep="last")].sort_index()
        print(f"{sym}: {len(old):,} saved bars + {len(new):,} new", flush=True)
    else:
        t0 = time.time()
        df = fetch(sym, start, end, headers)
        print(f"{sym}: downloaded {len(df):,} bars in {time.time() - t0:.0f}s", flush=True)
    df.to_pickle(path)
    return df[df.index >= start]


# ------------------------------------------------------------------ day preparation
def _swings(h):
    """True where h[i] is strictly above the SWING_N bars on each side."""
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


def build_days(df, sym):
    """One dict per trading day: that day's 4:00-16:00 bars, ATR, swings and the 4 levels."""
    df = df.sort_index()
    mins = (df.index.hour * 60 + df.index.minute).to_numpy()
    keep = (mins >= PRE_START) & (mins < RTH_END)
    df, mins = df[keep], mins[keep]
    if not len(df):
        return []
    dates = np.array(df.index.date)
    o, h, l, c = (df[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    rth = mins >= RTH_START

    # ATR(14) on regular-session bars, carried across days. The first bar of each day uses its
    # own high-low (no overnight gap). atr[i] = average of the 14 bars BEFORE bar i.
    ir = np.flatnonzero(rth)
    hr, lr, cr, dr = h[ir], l[ir], c[ir], dates[ir]
    pc = np.r_[np.nan, cr[:-1]]
    newday = np.r_[True, dr[1:] != dr[:-1]]
    tr = np.where(newday, hr - lr,
                  np.maximum(hr - lr, np.maximum(np.abs(hr - pc), np.abs(lr - pc))))
    atr = np.full(len(df), np.nan)
    atr[ir] = pd.Series(tr).rolling(ATR_N).mean().shift(1).to_numpy()

    starts = np.flatnonzero(np.r_[True, dates[1:] != dates[:-1]])
    ends = np.r_[starts[1:], len(df)]
    days, hist = [], []                 # hist = (high, low, close) of each past regular session
    for a, b in zip(starts, ends):
        r = rth[a:b]
        if not r.any():
            continue
        r0 = int(np.argmax(r))
        day_h, day_l = h[a:b], l[a:b]
        if hist:
            pdh, pdl = hist[-1][0], hist[-1][1]
            bias = {"none": 0,
                    "ict": ict_bias(hist[-1], hist[-2]) if len(hist) >= 2 else 0,
                    "trend": 0}
            if len(hist) >= TREND_MA:
                ma = np.mean([x[2] for x in hist[-TREND_MA:]])
                bias["trend"] = int(np.sign(hist[-1][2] - ma))
            lows, highs = [("PDL", pdl)], [("PDH", pdh)]
            taken = []
            if r0 > 0:
                pmh, pml = day_h[:r0].max(), day_l[:r0].min()
                lows.append(("PML", pml))
                highs.append(("PMH", pmh))
                # a previous-day level already traded through before 9:30 is no longer liquidity
                if pml <= pdl - TICK + EPS:
                    taken.append("PDL")
                if pmh >= pdh + TICK - EPS:
                    taken.append("PDH")
            days.append(dict(sym=sym, date=dates[a], o=o[a:b], h=day_h, l=day_l, c=c[a:b],
                             mins=mins[a:b], atr=atr[a:b], r0=r0, lows=lows, highs=highs,
                             taken=taken, sw_hi=_swings(day_h), sw_lo=_swings(-day_l), bias=bias))
        hist.append((day_h[r0:].max(), day_l[r0:].min(), c[a:b][-1]))
    return days


# ------------------------------------------------------------------ the rules, one day
def _hhmm(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def simulate_day(day, var, funnel):
    """Run one variant over one day. Returns the day's orders (filled ones carry the trade)."""
    o, h, l, c, mins, atr = day["o"], day["h"], day["l"], day["c"], day["mins"], day["atr"]
    n, r0 = len(h), day["r0"]
    # direction +1 = long; -1 = short, run as a long on flipped prices (high <-> -low)
    views = {1: (o, h, l, c, day["sw_hi"]), -1: (-o, -l, -h, -c, day["sw_lo"])}
    sweep_lv = {1: day["lows"], -1: [(nm, -p) for nm, p in day["highs"]]}
    target_lv = {1: day["highs"], -1: [(nm, -p) for nm, p in day["lows"]]}
    taken = set(day["taken"])
    setup = order = pos = None
    orders = []
    b = day.get("bias", {}).get(var.get("bias", "none"), 0)
    allowed = {1, -1} if var.get("bias", "none") == "none" else ({b} if b else set())

    def close(pos, exit_v, why, i):
        d = pos["d"]
        rec = pos["rec"]
        rec.update(filled=True, fill_time=_hhmm(mins[pos["fill"]]), exit_time=_hhmm(mins[i]),
                   exit=round(d * exit_v, 4), reason=why,
                   R=(exit_v - pos["entry"]) / (pos["entry"] - pos["stop"]))
        return orders

    for i in range(r0, n):
        # ---- open position: stop first (worse case), then target, then the 16:00 close
        if pos is not None:
            O, H, L, C, _ = views[pos["d"]]
            if L[i] <= pos["stop"] + EPS:
                return close(pos, min(pos["stop"], O[i]) - SLIPPAGE, "stop", i)
            if H[i] >= pos["target"] + TICK - EPS:
                return close(pos, pos["target"], "target", i)
            if i == n - 1:
                return close(pos, C[i] - SLIPPAGE, "close", i)
            continue
        if mins[i] >= WINDOW_END:                       # 11:00: no new entries, orders cancelled
            break

        # ---- pending limit order
        if order is not None:
            O, H, L, C, _ = views[order["d"]]
            if i > order["last"]:
                order["rec"]["cancel"] = "expired"
                order = None
            elif H[i] >= order["target"] - EPS:         # target traded before we were filled
                order["rec"]["cancel"] = "target first"
                order = None
            elif L[i] <= order["entry"] - TICK + EPS:   # traded $0.01 through the limit: filled
                pos = dict(order, fill=i)
                order = None
                funnel["fills"] += 1
                if L[i] <= pos["stop"] + EPS:           # entry and stop in the same bar = loss
                    return close(pos, pos["stop"] - SLIPPAGE, "stop (same bar)", i)
                continue

        # ---- sweeps of untaken levels
        hits = {}
        for d in (1, -1):
            L = views[d][2]
            hits[d] = [nm for nm, p in sweep_lv[d] if nm not in taken and L[i] <= p - TICK + EPS]
            taken.update(hits[d])
            if d not in allowed:                        # against the daily bias: level is used up,
                hits[d] = []                            # but no setup and no reset
        if hits[1] and hits[-1]:                        # one bar swept both sides: ambiguous, reset
            if order is not None:
                order["rec"]["cancel"] = "new sweep"
            setup = order = None
            continue
        dn = 1 if hits[1] else (-1 if hits[-1] else 0)
        if dn:
            funnel["sweeps"] += 1
            if setup is not None and setup["d"] == dn and setup["phase"] == "shift":
                setup["sweep"] = i                      # deeper sweep: same setup, clock restarts
                setup["levels"] += hits[dn]
            else:
                if order is not None:
                    order["rec"]["cancel"] = "new sweep"
                order = None
                setup = dict(d=dn, sweep=i, ext=views[dn][2][i], ext_bar=i, phase="shift",
                             levels=list(hits[dn]), sweep_time=_hhmm(mins[i]))
        if setup is None:
            continue
        d = setup["d"]
        O, H, L, C, SW = views[d]

        # ---- waiting for the structure shift
        if setup["phase"] == "shift":
            if L[i] < setup["ext"] - EPS:
                setup["ext"], setup["ext_bar"] = L[i], i
            ref = None
            eb = setup["ext_bar"]
            for j in range(eb - 1, max(eb - SWING_LOOKBACK, 0) - 1, -1):
                if SW[j] and j + SWING_N <= i:          # swing must be confirmed by now
                    ref = j
                    break
            if ref is not None and C[i] > H[ref] + EPS:
                setup.update(phase="gap", s=i, ref=ref)
                funnel["shifts"] += 1
            elif i - setup["sweep"] >= SWEEP_EXPIRY:
                setup = None
            continue

        # ---- bar after the shift: find the gap, place the order
        s = setup["s"]
        sw_setup, setup = setup, None
        if L[i] < sw_setup["ext"] - EPS:                # extreme broken before the order went in
            continue
        gaps = []
        for k in range(sw_setup["ext_bar"], s):         # candle 3 = k+2 <= s+1
            a = atr[k + 1]
            if not a > 0:
                continue
            size = L[k + 2] - H[k]
            if size > EPS and size >= GAP_MIN_ATR * a - EPS and C[k + 1] - O[k + 1] >= BODY_ATR * a - EPS:
                gaps.append(k)
        if not gaps:
            funnel["no gap"] += 1
            continue
        if var["gap"] == "last":
            k = gaps[-1]
        else:                                           # deepest = closest to the sweep extreme
            k = min(gaps, key=lambda g: (H[g], -g))
        top, bottom = L[k + 2], H[k]
        entry = round(top, 2) if var["entry"] == "edge" else round((top + bottom) / 2, 2)
        stop = round(sw_setup["ext"] - STOP_BUFFER, 2)
        risk = entry - stop
        if risk <= EPS:
            continue
        if var["target"] == "liq":
            cands = [(p, nm) for nm, p in target_lv[d] if nm not in taken and p > entry + EPS]
            if not cands:
                funnel["no target"] += 1
                continue
            target, tname = min(cands)
            if target - entry < MIN_R_LIQ * risk - EPS:
                funnel["target < 1.5R"] += 1
                continue
        else:
            target, tname = round(entry + FIXED_R * risk, 2), "2R"
        funnel["orders"] += 1
        rec = dict(date=day["date"], sym=day["sym"], variant=vname(var),
                   side="long" if d == 1 else "short", swept="+".join(sw_setup["levels"]),
                   sweep_time=sw_setup["sweep_time"], shift_time=_hhmm(mins[s]),
                   order_time=_hhmm(mins[i]), entry=round(d * entry, 2), stop=round(d * stop, 2),
                   target=round(d * target, 2), target_level=tname, risk=round(risk, 2),
                   filled=False, cancel="")
        orders.append(rec)
        order = dict(d=d, entry=entry, stop=stop, target=target, last=i + ORDER_BARS, rec=rec)

    if order is not None:
        order["rec"]["cancel"] = "11:00"
    return orders


# ------------------------------------------------------------------ statistics
def max_dd(curve):
    curve = np.asarray(curve, float)
    if not len(curve):
        return 0.0
    return float((np.maximum.accumulate(curve) - curve).max())


def r_stats(orders):
    """Stats in R. Expectancy per order counts missed fills as 0R."""
    n = len(orders)
    t = orders[orders["filled"]]
    if not n:
        return dict(orders=0, fills=0, fill=np.nan, win=np.nan, avgR=np.nan, exp=np.nan,
                    totR=0.0, pf=np.nan, ddR=0.0)
    R = t["R"].to_numpy(float)
    gain, loss = R[R > 0].sum(), -R[R < 0].sum()
    return dict(orders=n, fills=len(t), fill=len(t) / n,
                win=(R > 0).mean() if len(R) else np.nan,
                avgR=R.mean() if len(R) else np.nan, exp=R.sum() / n, totR=R.sum(),
                pf=gain / loss if loss > 0 else np.nan, ddR=max_dd(np.r_[0, np.cumsum(R)]))


def equity_curve(trades, start, end):
    """Daily equity: 1% risk per trade, max 2x equity per position, sized on the day's start equity."""
    eq, rows, capped, risked = START_EQUITY, [], 0, []
    by_day = {d: g for d, g in trades.groupby("date")} if len(trades) else {}
    for d in pd.bdate_range(start, end).date:
        g = by_day.get(d)
        pnl = 0.0
        if g is not None:
            for t in g.itertuples():
                risk_ps = abs(t.entry - t.stop)
                sh_risk = RISK * eq / risk_ps
                sh_cap = MAX_NOTIONAL * eq / abs(t.entry)
                capped += sh_cap < sh_risk
                shares = np.floor(min(sh_risk, sh_cap))
                risked.append(shares * risk_ps / eq)
                pnl += shares * t.R * risk_ps
        eq += pnl
        rows.append((d, eq))
    return pd.DataFrame(rows, columns=["date", "equity"]), capped, (np.mean(risked) if risked else np.nan)


def eq_stats(trades, start, end):
    curve, capped, avg_risk = equity_curve(trades, start, end)
    e = curve["equity"].to_numpy()
    yrs = max((pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25, 1 / 365)
    ret = e[-1] / START_EQUITY - 1 if len(e) else 0.0
    ann = (e[-1] / START_EQUITY) ** (1 / yrs) - 1 if len(e) and e[-1] > 0 else -1.0
    dd = float(((np.maximum.accumulate(e) - e) / np.maximum.accumulate(e)).max()) if len(e) else 0.0
    return dict(ret=ret, ann=ann, dd=dd, capped=capped / max(len(trades), 1), risk=avg_risk), curve


def _f(x, kind):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "-"
    return {"pct": f"{x:.0%}", "r": f"{x:+.2f}", "r1": f"{x:+.1f}", "n": f"{x:,}",
            "pf": f"{x:.2f}", "pct1": f"{x:+.1%}"}[kind]


R_HEAD = (f"{'variant':<30}{'orders':>7}{'fill%':>7}{'trades':>7}{'win%':>6}{'avgR':>7}"
          f"{'R/order':>8}{'totalR':>8}{'PF':>6}{'maxDD R':>8}")


def r_line(label, s):
    return (f"{label:<30}{_f(s['orders'], 'n'):>7}{_f(s['fill'], 'pct'):>7}{_f(s['fills'], 'n'):>7}"
            f"{_f(s['win'], 'pct'):>6}{_f(s['avgR'], 'r'):>7}{_f(s['exp'], 'r'):>8}"
            f"{_f(s['totR'], 'r1'):>8}{_f(s['pf'], 'pf'):>6}{_f(s['ddR'], 'r1'):>8}")


# ------------------------------------------------------------------ main
def run(data):
    """data: {symbol: 1-minute DataFrame}. Returns orders DataFrame and funnel counts."""
    days = {sym: build_days(df, sym) for sym, df in data.items()}
    rows, funnels = [], {}
    for var in VARIANTS:
        fun = defaultdict(int)
        for sym in days:
            for day in days[sym]:
                rows.extend(simulate_day(day, var, fun))
        funnels[vname(var)] = dict(fun)
    cols = ["date", "sym", "variant", "side", "swept", "sweep_time", "shift_time", "order_time",
            "entry", "stop", "target", "target_level", "risk", "filled", "cancel",
            "fill_time", "exit_time", "exit", "reason", "R"]
    orders = pd.DataFrame(rows).reindex(columns=cols)
    if len(orders):
        orders["filled"] = orders["filled"].astype(bool)
    return orders, funnels, days


def report(orders, funnels, first, last):
    split = last - dt.timedelta(days=OOS_DAYS)
    periods = [("IN-SAMPLE", first, split), ("OUT-OF-SAMPLE (last 12 months)", split + dt.timedelta(days=1), last)]
    out, curves = [], []
    out.append("ICT 2022 MODEL BACKTEST - SPY + QQQ, 1-minute bars (Alpaca, all exchanges)")
    out.append(f"Data {first} to {last}. In-sample to {split}; out-of-sample after it.")
    out.append("R/order = average R per order placed, missed fills counted as 0R (the fair way to compare).")
    out.append("Costs: limit fills only when price trades $0.01 through; $0.01/share slippage on stop and")
    out.append("16:00 exits; entry + stop in the same minute = loss; target and fill in the same minute = no trade.")
    out.append("")

    def sub(p0, p1, **kw):
        m = (orders["date"] >= p0) & (orders["date"] <= p1)
        for k, v in kw.items():
            m &= orders[k] == v
        return orders[m]

    # rank on in-sample, both directions
    names = [vname(v) for v in VARIANTS]
    rank = sorted(names, key=lambda nm: -np.nan_to_num(r_stats(sub(first, split, variant=nm))["exp"], nan=-9))
    # does the daily bias help? same setup, with and without each filter
    bases = [vname(dict(v, bias="none")) for v in VARIANTS if v["bias"] == BIASES[0]]
    if len(BIASES) > 1:
        out.append("=== DAILY BIAS EFFECT: R/order (missed fills = 0R), orders in brackets ===")
        out.append(f"{'setup':<20}" + "".join(f"{('IS ' + b):>16}{('OOS ' + b):>16}" for b in BIASES))
        for base in bases:
            cells = ""
            for bias in BIASES:
                nm = base if bias == "none" else f"{base} +{bias}"
                for p0, p1 in [(first, split), (split + dt.timedelta(days=1), last)]:
                    s = r_stats(sub(p0, p1, variant=nm))
                    cells += f"{_f(s['exp'], 'r') + ' (' + str(s['orders']) + ')':>16}"
            out.append(f"{base:<20}{cells}")
        out.append("")

    for title, p0, p1 in periods:
        out.append(f"=== {title}: both directions, both symbols (ranked by in-sample R/order) ===")
        out.append(R_HEAD)
        for nm in rank:
            out.append(r_line(nm, r_stats(sub(p0, p1, variant=nm))))
        out.append("")

    out.append("=== MONEY: 1% risk per trade, max 2x equity per position, $100k start ===")
    out.append(f"{'variant':<30}{'IS total':>10}{'IS /year':>10}{'IS maxDD':>10}"
               f"{'OOS total':>11}{'OOS maxDD':>11}{'size capped':>13}{'avg risk':>10}")
    for nm in rank:
        st = []
        for title, p0, p1 in periods:
            t = sub(p0, p1, variant=nm)
            s, curve = eq_stats(t[t["filled"]].sort_values(["date", "fill_time"]), p0, p1)
            curve["variant"], curve["period"] = nm, title.split(" ")[0]
            curves.append(curve)
            st.append(s)
        avg_risk = "-" if np.isnan(st[0]["risk"]) else f"{st[0]['risk']:.2%}"
        out.append(f"{nm:<30}{_f(st[0]['ret'], 'pct1'):>10}{_f(st[0]['ann'], 'pct1'):>10}"
                   f"{_f(-st[0]['dd'], 'pct1'):>10}{_f(st[1]['ret'], 'pct1'):>11}"
                   f"{_f(-st[1]['dd'], 'pct1'):>11}{_f(st[0]['capped'], 'pct'):>13}{avg_risk:>10}")
    out.append("'size capped' = share of in-sample trades where the 2x-equity limit cut the 1% risk")
    out.append("(tight stops); 'avg risk' = what an in-sample trade really risked, as % of equity.")
    out.append("")

    for title, p0, p1 in periods:
        out.append(f"=== {title}: by direction and symbol ===")
        out.append(R_HEAD)
        for nm in rank:
            for side in ("long", "short"):
                out.append(r_line(f"{nm} {side}", r_stats(sub(p0, p1, variant=nm, side=side))))
            for sym in SYMBOLS:
                out.append(r_line(f"{nm} {sym}", r_stats(sub(p0, p1, variant=nm, sym=sym))))
            out.append("")

    out.append("=== FUNNEL (whole period): how many setups survive each rule ===")
    keys = ["sweeps", "shifts", "no gap", "no target", "target < 1.5R", "orders", "fills"]
    out.append(f"{'variant':<30}" + "".join(f"{k:>14}" for k in keys))
    for nm in rank:
        out.append(f"{nm:<30}" + "".join(f"{funnels[nm].get(k, 0):>14,}" for k in keys))
    out.append("")
    filled = orders[orders["filled"]]
    if len(filled):
        out.append(f"Median risk per trade: SPY {filled[filled.sym == 'SPY']['risk'].median():.2f}$, "
                   f"QQQ {filled[filled.sym == 'QQQ']['risk'].median():.2f}$ per share. "
                   "With stops this tight, the $0.01 costs are a real share of 1R.")
    return "\n".join(out), (pd.concat(curves) if curves else pd.DataFrame())


def main():
    headers = _headers()
    end = pd.Timestamp.now(tz=ET) - pd.Timedelta(minutes=20)    # free plan: SIP data 15+ min old
    start = (end - pd.DateOffset(years=YEARS)).normalize()
    start = max(start, pd.Timestamp("2016-01-04", tz=ET))       # Alpaca's history starts 2016
    data = {sym: load(sym, start, end, headers) for sym in SYMBOLS}
    t0 = time.time()
    orders, funnels, days = run(data)
    print(f"Simulated {sum(len(d) for d in days.values()):,} symbol-days x {len(VARIANTS)} variants "
          f"in {time.time() - t0:.0f}s", flush=True)
    all_days = sorted({d["date"] for ds in days.values() for d in ds})
    text, curves = report(orders, funnels, all_days[0], all_days[-1])
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "summary.txt"), "w") as f:
        f.write(text + "\n")
    orders.to_csv(os.path.join(OUT_DIR, "orders.csv"), index=False)
    orders[orders["filled"]].to_csv(os.path.join(OUT_DIR, "trades.csv"), index=False)
    curves.to_csv(os.path.join(OUT_DIR, "equity.csv"), index=False)
    print(text)


if __name__ == "__main__":
    main()
