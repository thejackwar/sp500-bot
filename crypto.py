"""
Crypto data for crypto_scanner.py and the crypto backtest.

Coins: the 10 biggest cryptocurrencies by market cap that make sense to trade and that Alpaca
can trade against USD. Stablecoins (USDT, USDC...) and gold tokens are skipped because their price
doesn't move; coins Alpaca doesn't list (e.g. BNB, TRX) are skipped because they can't be paper traded.
The ranking is read from CoinGecko every run (CoinPaprika as backup, then a built-in list).

Prices: Alpaca's crypto data (24/7, real-time, free), Yahoo Finance as backup.
Crypto never closes, so a "day" is a calendar day in UTC (00:00-24:00 UTC = 02:00-02:00 Milan in
summer, 01:00-01:00 in winter), the same daily candle most crypto charts and TradingView show.
"""
import datetime as dt
import os
import time

import pandas as pd
import requests

TOP_N = 10
BARS_URL = "https://data.alpaca.markets/v1beta3/crypto/us/bars"
LATEST_URL = "https://data.alpaca.markets/v1beta3/crypto/us/latest/trades"
ASSETS_URL = "https://paper-api.alpaca.markets/v2/assets"
COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/markets"
COINPAPRIKA_URL = "https://api.coinpaprika.com/v1/tickers"

# Never traded: stablecoins and tokens that follow gold (their price barely moves)
SKIP = {"USDT", "USDC", "USDG", "DAI", "USDE", "USDS", "FDUSD", "PYUSD", "TUSD", "USD1", "RLUSD", "USDD",
        "USDP", "GUSD", "FRAX", "LUSD", "EURC", "USDF", "USDTB", "BUIDL", "PAXG", "XAUT"}
# Backups used only if a website can't be read:
# coins Alpaca trades against USD (Alpaca's list, March 2026) ...
ALPACA_USD_BACKUP = """AAVE ADA ARB AVAX BAT BCH BONK BTC CRV DOGE DOT ETH FIL GRT HYPE LDO LINK LTC ONDO
PAXG PEPE POL RENDER SHIB SKY SOL SUSHI TRUMP UNI USDC USDG USDT WIF XRP XTZ YFI""".split()
# ... and the market-cap ranking of the biggest of them (approximate, autumn 2026)
RANKING_BACKUP = "BTC ETH XRP SOL HYPE DOGE ADA LINK BCH AVAX LTC DOT SHIB UNI".split()


def _keys():
    key, secret = os.environ.get("ALPACA_KEY_ID"), os.environ.get("ALPACA_SECRET_KEY")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret} if key and secret else {}


def to_alpaca(coin):
    return f"{coin}/USD"


def coin_of(symbol):
    """'BTC/USD', 'BTCUSD' or 'BTC-USD' -> 'BTC'."""
    s = symbol.upper().replace("/", "").replace("-", "")
    return s[:-3] if s.endswith("USD") else s


def tradingview(symbol, interval=None):
    link = f"https://www.tradingview.com/chart/?symbol={coin_of(symbol)}USD"
    return link + (f"&interval={interval}" if interval else "")


# ---------------------------------------------------------------- which coins
def market_cap_ranking():
    """Coin symbols, biggest market cap first."""
    try:
        r = requests.get(COINGECKO_URL, timeout=20, params={"vs_currency": "usd", "order": "market_cap_desc",
                                                            "per_page": 100, "page": 1})
        r.raise_for_status()
        out = [c["symbol"].upper() for c in r.json()]
        if len(out) >= 20:
            return out, "CoinGecko"
    except Exception as e:
        print(f"Couldn't read the ranking from CoinGecko ({e})", flush=True)
    try:
        r = requests.get(COINPAPRIKA_URL, timeout=20)
        r.raise_for_status()
        rows = sorted((c for c in r.json() if c.get("rank")), key=lambda c: c["rank"])
        out = [c["symbol"].upper() for c in rows[:150]]
        if len(out) >= 20:
            return out, "CoinPaprika"
    except Exception as e:
        print(f"Couldn't read the ranking from CoinPaprika ({e})", flush=True)
    return list(RANKING_BACKUP), "built-in list"


def alpaca_coins():
    """Coins Alpaca can trade against USD right now."""
    h = _keys()
    if h:
        try:
            r = requests.get(ASSETS_URL, headers=h, timeout=20, params={"asset_class": "crypto", "status": "active"})
            r.raise_for_status()
            out = {a["symbol"][:-4] for a in r.json() if a["symbol"].endswith("/USD") and a.get("tradable")}
            if out:
                return out
        except Exception as e:
            print(f"Couldn't read Alpaca's coin list ({e}); using the built-in list", flush=True)
    return set(ALPACA_USD_BACKUP)


