"""Hold BTC while above BOTH its 50- and 200-day MAs, else cash (100% in).
Trend holding for Bitcoin only (run the backtest with coins = BTC and only = btc_trend):
buy at the next day's open (00:00 UTC) after a daily close above the average, sell at the next
open after a daily close below it. The whole account goes in (SIZE = 1.0), no stop-loss, no target.
(The report's R numbers mean little here: there is no stop to measure risk against.)"""
from strategies import tools

MARKETS = ("crypto",)
NAME = "Hold BTC while above BOTH its 50- and 200-day MAs, else cash (100% in)"
SHORT = "BTC>both"
SIZE = 1.0
EXIT = {"stop_atr": 30.0, "target_r": None, "trail_days": None, "max_days": None}


def _hold(d):
    c = d["Close"]
    return (c > tools.sma(c, 50)) & (c > tools.sma(c, 200))


def entries(d):
    return _hold(d)


def exits(d):
    return ~_hold(d) & d["Close"].notna()
