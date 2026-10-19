"""Bitcoin only: 50-day cross, lower high + close below the pullback low.
Rules in strategies/_structure.py. Whole account in (SIZE = 1.0); every decision on a daily close,
bought/sold at the next 00:00 UTC open. Run with coins = BTC, only = btc50."""
from strategies import _structure

MARKETS = ("crypto",)
NAME = "50-day cross, lower high + close below the pullback low"
SHORT = "LHbrk"
SIZE = 1.0
EXIT = {"stop_atr": 30.0, "target_r": None, "trail_days": None, "max_days": None}   # exits come from exits()


def entries(d):
    return _structure.run(d, exit_mode='break')["entry"]


def exits(d):
    return _structure.run(d, exit_mode='break')["exit"]