def top_coins(n=TOP_N):
    """The n biggest tradable coins as Alpaca symbols ('BTC/USD', ...), biggest first."""
    ranking, source = market_cap_ranking()
    tradable = alpaca_coins()
    out, skipped = [], []
    for c in ranking:
        if c in out:
            continue
        if c in SKIP or c.startswith("USD") or c.endswith("USD") or c not in tradable:
            if len(out) < n:
                skipped.append(c)
            continue
        out.append(c)
        if len(out) == n:
            break
    print(f"Top {len(out)} coins ({source}): {' '.join(out)}"
          + (f"  | skipped: {' '.join(skipped)} (stablecoin or not on Alpaca)" if skipped else ""), flush=True)
    return [to_alpaca(c) for c in out]


# ---------------------------------------------------------------- prices
def _frame(bars):
    df = pd.DataFrame(bars)
    df.index = pd.DatetimeIndex(pd.to_datetime(df["t"], utc=True))
    df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
    return df[["Open", "High", "Low", "Close", "Volume"]].astype(float)


def bars(symbols, timeframe, start, end):
    """Candles from Alpaca for several coins: {'BTC/USD': DataFrame (UTC times)}.
    timeframe: '15Min', '1Hour', '1Day'... Works without keys too (lower rate limit)."""
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    params = {"symbols": ",".join(symbols), "timeframe": timeframe, "limit": 10000,
              "start": start.astimezone(dt.timezone.utc).strftime(fmt),
              "end": end.astimezone(dt.timezone.utc).strftime(fmt)}
    rows = {}
    while True:
        r = None
        for _ in range(8):
            try:
                r = requests.get(BARS_URL, headers=_keys(), params=params, timeout=60)
            except requests.RequestException:
                time.sleep(5)
                continue
            if r.status_code == 429:                      # too many requests: wait and retry
                time.sleep(10)
                continue
            break
        if r is None or not r.ok:
            raise RuntimeError(f"Alpaca crypto bars: {r.status_code if r is not None else 'no connection'} "
                               f"{r.text[:200] if r is not None else ''}")
        js = r.json()
        for sym, b in (js.get("bars") or {}).items():
            rows.setdefault(sym, []).extend(b)
        if not js.get("next_page_token"):
            break
        params["page_token"] = js["next_page_token"]
    out = {}
    for sym, b in rows.items():
        df = _frame(b)
        out[sym] = df[~df.index.duplicated(keep="last")].sort_index()
    return out


def yahoo(symbols, period, interval):
    """Yahoo Finance backup ('BTC/USD' -> 'BTC-USD'). Times in UTC."""
    import yfinance as yf
    out = {}
    for sym in symbols:
        try:
            df = yf.download(sym.replace("/", "-"), period=period, interval=interval, auto_adjust=True,
                             progress=False, multi_level_index=False)
            df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
            if len(df):
                df.index = df.index.tz_localize("UTC") if df.index.tz is None else df.index.tz_convert("UTC")
                out[sym] = df.astype(float)
        except Exception as e:
            print(f"{sym}: Yahoo backup failed ({e})", flush=True)
        time.sleep(0.5)
    return out


def daily_long(symbol, start):
    """Daily candles (UTC days, index = UTC midnight) from `start`, for history older than Alpaca's (Alpaca's
    crypto history starts in 2021). Yahoo Finance first, Coinbase as a backup (both have BTC from 2014-15)."""
    start = pd.Timestamp(start).tz_localize("UTC") if pd.Timestamp(start).tz is None else pd.Timestamp(start).tz_convert("UTC")
    y = yahoo([symbol], "max", "1d").get(symbol)
    if y is not None and len(y) and y.index[0] <= start + pd.Timedelta(days=5):
        y.index = y.index.normalize()
        print(f"{symbol}: {len(y)} daily candles from Yahoo, from {y.index[0]:%Y-%m-%d}", flush=True)
        return y[y.index >= start]
    print(f"{symbol}: Yahoo history unavailable or too short; trying Coinbase", flush=True)
    rows, t = [], start
    end = pd.Timestamp.now(tz="UTC")
    while t < end:                                   # Coinbase: 300 candles per request
        t2 = min(t + pd.Timedelta(days=299), end)
        r = requests.get(f"https://api.exchange.coinbase.com/products/{symbol.replace('/', '-')}/candles", timeout=30,
                         params={"granularity": 86400, "start": t.isoformat(), "end": t2.isoformat()})
        r.raise_for_status()
        rows += r.json()                             # [time, low, high, open, close, volume]
        t = t2 + pd.Timedelta(days=1)
        time.sleep(0.4)
    df = pd.DataFrame(rows, columns=["t", "Low", "High", "Open", "Close", "Volume"]).drop_duplicates("t")
    df.index = pd.to_datetime(df.pop("t"), unit="s", utc=True)
    df = df.sort_index()[["Open", "High", "Low", "Close", "Volume"]].astype(float)
    print(f"{symbol}: {len(df)} daily candles from Coinbase, from {df.index[0]:%Y-%m-%d}", flush=True)
    return df


