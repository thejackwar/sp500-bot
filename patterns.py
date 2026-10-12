"""
Chart-pattern detection on daily and hourly candles.

Every pattern is built from "swing points" found with a zigzag: a new swing only counts
when price reverses by at least K x ATR (the stock's typical candle range), so small
wiggles don't create fake patterns.

Each detector returns a pattern with one or two "breaks" (direction + entry level + stop +
target). find_patterns() then decides the stage using the latest price:
    "forming"  - the shape is complete and price is still inside it (watchlist)
    "breakout" - price crossed the entry level on the latest candle (chart alert)

Harmonic patterns (ABCD, Gartley, Butterfly) use "forming" while price heads toward the
completion zone, and "breakout" when price reaches the zone and the latest candle turns.
"""
import numpy as np

SETTINGS = {
    "daily":  {"k": 2.0, "k_fine": 1.2, "lookback": 250, "max_age": 25, "max_span": 160},
    "hourly": {"k": 2.0, "k_fine": 1.2, "lookback": 220, "max_age": 35, "max_span": 160},
}
TOL = 0.6          # "equal" price levels may differ by up to TOL x ATR
MIN_BREAK_VOLUME = 1.3   # breakout candle volume vs its 20-candle average (pattern breakouts)
MIN_BREAK_MARGIN = 0.1   # breakout close must clear the line by this many ATRs
HARMONICS = ("ABCD", "Gartley", "Butterfly")


# ---------------------------------------------------------------- swing points
def zigzag(df, k):
    """Alternating swing highs/lows. Returns (confirmed pivots, current unconfirmed extreme).
    Each pivot is (bar index, price, "H" or "L")."""
    h, l, atr = df["High"].values, df["Low"].values, df["ATR"].values
    piv, trend, hi_i, lo_i = [], 0, 0, 0
    ext_i, ext_p = 0, None
    for i in range(1, len(df)):
        thr = k * atr[i]
        if trend == 0:
            if h[i] > h[hi_i]:
                hi_i = i
            if l[i] < l[lo_i]:
                lo_i = i
            if h[hi_i] - l[lo_i] >= thr:
                if lo_i < hi_i:
                    piv.append((lo_i, float(l[lo_i]), "L"))
                    trend, ext_i, ext_p = 1, hi_i, h[hi_i]
                else:
                    piv.append((hi_i, float(h[hi_i]), "H"))
                    trend, ext_i, ext_p = -1, lo_i, l[lo_i]
        elif trend == 1:
            if h[i] >= ext_p:
                ext_i, ext_p = i, h[i]
            elif ext_p - l[i] >= thr:
                piv.append((ext_i, float(ext_p), "H"))
                trend, ext_i, ext_p = -1, i, l[i]
        else:
            if l[i] <= ext_p:
                ext_i, ext_p = i, l[i]
            elif h[i] - ext_p >= thr:
                piv.append((ext_i, float(ext_p), "L"))
                trend, ext_i, ext_p = 1, i, h[i]
    last = (ext_i, float(ext_p), "H" if trend == 1 else "L") if trend else None
    return piv, last


def _line(p1, p2):
    (x1, y1), (x2, y2) = p1, p2
    s = (y2 - y1) / (x2 - x1) if x2 != x1 else 0.0
    return lambda x: y1 + s * (x - x1), s


def _brk(direction, level, stop, target):
    return {"dir": direction, "level": float(level), "stop": float(stop), "target": float(target)}


def _pat(name, breaks, keys, lines, points):
    return {"name": name, "breaks": breaks, "keys": keys, "lines": lines, "points": points}


