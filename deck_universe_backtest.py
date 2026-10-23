"""
Version 6 of the deck pattern trader (deck_trader.py, EXIT "trail") on a whole universe of stocks:
the S&P 100 and the S&P 500, with the same daily-candle engine, compared with buying and holding SPY.

Rules (like the live trader):
    signal  a charted deck pattern (deck_patterns.py; pattern_scan ALERTS) breaks UP at a day's close
            (the detector sees the last 290 daily candles each day) -> buy at the next day's open
    stop    the deck's protective stop; a day opening below it sells at the open, a low below it at the stop
    exit    a close below the 20-day low (lowest low of the 20 days before) sells at that close
            (the live trader checks it in the last 20 minutes before the close)
    money   1% of equity at risk, max 10% of equity per stock, max 100% invested, max 25 positions,
            max 8 new trades a day (multi-bar patterns first, then short-term, then candlesticks, ties random)
Daily candles only (500 stocks of 15-minute candles would not fit in the time limit), so stops are judged
on daily lows. Each universe from 10 start dates a week apart; the report shows the middle run.

    UNIVERSES="sp100 sp500" YEARS=8 python deck_universe_backtest.py      (needs ALPACA keys)
Leverage test: LEVERAGES="1 1.5 2 3" (every money limit x L, borrowed money pays MARGIN_RATE, margin call
below 25% equity). Results: reports/backtest/universe-deck-v6[-lev-...]-<universes>-<years>y/
"""
import datetime as dt
import io
import os
import time
from concurrent.futures import ProcessPoolExecutor

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests

import deck_patterns as dp
import pattern_scan
import scanner

YEARS = float(os.environ.get("YEARS", "8"))
UNIVERSES = os.environ.get("UNIVERSES", "sp100 sp500").split()
# price source: "alpaca" (from 2016) or "yahoo" (decades back, split/dividend adjusted; for START=2000-01-01 etc.)
SOURCE = os.environ.get("SOURCE", "alpaca").strip() or "alpaca"
# leverage test: every money limit (risk per trade, max per stock, max invested) x L, borrowed money pays
# MARGIN_RATE a year, and the whole account is sold at the close if equity falls below 25% of the positions
LEVERAGES = [float(x) for x in os.environ.get("LEVERAGES", "1").split()]
# moving-average confirmation (judged on the breakout day's close): only buy if ...
FILTERS = os.environ.get("FILTERS", "none").split()
ENTRY_RULES = {"gap1atr": "skip if it opens more than 1 ATR above the breakout close",
               "gap2pct": "skip if it opens more than 2% above the breakout close",
               "risk2x": "skip if the risk at the open is more than 2x the planned risk",
               "limit05": "limit order at the breakout close +0.5%"}
# patterns that lost money in both halves of the 8-year S&P 100 test (2x, ma50): skipped by the "noweak" filter
WEAK = {"Double bottom", "Cup and handle", "Pipe bottom", "Hanging man"}
FILTER_NAMES = {"none": "no MA filter",
                "ma50": "close above its 50-day MA",
                "ma200": "close above its 200-day MA",
                "ma50_200": "50-day MA above the 200-day MA",
                "both": "close above the 50- and 200-day MA",
                "ma50_rising": "close above a rising 50-day MA (higher than 10 days ago)",
                "vol12": "breakout volume >= 1.2x its 50-day average",
                "vol15": "breakout volume >= 1.5x its 50-day average",
                "vol20": "breakout volume >= 2x its 50-day average",
                "dry": "quiet volume for 10 days, then an above-average breakout day",
                "noweak": "without the losing patterns (" + ", ".join(sorted(WEAK)) + ")",
                "mkt50": "no new buys when SPY closes below its 50-day MA",
                "mkt200": "no new buys when SPY closes below its 200-day MA",
                "rs126": "stock beat SPY over the last 6 months",
                "beta09": "beta vs SPY at least 0.9",
                "beta1": "beta vs SPY at least 1.0",
                "noidio": "skip volatile stocks that don't move with the market (ATR >= 2.7% of price and beta < 0.9)"}
