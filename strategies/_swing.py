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

    trail="pullback" (the user's refinement, 20 Oct 2026): no fixed swing size. At the buy, the low to hold is
               the lowest low of the last 10 days (the dip before the cross). Then, whenever a day's high beats
               the highest high so far, the lowest low of the pullback since that previous high (at least one
               day that made no new high) becomes the new low to hold, if it is higher. A close (or touch) below
               it is the lower low that ends the trend. pull_min (e.g. 0.01): a pullback only counts if its low
               is at least that far below the high before it, so 1-day pauses don't move the low to hold.
    hh_entry   (the user's buy filter, 20 Oct 2026): buy only when the close is above the 50-day AND above the
               last swing high - a higher high, so the downtrend (lower highs) is over. Swing highs/lows here
               are zigzag turns of at least pull_min (a high counts once the price fell that far from it, a low
               once it rose that far). The buy is the first close where both hold; the low to hold at the buy
               is the last swing low before it.
    trend_entry (the user's full trend read, 20 Oct 2026): the same zigzag swings give a trend state. It turns
               UP on a higher low (a new swing low above the previous one) or a close above the last swing high;
               DOWN on a lower high (a new swing high below the previous one) or a close below the last swing
               low. Buy = the first close above the 50-day while the trend is UP.
    above200   the buy also needs the close above the 200-day average (the sell rule is unchanged).
"""
import numpy as np
import pandas as pd

from strategies import tools

_cache = {}


def run(d, pivot=5, mode="close", trail="pivot", pull_min=0.0, hh_entry=False, trend_entry=False, above200=False):
    """Daily candles -> DataFrame: entry (signal on that close), exit (signal on that close; mode "close"
    only), stop (the low to hold after that day's close, while in a trade), reason."""
    key = (pivot, mode, trail, pull_min, hh_entry, trend_entry, above200, d.index[0], d.index[-1], len(d), float(d["Close"].iat[-1]))
    if key in _cache:
        return _cache[key]
    o, h, l, c = (d[k].values.astype(float) for k in ("Open", "High", "Low", "Close"))
    n, P = len(d), pivot
    sma50 = tools.sma(d["Close"], 50).values
    sma200 = tools.sma(d["Close"], 200).values
    piv = np.zeros(n, bool)                          # day j is a swing low (known at day j + P)
    for j in range(P, n - P):
        if l[j] < l[j - P:j].min() and l[j] <= l[j + 1:j + P + 1].min():
            piv[j] = True

    # zigzag swing highs / lows (turns of at least zz), as known at each close (hh_entry)
    zz = pull_min or 0.005
    last_sh, last_sl = np.full(n, np.nan), np.full(n, np.nan)
    leg, ch, cl, sh, sl = "up", h[0], l[0], np.nan, np.nan
    trend_up = np.zeros(n, bool)                     # trend_entry: UP after a higher low / higher high
    up = False
    for i in range(n):
        if leg == "up":
            if h[i] > ch:
                ch = h[i]
            elif l[i] <= ch * (1 - zz):              # fell zz from the high: that high is a swing high
                if np.isfinite(sh) and ch < sh:
                    up = False                       # a lower high: downtrend
                sh, leg, cl = ch, "down", l[i]
        else:
            if l[i] < cl:
                cl = l[i]
            elif h[i] >= cl * (1 + zz):              # rose zz from the low: that low is a swing low
                if np.isfinite(sl) and cl > sl:
                    up = True                        # a higher low: uptrend
                sl, leg, ch = cl, "up", h[i]
        if np.isfinite(sh) and c[i] > sh:
            up = True                                # a higher high
        if np.isfinite(sl) and c[i] < sl:
            up = False                               # a lower low
        last_sh[i], last_sl[i], trend_up[i] = sh, sl, up
    ok_buy = np.zeros(n, bool)                       # buy condition at each close
    for i in range(n):
        above50 = np.isfinite(sma50[i]) and c[i] > sma50[i]
        ok_buy[i] = above50 and (not hh_entry or (np.isfinite(last_sh[i]) and c[i] > last_sh[i])) \
            and (not trend_entry or trend_up[i]) and (not above200 or (np.isfinite(sma200[i]) and c[i] > sma200[i]))

    entry, exit_ = np.zeros(n, bool), np.zeros(n, bool)
    stop_s = np.full(n, np.nan)
    reason = np.full(n, "", dtype=object)
    pos, pending, protect, e_day = False, False, None, None
    hi, hi_day = None, None                          # trail="pullback": highest high so far and its day
    for i in range(1, n):
        if pending:                                  # bought at today's open
            pos, pending, e_day = True, False, i
            hi, hi_day = h[i - 1], i - 1             # the signal day's high is the first high to beat
        if not pos:
            if ok_buy[i] and not ok_buy[i - 1]:
                entry[i], pending = True, True
                reason[i] = ("uptrend + above the 50-day" if trend_entry else
                             "higher high above the 50-day" if hh_entry else "cross above the 50-day")
                # the low to hold, known at this close: the last swing low below the close
                if (hh_entry or trend_entry) and np.isfinite(last_sl[i]) and last_sl[i] < c[i]:
                    protect = last_sl[i]                 # the last swing low before the breakout
                elif trail == "pullback":                # the low of the dip before the cross
                    protect = l[max(0, i - 9):i + 1].min()
                else:
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
        if trail == "pullback":
            if h[i] > hi:                            # a new high: lock the pullback low since the last high
                if i - hi_day >= 2:                  # at least one day in between that made no new high
                    pull = l[hi_day + 1:i].min()
                    # pull_min: only a real pullback counts (at least this far below the high before it)
                    if pull > protect and pull <= hi * (1 - pull_min):
                        protect = pull               # a higher low: the uptrend goes on
                hi, hi_day = h[i], i
        else:
            j = i - P                                # a swing low confirmed today, after the buy
            if j > e_day and piv[j] and l[j] > protect:
                protect = l[j]                       # a higher low: the uptrend goes on
        if mode == "close" and c[i] < held:
            exit_[i], pos, protect = True, False, None
            reason[i] = "lower low (close)"
            continue
        stop_s[i] = protect
    out = pd.DataFrame({"entry": entry, "exit": exit_, "stop": stop_s, "reason": reason}, index=d.index)
    _cache[key] = out
    return out