# ---------------------------------------------------------------- reversal: tops & bottoms
def _heads_triples(cp, n, atr):
    tol = TOL * atr
    out = []
    if len(cp) >= 6:   # head and shoulders / inverse
        p0, s1, n1, hd, n2, s2 = cp[-6:]
        for top in (True, False):
            if (s1[2] == "H") != top:
                continue
            sign = 1 if top else -1
            if (sign * (hd[1] - max(sign * s1[1], sign * s2[1]) * sign) > tol
                    and abs(s1[1] - s2[1]) <= 2 * tol and sign * (p0[1] - n1[1]) < 0):
                neck, _ = _line((n1[0], n1[1]), (n2[0], n2[1]))
                if abs(n1[1] - n2[1]) > 3 * tol:
                    continue
                lvl, height = neck(n - 1), abs(hd[1] - neck(hd[0]))
                name = "Head & shoulders" if top else "Inverse head & shoulders"
                b = _brk("SHORT", lvl, s2[1] + 0.3 * tol, lvl - height) if top else \
                    _brk("LONG", lvl, s2[1] - 0.3 * tol, lvl + height)
                out.append(_pat(name, [b], [s1, hd, s2],
                                [((n1[0], n1[1]), (n - 1, lvl))],
                                [(s1[0], s1[1], "LS"), (hd[0], hd[1], "Head"), (s2[0], s2[1], "RS")]))
                return out
    if len(cp) >= 6:   # triple top / bottom
        p0, a, b1, c, d, e = cp[-6:]
        for top in (True, False):
            if (a[2] == "H") != top:
                continue
            peaks = [a[1], c[1], e[1]]
            if max(peaks) - min(peaks) <= 1.5 * tol and abs(b1[1] - d[1]) <= 2 * tol \
                    and abs(np.mean(peaks) - b1[1]) >= 2 * atr \
                    and (p0[1] < min(b1[1], d[1]) if top else p0[1] > max(b1[1], d[1])):
                neck = min(b1[1], d[1]) if top else max(b1[1], d[1])
                height = abs(np.mean(peaks) - neck)
                br = _brk("SHORT", neck, max(peaks) + 0.3 * tol, neck - height) if top else \
                    _brk("LONG", neck, min(peaks) - 0.3 * tol, neck + height)
                out.append(_pat("Triple top" if top else "Triple bottom", [br], [a, c, e],
                                [((a[0], neck), (n - 1, neck))],
                                [(a[0], a[1], "1"), (c[0], c[1], "2"), (e[0], e[1], "3")]))
                return out
    return out


def _doubles(cp, n, atr):
    tol = TOL * atr
    out = []
    if len(cp) >= 4:   # double top / bottom
        p0, a, b1, c = cp[-4:]
        for top in (True, False):
            if (a[2] == "H") != top:
                continue
            if abs(a[1] - c[1]) <= tol and abs(max(a[1], c[1]) - b1[1]) >= 2 * atr and c[0] - a[0] >= 4 \
                    and (p0[1] < b1[1] - atr if top else p0[1] > b1[1] + atr):
                neck = b1[1]
                ext = max(a[1], c[1]) if top else min(a[1], c[1])
                height = abs(ext - neck)
                br = _brk("SHORT", neck, ext + 0.3 * tol, neck - height) if top else \
                    _brk("LONG", neck, ext - 0.3 * tol, neck + height)
                out.append(_pat("Double top" if top else "Double bottom", [br], [a, c],
                                [((a[0], neck), (n - 1, neck))],
                                [(a[0], a[1], "1"), (c[0], c[1], "2")]))
                return out
    return out


