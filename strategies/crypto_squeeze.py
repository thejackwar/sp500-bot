"""Volatility squeeze breakout: the Bollinger Bands were at their narrowest in ~6 months (125 days)
within the last 10 days, and the close now breaks above the upper band, while Bitcoin is above its
50-day average. Quiet markets often come before big moves. Stop 2xATR, sold below the 20-day low."""
from strategies import tools

MARKETS = ("crypto",)
NAME = "Squeeze breakout (Bollinger narrowest in 6 months), BTC filter"
SHORT = "Squeeze"
EXIT = {"stop_atr": 2.0, "target_r": None, "trail_days": 20, "max_days": None}


def entries(d):
    c = d["Close"]
    mid, upper, lower = tools.bollinger(c, 20, 2.0)
    width = (upper - lower) / mid
    squeeze = (width <= width.rolling(125).min().shift(1)).rolling(11, min_periods=1).max().astype(bool)
    return tools.crossed_above(c, upper) & squeeze & tools.market_up(d)


def rank(d):
    return tools.change(d["Close"], 90)
