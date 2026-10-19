"""
Bitcoin: buy the cross above the 50-day average, sell on a failed high (the user's rule, 19 Oct 2026).
Shared by the btc50_*.py strategy files (this file starts with "_", so the backtest doesn't run it alone).

All decisions are made on a DAILY CLOSE (UTC day) and carried out at the NEXT day's open (00:00 UTC),
which is what the backtest does with entries()/exits(). Stops are judged on closes, not wicks, so
the bad price spikes in Alpaca's crypto history can't set them off.

Swing points: a swing high is a day whose high is above the PIVOT days before and after it; it is
only known PIVOT days later (no look-ahead). Swing lows the same way with lows.

    entry        close crosses above the 50-day average (yesterday's close at or below it),
                 optionally only while the close is above the 200-day average; line="both": crosses
                 above the HIGHER of the 50- and 200-day averages
    trend        uptrend filter for the entry: None, "rising50" (50-day average higher than 10 days
                 ago) or "golden" (50-day average above the 200-day); above200=True = close above the 200-day
    ma_exit      True / "close": also sell after a close below that line (the hold rule's exit);
                 "touch": also sell after a day whose low comes back down to the line, once price has
                 traded fully above it since the entry (the user's "came back down and hit the 50-day")
    start stop   sell if a close falls below the last swing low before the entry, kept between
                 1 and 3 ATR below the entry (protection before any high has formed)
    exit "lh"    LOWER HIGH: after the entry, the highest high so far is the "previous high"; a new
                 swing high BELOW it means the bounce failed and price turned down -> sell
    exit "break" lower high, then a close below the swing low between the previous high and the
                 lower high (the classic trend-break confirmation) -> sell
    exit "hl"    trail the stop up to each new swing low after the entry (higher lows); a close
                 below the latest one -> sell
    take profit  optional: tp_r = sell after a close at entry + tp_r x the starting risk;
                 stretch = sell after a close this far above the 50-day average (0.20 = 20%)
    re-entry     after a lower-high / trend-break sell, buy again when a close is above the high that
                 failed (the trend resumed) while above the 50-day average; after a starting stop or a
                 take profit, wait for a new cross
"""
import numpy as np
import pandas as pd

from strategies import tools

PIVOT = 3
_cache = {}


