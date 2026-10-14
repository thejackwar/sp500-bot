"""
YOUR STRATEGY: 200-day moving average, Bollinger Bands, Donchian channel,
major support/resistance and Fibonacci retracements (the last two live in levels.py).

Individual alerts (each sent once per candle / per day):
    200-day MA   price crosses above / below the 200-day simple moving average (daily chart)
    Bollinger    price breaks above the upper band / below the lower band (20 candles, 2 std dev)
    Squeeze      Bollinger bands at their narrowest in ~6 months (daily) / 120 candles (intraday)
    Donchian     new 20-candle high / low (price breaks the Donchian channel)
    Major S/R    price breaks a support/resistance level touched 3+ times in the last year
    Fibonacci    price pulls back to the 38.2% / 50% / 61.8% retracement of the latest major swing
These individual alerts come from the DAILY chart (checked with the latest price during the day).

The intraday chart (15-minute candles for the first 3 hours, hourly after that) uses slower
settings (Donchian 55, Bollinger 2.5 std) and only sends STRONG setups, because short candles
break 20-candle highs/lows far too often (~240 alerts a day across the S&P 100 in testing).

STRONG alert (marked with a star) when everything lines up:
    LONG  = price above the 200-day MA + new Donchian high + Bollinger confirms
    SHORT = price below the 200-day MA + new Donchian low  + Bollinger confirms
    "Bollinger confirms" = price beyond the band on that chart, or a squeeze in the last few candles.

Change the numbers below to tune the strategy.
"""
import pandas as pd

from fmt import px

import levels

SMA_LONG = 200                   # the 200-day moving average
BB_PERIOD = 20                   # Bollinger Bands length
SETTINGS = {                     # per chart: Bollinger width (std devs) and Donchian length
    "daily":    {"bb_std": 2.0, "donchian": 20},
    "intraday": {"bb_std": 2.5, "donchian": 55},
}
INTRADAY_STRONG_ONLY = True      # intraday chart: only send STRONG setups (False = every event)
SQUEEZE_LOOKBACK = {"daily": 125, "intraday": 120}   # "narrowest in N candles" (125 days = ~6 months)
SQUEEZE_RECENT = 10              # a squeeze in the last N candles counts as Bollinger confirmation


def add_strategy_indicators(df, squeeze_lookback, bb_std=2.0, donchian=20):
    df = df.copy()
    c = df["Close"]
    df["SMA200"] = c.rolling(SMA_LONG).mean()
    mid = c.rolling(BB_PERIOD).mean()
    sd = c.rolling(BB_PERIOD).std(ddof=0)
    df["BB_mid"], df["BB_up"], df["BB_lo"] = mid, mid + bb_std * sd, mid - bb_std * sd
    df["BB_width"] = (df["BB_up"] - df["BB_lo"]) / mid
    # Donchian channel = highest high / lowest low of the PREVIOUS N candles
    df["DC_high"] = df["High"].rolling(donchian).max().shift(1)
    df["DC_low"] = df["Low"].rolling(donchian).min().shift(1)
    df.attrs["donchian"] = donchian
    df["Squeeze"] = df["BB_width"] <= df["BB_width"].rolling(squeeze_lookback).min().shift(1)
    return df


