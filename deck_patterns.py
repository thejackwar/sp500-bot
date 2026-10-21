"""
Chart patterns built from the rules in Fidelity's "Identifying Chart Patterns with Technical
Analysis" (C. Kirkpatrick, source: "Technical Analysis: the Complete Resource for Financial
Market Technicians"). Every pattern below follows the slide that describes it: its shape,
what counts as the breakout, and how the price target is calculated.

General rules from the deck that apply to every pattern:
  * A pattern is bounded by at least two trend lines (straight or curved).
  * It is NOT complete until an actual breakout happens  ->  two stages here:
        "forming"   the shape is there, price is still inside, no breakout yet
        "breakout"  a candle CLOSED beyond the breakout line (plus a small filter) in the last
                    few candles  ->  the pattern is complete / activated
  * Confirmation filter (slide 10): the close must clear the line by BREAK_FILTER x ATR
    (a "point" filter that adapts to each stock's normal daily range).
  * Protective stop (slides 13-14): just outside the breakout candle, on the opposite side.
  * False breakout (slide 12): after a breakout, a close back through the breakout line is flagged.
  * Candlestick patterns (slide 32): only act once a later candle breaks out of them; they work
    best as reversals after a move, so they are only counted at the end of a short trend.

detect(df) returns a list of pattern dicts (see _pat()); bar positions are row numbers of df.
df needs Open, High, Low, Close columns (oldest first).
"""
import numpy as np
import pandas as pd

# ---------------------------------------------------------------- settings
SCALES = (1.5, 3.0)       # zigzag swing sizes in ATRs: small and large patterns
TOL = 0.75                # "roughly the same price" = within TOL x ATR
BREAK_FILTER = 0.2        # breakout close must clear the line by this many ATRs (slide 10)
MIN_HEIGHT = 2.5          # a multi-bar pattern must be at least this many ATRs tall
MIN_BARS = 10             # ... and at least this many candles long
MIN_BARS_LINES = 15       # rectangles, triangles, wedges: at least this many candles
MAX_BARS = 150            # ... and at most this many (about 7 months of daily candles)
MIN_GAP = 4               # candles between two peaks (or troughs) of the same pattern
MAX_AGE = 40              # forming: the last swing of the pattern must be this recent (candles)
RECENT = 3                # breakout: must have happened in the last RECENT candles

# The deck's comments on each pattern (shown in the alert). * = from the CMT textbook statistics.
NOTES = {
    "Double top": "Two peaks at about the same price; completes on a close below the trough between them.",
    "Double bottom": "Two troughs at about the same price; completes on a close above the peak between them.",
    "Triple top": "Three peaks at about the same price; completes below the lowest of the two troughs.",
    "Triple bottom": "Three troughs at about the same price. Best after a long decline; average performance, watch for failures.*",
    "Rectangle": "Trading range between support and resistance. Many false breakouts: confirm the breakout. Best: bottom breaking upward.*",
    "Symmetrical triangle": "Falling upper and rising lower line. Many false breakouts; upward breakouts are above average.*",
    "Ascending triangle": "Flat top, rising bottom. Usually breaks up; downside breaks perform above average.*",
    "Descending triangle": "Flat bottom, falling top. Usually breaks down; upside breaks perform above average (a 'best' up signal).*",
    "Rising wedge": "Both lines rising and converging (often after a bubble). Below-average performance; retracements are very common.*",
    "Falling wedge": "Both lines falling and converging (often after a panic). Below-average performance; retracements are very common.*",
    "Head & shoulders top": "The standard top pattern, one of the lowest failure rates. Complete only on a close below the neckline.",
    "Inverse head & shoulders": "Same as the top pattern, upside down; less profitable than the top.*",
    "Cup and handle": "Rounded bottom, two lips, a small handle. Complete on a close above both lips; often throws back.",
    "Bull flag": "Steep rise, then a short consolidation sloping against the trend. The move after it often equals the pole.",
    "Bear flag": "Steep fall, then a short consolidation sloping against the trend (a 'best' down signal).",
    "Bull pennant": "Steep rise, then a small converging consolidation. The move after it often equals the pole.",
    "Bear pennant": "Steep fall, then a small converging consolidation. The move after it often equals the pole.",
    "Gap up (explosion gap pivot)": "Gap on a breakout; the throwback did not cover the gap. Buy above the gap candle's high, stop under the pivot low.",
    "Gap down (explosion gap pivot)": "Gap on a breakdown; the bounce did not cover the gap. Sell below the gap candle's low, stop above the pivot high.",
    "Pipe bottom": "Two-bar reversal at the end of a decline, wider than the candles before it (a 'best' up signal). More reliable on weekly charts.",
    "Pipe top": "Two-bar reversal at the end of a rise, wider than the candles before it.",
    "Narrow range (NR4)": "Low-volatility candle (narrowest of the last 4). New trends often start from low volatility.",
    "Inside bar": "Candle inside the previous one: volatility is shrinking. Trade the next candle's opening direction.",
    "Doji": "Open = close: indecision, a possible warning of a price change. Extremely common.",
    "Harami": "Small opposite-colour body inside a large one. Can break either way.*",
    "Hammer": "Long lower shadow after a decline. Frequent, below-average performance.*",
    "Hanging man": "Long lower shadow after a rise. Breaks either way, slight upward bias, below average.*",
    "Shooting star": "Long upper shadow after a rise. Average performance.*",
    "Inverted hammer": "Long upper shadow after a decline. Average performance.*",
    "Bullish engulfing": "White body engulfs the previous black body after a decline.",
    "Bearish engulfing": "Black body engulfs the previous white body after a rise.",
    "Dark cloud cover": "After a rise, a black candle opens above and closes deep into the previous white body.",
    "Piercing line": "After a decline, a white candle opens below and closes deep into the previous black body.",
}
FAMILY = {n: "candle" for n in ("Doji", "Harami", "Hammer", "Hanging man", "Shooting star", "Inverted hammer",
                                "Bullish engulfing", "Bearish engulfing", "Dark cloud cover", "Piercing line")}