# ---------------------------------------------------------------- lines: triangles, wedges, rectangle, flags
def _line_patterns(df, cp, n, atr, sizes=(7, 6, 5, 4), flags_only=False):
    tol = TOL * atr
    closes = df["Close"].values
    for m in sizes:
        if len(cp) < m:
            continue
        seg = cp[-m:]
        highs = [(p[0], p[1]) for p in seg if p[2] == "H"]
        lows = [(p[0], p[1]) for p in seg if p[2] == "L"]
        if len(highs) < 2 or len(lows) < 2:
            continue
        i0 = seg[0][0]
        span = (n - 1) - i0
        if span < 8:
            continue
        hs, hb = np.polyfit([p[0] for p in highs], [p[1] for p in highs], 1)
        ls, lb = np.polyfit([p[0] for p in lows], [p[1] for p in lows], 1)
        up = lambda x: hs * x + hb
        lo = lambda x: ls * x + lb
        if max(abs(p[1] - up(p[0])) for p in highs) > tol or max(abs(p[1] - lo(p[0])) for p in lows) > tol:
            continue
        w0, w1 = up(i0) - lo(i0), up(n - 1) - lo(n - 1)
        if w0 < 1.5 * atr or w1 <= 0:
            continue
        inside = closes[i0:n - 1]
        xs = np.arange(i0, n - 1)
        if np.any(inside > up(xs) + tol) or np.any(inside < lo(xs) - tol):
            continue
        flat_h, flat_l = abs(hs * span) <= tol, abs(ls * span) <= tol
        conv, parallel = w1 < 0.7 * w0, 0.75 * w0 <= w1 <= 1.3 * w0
        U, L = up(n - 1), lo(n - 1)
        lines = [((i0, up(i0)), (n - 1, U)), ((i0, lo(i0)), (n - 1, L))]
        pts = [(p[0], p[1], "") for p in seg]
        keys = [seg[0], seg[-1]]

        # flag / pennant: a strong "flagpole" move right before the consolidation
        if len(cp) > m:
            pole_a, pole_b = cp[-m - 1], seg[0]
            pole = pole_b[1] - pole_a[1]
            pole_bars = pole_b[0] - pole_a[0]
            if abs(pole) >= max(2.0 * w0, 4 * atr) and span <= 3 * pole_bars:
                bull = pole > 0
                sloped_against = (hs * span < -0.5 * tol) if bull else (hs * span > 0.5 * tol)
                if parallel and sloped_against:
                    name = "Bull flag" if bull else "Bear flag"
                elif conv and hs <= 0 <= ls:
                    name = "Bull pennant" if bull else "Bear pennant"
                else:
                    name = None
                if name:
                    br = _brk("LONG", U, L, U + abs(pole)) if bull else _brk("SHORT", L, U, L - abs(pole))
                    return [_pat(name, [br], [pole_a] + keys,
                                 lines + [((pole_a[0], pole_a[1]), (pole_b[0], pole_b[1]))], pts)]

        if flags_only or m < 5:
            continue
        h0 = w0
        if flat_h and flat_l:
            return [_pat("Rectangle", [_brk("LONG", U, L, U + h0), _brk("SHORT", L, U, L - h0)], keys, lines, pts)]
        if conv and flat_h and ls > 0:
            return [_pat("Ascending triangle", [_brk("LONG", U, L, U + h0)], keys, lines, pts)]
        if conv and flat_l and hs < 0:
            return [_pat("Descending triangle", [_brk("SHORT", L, U, L - h0)], keys, lines, pts)]
        if conv and hs < 0 < ls:
            return [_pat("Symmetrical triangle", [_brk("LONG", U, L, U + h0), _brk("SHORT", L, U, L - h0)],
                         keys, lines, pts)]
        if conv and hs > 0 and ls > 0:
            return [_pat("Rising wedge", [_brk("SHORT", L, U, L - h0)], keys, lines, pts)]
        if conv and hs < 0 and ls < 0:
            return [_pat("Falling wedge", [_brk("LONG", U, L, U + h0)], keys, lines, pts)]
    return []


# ---------------------------------------------------------------- bump and run, rounding bottom
def _bump_and_run(cp, n, atr):
    if len(cp) < 6:
        return []
    seg = cp[-7:]
    hb = max((p for p in seg if p[2] == "H"), key=lambda p: p[1])
    lows = [p for p in seg if p[2] == "L" and p[0] < hb[0]]
    if len(lows) < 2:
        return []
    la, lb = lows[-2], lows[-1]
    if lb[1] <= la[1]:
        return []
    line, slope = _line((la[0], la[1]), (lb[0], lb[1]))
    lead_highs = [p for p in seg if p[2] == "H" and la[0] < p[0] < lb[0]]
    if not lead_highs:
        return []
    lead = max(p[1] - line(p[0]) for p in lead_highs)
    bump = hb[1] - line(hb[0])
    if lead <= 0 or bump < 2.5 * lead or bump < 5 * atr:
        return []
    if (hb[1] - lb[1]) / max(hb[0] - lb[0], 1) < 2 * slope:      # bump must be much steeper
        return []
    lvl = line(n - 1)
    return [_pat("Bump & run reversal", [_brk("SHORT", lvl, hb[1], la[1])], [la, lb, hb],
                 [((la[0], la[1]), (n - 1, lvl))],
                 [(la[0], la[1], "lead-in"), (hb[0], hb[1], "bump")])]


