"""
Top-down trendlines: type a ticker, get one chart per timeframe (W, D, 4H, 1H, 30m, 15m, 5m)
with the trendlines drawn automatically.

Rules (same on every timeframe, each computed on that timeframe's own candles):
  * Red (down) lines start from the ABSOLUTE HIGHEST high of the lookback window. From each
    point, the line goes to the later swing high that keeps every swing high on or below the
    line (the least steep one). The next red line starts exactly where the previous one ended,
    so the lines form one chain and never cross.
  * Green (up) lines: the same from the ABSOLUTE LOWEST low, every swing low on or above the line.
  * Only the newest line of each chain is extended to the right, until price closes beyond it.
  * The most extreme candle of the last SWING bars counts as a swing as soon as it beats the
    SWING candles before it ("provisional"), so the newest low/high attaches a line right away.
  * Each chart also shows the lines of every HIGHER timeframe (dashed, thinner).

Run on GitHub: Actions -> Trendlines -> Run workflow -> type the ticker(s).
Run locally:   ALPACA_KEY_ID=... ALPACA_SECRET_KEY=... python trendlines.py AAPL
Charts go to trendlines-out/<TICKER>/ (and to Telegram if TELEGRAM_TOKEN / TELEGRAM_CHAT_ID are set).
"""
import datetime as dt
import io
import os
import sys
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ET = ZoneInfo("America/New_York")
OUT_DIR = os.environ.get("OUT_DIR", "trendlines-out")
SWING = int(os.environ.get("SWING", "5"))                  # swing strength: bars on each side
BREAK_BY = os.environ.get("BREAK_BY", "close")             # "close" or "wick"
SHOW_HTF = os.environ.get("SHOW_HTF", "1") == "1"          # draw higher-timeframe lines too

# timeframe -> (label, lookback bars searched for the absolute high/low, line width)
TFS = {
    "W":   ("Weekly", 300, 3.0),
    "D":   ("Daily", 250, 2.6),
    "4H":  ("4-hour", 400, 2.2),
    "1H":  ("1-hour", 500, 1.9),
    "30m": ("30-min", 600, 1.6),
    "15m": ("15-min", 1500, 1.4),
    "5m":  ("5-min", 1500, 1.2),
}
ORDER = list(TFS)                                          # highest -> lowest
UP, DN = "#4CAF50", "#F23645"
BG, GRID, TXT = "#131722", "#232835", "#d1d4dc"
C_UP, C_DN = "#26a69a", "#ef5350"


# ================================================================== data
def _alpaca(sym, timeframe, days):
    import scanner                                         # repo helper (Alpaca keys, paging)
    end = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=16)   # free plan: SIP 15+ min old
    start = end - dt.timedelta(days=days)
    df = scanner.alpaca_bars([sym], timeframe, start, end, chunk=1, workers=1, feed="sip").get(sym)
    if df is None or df.empty:
        raise RuntimeError(f"no {timeframe} data for {sym}")
    return df[~df.index.duplicated()].sort_index()


def _session(df):
    m = df.index.hour * 60 + df.index.minute
    return df[(m >= 570) & (m < 960)]                      # 9:30-16:00 New York


def _group(df, minutes):
    """Session-anchored candles: first one starts 9:30 (1H: 9:30..15:30, 4H: 9:30 and 13:30)."""
    m = df.index.hour * 60 + df.index.minute
    start = df.index.normalize() + pd.to_timedelta(570 + ((m - 570) // minutes) * minutes, unit="m")
    g = df.groupby(start)
    return pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(),
                         "Low": g["Low"].min(), "Close": g["Close"].last()}).sort_index()


def _day_index(df):
    df = df.copy()
    df.index = pd.DatetimeIndex([pd.Timestamp(d.date(), tz=ET) for d in df.index])
    return df[~df.index.duplicated(keep="last")]