def _timeframe_events(df, tf):
    """Events on one chart. Each event: (direction, code, text). Direction LONG/SHORT/WATCH."""
    cur, prev = df.iloc[-1], df.iloc[-2]
    ev = []
    if pd.notna(cur.BB_up) and pd.notna(prev.BB_up):
        if prev.Close <= prev.BB_up and cur.Close > cur.BB_up:
            ev.append(("LONG", "bb_up", f"Broke above upper Bollinger band {px(cur.BB_up)} ({tf})"))
        if prev.Close >= prev.BB_lo and cur.Close < cur.BB_lo:
            ev.append(("SHORT", "bb_lo", f"Broke below lower Bollinger band {px(cur.BB_lo)} ({tf})"))
    if pd.notna(cur.DC_high) and pd.notna(prev.DC_high):
        unit = "day" if tf == "daily" else "candle"
        n = df.attrs.get("donchian", 20)
        if prev.Close <= prev.DC_high and cur.Close > cur.DC_high:
            ev.append(("LONG", "dc_up", f"New {n}-{unit} high: broke Donchian high {px(cur.DC_high)} ({tf})"))
        if prev.Close >= prev.DC_low and cur.Close < cur.DC_low:
            ev.append(("SHORT", "dc_lo", f"New {n}-{unit} low: broke Donchian low {px(cur.DC_low)} ({tf})"))
    if bool(cur.Squeeze) and not bool(prev.Squeeze):
        span = "~6 months" if tf == "daily" else f"{SQUEEZE_LOOKBACK['intraday']} candles"
        ev.append(("WATCH", "squeeze", f"Bollinger squeeze: bands narrowest in {span}, a big move often follows ({tf})"))
    return ev


def _confirms(df, direction):
    cur = df.iloc[-1]
    beyond = cur.Close > cur.BB_up if direction == "LONG" else cur.Close < cur.BB_lo
    recent_squeeze = bool(df["Squeeze"].iloc[-SQUEEZE_RECENT - 1:].any())
    return bool(beyond) or recent_squeeze


def evaluate(daily, intraday, tf_name):
    """daily: daily candles incl. today's candle so far. intraday: completed 15-min/hourly candles.
    Returns (events, context, daily_df, intraday_df). Each event is a dict with
    dir, code, text, tf ("daily" or the intraday name), strong (bool)."""
    d = add_strategy_indicators(daily, SQUEEZE_LOOKBACK["daily"], **SETTINGS["daily"])
    h = add_strategy_indicators(intraday, SQUEEZE_LOOKBACK["intraday"], **SETTINGS["intraday"])
    cur, prev = d.iloc[-1], d.iloc[-2]
    events = []
    if pd.notna(cur.SMA200) and pd.notna(prev.SMA200):
        if prev.Close <= prev.SMA200 and cur.Close > cur.SMA200:
            events.append({"dir": "LONG", "code": "sma_up", "tf": "daily",
                           "text": f"Crossed above the 200-day moving average {px(cur.SMA200)}"})
        if prev.Close >= prev.SMA200 and cur.Close < cur.SMA200:
            events.append({"dir": "SHORT", "code": "sma_dn", "tf": "daily",
                           "text": f"Crossed below the 200-day moving average {px(cur.SMA200)}"})
    for frame, tf in ((d, "daily"), (h, tf_name)):
        for direction, code, text in _timeframe_events(frame, tf):
            events.append({"dir": direction, "code": code, "tf": tf, "text": text})

    lv_events, lv_ctx = levels.events(daily)          # major support/resistance + Fibonacci
    events += lv_events

    # STRONG: trend (200-day MA) + Donchian breakout + Bollinger confirmation on the same chart
    above = pd.notna(cur.SMA200) and cur.Close > cur.SMA200
    below = pd.notna(cur.SMA200) and cur.Close < cur.SMA200
    for e in events:
        e["strong"] = False
        frame = d if e["tf"] == "daily" else h
        if e["code"] == "dc_up" and above and _confirms(frame, "LONG"):
            e["strong"] = True
        if e["code"] == "dc_lo" and below and _confirms(frame, "SHORT"):
            e["strong"] = True

    if INTRADAY_STRONG_ONLY:
        events = [e for e in events if e["tf"] == "daily" or e["strong"]]

    context = {
        "price": float(h["Close"].iat[-1]),
        "sma200": float(cur.SMA200) if pd.notna(cur.SMA200) else None,
        "bb": (float(cur.BB_lo), float(cur.BB_up)) if pd.notna(cur.BB_up) else None,
        "dc": (float(cur.DC_low), float(cur.DC_high)) if pd.notna(cur.DC_high) else None,
        **lv_ctx,
    }
    return events, context, d, h
