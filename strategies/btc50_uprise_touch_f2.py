"""Bitcoin only: Your rule (50-day rising) + back to the 50-day, lower high after 2 days.
The user's rule: buy ONLY when the daily close crosses above the 50-day average while the 50-day average
is rising; sell when a bounce fails below the highest high since entry (lower high: a high above the 3
days before it, followed by lh_right lower day(s)), at the starting stop, or when price comes back to the
50-day; no buying back until the next fresh cross. Rules in strategies/_structure.py. Whole account in
(SIZE = 1.0); decisions on daily closes, executed at the next 00:00 UTC open."""
from strategies import _structure

MARKETS = ("crypto",)
NAME = "Your rule (50-day rising) + back to the 50-day, lower high after 2 days"
SHORT = "Tch f2"
SIZE = 1.0
KW = dict(exit_mode='lh', trend='rising50', reentry=False, ma_exit='touch', lh_right=2)          # the rule's settings (also used by trade_charts.py)
EXIT = {"stop_atr": 30.0, "target_r": None, "trail_days": None, "max_days": None}   # exits come from exits()


def entries(d):
    return _structure.run(d, **KW)["entry"]


def exits(d):
    return _structure.run(d, **KW)["exit"]