def load(sym):
    """All 7 timeframes, regular hours only, index = candle start (New York time)."""
    m30 = _session(_alpaca(sym, "30Min", 330))
    frames = {
        "W":   _day_index(_alpaca(sym, "1Week", 365 * 8)),
        "D":   _day_index(_alpaca(sym, "1Day", 460)),
        "4H":  _group(m30, 240),
        "1H":  _group(m30, 60),
        "30m": m30,
        "15m": _session(_alpaca(sym, "15Min", 95)),
        "5m":  _session(_alpaca(sym, "5Min", 35)),
    }
    return {k: v[["Open", "High", "Low", "Close"]].astype(float) for k, v in frames.items()}


# ================================================================== the rules
def find_pivots(h, l, n, kind):
    """Swing highs/lows as (index, price), plus the provisional newest one."""
    v = h if kind == "high" else l
    out = []
    for i in range(n, len(v) - n):
        x = v[i]
        left, right = v[i - n:i], v[i + 1:i + n + 1]
        ok = (left <= x).all() and (right < x).all() if kind == "high" else \
             (left >= x).all() and (right > x).all()
        if ok:
            out.append((i, x))
    # provisional: most extreme of the last n bars (ties -> newest), strictly beyond its n left bars
    if len(v) > n:
        tail = np.arange(max(0, len(v) - n), len(v))
        best = tail[0]
        for i in tail:
            if (v[i] >= v[best]) if kind == "high" else (v[i] <= v[best]):
                best = i
        if best >= n:
            left = v[best - n:best]
            if ((left < v[best]).all() if kind == "high" else (left > v[best]).all()):
                out.append((int(best), v[best]))
    return out


def build_chain(df, pivots, is_up, lookback, break_by=BREAK_BY):
    """Chain from the absolute extreme of the window. Returns dict:
    vertices [(idx, price)...], broken (idx, price) or None, level (value at last bar) or None, slope."""
    h, l, c = df["High"].values, df["Low"].values, df["Close"].values
    last = len(df) - 1
    s0 = max(0, len(df) - lookback)
    a = s0 + int(np.argmin(l[s0:]) if is_up else np.argmax(h[s0:]))
    cur = (a, l[a] if is_up else h[a])
    verts = [cur]
    while True:
        best, best_s = None, None
        for i, p in pivots:
            if i <= cur[0]:
                continue
            s = (p - cur[1]) / (i - cur[0])
            if best is None or (s <= best_s if is_up else s >= best_s):
                best, best_s = (i, p), s
        if best is None:
            break
        verts.append(best)
        cur = best
    res = {"vertices": verts, "broken": None, "level": None, "slope": None}
    if len(verts) < 2:
        return res
    (ia, pa), (ib, pb) = verts[-2], verts[-1]
    slope = (pb - pa) / (ib - ia)
    res["slope"] = slope
    for i in range(ib + 1, last + 1):
        v = pb + slope * (i - ib)
        px = c[i] if break_by == "close" else (l[i] if is_up else h[i])
        if (px < v) if is_up else (px > v):
            res["broken"] = (i, v)
            return res
    res["level"] = pb + slope * (last - ib)
    return res


def analyze(df, lookback):
    h, l = df["High"].values, df["Low"].values
    up = build_chain(df, find_pivots(h, l, SWING, "low"), True, lookback)
    dn = build_chain(df, find_pivots(h, l, SWING, "high"), False, lookback)
    u, d = up["level"] is not None, dn["level"] is not None
    bias = "Up" if u and not d else "Down" if d and not u else "Squeeze" if u and d else "-"
    return {"up": up, "down": dn, "bias": bias}


# ================================================================== drawing
def htf_position(ltf_index, htf_index):
    """Position of every lower-timeframe candle in the higher timeframe's bar numbering
    (bar k + fraction of the way through it), so higher-timeframe lines stay exact."""
    starts = htf_index.values
    k = np.searchsorted(starts, ltf_index.values, side="right") - 1
    pos = np.zeros(len(k))
    counts = pd.Series(k).value_counts()
    full = counts[counts.index < k.max()] if len(counts) > 1 else counts
    per = float(np.median(full.values)) if len(full) else 1.0      # candles per higher bar
    seen = {}
    for j, kk in enumerate(k):
        n = seen.get(kk, 0)
        seen[kk] = n + 1
        pos[j] = kk + min(n / per, 0.999)
    return pos, per