FAMILY.update({n: "short" for n in ("Bull flag", "Bear flag", "Bull pennant", "Bear pennant",
                                    "Gap up (explosion gap pivot)", "Gap down (explosion gap pivot)",
                                    "Pipe bottom", "Pipe top", "Narrow range (NR4)", "Inside bar")})
# when two multi-bar patterns overlap, keep the first in this list
PRIORITY = ["Head & shoulders top", "Inverse head & shoulders", "Cup and handle", "Triple top", "Triple bottom",
            "Rectangle", "Double top", "Double bottom", "Ascending triangle", "Descending triangle",
            "Symmetrical triangle", "Rising wedge", "Falling wedge"]


# ---------------------------------------------------------------- helpers
def add_atr(df, n=14):
    df = df.copy()
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df["ATR"] = tr.ewm(alpha=1 / n, adjust=False).mean()
    return df


def zigzag(h, l, atr, k):
    """Alternating swing highs/lows: a swing only counts once price has reversed k x ATR from it.
    Returns confirmed pivots [(bar, price, "H"/"L")]."""
    piv, n = [], len(h)
    trend, hi, lo, ext = 0, 0, 0, 0
    for i in range(n):
        if trend == 0:
            if h[i] > h[hi]:
                hi = i
            if l[i] < l[lo]:
                lo = i
            if h[hi] - l[lo] >= k * atr[i]:
                if lo < hi:
                    piv.append((lo, l[lo], "L")); trend, ext = 1, hi
                else:
                    piv.append((hi, h[hi], "H")); trend, ext = -1, lo
        elif trend == 1:
            if h[i] >= h[ext]:
                ext = i
            elif h[ext] - l[i] >= k * atr[ext]:
                piv.append((ext, h[ext], "H")); trend, ext = -1, i
        else:
            if l[i] <= l[ext]:
                ext = i
            elif h[i] - l[ext] >= k * atr[ext]:
                piv.append((ext, l[ext], "L")); trend, ext = 1, i
    return piv


class Line:
    """Straight trend line through (x1, y1) with a slope per candle."""
    def __init__(self, x1, y1, x2=None, y2=None, slope=None):
        self.x1, self.y1 = x1, y1
        self.slope = slope if slope is not None else (y2 - y1) / (x2 - x1) if x2 != x1 else 0.0

    def __call__(self, x):
        return self.y1 + self.slope * (x - self.x1)


def fit(points):
    """Least-squares line through [(x, y)] points (exact through 2 points)."""
    xs = np.array([p[0] for p in points], float)
    ys = np.array([p[1] for p in points], float)
    if len(xs) == 2:
        return Line(xs[0], ys[0], xs[1], ys[1])
    m, b = np.polyfit(xs, ys, 1)
    return Line(0.0, b, slope=m)


def _pat(name, stage, direction, start, end, **kw):
    """One pattern. direction: "LONG"/"SHORT" (expected or actual breakout) or "BOTH" (forming,
    can break either way). Optional keys: points [(bar, price, label)], lines [(Line, x_from, x_to)],
    up / down (breakout level lines when forming), targets {"LONG": price, "SHORT": price},
    brk {"bar", "dir", "level", "target", "stop", "false"}, box (bar0, bar1) for candle patterns,
    zone (x0, x1, Line low, Line high) for shading."""
    p = dict(name=name, family=FAMILY.get(name, "multi"), stage=stage, dir=direction,
             start=int(start), end=int(end), points=[], lines=[], targets={}, brk=None, note=NOTES.get(name, ""))
    p.update(kw)
    return p


def _first_break(c, a, b0, upper=None, lower=None, filt=BREAK_FILTER):
    """First candle from b0 on that closes above upper(x) or below lower(x) by filt x ATR.
    upper/lower: functions of the bar position (or None). Returns (bar, "LONG"/"SHORT") or (None, None)."""
    for x in range(b0, len(c)):
        if upper is not None and c[x] > upper(x) + filt * a[x]:
            return x, "LONG"
        if lower is not None and c[x] < lower(x) - filt * a[x]:
            return x, "SHORT"
    return None, None


