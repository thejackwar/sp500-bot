"""Turtle-style breakout (the classic trend-following system): buy a new 55-day high while Bitcoin
is above its 50-day average; stop 2xATR, sold below the 20-day low. Slower than the bot, so fewer
trades and fees, and it aims to catch only the big trends."""
from strategies import tools

MARKETS = ("crypto",)
NAME = "Turtle: 55-day high, 20-day-low exit, BTC filter"
SHORT = "Turtle"
EXIT = {"stop_atr": 2.0, "target_r": None, "trail_days": 20, "max_days": None}


def entries(d):
    return tools.crossed_above(d["Close"], tools.highest(d["High"], 55)) & tools.market_up(d)


def rank(d):
    return tools.change(d["Close"], 90)
