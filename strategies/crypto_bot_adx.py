"""Bot rule (daily) + BTC filter, but only when ADX(14) is above 25 = the coin is in a real trend.
The idea: skip breakouts out of sideways markets, which cause most false breakouts and losing streaks."""
from strategies import tools

MARKETS = ("crypto",)
NAME = "Bot rule (daily) + BTC filter + ADX above 25"
SHORT = "BotADX"
EXIT = {"stop_atr": 2.0, "target_r": None, "trail_days": 20, "max_days": None}


def entries(d):
    return tools.bot_long(d) & tools.market_up(d) & (tools.adx(d) > 25)


def rank(d):
    return tools.change(d["Close"], 90)
