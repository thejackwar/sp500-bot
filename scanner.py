"""
S&P 100 scanner: downloads daily + intraday prices for every S&P 100 stock, runs the
strategy in rules.py (200-day MA, Bollinger Bands, Donchian channel) and sends Telegram
alerts with a chart for each signal.

Prices come from Alpaca (official API, free plan, full all-exchange data
delayed 15+ minutes), with Yahoo Finance as automatic backup.

Runs every 15 minutes from 9:30 to 16:00 New York time on 15-minute candles (SCAN_MODE "15m";
"1h" = hourly candles, for manual runs). Intraday candles come from Alpaca's REAL-TIME IEX feed,
so each check analyzes the candle that has just closed; daily history comes from the complete
(15-minute delayed) all-exchange feed. The 9:30 check has no closed candle yet and checks the
daily-chart signals against the live opening price.

Runs on GitHub Actions (see .github/workflows/scan.yml) or locally:
    pip install -r requirements.txt
    python scanner.py            # prints alerts if no Telegram secrets are set
"""
import datetime as dt
import io
import textwrap
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from fmt import px
import requests
import yfinance as yf

import patterns
import rules
import trader

ET = ZoneInfo("America/New_York")
TICKERS_URL = "https://en.wikipedia.org/wiki/S%26P_100"
# Backup copy of the S&P 100 (October 2026), used if Wikipedia can't be read.
# GOOG is left out on purpose: GOOGL is the same company (Alphabet).
SP100_BACKUP = """AAPL ABBV ABT ACN ADBE AMAT AMD AMGN AMT AMZN ANET AVGO AXP BA BAC BKNG BLK BMY BNY BRK.B
C CAT CMCSA COF COP COST CRM CSCO CVS CVX DE DELL DHR DIS DUK EMR FDX GD GE GEV GILD GM GOOGL GS HD
IBM INTC INTU ISRG JNJ JPM KO LIN LLY LMT LOW LRCX MA MCD MDLZ MDT META MMM MO MRK MS MSFT MU NEE
NFLX NOW NVDA ORCL PANW PEP PFE PG PLTR PM QCOM RTX SBUX SCHW SNDK SO T TMO TMUS TSLA TXN UBER UNH
UNP UPS USB V VZ WFC WMT XOM""".split()
PATTERNS_ENABLED = True            # chart-pattern alerts (patterns.py); set False to turn them off
ALPACA_URL = "https://data.alpaca.markets/v2/stocks/bars"
DATA_DELAY_MINUTES = 16            # Alpaca free plan: full-market (SIP) data must be 15+ min old
INTRADAY_FEED = "iex"              # real-time on the free plan ("sip" = complete but 15+ min late)
SNAPSHOT_URL = "https://data.alpaca.markets/v2/stocks/snapshots"
STATE_FILE = "state.json"          # remembers sent alerts so you never get duplicates
DAILY_LOOKBACK = 120               # daily bars used for levels / trend lines (~6 months)
INTRADAY_LOOKBACK = 200            # entry-chart candles used for levels / trend lines

# Entry-chart settings per mode. "first"/"last" = earliest/latest candle START time (ET)
# this mode handles; "fresh" = how recently the candle must have closed to alert on it.
MODES = {
    "15m": {"minutes": 15, "alpaca": "15Min", "days": 12, "yahoo": ("14d", "15m"),
            "first": (9, 30), "last": (15, 45), "fresh": 30, "name": "15-min"},
    "1h":  {"minutes": 60, "alpaca": "30Min", "days": 45, "yahoo": ("60d", "1h"),
            "first": (9, 30), "last": (15, 30), "fresh": 75, "name": "hourly"},
}
PIVOT_WINDOW = 5                   # a swing high/low must be the extreme of +-5 bars
MAX_CHART_ALERTS = 10              # full chart alerts per run; any extra setups go in one summary
MAX_WATCHLIST = 40                 # forming patterns listed per check (the rest are counted)
TELEGRAM_PAUSE = 1.5               # seconds between Telegram messages (avoids rate limits)


# ---------------------------------------------------------------- data
def get_tickers():
    """Current S&P 100 from Wikipedia (falls back to the built-in list). Yahoo-style symbols."""
    syms = []
    try:
        html = requests.get(TICKERS_URL, timeout=20, headers={"User-Agent": "Mozilla/5.0 (sp100-alert-bot)"}).text
        for table in pd.read_html(io.StringIO(html)):
            if "Symbol" in table.columns and 90 <= len(table) <= 110:
                syms = [str(x).strip() for x in table["Symbol"]]
                break
    except Exception as e:
        print(f"Couldn't read the S&P 100 list from Wikipedia ({e}); using the built-in list.", flush=True)
    if not syms:
        syms = SP100_BACKUP
    syms = [x for x in syms if x != "GOOG"]                  # Alphabet: keep GOOGL only
    return [x.replace(".", "-") for x in syms]               # BRK.B -> BRK-B


