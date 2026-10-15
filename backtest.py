"""
Backtest of the bot's paper-trading strategy on the S&P 100.

It replays the past few years check by check (every 15 minutes, like the live bot) with the SAME
rules and settings the bot uses today (rules.py and trader.py):
    ⭐ STRONG LONG  = price above the 200-day MA + Donchian breakout up + Bollinger confirms
    ⭐ STRONG SHORT = the mirror image
    daily chart (Donchian 20, Bollinger 2.0, checked with the live price, once per day) and
    15-minute chart (Donchian 55, Bollinger 2.5, every candle)
    -> market entry at the next price, stop 2 x daily ATR, target 2:1, risk 1% of equity,
       max 20% of equity per stock, max 10 open positions, max 3 new per check,
       one trade per stock per day.
Fills: entries and stops get a small slippage; a stop or target that the price gaps through fills
at the opening price; if a 15-minute candle touches both the stop and the target, the stop is
assumed to be hit first (the cautious assumption).

Run it on GitHub: Actions -> Backtest -> Run workflow. The results arrive on Telegram
(summary, equity chart, list of trades) and are saved with the run as "backtest-results".
"""
import datetime as dt
import html
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests

import rules
import trader

from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

START_EQUITY = 100_000      # Alpaca paper accounts start with $100,000
SLIPPAGE = 0.0002           # 0.02% worse price on market fills (entries and stops)
MARGIN = 2.0                # buying power = 2 x equity minus what is already invested
MARGIN_RATE = 0.065         # yearly interest on borrowed money (when more than 100% is invested)
BENCHMARK = "SPY"
BARS_URL = "https://data.alpaca.markets/v2/stocks/{}/bars"
OUT = "backtest-results"
RUNS = 10                   # each version is tested from this many start dates...
RUN_STEP_DAYS = 5           # ...one week (5 trading days) apart; the report shows the middle and the range
CACHE_DIR = "price-cache"           # saved prices (kept between runs by the workflow)

VARIANTS = {                # each one changes the bot's rules (see simulate() for the options)
    "both":    ("1. Longs + shorts (old rules)", {"shorts": True}),
    "long":    ("2. Longs only, 2:1 target", {"shorts": False}),
    "trail":   ("3. Longs only + trailing exit", {"shorts": False, "exit": "trail"}),
    "long1x":  ("4. Longs only, no leverage", {"shorts": False, "margin": 1.0}),
    "trail1x": ("5. Longs + trailing exit, no leverage (bot now)", {"shorts": False, "exit": "trail", "margin": 1.0}),
}
SHORT = {"both": "1 L+S", "long": "2 Long", "trail": "3 Trail", "long1x": "4 L 1x", "trail1x": "5 T 1x"}

# What is tested (main() switches these to crypto when MARKET=crypto, see use_crypto())
T = trader                  # money rules: trader.py (stocks) or crypto_trader.py (crypto)
FEE = 0.0                   # trading fee per buy and per sell, as a share of the trade
UNIVERSE, UNIT = "S&amp;P 100", "stocks"
MARKET = "stocks"           # strategy files can be limited to one market with MARKETS = ("crypto",)
FILTER_MA = 200             # market trend filter: benchmark (SPY / BTC) above its N-day average
DAYS_PER_YEAR = 252
OPENING_CHECK = True        # stocks: a check at 9:30 with the opening price
OVERNIGHT_FILLS = False     # crypto never closes: a signal at a day's last candle fills at the next candle
FOOTER = ("Today's S&amp;P 100 members only (flatters results); no short-borrow fees; fills at the "
          "next 15-min open with 0.02% slippage.")
CRYPTO_VARIANTS = {
    "long":  ("1. Longs only, 2:1 target", {"shorts": False, "margin": 1.0}),
    "trail": ("2. Longs + trailing exit (bot now)", {"shorts": False, "exit": "trail", "margin": 1.0}),
    "both":  ("3. Longs + shorts, trailing (test only: Alpaca can't short crypto)",
              {"shorts": True, "exit": "trail", "margin": 1.0}),
}
CRYPTO_SHORT = {"long": "1 Long", "trail": "2 Trail", "both": "3 L+S"}


def use_crypto():
    """Switch the backtest to the crypto bot: top 10 coins, crypto_trader.py's money rules, Alpaca's
    0.25% fee per buy and sell, 0.05% slippage, no borrowing, BTC as the buy-and-hold benchmark,
    UTC days, no opening check (crypto trades around the clock)."""
    global T, FEE, SLIPPAGE, MARGIN, BENCHMARK, RUN_STEP_DAYS, UNIVERSE, UNIT, DAYS_PER_YEAR
    global OPENING_CHECK, OVERNIGHT_FILLS, VARIANTS, SHORT, FOOTER, MARKET, FILTER_MA
    import crypto_trader
    T, FEE, SLIPPAGE, MARGIN = crypto_trader, crypto_trader.FEE, 0.0005, 1.0
    BENCHMARK, RUN_STEP_DAYS, DAYS_PER_YEAR = "BTC", 7, 365
    UNIVERSE, UNIT = "top 10 crypto", "coins"
    OPENING_CHECK, OVERNIGHT_FILLS = False, True
    MARKET, FILTER_MA = "crypto", 50          # BTC filter: BTC above its 50-day average (crypto turns fast)
    VARIANTS, SHORT = CRYPTO_VARIANTS, CRYPTO_SHORT
    FOOTER = ("Today's top 10 coins only (coins that dropped out aren't tested, which flatters results); "
              f"fees {crypto_trader.FEE:.2%} per buy and sell; fills at the next 15-min open with 0.05% slippage.")


def _round(x):
    return T.round_price(x) if hasattr(T, "round_price") else T._r2(x)


