"""RSI(2) dip-buying, short holding: buy at the next open after a day when RSI(2) closed below 10
while the price is above its 200-day average (a sharp dip in an up-trend). Sold at the next open
after RSI(2) closes above 70, or after 2 days at most; stop 2xATR."""
from strategies import tools

MARKETS = ("crypto",)
NAME = "RSI(2) dip below 10 in up-trend, sell RSI above 70 or after 2 days"
SHORT = "RSI2s"
EXIT = {"stop_atr": 2.0, "target_r": None, "trail_days": None, "max_days": 2}


def entries(d):
    c = d["Close"]
    return (c > tools.sma(c, 200)) & (tools.rsi(c, 2) < 10)


def exits(d):
    return tools.rsi(d["Close"], 2) > 70


def rank(d):
    return -tools.rsi(d["Close"], 2)
