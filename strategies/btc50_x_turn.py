"""Your rule (btc50_uprise_ma_f1) + turn-up entry (buy when the 50-day turns up, price above it). Everything else as the live Bitcoin rule; tested on SPY
(each-stock mode) to filter out the small crosses in pullbacks. Rules in strategies/_structure.py."""
from strategies import _structure

MARKETS = ("crypto", "stocks")
NAME = "Your rule + turn-up entry (buy when the 50-day turns up, price above it)"
SHORT = "R turn"
SIZE = 1.0
KW = dict(exit_mode='lh', trend='rising50', reentry=False, ma_exit='close', lh_right=1, turn=True)
EXIT = {"stop_atr": 30.0, "target_r": None, "trail_days": None, "max_days": None}   # exits come from exits()


def entries(d):
    return _structure.run(d, **KW)["entry"]


def exits(d):
    return _structure.run(d, **KW)["exit"]
