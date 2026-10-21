"""Market structure only (no moving averages), swing size from volatility: a turn counts once the price moved
2 x the 14-day ATR from the high / low. Long while the swings say UPTREND (a higher low or a close above the
last swing high), cash in a DOWNTREND (a lower high or a close below the last swing low).
Rules in strategies/_swing.py (structure_only=True, atr_k=2)."""
from strategies import _swing

MARKETS = ("crypto", "stocks")
NAME = "Structure only: swings of 2x ATR, uptrend = any HL or HH"
SHORT = "St 2ATR"
SIZE = 1.0
KW = dict(structure_only=True, atr_k=2)
EXIT = {"stop_atr": 30.0, "target_r": None, "trail_days": None, "max_days": None}


def entries(d):
    """Long while the trend is UP (a buy signal on every UP day, ignored while already holding)."""
    return _swing.run(d, **KW)["trend_up"]


def exits(d):
    return ~_swing.run(d, **KW)["trend_up"]
