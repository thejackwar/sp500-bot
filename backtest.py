"""
Backtest of the bot's paper-trading strategy on the S&P 100.

It replays the past few years check by check (every 15 minutes, like the live bot) with the SAME
rules and settings the bot uses today (rules.py and trader.py):
    ⭐ STRONG LONG  = price above the 200-day MA + Donchian breakout up + Bollinger confirms
    ⭐ STRONG SHORT = the mirror image
    daily chart (Donchian 20, Bollinger 2.0, checked with the live price, once per day) and
    15-minute chart (Donchian 55, Bollinger 2.5, every candle)
    -> market entry at the next price, stop 2 x daily ATR, target 2:1, risk 1% of equity,
       max 20% of equity per stock, max 10 open positions, max 3 new per check,
       one trade per stock per day.
Fills: entries and stops get a small slippage; a stop or target that the price gaps through fills
at the opening price; if a 15-minute candle touches both the stop and the target, the stop is
assumed to be hit first (the cautious assumption).

Run it on GitHub: Actions -> Backtest -> Run workflow. The results arrive on Telegram
(summary, equity chart, list of trades) and are saved with the run as "backtest-results".
"""
import datetime as dt
import html
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests

import rules
import trader

from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

START_EQUITY = 100_000      # Alpaca paper accounts start with $100,000
SLIPPAGE = 0.0002           # 0.02% worse price on market fills (entries and stops)
MARGIN = 2.0                # buying power = 2 x equity minus what is already invested
BENCHMARK = "SPY"
BARS_URL = "https://data.alpaca.markets/v2/stocks/{}/bars"
OUT = "backtest-results"
CACHE_DIR = "price-cache"           # saved prices (kept between runs by the workflow)

VARIANTS = {                # which signals open trades
    "bot": ("As the bot trades (daily + 15-min)", ("daily", "intraday")),
    "daily": ("Daily signals only", ("daily",)),
    "intraday": ("15-min signals only", ("intraday",)),
}


