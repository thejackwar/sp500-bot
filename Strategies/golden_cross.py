"""Golden cross: buy when the 50-day average crosses above the 200-day average,
sell when it crosses back below (the "death cross"). Slow, long-term trend following."""
from strategies import tools

NAME = "Golden cross (50/200-day averages)"
SHORT = "GoldX"
EXIT = {"stop_atr": 4.0, "target_r": None, "trail_days": None, "max_days": None}


def entries(d):
    c = d["Close"]
    return tools.crossed_above(tools.sma(c, 50), tools.sma(c, 200))


def exits(d):
    c = d["Close"]
    return tools.sma(c, 50) < tools.sma(c, 200)
