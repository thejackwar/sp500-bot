"""Bitcoin only: the live Bitcoin rule (btc50_uprise_ma_f1, daily candles) traded with 10x leverage on a
perpetual futures exchange: the position is 10x the account. Costs: the fee per buy and sell (workflow
input, 0.05% = futures taker), funding while long (FUNDING a year on the whole position; 0.01% every 8 hours
= 10.95%/yr is the usual Bitcoin rate), and liquidation: if a 15-min low falls 1/10 (less 0.5% maintenance
margin) below the entry, the position is closed there and the margin is gone. Backtest only."""
from strategies import _structure

MARKETS = ("crypto",)
NAME = "10x leverage: your daily rule on perpetual futures (funding ~11%/yr, liquidation)"
SHORT = "Lev 10x"
SIZE = 1.0
LEVERAGE = 10
FUNDING = 0.1095
KW = dict(exit_mode='lh', trend='rising50', reentry=False, ma_exit='close', lh_right=1)
EXIT = {"stop_atr": 30.0, "target_r": None, "trail_days": None, "max_days": None}   # exits come from exits()


def entries(d):
    return _structure.run(d, **KW)["entry"]


def exits(d):
    return _structure.run(d, **KW)["exit"]
