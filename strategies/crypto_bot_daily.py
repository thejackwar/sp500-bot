"""The bot's rule on the daily chart only, with the BTC filter: the baseline the other new crypto
ideas are compared against (same entry as the bot, without its 15-min/hourly signals). Stop 2xATR,
sold below the 20-day low, buys only while Bitcoin is above its 50-day average."""
from strategies import tools

MARKETS = ("crypto",)
NAME = "Bot rule, daily chart only + BTC filter"
SHORT = "BotD"
EXIT = {"stop_atr": 2.0, "target_r": None, "trail_days": 20, "max_days": None}


def entries(d):
    return tools.bot_long(d) & tools.market_up(d)


def rank(d):
    return tools.change(d["Close"], 90)