def resample(df, rule):
    """Bigger candles from smaller ones, aligned to UTC (days start at 00:00 UTC, 4-hour candles at
    00:00, 04:00 ...)."""
    return (df.resample(rule).agg({"Open": "first", "High": "max", "Low": "min", "Close": "last",
                                   "Volume": "sum"}).dropna())


def daily_from(df):
    """Daily candles (UTC days) with plain dates as the index, like the stock bot's daily candles."""
    d = resample(df, "1D")
    d.index = d.index.tz_localize(None).normalize()
    return d


def completed(df, minutes, now):
    """Drop the last candle if it hadn't finished by `now`."""
    if len(df) and df.index[-1] + pd.Timedelta(minutes=minutes) > now:
        return df.iloc[:-1]
    return df


def latest_prices(symbols):
    """Real-time last trade per coin: {'BTC/USD': price}."""
    try:
        r = requests.get(LATEST_URL, headers=_keys(), params={"symbols": ",".join(symbols)}, timeout=20)
        r.raise_for_status()
        return {s: float(t["p"]) for s, t in (r.json().get("trades") or {}).items()}
    except Exception as e:
        print(f"Couldn't read live crypto prices ({e})", flush=True)
        return {}


# ---------------------------------------------------------------- backtest history
def _cached(sym, start, end, cache_dir):
    """15-minute candles of one coin, saved in cache_dir so later runs only download the new days."""
    import pandas as pd
    path = os.path.join(cache_dir, f"crypto_{coin_of(sym)}_15Min.pkl.gz")
    old = None
    try:
        old = pd.read_pickle(path)
    except Exception:
        pass
    if old is not None and len(old) > 1 and pd.Timestamp(old.attrs.get("from", "2100-01-01T00:00:00+00:00")) <= pd.Timestamp(start):
        new = bars([sym], "15Min", old.index[-1].to_pydatetime(), end).get(sym)
        df = old if new is None else pd.concat([old.iloc[:-1], new])     # the last saved candle may have been unfinished
        df = df[~df.index.duplicated(keep="last")].sort_index()
        df.attrs["from"], df.attrs["fresh"] = old.attrs["from"], False
    else:
        df = bars([sym], "15Min", start, end).get(sym)
        if df is None:
            return None
        df.attrs["from"], df.attrs["fresh"] = pd.Timestamp(start).isoformat(), True
    df.to_pickle(path)
    return df


def history(coins, start, end, cache_dir="price-cache", workers=4):
    """Backtest prices: {coin: daily candles}, {coin: 15-minute candles (UTC)}.
    Daily candles start 450 days earlier (200-day MA and 1-year levels on the first test day)."""
    from concurrent.futures import ThreadPoolExecutor
    os.makedirs(cache_dir, exist_ok=True)
    t0 = time.time()
    first = start - dt.timedelta(days=450)

    def one(sym):
        try:
            return sym, _cached(sym, first, end, cache_dir), None
        except Exception as e:
            return sym, None, e

    daily, intraday, full = {}, {}, 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for sym, df, err in pool.map(one, coins):
            if err or df is None:
                print(f"{sym}: download failed ({err or 'no prices'})", flush=True)
                continue
            d = daily_from(df[["Open", "High", "Low", "Close", "Volume"]])
            if len(d) <= 250:
                print(f"{sym}: only {len(d)} days of prices, left out (needs 250+)", flush=True)
                continue
            daily[sym] = d
            intraday[sym] = df[df.index >= pd.Timestamp(start) - pd.Timedelta(days=21)][["Open", "High", "Low", "Close", "Volume"]]
            full += bool(df.attrs.get("fresh"))
    print(f"Crypto prices ready in {time.time() - t0:.0f}s: {full} coins downloaded in full, "
          f"{len(intraday) - full} from the saved copy + new days", flush=True)
    return daily, intraday