def _finish(df, a, name, start, end, b, d, level_fn, target, n, **kw):
    """Turn a breakout at bar b into a "breakout" pattern (if recent), with stop and false-break check."""
    if b is None or b < n - RECENT:
        return None
    h, l, c = df["High"].values, df["Low"].values, df["Close"].values
    level = float(level_fn(b))
    stop = float(l[b] - BREAK_FILTER * a[b]) if d == "LONG" else float(h[b] + BREAK_FILTER * a[b])
    back = (c[-1] < level) if d == "LONG" else (c[-1] > level)
    brk = dict(bar=int(b), dir=d, level=level, target=float(target), stop=stop, false=bool(back and b < n - 1))
    return _pat(name, "breakout", d, start, max(end, b), brk=brk, **kw)


# ---------------------------------------------------------------- multi-bar patterns
def _tops_bottoms(df, a, piv, out):
    """Double / triple tops and bottoms (slides 18-21)."""
    h, l, c = df["High"].values, df["Low"].values, df["Close"].values
    n = len(c)
    for size, kind in ((5, "triple"), (3, "double")):
        for e in range(len(piv) - 1, max(-1, len(piv) - 4), -1):
            s = e - size + 1
            if s < 0:
                continue
            seq = piv[s:e + 1]
            top = seq[0][2] == "H"
            peaks = [p for p in seq if p[2] == ("H" if top else "L")]
            mids = [p for p in seq if p[2] == ("L" if top else "H")]
            x0, x1 = seq[0][0], seq[-1][0]
            atr = a[x1]
            if not (MIN_BARS <= x1 - x0 <= MAX_BARS):
                continue
            pp = [p[1] for p in peaks]
            if max(pp) - min(pp) > TOL * atr * (1.5 if kind == "triple" else 1.0):
                continue
            if any(peaks[i + 1][0] - peaks[i][0] < MIN_GAP for i in range(len(peaks) - 1)):
                continue
            if top:
                ext, brk_lvl = max(pp), min(m[1] for m in mids)       # resistance / support
                height = ext - brk_lvl
                if kind == "triple" and min(pp) <= max(m[1] for m in mids) + TOL * atr:
                    continue
            else:
                ext, brk_lvl = min(pp), max(m[1] for m in mids)
                height = brk_lvl - ext
                if kind == "triple" and max(pp) >= min(m[1] for m in mids) - TOL * atr:
                    continue
            if height < MIN_HEIGHT * atr:
                continue
            name = f"{'Triple' if kind == 'triple' else 'Double'} {'top' if top else 'bottom'}"
            d = "SHORT" if top else "LONG"
            # breakout: close beyond the middle reversal point(s); void if price closes beyond the peaks first
            b, bd = _first_break(c, a, x1 + 1, upper=(lambda x: ext) if top else (lambda x: brk_lvl),
                                 lower=(lambda x: brk_lvl) if top else (lambda x: ext))
            if b is not None and bd != d:
                continue                                    # broke the wrong way: no longer this pattern
            later = slice(x1 + 1, b if b is not None else n)
            if len(c[later]) and (h[later].max() > ext + TOL * atr if top else l[later].min() < ext - TOL * atr):
                continue                                    # a new, higher peak (lower trough): not this pattern
            if kind == "double":                            # deck: height peak->trough from the breakout price
                target = brk_lvl - height if top else brk_lvl + height
            else:                                           # deck: from the lowest trough / highest peak
                target = brk_lvl - height if top else brk_lvl + height
            labels = ["1", "2", "3"]
            pts = [(p[0], p[1], labels[i]) for i, p in enumerate(peaks)] + [(m[0], m[1], "") for m in mids]
            lines = [(Line(x0, ext, slope=0.0), x0, None), (Line(x0, brk_lvl, slope=0.0), x0, None)]
            common = dict(points=pts, lines=lines, kind_lines=("res", "sup") if top else ("sup", "res"))
            if b is not None:
                p = _finish(df, a, name, x0, x1, b, bd, lambda x: brk_lvl, target, n, **common)
            elif n - 1 - x1 <= MAX_AGE:
                p = _pat(name, "forming", d, x0, x1, up=None if top else brk_lvl, down=brk_lvl if top else None,
                         targets={d: target}, **common)
            else:
                p = None
            if p:
                out.append(p)


