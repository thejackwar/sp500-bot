"""Crypto time-series momentum (the effect found in academic studies of crypto): buy when a coin's
4-week return turns positive, sell when it turns negative. Simple trend following on returns."""
from strategies import tools

MARKETS = ("crypto",)
NAME = "Crypto 4-week momentum"
SHORT = "C4wMom"
EXIT = {"stop_atr": 3.0, "target_r": None, "trail_days": None, "max_days": None}


def entries(d):
    return tools.crossed_above(tools.change(d["Close"], 28), 0)


def exits(d):
    return tools.change(d["Close"], 28) < 0


def rank(d):
    return tools.change(d["Close"], 28)        # strongest 4-week gain first