def _alpaca_chunk(part, timeframe, start, end, headers, feed="sip"):
    """Download one group of symbols, following Alpaca's pages until done."""
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    params = {"symbols": ",".join(t.replace("-", ".") for t in part),   # BRK-B -> BRK.B
              "timeframe": timeframe, "start": start.strftime(fmt), "end": end.strftime(fmt),
              "limit": 10000, "adjustment": "all", "feed": feed}
    rows = {}
    try:
        while True:
            for _ in range(5):
                r = requests.get(ALPACA_URL, headers=headers, params=params, timeout=60)
                if r.status_code != 429:              # 429 = too many requests, wait and retry
                    break
                time.sleep(10)
            r.raise_for_status()
            js = r.json()
            for sym, bars in (js.get("bars") or {}).items():
                rows.setdefault(sym, []).extend(bars)
            if not js.get("next_page_token"):
                break
            params["page_token"] = js["next_page_token"]
    except Exception as e:
        print(f"Alpaca error ({timeframe}, {part[0]}..{part[-1]}): {e}", flush=True)
    return rows


def alpaca_bars(tickers, timeframe, start, end, chunk=25, workers=8, feed="sip"):
    """Bars from Alpaca's official API, several groups downloaded at the same time.
    feed "sip": all exchanges, `end` must be 15+ minutes old on the free plan.
    feed "iex": one exchange, real-time (prices match the market closely; volume is IEX's share)."""
    key, secret = os.environ.get("ALPACA_KEY_ID"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        print("No Alpaca keys set, using Yahoo for everything.")
        return {}
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    parts = [tickers[i:i + chunk] for i in range(0, len(tickers), chunk)]
    out = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for rows in pool.map(lambda p: _alpaca_chunk(p, timeframe, start, end, headers, feed), parts):
            for sym, bars in rows.items():
                df = pd.DataFrame(bars)
                df.index = pd.DatetimeIndex(pd.to_datetime(df["t"], utc=True)).tz_convert(ET)
                df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
                out[sym.replace(".", "-")] = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
    return out


def to_session(df, minutes):
    """Keep regular-session prices (9:30-16:00 ET) and build candles of `minutes` length
    starting at 9:30 (15-min: 9:30, 9:45 ...; hourly: 9:30, 10:30 ... 15:30)."""
    mins = df.index.hour * 60 + df.index.minute
    df = df[(mins >= 570) & (mins < 960)]
    return (df.resample(f"{minutes}min", offset="30min")
            .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
            .dropna())


def snapshots(tickers):
    """Live (IEX) snapshot per stock: today's daily bar so far and the latest trade."""
    key, secret = os.environ.get("ALPACA_KEY_ID"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        return {}
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    out = {}
    for i in range(0, len(tickers), 50):
        part = [t.replace("-", ".") for t in tickers[i:i + 50]]
        try:
            r = requests.get(SNAPSHOT_URL, headers=headers, timeout=30,
                             params={"symbols": ",".join(part), "feed": INTRADAY_FEED})
            r.raise_for_status()
            js = r.json()
            js = js.get("snapshots", js)
            for sym, snap in js.items():
                if snap:
                    out[sym.replace(".", "-")] = snap
        except Exception as e:
            print(f"Alpaca snapshot error: {e}", flush=True)
    return out


def get_prices(tickers, cutoff, mode, now=None):
    """Alpaca first; any ticker Alpaca can't deliver is fetched from Yahoo instead.
    Daily history: complete feed up to `cutoff`. Intraday: real-time IEX feed up to `now`."""
    m = MODES[mode]
    end = cutoff.astimezone(dt.timezone.utc)
    end_rt = (now or cutoff).astimezone(dt.timezone.utc)
    t0 = time.time()
    daily = alpaca_bars(tickers, "1Day", end - dt.timedelta(days=400), end)
    print(f"Daily bars from Alpaca: {time.time() - t0:.0f}s", flush=True)
    t0 = time.time()
    intraday = {t: to_session(df, m["minutes"]) for t, df in
                alpaca_bars(tickers, m["alpaca"], end_rt - dt.timedelta(days=m["days"]), end_rt,
                            feed=INTRADAY_FEED).items()}
    print(f"{m['name']} bars from Alpaca ({INTRADAY_FEED}, real-time): {time.time() - t0:.0f}s", flush=True)
    daily = {t: d for t, d in daily.items() if len(d) > 60}
    intraday = {t: h for t, h in intraday.items() if len(h) > 60}
    good = [t for t in tickers if t in daily and t in intraday]
    missing = [t for t in tickers if t not in good]
    print(f"Alpaca: {len(good)} tickers OK, {len(missing)} via Yahoo backup", flush=True)
    if missing:
        yd, yi = download(missing, "1y", "1d"), download(missing, *m["yahoo"])
        for t in missing:
            if t in yd and t in yi:             # both timeframes from the same source
                daily[t], intraday[t] = yd[t], yi[t]
    return daily, intraday


def download(tickers, period, interval, chunk=100):
    """Yahoo Finance backup."""
    out = {}
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        data = yf.download(part, period=period, interval=interval, group_by="ticker",
                           auto_adjust=True, threads=True, progress=False)
        for t in part:
            try:
                df = data[t].dropna()
            except KeyError:
                continue
            if len(df) > 60:
                out[t] = df
        time.sleep(1)
    return out


def bar_end(ts, minutes):
    """When a candle closes. US candles start at :30/:45/:00/:15; the last one ends at 16:00 ET."""
    return min(ts + dt.timedelta(minutes=minutes), ts.replace(hour=16, minute=0))


def completed_bars(df, cutoff, minutes):
    df = df.copy()
    df.index = df.index.tz_convert(ET) if df.index.tz else df.index.tz_localize(ET)
    if bar_end(df.index[-1], minutes) > cutoff:     # drop a candle that wasn't finished at the cutoff
        df = df.iloc[:-1]
    return df


def in_window(ts, m):
    """Is this candle (by its start time) one this mode is responsible for?"""
    return m["first"] <= (ts.hour, ts.minute) <= m["last"]


def with_today(daily, h):
    """Daily candles with today's candle rebuilt from the intraday candles seen so far,
    so daily trend and daily patterns react during the day."""
    d = daily.copy()
    idx = d.index
    if idx.tz is not None:
        idx = idx.tz_convert(ET).tz_localize(None)
    d.index = idx.normalize()
    today = h.index[-1].date()
    t = h[h.index.date == today]
    if t.empty:
        return d[["Open", "High", "Low", "Close", "Volume"]]
    d = d[d.index.date != today][["Open", "High", "Low", "Close", "Volume"]]
    row = pd.DataFrame({"Open": [t["Open"].iat[0]], "High": [t["High"].max()], "Low": [t["Low"].min()],
                        "Close": [t["Close"].iat[-1]], "Volume": [t["Volume"].sum()]},
                       index=[pd.Timestamp(today)])
    return pd.concat([d, row])


def with_snapshot(daily, snap, today):
    """Daily candles with today's candle taken from the live snapshot (None if no data for today)."""
    bar = (snap or {}).get("dailyBar")
    if not bar:
        return None
    if pd.Timestamp(bar["t"]).tz_convert(ET).date() != today:
        return None
    last = float((snap.get("latestTrade") or {}).get("p") or bar["c"])
    d = daily.copy()
    idx = d.index.tz_convert(ET).tz_localize(None) if d.index.tz is not None else d.index
    d.index = idx.normalize()
    d = d[d.index.date != today][["Open", "High", "Low", "Close", "Volume"]]
    row = pd.DataFrame({"Open": [float(bar["o"])], "High": [max(float(bar["h"]), last)],
                        "Low": [min(float(bar["l"]), last)], "Close": [last], "Volume": [float(bar["v"])]},
                       index=[pd.Timestamp(today)])
    return pd.concat([d, row])


def hourly_from(h, mode, cutoff):
    """Completed hourly candles for pattern detection (built from 15-min candles in 15m mode)."""
    if mode == "1h":
        return h
    hr = (h.resample("60min", offset="30min")
          .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna())
    if len(hr) and bar_end(hr.index[-1], 60) > cutoff:
        hr = hr.iloc[:-1]
    return hr


# ---------------------------------------------------------------- indicators
def add_indicators(df):
    c, h, l = df["Close"], df["High"], df["Low"]
    for n in (20, 50, 200):
        df[f"EMA{n}"] = c.ewm(span=n, adjust=False).mean()
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["RSI"] = 100 - 100 / (1 + gain / loss)
    df["MACD"] = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    df["MACD_signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df["ATR"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    df["VolAvg20"] = df["Volume"].rolling(20).mean()
    return df


# ---------------------------------------------------------------- levels & lines
def pivots(df, w=PIVOT_WINDOW):
    h, l = df["High"].values, df["Low"].values
    highs, lows = [], []
    for i in range(w, len(df) - w):
        if h[i] == h[i - w:i + w + 1].max():
            highs.append(i)
        if l[i] == l[i - w:i + w + 1].min():
            lows.append(i)
    return highs, lows


def sr_levels(df, highs, lows, min_touches=2):
    """Group swing highs/lows that sit within half an ATR of each other."""
    prices = sorted([df["High"].iat[i] for i in highs] + [df["Low"].iat[i] for i in lows])
    tol = df["ATR"].iat[-1] * 0.5
    clusters = []
    for p in prices:
        if clusters and p - clusters[-1][-1] <= tol:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    levels = [(float(np.mean(c)), len(c)) for c in clusters if len(c) >= min_touches]
    price = df["Close"].iat[-1]
    support = sorted([lv for lv in levels if lv[0] < price], reverse=True)   # nearest first
    resistance = sorted([lv for lv in levels if lv[0] >= price])
    return support, resistance


def trendline(df, piv, kind):
    """Rising support line (from the lowest swing low to the latest one) or
    falling resistance line (from the highest swing high to the latest one)."""
    if len(piv) < 2:
        return None
    col = "Low" if kind == "support" else "High"
    vals = df[col].values
    a = min(piv, key=lambda i: vals[i]) if kind == "support" else max(piv, key=lambda i: vals[i])
    later = [i for i in piv if i > a]
    if not later:
        return None
    b = later[-1]
    slope = (vals[b] - vals[a]) / (b - a)
    intercept = vals[a] - slope * a
    x = np.arange(a, b + 1)
    line = intercept + slope * x
    closes = df["Close"].values[a:b + 1]
    tol = df["ATR"].iat[-1] * 0.3
    bad = (closes < line - tol).sum() if kind == "support" else (closes > line + tol).sum()
    if bad > 2:                       # line was broken too often -> not a valid trend line
        return None
    n = len(df) - 1
    return {"a": a, "b": b, "slope": float(slope), "intercept": float(intercept),
            "now": float(intercept + slope * n), "prev": float(intercept + slope * (n - 1))}


def analyze(df, lookback):
    d = df.tail(lookback)
    highs, lows = pivots(d)
    support, resistance = sr_levels(d, highs, lows)
    return {"df": d, "support": support, "resistance": resistance,
            "up_line": trendline(d, lows, "support"),
            "down_line": trendline(d, highs, "resistance")}


# ---------------------------------------------------------------- chart & telegram
# ---------------------------------------------------------------- charts (candles, black background)
UP_COL, DOWN_COL, PAT_COL = "#26a69a", "#ef5350", "#ffd54f"
SR_COL, FIB_COL = "#26c6da", "#ffb74d"


def _pos(df, ts):
    """Row position of a timestamp in df (None if it isn't there)."""
    try:
        return int(df.index.get_indexer([pd.Timestamp(ts)], method="nearest")[0])
    except Exception:
        return None


def draw_chart(ticker, df, title, show_sma=False, pattern=None, n_show=120, extra=None, trade=None):
    """Candlestick chart on black. df: candles with a DatetimeIndex; optional strategy columns
    (BB_*, DC_*, SMA200, Squeeze) are drawn when present. pattern: positions refer to df rows.
    extra: {"levels": [(price, "sup"/"res")], "fib": swing} from levels.py."""
    extra = extra or {}
    n = len(df)
    start = max(0, n - n_show)
    if pattern:
        start = min(start, max(0, min(k[0] for k in pattern["keys"]) - 10))
    fib = extra.get("fib")
    fa = _pos(df, fib["a"][0]) if fib else None
    if fa is not None:
        start = min(start, max(0, fa - 5))
    d = df.iloc[start:]
    x = np.arange(start, n)
    o, h, l, c = (d[k].values.astype(float) for k in ("Open", "High", "Low", "Close"))

    fig, ax = plt.subplots(figsize=(11, 6), dpi=110)
    fig.patch.set_facecolor("black")
    ax.set_facecolor("black")
    for side in ax.spines.values():
        side.set_color("#444")
    ax.tick_params(colors="#bbbbbb", labelsize=8)
    ax.grid(color="#262626", lw=0.6)

    if "BB_up" in d:                                   # strategy overlays
        ax.fill_between(x, d["BB_lo"].values, d["BB_up"].values, color="#42a5f5", alpha=0.10, label="Bollinger Bands")
        ax.plot(x, d["BB_mid"].values, color="#42a5f5", lw=0.7, ls=":")
    if "DC_high" in d and not pattern:
        ax.plot(x, d["DC_high"].values, color="#66bb6a", lw=0.9, drawstyle="steps-post", label="Donchian high")
        ax.plot(x, d["DC_low"].values, color="#e57373", lw=0.9, drawstyle="steps-post", label="Donchian low")
    if show_sma and "SMA200" in d:
        ax.plot(x, d["SMA200"].values, color="#ba68c8", lw=1.6, label="200-day MA")

    up = c >= o                                        # candles
    col = np.where(up, UP_COL, DOWN_COL)
    ax.vlines(x, l, h, color=col, lw=0.8, zorder=2)
    span = float(np.nanmax(h) - np.nanmin(l)) or 1.0
    body = np.maximum(np.abs(c - o), span * 0.002)
    ax.bar(x, body, 0.6, bottom=np.minimum(o, c), color=col, edgecolor=col, lw=0.4, zorder=3)

    if "Squeeze" in d:
        sq = np.where(d["Squeeze"].fillna(False).values)[0]
        if len(sq):
            ax.scatter(x[sq], d["BB_mid"].values[sq], marker="s", s=9, color="#ffa726", label="Squeeze", zorder=4)

    lo_y, hi_y = float(np.nanmin(l)), float(np.nanmax(h))
    if pattern:                                        # pattern lines, swing points, levels
        for (x1, y1), (x2, y2) in pattern["lines"]:
            ax.plot([x1, x2], [y1, y2], color=PAT_COL, lw=1.8, zorder=5)
        if pattern["name"] in patterns.HARMONICS:       # connect the legs X-A-B-C and on to D
            xs = [pt[0] for pt in pattern["points"]] + [n - 1]
            ys = [pt[1] for pt in pattern["points"]] + [pattern["level"]]
            ax.plot(xs, ys, color=PAT_COL, lw=1.2, alpha=0.8, zorder=5)
            ax.annotate("D", (n - 1, pattern["level"]), textcoords="offset points", xytext=(-10, 0),
                        ha="right", va="center", fontsize=8, color="white", zorder=6)
        for i, price, label in pattern["points"]:
            ax.scatter([i], [price], color=PAT_COL, s=22, zorder=6)
            if label:
                low = price <= float(df["Low"].iat[i]) + 1e-9 or price < float(df["Close"].iat[i])
                ax.annotate(label, (i, price), textcoords="offset points", xytext=(0, -14 if low else 8),
                            ha="center", fontsize=8, color="white", zorder=6)
        for val, colr, lab in ((pattern["level"], "#42a5f5", "entry"), (pattern["stop"], "#ef5350", "stop"),
                               (pattern["target"], "#66bb6a", "target")):
            ax.axhline(val, color=colr, ls="--", lw=0.9, zorder=4)
            ax.text(n - 0.3, val, f" {lab} {px(val)}", color=colr, va="center", fontsize=8,
                    bbox=dict(facecolor="black", edgecolor="none", pad=1), zorder=7)
            lo_y, hi_y = min(lo_y, val), max(hi_y, val)
    def right_label(val, text, colr):
        ax.text(n - 0.3, val, f" {text}", color=colr, va="center", fontsize=8,
                bbox=dict(facecolor="black", edgecolor="none", pad=1), zorder=7)

    for val, kind in extra.get("levels", []):          # major support / resistance
        ax.axhline(val, color=SR_COL, lw=1.6, zorder=4)
        right_label(val, f"major {'resistance' if kind == 'res' else 'support'} {px(val)}", SR_COL)
        lo_y, hi_y = min(lo_y, val), max(hi_y, val)
    if fib:                                            # Fibonacci swing and retracement levels
        fb = _pos(df, fib["b"][0])
        x0 = fa if fa is not None else start
        if fa is not None and fb is not None:
            ax.plot([fa, fb], [fib["a"][1], fib["b"][1]], color=FIB_COL, lw=1.2, ls="--", zorder=5)
        for r, val in [(0.0, fib["b"][1])] + sorted(fib["levels"].items()) + [(1.0, fib["a"][1])]:
            ax.plot([x0, n - 1], [val, val], color=FIB_COL, lw=1.0 if r in (0.0, 1.0) else 1.2,
                    alpha=0.6 if r in (0.0, 1.0) else 0.95, zorder=4)
            right_label(val, f"{r * 100:.1f}%  {px(val)}", FIB_COL)
            lo_y, hi_y = min(lo_y, val), max(hi_y, val)
    pad = 0.04 * (hi_y - lo_y or 1)
    ax.set_ylim(lo_y - pad, hi_y + pad)
    if trade:                                          # TradingView-style long/short position tool
        e, tp, sl = trade["entry"], trade.get("target"), trade["stop"]
        long = trade["dir"] == "LONG"
        x0, x1 = n - 1.5, n + 14
        pct = lambda v: (v / e - 1) * 100 * (1 if long else -1)     # + = gain, − = loss
        side = "BUY" if long else "SELL SHORT"
        ax.fill_between([x0, x1], e, sl, color="#ef5350", alpha=0.28, lw=0, zorder=4)
        if tp is not None:                             # fixed take-profit target
            ax.fill_between([x0, x1], e, tp, color="#26a69a", alpha=0.28, lw=0, zorder=4)
            ax.plot([x0, x1], [tp, tp], color="#26a69a", lw=1.3, zorder=5)
            ax.text(x0 + 0.5, tp, f" Take profit {px(tp)} ({pct(tp):+.1f}%)", color="white", fontsize=8,
                    va="bottom" if long else "top", zorder=7)
            entry_txt = f" {side} {trade['qty']} @ {px(e)}   R:R {abs(tp - e) / abs(e - sl):.1f}"
            top = tp
        else:                                          # trailing exit: no target, sells below the 20-day low
            entry_txt = f" {side} {trade['qty']} @ {px(e)}   no fixed target"
            top = e + (e - sl)
            ax.annotate("", xy=(x1 - 1, top), xytext=(x1 - 1, e), zorder=6,
                        arrowprops=dict(arrowstyle="-|>", color="#26a69a", lw=1.5, ls="--"))
            ax.text(x0 + 0.5, top, " Let it run: no target", color="#26a69a", fontsize=8, va="bottom", zorder=7)
            if trade.get("trail"):
                tr = trade["trail"]
                ax.plot([start, x1], [tr, tr], color="#ffd54f", lw=1.2, ls="--", zorder=5)
                ax.text(x0 + 0.5, tr, f" Trend exit below {px(tr)} ({trade.get('trail_note', '20-day low, 15:45 check')})", color="#ffd54f",
                        fontsize=8, va="top" if tr < sl else "bottom", zorder=7)
                lo_y, hi_y = min(lo_y, tr), max(hi_y, tr)
        for val, colr in ((e, "#e0e0e0"), (sl, "#ef5350")):
            ax.plot([x0, x1], [val, val], color=colr, lw=1.3, zorder=5)
        ax.text(x0 + 0.5, sl, f" Stop loss {px(sl)} ({pct(sl):+.1f}%)", color="white", fontsize=8,
                va="top" if long else "bottom", zorder=7)
        ax.text(x0 + 0.5, e, entry_txt,
                color="white", fontsize=8, va="center", fontweight="bold",
                bbox=dict(facecolor="#1e222d", edgecolor="#555", pad=2), zorder=8)
        ax.annotate("", xy=(n - 1, e), xytext=(n - 1, e - (1 if long else -1) * 0.06 * (hi_y - lo_y or 1)),
                    arrowprops=dict(arrowstyle="-|>", color="#26a69a" if long else "#ef5350", lw=2), zorder=8)
        lo_y, hi_y = min(lo_y, top, sl), max(hi_y, top, sl)
        why = ["Why this trade:"] + [textwrap.shorten(r, 78, placeholder="…") for r in trade.get("why", [])]
        ax.text(0.01, 0.98, "\n".join(why[:1] + [f"• {w}" for w in why[1:]]), transform=ax.transAxes,
                va="top", ha="left", fontsize=8, color="#e0e0e0", linespacing=1.5, zorder=9,
                bbox=dict(facecolor="#131722", edgecolor="#555", alpha=0.92, pad=6))
        pad = 0.04 * (hi_y - lo_y or 1)
        ax.set_ylim(lo_y - pad, hi_y + pad)
    labels = pattern or extra.get("levels") or fib
    ax.set_xlim(start - 1, n + (16 if labels or trade else 2))

    step = max(1, len(d) // 7)
    ticks = list(range(start, n, step))
    fmt = "%d %b" if (d.index[-1] - d.index[0]).days > 20 else "%d %b %H:%M"
    ax.set_xticks(ticks)
    ax.set_xticklabels([pd.Timestamp(df.index[i]).strftime(fmt) for i in ticks])
    ax.set_title(title, color="white", fontsize=11)
    leg = ax.legend(loc="lower left" if trade else "upper left", fontsize=7, ncol=4, facecolor="black", edgecolor="#444")
    for t in leg.get_texts():
        t.set_color("#dddddd")
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", facecolor="black")
    plt.close(fig)
    return buf.getvalue()


def send_telegram(text, image=None):
    """Send one message, pacing requests and retrying when Telegram says "slow down".
    Returns True if the message was delivered."""
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("\n[no Telegram secrets set, printing instead]\n" + text)
        return True
    base = f"https://api.telegram.org/bot{token}"
    for attempt in range(4):
        try:
            if image:
                r = requests.post(f"{base}/sendPhoto", timeout=(10, 30),
                                  data={"chat_id": chat, "caption": text[:1024], "parse_mode": "HTML"},
                                  files={"photo": ("chart.png", image)})
            else:
                r = requests.post(f"{base}/sendMessage", timeout=(10, 30),
                                  data={"chat_id": chat, "text": text[:4096], "parse_mode": "HTML",
                                        "disable_web_page_preview": "true"})
        except requests.RequestException as e:
            print(f"Telegram connection problem (attempt {attempt + 1}): {e}")
            time.sleep(5)
            continue
        if r.ok:
            time.sleep(TELEGRAM_PAUSE)
            return True
        if r.status_code == 429:                      # rate limited: wait as long as Telegram asks
            wait = r.json().get("parameters", {}).get("retry_after", 5)
            print(f"Telegram asked to slow down, waiting {wait}s")
            time.sleep(min(wait, 60) + 1)
            continue
        print("Telegram error:", r.status_code, r.text[:300])
        return False
    return False


def alert_chart(item, m):
    """Pick the chart for an alert: a pattern breakout is drawn on its own timeframe; otherwise the
    daily (or intraday) strategy chart, with any pattern still forming on that chart drawn on top."""
    t = item["t"]
    if item["pats"]:
        pt, frame = item["pats"][0]
        return draw_chart(t, frame, f"{t} {pt['timeframe']}: {pt['name']} (breakout)",
                          show_sma=pt["timeframe"] == "daily", pattern=pt)
    daily = any(e["tf"] == "daily" for e in item["events"])
    tf = "daily" if daily else "hourly"
    extra = {"levels": []}
    for e in item["events"]:                          # major S/R and Fibonacci overlays
        draw = e.get("draw", {})
        extra["levels"] += draw.get("levels", [])
        if "fib" in draw:
            extra["fib"] = draw["fib"]
    forming = [(p, f) for p, f in item.get("forming", []) if p["timeframe"] == tf]
    if forming:
        p, frame = forming[0]
        return draw_chart(t, frame, f"{t} {tf}: {p['name']} (forming)", show_sma=daily, pattern=p,
                          extra=extra if daily else None)
    if daily:
        what = " + ".join(x for x, k in (("major S/R", extra["levels"]), ("Fibonacci", extra.get("fib"))) if k)
        return draw_chart(t, item["sd"], f"{t} daily" + (f": {what}" if what else ""), show_sma=True,
                          extra=extra)
    return draw_chart(t, item["sh"], f"{t} {m['name']}")


def trade_chart(item, trade):
    """Chart of a paper trade on the timeframe of its signal, with entry/stop/target and reasons."""
    t = item["t"]
    daily = trade["tf"] == "daily"
    frame = item["sd"] if daily else item["sh"]
    name = "daily" if daily else "15-min"
    head = "LONG" if trade["dir"] == "LONG" else "SHORT"
    return draw_chart(t, frame, f"{t} {name}: paper trade {head}", show_sma=daily,
                      n_show=90 if daily else 100, trade=trade)


def headline(events):
    """Overall direction of a stock's alert: STRONG LONG/SHORT, LONG, SHORT or WATCH."""
    strong = [e["dir"] for e in events if e["strong"]]
    if strong:
        return "⭐ STRONG " + ("LONG" if strong[0] == "LONG" else "SHORT"), strong[0]
    dirs = {e["dir"] for e in events} - {"WATCH"}
    if dirs == {"LONG"}:
        return "🟢 LONG", "LONG"
    if dirs == {"SHORT"}:
        return "🔴 SHORT", "SHORT"
    return ("⚪ MIXED" if dirs else "⚪ WATCH"), "WATCH"


def format_alert(ticker, events, ctx, mode, link=None):
    title, _ = headline(events)
    icon = {"LONG": "🟢", "SHORT": "🔴", "WATCH": "⚪"}
    lines = [f"<b>{title}: {ticker}</b>  ${px(ctx['price'])}"]
    if any(e["strong"] for e in events):
        lines.append("<i>Trend (200-day MA) + Donchian breakout + Bollinger confirmation</i>")
    lines += [f"{'⭐' if e['strong'] else icon[e['dir']]} {e['text']}" for e in events]
    if ctx["sma200"]:
        side = "above" if ctx["price"] > ctx["sma200"] else "below"
        pct = (ctx["price"] / ctx["sma200"] - 1) * 100
        lines.append(f"200-day MA {px(ctx['sma200'])}: price {side} ({pct:+.1f}%)")
    if ctx["bb"]:
        lines.append(f"Daily Bollinger {px(ctx['bb'][0])} – {px(ctx['bb'][1])}")
    if ctx["dc"]:
        lines.append(f"Daily Donchian {px(ctx['dc'][0])} – {px(ctx['dc'][1])}")
    if ctx.get("major_sup") or ctx.get("major_res"):
        sup = f"{px(ctx['major_sup'])}" if ctx.get("major_sup") else "none"
        res = f"{px(ctx['major_res'])}" if ctx.get("major_res") else "none"
        lines.append(f"Major support {sup} | major resistance {res}")
    if ctx.get("fib"):
        f = ctx["fib"]
        zone = " / ".join(f"{px(v)}" for _, v in sorted(f["levels"].items()))
        lines.append(f"Fibonacci 38.2/50/61.8% of the last {'rise' if f['up'] else 'drop'}: {zone}")
    link = link or f"https://www.tradingview.com/chart/?symbol={ticker.replace('-', '.')}"
    lines.append(f'<a href="{link}">Open chart</a>')
    lines.append("<i>Possible setup only. Check it yourself before trading.</i>")
    return "\n".join(lines)


def send_long(lines):
    """Send a list of lines as one or more Telegram messages (max 4096 characters each)."""
    ok, chunk = True, ""
    for line in lines:
        if len(chunk) + len(line) > 3900:
            ok &= send_telegram(chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk:
        ok &= send_telegram(chunk)
    return ok


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state, now):
    cutoff = (now - dt.timedelta(days=5)).isoformat()
    # sent-alert keys expire after 5 days; the paper-trade book ("_trades") is kept
    state = {k: v for k, v in state.items() if not isinstance(v, str) or v >= cutoff}
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def log(msg, t0):
    print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)


def main():
    t0 = time.time()
    now = dt.datetime.now(ET)
    force = os.environ.get("ALERT_ANY_TIME") == "1"
    mode = os.environ.get("SCAN_MODE", "1h")
    m = MODES[mode]
    state = load_state()

    tickers = get_tickers()
    log(f"{now:%Y-%m-%d %H:%M} ET: {m['name']} scan of {len(tickers)} tickers"
        + (" (test mode)" if force else ""), t0)
    daily_cutoff = now - dt.timedelta(minutes=DATA_DELAY_MINUTES)
    cutoff = now if INTRADAY_FEED == "iex" else daily_cutoff     # intraday candles are real-time
    daily, intraday = get_prices(tickers, daily_cutoff, mode, now=cutoff)
    snaps = snapshots(tickers)
    log(f"Prices downloaded for {len(daily)} tickers, {len(snaps)} live snapshots", t0)

    # 1) analyze everything first
    found, watch, scanned = [], [], 0
    for n, t in enumerate(tickers, 1):
        if n % 25 == 0:
            log(f"Analyzed {n}/{len(tickers)}", t0)
        if t not in daily or t not in intraday:
            continue
        try:
            h = completed_bars(intraday[t], cutoff, m["minutes"])
            if len(h) < 60:
                continue
            fresh = (in_window(h.index[-1], m) and
                     now - bar_end(h.index[-1], m["minutes"]) <= dt.timedelta(minutes=m["fresh"]))
            live = with_snapshot(daily[t], snaps.get(t), now.date())
            # opening check (9:32): no closed candle today yet -> daily-chart signals with the live price
            opening = not fresh and live is not None and (9, 30) <= (now.hour, now.minute) < (10, 0)
            if not (fresh or opening or force):
                continue
            scanned += 1
            dd = add_indicators(live if live is not None else with_today(daily[t], h))
            events, ctx, sd, sh = rules.evaluate(dd, add_indicators(h), m["name"])
            if live is not None:
                ctx["price"] = float(dd["Close"].iat[-1])
            if opening and not force:
                events = [e for e in events if e["tf"] == "daily"]       # no new candle to judge yet
            # each event once per day (daily chart) or once per candle (intraday chart)
            day_key, bar_key = str(dd.index[-1].date()), f"{mode}|{h.index[-1].isoformat()}"
            for e in events:
                e["key"] = f"{t}|{e['code']}|{e['tf']}|{day_key if e['tf'] == 'daily' else bar_key}"
            events = [e for e in events if e["key"] not in state]
            keys = [e["key"] for e in events]
            pat_hits, forming_any = [], []

            if PATTERNS_ENABLED and (fresh or force):   # chart patterns on the daily and hourly charts
                price, prev = float(h["Close"].iat[-1]), float(h["Close"].iat[-2])
                hv = add_indicators(h).iloc[-1]
                vol_now = float(hv.Volume / hv.VolAvg20) if hv.VolAvg20 else None
                for tf, frame in (("daily", dd), ("hourly", add_indicators(hourly_from(h, mode, cutoff)))):
                    pats, pdf = patterns.find_patterns(frame, tf, price, prev, vol_now)
                    if not pats:
                        continue
                    cfg = rules.SETTINGS["daily" if tf == "daily" else "intraday"]
                    lb = rules.SQUEEZE_LOOKBACK["daily" if tf == "daily" else "intraday"]
                    cframe = rules.add_strategy_indicators(frame, lb, **cfg).tail(patterns.SETTINGS[tf]["lookback"])
                    for pt in pats:
                        if pt["stage"] == "forming":
                            forming_any.append((pt, cframe))    # drawn on this stock's alert chart, if any
                        key = f"{t}|PAT|{pt['id']}|{pt['stage']}"
                        if key in state:
                            continue
                        if pt["stage"] == "breakout":
                            verb = "completed" if pt["name"] in patterns.HARMONICS else "breakout"
                            events.append({"dir": pt["dir"], "code": "pattern", "tf": tf, "strong": False, "key": key,
                                           "text": f"{pt['name']} {verb} ({tf}): entry {px(pt['level'])}, "
                                                   f"stop {px(pt['stop'])}, target {px(pt['target'])}"})
                            pat_hits.append((pt, cframe))
                            keys.append(key)
                        else:
                            watch.append({"t": t, "p": pt, "price": ctx["price"], "key": key})

            if events:
                n_strong = sum(e["strong"] for e in events)
                found.append({"t": t, "events": events, "ctx": ctx, "sd": sd, "sh": sh, "pats": pat_hits,
                              "forming": forming_any,
                              "keys": keys, "score": (n_strong, len(events))})
        except Exception as e:                       # one bad ticker never stops the scan
            print(f"{t}: {e}", flush=True)
    log(f"Checked {scanned} tickers, {len(found)} stocks with new signals"
        + (f", {len(watch)} new patterns forming" if PATTERNS_ENABLED else ""), t0)

    # 2) send the strongest setups with charts, the rest in one summary
    found.sort(key=lambda x: x["score"], reverse=True)
    top, rest = found[:MAX_CHART_ALERTS], found[MAX_CHART_ALERTS:]

    def mark(item):
        for k in item["keys"]:
            state[k] = now.isoformat()

    sent = 0
    for item in top:
        try:
            chart = alert_chart(item, m)
            if send_telegram(format_alert(item["t"], item["events"], item["ctx"], mode), chart):
                mark(item)
                sent += 1
        except Exception as e:
            print(f"{item['t']}: alert failed: {e}", flush=True)
    if rest:
        lines = [f"<b>+{len(rest)} more setups</b> ({m['name']} candles, strongest first)"]
        for item in rest:
            title, _ = headline(item["events"])
            first = next((e for e in item["events"] if e["strong"]), item["events"][0])
            more = f" (+{len(item['events']) - 1} more)" if len(item["events"]) > 1 else ""
            lines.append(f"{title.split()[0]} <b>{item['t']}</b> ${px(item['ctx']['price'])}: {first['text']}{more}")
        if send_long(lines):
            for item in rest:
                mark(item)

    # 3) one watchlist message for patterns that are forming (no breakout yet)
    if watch:
        icon = {"LONG": "🟢", "SHORT": "🔴", "BOTH": "⚪"}
        lines = [f"<b>👀 Patterns forming</b> (watchlist, {len(watch)} new)"]
        for w in watch[:MAX_WATCHLIST]:
            p = w["p"]
            if p["dir"] == "BOTH":
                b1, b2 = p["breaks"]
                where = f"breaks above {px(b1['level'])} or below {px(b2['level'])}"
            elif p["name"] in patterns.HARMONICS:
                where = f"completion zone near {px(p['level'])}"
            else:
                where = f"breakout {'above' if p['dir'] == 'LONG' else 'below'} {px(p['level'])}"
            lines.append(f"{icon[p['dir']]} <b>{w['t']}</b> ${px(w['price'])}: {p['name']} ({p['timeframe']}), {where}")
        if len(watch) > MAX_WATCHLIST:
            lines.append(f"…and {len(watch) - MAX_WATCHLIST} more")
        if send_long(lines):
            for w in watch:
                state[w["key"]] = now.isoformat()

    # 4) paper trading: STRONG signals become bracket orders in the Alpaca paper account
    for line in trader.run(found, state, send_telegram, test_mode=force, chart_fn=trade_chart, daily=daily):
        print(line, flush=True)

    save_state(state, now)
    log(f"Done: {sent} chart alerts sent" + (f", {len(rest)} more in the summary" if rest else ""), t0)
    if scanned == 0 and not force:
        print(f"No fresh {m['name']} candle in this mode's window. Use test mode to scan anyway.")


if __name__ == "__main__":
    main()
