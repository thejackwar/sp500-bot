"""RSI(2) pullback (Larry Connors): buy short, sharp dips in stocks that are in an up-trend,
sell when they bounce back. Many small wins, holds only a few days."""
from strategies import tools

MARKETS = ("stocks",)          # lost money on crypto in every test (fees eat the small wins)
NAME = "RSI(2) pullback: buy dips in up-trends"
SHORT = "RSI2"
EXIT = {"stop_atr": 3.0, "target_r": None, "trail_days": None, "max_days": 10}


def entries(d):
    c = d["Close"]
    return (c > tools.sma(c, 200)) & (tools.rsi(c, 2) < 10)


def exits(d):
    c = d["Close"]
    return c > tools.sma(c, 5)                 # bounced back above its 5-day average


def rank(d):
    return -tools.rsi(d["Close"], 2)           # deepest dip first
