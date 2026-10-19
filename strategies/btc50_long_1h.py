"""Bitcoin only: the live Bitcoin rule (btc50_uprise_ma_f1) on 1-hour candles instead of daily ones.
The same rule counted in candles: buy when a 1-hour close crosses above the 50-candle average while that
average is rising (higher than 10 candles ago); sell on a close below the 50-candle average, on a lower
high (a high above the 3 candles before it, then 1 lower candle), or at the starting stop (under the last
swing low, 1-3 ATR of these candles); no buying back until the next fresh cross. Decides at each
candle's close, trades at the next 15-min open. Rules in strategies/_structure.py; TF makes the
backtest build the candles."""
from strategies import _structure

MARKETS = ("crypto",)
NAME = "LONG (your rule) on 1-hour candles (50-candle average)"
SHORT = "Long 1h"
TF = "1h"
SIZE = 1.0
KW = dict(exit_mode='lh', trend='rising50', reentry=False, ma_exit='close', lh_right=1)
EXIT = {"stop_atr": 30.0, "target_r": None, "trail_days": None, "max_days": None}   # exits come from exits()


def entries(d):
    return _structure.run(d, **KW)["entry"]


def exits(d):
    return _structure.run(d, **KW)["exit"]
