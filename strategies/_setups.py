"""
Early-entry setups: buy NEAR SUPPORT, before the move up, instead of after a breakout.
Shared by the early_*.py strategy files (this file starts with "_", so the backtest doesn't run it
on its own). Every setup is judged on a day's close (bought at the next day's open) and gives its
own stop (just below the support) and target (the next resistance above).

    bottom   a bottom pattern still FORMING (double / triple bottom, inverse head & shoulders,
             falling wedge, rounding bottom) with price in the lower half of the pattern, i.e.
             still near its lows, on an up day (close above open). Or a harmonic pattern
             (ABCD, Gartley, Butterfly) completing at its low point and turning up.
             Stop: the pattern's stop (below its lows). Target: the pattern's neckline / breakout
             line, the resistance the price has to reach first.
    support  price dips to a MAJOR support (a level touched 3+ times in the last year, levels.py)
             or to the 50% / 61.8% Fibonacci retracement of the last big rise, and closes back
             above it on an up day. Stop: 0.5 x ATR below the support (or the day's low).
             Target: the nearest major resistance above.
    ma200    price recently crossed above its 200-day average (within 60 days), pulls back to
             it and closes back above it on an up day: an early trend change, retested.
             Stop: 0.5 x ATR below the pullback low / the average. Target: nearest major resistance.

Support and 200-day setups only count if the target is at least MIN_RR x the risk away (room to the
resistance); bottom patterns need price in the lower half of the pattern (= at least 1:1 to the neckline;
a swing low is only confirmed after a ~2 ATR bounce, so 1.5:1 would reject most of them);
with no resistance above (price at new highs), the target is NO_RES_R x the risk.
Only data up to the signal day is used (levels from the days BEFORE it).
"""
import numpy as np
import pandas as pd

import levels
import patterns
from strategies import tools

MIN_RR = 1.5                   # target at least 1.5 x the risk away
NO_RES_R = 3.0                 # no resistance above: target = 3 x the risk
BOTTOMS = {"Double bottom", "Triple bottom", "Inverse head & shoulders", "Falling wedge", "Rounding bottom"}
HARMONICS = {"ABCD", "Gartley", "Butterfly"}
LOWER_PART = 0.5               # bottom patterns: price in the lower half between stop and neckline
TOUCH = 0.25                   # "touched" support: low within 0.25 x ATR of it
CROSS_DAYS = 60                # ma200: crossed above the 200-day average within this many days
STOP_PAD = 0.5                 # stop 0.5 x ATR below the support
_cache = {}


def _target(close, stop, res_levels):
    """Nearest resistance at least MIN_RR x risk above close; None = not enough room."""
    risk = close - stop
    if risk <= 0:
        return None
    above = sorted(r for r in res_levels if r > close)
    if not above:
        return close + NO_RES_R * risk
    return above[0] if above[0] - close >= MIN_RR * risk else None


def find(d):
    """All setups of one coin/stock: DataFrame (one row per setup) with date, setup, name, stop,
    target, above200 (close above the 200-day average that day)."""
    key = (d.index[0], d.index[-1], len(d), float(d["Close"].iat[-1]))
    if key in _cache:
        return _cache[key]
    df = levels._atr(d[["Open", "High", "Low", "Close"]].copy())
    o, h, l, c, atr = (df[k].values for k in ("Open", "High", "Low", "Close", "ATR"))
    sma200 = tools.sma(df["Close"], 200).values
    look = patterns.SETTINGS["daily"]["lookback"]
    rows, seen = [], set()
    for i in range(200, len(df)):
        if not np.isfinite(atr[i]) or not np.isfinite(sma200[i]):
            continue
        up_day = c[i] > o[i]
        win = df.iloc[max(0, i - levels.SR_LOOKBACK):i + 1]
        lv = levels.major_levels(win)                  # levels from the days before day i
        res = [p for p, *_ in lv]
        above200 = bool(c[i] > sma200[i])

        # --- bottom patterns forming / harmonics completing
        pats, _ = patterns.find_patterns(df.iloc[max(0, i - look + 1):i + 1], "daily", float(c[i]), float(c[i - 1]))
        for pt in pats:
            if pt["dir"] != "LONG" or pt["id"] in seen:
                continue
            name = pt["name"]
            if name in BOTTOMS and pt["stage"] == "forming":
                rng = pt["level"] - pt["stop"]
                if not (up_day and rng > 0 and c[i] - pt["stop"] <= LOWER_PART * rng):
                    continue
                tgt = pt["level"]          # lower half => the neckline is at least 1 x the risk away
            elif name in HARMONICS and pt["stage"] == "breakout":
                tgt = _target(c[i], pt["stop"], res + [pt["target"]])
            else:
                continue
            if tgt is None:
                continue
            seen.add(pt["id"])
            rows.append((df.index[i], "bottom", name, pt["stop"], tgt, above200))

        if not up_day:
            continue
        # --- bounce at major support or Fibonacci 50-61.8% of the last rise
        sup = [p for p in res if l[i] <= p + TOUCH * atr[i] and c[i] > p and c[i] - p <= atr[i]]
        name = "Major support" if sup else None
        if not sup:
            sw = levels.fib_swing(win)
            if sw and sw["up"] and l[i] > sw["a"][1]:
                fib = [lvl for r, lvl in sw["levels"].items() if r >= 0.5
                       and l[i] <= lvl + TOUCH * atr[i] and c[i] > lvl]
                if fib:
                    sup, name = fib, "Fibonacci 50-62%"
        if sup:
            s = max(sup)
            stop = min(s, l[i]) - STOP_PAD * atr[i]
            tgt = _target(c[i], stop, res)
            if tgt is not None:
                rows.append((df.index[i], "support", name, stop, tgt, above200))

        # --- 200-day average: recent cross above, pulled back to it, held
        recent = c[max(0, i - CROSS_DAYS):i - 3] < sma200[max(0, i - CROSS_DAYS):i - 3]
        if recent.any() and c[i] > sma200[i] and l[i] <= sma200[i] + TOUCH * atr[i] \
                and (c[i - 3:i] > sma200[i - 3:i]).all():
            stop = min(sma200[i], l[i]) - STOP_PAD * atr[i]
            tgt = _target(c[i], stop, res)
            if tgt is not None:
                rows.append((df.index[i], "ma200", "200-day MA retest", stop, tgt, above200))
    out = pd.DataFrame(rows, columns=["date", "setup", "name", "stop", "target", "above200"])
    _cache[key] = out
    return out


def pick(d, setup, above200=None):
    """The setups of one kind as strategy_signals() expects: index = date, columns stop, target, name.
    above200: True = only while the close is above the 200-day average, None = always."""
    s = find(d)
    s = s[s["setup"].isin([setup] if isinstance(setup, str) else setup)]
    if above200 is not None:
        s = s[s["above200"] == above200]
    s = s.drop_duplicates("date", keep="first")
    return s.set_index("date")[["stop", "target", "name"]]
