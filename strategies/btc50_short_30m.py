"""Bitcoin only: the SHORT mirror rule (btc50_short_ma_f1) on 30-minute candles instead of daily ones.
The same rule counted in candles: sell short when a 30-minute close crosses below the 50-candle average
while that average is falling (lower than 10 candles ago); cover on a higher low (a low below the 3 candles
before it, then 1 higher candle), on a close back above the 50-candle average, or at the starting stop
above the last swing high (1-3 ATR of these candles). Decides at each candle's close, trades at the next
15-min open. Rules in strategies/_structure.py (side="short"); TF makes the backtest build the candles.
Backtest only: Alpaca can't short crypto."""
from strategies import _structure

MARKETS = ("crypto",)
NAME = "SHORT mirror on 30-minute candles (50-candle average)"
SHORT = "Short 30m"
SIDE = "short"
TF = "30min"
SIZE = 1.0
KW = dict(exit_mode='lh', trend='rising50', reentry=False, ma_exit='close', lh_right=1, side="short")
EXIT = {"stop_atr": 30.0, "target_r": None, "trail_days": None, "max_days": None}   # exits come from exits()


def entries(d):
    return _structure.run(d, **KW)["entry"]


def exits(d):
    return _structure.run(d, **KW)["exit"]
