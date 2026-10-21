"""
Pattern scan: finds the chart patterns of Fidelity's "Identifying Chart Patterns" deck on every
S&P 100 stock (rules in deck_patterns.py) and sends a Telegram alert with the chart and the
pattern drawn on it:
    🟡 forming   - the pattern has taken shape, price is still inside it (sent once)
    🟢/🔴 breakout - a candle closed beyond the breakout line: the pattern is complete
Each run ends with one summary message listing everything found.

Runs once a day after the US close on daily candles (.github/workflows/pattern-scan.yml), or by
hand on daily or hourly candles. Alerts go to TELEGRAM_CHAT_ID (the workflow points it at the
pattern group). Never trades.

    python pattern_scan.py                 # needs ALPACA keys (or falls back to Yahoo)
    PREVIEW=1 python pattern_scan.py       # no Telegram: saves charts + summary to reports/pattern-scan/
"""
import datetime as dt
import html
import io
import json
import os
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle as Box
import numpy as np
import pandas as pd

import deck_patterns as dp
from fmt import px
from scanner import ET, alpaca_bars, download, get_tickers, send_telegram, to_session

# ---------------------------------------------------------------- settings
TIMEFRAME = os.environ.get("PATTERN_TF", "1d")          # "1d" daily candles, "1h" hourly candles
PREVIEW = os.environ.get("PREVIEW") == "1"               # save charts to the repo instead of Telegram
RESEND = os.environ.get("RESEND") == "1"                 # ignore the memory: send everything found now
STATE_FILE = os.environ.get("STATE_FILE", "deck_pattern_state.json")
MAX_CHARTS = 30           # chart messages per run; anything beyond goes in the summary only
CHART_BARS = 110          # candles shown on a chart (more if the pattern is longer)

# What each pattern sends: "chart" = its own message with the chart, "list" = only a line in the
# summary, "off" = ignored. Keys are a family ("multi", "short", "candle") or a pattern name;
# a pattern name wins over its family. Candlestick patterns only get a chart once ACTIVATED (deck
# slide 32); while they wait for activation they are listed in the summary.
ALERTS = {
    "multi": "chart",                 # double/triple tops & bottoms, rectangles, triangles, wedges, H&S, cup
    "short": "chart",                 # flags, pennants, gaps, pipe bottom/top
    "candle": "chart",                # doji, harami, hammer, shooting star, engulfing, dark cloud/piercing
    "Narrow range (NR4)": "list",     # very frequent (about 1 candle in 4): summary only
    "Inside bar": "list",             # very frequent: summary only
    "Doji": "list",                   # "extremely common" (deck): summary only
}
STAGES = {"forming": "🟡 forming", "breakout": "breakout"}
TF_NAME = {"1d": "daily", "1h": "hourly"}[TIMEFRAME]
UP, DOWN, PAT, BLUE, GREY = "#26a69a", "#ef5350", "#ffd54f", "#42a5f5", "#9e9e9e"


def mode_of(p):
    m = ALERTS.get(p["name"], ALERTS.get(p["family"], "chart"))
    if m == "chart" and p["family"] == "candle" and p["stage"] == "forming":
        m = "list"
    return m


