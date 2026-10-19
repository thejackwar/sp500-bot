"""Previous-day high breakout, held 2 days at most. Buy during the day when the price closes a
15-min candle above yesterday's high, if Bitcoin closed above its 50-day average yesterday.
Stop 1xATR below the entry, take profit at 2x the stop distance, otherwise sold after 2 days."""
from strategies import tools

MARKETS = ("crypto",)
NAME = "Previous-day high breakout, 1×ATR stop, 2R target, max 2 days"
SHORT = "PrevHi"
EXIT = {"stop_atr": 1.0, "target_r": 2.0, "trail_days": None, "max_days": 2}


def entry_level(d):
    up = tools.market_up(d).shift(1, fill_value=False)
    return d["High"].shift(1).where(up)
