"""Bitcoin only: Your rule (uptrend = 50-day above 200-day) + take profit at 2R.
The user's rule (19 Oct 2026): buy ONLY when the daily close crosses above the 50-day average while
Bitcoin is in an uptrend; sell on a lower high (a swing high below the highest high since entry) or at
the starting stop (under the last swing low, 1-3 ATR); never buy back until the next fresh cross.
Rules in strategies/_structure.py. Whole account in (SIZE = 1.0); decisions on daily closes, executed
at the next 00:00 UTC open. Run with coins = BTC."""
from strategies import _structure

MARKETS = ("crypto",)
NAME = "Your rule (uptrend = 50-day above 200-day) + take profit at 2R"
SHORT = "UpGol+2R"
SIZE = 1.0
KW = dict(exit_mode='lh', trend='golden', reentry=False, tp_r=2)          # the rule's settings (also used by trade_charts.py)
EXIT = {"stop_atr": 30.0, "target_r": None, "trail_days": None, "max_days": None}   # exits come from exits()


def entries(d):
    return _structure.run(d, **KW)["entry"]


def exits(d):
    return _structure.run(d, **KW)["exit"]
