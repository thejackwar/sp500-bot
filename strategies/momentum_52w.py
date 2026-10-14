"""52-week-high momentum: buy stocks closing at a new 1-year high (strongest stocks keep
leading), ride the trend and sell when the price falls below its 50-day low."""
from strategies import tools

NAME = "52-week-high momentum"
SHORT = "52wHi"
EXIT = {"stop_atr": 3.0, "target_r": None, "trail_days": 50, "max_days": None}


def entries(d):
    c = d["Close"]
    return (c > tools.highest(d["High"], 252)) & (c > tools.sma(c, 200))


def rank(d):
    c = d["Close"]
    return c / c.shift(126) - 1                # strongest 6-month gain first
