"""
TEMPLATE: copy this file, rename it (e.g. my_idea.py, no spaces) and change the rules.
Files starting with "_" are ignored by the backtest, so this one never runs.

How a strategy is tested (backtest.py):
  - entries(d) is called once per stock with its DAILY candles: a DataFrame with the columns
    Open, High, Low, Close (one row per day, oldest first). Return True on the days whose CLOSE
    gives a buy signal. The backtest buys at the NEXT day's opening price.
  - Same money rules as the bot: risk 1% of the account per trade (distance to the stop),
    max 20% of the account per stock, max 10 positions, max 3 new trades a day, never more than
    100% of the account invested (trader.py settings).
  - Exits (EXIT below), whichever comes first:
      stop_atr    stop-loss this many daily ATRs below the entry (always on; it also sizes the trade)
      target_r    take profit at this many times the stop distance (None = no target)
      trail_days  sell when the price falls below the lowest low of this many days (None = off)
      max_days    sell after this many trading days (None = off)
      exits(d)    optional: return True on days whose close gives a sell signal (sold next open)
  - entry_level(d) optional, instead of entries(d): buy DURING the day. Return one price per day
    (NaN = no trade that day), using only what is known when that day starts (its Open, and
    yesterday's values via .shift(1)). The backtest buys at the next 15-min candle after the first
    15-min close above that day's level (see btc_vol_breakout.py).
  - rank(d) optional: when more stocks signal on the same day than there is room for, the
    highest rank goes first.

Rules for honest results: only use today's and earlier values (tools.py helpers do this).
Never use .shift(-1) or anything that looks at future days.
"""
from strategies import tools

NAME = "My strategy"          # shown in the report
SHORT = "Mine"                # short label for the yearly table (max 7 letters)
EXIT = {"stop_atr": 2.0, "target_r": None, "trail_days": 20, "max_days": None}


def entries(d):
    c = d["Close"]
    return (c > tools.sma(c, 200)) & tools.crossed_above(c, tools.highest(d["High"], 50))


# def exits(d):
#     return d["Close"] < tools.sma(d["Close"], 50)

# def rank(d):
#     return d["Close"] / tools.sma(d["Close"], 200)