# beta = Bloomberg-style adjusted beta: 0.67 x (weekly returns vs SPY, last 104 weeks, min 52) + 0.33,
# using completed weeks only (known at the breakout close)
# rules applied inside simulate (sizing / exits)
SIM_RULES = {"reg50": "leverage only while SPY is above its 50-day MA (else 1x, trimmed at the close)",
             "reg200": "leverage only while SPY is above its 200-day MA (else 1x, trimmed at the close)",
             "reg200h": "leverage only while SPY is above its 200-day MA, else 0.5x (trimmed at the close)",
             "dd15": "1x after a 15% drop from the account high (trimmed at the close), back to full on a new high",
             "time10": "sell at the close after 10 days if not up at least 0.5R",
             "pos15": "max 7.5% of equity per stock x leverage (15% at 2x) instead of 10%",
             "pos25": "max 12.5% of equity per stock x leverage (25% at 2x) instead of 10%",
             "pos8": "max 4% of equity per stock x leverage (8% at 2x: up to 25 positions)",
             "pos10": "max 5% of equity per stock x leverage (10% at 2x: up to 20 positions)",
             "pos12": "max 6% of equity per stock x leverage (12% at 2x: up to 16 positions)",
             "gapconf": "only buy if it opens at least 0.5% above the breakout close",
             "skipstop3": "skip if the stop is less than 3% below the entry",
             "minatr25": "skip if the stock's ATR is under 2.5% of its price",
             "minstop3": "stop at least 3% below the entry (deck stop moved down if tighter)",
             "minstop15atr": "stop at least 1.5 ATR below the entry (deck stop moved down if tighter)",
             "betrail": "stop follows the high at the starting distance until it reaches the entry (break-even)",
             "be1r": "stop moved to the entry once the price has been 1R above it",
             "be2r": "stop moved to the entry once the price has been 2R above it",
             "risk05": "sized by risk: 0.25% x leverage (0.5% at 2x) per trade, cap 15% x leverage per stock",
             "risk075": "sized by risk: 0.375% x leverage (0.75% at 2x) per trade, cap 15% x leverage per stock",
             "betasize": "position size x beta (beta clipped to 0.5-1.5): more money in high-beta stocks"}
# note (22 Oct 2026): with 1% risk and a 10%-per-stock cap, ~98% of trades are sized by the cap, not the risk
SPY_TREND = None                                        # set in main: SPY close, 50/200-day MA, 6-month return


def ma_ok(flt, tt, date):
    if "+" in flt:                                      # combined filters, e.g. ma50+vol15
        return all(ma_ok(f, tt, date) for f in flt.split("+"))
    if flt in ("none", "noweak") or flt in SIM_RULES:   # noweak: pattern name (main); SIM_RULES: simulate
        return True
    if flt in ("mkt50", "mkt200", "rs126"):
        try:
            m = SPY_TREND.loc[date]
        except KeyError:
            return False
        if flt == "rs126":
            try:
                return tt.loc[date].r126 > m.r126
            except KeyError:
                return False
        return m.c > (m.m50 if flt == "mkt50" else m.m200)
    if flt in ENTRY_RULES:                              # entry rules are applied at the open (simulate)
        return True
    if flt in ("beta09", "beta1", "noidio"):
        try:
            r = tt.loc[date]
        except KeyError:
            return False
        if not np.isfinite(r.beta):
            return flt == "noidio"                      # unknown beta: beta floors skip, noidio keeps
        if flt == "noidio":
            return not (r.atrp >= 0.027 and r.beta < 0.9)
        return r.beta >= (0.9 if flt == "beta09" else 1.0)
    if flt.startswith("vol") or flt == "dry":
        try:
            r = tt.loc[date]
        except KeyError:
            return False
        if not (r.v50 > 0):
            return False
        if flt == "dry":                                # quiet 10 days before, then above-average breakout day
            return r.v10 < r.v50 and r.v > r.v50
        return r.v >= int(flt[3:]) / 10 * r.v50        # vol15 = breakout volume >= 1.5 x the 50-day average
    try:
        r = tt.loc[date]
    except KeyError:
        return False
    if flt == "ma50":
        return r.c > r.m50
    if flt == "ma200":
        return r.c > r.m200
    if flt == "ma50_200":
        return r.m50 > r.m200
    if flt == "both":
        return r.c > r.m50 and r.c > r.m200
    if flt == "ma50_rising":
        return r.c > r.m50 and r.m50 > r.m50_10
    raise ValueError(flt)