def segments_xy(chain, f, f_future):
    """Turn a chain (in its own bar numbering) into x/y polylines on a chart whose candles sit at
    positions f (and future positions f_future). Returns [(xs, ys, is_last)]."""
    out = []
    verts = chain["vertices"]
    allf = np.concatenate([f, f_future])
    x = np.arange(len(allf))
    for j in range(len(verts) - 1):
        (ia, pa), (ib, pb) = verts[j], verts[j + 1]
        slope = (pb - pa) / (ib - ia)
        last = j == len(verts) - 2
        if not last:
            end = ib
        elif chain["broken"] is not None:
            end = chain["broken"][0]
        else:
            end = np.inf
        m = (allf >= ia) & (allf <= end)
        if not m.any():
            continue
        xs = x[m]
        ys = pa + slope * (allf[m] - ia)
        # start the line exactly on the vertex candle when it is on this chart
        out.append((xs, ys, last))
    return out


def draw(sym, tf, frames, results):
    label, lookback, _ = TFS[tf]
    df = frames[tf]
    n = len(df)
    w0 = max(0, n - lookback)
    view = df.iloc[w0:]
    nv = len(view)
    fut = max(10, nv // 12)                                # empty space on the right

    fig, ax = plt.subplots(figsize=(19, 9.5), dpi=110)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    # candles
    x = np.arange(nv)
    o, h, l, c = (view[k].values for k in ("Open", "High", "Low", "Close"))
    up = c >= o
    bw = 0.7 if nv < 400 else 0.8
    lw = 0.8 if nv < 400 else 0.5
    for msk, col in ((up, C_UP), (~up, C_DN)):
        ax.vlines(x[msk], l[msk], h[msk], color=col, linewidth=lw, zorder=2)
        ax.bar(x[msk], np.maximum(np.abs(c[msk] - o[msk]), (h.max() - l.min()) * 0.0008),
               bottom=np.minimum(o[msk], c[msk]), width=bw, color=col, linewidth=0, zorder=3)

    # lines: this timeframe (solid) + every higher timeframe (dashed)
    shown = [t for t in ORDER[:ORDER.index(tf) + 1] if t == tf or SHOW_HTF]
    for t in shown:
        res = results[t]
        if t == tf:
            f = np.arange(w0, n, dtype=float)
            per = 1.0
        else:
            f_all, per = htf_position(df.index, frames[t].index)
            f = f_all[w0:]
        f_future = f[-1] + (np.arange(1, fut + 1) / per)
        width = TFS[t][2] if t == tf else max(0.9, TFS[t][2] * 0.7)
        style = "-" if t == tf else (0, (6, 3))
        for side, col in (("up", UP), ("down", DN)):
            ch = res[side]
            segs = segments_xy(ch, f, f_future)
            for xs, ys, is_last in segs:
                ax.plot(xs, ys, color=col, lw=width, ls=style, alpha=1.0 if is_last else 0.45,
                        zorder=4 if t == tf else 3.5, solid_capstyle="round")
            if segs:
                xs, ys, _ = segs[-1]
                if ch["broken"] is not None:
                    ax.plot(xs[-1], ys[-1], marker="x", color=col, ms=8, mew=2, zorder=6)
                # label at the newest vertex (if it is on this chart)
                vi, vp = ch["vertices"][-1]
                hit = np.where(np.floor(np.concatenate([f, f_future])) == vi)[0]
                if len(hit):
                    k = shown.index(t)            # spread labels when chains end on the same point
                    ax.annotate(t, (hit[0], vp), xytext=(-8 + 20 * k, -13 if side == "up" else 7),
                                textcoords="offset points", ha="center", fontsize=8,
                                color=col, fontweight="bold", zorder=7)

    # axes
    lo, hi = l.min(), h.max()
    pad = (hi - lo) * 0.06
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(-1, nv + fut)
    ax.yaxis.tick_right()
    ax.grid(color=GRID, lw=0.6)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.tick_params(colors=TXT, labelsize=9)
    ticks = np.linspace(0, nv - 1, 10).astype(int)
    fmtd = "%b %Y" if tf == "W" else "%d %b %y" if tf == "D" else "%d %b %H:%M"
    ax.set_xticks(ticks)
    ax.set_xticklabels([view.index[i].strftime(fmtd) for i in ticks])
    last_px = c[-1]
    ax.axhline(last_px, color="#787b86", lw=0.6, ls=":", zorder=1)
    ax.annotate(f"{last_px:,.2f}", (1.0, last_px), xycoords=("axes fraction", "data"),
                xytext=(4, 0), textcoords="offset points", va="center", fontsize=9,
                color="white", bbox=dict(boxstyle="round,pad=0.25", fc="#2962FF", ec="none"))

    # title + top-down table
    htf_txt = f" + higher timeframes dashed" if SHOW_HTF and tf != "W" else ""
    ax.set_title(f"{sym} · {label} · trendlines from the absolute high / low{htf_txt}",
                 color=TXT, fontsize=13, loc="left", pad=10)
    rows = []
    for t in ORDER[:ORDER.index(tf) + 1]:
        r = results[t]
        def lv(ch):
            return f"{ch['level']:,.2f}" if ch["level"] is not None else ("broken" if ch["broken"] else "-")
        rows.append(f"{t:>3}  up {lv(r['up']):>10}  down {lv(r['down']):>10}  {r['bias']}")
    ax.text(0.005, 0.99, "\n".join(rows), transform=ax.transAxes, va="top", ha="left",
            family="monospace", fontsize=8.5, color=TXT, zorder=8,
            bbox=dict(boxstyle="round,pad=0.4", fc=BG, ec=GRID, alpha=0.92))

    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", facecolor=BG)
    plt.close(fig)
    return buf.getvalue()


def summary(sym, results):
    def lv(ch):
        return f"{ch['level']:,.2f}" if ch["level"] is not None else ("broken" if ch["broken"] else "–")
    lines = [f"<b>{sym} trendlines</b>", "<pre>TF   up line     down line   bias"]
    for t in ORDER:
        r = results[t]
        lines.append(f"{t:<4} {lv(r['up']):>10}  {lv(r['down']):>10}   {r['bias']}")
    return "\n".join(lines) + "</pre>"


# ================================================================== main
def run(sym, frames=None, tfs=None, telegram=True):
    sym = sym.strip().upper().replace(".", "-")
    frames = frames or load(sym)
    results = {t: analyze(frames[t], TFS[t][1]) for t in ORDER}
    out = os.path.join(OUT_DIR, sym)
    os.makedirs(out, exist_ok=True)
    text = summary(sym, results)
    with open(os.path.join(out, "summary.txt"), "w") as fh:
        fh.write(text.replace("<b>", "").replace("</b>", "").replace("<pre>", "").replace("</pre>", "") + "\n")
    charts = []
    for t in (tfs or ORDER):
        png = draw(sym, t, frames, results)
        path = os.path.join(out, f"{ORDER.index(t) + 1}_{t}.png")
        with open(path, "wb") as fh:
            fh.write(png)
        charts.append((t, png))
        print(f"  {sym} {t}: saved {path}", flush=True)
    if telegram:
        import scanner
        scanner.send_telegram(text)
        for t, png in charts:
            scanner.send_telegram(f"{sym} · {TFS[t][0]}", image=png)
    print(text.replace("<b>", "").replace("</b>", "").replace("<pre>", "").replace("</pre>", ""))
    return results


def main():
    raw = " ".join(sys.argv[1:]) or os.environ.get("TICKERS", "")
    syms = [s for s in raw.replace(",", " ").split() if s]
    if not syms:
        sys.exit("Give at least one ticker, e.g. python trendlines.py AAPL")
    tfs = [t.strip() for t in os.environ.get("TIMEFRAMES", "").replace(",", " ").split() if t.strip()]
    tfs = [t for t in ORDER if t in tfs] or ORDER
    send = os.environ.get("SEND_TELEGRAM", "1") == "1"
    failed = []
    for s in syms:
        if s.upper() in ("SPX", "^SPX", "^GSPC"):
            print("SPX is an index (no Alpaca data): using SPY instead.")
            s = "SPY"
        try:
            run(s, tfs=tfs, telegram=send)
        except Exception as e:
            print(f"{s}: failed - {e}", flush=True)
            failed.append(s)
    if failed:
        sys.exit(f"Failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
