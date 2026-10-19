"""Early: bounce at support / Fib, above 200-day MA, 20-day-low trail.
Setup details in strategies/_setups.py. Buys at the next day's open; stop just below the support;
no target: sold below the 20-day low (like the bot) or at the stop below support."""
from strategies import _setups

MARKETS = ("crypto",)
NAME = "Early: bounce at support / Fib, above 200-day MA, 20-day-low trail"
SHORT = "ESupTr"
EXIT = {"stop_atr": 2.0, "target_r": None, "trail_days": 20, "max_days": None}


def setups(d):
    return _setups.pick(d, 'support', above200=True)
