"""Crypto moving-average cross: buy when the 20-day average crosses above the 50-day average,
sell when it crosses back below. A faster version of the golden cross for crypto's quick trends."""
from strategies import tools

MARKETS = ("crypto",)
NAME = "Crypto 20/50-day MA cross"
SHORT = "C20/50"
EXIT = {"stop_atr": 3.0, "target_r": None, "trail_days": None, "max_days": None}


def entries(d):
    c = d["Close"]
    return tools.crossed_above(tools.sma(c, 20), tools.sma(c, 50))


def exits(d):
    c = d["Close"]
    return tools.sma(c, 20) < tools.sma(c, 50)


def rank(d):
    return tools.change(d["Close"], 90)
