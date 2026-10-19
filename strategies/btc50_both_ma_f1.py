"""Bitcoin only: LONG + SHORT. The live rule (btc50_uprise_ma_f1) for longs and its exact mirror
(btc50_short_ma_f1) for shorts, one position at a time with the whole account. A long is closed on a
close below the 50-day, which is also the day a short can start (and the other way round), so it can
flip from long to short at the same open. Rules in strategies/_structure.py.
Backtest only: Alpaca can't short crypto, so the live Bitcoin bot can't trade the short side."""
from strategies import _structure

MARKETS = ("crypto",)
NAME = "LONG + SHORT: your rule both ways (close beyond the 50-day, lower high / higher low after 1 day)"
SHORT = "L+S f1"
SIDE = "both"
SIZE = 1.0
KW = dict(exit_mode='lh', trend='rising50', reentry=False, ma_exit='close', lh_right=1)
EXIT = {"stop_atr": 30.0, "target_r": None, "trail_days": None, "max_days": None}   # exits come from the rule


def entries(d):
    return _structure.run(d, **KW)["entry"]


def exits(d):
    return _structure.run(d, **KW)["exit"]


def short_entries(d):
    return _structure.run(d, **KW, side="short")["entry"]


def short_exits(d):
    return _structure.run(d, **KW, side="short")["exit"]