def run(d, exit_mode="lh", tp_r=None, stretch=None, reentry=True, above200=False, line="50", ma_exit=False,
        start_stop=True, trend=None):
    """Replays the rule on daily candles. Returns a DataFrame indexed like d with columns
    entry (signal on that close), exit (signal on that close), stop (starting/trailing stop while in a
    trade), peak (highest high since entry), lh (a lower high was confirmed that day), reason."""
    key = (exit_mode, tp_r, stretch, reentry, above200, line, ma_exit, start_stop, trend,
           d.index[0], d.index[-1], len(d), float(d["Close"].iat[-1]))
    if key in _cache:
        return _cache[key]
    o, h, l, c = (d[k].values.astype(float) for k in ("Open", "High", "Low", "Close"))
    n = len(d)
    sma50 = tools.sma(d["Close"], 50).values
    sma200 = tools.sma(d["Close"], 200).values
    atr = tools.atr(d).values
    # the line to cross: the 50-day average, or "both" = the higher of the 50- and 200-day averages
    ln = sma50 if line == "50" else np.where(np.isfinite(sma200), np.maximum(sma50, sma200), np.nan)
    P = PIVOT
    piv_hi = np.zeros(n, bool)                       # day j is a swing high (known at day j + P)
    piv_lo = np.zeros(n, bool)
    for j in range(P, n - P):
        if h[j] > h[j - P:j].max() and h[j] >= h[j + 1:j + P + 1].max():
            piv_hi[j] = True
        if l[j] < l[j - P:j].min() and l[j] <= l[j + 1:j + P + 1].min():
            piv_lo[j] = True

    entry = np.zeros(n, bool)
    exit_ = np.zeros(n, bool)
    stop_s = np.full(n, np.nan)
    peak_s = np.full(n, np.nan)
    lh_s = np.zeros(n, bool)
    reason = np.full(n, "", dtype=object)

    pos = False
    pending_entry = False
    e_day = e_price = stop = risk = peak = None
    peak_day = None
    brk = None                                       # "break": level to watch after a lower high
    failed_high = None                               # after a sell on a failed high: re-entry level
    for i in range(1, n):
        if pending_entry:                            # bought at today's open
            pos, pending_entry = True, False
            e_day, e_price = i, o[i]
            lows = [j for j in range(max(0, i - 60), i - P) if piv_lo[j]]   # swing lows known by yesterday
            sl = l[lows[-1]] if lows else e_price - 2 * atr[i - 1]
            a = atr[i - 1]
            stop = min(max(sl, e_price - 3 * a), e_price - a)
            risk = e_price - stop
            peak, peak_day, brk, trailed, lifted = h[i], i, None, False, False
        if not pos:
            if not np.isfinite(ln[i]) or (above200 and not (np.isfinite(sma200[i]) and c[i] > sma200[i])):
                continue
            if trend == "rising50" and not (i >= 10 and np.isfinite(sma50[i - 10]) and sma50[i] > sma50[i - 10]):
                continue
            if trend == "golden" and not (np.isfinite(sma200[i]) and sma50[i] > sma200[i]):
                continue
            # "both": the first day the line exists counts too (that is when the hold rule can first buy);
            # "50": only a real cross from below
            cross = c[i] > ln[i] and (c[i - 1] <= ln[i - 1] or (line != "50" and not np.isfinite(ln[i - 1])))
            resume = reentry and failed_high is not None and c[i] > failed_high and c[i] > ln[i]
            if cross or resume:
                entry[i] = True
                pending_entry = True
                reason[i] = "cross" if cross else "new high"
                failed_high = None
            continue

        # in a trade: update the highest high (the "previous high" a bounce must beat)
        if h[i] > peak:
            peak, peak_day, brk = h[i], i, None
        j = i - P                                    # swing points confirmed today
        why = None
        if j > e_day:
            if piv_hi[j] and h[j] < max(h[e_day:j]) and j > peak_day:
                lh_s[i] = True
                if exit_mode == "lh":
                    why = "lower high"
                elif exit_mode == "break" and brk is None:
                    brk = l[peak_day:j + 1].min()    # the pullback low between the high and the lower high
            if exit_mode == "hl" and piv_lo[j] and l[j] > stop:
                stop, trailed = l[j], True
        if why is None and exit_mode == "break" and brk is not None and c[i] < brk:
            why = "broke the pullback low"
        if l[i] > ln[i]:
            lifted = True                            # traded fully above the line since the entry
        if why is None and ma_exit == "touch" and lifted and l[i] <= ln[i]:
            why = "came back to the 50-day" if line == "50" else "came back to the line"
        if why is None and ma_exit and ma_exit != "touch" and c[i] < ln[i]:
            why = "close below the 50-day" if line == "50" else "close below the line"
        if why is None and start_stop and c[i] < stop:
            why = "below higher low" if trailed else "starting stop"
        if why is None and tp_r and c[i] >= e_price + tp_r * risk:
            why = f"take profit {tp_r:g}R"
        if why is None and stretch and np.isfinite(sma50[i]) and c[i] >= sma50[i] * (1 + stretch):
            why = f"{stretch:.0%} above 50-day"
        stop_s[i], peak_s[i] = stop, peak
        if why:
            exit_[i] = True
            reason[i] = why
            pos = False
            # re-entry on a new high only after a failed-high / trend-break sell; after a starting stop or a
            # take profit, wait for a fresh cross (else it buys straight back in, higher, paying fees twice)
            failed_high = peak if why in ("lower high", "broke the pullback low", "below higher low") else None
    out = pd.DataFrame({"entry": entry, "exit": exit_, "stop": stop_s, "peak": peak_s, "lh": lh_s,
                        "reason": reason}, index=d.index)
    _cache[key] = out
    return out
