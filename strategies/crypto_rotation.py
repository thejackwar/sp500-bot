"""Crypto momentum rotation: hold the coins with the best 3-month gain, as long as they are above
their 50-day average and Bitcoin is in an up-trend. A coin that drops below its 50-day average is
sold; the free money goes to the next strongest coin. With max 5 positions, this keeps the top 5."""
from strategies import tools

MARKETS = ("crypto",)
NAME = "Crypto 3-month momentum rotation, BTC filter"
SHORT = "CRotate"
EXIT = {"stop_atr": 3.0, "target_r": None, "trail_days": None, "max_days": None}


def entries(d):
    c = d["Close"]
    return (tools.change(c, 90) > 0) & (c > tools.sma(c, 50)) & tools.market_up(d)


def exits(d):
    c = d["Close"]
    return (c < tools.sma(c, 50)) | ~tools.market_up(d)


def rank(d):
    return tools.change(d["Close"], 90)
