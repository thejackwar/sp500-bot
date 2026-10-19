"""Bitcoin only: the live rule (btc50_uprise_ma_f1) + a market-structure filter on daily closes: buy only while
the lows are rising - the last confirmed swing low (a day lower than the 3 days on each side) is above the
one before it, and nothing since has gone below it. After a lower low, no buying until a new higher low
forms. Everything else as the live rule. Rules in strategies/_structure.py (structure="close")."""
from strategies import _structure

MARKETS = ("crypto",)
NAME = "Your rule + higher lows only (judged on daily closes)"
SHORT = "HL close"
SIZE = 1.0
KW = dict(exit_mode='lh', trend='rising50', reentry=False, ma_exit='close', lh_right=1, structure="close")
EXIT = {"stop_atr": 30.0, "target_r": None, "trail_days": None, "max_days": None}   # exits come from exits()


def entries(d):
    return _structure.run(d, **KW)["entry"]


def exits(d):
    return _structure.run(d, **KW)["exit"]
