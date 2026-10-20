"""Market structure only (no moving averages): long while the swings say UPTREND, cash in a DOWNTREND.
Swings = zigzag turns of at least 2%. Up = any HL or HH; down = a lower high or a close below the last swing low.
Rules in strategies/_swing.py (structure_only=True)."""
from strategies import _swing

MARKETS = ("crypto", "stocks")
NAME = "Structure only: swings 2%, uptrend = any HL or HH"
SHORT = "St 2%"
SIZE = 1.0
KW = dict(pull_min=0.02, structure_only=True, strict=False)
EXIT = {"stop_atr": 30.0, "target_r": None, "trail_days": None, "max_days": None}


def entries(d):
    return _swing.run(d, **KW)["entry"]


def exits(d):
    return _swing.run(d, **KW)["exit"]