# ---------------------------------------------------------------- data
def _headers():
    key, secret = os.environ.get("ALPACA_KEY_ID"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        sys.exit("ALPACA_KEY_ID / ALPACA_SECRET_KEY are not set (add them as repository secrets).")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def fetch(sym, timeframe, start, end, headers):
    """All bars for one stock (split-adjusted). Intraday: regular session only (9:30-16:00)."""
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    params = {"timeframe": timeframe, "start": start.strftime(fmt), "end": end.strftime(fmt),
              "limit": 10000, "adjustment": "all", "feed": "sip"}
    frames = []
    while True:
        r = None
        for attempt in range(10):
            try:
                r = requests.get(BARS_URL.format(sym.replace("-", ".")), headers=headers, params=params, timeout=60)
            except requests.RequestException:
                time.sleep(5)
                continue
            if r.status_code == 429:                      # too many requests: wait and retry
                time.sleep(15)
                continue
            break
        if r is None or not r.ok:
            raise RuntimeError(f"{sym}: {r.status_code if r is not None else 'no connection'} "
                               f"{r.text[:200] if r is not None else ''}")
        js = r.json()
        bars = js.get("bars") or []
        if bars:
            df = pd.DataFrame(bars)
            df.index = pd.DatetimeIndex(pd.to_datetime(df["t"], utc=True)).tz_convert(ET)
            df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
            cols = ["Open", "High", "Low", "Close"] + (["Volume"] if timeframe == "1Day" and "Volume" in df else [])
            df = df[cols].astype(float)
            if timeframe != "1Day":
                mins = df.index.hour * 60 + df.index.minute
                df = df[(mins >= 570) & (mins < 960)]
            frames.append(df)
        if not js.get("next_page_token"):
            break
        params["page_token"] = js["next_page_token"]
    if not frames:
        return None
    df = pd.concat(frames)
    df = df[~df.index.duplicated()].sort_index()
    if timeframe == "1Day":
        df.index = df.index.tz_localize(None).normalize()
    return df


def _load(path):
    try:
        return pd.read_pickle(path)
    except Exception:
        return None


def cached_fetch(sym, timeframe, start, end, headers):
    """Bars from the saved copy, downloading only what is new since the last run.
    The whole history is downloaded again when the saved copy doesn't reach back far enough, or when
    Alpaca has re-adjusted old prices (after a split or dividend the last saved candle no longer matches)."""
    path = os.path.join(CACHE_DIR, f"{sym}_{timeframe}.pkl.gz")
    old = _load(path)
    if old is not None and timeframe == "1Day" and "Volume" not in old:
        old = None                                     # saved before volume was kept: download again (small)
    if old is not None and len(old) > 1 and old.attrs.get("from") and pd.Timestamp(old.attrs["from"]) <= pd.Timestamp(start):
        last = old.index[-1]
        since = (last.tz_localize(ET) if last.tzinfo is None else last).tz_convert("UTC").to_pydatetime()
        new = fetch(sym, timeframe, since, end, headers)
        same = new is not None and last in new.index and \
            abs(new.at[last, "Close"] / old.at[last, "Close"] - 1) < 2e-4
        if new is None or same:
            df = old if new is None else pd.concat([old.iloc[:-1], new])   # the last saved candle may have been unfinished
            df = df[~df.index.duplicated(keep="last")].sort_index()
            df.attrs["from"] = old.attrs["from"]
            df.attrs["fresh"] = False
            df.to_pickle(path)
            return df
    df = fetch(sym, timeframe, start, end, headers)
    if df is not None:
        df.attrs["from"] = pd.Timestamp(start).isoformat()
        df.attrs["fresh"] = True
        df.to_pickle(path)
    return df


def download(tickers, start, end, workers=10):
    """Prices for every stock. Saved in price-cache/ (kept by GitHub between runs), so only the first
    run downloads the whole history; later runs add the days since the previous run."""
    headers = _headers()
    os.makedirs(CACHE_DIR, exist_ok=True)
    t0 = time.time()
    daily, intraday = {}, {}
    full = 0

    def one(sym):
        try:
            d = cached_fetch(sym, "1Day", start - dt.timedelta(days=450), end, headers)
            h = cached_fetch(sym, "15Min", start - dt.timedelta(days=21), end, headers)
            return sym, d, h, None
        except Exception as e:
            return sym, None, None, e

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for n, (sym, d, h, err) in enumerate(pool.map(one, tickers), 1):
            if err:
                print(f"{sym}: download failed ({err})", flush=True)
            elif d is not None and h is not None and len(d) > 250 and len(h) > 500:
                cut = pd.Timestamp(start).tz_convert(ET) - pd.Timedelta(days=21)
                daily[sym] = d[d.index >= (cut - pd.Timedelta(days=430)).tz_localize(None)]
                intraday[sym] = h[h.index >= cut]
                full += bool(h.attrs.get("fresh"))
            if n % 10 == 0:
                print(f"Prices {n}/{len(tickers)} ({time.time() - t0:.0f}s)", flush=True)
    print(f"Prices ready in {time.time() - t0:.0f}s: {full} stocks downloaded in full, "
          f"{len(intraday) - full} from the saved copy + new days", flush=True)
    return daily, intraday


# ---------------------------------------------------------------- signals (same rules as rules.py)
def daily_context(D):
    """What is known about each day BEFORE it opens (row d = values from the completed days up to d-1),
    so today's indicators can be finished with the live price, exactly like the bot does."""
    c, hi, lo = D["Close"], D["High"], D["Low"]
    n = rules.BB_PERIOD
    std, dn = rules.SETTINGS["daily"]["bb_std"], rules.SETTINGS["daily"]["donchian"]
    lb = rules.SQUEEZE_LOOKBACK["daily"]
    tr = pd.concat([hi - lo, (hi - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    mid, sd = c.rolling(n).mean(), c.rolling(n).std(ddof=0)
    width = 2 * std * sd / mid
    squeeze = (width <= width.rolling(lb).min().shift(1)).astype(float)
    dch, dcl = hi.rolling(dn).max(), lo.rolling(dn).min()
    known = pd.DataFrame({
        "pc": c,                                             # yesterday's close
        "atr": tr.ewm(alpha=1 / 14, adjust=False).mean(),
        "s_sma": c.rolling(rules.SMA_LONG - 1).sum(),        # + today's price = 200-day MA
        "s_bb": c.rolling(n - 1).sum(), "s_bb2": (c * c).rolling(n - 1).sum(),
        "dch": dch, "dcl": dcl,                              # today's Donchian channel
        "up_ok": (c <= dch.shift(1)).astype(float),          # yesterday closed inside the channel
        "dn_ok": (c >= dcl.shift(1)).astype(float),
        "minw": width.rolling(lb).min(),                     # narrowest band width of the last 125 days
        "sq_recent": squeeze.rolling(rules.SQUEEZE_RECENT).max(),
    })
    return known.shift(1)


def stock_signals(h, D, debug=False):
    """Every STRONG signal of one stock. Returns one row per check that has one:
    check (time of the check), fill (start of the candle the order fills on), dl/ds/il/is (daily or
    intraday long/short strong), atr (daily ATR incl. today, used for the stop), price."""
    day = pd.DatetimeIndex(h.index.tz_localize(None).normalize())
    H = h["High"].groupby(day).cummax().values
    L = h["Low"].groupby(day).cummin().values
    n = len(h)
    pos = np.arange(n)
    same_next = np.r_[day[1:] == day[:-1], False]
    first = np.r_[True, day[1:] != day[:-1]]
    at_open = first & (h.index.hour == 9) & (h.index.minute == 30) & OPENING_CHECK

    # intraday chart (15-min): rules.py's own indicator code
    s = rules.SETTINGS["intraday"]
    hi = rules.add_strategy_indicators(h, rules.SQUEEZE_LOOKBACK["intraday"], **s)
    c, dch, dcl = hi["Close"], hi["DC_high"], hi["DC_low"]
    recent = hi["Squeeze"].astype(float).rolling(rules.SQUEEZE_RECENT + 1, min_periods=1).max() > 0
    i_up = ((c.shift() <= dch.shift()) & (c > dch) & ((c > hi["BB_up"]) | recent)).values
    i_dn = ((c.shift() >= dcl.shift()) & (c < dcl) & ((c < hi["BB_lo"]) | recent)).values

    # checks: the opening check (9:30, live price = first trade) and one after every candle closes
    op = np.flatnonzero(at_open)
    chk = pd.DataFrame({
        "check": np.r_[h.index[op], h.index + pd.Timedelta(minutes=15)],
        "day": np.r_[day[op], day],
        "p": np.r_[h["Open"].values[op], h["Close"].values],
        "H": np.r_[h["Open"].values[op], H],
        "L": np.r_[h["Open"].values[op], L],
        "fill": np.r_[op, np.where(same_next | (OVERNIGHT_FILLS & (pos + 1 < n)), pos + 1, -1)],   # -1 = market closed
        "i_up": np.r_[np.zeros(len(op), bool), i_up],
        "i_dn": np.r_[np.zeros(len(op), bool), i_dn],
    }).sort_values("check", kind="stable").reset_index(drop=True)
    k = daily_context(D).reindex(chk["day"]).reset_index(drop=True)

    p = chk["p"]
    sma = (k["s_sma"] + p) / rules.SMA_LONG
    mid = (k["s_bb"] + p) / rules.BB_PERIOD
    sd = np.sqrt(((k["s_bb2"] + p * p) / rules.BB_PERIOD - mid * mid).clip(lower=0))
    bstd = rules.SETTINGS["daily"]["bb_std"]
    up, lo = mid + bstd * sd, mid - bstd * sd
    sq_today = ((up - lo) / mid) <= k["minw"]
    confirm_up = (p > up) | sq_today | (k["sq_recent"] > 0)
    confirm_dn = (p < lo) | sq_today | (k["sq_recent"] > 0)
    above, below = p > sma, p < sma

    # daily Donchian breakout: alerted once per day, at the first check it happens
    d_up = (k["up_ok"] > 0) & (p > k["dch"])
    d_dn = (k["dn_ok"] > 0) & (p < k["dcl"])
    d_up &= ~d_up.groupby(chk["day"]).cumsum().gt(1)
    d_dn &= ~d_dn.groupby(chk["day"]).cumsum().gt(1)

    tr = np.maximum.reduce([chk["H"] - chk["L"], (chk["H"] - k["pc"]).abs(), (chk["L"] - k["pc"]).abs()])
    out = pd.DataFrame({
        "check": chk["check"], "fill": chk["fill"], "price": p,
        "dl": d_up & above & confirm_up, "ds": d_dn & below & confirm_dn,
        "il": chk["i_up"] & above, "is": chk["i_dn"] & below,
        "atr": k["atr"] + (tr - k["atr"]) / 14,
    })
    if debug:                                        # every check with its indicator values (tests)
        return out.assign(sma=sma, up=up, lo=lo, sq=sq_today | (k["sq_recent"] > 0), dch=k["dch"], dcl=k["dcl"])
    out = out[out[["dl", "ds", "il", "is"]].any(axis=1)]
    out = out[out["fill"] >= 0].copy()
    out["fill"] = h.index[out["fill"].values]
    return out


# ---------------------------------------------------------------- portfolio simulation
def simulate(sig, O, H, L, C, slots, syms, cfg=None, extra=None):
    """Replays the trader's rules (trader.py) over time. sig: all signals with 'slot' and 'col'
    (optional 'score': higher = preferred when several signals arrive together).
    cfg (all optional; the defaults are the bot's rules):
        kinds       ("daily", "intraday"): which signals open trades
        shorts      T.ALLOW_SHORTS
        exit        "target" = 2:1 target; "trail" = no target, sell at the last check of the day
                    (~15:45) when the price is below the 20-day low; "custom" = use the keys below
        stop_atr    T.STOP_ATR: stop distance in daily ATRs (also sizes the trade: 1% risk)
        target_r    take profit at this many times the stop distance (None = no target)
        trail_days  sell at ~15:45 when the price is below the lowest low of this many days (None = off)
        max_days    sell at the open after this many trading days (None = off)
        exit_matrix bool [day, stock]: True at a day's close = sell at the next day's open
        margin      MARGIN: total invested can reach margin x equity (1.0 = no borrowing, like SPY)
        seed        None = ties between signals broken alphabetically; a number = in random order
        spy_filter  False; True = longs only while SPY closed above its 200-day average the day
                    before, shorts only while it closed below
    extra: day index per slot, N-day lows/highs per day and stock, SPY trend per day (from run())."""
    if isinstance(cfg, (tuple, list)):
        cfg = {"kinds": tuple(cfg)}
    cfg = {"kinds": ("daily", "intraday"), "shorts": T.ALLOW_SHORTS, "exit": "target",
           "spy_filter": False, "margin": MARGIN, "stop_atr": T.STOP_ATR, "max_days": None,
           "exit_matrix": None, "seed": None, **(cfg or {})}
    if cfg["exit"] == "trail":
        cfg.setdefault("target_r", None)
        cfg.setdefault("trail_days", rules.SETTINGS["daily"]["donchian"])
    elif cfg["exit"] == "target":
        cfg.setdefault("target_r", T.REWARD_RISK)
        cfg.setdefault("trail_days", None)
    else:
        cfg.setdefault("target_r", None)
        cfg.setdefault("trail_days", None)
    kinds = cfg["kinds"]
    use = sig.copy()
    long_ = np.zeros(len(use), int)
    short = np.zeros(len(use), int)
    if "daily" in kinds:
        long_ += use["dl"].values
        short += use["ds"].values
    if "intraday" in kinds:
        long_ += use["il"].values
        short += use["is"].values
    use["n"] = long_ + short
    use["dir"] = np.where(long_ > 0, "LONG", "SHORT")
    use = use[(use["n"] > 0) & ~((long_ > 0) & (short > 0))]   # skip if the signals disagree
    if not cfg["shorts"]:
        use = use[use["dir"] == "LONG"]
    if "score" not in use:
        use["score"] = 0.0
    di = extra["day_idx"] if extra else None
    spy_up = extra.get("spy_up") if extra and cfg["spy_filter"] else None
    trail_n = cfg["trail_days"]
    if trail_n:
        lows = (extra.get("low") or {}).get(trail_n)
        highs = (extra.get("high") or {}).get(trail_n)
        if lows is None and trail_n == rules.SETTINGS["daily"]["donchian"]:
            lows, highs = extra["dcl"], extra["dch"]
    exit_m, max_days = cfg["exit_matrix"], cfg["max_days"]
    use["kind"] = np.where((use["dl"] | use["ds"]) & ("daily" in kinds), "daily", "15-min")
    if "pattern" in use:                               # chart-pattern trades: named after the pattern
        use["kind"] = use["pattern"].fillna("daily").values
    for c in ("stop", "target"):                       # the pattern's own stop/target prices (NaN = none)
        if c not in use:
            use[c] = np.nan
    if cfg["seed"] is None:
        use["tie"] = use["sym"].rank(method="dense")
    else:
        use["tie"] = np.random.default_rng(cfg["seed"]).random(len(use))
    use = use.sort_values(["slot", "n", "score", "tie"], ascending=[True, False, False, True])
    by_slot = {}
    for slot, col, dr, atr, kind, stp, tgt in zip(use["slot"].values, use["col"].values, use["dir"].values,
                                        use["atr"].values, use["kind"].values, use["stop"].values,
                                        use["target"].values):
        by_slot.setdefault(int(slot), []).append((int(col), dr, float(atr), kind, float(stp), float(tgt)))
    stop_scale = cfg["stop_atr"] / T.STOP_ATR

    Cf = pd.DataFrame(C).ffill().values
    OT, HT, LT, CT = (np.ascontiguousarray(m.T) for m in (O, H, L, C))      # one row per stock (fast)
    n_slots = len(slots)
    day = slots.tz_localize(None).normalize()
    last_of_day = np.r_[day[1:] != day[:-1], True]
    first_of_day = np.r_[True, day[1:] != day[:-1]]
    prev_same_day = np.r_[False, day[1:] == day[:-1]]
    if di is None:
        di = np.cumsum(first_of_day) - 1
    cash, pos, trades, curve, traded_on = float(START_EQUITY), {}, [], [], {}
    exits_at = {}                                      # slot -> [(phase, col, price, reason)]
    skipped = 0
    interest, prev_day = 0.0, None

    def close(col, px, s, why):
        nonlocal cash
        p = pos.pop(col)
        sign = 1 if p["dir"] == "LONG" else -1
        cash += sign * p["qty"] * px - FEE * p["qty"] * px
        pnl = sign * p["qty"] * (px - p["fill"]) - FEE * p["qty"] * (px + p["fill"])
        trades.append({"symbol": syms[col], "direction": p["dir"], "signal": p["kind"],
                       "entry_time": slots[p["slot"]], "exit_time": slots[s] + pd.Timedelta(minutes=15),
                       "entry": round(p["fill"], 2), "stop": p["stop"], "target": p["target"],
                       "exit": round(px, 2), "qty": p["qty"], "result": why,
                       "pnl": round(pnl, 2), "R": round(pnl / p["risk"], 2),
                       "days_held": (day[s] - day[p["slot"]]).days})

    def find_exit(col, s, p):
        """The first candle (from the entry on) where this trade ends, its price and why.
        Phase 0 = before the check's new entries (exit signal, time exit, trend exit),
        phase 1 = during the candle (stop, target). Same order as a candle-by-candle replay."""
        long_, stop, tgt = p["dir"] == "LONG", p["stop"], p["target"]
        sign = 1 if long_ else -1
        start, step = s, 1500
        while start < n_slots:
            end = min(n_slots, start + step)
            idx = np.arange(start, end)
            o, hh, ll = OT[col, start:end], HT[col, start:end], LT[col, start:end]
            valid = np.isfinite(o)
            old = valid & (idx != s)
            c1 = np.zeros(len(idx), bool)
            reason1 = None
            if exit_m is not None or max_days:
                fd = old & first_of_day[start:end] & (di[start:end] > 0)
                sig_ex = fd & exit_m[np.maximum(di[start:end] - 1, 0), col] if exit_m is not None else np.zeros(len(idx), bool)
                time_ex = fd & (di[start:end] - di[s] >= max_days) if max_days else np.zeros(len(idx), bool)
                c1 = sig_ex | time_ex
                reason1 = (sig_ex, time_ex)
            if trail_n:
                prev_c = CT[col, np.maximum(idx - 1, 0)]
                level = (lows if long_ else highs)[di[start:end], col]
                broke = prev_c < level if long_ else prev_c > level
                c2 = old & last_of_day[start:end] & prev_same_day[start:end] & np.isfinite(prev_c) & broke
            else:
                c2 = np.zeros(len(idx), bool)
            if long_:
                c3 = old & (o <= stop)
                c4 = old & (o >= tgt) if tgt is not None else np.zeros(len(idx), bool)
                c5 = valid & (ll <= stop)
                c6 = valid & (hh >= tgt) if tgt is not None else np.zeros(len(idx), bool)
            else:
                c3 = old & (o >= stop)
                c4 = old & (o <= tgt) if tgt is not None else np.zeros(len(idx), bool)
                c5 = valid & (hh >= stop)
                c6 = valid & (ll <= tgt) if tgt is not None else np.zeros(len(idx), bool)
            hit = c1 | c2 | c3 | c4 | c5 | c6
            if hit.any():
                k = int(np.argmax(hit))
                j = start + k
                if c1[k]:
                    why = "exit signal" if reason1[0][k] else "time exit"
                    return j, 0, o[k] * (1 - sign * SLIPPAGE), why
                if c2[k]:
                    return j, 0, o[k] * (1 - sign * SLIPPAGE), "trend exit"
                if c3[k]:
                    return j, 1, o[k] * (1 - sign * SLIPPAGE), "stop (gap)"
                if c4[k]:
                    return j, 1, o[k], "target (gap)"
                if c5[k]:
                    return j, 1, stop * (1 - sign * SLIPPAGE), "stop"
                return j, 1, tgt, "target"
            start = end
            step *= 4
        return None

    def run_exits(s, phase):
        for ph, col, pid, px, why in exits_at.get(s, []):
            if ph == phase and col in pos and pos[col]["id"] == pid:
                close(col, px, s, why)

    for s in range(n_slots):
        if s in exits_at:
            run_exits(s, 0)
        if s in by_slot and len(pos) <= T.MAX_OPEN_POSITIONS:
            px = Cf[s - 1] if s else Cf[0]
            value = sum((1 if p["dir"] == "LONG" else -1) * p["qty"] * px[c] for c, p in pos.items())
            gross = sum(p["qty"] * px[c] for c, p in pos.items())
            equity = cash + value
            bp = max(0.0, cfg["margin"] * equity - gross)
            placed, free = 0, T.MAX_OPEN_POSITIONS - len(pos)
            for col, r_dir, r_atr, r_kind, r_stop, r_tgt in by_slot[s]:
                if col in pos or traded_on.get(col) == day[s]:
                    continue
                if spy_up is not None and spy_up[di[s]] != (r_dir == "LONG"):
                    continue                           # against the market's trend
                if placed >= T.MAX_NEW_PER_RUN or free <= 0:
                    skipped += 1
                    continue
                price = O[s, col]
                if not np.isfinite(price) or not np.isfinite(r_atr):
                    continue
                sign = 1 if r_dir == "LONG" else -1
                if np.isfinite(r_stop):                # chart pattern: its own stop (and target)
                    dist = sign * (price - r_stop)
                    use_tgt = cfg.get("pattern_target") and np.isfinite(r_tgt)
                    if dist <= 0 or (use_tgt and sign * (r_tgt - price) <= 0):
                        continue                       # opened beyond the stop or already at the target
                    plan = T.plan_trade(r_dir, price, dist / T.STOP_ATR, equity, bp)
                    if not plan:
                        continue
                    plan["target"] = _round(r_tgt) if use_tgt else \
                        (_round(price + sign * cfg["target_r"] * dist) if cfg["target_r"] else None)
                else:
                    plan = T.plan_trade(r_dir, price, r_atr * stop_scale, equity, bp)
                    if not plan:
                        continue
                    dist = cfg["stop_atr"] * r_atr
                    plan["target"] = _round(price + sign * cfg["target_r"] * dist) if cfg["target_r"] else None
                fill = price * (1 + sign * SLIPPAGE)
                cash -= sign * plan["qty"] * fill + FEE * plan["qty"] * fill
                pid = len(trades) + len(pos) + s * 1000
                pos[col] = {"dir": r_dir, "kind": r_kind, "slot": s, "fill": fill, "id": pid, **plan}
                ex = find_exit(col, s, pos[col])
                if ex:
                    exits_at.setdefault(ex[0], []).append((ex[1], col, pid, ex[2], ex[3]))
                traded_on[col] = day[s]
                bp -= plan["qty"] * price
                placed += 1
                free -= 1
        if s in exits_at:
            run_exits(s, 1)
        if last_of_day[s]:
            if cash < 0:                               # borrowed money pays interest every calendar day
                n_days = (day[s] - prev_day).days if prev_day is not None else 1
                fee = -cash * MARGIN_RATE * n_days / 360
                cash -= fee
                interest += fee
            prev_day = day[s]
            value = sum((1 if p["dir"] == "LONG" else -1) * p["qty"] * Cf[s, c] for c, p in pos.items())
            gross = sum(p["qty"] * Cf[s, c] for c, p in pos.items())
            curve.append((day[s], cash + value, len(pos), gross / (cash + value)))

    still_open = len(pos)
    for col in list(pos):                              # value what is still open at the last price
        close(col, Cf[-1, col], len(slots) - 1, "still open")
    eq = pd.DataFrame(curve, columns=["date", "equity", "positions", "exposure"]).set_index("date")
    cols = ["symbol", "direction", "signal", "entry_time", "exit_time", "entry", "stop", "target", "exit",
            "qty", "result", "pnl", "R", "days_held"]
    return pd.DataFrame(trades, columns=cols), eq, {"skipped": skipped, "still_open": still_open, "interest": interest}


# ---------------------------------------------------------------- statistics
def streak(results):
    best = cur = 0
    for r in results:
        cur = cur + 1 if r < 0 else 0
        best = max(best, cur)
    return best


def stats(trades, eq):
    e = eq["equity"]
    years = max((e.index[-1] - e.index[0]).days / 365.25, 1e-9)
    dd = e / e.cummax() - 1
    done = trades[trades["result"] != "still open"] if len(trades) else trades
    wins, losses = done[done["pnl"] > 0], done[done["pnl"] <= 0]
    gross_loss = -losses["pnl"].sum()
    daily_ret = e.pct_change().dropna()
    return {
        "return": e.iat[-1] / START_EQUITY - 1,
        "cagr": (e.iat[-1] / START_EQUITY) ** (1 / years) - 1,
        "max_dd": dd.min(),
        "dd_date": dd.idxmin(),
        "sharpe": daily_ret.mean() / daily_ret.std() * np.sqrt(DAYS_PER_YEAR) if daily_ret.std() > 0 else 0.0,
        "trades": len(done),
        "per_month": len(done) / (years * 12),
        "win_rate": len(wins) / len(done) if len(done) else 0.0,
        "avg_win": wins["pnl"].mean() if len(wins) else 0.0,
        "avg_loss": losses["pnl"].mean() if len(losses) else 0.0,
        "avg_R": done["R"].mean() if len(done) else 0.0,
        "pf": wins["pnl"].sum() / gross_loss if gross_loss > 0 else float("inf"),
        "streak": streak(done["pnl"].values) if len(done) else 0,
        "days_held": done["days_held"].mean() if len(done) else 0.0,
        "avg_positions": eq["positions"].mean(),
        "exposure": eq["exposure"].mean(),
        "final": e.iat[-1],
    }


def benchmark(D, start):
    c = D["Close"][D.index >= start]
    return c / c.iat[0] * START_EQUITY


# ---------------------------------------------------------------- report
def pct(x):
    return f"{x * 100:+.1f}%"


def yearly(eq):
    e = eq["equity"]
    out, prev = {}, START_EQUITY
    for y, v in e.groupby(e.index.year).last().items():
        out[y] = v / prev - 1
        prev = v
    return out


def summary_text(res, bench, start, end, n_stocks, mode="bot versions"):
    eq0 = next(iter(res.values()))["equity"]["equity"]
    years = (eq0.index[-1] - eq0.index[0]).days / 365.25
    money = (f"risk {T.RISK_PER_TRADE:.0%}/trade, max {T.MAX_OPEN_POSITIONS} positions of max "
             f"{T.MAX_POSITION_PCT:.0%} each")
    if mode == "patterns":
        import patterns
        sel_txt = (f" Versions 5-6 only trade your selected patterns: {patterns.selection_text(MARKET)}."
                   if patterns.selection(MARKET) else "")
        rules_txt = (f"Chart patterns only (patterns.py, daily chart): a breakout on a day's close is traded at the "
                     f"next day's open with the pattern's own stop and target (sold after {PATTERN_MAX_DAYS} days "
                     f"if neither is hit); {money}, max {T.MAX_INVESTED:.0%} invested.{sel_txt}")
    elif mode == "strategies":
        rules_txt = (f"Strategy comparison, same money rules for all: {money}, max "
                     f"{T.MAX_INVESTED:.0%} invested. Daily strategies buy at the next day's open.")
    else:
        rules_txt = (f"Bot versions: ⭐ STRONG signals (daily + 15-min), stop 2×ATR, {money}; target 2:1 "
                     f"unless trailing. " + (f"Borrowed money pays {MARGIN_RATE:.1%}/yr interest." if MARGIN > 1 else "No borrowing."))
    title = {"strategies": "strategies", "patterns": "chart patterns"}.get(mode, "bot versions")
    lines = [f"📊 <b>Backtest {UNIVERSE}: {title}</b>",
             f"{start:%d %b %Y} → {end:%d %b %Y} ({years:.1f} years), {UNIVERSE} ({n_stocks} {UNIT}), "
             f"$100,000 start. {rules_txt} 'Invested' = average money in the market ({BENCHMARK} = 100%)", ""]
    n_runs = next(iter(res.values())).get("agg", {}).get("runs", 1)
    if n_runs > 1:
        lines[-1:] = [f"Each version is tested from {n_runs} start dates a week apart (ties between signals "
                      f"in random order); figures are the middle run, with the range in brackets.", ""]
    for key, r in res.items():
        a, name = r.get("agg") or r["stats"], r["name"]
        if n_runs > 1:
            spy_txt = f", beat {BENCHMARK} in {a['beat']}/{a['spy_runs']} runs" if a.get("spy_runs") else ""
            lines += [f"<b>{name}</b>",
                      f"{pct(a['cagr'])}/yr ({pct(a['cagr_min'])} to {pct(a['cagr_max'])}){spy_txt}",
                      f"max drop {pct(a['max_dd'])} (worst {pct(a['dd_worst'])}) · profit factor {a['pf']:.2f}",
                      f"~{a['trades']:.0f} trades, win {a['win_rate']:.0%}, avg {a['avg_R']:+.2f}R, worst streak "
                      f"{a['streak']:.0f}, hold {a['days_held']:.0f}d, invested {a['exposure']:.0%}"
                      + (f", interest ~${a['interest']:,.0f}" if a.get("interest") else ""), ""]
            continue
        s = r["stats"]
        lines += [f"<b>{name}</b>",
                  f"{pct(s['return'])} ({pct(s['cagr'])}/yr) · max drop {pct(s['max_dd'])} · "
                  f"profit factor {s['pf']:.2f}",
                  f"{s['trades']} trades, win {s['win_rate']:.0%}, avg {s['avg_R']:+.2f}R, "
                  f"worst streak {s['streak']}, hold {s['days_held']:.0f}d, invested {s['exposure']:.0%}"
                  + (f", interest paid ${s['interest']:,.0f}" if s.get("interest") else ""), ""]
    if bench is not None and len(bench) > 1:
        b = bench
        b_years = max((b.index[-1] - b.index[0]).days / 365.25, 1e-9)
        b_cagr = (b.iat[-1] / START_EQUITY) ** (1 / b_years) - 1
        lines += [f"<b>Buy &amp; hold {BENCHMARK}</b>",
                  f"{pct(b.iat[-1] / START_EQUITY - 1)} ({pct(b_cagr)}/yr) · "
                  f"max drop {pct((b / b.cummax() - 1).min())}", ""]
    lines += [f"<i>{FOOTER} Past results don't guarantee future ones.</i>"]
    return "\n".join(lines)


def cell(x):
    return f"{x * 100:>+6.0f}%" if x is not None and np.isfinite(x) else "    n/a"


def details_text(res, bench):
    """Second message: returns by year and long/short split for every version."""
    years = sorted(set().union(*(yearly(r["equity"]).keys() for r in res.values())))
    n_runs = len(next(iter(res.values())).get("runs", [0]))
    lines = ["📅 <b>Return by year</b>" + (f" (middle of {n_runs} runs)" if n_runs > 1 else ""), "<pre>" + "Version " + "".join(f"{y:>7}" for y in years)]
    for key, r in res.items():
        per_run = [yearly(p["equity"]) for p in r.get("runs", [r])]
        yr = {y: median([p.get(y) for p in per_run]) for y in years}
        lines.append(f"{r['short'][:7]:<8}" + "".join(cell(yr.get(y)) for y in years))
    if bench is not None and len(bench) > 1:
        yr = yearly(pd.DataFrame({"equity": bench}))
        lines.append(f"{BENCHMARK:<8}" + "".join(cell(yr.get(y)) for y in years))
    lines[-1] += "</pre>"
    lines += ["", "<b>Long vs short</b> (first run)"]
    for key, r in res.items():
        t = r["trades"]
        t = t[t["result"] != "still open"]
        parts = []
        for d, g in t.groupby("direction"):
            parts.append(f"{d.lower()} {len(g)}× {g['R'].mean():+.2f}R ${g['pnl'].sum() / 1000:+,.0f}k")
        lines.append(f"{r['short']}: " + (", ".join(parts) or "no trades"))
    return "\n".join(lines)


def equity_chart(res, bench):
    plt.style.use("dark_background")
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    fig.patch.set_facecolor("black")
    palette = ["#26a69a", "#42a5f5", "#ffb74d", "#ab47bc", "#ef5350", "#ffee58", "#8d6e63", "#4dd0e1",
               "#f06292", "#aed581"]
    for i, (key, r) in enumerate(res.items()):
        e = r["equity"]["equity"]
        colr = palette[i % len(palette)]
        a1.plot(e.index, e.values, color=colr, lw=2 if r.get("main") else 1.1, label=r["name"])
        a2.plot(e.index, (e / e.cummax() - 1).values * 100, color=colr, lw=1)
    if bench is not None:
        a1.plot(bench.index, bench.values, color="#9e9e9e", lw=1.2, ls="--", label=f"Buy & hold {BENCHMARK}")
    a1.axhline(START_EQUITY, color="#555555", lw=0.8)
    a1.set_ylabel("Account value ($)")
    a1.set_title("Backtest vs buy & hold", color="white")
    a1.legend(loc="upper left", fontsize=9, facecolor="black")
    a1.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v / 1000:,.0f}k"))
    a2.set_ylabel("Drawdown (%)")
    for a in (a1, a2):
        a.set_facecolor("black")
        a.grid(color="#222222")
    fig.tight_layout()
    path = os.path.join(OUT, "equity.png")
    fig.savefig(path, dpi=110, facecolor="black")
    plt.close(fig)
    return path


def telegram(text=None, photo=None, document=None, caption=""):
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    base = f"https://api.telegram.org/bot{token}"
    for attempt in range(3):
        try:
            if photo:
                with open(photo, "rb") as f:
                    r = requests.post(f"{base}/sendPhoto", data={"chat_id": chat, "caption": caption},
                                      files={"photo": f}, timeout=60)
            elif document:
                with open(document, "rb") as f:
                    r = requests.post(f"{base}/sendDocument", data={"chat_id": chat, "caption": caption},
                                      files={"document": f}, timeout=60)
            else:
                r = requests.post(f"{base}/sendMessage", timeout=30,
                                  data={"chat_id": chat, "text": text[:4096], "parse_mode": "HTML"})
            if r.status_code == 429:
                time.sleep(r.json().get("parameters", {}).get("retry_after", 5) + 1)
                continue
            if not r.ok:
                print("Telegram error:", r.status_code, r.text[:300])
            time.sleep(1.5)
            return
        except requests.RequestException as e:
            print(f"Telegram problem: {e}")
            time.sleep(5)


# ---------------------------------------------------------------- main
def load_strategies(only=None):
    """Strategy files in strategies/ (files starting with "_" and tools.py are skipped)."""
    import glob
    import importlib
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "strategies")
    sys.path.insert(0, os.path.dirname(folder))
    out = {}
    for path in sorted(glob.glob(os.path.join(folder, "*.py"))):
        name = os.path.basename(path)[:-3]
        if name.startswith("_") or name == "tools" or (only and name not in only):
            continue
        try:
            out[name] = importlib.import_module(f"strategies.{name}")
        except Exception as e:
            print(f"Strategy {name} couldn't be loaded: {e}", flush=True)
    return out


def strategy_signals(mod, daily, syms, days, first_slot):
    """Buy signals of one strategy file, as rows the simulator understands, plus its exit matrix.
    A signal on a day's close buys at the next trading day's open."""
    from strategies import tools
    rows, exit_cols = [], []
    for col, sym in enumerate(syms):
        d = daily[sym][["Open", "High", "Low", "Close"]].copy()
        ent = pd.Series(mod.entries(d), index=d.index).fillna(False).astype(bool)
        atr = tools.atr(d)
        score = pd.Series(mod.rank(d), index=d.index) if hasattr(mod, "rank") else pd.Series(0.0, index=d.index)
        dates = d.index[ent.values]
        pos = days.get_indexer(dates)
        ok = (pos >= 0) & (pos + 1 < len(days))
        for dte, p in zip(dates[ok], pos[ok]):
            rows.append({"slot": first_slot[p + 1], "col": col, "sym": sym, "dl": True, "ds": False,
                         "il": False, "is": False, "atr": float(atr.at[dte]),
                         "score": float(score.at[dte]) if pd.notna(score.at[dte]) else 0.0})
        if hasattr(mod, "exits"):
            ex = pd.Series(mod.exits(d), index=d.index).reindex(days).fillna(False).astype(bool)
            exit_cols.append(ex.values)
    sig = pd.DataFrame(rows, columns=["slot", "col", "sym", "dl", "ds", "il", "is", "atr", "score"])
    return sig, (np.column_stack(exit_cols) if exit_cols else None)


# ---------------------------------------------------------------- chart patterns (patterns.py)
PATTERN_MAX_DAYS = 60       # pattern trades still open after 60 trading days are sold


def _pattern_rows(args):
    """Every daily pattern breakout of one stock/coin, judged on each day's close (run in parallel).
    Returns [(date, direction, stop, target, name, atr)], each pattern once (its first breakout)."""
    import patterns
    import scanner
    sym, d, dates = args
    d = d.copy()
    if "Volume" not in d:
        d["Volume"] = np.nan                           # no volume: the breakout volume check is skipped
    d = scanner.add_indicators(d)
    look = patterns.SETTINGS["daily"]["lookback"]
    close = d["Close"].values
    vol = (d["Volume"] / d["VolAvg20"]).values
    pos = d.index.get_indexer(dates)
    out, seen = [], set()
    for date, i in zip(dates, pos):
        if i < 60:
            continue
        v = vol[i] if np.isfinite(vol[i]) and vol[i] > 0 else None
        pats, _ = patterns.find_patterns(d.iloc[max(0, i - look + 1):i + 1], "daily", float(close[i]),
                                         float(close[i - 1]), v)
        for pt in pats:
            if pt["stage"] != "breakout" or pt["id"] in seen:
                continue
            seen.add(pt["id"])
            out.append((date, pt["dir"], pt["stop"], pt["target"], pt["name"], float(d["ATR"].iat[i])))
    return sym, out


def pattern_signals(daily, syms, days, first_slot, workers=4):
    """Pattern breakouts of every stock/coin as simulator rows: a breakout on a day's close buys
    (or sells short) at the next day's open, with the pattern's own stop and target."""
    from concurrent.futures import ProcessPoolExecutor
    t0 = time.time()
    jobs = [(sym, daily[sym], days[:-1]) for sym in syms]
    rows = []
    col_of = {sym: k for k, sym in enumerate(syms)}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for sym, found in pool.map(_pattern_rows, jobs):
            for date, direction, stop, target, name, atr in found:
                p = days.get_loc(date)
                rows.append({"slot": first_slot[p + 1], "col": col_of[sym], "sym": sym, "date": date,
                             "dl": direction == "LONG", "ds": direction == "SHORT", "il": False, "is": False,
                             "atr": atr, "score": 0.0, "stop": stop, "target": target,
                             "pattern": f"{name} {'↑' if direction == 'LONG' else '↓'}"})
    sig = pd.DataFrame(rows, columns=["slot", "col", "sym", "date", "dl", "ds", "il", "is", "atr", "score",
                                      "stop", "target", "pattern"])
    print(f"Pattern breakouts: {len(sig)} ({int(sig['dl'].sum())} up, {int(sig['ds'].sum())} down) "
          f"in {time.time() - t0:.0f}s", flush=True)
    return sig


TRAIL_DAYS = 20             # scorecard's trailing exit: sell on a close below the lowest low of 20 days
SPLIT_MIN_TRADES = 15       # split check: a pattern is "chosen" on the first half with this many trades ...
SPLIT_MIN_R = 0.0           # ... and an average R above this


def _trade(o, h, l, c, low_n, high_n, i, sign, stop, target, trail):
    """One breakout traded on daily candles from the open of day i+1.
    trail=False: pattern's stop and target, sold after PATTERN_MAX_DAYS days.
    trail=True: pattern's stop, then sold on a close beyond the 20-day low (high for shorts), no target.
    Returns (exit price, how, last day index)."""
    last = len(o) - 1 if trail else min(i + PATTERN_MAX_DAYS, len(o) - 1)
    for j in range(i + 1, last + 1):
        if j > i + 1 and sign * (o[j] - stop) <= 0:
            return o[j], "stop", j                     # opened through the stop
        if not trail and j > i + 1 and sign * (o[j] - target) >= 0:
            return o[j], "target", j                   # opened through the target
        if sign * ((l[j] if sign > 0 else h[j]) - stop) <= 0:
            return stop, "stop", j
        if not trail and sign * ((h[j] if sign > 0 else l[j]) - target) >= 0:
            return target, "target", j
        if trail:
            level = low_n[j] if sign > 0 else high_n[j]
            if np.isfinite(level) and sign * (c[j] - level) < 0:
                return c[j], "trend exit", j
    how = "time" if not trail and last - i >= PATTERN_MAX_DAYS else "open"   # "open" = still running at the end
    return c[last], how, last


def pattern_scorecard(sig, daily):
    """How each pattern did on its own, without the money rules: every breakout traded at the next
    day's open, costs included, two ways:
        R        pattern's stop and target (sold after PATTERN_MAX_DAYS days)
        R_trail  pattern's stop, then sold on a close below the 20-day low (above the 20-day high for
                 shorts): no target, the trend decides
    Daily candles: if one day touches both stop and target, the stop counts (cautious)."""
    out = []
    cost = 2 * (FEE + SLIPPAGE)
    cache = {}
    for r in sig.itertuples():
        if r.sym not in cache:
            d = daily[r.sym]
            cache[r.sym] = (d.index, *(d[k].values for k in ("Open", "High", "Low", "Close")),
                            d["Low"].rolling(TRAIL_DAYS).min().shift(1).values,
                            d["High"].rolling(TRAIL_DAYS).max().shift(1).values)
        idx, o, h, l, c, low_n, high_n = cache[r.sym]
        i = idx.get_loc(r.date)
        if i + 1 >= len(o):
            continue
        sign = 1 if r.dl else -1
        entry = o[i + 1]
        risk = sign * (entry - r.stop)
        if risk <= 0 or sign * (r.target - entry) <= 0:
            continue                                   # opened beyond the stop or the target
        x1, how1, j1 = _trade(o, h, l, c, low_n, high_n, i, sign, r.stop, r.target, trail=False)
        x2, how2, j2 = _trade(o, h, l, c, low_n, high_n, i, sign, r.stop, r.target, trail=True)
        fee = cost * entry / risk
        out.append({"pattern": r.pattern, "symbol": r.sym, "date": r.date, "entry": entry, "stop": r.stop,
                    "target": r.target, "exit": x1, "result": how1, "R": sign * (x1 - entry) / risk - fee,
                    "days": j1 - i, "exit_trail": x2, "result_trail": how2,
                    "R_trail": sign * (x2 - entry) / risk - fee, "days_trail": j2 - i})
    cols = ["pattern", "symbol", "date", "entry", "stop", "target", "exit", "result", "R", "days",
            "exit_trail", "result_trail", "R_trail", "days_trail"]
    return pd.DataFrame(out, columns=cols)


SHORT_NAMES = {"Symmetrical triangle": "Sym. triangle", "Inverse head & shoulders": "Inv. H&S",
               "Head & shoulders": "H&S", "Bump & run reversal": "Bump & run", "Ascending triangle": "Asc. triangle",
               "Descending triangle": "Desc. triangle", "Rounding bottom": "Round. bottom"}


def _label(name, width=16):
    base, arrow = name[:-2], name[-1]
    return f"{SHORT_NAMES.get(base, base)[:width - 2]} {arrow}"


def _closed(card, col):
    """Trades that finished (not still running at the end of the data) for one exit."""
    return card[card["result" if col == "R" else "result_trail"] != "open"]


def scorecard_text(card):
    if card.empty:
        return "🧩 <b>Pattern scorecard</b>: no pattern breakouts in this period."
    tg, tr = _closed(card, "R"), _closed(card, "R_trail")
    t = pd.DataFrame({"n": tg.groupby("pattern").size(),
                      "win": tg.groupby("pattern")["R"].apply(lambda x: (x > 0).mean()),
                      "avgR": tg.groupby("pattern")["R"].mean(),
                      "tgt": tg.groupby("pattern")["result"].apply(lambda x: (x == "target").mean()),
                      "trail": tr.groupby("pattern")["R_trail"].mean()}).fillna(0)
    t = t.sort_values("avgR", ascending=False)
    lines = ["🧩 <b>Pattern scorecard</b>: every breakout traded on its own, costs included. "
             f"<b>avgR</b> = with the pattern's stop and target (sold after {PATTERN_MAX_DAYS} days), "
             f"<b>trail</b> = pattern's stop + sold below the {TRAIL_DAYS}-day low, no target. "
             "Above 0 = made money on average; fewer than ~20 trades = too few to trust.",
             "<pre>Pattern            n  win  avgR  tgt trail"]
    for name, r in t.iterrows():
        lines.append(f"{_label(name):<16}{int(r.n):>4}{r.win:>5.0%}{r.avgR:>+6.2f}{r.tgt:>5.0%}{r.trail:>+6.2f}")
    lines.append(f"{'All patterns':<16}{len(tg):>4}{(tg['R'] > 0).mean():>5.0%}{tg['R'].mean():>+6.2f}"
                 f"{(tg['result'] == 'target').mean():>5.0%}{tr['R_trail'].mean():>+6.2f}</pre>")
    return "\n".join(lines)


def split_text(card):
    """Honest check: patterns are chosen on the first half of the period only, then judged on the
    second half, which they weren't chosen on."""
    if card.empty or card["date"].nunique() < 2:
        return None
    dates = pd.to_datetime(card["date"])
    mid = dates.min() + (dates.max() - dates.min()) / 2
    first, second = card[dates <= mid], card[dates > mid]
    lines = [f"✂️ <b>Split check</b>: patterns chosen on {dates.min():%b %Y}–{mid:%b %Y} only (at least "
             f"{SPLIT_MIN_TRADES} trades and avg R above {SPLIT_MIN_R:+.1f}), then tested on "
             f"{mid:%b %Y}–{dates.max():%b %Y}, data they weren't chosen on. If the chosen ones still beat "
             "'all patterns' in the second half, the choice is probably real, not luck."]
    for col, name in (("R", "stop + target"), ("R_trail", "stop + trailing exit")):
        a, b = _closed(first, col), _closed(second, col)
        g = a.groupby("pattern")[col].agg(["size", "mean"])
        chosen = g[(g["size"] >= SPLIT_MIN_TRADES) & (g["mean"] > SPLIT_MIN_R)].index.tolist()
        lines += ["", f"<b>{name}</b>"]
        if not chosen:
            lines.append("No pattern qualified on the first half.")
            continue
        lines.append("Chosen: " + ", ".join(_label(c, 24) for c in chosen))
        bc = b[b["pattern"].isin(chosen)]
        lines.append(f"1st half: chosen {len(a[a['pattern'].isin(chosen)])} trades, avg "
                     f"{a[a['pattern'].isin(chosen)][col].mean():+.2f}R | all {len(a)}, {a[col].mean():+.2f}R")
        lines.append(f"2nd half: chosen {len(bc)} trades, avg "
                     f"{(bc[col].mean() if len(bc) else float('nan')):+.2f}R | all {len(b)}, "
                     f"{(b[col].mean() if len(b) else float('nan')):+.2f}R")
    # per pattern, both halves (stop + target / trailing)
    rows = []
    for pat in card["pattern"].unique():
        vals = []
        for part in (first, second):
            for col in ("R", "R_trail"):
                x = _closed(part, col)
                x = x[x["pattern"] == pat][col]
                vals.append((len(x), x.mean() if len(x) else float("nan")))
        rows.append((pat, vals))
    rows.sort(key=lambda r: -(r[1][0][1] if np.isfinite(r[1][0][1]) else -9))
    lines += ["", "<pre>Pattern         1st: n  avgR trail  2nd: n  avgR trail"]
    for pat, v in rows:
        f = lambda x: f"{x:+5.2f}" if np.isfinite(x) else "    -"
        lines.append(f"{_label(pat):<16}{v[0][0]:>5} {f(v[0][1])} {f(v[1][1])}{v[2][0]:>7} {f(v[2][1])} {f(v[3][1])}")
    lines[-1] += "</pre>"
    return "\n".join(lines)


BOT_NOW = {"shorts": False, "exit": "trail", "margin": 1.0}     # the paper bot's current rules


def slice_extra(extra, d0):
    """extra (per-day arrays) for a run that starts on day number d0."""
    out = {}
    for k, v in extra.items():
        if k == "day_idx":
            continue
        if isinstance(v, dict):
            out[k] = {n: a[d0:] for n, a in v.items()}
        else:
            out[k] = v[d0:]
    return out


def median(xs):
    xs = [x for x in xs if x is not None and np.isfinite(x)]
    return float(np.median(xs)) if xs else float("nan")


def aggregate(per):
    """Middle value and range of each figure over the runs."""
    st = [p["stats"] for p in per]
    agg = {k: median([x[k] for x in st]) for k in st[0] if isinstance(st[0][k], (int, float, np.floating))}
    cagr = [x["cagr"] for x in st]
    agg.update(cagr_min=min(cagr), cagr_max=max(cagr), dd_worst=min(x["max_dd"] for x in st),
               runs=len(per), beat=sum(p["stats"]["cagr"] > p["spy_cagr"] for p in per if p.get("spy_cagr") is not None),
               spy_runs=sum(p.get("spy_cagr") is not None for p in per))
    return agg


def run(daily, intraday, start, end, spy=None, mode="bot versions", strategies=None, runs=None):
    """Signals + simulation. daily/intraday: {symbol: DataFrame}; spy: SPY daily.
    mode "bot versions": the VARIANTS of the bot's own strategy.
    mode "strategies": the bot now vs every strategy file in strategies/."""
    syms = sorted(intraday)
    t0 = time.time()
    slots = pd.DatetimeIndex(sorted(set().union(*(intraday[s].index for s in syms))))
    keep = slots >= pd.Timestamp(start).tz_convert(slots.tz)
    slots = slots[keep]
    if mode == "patterns":
        sig = None                                     # pattern breakouts are found below, per day
    else:
        sig = []
        for col, sym in enumerate(syms):
            s = stock_signals(intraday[sym], daily[sym])
            s["sym"], s["col"] = sym, col
            sig.append(s)
        sig = pd.concat(sig, ignore_index=True)
        sig = sig[sig["fill"] >= slots[0]].copy()
        sig["slot"] = slots.get_indexer(sig["fill"])
        sig = sig[sig["slot"] >= 0]
        print(f"Signals: {len(sig)} ({sig[['dl', 'ds']].any(axis=1).sum()} daily, "
              f"{sig[['il', 'is']].any(axis=1).sum()} 15-min) in {time.time() - t0:.0f}s", flush=True)

    def matrix(field):
        return np.column_stack([intraday[s][field].reindex(slots).values for s in syms])
    O, H, L, C = (matrix(f) for f in ("Open", "High", "Low", "Close"))

    # per day: 20-day low/high of every stock (for the trailing exit) and SPY's trend (for the filter)
    slot_day = slots.tz_localize(None).normalize()
    days = slot_day.unique()
    extra = {"day_idx": days.get_indexer(slot_day)}
    ctx = {sym: daily_context(daily[sym]).reindex(days) for sym in syms}
    extra["dcl"] = np.column_stack([ctx[sym]["dcl"].values for sym in syms])
    extra["dch"] = np.column_stack([ctx[sym]["dch"].values for sym in syms])
    if spy is not None:
        c = spy["Close"]
        up = (c > c.rolling(FILTER_MA).mean()).shift(1)            # yesterday's close vs its N-day MA
        extra["spy_up"] = up.reindex(days).fillna(True).astype(bool).values
    else:
        print(f"No {BENCHMARK} prices: the {BENCHMARK} trend filter is switched off", flush=True)

    def lows_highs(n):
        lo = np.column_stack([daily[s]["Low"].rolling(n).min().shift(1).reindex(days).values for s in syms])
        hi = np.column_stack([daily[s]["High"].rolling(n).max().shift(1).reindex(days).values for s in syms])
        return lo, hi

    # what to simulate: (key, name, short label, signals, settings)
    jobs = []
    if mode == "strategies":
        jobs.append(("bot", "Bot now: breakout, trailing exit", "Bot", sig, BOT_NOW))
        if MARKET == "crypto" and spy is not None:
            jobs.append(("bot_btc", f"Bot now + BTC filter (BTC above its {FILTER_MA}-day MA)", "Bot+BTC", sig,
                         {**BOT_NOW, "spy_filter": True}))
        from strategies import tools
        tools.MARKET = spy["Close"] if spy is not None else None      # for tools.market_up()
        tools.MARKET_MA = FILTER_MA
        first_slot = np.flatnonzero(np.r_[True, slot_day[1:] != slot_day[:-1]])
        for name, mod in (strategies if strategies is not None else load_strategies()).items():
            if MARKET not in getattr(mod, "MARKETS", ("stocks", "crypto")):
                continue                               # a strategy made for the other market
            try:
                s_sig, s_exit = strategy_signals(mod, daily, syms, days, first_slot)
            except Exception as e:
                print(f"Strategy {name} failed: {e}", flush=True)
                continue
            ex = {"stop_atr": T.STOP_ATR, "target_r": None, "trail_days": None, "max_days": None,
                  **getattr(mod, "EXIT", {})}
            cfg = {"kinds": ("daily",), "shorts": False, "exit": "custom", "margin": T.MAX_INVESTED,
                   "exit_matrix": s_exit, **ex}
            if ex["trail_days"]:
                extra.setdefault("low", {})[ex["trail_days"]], extra.setdefault("high", {})[ex["trail_days"]] = \
                    lows_highs(ex["trail_days"])
            jobs.append((name, getattr(mod, "NAME", name), getattr(mod, "SHORT", name[:7]), s_sig, cfg))
            print(f"Strategy {name}: {len(s_sig)} buy signals", flush=True)
    elif mode == "patterns":
        first_slot = np.flatnonzero(np.r_[True, slot_day[1:] != slot_day[:-1]])
        sig = pattern_signals(daily, syms, days, first_slot)
        pv = {"kinds": ("daily",), "exit": "custom", "margin": T.MAX_INVESTED, "pattern_target": True,
              "target_r": None, "trail_days": None, "max_days": PATTERN_MAX_DAYS}
        no_short = " (test only: Alpaca can't short crypto)" if MARKET == "crypto" else ""
        jobs += [
            ("p_long", "1. Up patterns: pattern's stop + target", "P Long", sig, {**pv, "shorts": False}),
            ("p_both", "2. Up + down patterns (shorts)" + no_short, "P L+S", sig, {**pv, "shorts": True}),
            ("p_filter", f"3. Up patterns, only while {BENCHMARK} is above its {FILTER_MA}-day MA", "P Filt", sig,
             {**pv, "shorts": False, "spy_filter": True}),
            ("p_trail", "4. Up patterns: pattern's stop + trailing exit (20-day low)", "P Trail", sig,
             {**pv, "shorts": False, "pattern_target": False, "trail_days": 20, "max_days": None}),
        ]
        import patterns
        if patterns.selection(MARKET):                 # your pattern selection (patterns.SELECTED)
            keep = [patterns.is_selected(lbl[:-2], "LONG" if lbl.endswith("↑") else "SHORT", MARKET)
                    for lbl in sig["pattern"]]
            sel = sig[np.array(keep, bool)]
            print(f"Selected patterns ({patterns.selection_text(MARKET)}): {len(sel)} breakouts", flush=True)
            jobs += [
                ("p_sel", "5. Selected patterns: pattern's stop + target", "Sel Tgt", sel,
                 {**pv, "shorts": True}),
                ("p_sel_trail", "6. Selected patterns: pattern's stop + trailing exit", "Sel Trl", sel,
                 {**pv, "shorts": True, "pattern_target": False, "trail_days": 20, "max_days": None}),
            ]
    else:
        for key, (name, cfg) in VARIANTS.items():
            jobs.append((key, name, SHORT[key], sig, cfg))

    # every version from several start dates a week apart, ties between signals in random order
    runs = max(1, int(runs or RUNS))
    day_first = np.flatnonzero(np.r_[True, slot_day[1:] != slot_day[:-1]])
    res = {}
    for key, name, short, jsig, cfg in jobs:
        per = []
        for i in range(runs):
            d0 = min(i * RUN_STEP_DAYS, len(days) - 2)
            k = day_first[d0]
            sub = jsig[jsig["slot"] >= k].copy()
            sub["slot"] -= k
            ex = slice_extra(extra, d0)
            ex["day_idx"] = extra["day_idx"][k:] - d0
            c = {**cfg, "seed": i if runs > 1 else None}
            if c.get("exit_matrix") is not None:
                c["exit_matrix"] = c["exit_matrix"][d0:]
            trades, eq, info = simulate(sub, O[k:], H[k:], L[k:], C[k:], slots[k:], syms, c, ex)
            st = {**stats(trades, eq), "interest": info["interest"]}
            spy_cagr = None
            if spy is not None:
                b = benchmark(spy, days[d0])
                if len(b) > 1:
                    yrs = max((b.index[-1] - b.index[0]).days / 365.25, 1e-9)
                    spy_cagr = (b.iat[-1] / START_EQUITY) ** (1 / yrs) - 1
            per.append({"trades": trades, "equity": eq, "info": info, "stats": st, "spy_cagr": spy_cagr,
                        "start": days[d0]})
        agg = aggregate(per)
        res[key] = {"name": name, "short": short, "main": key in ("bot", "trail1x", "p_long") or (T is not trader and key == "trail"), "runs": per, "agg": agg,
                    **per[0]}
        print(f"{name}: median {pct(agg['cagr'])}/yr (runs {pct(agg['cagr_min'])} to {pct(agg['cagr_max'])}), "
              f"beat {BENCHMARK} in {agg['beat']}/{agg['spy_runs']}, ~{agg['trades']:.0f} trades", flush=True)
    return res, sig


def main():
    years = float(os.environ.get("YEARS", "5"))
    mode = os.environ.get("TEST", "strategies")
    runs = int(os.environ.get("RUNS") or RUNS)
    market = os.environ.get("MARKET", "stocks")
    end = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20)
    start = end - dt.timedelta(days=round(365.25 * years))
    start = max(start, dt.datetime(2016, 3, 1, tzinfo=dt.timezone.utc))      # Alpaca history starts 2016
    os.makedirs(OUT, exist_ok=True)

    if market == "crypto":
        import crypto
        use_crypto()
        tickers = crypto.top_coins()
        print(f"Crypto backtest {start:%Y-%m-%d} → {end:%Y-%m-%d}, {len(tickers)} coins", flush=True)
        daily, intraday = crypto.history(tickers, start, end, CACHE_DIR)
        if not intraday:
            sys.exit("No crypto prices could be downloaded.")
        first = min(h.index[0] for h in intraday.values())
        if first > pd.Timestamp(start) - pd.Timedelta(days=20):     # Alpaca's crypto history is shorter
            start = (first + pd.Timedelta(days=21)).to_pydatetime()
            print(f"Prices only go back to {first:%Y-%m-%d}: testing from {start:%Y-%m-%d}", flush=True)
        bench_d = daily.get("BTC/USD")
        print(f"Prices for {len(intraday)} coins", flush=True)
    else:
        import scanner
        tickers = scanner.get_tickers()
        print(f"Backtest {start:%Y-%m-%d} → {end:%Y-%m-%d}, {len(tickers)} stocks", flush=True)
        daily, intraday = download(tickers + [BENCHMARK], start, end)
        bench_d = daily.pop(BENCHMARK, None)
        intraday.pop(BENCHMARK, None)
        print(f"Prices for {len(intraday)} stocks", flush=True)

    res, sig = run(daily, intraday, start, end, spy=bench_d, mode=mode, runs=runs)
    bench = benchmark(bench_d, pd.Timestamp(start.date())) if bench_d is not None else None

    text = summary_text(res, bench, start, end, len(intraday), mode)
    print("\n" + html.unescape(text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")))
    chart = equity_chart(res, bench)
    t = pd.concat([r["trades"].assign(version=r["name"]) for r in res.values()], ignore_index=True)
    t = t[["version"] + [c for c in t.columns if c != "version"]]
    for c in ("entry_time", "exit_time"):
        t[c] = pd.to_datetime(t[c]).dt.strftime("%Y-%m-%d %H:%M")
    csv = os.path.join(OUT, "trades.csv")
    t.to_csv(csv, index=False)
    with open(os.path.join(OUT, "summary.txt"), "w") as f:
        f.write(html.unescape(text))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("```\n" + html.unescape(text.replace("<b>", "").replace("</b>", "")
                                            .replace("<i>", "").replace("</i>", "")) + "\n```\n")
    card = None
    if mode == "patterns":
        card = pattern_scorecard(sig, daily)
        card.to_csv(os.path.join(OUT, "patterns.csv"), index=False)
        for txt in (scorecard_text(card), split_text(card)):
            if txt:
                print("\n" + html.unescape(txt.replace("<b>", "").replace("</b>", "")
                                           .replace("<pre>", "").replace("</pre>", "")))
    details = details_text(res, bench)
    print("\n" + html.unescape(details.replace("<b>", "").replace("</b>", "").replace("<pre>", "").replace("</pre>", "")))
    telegram(text)
    telegram(details)
    if card is not None:
        telegram(scorecard_text(card))
        split = split_text(card)
        if split:
            telegram(split)
    telegram(photo=chart, caption="Account value and drawdowns of each version (first run)")
    telegram(document=csv, caption="Every trade of every version, first run (column 'version'). Open in Excel / Google Sheets.")
    if card is not None:
        telegram(document=os.path.join(OUT, "patterns.csv"),
                 caption="Every pattern breakout traded on its own (the scorecard's trades).")


if __name__ == "__main__":
    main()