def _lines_patterns(df, a, piv, out):
    """Rectangles, triangles and wedges (slides 22-26): pivots bounded by two straight lines."""
    h, l, c = df["High"].values, df["Low"].values, df["Close"].values
    n = len(c)
    for e in range(len(piv) - 1, max(-1, len(piv) - 3), -1):
        for m in (4, 5, 6, 7, 8):
            s = e - m + 1
            if s < 0:
                break
            seq = piv[s:e + 1]
            highs = [(p[0], p[1]) for p in seq if p[2] == "H"]
            lows = [(p[0], p[1]) for p in seq if p[2] == "L"]
            if len(highs) < 2 or len(lows) < 2:
                continue
            x0, x1 = seq[0][0], seq[-1][0]
            span = x1 - x0
            if not (MIN_BARS_LINES <= span <= MAX_BARS):
                continue
            atr = a[x1]
            tol = TOL * atr
            up, lo = fit(highs), fit(lows)
            if any(abs(y - up(x)) > tol for x, y in highs) or any(abs(y - lo(x)) > tol for x, y in lows):
                continue
            # no close may cut through the pattern between its first and last swing
            xs = np.arange(x0, x1 + 1)
            if np.any(c[x0:x1 + 1] > up(xs) + tol) or np.any(c[x0:x1 + 1] < lo(xs) - tol):
                continue
            w0, w1 = up(x0) - lo(x0), up(x1) - lo(x1)
            if w1 <= 0 or max(w0, w1) < MIN_HEIGHT * atr:
                continue
            flat = lambda ln: abs(ln.slope * span) <= tol
            uf, lf = flat(up), flat(lo)
            conv = w1 < 0.75 * w0
            if uf and lf or (abs(up.slope - lo.slope) * span <= tol and abs(up.slope) * span <= 2 * tol
                             and abs(lo.slope) * span <= 2 * tol):
                name = "Rectangle"
            elif not conv:
                continue                                    # parallel sloping channel: not a deck pattern
            elif up.slope < 0 < lo.slope and not uf and not lf:
                name = "Symmetrical triangle"
            elif uf and lo.slope > 0:
                name = "Ascending triangle"
            elif lf and up.slope < 0:
                name = "Descending triangle"
            elif up.slope > 0 and lo.slope > 0 and not uf and not lf:
                name = "Rising wedge"
            elif up.slope < 0 and lo.slope < 0 and not uf and not lf:
                name = "Falling wedge"
            else:
                continue
            if "wedge" in name and m < 5:                   # deck: at least 5 touches (3 + 2)
                continue
            if name != "Rectangle":
                apex = x1 + w1 / (lo.slope - up.slope) if lo.slope != up.slope else 1e9
                if apex <= x1:
                    continue
            if name == "Rectangle":                         # a rectangle needs both lines touched twice too
                pass
            hi_peak, lo_trough = max(y for _, y in highs), min(y for _, y in lows)
            height = hi_peak - lo_trough
            b, bd = _first_break(c, a, x1 + 1, upper=up, lower=lo)
            if name == "Rectangle":
                tgt = lambda d, lvl, x: (lvl + (up(x) - lo(x))) if d == "LONG" else (lvl - (up(x) - lo(x)))
            elif name == "Rising wedge":
                tgt = lambda d, lvl, x: lvl + height if d == "LONG" else lo_trough
            elif name == "Falling wedge":
                tgt = lambda d, lvl, x: lvl + height if d == "LONG" else lo_trough
            else:
                tgt = lambda d, lvl, x: lvl + height if d == "LONG" else lvl - height
            pts = [(x, y, "") for x, y in highs + lows]
            lines = [(up, highs[0][0], None), (lo, lows[0][0], None)]   # each line from its first touch
            common = dict(points=pts, lines=lines, kind_lines=("res", "sup"))
            if b is not None:
                lvl_fn = up if bd == "LONG" else lo
                p = _finish(df, a, name, x0, x1, b, bd, lvl_fn, tgt(bd, lvl_fn(b), b), n, **common)
            elif n - 1 - x1 <= MAX_AGE and (name == "Rectangle" or n - 1 < apex):
                xn = n - 1
                p = _pat(name, "forming", "BOTH", x0, x1, up=float(up(xn)), down=float(lo(xn)),
                         targets={"LONG": tgt("LONG", up(xn), xn), "SHORT": tgt("SHORT", lo(xn), xn)}, **common)
            else:
                p = None
            if p:
                out.append(p)
                break                                       # the longest fit for this end is enough


def _head_shoulders(df, a, piv, out):
    """Head and shoulders top and inverse (slides 27-28)."""
    c = df["Close"].values
    n = len(c)
    for e in range(len(piv) - 1, max(-1, len(piv) - 4), -1):
        s = e - 4
        if s < 0:
            continue
        seq = piv[s:e + 1]
        top = seq[0][2] == "H"
        ls, n1, hd, n2, rs = seq
        x0, x1 = ls[0], rs[0]
        atr = a[x1]
        if not (MIN_BARS <= x1 - x0 <= MAX_BARS):
            continue
        sgn = 1 if top else -1
        # head beyond both shoulders, shoulders at about the same level
        if top and hd[1] - max(ls[1], rs[1]) < 0.5 * atr or not top and min(ls[1], rs[1]) - hd[1] < 0.5 * atr:
            continue
        neck = Line(n1[0], n1[1], n2[0], n2[1])
        depth = (hd[1] - neck(hd[0])) * sgn
        if depth < MIN_HEIGHT * atr:
            continue
        if abs(ls[1] - rs[1]) > min(1.5 * TOL * atr, 0.2 * depth):
            continue                                        # shoulders at about the same level
        # shoulders must be clearly beyond the neckline
        if sgn * (ls[1] - neck(ls[0])) < 0.4 * depth or sgn * (rs[1] - neck(rs[0])) < 0.4 * depth:
            continue
        name = "Head & shoulders top" if top else "Inverse head & shoulders"
        d = "SHORT" if top else "LONG"
        # void if price goes back beyond the head before the neckline breaks
        b, bd = _first_break(c, a, x1 + 1, upper=(lambda x: hd[1]) if top else neck,
                             lower=neck if top else (lambda x: hd[1]))
        if b is not None and bd != d:
            continue
        pts = [(ls[0], ls[1], "LS"), (hd[0], hd[1], "H"), (rs[0], rs[1], "RS"), (n1[0], n1[1], ""), (n2[0], n2[1], "")]
        shape = [(p[0], p[1]) for p in seq]
        common = dict(points=pts, lines=[(neck, x0, None)], kind_lines=("neck",), shape=shape)
        if b is not None:
            lvl = neck(b)
            p = _finish(df, a, name, x0, x1, b, bd, neck, lvl - sgn * depth, n, **common)
        elif n - 1 - x1 <= MAX_AGE:
            xn = n - 1
            lvl = float(neck(xn))
            p = _pat(name, "forming", d, x0, x1, up=None if top else lvl, down=lvl if top else None,
                     targets={d: lvl - sgn * depth}, **common)
        else:
            p = None
        if p:
            out.append(p)


