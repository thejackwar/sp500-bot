"""Bot rule (daily) + BTC filter, with a Chandelier exit instead of the 20-day low: sold when the
close falls more than 3 x ATR(22) below the highest high of the last 22 days. The stop follows the
price up and adapts to volatility, to keep big winners and leave crashes sooner."""
from strategies import tools

MARKETS = ("crypto",)
NAME = "Bot rule (daily) + BTC filter, Chandelier exit (3×ATR from 22-day high)"
SHORT = "Chand"
EXIT = {"stop_atr": 3.0, "target_r": None, "trail_days": None, "max_days": None}


def entries(d):
    return tools.bot_long(d) & tools.market_up(d)


def exits(d):
    return d["Close"] < d["High"].rolling(22).max() - 3 * tools.atr(d, 22)


def rank(d):
    return tools.change(d["Close"], 90)
