"""
S&P 500 scanner: downloads daily + hourly prices for every S&P 500 stock,
computes indicators, support/resistance levels and trend lines, runs the
rules in rules.py and sends Telegram alerts (with a chart) for possible trades.

Prices come from Alpaca (official API, free plan, full all-exchange data
delayed 15+ minutes), with Yahoo Finance as automatic backup.

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

import rules

ET = ZoneInfo("America/New_York")
TICKERS_URL = ("https://raw.githubusercontent.com/datasets/"
               "s-and-p-500-companies/main/data/constituents.csv")
ALPACA_URL = "https://data.alpaca.markets/v2/stocks/bars"
DATA_DELAY_MINUTES = 16            # Alpaca free plan: full-market data must be 15+ min old
STATE_FILE = "state.json"          # remembers sent alerts so you never get duplicates
FRESH_MINUTES = 90                 # only alert on an hourly bar that closed recently
DAILY_LOOKBACK = 120               # daily bars used for levels / trend lines (~6 months)
HOURLY_LOOKBACK = 200              # hourly bars used for levels / trend lines (~1 month)
PIVOT_WINDOW = 5                   # a swing high/low must be the extreme of +-5 bars
MAX_CHART_ALERTS = 10              # full chart alerts per run; any extra setups go in one summary
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


def to_hourly(df30):
    """30-minute bars -> regular-session hourly bars starting at :30 (9:30, 10:30 ... 15:30)."""
    mins = df30.index.hour * 60 + df30.index.minute
    df30 = df30[(mins >= 570) & (mins < 960)]               # 9:30-16:00 ET only
    return (df30.resample("60min", offset="30min")
            .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
            .dropna())


def get_prices(tickers, cutoff):
    """Alpaca first; any ticker Alpaca can't deliver is fetched from Yahoo instead."""
    end = cutoff.astimezone(dt.timezone.utc)
    t0 = time.time()
    daily = alpaca_bars(tickers, "1Day", end - dt.timedelta(days=400), end)
    print(f"Daily bars from Alpaca: {time.time() - t0:.0f}s", flush=True)
    t0 = time.time()
    # 45 days ~ 31 trading days ~ 215 hourly candles, enough for the 200-candle lookback
    hourly = {t: to_hourly(df) for t, df in
              alpaca_bars(tickers, "30Min", end - dt.timedelta(days=45), end).items()}
    print(f"30-minute bars from Alpaca: {time.time() - t0:.0f}s", flush=True)
    daily = {t: d for t, d in daily.items() if len(d) > 60}
    hourly = {t: h for t, h in hourly.items() if len(h) > 60}
    good = [t for t in tickers if t in daily and t in hourly]
    missing = [t for t in tickers if t not in good]
    print(f"Alpaca: {len(good)} tickers OK, {len(missing)} via Yahoo backup", flush=True)
    if missing:
        yd, yh = download(missing, "1y", "1d"), download(missing, "60d", "1h")
        for t in missing:
            if t in yd and t in yh:             # both timeframes from the same source
                daily[t], hourly[t] = yd[t], yh[t]
    return daily, hourly


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


def bar_end(ts):
    """Hourly US bars start at :30 and the last one closes at 16:00 ET."""
    return min(ts + dt.timedelta(hours=1), ts.replace(hour=16, minute=0))


def completed_hourly(df, cutoff):
    df = df.copy()
    df.index = df.index.tz_convert(ET) if df.index.tz else df.index.tz_localize(ET)
    if bar_end(df.index[-1]) > cutoff:       # drop a bar that wasn't finished at the cutoff
        df = df.iloc[:-1]
    return df


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


def format_alert(ticker, signals, da, ha):
    last = ha["df"].iloc[-1]
    longs = [s for s in signals if s[0] == "LONG"]
    side = "🟢 LONG" if longs else "🔴 SHORT"
    sup = da["support"][0][0] if da["support"] else None
    res = da["resistance"][0][0] if da["resistance"] else None
    lines = [f"<b>{side} setup: {ticker}</b>  ${last.Close:.2f}"]
    lines += [f"• {desc}" for _, desc in signals]
    lines.append(f"RSI(1h) {last.RSI:.0f} | ATR(1h) {last.ATR:.2f}")
    lines.append(f"Daily support {sup:.2f}" if sup else "Daily support: none nearby")
    lines.append(f"Daily resistance {res:.2f}" if res else "Daily resistance: none nearby")
    lines.append(f'<a href="https://www.tradingview.com/chart/?symbol={ticker.replace("-", ".")}">Open chart</a>')
    lines.append("<i>Possible setup only. Check it yourself before trading.</i>")
    return "\n".join(lines)