def _cup_handle(df, a, piv, out):
    """Cup and handle (slide 29): rounded bottom between two lips at about the same price,
    then a small handle; complete on a close above both lips."""
    h, l, c = df["High"].values, df["Low"].values, df["Close"].values
    n = len(c)
    highs = [p for p in piv if p[2] == "H"]
    for j in range(len(highs) - 1, max(-1, len(highs) - 4), -1):
        rx, ry = highs[j][0], highs[j][1]
        for i in range(j - 1, -1, -1):
            lx, ly = highs[i][0], highs[i][1]
            if rx - lx > MAX_BARS:
                break
            if rx - lx < 20:
                continue
            atr = a[rx]
            if abs(ly - ry) > TOL * atr:
                continue
            lip = max(ly, ry)
            inside_h = h[lx + 1:rx]
            if len(inside_h) and inside_h.max() > min(ly, ry) + 0.25 * atr:
                continue                                    # something poked above the lips: not a cup
            bot_x = lx + int(np.argmin(l[lx:rx + 1]))
            depth = min(ly, ry) - l[bot_x]
            if depth < MIN_HEIGHT * atr:
                continue
            # rounded, not a "V": the price spends a while near the bottom, and the low is in the middle
            near = np.mean(c[lx:rx + 1] < l[bot_x] + depth / 3)
            if near < 0.2 or not (0.25 <= (bot_x - lx) / (rx - lx) <= 0.75):
                continue
            # handle: a pullback after the right lip that stays in the upper half of the cup
            after_l = l[rx + 1:]
            if len(after_l) == 0:
                break
            b, bd = _first_break(c, a, rx + 1, upper=lambda x: lip)
            hend = b if b is not None else n
            if hend - rx < 3:
                break
            hl_x = rx + 1 + int(np.argmin(l[rx + 1:hend]))
            hlow = l[hl_x]
            if ry - hlow < 0.75 * atr or hlow < l[bot_x] + depth / 2:
                break
            if np.any(c[rx + 1:hend] < l[bot_x] + depth / 2):
                break
            target = lip + (ry - l[bot_x])
            curve_x = np.arange(lx, rx + 1)
            # draw the cup as a smooth curve (half-ellipse through both lips and the bottom)
            t = (curve_x - lx) / (rx - lx)
            curve_y = lip - depth * np.sin(np.pi * t) ** 0.8
            pts = [(lx, ly, "lip"), (bot_x, l[bot_x], ""), (rx, ry, "lip"), (hl_x, hlow, "handle")]
            common = dict(points=pts, lines=[(Line(lx, lip, slope=0.0), lx, None)], kind_lines=("res",),
                          curve=(curve_x.tolist(), curve_y.tolist()))
            if b is not None:
                p = _finish(df, a, "Cup and handle", lx, hl_x, b, "LONG", lambda x: lip, target, n, **common)
            elif n - 1 - rx <= MAX_AGE:
                p = _pat("Cup and handle", "forming", "LONG", lx, hl_x, up=lip, targets={"LONG": target}, **common)
            else:
                p = None
            if p:
                out.append(p)
            return


