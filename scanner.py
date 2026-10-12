"""
S&P 500 scanner: downloads daily + intraday prices for every S&P 500 stock,
computes indicators, support/resistance levels and trend lines, runs the
rules in rules.py and sends Telegram alerts (with a chart) for possible trades.

Prices come from Alpaca (official API, free plan, full all-exchange data
delayed 15+ minutes), with Yahoo Finance as automatic backup.

Two scan modes (SCAN_MODE env): "15m" uses 15-minute candles for entries (first
3 hours after the open), "1h" uses hourly candles (rest of the day).

Runs on GitHub Actions (see .github/workflows/scan.yml) or locally:
    pip install -r requirements.txt
    python scanner.py            # prints alerts if no Telegram secrets are set
"""
import datetime as dt
import io
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
import requests
import yfinance as yf

import patterns
import rules

ET = ZoneInfo("America/New_York")
TICKERS_URL = ("https://raw.githubusercontent.com/datasets/"
               "s-and-p-500-companies/main/data/constituents.csv")
ALPACA_URL = "https://data.alpaca.markets/v2/stocks/bars"
DATA_DELAY_MINUTES = 16            # Alpaca free plan: full-market data must be 15+ min old
STATE_FILE = "state.json"          # remembers sent alerts so you never get duplicates
DAILY_LOOKBACK = 120               # daily bars used for levels / trend lines (~6 months)
INTRADAY_LOOKBACK = 200            # entry-chart candles used for levels / trend lines

# Entry-chart settings per mode. "first"/"last" = earliest/latest candle START time (ET)
# this mode handles; "fresh" = how recently the candle must have closed to alert on it.
MODES = {
    "15m": {"minutes": 15, "alpaca": "15Min", "days": 21, "yahoo": ("14d", "15m"),
            "first": (9, 30), "last": (12, 15), "fresh": 35, "name": "15-min"},
    "1h":  {"minutes": 60, "alpaca": "30Min", "days": 45, "yahoo": ("60d", "1h"),
            "first": (11, 30), "last": (15, 30), "fresh": 75, "name": "hourly"},
}
PIVOT_WINDOW = 5                   # a swing high/low must be the extreme of +-5 bars
MAX_CHART_ALERTS = 10              # full chart alerts per run; any extra setups go in one summary
MAX_WATCHLIST = 40                 # forming patterns listed per check (the rest are counted)
TELEGRAM_PAUSE = 1.5               # seconds between Telegram messages (avoids rate limits)


# ---------------------------------------------------------------- data
def get_tickers():
    df = pd.read_csv(TICKERS_URL)
    return [s.replace(".", "-") for s in df["Symbol"]]   # BRK.B -> BRK-B for Yahoo


def _alpaca_chunk(part, timeframe, start, end, headers):
    """Download one group of symbols, following Alpaca's pages until done."""
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    params = {"symbols": ",".join(t.replace("-", ".") for t in part),   # BRK-B -> BRK.B
              "timeframe": timeframe, "start": start.strftime(fmt), "end": end.strftime(fmt),
              "limit": 10000, "adjustment": "all", "feed": "sip"}
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