MARGIN_RATE = float(os.environ.get("MARGIN_RATE", "0.065"))
MAINTENANCE = 0.25
RUNS, STEP = 10, 5
WINDOW, TRAIL = 290, 20
RISK, MAX_POS_PCT, MAX_GROSS, MAX_POS, MAX_NEW = 0.01, 0.10, 1.0, 25, 8
SLIP = 0.0002
START_EQ = 100_000.0
FAM = {"multi": 0, "short": 1, "candle": 2}
SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def adj_beta(close, spy_close):
    """Adjusted weekly beta vs SPY per day, from completed weeks only (0.67 x raw + 0.33)."""
    px = pd.concat([close, spy_close], axis=1, keys=["s", "m"], sort=True).dropna()
    wk = px.groupby(px.index.to_period("W-FRI")).tail(1)          # last trading day of each week
    r = wk.pct_change()
    raw = r["s"].rolling(104, min_periods=52).cov(r["m"]) / r["m"].rolling(104, min_periods=52).var()
    # a week's beta is known after its last day's close: same-day lookup is fine (signal at that close)
    return (0.67 * raw + 0.33).reindex(close.index, method="ffill")


def sp500():
    html = requests.get(SP500_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0 (sp100-alert-bot)"}).text
    for t in pd.read_html(io.StringIO(html)):
        if "Symbol" in t.columns and len(t) > 400:
            return [str(x).strip().replace(".", "-") for x in t["Symbol"]]
    raise SystemExit("Couldn't read the S&P 500 list from Wikipedia")


def _signals(args):
    """Up breakouts of one stock: [(date, stop, family rank, pattern)] - each pattern once."""
    sym, d = args
    d = dp.add_atr(d)
    out, seen = [], set()
    for i in range(150, len(d) - 1):
        w = d.iloc[max(0, i - WINDOW + 1):i + 1]
        try:
            pats = dp.detect(w, families=("multi", "short", "candle"))
        except Exception:
            continue
        for p in pats:
            b = p.get("brk")
            if (p["stage"] != "breakout" or not b or b["bar"] != len(w) - 1 or b["dir"] != "LONG"
                    or pattern_scan.mode_of(p) != "chart"):
                continue
            key = (p["name"], w.index[p["start"]])
            if key in seen:
                continue
            seen.add(key)
            out.append((d.index[i], float(b["stop"]), FAM.get(p["family"], 1), p["name"].split(" (")[0],
                        float(d["Close"].iat[i]), float(d["ATR"].iat[i])))
    return sym, out


def simulate(days, O, H, L, C, low20, sigs, syms, d0, seed, lev=1.0, entry=None, rules=(), spy_up=None):
    """rules: SIM_RULES names; spy_up: {"reg50"/"reg200": bool array per day, SPY above that MA at the close}."""
    rules = set(rules)
    pos_pct = next((int(r[3:]) / 200 for r in rules if r.startswith("pos")), MAX_POS_PCT)   # posN = N% at 2x
    risk_pct = RISK
    if "risk05" in rules or "risk075" in rules:          # true risk sizing: the per-stock cap rarely binds
        risk_pct, pos_pct = (0.0025 if "risk05" in rules else 0.00375), 0.15
    peak, dd_off = START_EQ, False
    """sigs: {day index: [(col, stop, fam, name)]} signals at that day's close -> buy next open."""
    rng = np.random.default_rng(seed)
    cash, pos, trades = START_EQ, {}, []
    curve = np.full(len(days), np.nan)
    info = {"interest": 0.0, "margin_calls": 0}
    for t in range(d0, len(days)):
        if cash < 0 and t > d0:                              # interest on borrowed money, per calendar day
            fee = -cash * MARGIN_RATE * (days[t] - days[t - 1]).days / 365
            cash -= fee
            info["interest"] += fee
        # 1. exits during day t (positions opened before today, or at today's open)
        for col in list(pos):
            p = pos[col]
            o, l, c = O[t, col], L[t, col], C[t, col]
            if not np.isfinite(o):
                continue
            x, why = None, None
            if o <= p["stop"] and t > p["t"]:
                x, why = o * (1 - SLIP), "stop (gap)"
            elif l <= p["stop"]:
                x, why = p["stop"] * (1 - SLIP), ("stop" if p["stop"] <= p.get("stop0", p["stop"]) else
                                                   "break-even stop" if p["stop"] >= p["fill"] else "raised stop")
            elif np.isfinite(low20[t, col]) and c < low20[t, col]:
                x, why = c * (1 - SLIP), "trend exit"
            elif "time10" in rules and t - p["t"] >= 10 and c < p["fill"] + 0.5 * p["risk"]:
                x, why = c * (1 - SLIP), "time stop"
            if x is not None:
                cash += p["qty"] * x
                trades.append({"symbol": syms[col], "pattern": p["name"], "entry_date": days[p["t"]],
                               "exit_date": days[t], "entry": p["fill"], "stop": p["stop"], "exit": x, "result": why,
                               "pnl": p["qty"] * (x - p["fill"]), "R": (x - p["fill"]) / p["risk"],
                               "days": (days[t] - days[p["t"]]).days})
                del pos[col]
            elif np.isfinite(H[t, col]) and ("betrail" in rules or "be1r" in rules or "be2r" in rules):
                p.setdefault("stop0", p["stop"])            # raise the stop for tomorrow, never above the entry
                if "betrail" in rules:
                    new = min(p["fill"], H[t, col] - p["risk"])
                elif H[t, col] >= p["fill"] + (1 if "be1r" in rules else 2) * p["risk"]:
                    new = p["fill"]
                else:
                    new = p["stop"]
                p["stop"] = max(p["stop"], new)
        value = sum(p["qty"] * (C[t, c] if np.isfinite(C[t, c]) else p["fill"]) for c, p in pos.items())
        if pos and cash + value < MAINTENANCE * value:      # margin call: everything sold at the close
            info["margin_calls"] += 1
            for col in list(pos):
                p = pos.pop(col)
                x = (C[t, col] if np.isfinite(C[t, col]) else p["fill"]) * (1 - SLIP)
                cash += p["qty"] * x
                trades.append({"symbol": syms[col], "pattern": p["name"], "entry_date": days[p["t"]],
                               "exit_date": days[t], "entry": p["fill"], "stop": p["stop"], "exit": x,
                               "result": "margin call", "pnl": p["qty"] * (x - p["fill"]),
                               "R": (x - p["fill"]) / p["risk"], "days": (days[t] - days[p["t"]]).days})
            value = 0.0
        curve[t] = cash + value
        if curve[t] <= 0:                                   # account wiped out
            curve[t:] = 0.0
            break
        lev_t = lev                                         # leverage allowed for today (regime / drawdown rules)
        for rg in ("reg50", "reg200", "reg200h"):
            if rg in rules and spy_up is not None and not spy_up[rg[:6]][t]:
                lev_t = min(lev_t, 0.5 if rg == "reg200h" else 1.0)
        if "dd15" in rules:
            peak = max(peak, curve[t])
            if curve[t] < 0.85 * peak:
                dd_off = True
            elif curve[t] >= peak:
                dd_off = False
            if dd_off:
                lev_t = min(lev_t, 1.0)
        cap = MAX_GROSS * lev_t * curve[t]
        if lev_t < lev and value > cap and value > 0:       # de-lever: sell part of every position at the close
            frac = 1 - cap / value
            for col in list(pos):
                p = pos[col]
                px_ = C[t, col] if np.isfinite(C[t, col]) else p["fill"]
                q = np.ceil(p["qty"] * frac)
                if q <= 0:
                    continue
                q = min(q, p["qty"])
                x = px_ * (1 - SLIP)
                cash += q * x
                trades.append({"symbol": syms[col], "pattern": p["name"], "entry_date": days[p["t"]],
                               "exit_date": days[t], "entry": p["fill"], "stop": p["stop"], "exit": x,
                               "result": "de-lever", "pnl": q * (x - p["fill"]), "R": (x - p["fill"]) / p["risk"],
                               "days": (days[t] - days[p["t"]]).days})
                p["qty"] -= q
                if p["qty"] <= 0:
                    del pos[col]
            value = sum(p["qty"] * (C[t, c] if np.isfinite(C[t, c]) else p["fill"]) for c, p in pos.items())
            curve[t] = cash + value
        # 2. yesterday's breakouts are bought at today's open - handled at the start of the next day:
        #    here we queue today's signals for tomorrow
        if t + 1 >= len(days):
            continue
        todays = sigs.get(t, [])
        if not todays:
            continue
        equity = curve[t]
        gross = value
        order = sorted(todays, key=lambda s: (s[2], rng.random()))
        placed = 0
        for col, stop, fam, name, bclose, batr, sbeta in order:
            if col in pos or len(pos) >= MAX_POS or placed >= MAX_NEW:
                continue
            o = O[t + 1, col]
            if not np.isfinite(o):
                continue
            fill = o * (1 + SLIP)
            # no-chase entry rules (ENTRY_RULES): don't buy when the open is already too high
            if "gapconf" in rules and o < bclose * 1.005:
                continue
            if "minatr25" in rules and batr < 0.025 * bclose:
                continue
            if "skipstop3" in rules and fill - stop < 0.03 * fill:
                continue
            if "minstop3" in rules:
                stop = min(stop, fill * 0.97)
            if "minstop15atr" in rules:
                stop = min(stop, fill - 1.5 * batr)
            if entry == "gap1atr" and o > bclose + batr:
                continue
            if entry == "gap2pct" and o > bclose * 1.02:
                continue
            if entry == "risk2x" and (o - stop) > 2 * (bclose - stop):
                continue
            if entry == "limit05":                           # limit order at the breakout close + 0.5%
                lim = bclose * 1.005
                if o > lim:
                    if not (L[t + 1, col] <= lim):
                        continue
                    fill = lim                               # filled when the price came back down
            risk = fill - stop
            if risk <= 0:
                continue
            room = MAX_GROSS * lev_t * equity - gross
            bm = float(np.clip(sbeta, 0.5, 1.5)) if "betasize" in rules and np.isfinite(sbeta) else 1.0
            qty = min(equity * risk_pct * lev_t * bm / risk, equity * pos_pct * lev_t * bm / fill, room * 0.95 / fill)
            qty = np.floor(qty)
            if qty < 1:
                continue
            cash -= qty * fill
            gross += qty * fill
            pos[col] = {"t": t + 1, "fill": fill, "stop": stop, "qty": qty, "risk": risk, "name": name}
            placed += 1
    for col, p in pos.items():                                 # still open at the end
        x = C[len(days) - 1, col]
        trades.append({"symbol": syms[col], "pattern": p["name"], "entry_date": days[p["t"]], "exit_date": days[-1],
                       "entry": p["fill"], "stop": p["stop"], "exit": x, "result": "still open",
                       "pnl": p["qty"] * (x - p["fill"]), "R": (x - p["fill"]) / p["risk"],
                       "days": (days[-1] - days[p["t"]]).days})
    eq = pd.Series(curve, index=days).iloc[d0:].ffill()
    cols = ["symbol", "pattern", "entry_date", "exit_date", "entry", "stop", "exit", "result", "pnl", "R", "days"]
    return pd.DataFrame(trades, columns=cols), eq, info


def summarize(eq, tr):
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iat[-1] / START_EQ) ** (1 / yrs) - 1
    done = tr[~tr.result.isin(["still open", "de-lever"])]   # partial de-lever sales aren't whole trades
    gl = -done.loc[done.pnl < 0, "pnl"].sum()
    return {"cagr": cagr, "dd": (eq / eq.cummax() - 1).min(), "final": eq.iat[-1], "trades": len(done),
            "win": (done.pnl > 0).mean() if len(done) else 0, "avgR": done.R.mean() if len(done) else 0,
            "pf": done.loc[done.pnl > 0, "pnl"].sum() / gl if gl else np.inf,
            "hold": done.days.mean() if len(done) else 0}


