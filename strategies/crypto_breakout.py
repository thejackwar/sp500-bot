"""Crypto breakout: the bot's idea on the daily chart only, adapted to crypto. Buy a new 20-day
high while the coin is above its 100-day average and Bitcoin is in an up-trend (above its 50-day
average); wider stop (3 x ATR) for crypto's big daily swings; sell below the 20-day low."""
from strategies import tools

MARKETS = ("crypto",)
NAME = "Crypto daily breakout, 3×ATR stop, BTC filter"
SHORT = "CBreak"
EXIT = {"stop_atr": 3.0, "target_r": None, "trail_days": 20, "max_days": None}


def entries(d):
    c = d["Close"]
    return (tools.crossed_above(c, tools.highest(d["High"], 20)) & (c > tools.sma(c, 100))
            & tools.market_up(d))


def rank(d):
    return tools.change(d["Close"], 90)        # strongest 3-month gain first
