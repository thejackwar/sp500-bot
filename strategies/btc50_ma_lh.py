"""Bitcoin only: Hold above the 50-day + also sell on a lower high (re-enter on a new high).
Buys when the close crosses above the line; sells after a close below it (the hold rule's exit) OR on a
lower high (a swing high below the highest high since entry), whichever comes first. Rules in
strategies/_structure.py. Whole account in (SIZE = 1.0); decisions on daily closes, executed at the
next 00:00 UTC open. Run with coins = BTC."""
from strategies import _structure

MARKETS = ("crypto",)
NAME = "Hold above the 50-day + also sell on a lower high (re-enter on a new high)"
SHORT = "50+LH"
SIZE = 1.0
KW = dict(exit_mode='lh', line='50', ma_exit=True)          # the rule's settings (also used by trade_charts.py)
EXIT = {"stop_atr": 30.0, "target_r": None, "trail_days": None, "max_days": None}   # exits come from exits()


def entries(d):
    return _structure.run(d, **KW)["entry"]


def exits(d):
    return _structure.run(d, **KW)["exit"]