# ---------------------------------------------------------------- short-term patterns
def _flags(df, a, out):
    """Pennants and flags (slide 39): steep pole, then a short consolidation sloping against the trend."""
    h, l, c = df["High"].values, df["Low"].values, df["Close"].values
    n = len(c)
    for bull in (True, False):
        found = False
        for t in range(n - 4, max(n - 30, 13), -1):            # pole end = start of consolidation
            if found:
                break
            win = (h if bull else l)[max(0, t - 15):t + 1]
            if (bull and h[t] < win.max()) or (not bull and l[t] > win.min()):
                continue                                        # t = the pole's extreme
            seg = l[t - 12:t] if bull else h[t - 12:t]
            ps = t - 12 + (int(np.argmin(seg)) if bull else int(np.argmax(seg)))
            pole = (h[t] - l[ps]) if bull else (h[ps] - l[t])
            atr = a[t]
            if pole < 4 * atr or t - ps > 12 or t - ps < 2:
                continue
            # consolidation: from t until a breakout (or now), 3-20 candles
            for e in range(t + 3, min(n, t + 21)):
                cons_h, cons_l = h[t:e + 1], l[t:e + 1]
                rng = cons_h.max() - cons_l.min()
                if rng > 0.5 * pole:
                    break
                xs = np.arange(t, e + 1)
                up, lo = fit(list(zip(xs, cons_h))), fit(list(zip(xs, cons_l)))
                up = Line(t, up(t) + max(0, (cons_h - up(xs)).max()), slope=up.slope)   # envelope
                lo = Line(t, lo(t) - max(0, (lo(xs) - cons_l).max()), slope=lo.slope)
                slope_ok = (up.slope <= 0.05 * atr) if bull else (lo.slope >= -0.05 * atr)
                if not slope_ok:
                    continue
                conv = (up(e) - lo(e)) < 0.7 * (up(t) - lo(t))
                kind = ("Bull " if bull else "Bear ") + ("pennant" if conv else "flag")
                nxt = e + 1
                b = None
                if nxt < n:
                    if bull and c[nxt] > up(nxt) + BREAK_FILTER * a[nxt]:
                        b = nxt
                    elif not bull and c[nxt] < lo(nxt) - BREAK_FILTER * a[nxt]:
                        b = nxt
                    elif (bull and c[nxt] < lo(nxt)) or (not bull and c[nxt] > up(nxt)):
                        break                                   # broke the wrong way
                    else:
                        continue                                # still consolidating: extend
                d = "LONG" if bull else "SHORT"
                if bull:                                        # deck: pole start -> highest peak, added to the bottom
                    target = cons_l.min() + (cons_h.max() - l[ps])
                else:
                    target = cons_h.max() - (h[ps] - cons_l.min())
                pts = [(ps, l[ps] if bull else h[ps], "pole"), (t, h[t] if bull else l[t], "")]
                common = dict(points=pts, lines=[(up, t, None), (lo, t, None)], kind_lines=("res", "sup"),
                              pole=((ps, l[ps] if bull else h[ps]), (t, h[t] if bull else l[t])))
                if b is not None:
                    p = _finish(df, a, kind, ps, e, b, d, up if bull else lo, target, n, **common)
                elif e == n - 1:
                    p = _pat(kind, "forming", d, ps, e, up=float(up(n - 1)) if bull else None,
                             down=None if bull else float(lo(n - 1)), targets={d: target}, **common)
                else:
                    p = None
                if p:
                    out.append(p)
                    found = True
                break


def _gaps(df, a, out):
    """Explosion gap pivot (slides 40-41): a gap out of the recent range; wait for the throwback;
    if it doesn't cover the gap, its low is the pivot; buy above the gap candle's high."""
    o, h, l, c = (df[k].values for k in ("Open", "High", "Low", "Close"))
    n = len(c)
    for g in range(n - 2, max(n - 16, 21), -1):
        atr = a[g - 1]
        up = l[g] > h[g - 1] + 0.25 * atr and h[g] > h[g - 20:g].max()        # gap up, breaking out
        dn = h[g] < l[g - 1] - 0.25 * atr and l[g] < l[g - 20:g].min()        # gap down, breaking down
        if not (up or dn):
            continue
        after = slice(g + 1, n)
        if up:
            if l[after].min() <= h[g - 1]:
                continue                                        # throwback covered the gap: no action
            # throwback = at least one candle that closed lower; pivot = lowest low since the gap
            if not np.any(c[g + 1:] < c[g:-1]):
                continue
            pv = g + 1 + int(np.argmin(l[after]))
            b, _ = _first_break(c, a, pv + 1, upper=lambda x: h[g], filt=0.0)
            name, d, lvl, stop = "Gap up (explosion gap pivot)", "LONG", h[g], l[pv]
        else:
            if h[after].max() >= l[g - 1]:
                continue
            if not np.any(c[g + 1:] > c[g:-1]):
                continue
            pv = g + 1 + int(np.argmax(h[after]))
            b, _ = _first_break(c, a, pv + 1, lower=lambda x: l[g], filt=0.0)
            name, d, lvl, stop = "Gap down (explosion gap pivot)", "SHORT", l[g], h[pv]
        pts = [(g, lvl, "gap"), (pv, stop, "pivot")]
        common = dict(points=pts, gap=(g - 1, h[g - 1] if up else l[g - 1], l[g] if up else h[g]),
                      lines=[(Line(g, lvl, slope=0.0), g, None)], kind_lines=("res" if up else "sup",))
        if b is not None and b >= n - RECENT:
            brk = dict(bar=int(b), dir=d, level=float(lvl), target=None, stop=float(stop),
                       false=bool((c[-1] < lvl) if up else (c[-1] > lvl)) and b < n - 1)
            out.append(_pat(name, "breakout", d, g - 1, b, brk=brk, **common))
        elif b is None:
            out.append(_pat(name, "forming", d, g - 1, n - 1, up=lvl if up else None, down=None if up else lvl,
                            targets={}, stop=float(stop), **common))
        return


