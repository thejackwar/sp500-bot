"""The user's swing rule, pullback version: buy the cross above the 50-day; every pullback low becomes the
low to hold once the price makes a new high after it; sell on a touch below it (a lower low).
Rules in strategies/_swing.py (trail="pullback")."""
from strategies import _swing

MARKETS = ("crypto", "stocks")
NAME = "Swing rule: buy the 50-day cross, sell on a lower low (touch, every pullback low)"
SHORT = "Sw tpb"
SIZE = 1.0
KW = dict(mode="touch", trail="pullback")
EXIT = {"stop_atr": 30.0, "target_r": None, "trail_days": None, "max_days": None}


def entries(d):
    return _swing.run(d, **KW)["entry"]


def exits(d):
    return _swing.run(d, **KW)["exit"]


def stops(d):
    """The low to hold after each close: tomorrow a touch below it sells at that level (stop order)."""
    return _swing.run(d, **KW)["stop"]