# ---------------------------------------------------------------- main
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
    state = load_state()

    tickers = get_tickers()
    log(f"{now:%Y-%m-%d %H:%M} ET: scanning {len(tickers)} tickers"
        + (" (test mode)" if force else ""), t0)
    cutoff = now - dt.timedelta(minutes=DATA_DELAY_MINUTES)
    daily, hourly = get_prices(tickers, cutoff)
    log(f"Prices downloaded for {len(daily)} tickers", t0)

    # 1) analyze everything first
    found, scanned = [], 0
    for n, t in enumerate(tickers, 1):
        if n % 100 == 0:
            log(f"Analyzed {n}/{len(tickers)}", t0)
        if t not in daily or t not in hourly:
            continue
        try:
            h = completed_hourly(hourly[t], cutoff)
            if len(h) < 60:
                continue
            if not force and now - bar_end(h.index[-1]) > dt.timedelta(minutes=FRESH_MINUTES):
                continue
            scanned += 1
            da = analyze(add_indicators(daily[t].copy()), DAILY_LOOKBACK)
            ha = analyze(add_indicators(h), HOURLY_LOOKBACK)
            trend = rules.trend_filter(da)
            signals = rules.entry_signals(da, ha, trend)
            bar_key = h.index[-1].isoformat()
            signals = [s for s in signals if f"{t}|{s[1]}|{bar_key}" not in state]
            if signals:
                last = ha["df"].iloc[-1]
                vol_ratio = float(last.Volume / last.VolAvg20) if last.VolAvg20 else 0.0
                found.append({"t": t, "signals": signals, "da": da, "ha": ha,
                              "bar_key": bar_key, "score": (len(signals), vol_ratio)})
        except Exception as e:                       # one bad ticker never stops the scan
            print(f"{t}: {e}", flush=True)
    log(f"Checked {scanned} tickers with a fresh bar, {len(found)} setups found", t0)

    # 2) send the strongest setups with charts, the rest in one summary
    found.sort(key=lambda x: x["score"], reverse=True)
    top, rest = found[:MAX_CHART_ALERTS], found[MAX_CHART_ALERTS:]

    def mark(item):
        for s in item["signals"]:
            state[f"{item['t']}|{s[1]}|{item['bar_key']}"] = now.isoformat()

    sent = 0
    for item in top:
        try:
            chart = make_chart(item["t"], item["da"])
            if send_telegram(format_alert(item["t"], item["signals"], item["da"], item["ha"]), chart):
                mark(item)
                sent += 1
        except Exception as e:
            print(f"{item['t']}: alert failed: {e}", flush=True)
    if rest:
        lines = [f"<b>+{len(rest)} more setups this hour</b> (strongest first)"]
        for item in rest:
            side = "🟢" if any(s[0] == "LONG" for s in item["signals"]) else "🔴"
            price = item["ha"]["df"]["Close"].iat[-1]
            lines.append(f"{side} <b>{item['t']}</b> ${price:.2f}: {item['signals'][0][1]}")
        text, chunk = "\n".join(lines), ""
        ok = True
        for line in text.split("\n"):              # Telegram messages max 4096 characters
            if len(chunk) + len(line) > 3900:
                ok &= send_telegram(chunk)
                chunk = ""
            chunk += line + "\n"
        if chunk:
            ok &= send_telegram(chunk)
        if ok:
            for item in rest:
                mark(item)

    save_state(state, now)
    log(f"Done: {sent} chart alerts sent" + (f", {len(rest)} more in the summary" if rest else ""), t0)
    if scanned == 0 and not force:
        print("No fresh hourly bar (market closed?). Use test mode to scan anyway.")


if __name__ == "__main__":
    main()
