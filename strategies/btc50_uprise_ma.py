"""Bitcoin only: Your rule (50-day rising) + sell on a close below the 50-day.
The user's rule (19 Oct 2026): buy ONLY when the daily close crosses above the 50-day average while the
50-day average is rising (uptrend); sell on a lower high, at the starting stop, or when price comes back
down to the 50-day; never buy back until the next fresh cross. Rules in strategies/_structure.py.
Whole account in (SIZE = 1.0); decisions on daily closes, executed at the next 00:00 UTC open."""
from strategies import _structure

MARKETS = ("crypto",)
NAME = "Your rule (50-day rising) + sell on a close below the 50-day"
SHORT = "UpR+MA"
SIZE = 1.0
KW = dict(exit_mode='lh', trend='rising50', reentry=False, ma_exit='close')          # the rule's settings (also used by trade_charts.py)
EXIT = {"stop_atr": 30.0, "target_r": None, "trail_days": None, "max_days": None}   # exits come from exits()


def entries(d):
    return _structure.run(d, **KW)["entry"]


def exits(d):
    return _structure.run(d, **KW)["exit"]
