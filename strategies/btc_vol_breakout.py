"""Volatility breakout (Larry Williams style), a popular 1-day crypto strategy.
Buy during the day when the price rises above: today's open + 0.5 x yesterday's range (high - low),
but only if Bitcoin closed above its 50-day average yesterday. Sold at the next day's open
(00:00 UTC), or earlier at the 2xATR stop. The idea: a big move away from the open tends to
continue for the rest of the day. Bought on the first 15-min close above the level."""
from strategies import tools

MARKETS = ("crypto",)
NAME = "Volatility breakout: open + 0.5× yesterday's range, sell next open"
SHORT = "VolBO"
EXIT = {"stop_atr": 2.0, "target_r": None, "trail_days": None, "max_days": 1}
K = 0.5


def entry_level(d):
    rng = (d["High"] - d["Low"]).shift(1)                 # yesterday's range
    level = d["Open"] + K * rng                           # today's open is known when the day starts
    up = tools.market_up(d).shift(1, fill_value=False)    # yesterday's close: BTC above its 50-day MA
    return level.where(up)