def alpaca_bars(tickers, timeframe, start, end, chunk=25, workers=8):
    """Bars from Alpaca's official API, several groups downloaded at the same time.
    The free plan gives full all-exchange (SIP) data as long as `end` is 15+ minutes old."""
    key, secret = os.environ.get("ALPACA_KEY_ID"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        print("No Alpaca keys set, using Yahoo for everything.")
        return {}
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    parts = [tickers[i:i + chunk] for i in range(0, len(tickers), chunk)]
    out = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for rows in pool.map(lambda p: _alpaca_chunk(p, timeframe, start, end, headers), parts):
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


def get_prices(tickers, cutoff, mode):
    """Alpaca first; any ticker Alpaca can't deliver is fetched from Yahoo instead."""
    m = MODES[mode]
    end = cutoff.astimezone(dt.timezone.utc)
    t0 = time.time()
    daily = alpaca_bars(tickers, "1Day", end - dt.timedelta(days=400), end)
    print(f"Daily bars from Alpaca: {time.time() - t0:.0f}s", flush=True)
    t0 = time.time()
    intraday = {t: to_session(df, m["minutes"]) for t, df in
                alpaca_bars(tickers, m["alpaca"], end - dt.timedelta(days=m["days"]), end).items()}
    print(f"{m['name']} bars from Alpaca: {time.time() - t0:.0f}s", flush=True)
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
def make_chart(ticker, da):
    d = da["df"]
    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=110)
    x = np.arange(len(d))
    ax.plot(x, d["Close"].values, color="#222", lw=1.3, label="Close")
    ax.plot(x, d["EMA50"].values, color="#1f77b4", lw=0.9, label="EMA50")
    ax.plot(x, d["EMA200"].values, color="#9467bd", lw=0.9, label="EMA200")
    for lvl, _ in da["support"][:3]:
        ax.axhline(lvl, color="#2ca02c", lw=0.9, ls="--")
    for lvl, _ in da["resistance"][:3]:
        ax.axhline(lvl, color="#d62728", lw=0.9, ls="--")
    for key, col in (("up_line", "#2ca02c"), ("down_line", "#d62728")):
        ln = da[key]
        if ln:
            xs = np.arange(ln["a"], len(d))
            ax.plot(xs, ln["intercept"] + ln["slope"] * xs, color=col, lw=1.4)
    step = max(1, len(d) // 6)
    ax.set_xticks(x[::step])
    ax.set_xticklabels([ts.strftime("%d %b") for ts in d.index[::step]])
    ax.set_title(f"{ticker} daily: support (green) / resistance (red) / trend lines")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.25)
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def make_pattern_chart(ticker, d, p):
    """Chart of the pattern on its own timeframe: lines, swing points, entry/stop/target."""
    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=110)
    x = np.arange(len(d))
    start = max(0, min(k[0] for k in p["keys"]) - 25)
    ax.vlines(x, d["Low"].values, d["High"].values, color="#bbbbbb", lw=0.7)
    ax.plot(x, d["Close"].values, color="#222", lw=1.2)
    for (x1, y1), (x2, y2) in p["lines"]:
        ax.plot([x1, x2], [y1, y2], color="#ff7f0e", lw=1.8)
    for i, price, label in p["points"]:
        ax.scatter([i], [price], color="#ff7f0e", s=18, zorder=3)
        if label:
            ax.annotate(label, (i, price), textcoords="offset points", xytext=(0, 7), ha="center", fontsize=8)
    levels = ((p["level"], "#1f77b4", "entry"), (p["stop"], "#d62728", "stop"), (p["target"], "#2ca02c", "target"))
    for val, col, lab in levels:
        ax.axhline(val, color=col, ls="--", lw=0.9)
        ax.text(len(d) - 0.5, val, f" {lab} {val:.2f}", color=col, va="center", fontsize=8,
                bbox=dict(facecolor="white", edgecolor="none", pad=1))
    seen = d.iloc[start:]
    lo = min(seen["Low"].min(), *(v for v, _, _ in levels))
    hi = max(seen["High"].max(), *(v for v, _, _ in levels))
    ax.set_xlim(start, len(d) + 8)
    ax.set_ylim(lo - 0.03 * (hi - lo), hi + 0.03 * (hi - lo))
    ts = d[d.columns[0]]
    step = max(1, (len(d) - start) // 6)
    ticks = list(range(start, len(d), step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([pd.Timestamp(ts.iat[i]).strftime("%d %b") for i in ticks])
    ax.set_title(f"{ticker} {p['timeframe']}: {p['name']} ({p['stage']})")
    ax.grid(alpha=0.25)
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
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


def format_alert(ticker, signals, da, ha, mode):
    last = ha["df"].iloc[-1]
    tf = mode
    longs = [s for s in signals if s[0] == "LONG"]
    side = "🟢 LONG" if longs else "🔴 SHORT"
    sup = da["support"][0][0] if da["support"] else None
    res = da["resistance"][0][0] if da["resistance"] else None
    lines = [f"<b>{side} setup: {ticker}</b>  ${last.Close:.2f}",
             f"<i>{MODES[mode]['name']} candle closed {bar_end(ha['df'].index[-1], MODES[mode]['minutes']):%H:%M} New York</i>"]
    lines += [f"• {desc}" for _, desc in signals]
    lines.append(f"RSI({tf}) {last.RSI:.0f} | ATR({tf}) {last.ATR:.2f}")
    lines.append(f"Daily support {sup:.2f}" if sup else "Daily support: none nearby")
    lines.append(f"Daily resistance {res:.2f}" if res else "Daily resistance: none nearby")
    lines.append(f'<a href="https://www.tradingview.com/chart/?symbol={ticker.replace("-", ".")}">Open chart</a>')
    lines.append("<i>Possible setup only. Check it yourself before trading.</i>")
    return "\n".join(lines)


# ---------------------------------------------------------------- main
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
    state = {k: v for k, v in state.items() if v >= cutoff}
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
    cutoff = now - dt.timedelta(minutes=DATA_DELAY_MINUTES)
    daily, intraday = get_prices(tickers, cutoff, mode)
    log(f"Prices downloaded for {len(daily)} tickers", t0)

    # 1) analyze everything first
    found, watch, scanned = [], [], 0
    for n, t in enumerate(tickers, 1):
        if n % 100 == 0:
            log(f"Analyzed {n}/{len(tickers)}", t0)
        if t not in daily or t not in intraday:
            continue
        try:
            h = completed_bars(intraday[t], cutoff, m["minutes"])
            if len(h) < 60:
                continue
            if not force:
                if not in_window(h.index[-1], m):
                    continue
                if now - bar_end(h.index[-1], m["minutes"]) > dt.timedelta(minutes=m["fresh"]):
                    continue
            scanned += 1
            dd = add_indicators(with_today(daily[t], h))
            da = analyze(dd, DAILY_LOOKBACK)
            ha = analyze(add_indicators(h), INTRADAY_LOOKBACK)
            trend = rules.trend_filter(da)
            signals = rules.entry_signals(da, ha, trend)
            bar_key = f"{mode}|{h.index[-1].isoformat()}"
            # one alert per stock, direction and candle (even if a level shifts by a few cents)
            signals = [s for s in signals if f"{t}|{s[0]}|{bar_key}" not in state]
            rule_keys = [f"{t}|{s[0]}|{bar_key}" for s in signals]

            # chart patterns on the daily and hourly charts
            price, prev = float(h["Close"].iat[-1]), float(h["Close"].iat[-2])
            hv = ha["df"].iloc[-1]
            vol_now = float(hv.Volume / hv.VolAvg20) if hv.VolAvg20 else None
            pat_hits, pat_keys = [], []
            for tf, frame in (("daily", dd), ("hourly", add_indicators(hourly_from(h, mode, cutoff)))):
                pats, pdf = patterns.find_patterns(frame, tf, price, prev, vol_now)
                for pt in pats:
                    key = f"{t}|PAT|{pt['id']}|{pt['stage']}"
                    if key in state:
                        continue
                    if pt["stage"] == "breakout":
                        verb = "completed" if pt["name"] in patterns.HARMONICS else "breakout"
                        signals.append((pt["dir"], f"{pt['name']} {verb} ({tf}): entry {pt['level']:.2f}, "
                                                   f"stop {pt['stop']:.2f}, target {pt['target']:.2f}"))
                        pat_hits.append((pt, pdf))
                        pat_keys.append(key)
                    else:
                        watch.append({"t": t, "p": pt, "price": price, "key": key})
            if signals:
                last = ha["df"].iloc[-1]
                vol_ratio = float(last.Volume / last.VolAvg20) if last.VolAvg20 else 0.0
                found.append({"t": t, "signals": signals, "da": da, "ha": ha, "pats": pat_hits,
                              "keys": rule_keys + pat_keys, "score": (len(signals), vol_ratio)})
        except Exception as e:                       # one bad ticker never stops the scan
            print(f"{t}: {e}", flush=True)
    log(f"Checked {scanned} tickers with a fresh bar, {len(found)} setups found, "
        f"{len(watch)} new patterns forming", t0)

    # 2) send the strongest setups with charts, the rest in one summary
    found.sort(key=lambda x: x["score"], reverse=True)
    top, rest = found[:MAX_CHART_ALERTS], found[MAX_CHART_ALERTS:]

    def mark(item):
        for k in item["keys"]:
            state[k] = now.isoformat()

    sent = 0
    for item in top:
        try:
            if item["pats"]:
                pt, pdf = item["pats"][0]
                chart = make_pattern_chart(item["t"], pdf, pt)
            else:
                chart = make_chart(item["t"], item["da"])
            if send_telegram(format_alert(item["t"], item["signals"], item["da"], item["ha"], mode), chart):
                mark(item)
                sent += 1
        except Exception as e:
            print(f"{item['t']}: alert failed: {e}", flush=True)
    if rest:
        lines = [f"<b>+{len(rest)} more setups</b> ({m['name']} candles, strongest first)"]
        for item in rest:
            side = "🟢" if any(s[0] == "LONG" for s in item["signals"]) else "🔴"
            price = item["ha"]["df"]["Close"].iat[-1]
            lines.append(f"{side} <b>{item['t']}</b> ${price:.2f}: {item['signals'][0][1]}")
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
                where = f"breaks above {b1['level']:.2f} or below {b2['level']:.2f}"
            elif p["name"] in patterns.HARMONICS:
                where = f"completion zone near {p['level']:.2f}"
            else:
                where = f"breakout {'above' if p['dir'] == 'LONG' else 'below'} {p['level']:.2f}"
            lines.append(f"{icon[p['dir']]} <b>{w['t']}</b> ${w['price']:.2f}: {p['name']} ({p['timeframe']}), {where}")
        if len(watch) > MAX_WATCHLIST:
            lines.append(f"…and {len(watch) - MAX_WATCHLIST} more")
        if send_long(lines):
            for w in watch:
                state[w["key"]] = now.isoformat()

    save_state(state, now)
    log(f"Done: {sent} chart alerts sent" + (f", {len(rest)} more in the summary" if rest else ""), t0)
    if scanned == 0 and not force:
        print(f"No fresh {m['name']} candle in this mode's window. Use test mode to scan anyway.")


if __name__ == "__main__":
    main()