def _pipes(df, a, out):
    """Two-bar reversal / pipe bottom and top (slide 42)."""
    o, h, l, c = (df[k].values for k in ("Open", "High", "Low", "Close"))
    n = len(c)
    rng = h - l
    for j in range(n - 1, max(n - 1 - RECENT - 3, 25), -1):      # j = second bar of the pair
        i = j - 1
        prev = rng[i - 5:i]
        if min(rng[i], rng[j]) <= prev.max():
            continue                                            # both bars wider than the ones before
        lo_, hi_ = min(l[i], l[j]), max(h[i], h[j])
        bottom = (abs(l[i] - l[j]) <= 0.25 * max(rng[i], rng[j]) and c[i] <= l[i] + 0.25 * rng[i]
                  and c[j] >= l[j] + 0.5 * rng[j] and lo_ <= l[i - 20:i].min() and c[i - 20] > c[i])
        top = (abs(h[i] - h[j]) <= 0.25 * max(rng[i], rng[j]) and c[i] >= h[i] - 0.25 * rng[i]
               and c[j] <= h[j] - 0.5 * rng[j] and hi_ >= h[i - 20:i].max() and c[i - 20] < c[i])
        if not (bottom or top):
            continue
        name, d = ("Pipe bottom", "LONG") if bottom else ("Pipe top", "SHORT")
        lvl = h[j] if bottom else l[j]                          # action on a breakout through the second bar
        target = hi_ + (hi_ - lo_) if bottom else lo_ - (hi_ - lo_)
        b, bd = _first_break(c, a, j + 1, upper=(lambda x: lvl) if bottom else None,
                             lower=None if bottom else (lambda x: lvl), filt=0.0)
        common = dict(box=(i, j), points=[], lines=[(Line(i, lvl, slope=0.0), i, None)], kind_lines=("res" if bottom else "sup",))
        if b is not None:
            if b >= n - RECENT:
                brk = dict(bar=int(b), dir=d, level=float(lvl), target=float(target),
                           stop=float(lo_ if bottom else hi_), false=False)
                out.append(_pat(name, "breakout", d, i, b, brk=brk, **common))
        elif j >= n - RECENT:
            out.append(_pat(name, "forming", d, i, j, up=lvl if bottom else None, down=None if bottom else lvl,
                            targets={d: target}, stop=float(lo_ if bottom else hi_), **common))
        return


def _volatility(df, a, out):
    """Narrow range NR4 and inside bar (slides 43-44)."""
    o, h, l, c = (df[k].values for k in ("Open", "High", "Low", "Close"))
    n = len(c)
    rng = h - l
    # NR4: bar j narrower than the 3 before it; breakout = later close above its high / below its low
    for j in range(n - 1, n - 1 - RECENT, -1):
        if rng[j] < rng[j - 3:j].min():
            b, bd = _first_break(c, a, j + 1, upper=lambda x: h[j], lower=lambda x: l[j], filt=0.0)
            common = dict(box=(j, j), lines=[(Line(j, h[j], slope=0.0), j, None), (Line(j, l[j], slope=0.0), j, None)],
                          kind_lines=("res", "sup"))
            if b is not None and b >= n - RECENT and b == j + 1:
                brk = dict(bar=int(b), dir=bd, level=float(h[j] if bd == "LONG" else l[j]), target=None,
                           stop=float(l[j] if bd == "LONG" else h[j]), false=False)
                out.append(_pat("Narrow range (NR4)", "breakout", bd, j - 3, b, brk=brk, **common))
            elif j == n - 1:
                out.append(_pat("Narrow range (NR4)", "forming", "BOTH", j - 3, j, up=float(h[j]), down=float(l[j]),
                                targets={}, **common))
            break
    # inside bar j: next candle's open above the inside bar's close = buy, below = sell
    for j in range(n - 1, n - 1 - RECENT, -1):
        if h[j] < h[j - 1] and l[j] > l[j - 1]:
            common = dict(box=(j - 1, j), lines=[(Line(j, c[j], slope=0.0), j, None)], kind_lines=("neck",))
            if j + 1 < n and j + 1 >= n - RECENT:
                d = "LONG" if o[j + 1] > c[j] else "SHORT" if o[j + 1] < c[j] else None
                if d:
                    brk = dict(bar=j + 1, dir=d, level=float(c[j]), target=None,
                               stop=float(l[j] if d == "LONG" else h[j]), false=False)
                    out.append(_pat("Inside bar", "breakout", d, j - 1, j + 1, brk=brk, **common))
            elif j == n - 1:
                out.append(_pat("Inside bar", "forming", "BOTH", j - 1, j, up=float(c[j]), down=float(c[j]),
                                targets={}, **common))
            break