# ---------------------------------------------------------------- prices
def get_candles(tickers, now):
    """Completed candles per ticker: Alpaca (all exchanges, 16 minutes delayed), Yahoo as backup."""
    end = (now - dt.timedelta(minutes=16)).astimezone(dt.timezone.utc)
    if TIMEFRAME == "1d":
        data = alpaca_bars(tickers, "1Day", end - dt.timedelta(days=430), end)
        for t, d in data.items():
            d.index = d.index.tz_convert(ET).tz_localize(None).normalize()
    else:
        data = {t: to_session(d, 60) for t, d in
                alpaca_bars(tickers, "30Min", end - dt.timedelta(days=75), end).items()}
    missing = [t for t in tickers if len(data.get(t, [])) < 60]
    if missing:
        print(f"{len(missing)} tickers from Yahoo: {' '.join(missing[:15])}{' ...' if len(missing) > 15 else ''}")
        y = download(missing, "2y", "1d") if TIMEFRAME == "1d" else download(missing, "60d", "1h")
        for t, d in y.items():
            d = d[["Open", "High", "Low", "Close", "Volume"]].astype(float)
            if d.index.tz is not None:
                d.index = d.index.tz_convert(ET).tz_localize(None)
            if TIMEFRAME == "1d":
                d.index = d.index.normalize()
            data[t] = d
    out = {}
    for t, d in data.items():
        d = d.sort_index()
        if TIMEFRAME == "1d":
            # drop today's candle while the market is still open (only completed candles count)
            if len(d) and d.index[-1].date() == now.date() and (now.hour, now.minute) < (16, 0):
                d = d.iloc[:-1]
        else:
            last_end = d.index[-1] + pd.Timedelta(minutes=60) if len(d) else None
            if last_end is not None:
                if last_end.tzinfo is None:
                    last_end = last_end.tz_localize(ET)
                if min(last_end, last_end.replace(hour=16, minute=0)) > now:
                    d = d.iloc[:-1]
        if len(d) >= 60:
            out[t] = d
    return out


# ---------------------------------------------------------------- chart
def _date(df, i):
    ts = pd.Timestamp(df.index[min(i, len(df) - 1)])
    return ts.strftime("%d %b") if TIMEFRAME == "1d" else ts.strftime("%d %b %H:%M")


