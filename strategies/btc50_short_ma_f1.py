"""Bitcoin only: SHORT mirror of btc50_uprise_ma_f1 (the live Bitcoin rule turned upside down).
Sell short ONLY when the daily close crosses below the 50-day average while the 50-day average is falling
(lower than 10 days ago); cover on a higher low (a low below the 3 days before it, then 1 higher day),
on a close back above the 50-day, or at the starting stop (above the last swing high, 1-3 ATR); no
shorting again until the next fresh cross down. Rules in strategies/_structure.py (side="short").
Whole account (SIZE = 1.0); decisions on daily closes, executed at the next 00:00 UTC open.
Backtest only: Alpaca can't short crypto, so the live Bitcoin bot can't trade this."""
from strategies import _structure

MARKETS = ("crypto",)
NAME = "SHORT mirror: 50-day cross down (50-day falling) + close above the 50-day, higher low after 1 day"
SHORT = "Short f1"
SIDE = "short"
SIZE = 1.0
KW = dict(exit_mode='lh', trend='rising50', reentry=False, ma_exit='close', lh_right=1, side="short")
EXIT = {"stop_atr": 30.0, "target_r": None, "trail_days": None, "max_days": None}   # exits come from exits()


def entries(d):
    return _structure.run(d, **KW)["entry"]


def exits(d):
    return _structure.run(d, **KW)["exit"]
