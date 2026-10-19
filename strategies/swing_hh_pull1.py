"""The user's swing rule, pullback version: buy the cross above the 50-day; every pullback of 1% or more becomes the
low to hold once the price makes a new high after it; sell on a close below it (a lower low).
Rules in strategies/_swing.py (trail="pullback")."""
from strategies import _swing

MARKETS = ("crypto", "stocks")
NAME = "Swing rule: buy the 50-day cross, sell on a lower low (close, pullbacks of 1%+) + buy only after a higher high"
SHORT = "Sw hh1"
SIZE = 1.0
KW = dict(mode="close", trail="pullback", pull_min=0.01, hh_entry=True)
EXIT = {"stop_atr": 30.0, "target_r": None, "trail_days": None, "max_days": None}


def entries(d):
    return _swing.run(d, **KW)["entry"]


def exits(d):
    return _swing.run(d, **KW)["exit"]
