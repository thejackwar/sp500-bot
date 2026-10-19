"""Your rule (btc50_uprise_ma_f1) + real cross (close 0.5 ATR above the 50-day). Everything else as the live Bitcoin rule; tested on SPY
(each-stock mode) to filter out the small crosses in pullbacks. Rules in strategies/_structure.py."""
from strategies import _structure

MARKETS = ("crypto", "stocks")
NAME = "Your rule + real cross (close 0.5 ATR above the 50-day)"
SHORT = "R cross"
SIZE = 1.0
KW = dict(exit_mode='lh', trend='rising50', reentry=False, ma_exit='close', lh_right=1, cross_atr=0.5)
EXIT = {"stop_atr": 30.0, "target_r": None, "trail_days": None, "max_days": None}   # exits come from exits()


def entries(d):
    return _structure.run(d, **KW)["entry"]


def exits(d):
    return _structure.run(d, **KW)["exit"]