# ---------------------------------------------------------------- data
def _headers():
    key, secret = os.environ.get("ALPACA_KEY_ID"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        sys.exit("ALPACA_KEY_ID / ALPACA_SECRET_KEY are not set (add them as repository secrets).")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def fetch(sym, timeframe, start, end, headers):
    """All bars for one stock (split-adjusted). Intraday: regular session only (9:30-16:00)."""
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    params = {"timeframe": timeframe, "start": start.strftime(fmt), "end": end.strftime(fmt),
              "limit": 10000, "adjustment": "all", "feed": "sip"}
    frames = []
    while True:
        r = None
        for attempt in range(10):
            try:
                r = requests.get(BARS_URL.format(sym.replace("-", ".")), headers=headers, params=params, timeout=60)
            except requests.RequestException:
                time.sleep(5)
                continue
            if r.status_code == 429:                      # too many requests: wait and retry
                time.sleep(15)
                continue
            break
        if r is None or not r.ok:
            raise RuntimeError(f"{sym}: {r.status_code if r is not None else 'no connection'} "
                               f"{r.text[:200] if r is not None else ''}")
        js = r.json()
        bars = js.get("bars") or []
        if bars:
            df = pd.DataFrame(bars)
            df.index = pd.DatetimeIndex(pd.to_datetime(df["t"], utc=True)).tz_convert(ET)
            df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close"})
            df = df[["Open", "High", "Low", "Close"]].astype(float)
            if timeframe != "1Day":
                mins = df.index.hour * 60 + df.index.minute
                df = df[(mins >= 570) & (mins < 960)]
            frames.append(df)
        if not js.get("next_page_token"):
            break
        params["page_token"] = js["next_page_token"]
    if not frames:
        return None
    df = pd.concat(frames)
    df = df[~df.index.duplicated()].sort_index()
    if timeframe == "1Day":
        df.index = df.index.tz_localize(None).normalize()
    return df


def _load(path):
    try:
        return pd.read_pickle(path)
    except Exception:
        return None


def cached_fetch(sym, timeframe, start, end, headers):
    """Bars from the saved copy, downloading only what is new since the last run.
    The whole history is downloaded again when the saved copy doesn't reach back far enough, or when
    Alpaca has re-adjusted old prices (after a split or dividend the last saved candle no longer matches)."""
    path = os.path.join(CACHE_DIR, f"{sym}_{timeframe}.pkl.gz")
    old = _load(path)
    if old is not None and len(old) > 1 and old.attrs.get("from") and pd.Timestamp(old.attrs["from"]) <= pd.Timestamp(start):
        last = old.index[-1]
        since = (last.tz_localize(ET) if last.tzinfo is None else last).tz_convert("UTC").to_pydatetime()
        new = fetch(sym, timeframe, since, end, headers)
        same = new is not None and last in new.index and \
            abs(new.at[last, "Close"] / old.at[last, "Close"] - 1) < 2e-4
        if new is None or same:
            df = old if new is None else pd.concat([old.iloc[:-1], new])   # the last saved candle may have been unfinished
            df = df[~df.index.duplicated(keep="last")].sort_index()
            df.attrs["from"] = old.attrs["from"]
            df.attrs["fresh"] = False
            df.to_pickle(path)
            return df
    df = fetch(sym, timeframe, start, end, headers)
    if df is not None:
        df.attrs["from"] = pd.Timestamp(start).isoformat()
        df.attrs["fresh"] = True
        df.to_pickle(path)
    return df


def download(tickers, start, end, workers=10):
    """Prices for every stock. Saved in price-cache/ (kept by GitHub between runs), so only the first
    run downloads the whole history; later runs add the days since the previous run."""
    headers = _headers()
    os.makedirs(CACHE_DIR, exist_ok=True)
    t0 = time.time()
    daily, intraday = {}, {}
    full = 0

    def one(sym):
        try:
            d = cached_fetch(sym, "1Day", start - dt.timedelta(days=450), end, headers)
            h = cached_fetch(sym, "15Min", start - dt.timedelta(days=21), end, headers)
            return sym, d, h, None
        except Exception as e:
            return sym, None, None, e

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for n, (sym, d, h, err) in enumerate(pool.map(one, tickers), 1):
            if err:
                print(f"{sym}: download failed ({err})", flush=True)
            elif d is not None and h is not None and len(d) > 250 and len(h) > 500:
                cut = pd.Timestamp(start).tz_convert(ET) - pd.Timedelta(days=21)
                daily[sym] = d[d.index >= (cut - pd.Timedelta(days=430)).tz_localize(None)]
                intraday[sym] = h[h.index >= cut]
                full += bool(h.attrs.get("fresh"))
            if n % 10 == 0:
                print(f"Prices {n}/{len(tickers)} ({time.time() - t0:.0f}s)", flush=True)
    print(f"Prices ready in {time.time() - t0:.0f}s: {full} stocks downloaded in full, "
          f"{len(intraday) - full} from the saved copy + new days", flush=True)
    return daily, intraday


# ---------------------------------------------------------------- signals (same rules as rules.py)
def daily_context(D):
    """What is known about each day BEFORE it opens (row d = values from the completed days up to d-1),
    so today's indicators can be finished with the live price, exactly like the bot does."""
    c, hi, lo = D["Close"], D["High"], D["Low"]
    n = rules.BB_PERIOD
    std, dn = rules.SETTINGS["daily"]["bb_std"], rules.SETTINGS["daily"]["donchian"]
    lb = rules.SQUEEZE_LOOKBACK["daily"]
    tr = pd.concat([hi - lo, (hi - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    mid, sd = c.rolling(n).mean(), c.rolling(n).std(ddof=0)
    width = 2 * std * sd / mid
    squeeze = (width <= width.rolling(lb).min().shift(1)).astype(float)
    dch, dcl = hi.rolling(dn).max(), lo.rolling(dn).min()
    known = pd.DataFrame({
        "pc": c,                                             # yesterday's close
        "atr": tr.ewm(alpha=1 / 14, adjust=False).mean(),
        "s_sma": c.rolling(rules.SMA_LONG - 1).sum(),        # + today's price = 200-day MA
        "s_bb": c.rolling(n - 1).sum(), "s_bb2": (c * c).rolling(n - 1).sum(),
        "dch": dch, "dcl": dcl,                              # today's Donchian channel
        "up_ok": (c <= dch.shift(1)).astype(float),          # yesterday closed inside the channel
        "dn_ok": (c >= dcl.shift(1)).astype(float),
        "minw": width.rolling(lb).min(),                     # narrowest band width of the last 125 days
        "sq_recent": squeeze.rolling(rules.SQUEEZE_RECENT).max(),
    })
    return known.shift(1)


def stock_signals(h, D, debug=False):
    """Every STRONG signal of one stock. Returns one row per check that has one:
    check (time of the check), fill (start of the candle the order fills on), dl/ds/il/is (daily or
    intraday long/short strong), atr (daily ATR incl. today, used for the stop), price."""
    day = pd.DatetimeIndex(h.index.tz_localize(None).normalize())
    H = h["High"].groupby(day).cummax().values
    L = h["Low"].groupby(day).cummin().values
    n = len(h)
    pos = np.arange(n)
    same_next = np.r_[day[1:] == day[:-1], False]
    first = np.r_[True, day[1:] != day[:-1]]
    at_open = first & (h.index.hour == 9) & (h.index.minute == 30)

    # intraday chart (15-min): rules.py's own indicator code
    s = rules.SETTINGS["intraday"]
    hi = rules.add_strategy_indicators(h, rules.SQUEEZE_LOOKBACK["intraday"], **s)
    c, dch, dcl = hi["Close"], hi["DC_high"], hi["DC_low"]
    recent = hi["Squeeze"].astype(float).rolling(rules.SQUEEZE_RECENT + 1, min_periods=1).max() > 0
    i_up = ((c.shift() <= dch.shift()) & (c > dch) & ((c > hi["BB_up"]) | recent)).values
    i_dn = ((c.shift() >= dcl.shift()) & (c < dcl) & ((c < hi["BB_lo"]) | recent)).values

    # checks: the opening check (9:30, live price = first trade) and one after every candle closes
    op = np.flatnonzero(at_open)
    chk = pd.DataFrame({
        "check": np.r_[h.index[op], h.index + pd.Timedelta(minutes=15)],
        "day": np.r_[day[op], day],
        "p": np.r_[h["Open"].values[op], h["Close"].values],
        "H": np.r_[h["Open"].values[op], H],
        "L": np.r_[h["Open"].values[op], L],
        "fill": np.r_[op, np.where(same_next, pos + 1, -1)],       # -1 = market already closed
        "i_up": np.r_[np.zeros(len(op), bool), i_up],
        "i_dn": np.r_[np.zeros(len(op), bool), i_dn],
    }).sort_values("check", kind="stable").reset_index(drop=True)
    k = daily_context(D).reindex(chk["day"]).reset_index(drop=True)

    p = chk["p"]
    sma = (k["s_sma"] + p) / rules.SMA_LONG
    mid = (k["s_bb"] + p) / rules.BB_PERIOD
    sd = np.sqrt(((k["s_bb2"] + p * p) / rules.BB_PERIOD - mid * mid).clip(lower=0))
    bstd = rules.SETTINGS["daily"]["bb_std"]
    up, lo = mid + bstd * sd, mid - bstd * sd
    sq_today = ((up - lo) / mid) <= k["minw"]
    confirm_up = (p > up) | sq_today | (k["sq_recent"] > 0)
    confirm_dn = (p < lo) | sq_today | (k["sq_recent"] > 0)
    above, below = p > sma, p < sma

    # daily Donchian breakout: alerted once per day, at the first check it happens
    d_up = (k["up_ok"] > 0) & (p > k["dch"])
    d_dn = (k["dn_ok"] > 0) & (p < k["dcl"])
    d_up &= ~d_up.groupby(chk["day"]).cumsum().gt(1)
    d_dn &= ~d_dn.groupby(chk["day"]).cumsum().gt(1)

    tr = np.maximum.reduce([chk["H"] - chk["L"], (chk["H"] - k["pc"]).abs(), (chk["L"] - k["pc"]).abs()])
    out = pd.DataFrame({
        "check": chk["check"], "fill": chk["fill"], "price": p,
        "dl": d_up & above & confirm_up, "ds": d_dn & below & confirm_dn,
        "il": chk["i_up"] & above, "is": chk["i_dn"] & below,
        "atr": k["atr"] + (tr - k["atr"]) / 14,
    })
    if debug:                                        # every check with its indicator values (tests)
        return out.assign(sma=sma, up=up, lo=lo, sq=sq_today | (k["sq_recent"] > 0), dch=k["dch"], dcl=k["dcl"])
    out = out[out[["dl", "ds", "il", "is"]].any(axis=1)]
    out = out[out["fill"] >= 0].copy()
    out["fill"] = h.index[out["fill"].values]
    return out


# ---------------------------------------------------------------- portfolio simulation
def simulate(sig, O, H, L, C, slots, syms, kinds):
    """Replays the trader's rules (trader.py) over time. sig: all signals with 'slot' and 'col'."""
    use = sig.copy()
    long_ = np.zeros(len(use), int)
    short = np.zeros(len(use), int)
    if "daily" in kinds:
        long_ += use["dl"].values
        short += use["ds"].values
    if "intraday" in kinds:
        long_ += use["il"].values
        short += use["is"].values
    use["n"] = long_ + short
    use["dir"] = np.where(long_ > 0, "LONG", "SHORT")
    use = use[(use["n"] > 0) & ~((long_ > 0) & (short > 0))]   # skip if the signals disagree
    if not trader.ALLOW_SHORTS:
        use = use[use["dir"] == "LONG"]
    use["kind"] = np.where((use["dl"] | use["ds"]) & ("daily" in kinds), "daily", "15-min")
    use = use.sort_values(["slot", "n", "sym"], ascending=[True, False, True])
    by_slot = {s: g for s, g in use.groupby("slot", sort=False)}

    Cf = pd.DataFrame(C).ffill().values
    day = slots.tz_localize(None).normalize()
    last_of_day = np.r_[day[1:] != day[:-1], True]
    cash, pos, trades, curve, traded_on = float(START_EQUITY), {}, [], [], {}
    skipped = 0

    def close(col, px, s, why):
        nonlocal cash
        p = pos.pop(col)
        sign = 1 if p["dir"] == "LONG" else -1
        cash += sign * p["qty"] * px
        pnl = sign * p["qty"] * (px - p["fill"])
        trades.append({"symbol": syms[col], "direction": p["dir"], "signal": p["kind"],
                       "entry_time": slots[p["slot"]], "exit_time": slots[s] + pd.Timedelta(minutes=15),
                       "entry": round(p["fill"], 2), "stop": p["stop"], "target": p["target"],
                       "exit": round(px, 2), "qty": p["qty"], "result": why,
                       "pnl": round(pnl, 2), "R": round(pnl / p["risk"], 2),
                       "days_held": (day[s] - day[p["slot"]]).days})

    for s in range(len(slots)):
        if s in by_slot and len(pos) <= trader.MAX_OPEN_POSITIONS:
            px = Cf[s - 1] if s else Cf[0]
            value = sum((1 if p["dir"] == "LONG" else -1) * p["qty"] * px[c] for c, p in pos.items())
            gross = sum(p["qty"] * px[c] for c, p in pos.items())
            equity = cash + value
            bp = max(0.0, MARGIN * equity - gross)
            placed, free = 0, trader.MAX_OPEN_POSITIONS - len(pos)
            for r in by_slot[s].itertuples():
                col = r.col
                if col in pos or traded_on.get(col) == day[s]:
                    continue
                if placed >= trader.MAX_NEW_PER_RUN or free <= 0:
                    skipped += 1
                    continue
                price = O[s, col]
                if not np.isfinite(price):
                    continue
                plan = trader.plan_trade(r.dir, price, r.atr, equity, bp)
                if not plan:
                    continue
                sign = 1 if r.dir == "LONG" else -1
                fill = price * (1 + sign * SLIPPAGE)
                cash -= sign * plan["qty"] * fill
                pos[col] = {"dir": r.dir, "kind": r.kind, "slot": s, "fill": fill, **plan}
                traded_on[col] = day[s]
                bp -= plan["qty"] * price
                placed += 1
                free -= 1
        # stops and targets during this 15-minute candle
        for col in list(pos):
            p = pos[col]
            o, hh, ll = O[s, col], H[s, col], L[s, col]
            if not np.isfinite(o):
                continue
            new = p["slot"] == s                       # entered at this candle's open
            if p["dir"] == "LONG":
                if not new and o <= p["stop"]:
                    close(col, o * (1 - SLIPPAGE), s, "stop (gap)")
                elif not new and o >= p["target"]:
                    close(col, o, s, "target (gap)")
                elif ll <= p["stop"]:
                    close(col, p["stop"] * (1 - SLIPPAGE), s, "stop")
                elif hh >= p["target"]:
                    close(col, p["target"], s, "target")
            else:
                if not new and o >= p["stop"]:
                    close(col, o * (1 + SLIPPAGE), s, "stop (gap)")
                elif not new and o <= p["target"]:
                    close(col, o, s, "target (gap)")
                elif hh >= p["stop"]:
                    close(col, p["stop"] * (1 + SLIPPAGE), s, "stop")
                elif ll <= p["target"]:
                    close(col, p["target"], s, "target")
        if last_of_day[s]:
            value = sum((1 if p["dir"] == "LONG" else -1) * p["qty"] * Cf[s, c] for c, p in pos.items())
            curve.append((day[s], cash + value, len(pos)))

    still_open = len(pos)
    for col in list(pos):                              # value what is still open at the last price
        close(col, Cf[-1, col], len(slots) - 1, "still open")
    eq = pd.DataFrame(curve, columns=["date", "equity", "positions"]).set_index("date")
    cols = ["symbol", "direction", "signal", "entry_time", "exit_time", "entry", "stop", "target", "exit",
            "qty", "result", "pnl", "R", "days_held"]
    return pd.DataFrame(trades, columns=cols), eq, {"skipped": skipped, "still_open": still_open}


# ---------------------------------------------------------------- statistics
def streak(results):
    best = cur = 0
    for r in results:
        cur = cur + 1 if r < 0 else 0
        best = max(best, cur)
    return best


def stats(trades, eq):
    e = eq["equity"]
    years = max((e.index[-1] - e.index[0]).days / 365.25, 1e-9)
    dd = e / e.cummax() - 1
    done = trades[trades["result"] != "still open"] if len(trades) else trades
    wins, losses = done[done["pnl"] > 0], done[done["pnl"] <= 0]
    gross_loss = -losses["pnl"].sum()
    daily_ret = e.pct_change().dropna()
    return {
        "return": e.iat[-1] / START_EQUITY - 1,
        "cagr": (e.iat[-1] / START_EQUITY) ** (1 / years) - 1,
        "max_dd": dd.min(),
        "dd_date": dd.idxmin(),
        "sharpe": daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0.0,
        "trades": len(done),
        "per_month": len(done) / (years * 12),
        "win_rate": len(wins) / len(done) if len(done) else 0.0,
        "avg_win": wins["pnl"].mean() if len(wins) else 0.0,
        "avg_loss": losses["pnl"].mean() if len(losses) else 0.0,
        "avg_R": done["R"].mean() if len(done) else 0.0,
        "pf": wins["pnl"].sum() / gross_loss if gross_loss > 0 else float("inf"),
        "streak": streak(done["pnl"].values) if len(done) else 0,
        "days_held": done["days_held"].mean() if len(done) else 0.0,
        "avg_positions": eq["positions"].mean(),
        "final": e.iat[-1],
    }


def benchmark(D, start):
    c = D["Close"][D.index >= start]
    return c / c.iat[0] * START_EQUITY


# ---------------------------------------------------------------- report
def pct(x):
    return f"{x * 100:+.1f}%"


def summary_text(res, bench, start, end, n_stocks):
    eq0 = res["bot"]["equity"]["equity"]
    years = (eq0.index[-1] - eq0.index[0]).days / 365.25
    lines = ["📊 <b>Backtest: S&amp;P 100 strategy</b>",
             f"{start:%d %b %Y} → {end:%d %b %Y} ({years:.1f} years), {n_stocks} stocks, $100,000 start",
             f"Rules as the bot trades now: ⭐ STRONG signals, stop 2×ATR, target 2:1, risk "
             f"{trader.RISK_PER_TRADE:.0%}/trade, max {trader.MAX_OPEN_POSITIONS} positions", ""]
    for key, (name, _) in VARIANTS.items():
        s = res[key]["stats"]
        lines += [f"<b>{name}</b>",
                  f"Return {pct(s['return'])} ({pct(s['cagr'])}/yr), final ${s['final']:,.0f}",
                  f"Max drawdown {pct(s['max_dd'])} ({s['dd_date']:%b %Y})",
                  f"Trades {s['trades']} (~{s['per_month']:.1f}/month), win rate {s['win_rate']:.0%}",
                  f"Avg win ${s['avg_win']:,.0f}, avg loss ${s['avg_loss']:,.0f}, avg {s['avg_R']:+.2f}R per trade",
                  f"Profit factor {s['pf']:.2f}, worst losing streak {s['streak']}, "
                  f"avg hold {s['days_held']:.1f} days", ""]
    if bench is not None and len(bench) > 1:
        b = bench
        b_years = max((b.index[-1] - b.index[0]).days / 365.25, 1e-9)
        b_cagr = (b.iat[-1] / START_EQUITY) ** (1 / b_years) - 1
        lines += [f"<b>Buy &amp; hold {BENCHMARK}</b>",
                  f"Return {pct(b.iat[-1] / START_EQUITY - 1)} ({pct(b_cagr)}/yr), "
                  f"max drawdown {pct((b / b.cummax() - 1).min())}", ""]
    t = res["bot"]["trades"]
    if len(t):
        done = t[t["result"] != "still open"]
        lines.append("<b>Breakdown (as the bot trades)</b>")
        for col, label in (("direction", None), ("signal", None)):
            for k, g in done.groupby(col):
                lines.append(f"{k}: {len(g)} trades, win {(g['pnl'] > 0).mean():.0%}, "
                             f"avg {g['R'].mean():+.2f}R, total ${g['pnl'].sum():,.0f}")
        eq = res["bot"]["equity"]["equity"]
        yearly = eq.groupby(eq.index.year).last()
        prev = START_EQUITY
        parts = []
        for y, v in yearly.items():
            parts.append(f"{y} {pct(v / prev - 1)}")
            prev = v
        lines.append("By year: " + ", ".join(parts))
    lines += ["", "<i>Caveats: today's S&amp;P 100 members only (stocks that dropped out aren't tested, "
                  "which flatters results); no borrow fees on shorts; fills at the next 15-min open with "
                  "0.02% slippage. Past results don't guarantee future ones.</i>"]
    return "\n".join(lines)


def equity_chart(res, bench):
    plt.style.use("dark_background")
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    fig.patch.set_facecolor("black")
    colors = {"bot": "#26a69a", "daily": "#42a5f5", "intraday": "#ffb74d"}
    for key, (name, _) in VARIANTS.items():
        e = res[key]["equity"]["equity"]
        a1.plot(e.index, e.values, color=colors[key], lw=2 if key == "bot" else 1.2, label=name)
        a2.plot(e.index, (e / e.cummax() - 1).values * 100, color=colors[key], lw=1)
    if bench is not None:
        a1.plot(bench.index, bench.values, color="#9e9e9e", lw=1.2, ls="--", label=f"Buy & hold {BENCHMARK}")
    a1.axhline(START_EQUITY, color="#555555", lw=0.8)
    a1.set_ylabel("Account value ($)")
    a1.set_title("Backtest: S&P 100 strategy vs buy & hold", color="white")
    a1.legend(loc="upper left", fontsize=9, facecolor="black")
    a1.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v / 1000:,.0f}k"))
    a2.set_ylabel("Drawdown (%)")
    for a in (a1, a2):
        a.set_facecolor("black")
        a.grid(color="#222222")
    fig.tight_layout()
    path = os.path.join(OUT, "equity.png")
    fig.savefig(path, dpi=110, facecolor="black")
    plt.close(fig)
    return path


def telegram(text=None, photo=None, document=None, caption=""):
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    base = f"https://api.telegram.org/bot{token}"
    for attempt in range(3):
        try:
            if photo:
                with open(photo, "rb") as f:
                    r = requests.post(f"{base}/sendPhoto", data={"chat_id": chat, "caption": caption},
                                      files={"photo": f}, timeout=60)
            elif document:
                with open(document, "rb") as f:
                    r = requests.post(f"{base}/sendDocument", data={"chat_id": chat, "caption": caption},
                                      files={"document": f}, timeout=60)
            else:
                r = requests.post(f"{base}/sendMessage", timeout=30,
                                  data={"chat_id": chat, "text": text[:4096], "parse_mode": "HTML"})
            if r.status_code == 429:
                time.sleep(r.json().get("parameters", {}).get("retry_after", 5) + 1)
                continue
            if not r.ok:
                print("Telegram error:", r.status_code, r.text[:300])
            time.sleep(1.5)
            return
        except requests.RequestException as e:
            print(f"Telegram problem: {e}")
            time.sleep(5)


# ---------------------------------------------------------------- main
def run(daily, intraday, start, end):
    """Signals + simulation for all variants. daily/intraday: {symbol: DataFrame}."""
    syms = sorted(intraday)
    t0 = time.time()
    sig = []
    for col, sym in enumerate(syms):
        s = stock_signals(intraday[sym], daily[sym])
        s["sym"], s["col"] = sym, col
        sig.append(s)
    sig = pd.concat(sig, ignore_index=True)
    slots = pd.DatetimeIndex(sorted(set().union(*(intraday[s].index for s in syms))))
    keep = slots >= pd.Timestamp(start).tz_convert(ET)
    slots = slots[keep]
    sig = sig[sig["fill"] >= slots[0]].copy()
    sig["slot"] = slots.get_indexer(sig["fill"])
    sig = sig[sig["slot"] >= 0]
    print(f"Signals: {len(sig)} ({sig[['dl', 'ds']].any(axis=1).sum()} daily, "
          f"{sig[['il', 'is']].any(axis=1).sum()} 15-min) in {time.time() - t0:.0f}s", flush=True)

    def matrix(field):
        return np.column_stack([intraday[s][field].reindex(slots).values for s in syms])
    O, H, L, C = (matrix(f) for f in ("Open", "High", "Low", "Close"))
    res = {}
    for key, (name, kinds) in VARIANTS.items():
        trades, eq, info = simulate(sig, O, H, L, C, slots, syms, kinds)
        res[key] = {"trades": trades, "equity": eq, "info": info, "stats": stats(trades, eq)}
        print(f"{name}: {res[key]['stats']['trades']} trades, return {pct(res[key]['stats']['return'])}, "
              f"{info['skipped']} signals skipped (limits)", flush=True)
    return res, sig


def main():
    years = float(os.environ.get("YEARS", "5"))
    end = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20)
    start = end - dt.timedelta(days=round(365.25 * years))
    start = max(start, dt.datetime(2016, 3, 1, tzinfo=dt.timezone.utc))      # Alpaca history starts 2016
    os.makedirs(OUT, exist_ok=True)

    import scanner
    tickers = scanner.get_tickers()
    print(f"Backtest {start:%Y-%m-%d} → {end:%Y-%m-%d}, {len(tickers)} stocks", flush=True)
    daily, intraday = download(tickers + [BENCHMARK], start, end)
    bench_d = daily.pop(BENCHMARK, None)
    intraday.pop(BENCHMARK, None)
    print(f"Prices for {len(intraday)} stocks", flush=True)

    res, sig = run(daily, intraday, start, end)
    bench = benchmark(bench_d, pd.Timestamp(start.date())) if bench_d is not None else None

    text = summary_text(res, bench, start, end, len(intraday))
    print("\n" + html.unescape(text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")))
    chart = equity_chart(res, bench)
    t = res["bot"]["trades"].copy()
    for c in ("entry_time", "exit_time"):
        t[c] = pd.to_datetime(t[c]).dt.strftime("%Y-%m-%d %H:%M")
    csv = os.path.join(OUT, "trades.csv")
    t.to_csv(csv, index=False)
    with open(os.path.join(OUT, "summary.txt"), "w") as f:
        f.write(html.unescape(text))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("```\n" + html.unescape(text.replace("<b>", "").replace("</b>", "")
                                            .replace("<i>", "").replace("</i>", "")) + "\n```\n")
    telegram(text)
    telegram(photo=chart, caption="Equity curves and drawdowns")
    telegram(document=csv, caption="Every trade (as the bot trades). Open in Excel / Google Sheets.")


if __name__ == "__main__":
    main()