def main():
    t00 = time.time()
    end = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20)
    start = end - dt.timedelta(days=round(365.25 * YEARS))
    if os.environ.get("START"):                        # a fixed start date, e.g. 2020-06-01
        start = dt.datetime.fromisoformat(os.environ["START"]).replace(tzinfo=dt.timezone.utc)
    lists = {"sp100": scanner.get_tickers(), "sp500": None}
    if "sp500" in UNIVERSES:
        lists["sp500"] = sp500()
    every = sorted(set(sum((lists[u] for u in UNIVERSES), [])) | {"SPY"})
    print(f"{len(every)} symbols, {start:%Y-%m-%d} → {end:%Y-%m-%d}", flush=True)
    first_day = pd.Timestamp((start - dt.timedelta(days=450)).date())
    if SOURCE == "yahoo":
        raw = scanner.download(every, "max", "1d")
    else:
        raw = scanner.alpaca_bars(every, "1Day", start - dt.timedelta(days=450), end, chunk=50, workers=6)
    daily = {}
    for s, d in raw.items():
        d = d.copy()
        if d.index.tz is not None:
            d.index = d.index.tz_convert(scanner.ET).tz_localize(None)
        d.index = pd.DatetimeIndex(d.index).normalize()
        d = d[d.index >= first_day]
        if len(d):
            daily[s] = d[["Open", "High", "Low", "Close", "Volume"]]
    print(f"Prices for {len(daily)} symbols ({time.time() - t00:.0f}s)", flush=True)
    spy = daily.pop("SPY")
    global SPY_TREND
    sc = spy["Close"]
    SPY_TREND = pd.DataFrame({"c": sc, "m50": sc.rolling(50).mean(), "m200": sc.rolling(200).mean(),
                              "r126": sc / sc.shift(126) - 1})

    # breakouts of every stock (the slow part), in parallel
    t0 = time.time()
    found = {}
    jobs = [(s, daily[s]) for s in sorted(daily) if len(daily[s]) > 300]
    with ProcessPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        for n, (s, out) in enumerate(pool.map(_signals, jobs, chunksize=4), 1):
            found[s] = out
            if n % 50 == 0:
                print(f"Patterns {n}/{len(jobs)} ({time.time() - t0:.0f}s)", flush=True)
    print(f"Breakouts found in {time.time() - t0:.0f}s", flush=True)

    days = spy.index[spy.index >= pd.Timestamp(start.date()) - pd.Timedelta(days=40)]
    st_ = SPY_TREND.reindex(days)
    spy_up = {"reg50": (st_.c > st_.m50).values, "reg200": (st_.c > st_.m200).values}
    lev_tag = "" if LEVERAGES == [1.0] else "-lev-" + "-".join(f"{x:g}" for x in LEVERAGES)
    if FILTERS != ["none"]:
        lev_tag += "-f-" + "-".join(f.replace("+", "_") for f in FILTERS)
    if len(lev_tag) > 90:                               # many variants: keep the folder name short
        lev_tag = lev_tag[:lev_tag.find("-f-")] + f"-f-{len(FILTERS)}variants-{dt.date.today():%Y%m%d}"
    if SOURCE != "alpaca":
        lev_tag += f"-{SOURCE}"
    out_dir = os.path.join("reports", "backtest", f"universe-deck-v6{lev_tag}-{'-'.join(UNIVERSES)}-"
                           + (f"from{os.environ['START']}" if os.environ.get("START") else f"{YEARS:g}y"))
    os.makedirs(out_dir, exist_ok=True)
    first = int(np.searchsorted(days.values, np.datetime64(pd.Timestamp(start.date()))))
    lines = ["📊 Deck pattern trader version 6 (up breakouts, deck stop, sell on a close below the 20-day low) "
             "on whole universes, daily candles",
             f"{days[first]:%d %b %Y} → {days[-1]:%d %b %Y}, $100,000, 1% risk, max 10%/stock, 25 positions, 8 new/day, "
             f"{'no leverage' if LEVERAGES == [1.0] else f'leverage as named (limits x L, borrowed money {MARGIN_RATE:.1%}/yr, margin call below 25%)'}, 0.02% slippage. 10 start dates a week apart: middle run, range in brackets.", ""]
    curves, all_tr, rows = {}, [], []
    spy_c = spy["Close"].reindex(days).ffill()
    for u in UNIVERSES:
        syms = [s for s in lists[u] if s in daily and s in found]
        O, H, L, C = (np.column_stack([daily[s][k].reindex(days).values for s in syms]) for k in
                      ("Open", "High", "Low", "Close"))
        low20 = np.column_stack([daily[s]["Low"].rolling(TRAIL).min().shift(1).reindex(days).values for s in syms])
        pos_of = {d: k for k, d in enumerate(days)}
        need_beta = any(x in f.split("+") for f in FILTERS for x in ("beta09", "beta1", "noidio", "betasize"))
        trend = {}                                      # each stock's moving averages (closes up to that day)
        for s_ in syms:
            c_ = daily[s_]["Close"]
            m50, m200 = c_.rolling(50).mean(), c_.rolling(200).mean()
            v_ = daily[s_]["Volume"]
            v50 = v_.rolling(50).mean().shift(1)          # average of the 50 days BEFORE that day
            trend[s_] = pd.DataFrame({"c": c_, "m50": m50, "m200": m200, "m50_10": m50.shift(10),
                                      "r126": c_ / c_.shift(126) - 1,
                                      "v": v_, "v50": v50, "v10": v_.rolling(10).mean().shift(1)})
            if need_beta:
                atr_ = dp.add_atr(daily[s_].copy())["ATR"]
                trend[s_]["beta"] = adj_beta(c_, spy["Close"])
                trend[s_]["atrp"] = atr_ / c_
        sigs_by = {}
        beta_of = {s_: trend[s_]["beta"] for s_ in syms} if need_beta else {}
        for flt in FILTERS:
            sigs, n_sig = {}, 0
            for col, s_ in enumerate(syms):
                tt = trend[s_]
                for date, stop, fam, name, bclose, batr in found[s_]:
                    k = pos_of.get(pd.Timestamp(date))
                    if k is None or k < first or not ma_ok(flt, tt, pd.Timestamp(date)):
                        continue
                    if "noweak" in flt.split("+") and name in WEAK:
                        continue
                    sb = float(beta_of[s_].get(pd.Timestamp(date), np.nan)) if need_beta else np.nan
                    sigs.setdefault(k, []).append((col, stop, fam, name, bclose, batr, sb))
                    n_sig += 1
            sigs_by[flt] = (sigs, n_sig)
        for flt, (sigs, n_sig) in sigs_by.items():
            for lev in LEVERAGES:
                tag = u.upper() + (f" {lev:g}x leverage" if len(LEVERAGES) > 1 or lev != 1 else "") + \
                ("" if FILTERS == ["none"] else " · " + " + ".join({**FILTER_NAMES, **ENTRY_RULES, **SIM_RULES}[f]
                                                                    for f in flt.split("+")))
                res = []
                for r in range(RUNS):
                    d0 = first + r * STEP
                    ent = next((f for f in flt.split("+") if f in ENTRY_RULES), None)
                    tr, eq, info = simulate(days, O, H, L, C, low20, sigs, syms, d0, seed=r, lev=lev, entry=ent,
                                            rules=[f for f in flt.split("+") if f in SIM_RULES], spy_up=spy_up)
                    st = summarize(eq, tr)
                    st.update(info)
                    b = spy_c.iloc[d0:]
                    b = b / b.iat[0] * START_EQ
                    byrs = (b.index[-1] - b.index[0]).days / 365.25
                    st["spy_cagr"] = (b.iat[-1] / START_EQ) ** (1 / byrs) - 1
                    st["beat"] = st["final"] >= b.iat[-1]
                    res.append((st, tr, eq))
                    print(f"{tag} run {r + 1}: {st['cagr'] * 100:+.1f}%/yr, {st['trades']} trades, "
                          f"drop {st['dd'] * 100:+.1f}%", flush=True)
                cg = [x[0]["cagr"] for x in res]
                mid = int(np.argsort(cg)[len(cg) // 2])
                st, tr, eq = res[mid]
                by_year = eq.groupby(eq.index.year).last()
                by_year = by_year / np.r_[START_EQ, by_year.values[:-1]] - 1
                calls = sum(x[0]["margin_calls"] for x in res)
                lines += [f"<b>{tag} ({len(syms)} stocks, {n_sig} up breakouts)</b>",
                          f"{st['cagr'] * 100:+.1f}%/yr ({min(cg) * 100:+.1f}% to {max(cg) * 100:+.1f}%), beat SPY in "
                          f"{sum(x[0]['beat'] for x in res)}/{RUNS} runs · max drop {st['dd'] * 100:+.1f}% "
                          f"(worst {min(x[0]['dd'] for x in res) * 100:+.1f}%) · final ${st['final']:,.0f}",
                          f"~{st['trades']} trades, win {st['win']:.0%}, avg {st['avgR']:+.2f}R, profit factor {st['pf']:.2f}, "
                          f"hold {st['hold']:.0f} days" + (f", interest paid ${st['interest']:,.0f}" if st["interest"] else "")
                          + (f", margin calls in all runs: {calls}" if lev > 1 else ""),
                          "by year: " + "  ".join(f"{y}: {v * 100:+.0f}%" for y, v in by_year.items()), ""]
                rows.append({"universe": u, "filter": flt, "leverage": lev, "stocks": len(syms), "breakouts": n_sig,
                             "cagr_min": min(cg), "cagr_max": max(cg), "dd_worst": min(x[0]["dd"] for x in res),
                             "beat": sum(x[0]["beat"] for x in res), "calls_all_runs": calls,
                             **{k: v for k, v in st.items()}})
                curves[f"{tag}"] = eq
                all_tr.append(tr.assign(universe=u, filter=flt, leverage=lev))
                if len(LEVERAGES) == 1 and len(FILTERS) == 1:   # per pattern (middle run)
                    g = tr[tr.result != "still open"].groupby("pattern")["R"].agg(["size", "mean"]).sort_values(
                        "mean", ascending=False)
                    lines.append(f"  per pattern ({u}, middle run): " + ", ".join(
                        f"{p} {int(r['size'])}× {r['mean']:+.2f}R" for p, r in g.iterrows()))
                    lines.append("")
    b = spy_c.iloc[first:]
    b = b / b.iat[0] * START_EQ
    byrs = (b.index[-1] - b.index[0]).days / 365.25
    sy = b.groupby(b.index.year).last()
    sy = sy / np.r_[START_EQ, sy.values[:-1]] - 1
    lines += [f"<b>Buy & hold SPY</b>: {((b.iat[-1] / START_EQ) ** (1 / byrs) - 1) * 100:+.1f}%/yr, max drop "
              f"{(b / b.cummax() - 1).min() * 100:+.1f}%, final ${b.iat[-1]:,.0f}",
              "by year: " + "  ".join(f"{y}: {v * 100:+.0f}%" for y, v in sy.items()), "",
              "<i>Today's index members only (stocks that left the index are missing, which flatters results - more "
              "so for the S&P 500); daily candles: stops judged on daily lows, 20-day-low exit at the close. "
              "Past results don't guarantee future ones.</i>"]
    text = "\n".join(lines)
    plain = text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
    print("\n" + plain)
    with open(os.path.join(out_dir, "summary.txt"), "w") as f:
        f.write(plain + "\n")
    pd.concat(all_tr).to_csv(os.path.join(out_dir, "trades.csv"), index=False)
    curves["Buy & hold SPY"] = b
    pd.DataFrame(curves).round(0).to_csv(os.path.join(out_dir, "equity.csv"), index_label="date")
    pd.DataFrame(rows).to_csv(os.path.join(out_dir, "results.csv"), index=False)

    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(12, 7.5), dpi=110, sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    fig.patch.set_facecolor("black")
    palette = ["#42a5f5", "#ffd54f", "#66bb6a", "#ef5350", "#ab47bc", "#26c6da", "#ffa726", "#ec407a"]
    cols = {n: palette[k % len(palette)] for k, n in enumerate(c for c in curves if not c.startswith("Buy"))}
    cols["Buy & hold SPY"] = "#bbbbbb"
    for a_ in (ax, ax2):
        a_.set_facecolor("black"); a_.grid(color="#222"); a_.tick_params(colors="#bbbbbb")
        for s in a_.spines.values():
            s.set_color("#444")
    for name, s in curves.items():
        ls = "--" if name.startswith("Buy") else "-"
        ax.plot(s.index, s.values, color=cols.get(name, "#fff"), lw=1.6, ls=ls, label=name)
        ax2.plot(s.index, (s / s.cummax() - 1) * 100, color=cols.get(name, "#fff"), lw=1.0, ls=ls)
    ax.set_title("Deck patterns version 6 vs SPY" + (" (leverage)" if lev_tag else ""), color="white")
    if lev_tag:
        ax.set_yscale("log")
    ax.set_ylabel("Account value ($)", color="#bbbbbb"); ax2.set_ylabel("Drawdown (%)", color="#bbbbbb")
    leg = ax.legend(facecolor="black", edgecolor="#444")
    for x in leg.get_texts():
        x.set_color("#ddd")
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "chart.png"), facecolor="black")
    print(f"Done in {time.time() - t00:.0f}s, saved in {out_dir}")


if __name__ == "__main__":
    main()
