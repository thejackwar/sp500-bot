"""Early: bounce at major support / Fib 50-62%, above 200-day MA → next resistance.
Setup details in strategies/_setups.py. Buys at the next day's open; stop just below the support;
sold at the target (the next resistance), at the stop, or after 60 days."""
from strategies import _setups

MARKETS = ("crypto",)
NAME = "Early: bounce at major support / Fib 50-62%, above 200-day MA → next resistance"
SHORT = "ESup"
EXIT = {"stop_atr": 2.0, "target_r": None, "trail_days": None, "max_days": 60, "to_target": True}


def setups(d):
    return _setups.pick(d, 'support', above200=True)