# ---------------------------------------------------------------- candlestick patterns
def _candles(df, a, out):
    """Slides 32-38. Bottom patterns only count after a decline (the pattern makes the lowest low of
    the last 10 candles), top patterns only after a rise. Activated when a later candle closes beyond
    the pattern's high (bullish) or low (bearish); doji, harami and hanging man can go either way."""
    o, h, l, c = (df[k].values for k in ("Open", "High", "Low", "Close"))
    n = len(c)
    body = np.abs(c - o)
    rng = h - l
    avg_body = pd.Series(body).rolling(10).mean().shift(1).values
    found = []
    for j in range(n - 1, n - 1 - RECENT - 1, -1):           # the pattern's last candle
        if j < 12:
            break
        atr = a[j]
        after_fall = lambda s: l[s:j + 1].min() <= l[j - 10:s].min() and c[j - 10] > c[s]
        after_rise = lambda s: h[s:j + 1].max() >= h[j - 10:s].max() and c[j - 10] < c[s]
        up_sh, lo_sh = h[j] - max(o[j], c[j]), min(o[j], c[j]) - l[j]
        white, black = c[j] > o[j], c[j] < o[j]
        cands = []
        if rng[j] >= 0.5 * atr and body[j] <= 0.1 * rng[j] and min(up_sh, lo_sh) >= 0.5 * max(up_sh, lo_sh):
            if after_fall(j) or after_rise(j):
                cands.append(("Doji", j, "BOTH"))
        small_hi = up_sh <= 0.1 * rng[j]
        small_lo = lo_sh <= 0.1 * rng[j]
        if rng[j] >= 0.7 * atr and lo_sh >= 2 * body[j] and small_hi and body[j] > 0:
            if white and after_fall(j):
                cands.append(("Hammer", j, "LONG"))
            elif black and after_rise(j):
                cands.append(("Hanging man", j, "BOTH"))
        if rng[j] >= 0.7 * atr and up_sh >= 2 * body[j] and small_lo and body[j] > 0:
            if after_rise(j):
                cands.append(("Shooting star", j, "SHORT"))
            elif after_fall(j):
                cands.append(("Inverted hammer", j, "LONG"))
        i = j - 1
        big_i = body[i] >= max(avg_body[i] if np.isfinite(avg_body[i]) else 0, 0.6 * atr)
        w_i, b_i = c[i] > o[i], c[i] < o[i]
        lo_b, hi_b = min(o[i], c[i]), max(o[i], c[i])
        if big_i and body[j] <= 0.5 * body[i] and min(o[j], c[j]) > lo_b and max(o[j], c[j]) < hi_b:
            if (w_i and black and after_rise(i)) or (b_i and white and after_fall(i)):
                cands.append(("Harami", j, "BOTH"))
        if min(o[j], c[j]) < lo_b and max(o[j], c[j]) > hi_b and body[j] >= 0.6 * atr:
            if b_i and white and after_fall(i):
                cands.append(("Bullish engulfing", j, "LONG"))
            elif w_i and black and after_rise(i):
                cands.append(("Bearish engulfing", j, "SHORT"))
        mid = (o[i] + c[i]) / 2
        if big_i and w_i and black and o[j] > c[i] and o[i] < c[j] < mid and after_rise(i):
            cands.append(("Dark cloud cover", j, "SHORT"))
        if big_i and b_i and white and o[j] < c[i] and mid < c[j] < o[i] and after_fall(i):
            cands.append(("Piercing line", j, "LONG"))
        for name, jj, d in cands:
            two = name in ("Harami", "Bullish engulfing", "Bearish engulfing", "Dark cloud cover", "Piercing line")
            s = jj - 1 if two else jj
            hi_, lo_ = h[s:jj + 1].max(), l[s:jj + 1].min()
            b, bd = _first_break(c, a, jj + 1, upper=None if d == "SHORT" else (lambda x: hi_),
                                 lower=None if d == "LONG" else (lambda x: lo_), filt=0.0)
            if b is not None and b > jj + 3:
                continue                                        # activation must come within 3 candles
            common = dict(box=(s, jj), lines=[], points=[])
            if b is not None:
                if b >= n - RECENT:
                    brk = dict(bar=int(b), dir=bd, level=float(hi_ if bd == "LONG" else lo_), target=None,
                               stop=float(lo_ if bd == "LONG" else hi_), false=False)
                    found.append(_pat(name, "breakout", bd, s, b, brk=brk, **common))
            elif jj == n - 1:
                found.append(_pat(name, "forming", d, s, jj, up=None if d == "SHORT" else float(hi_),
                                  down=None if d == "LONG" else float(lo_), targets={}, **common))
    out.extend(found)


# ---------------------------------------------------------------- main entry
def detect(df, families=("multi", "short", "candle")):
    """All deck patterns on these candles, one best multi-bar pattern per stage, deduplicated."""
    df = add_atr(df) if "ATR" not in df else df
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    if len(df) < 60:
        return []
    a = df["ATR"].values
    h, l = df["High"].values, df["Low"].values
    out = []
    if "multi" in families:
        multi = []
        for k in SCALES:
            piv = zigzag(h, l, a, k)
            for fn in (_tops_bottoms, _lines_patterns, _head_shoulders, _cup_handle):
                fn(df, a, piv, multi)
        out += _dedupe(multi)
    if "short" in families:
        for fn in (_flags, _gaps, _pipes, _volatility):
            fn(df, a, out)
    if "candle" in families:
        _candles(df, a, out)
    return out


def _dedupe(pats):
    """Overlapping multi-bar patterns: breakouts first, then the deck's more specific pattern
    (PRIORITY), then the bigger one. Patterns that don't overlap are all kept."""
    def order(p):
        pr = PRIORITY.index(p["name"])
        if p["stage"] == "forming" and p["name"] == "Rectangle":
            pr = -1           # equal highs AND equal lows: can still break either way -> rectangle until it breaks
        return (p["stage"] != "breakout", pr, -(p["end"] - p["start"]))
    keep = []
    for p in sorted(pats, key=order):
        if any(min(p["end"], q["end"]) - max(p["start"], q["start"])
               > 0.3 * min(p["end"] - p["start"], q["end"] - q["start"]) for q in keep):
            continue
        keep.append(p)
    return keep