def draw(ticker, df, p):
    """Candles on black with the pattern drawn: its trend lines, swing points, breakout candle,
    breakout level, target and protective stop (or, while forming, both breakout lines)."""
    n = len(df)
    brk = p.get("brk")
    start = max(0, min(n - CHART_BARS, p["start"] - 15))
    d = df.iloc[start:]
    x = np.arange(start, n)
    o, h, l, c = (d[k].values.astype(float) for k in ("Open", "High", "Low", "Close"))

    fig, ax = plt.subplots(figsize=(11, 6.2), dpi=110)
    fig.patch.set_facecolor("black")
    ax.set_facecolor("black")
    for s in ax.spines.values():
        s.set_color("#444")
    ax.tick_params(colors="#bbbbbb", labelsize=8)
    ax.grid(color="#222", lw=0.6)

    up = c >= o
    col = np.where(up, UP, DOWN)
    ax.vlines(x, l, h, color=col, lw=0.8, zorder=2)
    span = float(h.max() - l.min()) or 1.0
    ax.bar(x, np.maximum(np.abs(c - o), span * 0.002), 0.62, bottom=np.minimum(o, c), color=col,
           edgecolor=col, lw=0.4, zorder=3)
    lo_y, hi_y = float(l.min()), float(h.max())
    right = n - 1

    # pattern area shading between its two lines
    if len(p["lines"]) == 2 and p["family"] != "short" or p["name"].startswith(("Bull", "Bear")):
        (l1, a1, _), (l2, a2, _) = p["lines"][:2]
        x0 = max(a1, a2)
        x1 = brk["bar"] if brk else right
        xs = np.arange(x0, x1 + 1)
        ax.fill_between(xs, l1(xs), l2(xs), color=PAT, alpha=0.07, zorder=1)
    # trend lines
    for ln, xa, xb in p["lines"]:
        xb = xb if xb is not None else (min(right, brk["bar"] + 3) if brk else right)
        ax.plot([xa, xb], [ln(xa), ln(xb)], color=PAT, lw=1.8, zorder=5)
    if p.get("shape"):                                   # head & shoulders outline
        xs, ys = zip(*p["shape"])
        ax.plot(xs, ys, color=PAT, lw=1.0, alpha=0.6, ls="--", zorder=5)
    if p.get("curve"):                                   # cup
        ax.plot(p["curve"][0], p["curve"][1], color=PAT, lw=1.6, alpha=0.9, zorder=5)
    if p.get("pole"):                                    # flag pole
        (xa, ya), (xb, yb) = p["pole"]
        ax.plot([xa, xb], [ya, yb], color=PAT, lw=2.4, alpha=0.8, zorder=5)
    if p.get("gap"):                                     # the gap itself
        g0, y0, y1 = p["gap"]
        ax.add_patch(Box((g0 + 0.5, min(y0, y1)), right - g0, abs(y1 - y0), color=BLUE, alpha=0.18, lw=0, zorder=1))
    if p.get("box"):                                     # candlestick / two-bar patterns
        b0, b1 = p["box"]
        bl, bh = float(df["Low"].values[b0:b1 + 1].min()), float(df["High"].values[b0:b1 + 1].max())
        pad = 0.02 * span
        ax.add_patch(Box((b0 - 0.6, bl - pad), b1 - b0 + 1.2, bh - bl + 2 * pad, fill=False, edgecolor=PAT,
                         lw=1.6, zorder=6))
    for i, price, lab in p["points"]:
        ax.scatter([i], [price], color=PAT, s=24, zorder=6)
        if lab:
            low = price <= float(df["Low"].values[i]) + 1e-9
            ax.annotate(lab, (i, price), textcoords="offset points", xytext=(0, -14 if low else 8),
                        ha="center", fontsize=8, color="white", fontweight="bold", zorder=7)

    def level(val, colr, text, ls="--"):
        nonlocal lo_y, hi_y
        if val is None or not np.isfinite(val):
            return
        x0 = brk["bar"] if brk else max(start, p["end"])
        ax.plot([x0, right + 12], [val, val], color=colr, ls=ls, lw=1.0, zorder=4)
        pct = (val / c[-1] - 1) * 100
        ax.text(right + 12.3, val, f" {text} {px(val)} ({pct:+.1f}%)", color=colr, va="center", fontsize=8,
                bbox=dict(facecolor="black", edgecolor="none", pad=1), zorder=8)
        lo_y, hi_y = min(lo_y, val), max(hi_y, val)

    if brk:
        b = brk["bar"]
        long = brk["dir"] == "LONG"
        yb = float(df["Low"].values[b]) if long else float(df["High"].values[b])
        ax.annotate("", xy=(b, yb), xytext=(b, yb - (1 if long else -1) * 0.07 * span), zorder=8,
                    arrowprops=dict(arrowstyle="-|>", color=UP if long else DOWN, lw=2.2))
        ax.annotate("breakout", (b, yb), textcoords="offset points", xytext=(0, -26 if long else 22),
                    ha="center", fontsize=8, color=UP if long else DOWN, fontweight="bold", zorder=8)
        level(brk["level"], BLUE, "breakout")
        level(brk.get("target"), UP if long else DOWN, "target")
        level(brk["stop"], DOWN if long else UP, "stop")
    else:
        if p.get("up") is not None:
            level(p["up"], UP, "breaks up above")
            level(p["targets"].get("LONG"), UP, "target if up", ls=":")
        if p.get("down") is not None:
            level(p["down"], DOWN, "breaks down below")
            level(p["targets"].get("SHORT"), DOWN, "target if down", ls=":")
        if p.get("stop") is not None:
            level(p["stop"], GREY, "stop")

    pad = 0.05 * (hi_y - lo_y or 1)
    ax.set_ylim(lo_y - pad, hi_y + pad)
    ax.set_xlim(start - 1, right + 30)
    step = max(1, len(d) // 8)
    ticks = list(range(start, n, step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([_date(df, i) for i in ticks])
    arrow = {"LONG": " ↑", "SHORT": " ↓"}.get(p["dir"], "")
    stage = "BREAKOUT" if brk else "forming"
    ax.set_title(f"{ticker}  ·  {p['name']}{arrow}  ·  {stage}  ·  {TF_NAME}", color="white", fontsize=12)
    ax.text(0.01, 0.98, "\n".join(_wrap(p["note"], 70)), transform=ax.transAxes, va="top", ha="left",
            fontsize=7.5, color="#cfcfcf", zorder=9,
            bbox=dict(facecolor="#131722", edgecolor="#444", alpha=0.9, pad=5))
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", facecolor="black")
    plt.close(fig)
    return buf.getvalue()


def _wrap(text, width):
    import textwrap
    return textwrap.wrap(text, width) or [""]


# ---------------------------------------------------------------- messages
def _pct(v, ref):
    return f"{(v / ref - 1) * 100:+.1f}%"


def caption(t, df, p):
    c = float(df["Close"].iat[-1])
    link = f"https://www.tradingview.com/chart/?symbol={t.replace('-', '.')}"
    name = html.escape(p["name"])
    brk = p.get("brk")
    if brk:
        long = brk["dir"] == "LONG"
        icon = "🟢" if long else "🔴"
        lines = [f"{icon} <b>{t}</b> — {name} {'↑' if long else '↓'} <b>BREAKOUT</b> ({TF_NAME})",
                 f"Close {px(c)} on {_date(df, len(df) - 1)}; broke {'above' if long else 'below'} "
                 f"{px(brk['level'])} on {_date(df, brk['bar'])}"]
        lv = []
        if brk.get("target") is not None:
            lv.append(f"🎯 target {px(brk['target'])} ({_pct(brk['target'], c)})")
        lv.append(f"🛑 stop {px(brk['stop'])} ({_pct(brk['stop'], c)})")
        if brk.get("target") is not None and abs(c - brk["stop"]) > 0:
            lv.append(f"R:R {abs(brk['target'] - c) / abs(c - brk['stop']):.1f}")
        lines.append(" · ".join(lv))
        if brk.get("false"):
            lines.append("⚠️ Price is back through the breakout line: possible false breakout")
    else:
        arrow = {"LONG": " ↑", "SHORT": " ↓"}.get(p["dir"], "")
        lines = [f"🟡 <b>{t}</b> — {name}{arrow} forming ({TF_NAME})", f"Price now {px(c)}"]
        if p.get("up") is not None:
            tg = p["targets"].get("LONG")
            lines.append(f"↑ completes on a close above {px(p['up'])} ({_pct(p['up'], c)})"
                         + (f" → target {px(tg)}" if tg is not None else ""))
        if p.get("down") is not None:
            tg = p["targets"].get("SHORT")
            lines.append(f"↓ completes on a close below {px(p['down'])} ({_pct(p['down'], c)})"
                         + (f" → target {px(tg)}" if tg is not None else ""))
    lines.append(f"📖 {html.escape(p['note'])}")
    lines.append(f'<a href="{link}">TradingView</a>')
    return "\n".join(lines)


def short_line(t, df, p):
    c = float(df["Close"].iat[-1])
    brk = p.get("brk")
    if brk:
        long = brk["dir"] == "LONG"
        s = f"{'🟢' if long else '🔴'} {t} {p['name']} {'↑' if long else '↓'} @ {px(brk['level'])}"
        if brk.get("target") is not None:
            s += f" → {px(brk['target'])}"
        return s + (" ⚠️" if brk.get("false") else "")
    parts = []
    if p.get("up") is not None:
        parts.append(f"↑{px(p['up'])}")
    if p.get("down") is not None:
        parts.append(f"↓{px(p['down'])}")
    return f"🟡 {t} {p['name']} ({' / '.join(parts)}, now {px(c)})"


# ---------------------------------------------------------------- memory
def key(t, df, p):
    d = p.get("brk", {}) or {}
    return "|".join([t, TIMEFRAME, p["name"], p["stage"], d.get("dir", p["dir"]), _date(df, p["start"])])


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"sent": {}}


def save_state(state, today):
    cutoff = (today - dt.timedelta(days=90)).isoformat()
    state["sent"] = {k: v for k, v in state["sent"].items() if v >= cutoff}
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=0)


# ---------------------------------------------------------------- main
RANK = {"breakout": 0, "forming": 1}
FAM_RANK = {"multi": 0, "short": 1, "candle": 2}


def main():
    t0 = time.time()
    now = dt.datetime.now(ET)
    today = now.date()
    tickers = get_tickers()
    print(f"{len(tickers)} tickers, {TF_NAME} candles, preview={PREVIEW}, resend={RESEND}", flush=True)
    data = get_candles(tickers, now)
    print(f"Prices for {len(data)} tickers ({time.time() - t0:.0f}s)", flush=True)

    found = []                                             # (ticker, df, pattern)
    for t in sorted(data):
        df = dp.add_atr(data[t])
        try:
            pats = dp.detect(df)
        except Exception as e:                             # one bad stock never stops the scan
            print(f"{t}: detection error {e!r}")
            continue
        for p in pats:
            if mode_of(p) != "off":
                found.append((t, df, p))
    found.sort(key=lambda x: (RANK[x[2]["stage"]], FAM_RANK[x[2]["family"]], x[0]))
    print(f"Found {len(found)} patterns ({time.time() - t0:.0f}s)", flush=True)

    state = {"sent": {}} if RESEND else load_state()
    out_dir = None
    if PREVIEW:
        out_dir = os.path.join("reports", "pattern-scan", f"{today.isoformat()}-{TIMEFRAME}")
        os.makedirs(out_dir, exist_ok=True)
        for f in os.listdir(out_dir):
            os.remove(os.path.join(out_dir, f))
    charts = 0
    new_lines, old_lines, listed = [], [], []
    for t, df, p in found:
        k = key(t, df, p)
        is_new = k not in state["sent"]
        line = short_line(t, df, p)
        if not is_new:
            old_lines.append(line)
            continue
        if mode_of(p) == "chart" and charts < MAX_CHARTS:
            img = draw(t, df, p)
            cap = caption(t, df, p)
            if PREVIEW:
                fn = f"{charts + 1:02d}-{t}-{p['name'].split(' (')[0].replace(' ', '_').replace('&', 'and')}-{p['stage']}.png"
                with open(os.path.join(out_dir, fn), "wb") as f:
                    f.write(img)
                listed.append(f"### {fn}\n\n![]({fn})\n\n{cap}\n")
                ok = True
            else:
                ok = send_telegram(cap, img)
            charts += 1
            if ok:
                state["sent"][k] = today.isoformat()
            new_lines.append(line)
        else:
            new_lines.append(line + ("" if mode_of(p) == "list" else "  (no chart: limit reached)"))
            state["sent"][k] = today.isoformat()

    # summary message: everything found this run, new first
    n_brk = sum(1 for _, _, p in found if p["stage"] == "breakout")
    head = (f"🧩 <b>Pattern scan</b> — S&P 100, {TF_NAME} candles, {now.strftime('%d %b %H:%M')} NY\n"
            f"{n_brk} breakouts, {len(found) - n_brk} forming · {charts} charts sent")
    body = []
    if new_lines:
        body += ["", "<b>New</b>"] + [html.escape(x) for x in new_lines]
    if old_lines:
        body += ["", "<b>Still there (already sent)</b>"] + [html.escape(x) for x in old_lines]
    if not found:
        body.append("No patterns today.")
    msgs, cur = [], head
    for line in body:
        if len(cur) + len(line) + 1 > 3900:
            msgs.append(cur)
            cur = ""
        cur += "\n" + line
    msgs.append(cur + f"\n\n✅ scan done in {time.time() - t0:.0f}s")
    if PREVIEW:
        with open(os.path.join(out_dir, "README.md"), "w") as f:
            f.write(f"# Pattern scan preview {today} ({TF_NAME})\n\n```\n" + "\n\n".join(msgs).replace("<b>", "").replace("</b>", "")
                    + "\n```\n\n" + "\n".join(listed))
        print(f"Preview saved in {out_dir}")
    else:
        for m in msgs:
            send_telegram(m)
        save_state(state, today)
    print("\n\n".join(msgs))


if __name__ == "__main__":
    main()
