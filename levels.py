"""
Major support / resistance and Fibonacci retracements on the DAILY chart.

Major support/resistance
    Swing highs and lows of the last year (found with a zigzag that ignores moves smaller than
    2 x ATR) are grouped when they sit within half an ATR of each other. A group with at least
    MIN_TOUCHES swings is a "major" level.
    Alert: yesterday's close was on one side and today's price is clearly on the other side
    (by at least BREAK_MARGIN x ATR).

Fibonacci retracements
    The most recent major swing (a move of at least MIN_SWING_ATR x ATR between two zigzag
    turning points found with 3 x ATR) gives the retracement levels 38.2%, 50% and 61.8%.
    Alert: price pulls back to one of those levels for the FIRST time since the swing ended
    (a classic zone to watch for a bounce). Retracing an up-swing = potential LONG,
    retracing a down-swing = potential SHORT. If price goes back past 100%, the swing is dead.
"""
import numpy as np
import pandas as pd

import patterns

SR_LOOKBACK = 250        # daily candles used to find major levels (~1 year)
SR_ZIGZAG_K = 2.0        # swing must reverse by 2 x ATR to count
SR_CLUSTER = 0.5         # swings within 0.5 x ATR of each other form one level
MIN_TOUCHES = 3          # touches needed for a level to be "major"
BREAK_MARGIN = 0.25      # price must clear the level by 0.25 x ATR to count as a break

FIB_ZIGZAG_K = 3.0       # turning points for the Fibonacci swing (bigger moves only)
MIN_SWING_ATR = 6.0      # the swing must be at least 6 x ATR long
FIB_LEVELS = (0.382, 0.5, 0.618)
FIB_TOUCH = 0.15         # within 0.15 x ATR of a level counts as touching it


def _atr(df):
    if "ATR" in df:
        return df
    df = df.copy()
    c, h, l = df["Close"], df["High"], df["Low"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df["ATR"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    return df


def major_levels(df):
    """Major levels from completed candles (today's candle excluded so it can't define the level
    it is breaking). Returns [(price, touches, first_date, last_date)] sorted by price."""
    hist = df.iloc[:-1].tail(SR_LOOKBACK)
    if len(hist) < 60:
        return []
    d = hist.reset_index()
    piv, _ = patterns.zigzag(d, SR_ZIGZAG_K)
    if len(piv) < MIN_TOUCHES:
        return []
    tol = SR_CLUSTER * float(hist["ATR"].iat[-1])
    pts = sorted((p[1], p[0]) for p in piv)
    groups = [[pts[0]]]
    for price, i in pts[1:]:
        if price - groups[-1][-1][0] <= tol:
            groups[-1].append((price, i))
        else:
            groups.append([(price, i)])
    out = []
    for g in groups:
        if len(g) >= MIN_TOUCHES:
            idx = sorted(i for _, i in g)
            out.append((float(np.mean([p for p, _ in g])), len(g),
                        hist.index[idx[0]], hist.index[idx[-1]]))
    return out


def fib_swing(df):
    """The latest major swing that price is now retracing. Returns a dict or None."""
    hist = df.iloc[:-1].tail(SR_LOOKBACK)
    if len(hist) < 60:
        return None
    d = hist.reset_index()
    piv, _ = patterns.zigzag(d, FIB_ZIGZAG_K)
    if len(piv) < 2:
        return None
    a, b = piv[-2], piv[-1]                          # swing start (A) and end (B)
    atr = float(hist["ATR"].iat[-1])
    size = abs(b[1] - a[1])
    if size < MIN_SWING_ATR * atr:
        return None
    up = b[2] == "H"                                 # A low -> B high = up-swing
    levels = {r: (b[1] - r * size if up else b[1] + r * size) for r in FIB_LEVELS}
    return {"up": up, "a": (hist.index[a[0]], a[1]), "b": (hist.index[b[0]], b[1]),
            "b_pos": b[0], "levels": levels, "size": size}


def events(df):
    """Daily-chart events for major S/R breaks and Fibonacci touches.
    df: daily candles incl. today's candle so far (newest last)."""
    df = _atr(df)
    if len(df) < 80:
        return [], {}
    cur, prev = df.iloc[-1], df.iloc[-2]
    atr = float(df["ATR"].iat[-2])
    price = float(cur.Close)
    out = []

    # --- major support / resistance breaks
    levels = major_levels(df)
    margin = BREAK_MARGIN * atr
    broken_up = [lv for lv in levels if prev.Close < lv[0] and price > lv[0] + margin]
    broken_dn = [lv for lv in levels if prev.Close > lv[0] and price < lv[0] - margin]
    if broken_up:
        lv = max(broken_up, key=lambda x: x[1])
        out.append({"dir": "LONG", "code": "sr_up", "tf": "daily",
                    "text": f"Broke above MAJOR resistance {lv[0]:.2f} ({lv[1]} touches since "
                            f"{pd.Timestamp(lv[2]):%b %Y})",
                    "draw": {"levels": [(lv[0], "res")]}})
    if broken_dn:
        lv = max(broken_dn, key=lambda x: x[1])
        out.append({"dir": "SHORT", "code": "sr_dn", "tf": "daily",
                    "text": f"Broke below MAJOR support {lv[0]:.2f} ({lv[1]} touches since "
                            f"{pd.Timestamp(lv[2]):%b %Y})",
                    "draw": {"levels": [(lv[0], "sup")]}})

    # --- Fibonacci retracement: first touch of a level since the swing ended
    sw = fib_swing(df)
    if sw:
        hist = df.iloc[:-1].tail(SR_LOOKBACK)
        after_b = hist.iloc[sw["b_pos"] + 1:]           # completed candles after the swing ended
        a_price = sw["a"][1]
        if sw["up"]:
            alive = after_b["Low"].min() > a_price if len(after_b) else True
            prior_extreme = after_b["Low"].min() if len(after_b) else np.inf
            tol = FIB_TOUCH * atr
            hits = [r for r, lvl in sw["levels"].items() if prior_extreme > lvl + tol and float(cur.Low) <= lvl + tol]
        else:
            alive = after_b["High"].max() < a_price if len(after_b) else True
            prior_extreme = after_b["High"].max() if len(after_b) else -np.inf
            tol = FIB_TOUCH * atr
            hits = [r for r, lvl in sw["levels"].items() if prior_extreme < lvl - tol and float(cur.High) >= lvl - tol]
        still_alive = alive and (price > a_price if sw["up"] else price < a_price)
        if hits and still_alive:
            r = max(hits)                                # deepest level reached today
            lvl = sw["levels"][r]
            move = "rise" if sw["up"] else "drop"
            hint = "watch for a bounce" if sw["up"] else "watch for a rejection"
            out.append({"dir": "LONG" if sw["up"] else "SHORT", "code": "fib", "tf": "daily",
                        "text": f"Pulled back to the {r * 100:.1f}% Fibonacci retracement {lvl:.2f} of the "
                                f"{move} {a_price:.2f} → {sw['b'][1]:.2f} ({hint})",
                        "draw": {"fib": sw}})

    # context for every alert: nearest major levels and the active Fibonacci zone
    below = [lv for lv in levels if lv[0] < price]
    above = [lv for lv in levels if lv[0] > price]
    ctx = {"major_sup": max(below)[0] if below else None,
           "major_res": min(above)[0] if above else None,
           "fib": sw}
    return out, ctx
