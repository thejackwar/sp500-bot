"""Bitcoin only: the live rule (btc50_uprise_ma_f1) + volume dry-up: the 10 days before the cross averaged below the 50-day average volume,
and the cross day's volume is above it.
Everything else exactly as the live rule (buy the daily close crossing above the rising 50-day average; sell on
a lower high after 1 day, a close below the 50-day or the starting stop; no buying back until the next fresh
cross). A cross that fails the volume check is skipped. Volume = the price source's daily volume: use the
backtest's "daily (from 2015)" source (Yahoo/Coinbase market volume) - Alpaca's crypto volume only counts trades
on Alpaca. Rules in strategies/_structure.py (vol=...)."""
from strategies import _structure

MARKETS = ("crypto",)
NAME = "Volume 3: your rule + dry-up (quiet 10 days, then an above-average cross day)"
SHORT = "Vol dry"
SIZE = 1.0
VOLUME = True                    # the backtest passes the daily volume
KW = dict(exit_mode='lh', trend='rising50', reentry=False, ma_exit='close', lh_right=1, vol="dry")
EXIT = {"stop_atr": 30.0, "target_r": None, "trail_days": None, "max_days": None}   # exits come from exits()


def entries(d):
    return _structure.run(d, **KW)["entry"]


def exits(d):
    return _structure.run(d, **KW)["exit"]
