"""The user's swing rule: buy the cross above the 50-day, sell only on a LOWER LOW (the price breaks the last
higher swing low; swing low = lowest of ±10 days), judged on a touch. Rules in strategies/_swing.py."""
from strategies import _swing

MARKETS = ("crypto", "stocks")
NAME = "Swing rule: buy the 50-day cross, sell on a lower low (touch, swing ±10 days)"
SHORT = "Sw t10"
SIZE = 1.0
KW = dict(pivot=10, mode="touch")
EXIT = {"stop_atr": 30.0, "target_r": None, "trail_days": None, "max_days": None}


def entries(d):
    return _swing.run(d, **KW)["entry"]


def exits(d):
    return _swing.run(d, **KW)["exit"]


def stops(d):
    """The low to hold after each close: tomorrow a touch below it sells at that level (stop order)."""
    return _swing.run(d, **KW)["stop"]
