"""Market structure only (no moving averages): long while the swings say UPTREND, cash in a DOWNTREND.
Swings = zigzag turns of at least 2%. Up = strict HH + HL; down = a lower high or a close below the last swing low.
Rules in strategies/_swing.py (structure_only=True)."""
from strategies import _swing

MARKETS = ("crypto", "stocks")
NAME = "Structure only: swings 2%, uptrend = strict HH + HL"
SHORT = "St 2% strict"
SIZE = 1.0
KW = dict(pull_min=0.02, structure_only=True, strict=True)
EXIT = {"stop_atr": 30.0, "target_r": None, "trail_days": None, "max_days": None}


def entries(d):
    """Long while the trend is UP: a buy signal on every UP day (ignored while already holding), so a test that
    starts in the middle of an uptrend buys on day one instead of waiting for the next turn."""
    return _swing.run(d, **KW)["trend_up"]


def exits(d):
    return ~_swing.run(d, **KW)["trend_up"]
