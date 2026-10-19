"""
The user's swing rule (19 Oct 2026): buy the cross above the 50-day average, sell only when the uptrend
breaks with a LOWER LOW. Shared by the swing_*.py strategy files (starts with "_": not run on its own).

    buy        a daily close crosses above the 50-day average (yesterday's close at or below it); bought at
               the next open. No other condition (the exit decides when the trend is over).
    low to     a swing low = a day whose low is lower than the PIVOT days before and after it (known PIVOT days
    hold       later, no look-ahead). At the buy it is the last swing low known before the buy (below the entry;
               without one, the lowest low of the last 20 days). After the buy, every new swing low that is
               HIGHER than the current low to hold becomes the new one (a higher low = the uptrend goes on).
    sell       mode "close": a daily close below the low to hold -> sell at the next open.
               mode "touch": the price touches below the low to hold during the day -> sold right there, like
               a stop order (the backtest fills at that level, or at the open if it opens below it).
               Nothing else sells: no 50-day exit, no lower-high exit.
    again      after a sell, buy again only on the next cross above the 50-day.
"""
import numpy as np
import pandas as pd

from strategies import tools

_cache = {}


def run(d, pivot=5, mode="close"):
    """Daily candles -> DataFrame: entry (signal on that close), exit (signal on that close; mode "close"
    only), stop (the low to hold after that day's close, while in a trade), reason."""
    key = (pivot, mode, d.index[0], d.index[-1], len(d), float(d["Close"].iat[-1]))
    if key in _cache:
        return _cache[key]
    o, h, l, c = (d[k].values.astype(float) for k in ("Open", "High", "Low", "Close"))
    n, P = len(d), pivot
    sma50 = tools.sma(d["Close"], 50).values
    piv = np.zeros(n, bool)                          # day j is a swing low (known at day j + P)
    for j in range(P, n - P):
        if l[j] < l[j - P:j].min() and l[j] <= l[j + 1:j + P + 1].min():
            piv[j] = True

    entry, exit_ = np.zeros(n, bool), np.zeros(n, bool)
    stop_s = np.full(n, np.nan)
    reason = np.full(n, "", dtype=object)
    pos, pending, protect, e_day = False, False, None, None
    for i in range(1, n):
        if pending:                                  # bought at today's open
            pos, pending, e_day = True, False, i
        if not pos:
            if np.isfinite(sma50[i]) and np.isfinite(sma50[i - 1]) and c[i] > sma50[i] and c[i - 1] <= sma50[i - 1]:
                entry[i], pending = True, True
                reason[i] = "cross above the 50-day"
                # the low to hold, known at this close: the last swing low below the close
                known = [j for j in range(max(0, i - 120), i - P + 1) if piv[j] and l[j] < c[i]]
                protect = l[known[-1]] if known else l[max(0, i - 19):i + 1].min()
                stop_s[i] = protect                  # in force from the buy (next open) on
            continue
        # in a trade: the low to hold as it stood at yesterday's close
        held = protect
        if mode == "touch" and l[i] < held:
            pos, protect = False, None
            reason[i] = "lower low (touched)"
            continue
        j = i - P                                    # a swing low confirmed today, after the buy
        if j > e_day and piv[j] and l[j] > protect:
            protect = l[j]                           # a higher low: the uptrend goes on
        if mode == "close" and c[i] < held:
            exit_[i], pos, protect = True, False, None
            reason[i] = "lower low (close)"
            continue
        stop_s[i] = protect
    out = pd.DataFrame({"entry": entry, "exit": exit_, "stop": stop_s, "reason": reason}, index=d.index)
    _cache[key] = out
    return out