def _rounding_bottom(df, n, atr):
    closes = df["Close"].values
    for w in (120, 90, 60):
        if n < w + 2:
            continue
        y = closes[n - 1 - w:n - 1]
        x = np.linspace(0, 1, len(y))
        a, b, c = np.polyfit(x, y, 2)
        if a <= 0:
            continue
        xv = -b / (2 * a)
        fit = a * x ** 2 + b * x + c
        r2 = 1 - np.sum((y - fit) ** 2) / np.sum((y - y.mean()) ** 2)
        rim = float(np.max(y[: max(3, len(y) // 8)]))
        bot_i = int(np.argmin(y))
        bottom = float(y[bot_i])
        depth = rim - bottom
        if 0.3 <= xv <= 0.7 and r2 >= 0.85 and depth >= 4 * atr and y[-1] >= bottom + 0.6 * depth:
            i0 = n - 1 - w
            curve = [(i0 + int(k), float(fit[k]), "") for k in range(0, len(y), max(1, len(y) // 12))]
            return [_pat("Rounding bottom", [_brk("LONG", rim, bottom, rim + depth)],
                         [(i0 + bot_i, bottom, "L")], [((i0, rim), (n - 1, rim))], curve)]
    return []


# ---------------------------------------------------------------- harmonics
def _harmonics(cp, last, n, atr, price, prev):
    """X-A-B-C confirmed, D = the leg in progress. Returns patterns with a fixed stage."""
    out = []
    if last is None or len(cp) < 3:
        return out
    tests = []
    if len(cp) >= 4:
        X, A, B, C = cp[-4:]
        XA, AB, BC = abs(A[1] - X[1]), abs(A[1] - B[1]), abs(C[1] - B[1])
        if XA > 0 and AB > 0:
            rAB, rBC = AB / XA, BC / AB
            bull = X[2] == "L"
            s = -1 if bull else 1       # D lies below A for bullish, above for bearish
            if 0.55 <= rAB <= 0.68 and 0.382 <= rBC <= 0.886:
                d = A[1] + s * 0.786 * XA
                if 1.13 <= abs(C[1] - d) / BC <= 1.9:
                    tests.append(("Gartley", bull, d, 0.05 * XA, X[1], [X, A, B, C]))
            if 0.74 <= rAB <= 0.83 and 0.382 <= rBC <= 0.886:
                d = A[1] + s * 1.27 * XA
                if 1.5 <= abs(C[1] - d) / BC <= 2.7:
                    tests.append(("Butterfly", bull, d, 0.07 * XA, A[1] + s * 1.618 * XA, [X, A, B, C]))
    A, B, C = cp[-3:]
    AB, BC = abs(A[1] - B[1]), abs(C[1] - B[1])
    if AB > 0 and 0.60 <= BC / AB <= 0.79:
        bull = A[2] == "H"              # A high, B low, C lower high -> D low (buy)
        s = -1 if bull else 1
        d = C[1] + s * 1.445 * BC       # middle of the 1.272-1.618 extension zone
        stop = C[1] + s * 1.8 * BC
        if 0.8 <= abs(C[1] - d) / AB <= 1.25:          # AB = CD
            tests.append(("ABCD", bull, d, 0.173 * BC, stop, [A, B, C]))

    for name, bull, d, half, stop, keys in tests:
        C = keys[-1]
        if last[2] != ("L" if bull else "H"):          # the current leg must head toward D
            continue
        lo_z, hi_z = d - half, d + half
        target = d + 0.618 * (C[1] - d)
        direction = "LONG" if bull else "SHORT"
        ext = last[1]                                   # furthest point of the current leg
        in_zone = lo_z <= ext <= hi_z
        turned = price > prev if bull else price < prev
        near = abs(price - d) <= 2 * half + 0.5 * atr      # price still close to the zone
        if in_zone and turned and near:
            stage = "breakout"
        elif not in_zone and (ext > hi_z if bull else ext < lo_z):
            covered = abs(C[1] - ext) / max(abs(C[1] - d), 1e-9)
            stage = "forming" if covered >= 0.75 else None
        else:
            stage = None
        if stage:
            p = _pat(name, [_brk(direction, d, stop, target)], keys,
                     [((C[0], lo_z), (n - 1, lo_z)), ((C[0], hi_z), (n - 1, hi_z))],
                     [(k[0], k[1], lbl) for k, lbl in zip(keys, "XABC"[-len(keys):])])
            p["stage"] = stage
            out.append(p)
    return out


# ---------------------------------------------------------------- stage
def _stage(p, d, ts_col, n, cfg, timeframe, price, prev, atr, vol_ratio):
    """Fill in stage/direction/levels. Returns False if the pattern is too old or no longer valid."""
    newest = max(k[0] for k in p["keys"])
    oldest = min(k[0] for k in p["keys"])
    too_old = (n - 1) - newest > cfg["max_age"] and p["name"] != "Rounding bottom"
    if too_old or (n - 1) - oldest > cfg["max_span"]:
        return False
    stage, brk = p.get("stage"), p["breaks"][0]
    if stage is None:
        margin = MIN_BREAK_MARGIN * atr
        for b in p["breaks"]:
            if (b["dir"] == "LONG" and prev <= b["level"] < price) or \
                    (b["dir"] == "SHORT" and prev >= b["level"] > price):
                strong = abs(price - b["level"]) >= margin and (vol_ratio is None or vol_ratio >= MIN_BREAK_VOLUME)
                stage, brk = ("breakout" if strong else None), b
                break
        else:
            inside = all((b["stop"] < price < b["level"]) if b["dir"] == "LONG"
                         else (b["level"] < price < b["stop"]) for b in p["breaks"])
            stage = "forming" if inside else None
    if not stage:
        return False
    p.update(stage=stage, timeframe=timeframe, **{k: brk[k] for k in ("dir", "level", "stop", "target")})
    if stage == "forming" and len(p["breaks"]) == 2:
        p["dir"] = "BOTH"
    p["id"] = f"{timeframe}|{p['name']}|" + ",".join(str(d[ts_col].iat[k[0]]) for k in p["keys"])
    return True


# ---------------------------------------------------------------- main entry point
def find_patterns(df, timeframe, price, prev, vol_ratio=None):
    """df: candles with ATR (newest last). price/prev: latest and previous price.
    vol_ratio: latest candle's volume / its 20-candle average (None = don't check volume).
    Returns patterns with stage "forming" or "breakout"."""
    cfg = SETTINGS[timeframe]
    d = df.tail(cfg["lookback"]).reset_index()
    ts_col = d.columns[0]
    n = len(d)
    if n < 40:
        return [], d
    atr = float(d["ATR"].iat[-1])
    cp, last = zigzag(d, cfg["k"])
    if len(cp) < 3:
        return [], d

    # One "shape" per chart, most specific first: flags/pennants (finer swings, need a flagpole),
    # head & shoulders and triples, multi-touch triangles/wedges/rectangles, doubles, the rest.
    # The first candidate that is valid right now (forming or breaking out) wins.
    fine, _ = zigzag(d, cfg["k_fine"])
    shape_finders = (
        lambda: _line_patterns(d, fine, n, atr, flags_only=True),
        lambda: _heads_triples(cp, n, atr),
        lambda: _line_patterns(d, cp, n, atr, sizes=(7, 6, 5)),
        lambda: _doubles(cp, n, atr),
        lambda: _bump_and_run(cp, n, atr),
    )
    results = []
    for finder in shape_finders:
        valid = [p for p in finder() if _stage(p, d, ts_col, n, cfg, timeframe, price, prev, atr, vol_ratio)]
        if valid:
            results += valid
            break
    for extra in (_rounding_bottom(d, n, atr), _harmonics(cp, last, n, atr, price, prev)):
        for p in extra:
            if _stage(p, d, ts_col, n, cfg, timeframe, price, prev, atr, vol_ratio):
                results.append(p)
                if p["name"] in HARMONICS:
                    break          # one harmonic per chart (the most specific one)
    return results, d
