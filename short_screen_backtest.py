"""
Short strategy built from a "weak stocks" screen (like Finviz losers / new lows), rebuilt in code so it can
be tested on past dates. Daily candles, S&P 500 + S&P 400 mid caps (wider than the S&P 100).

Screen (judged at each day's close) - the "weak list":
    liquid      50-day average dollar volume >= MIN_DVOL ($20M) and price >= $10
                (stands in for "market cap above ~$2B, easy to borrow": no historical market caps here)
    downtrend   close below the 200-day MA and the 50-day MA below the 200-day MA
    near lows   close within 10% of the 52-week low
    weakest     6-month return below SPY's AND in the bottom WEAK_PCT (10%) of the liquid stocks that day
Entry triggers (a stock on the weak list, at that day's close) -> short at the next day's open:
    break       close below the lowest low of the 20 days before (breakdown)
    rally       failed rally: high reaches a falling 20-day MA, close back below it and below the open
Market filter (on the signal day's close):
    none        always allowed
    mkt200      only when SPY closes below its 200-day MA (stands in for the manual bear switch)
    mkt50       only when SPY closes below its 50-day MA
Exits:
    stop        entry + 2 ATR (a day opening above it covers at the open, a high above it at the stop)
    trail       a close above the highest high of the 10 days before covers at that close
    time        cover at the close after 15 trading days
Money: 10% of equity per short, max 5 shorts (50% short), most-negative 6-month return first.
Costs: 0.02% slippage per side, borrow fee 0.5%/yr on the short value. Prices are dividend-adjusted, so
dividends owed while short are already in the P&L.

    YEARS=8 ENTRIES="break rally" MARKETS="none mkt200 mkt50" STOPS="2 3 4 none" NEARS="10 off" python short_screen_backtest.py   (ALPACA keys)
    SOURCE=yahoo START=2005-01-01 ...   (decades back, includes 2008)
Results: reports/backtest/short-screen-<universes>-<years>y/
"""
import datetime as dt
import io
import os
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests

YEARS = float(os.environ.get("YEARS", "8"))
UNIVERSES = os.environ.get("UNIVERSES", "sp500 sp400").split()
SOURCE = os.environ.get("SOURCE", "alpaca").strip() or "alpaca"
ENTRIES = os.environ.get("ENTRIES", "break rally").split()
MARKETS = os.environ.get("MARKETS", "none mkt200 mkt50").split()
# stop distances to compare, in ATR ("none" = no stop, only the trailing and time exits)
STOPS = os.environ.get("STOPS", os.environ.get("ATR_STOP", "2")).split()
# "near 52-week low" rule: max % above the low ("off" = rule not used)
NEARS = os.environ.get("NEARS", "10").split()

MIN_DVOL = float(os.environ.get("MIN_DVOL", "20e6"))
MIN_PRICE = 10.0
WEAK_PCT = float(os.environ.get("WEAK_PCT", "0.10"))
POS_PCT = float(os.environ.get("POS_PCT", "0.10"))
MAX_POS = int(os.environ.get("MAX_POS", "5"))
TRAIL = int(os.environ.get("TRAIL", "10"))
TIME_STOP = int(os.environ.get("TIME_STOP", "15"))
SLIP = 0.0002
BORROW = float(os.environ.get("BORROW", "0.005"))
START_EQ = 100_000.0

WIKI = {"sp500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "sp400": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
        "sp600": "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies"}
ENTRY_NAMES = {"break": "breakdown below the 20-day low", "rally": "failed rally at a falling 20-day MA"}
MARKET_NAMES = {"none": "no market filter", "mkt200": "only when SPY < 200-day MA",
                "mkt50": "only when SPY < 50-day MA"}


def index_members(u):
    html = requests.get(WIKI[u], timeout=30, headers={"User-Agent": "Mozilla/5.0 (sp100-alert-bot)"}).text
    for t in pd.read_html(io.StringIO(html)):
        col = next((c for c in t.columns if str(c).strip() in ("Symbol", "Ticker symbol", "Ticker")), None)
        if col is not None and len(t) > 300:
            return [str(x).strip().replace(".", "-") for x in t[col]]
    raise SystemExit(f"Couldn't read the {u} list from Wikipedia")


def load_prices(symbols, start, end):
    import scanner                                       # only needed for real data
    if SOURCE == "yahoo":
        raw = scanner.download(symbols, "max", "1d")
    else:
        raw = scanner.alpaca_bars(symbols, "1Day", start - dt.timedelta(days=450), end, chunk=50, workers=6)
    first_day = pd.Timestamp((start - dt.timedelta(days=450)).date())
    daily = {}
    for s, d in raw.items():
        d = d.copy()
        if d.index.tz is not None:
            d.index = d.index.tz_convert(scanner.ET).tz_localize(None)
        d.index = pd.DatetimeIndex(d.index).normalize()
        d = d[~d.index.duplicated(keep="last")]
        d = d[d.index >= first_day]
        if len(d) > 260:
            daily[s] = d[["Open", "High", "Low", "Close", "Volume"]]
    return daily


def features(daily, days):
    """Day x stock arrays of everything the screen, triggers and exits need (all known at that day's close)."""
    syms = sorted(daily)
    f = {k: [] for k in ("O", "H", "L", "C", "atr", "low20", "high_tr", "ma20", "ma20_5", "ma50", "ma200",
                         "low252", "r126", "dvol")}
    for s in syms:
        d = daily[s]
        c, h, l = d["Close"], d["High"], d["Low"]
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        ma20 = c.rolling(20).mean()
        cols = {"O": d["Open"], "H": h, "L": l, "C": c, "atr": tr.rolling(14).mean(),
                "low20": l.rolling(20).min().shift(1), "high_tr": h.rolling(TRAIL).max().shift(1),
                "ma20": ma20, "ma20_5": ma20.shift(5), "ma50": c.rolling(50).mean(),
                "ma200": c.rolling(200).mean(), "low252": l.rolling(252, min_periods=200).min(),
                "r126": c / c.shift(126) - 1, "dvol": (c * d["Volume"]).rolling(50).mean()}
        for k, v in cols.items():
            f[k].append(v.reindex(days).values)
    return syms, {k: np.column_stack(v).astype(float) for k, v in f.items()}


def weak_list(F, spy_r126, near_low=0.10):
    """Boolean day x stock: on the weak list at that day's close."""
    with np.errstate(invalid="ignore"):
        liquid = (F["dvol"] >= MIN_DVOL) & (F["C"] >= MIN_PRICE)
        trend = (F["C"] < F["ma200"]) & (F["ma50"] < F["ma200"])
        near = np.ones_like(liquid) if near_low is None else F["C"] <= F["low252"] * (1 + near_low)
        r = np.where(liquid, F["r126"], np.nan)
        cut = np.nanquantile(np.where(np.isfinite(r).sum(1, keepdims=True) > 20, r, np.nan), WEAK_PCT, axis=1)
        weakest = (r <= cut[:, None]) & (r < spy_r126[:, None])
    return liquid & trend & near & weakest


def triggers(F, kind):
    with np.errstate(invalid="ignore"):
        if kind == "break":
            return F["C"] < F["low20"]
        if kind == "rally":
            return (F["ma20"] < F["ma20_5"]) & (F["H"] >= F["ma20"]) & (F["C"] < F["ma20"]) & (F["C"] < F["O"])
    raise SystemExit(f"Unknown entry {kind}")


def simulate(days, syms, F, signal, allowed, spy_above200, k0, atr_stop=2.0):
    """Short at the next open after a signal. Returns trades and the daily equity curve."""
    O, H, C, atr, high_tr, r126 = F["O"], F["H"], F["C"], F["atr"], F["high_tr"], F["r126"]
    realized, fees, pos, trades, eq = 0.0, 0.0, {}, [], []
    last_c = {}

    def cover(col, p, px, k, why):
        nonlocal realized
        px *= 1 + SLIP
        pnl = p["qty"] * (p["entry"] - px)
        realized += pnl
        trades.append({"symbol": syms[col], "entry_date": days[p["k"]].date(), "exit_date": days[k].date(),
                       "entry": round(p["entry"], 2), "exit": round(px, 2), "stop": round(p["stop"], 2) if np.isfinite(p["stop"]) else None,
                       "days": k - p["k"], "pnl": round(pnl, 2), "ret": (p["entry"] - px) / p["entry"],
                       "R": (p["entry"] - px) / (p["risk_stop"] - p["entry"]), "result": why,
                       "regime": "SPY>200" if p["bull"] else "SPY<200"})

    equity = START_EQ
    for k in range(k0, len(days)):
        # 1) new shorts at the open from yesterday's signals
        y = k - 1
        if allowed[y] and len(pos) < MAX_POS:
            cand = [c for c in np.flatnonzero(signal[y]) if c not in pos and np.isfinite(O[k, c])
                    and np.isfinite(atr[y, c]) and atr[y, c] > 0]
            cand.sort(key=lambda c: r126[y, c])          # weakest first
            for c in cand[:MAX_POS - len(pos)]:
                entry = O[k, c] * (1 - SLIP)
                # R is always measured against a 2-ATR risk, so versions with other stops compare fairly
                stop = O[k, c] + atr_stop * atr[y, c] if atr_stop else np.inf
                pos[c] = {"qty": POS_PCT * equity / entry, "entry": entry, "stop": stop, "k": k,
                          "risk_stop": O[k, c] + 2 * atr[y, c],
                          "bull": bool(spy_above200[y])}
        # 2) exits
        for c in list(pos):
            p = pos[c]
            o, h, cl = O[k, c], H[k, c], C[k, c]
            if not np.isfinite(cl):                      # no candle today (halt / delisted): keep it
                if k == len(days) - 1 and c in last_c:
                    cover(c, p, last_c[c], k, "still open"); del pos[c]
                continue
            last_c[c] = cl
            if o >= p["stop"]:
                cover(c, p, o, k, "stop (gap)")
            elif h >= p["stop"]:
                cover(c, p, p["stop"], k, "stop")
            elif np.isfinite(high_tr[k, c]) and cl > high_tr[k, c]:
                cover(c, p, cl, k, f"{TRAIL}-day high")
            elif k - p["k"] >= TIME_STOP:
                cover(c, p, cl, k, "time")
            elif k == len(days) - 1:
                cover(c, p, cl, k, "still open")
            else:
                continue
            del pos[c]
        # 3) borrow fee and mark to market
        open_pnl, short_val = 0.0, 0.0
        for c, p in pos.items():
            px = last_c.get(c, p["entry"])
            open_pnl += p["qty"] * (p["entry"] - px)
            short_val += p["qty"] * px
        fees += short_val * BORROW / 252
        equity = START_EQ + realized + open_pnl - fees
        eq.append(equity)
    return pd.DataFrame(trades), pd.Series(eq, index=days[k0:])


def stats(eq, tr):
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    done = tr[tr.result != "still open"] if len(tr) else tr
    win = (done.pnl > 0).mean() if len(done) else np.nan
    gain, loss = done.pnl[done.pnl > 0].sum() if len(done) else 0, -done.pnl[done.pnl < 0].sum() if len(done) else 0
    return {"cagr": (eq.iat[-1] / START_EQ) ** (1 / yrs) - 1, "dd": (eq / eq.cummax() - 1).min(),
            "final": eq.iat[-1], "trades": len(tr), "win": win,
            "avgR": done.R.mean() if len(done) else np.nan, "avg_ret": done.ret.mean() if len(done) else np.nan,
            "pf": gain / loss if loss else np.nan, "hold": done.days.mean() if len(done) else np.nan}


def run(daily, spy, start, out_dir, members=None):
    """Everything after the download (also used by tests with made-up prices)."""
    t0 = time.time()
    days = spy.index[spy.index >= pd.Timestamp(start.date())]
    full = spy.index                                     # warm-up history for the moving averages
    syms, F = features(daily, full)
    sc = spy["Close"]
    spy_r126 = (sc / sc.shift(126) - 1).reindex(full).values
    m50, m200 = sc.rolling(50).mean(), sc.rolling(200).mean()
    below = {"none": np.ones(len(full), bool), "mkt200": (sc < m200).reindex(full).fillna(False).values,
             "mkt50": (sc < m50).reindex(full).fillna(False).values}
    above200 = (sc > m200).reindex(full).fillna(True).values
    nears = {n: None if n == "off" else float(n) / 100 for n in NEARS}
    weaks = {n: weak_list(F, spy_r126, v) for n, v in nears.items()}
    weak = weak_list(F, spy_r126)                        # the screener list at the end: within 10% of the low
    k0 = int(np.searchsorted(full, days[0])) + 1
    os.makedirs(out_dir, exist_ok=True)

    lines = [f"Short strategy from a weak-stocks screen · {len(syms)} stocks ({' + '.join(UNIVERSES)}) · "
             f"{days[0]:%Y-%m-%d} → {days[-1]:%Y-%m-%d}",
             f"weak list: liquid (${MIN_DVOL / 1e6:.0f}M+/day, $10+), below the 200-day MA with the 50-day under it, "
             f"6-month return below SPY and in the bottom {WEAK_PCT:.0%}, + 'near low' = within X% of the 52-week low",
             f"exits: stop X ATR (none = no stop), close above the {TRAIL}-day high, {TIME_STOP} days · "
             f"{POS_PCT:.0%} per short, max {MAX_POS} · borrow {BORROW:.1%}/yr, slippage {SLIP:.2%}/side",
             "R is always measured against a 2-ATR risk, so versions with different stops compare fairly",
             "stocks on the weak list on an average day: " + ", ".join(
                 f"near low {n}{'' if n == 'off' else '%'}: {w[k0:].sum(1).mean():.1f}" for n, w in weaks.items()), ""]
    curves, rows, all_tr, detail = {}, [], [], []
    for ent in ENTRIES:
        trig_ = triggers(F, ent)
        for near, wk in weaks.items():
            signal = wk & trig_
            for mk in MARKETS:
                for stp in STOPS:
                    atr_stop = None if stp == "none" else float(stp)
                    tag = f"{ent} · {mk} · stop {stp} · near {near}"
                    tr, eq = simulate(full, syms, F, signal, below[mk], above200, k0, atr_stop)
                    st = stats(eq, tr)
                    yr = eq.groupby(eq.index.year).last()
                    yr = yr / np.r_[START_EQ, yr.values[:-1]] - 1
                    detail += [f"<b>{ENTRY_NAMES[ent]} · {MARKET_NAMES[mk]} · stop "
                               f"{'none' if atr_stop is None else f'{atr_stop:g} ATR'} · near low {near}</b>",
                               f"{st['cagr'] * 100:+.1f}%/yr · max drop {st['dd'] * 100:+.1f}% · final ${st['final']:,.0f}",
                               f"{st['trades']} trades, win {st['win']:.0%}, avg {st['avgR']:+.2f}R "
                               f"({st['avg_ret'] * 100:+.2f}% per trade), profit factor {st['pf']:.2f}, "
                               f"hold {st['hold']:.1f} days",
                               "by year: " + "  ".join(f"{y}: {v * 100:+.0f}%" for y, v in yr.items())]
                    if len(tr):
                        done = tr[tr.result != "still open"]
                        g = done.groupby("regime").agg(n=("R", "size"), R=("R", "mean"),
                                                       win=("pnl", lambda x: (x > 0).mean()))
                        detail.append("by market at entry: " + "  ".join(
                            f"{i} {int(r.n)}× {r.R:+.2f}R win {r.win:.0%}" for i, r in g.iterrows()))
                        g = done.groupby("result")["R"].agg(["size", "mean"])
                        detail.append("exits: " + "  ".join(f"{i} {int(r['size'])}× {r['mean']:+.2f}R"
                                                             for i, r in g.iterrows()))
                    detail.append("")
                    y22 = yr.get(2022, np.nan)
                    rows.append({"entry": ent, "market": mk, "stop": stp, "near_low": near, "y2022": y22, **st})
                    curves[tag] = eq
                    all_tr.append(tr.assign(entry_rule=ent, market=mk, stop_atr=stp, near_low=near))
                    print(f"{tag}: {st['cagr'] * 100:+.1f}%/yr, {st['trades']} trades, drop {st['dd'] * 100:+.1f}%",
                          flush=True)
    rk = pd.DataFrame(rows).sort_values("cagr", ascending=False)
    lines.append("<b>Ranking (best first)</b>")
    lines.append(f"  {'entry':6} {'market':6} {'stop':4} {'near':4} {'%/yr':>6} {'drop':>6} {'trades':>6} "
                 f"{'win':>4} {'avgR':>6} {'PF':>5} {'2022':>5}")
    for _, r in rk.iterrows():
        lines.append(f"  {r.entry:6} {r.market:6} {r.stop:4} {r.near_low:4} {r.cagr * 100:+6.1f} {r.dd * 100:+6.0f} "
                     f"{r.trades:6d} {r.win:4.0%} {r.avgR:+6.2f} {r.pf:5.2f} {r.y2022 * 100:+5.0f}")
    lines += [""] + detail
    best = list(rk.head(6).apply(lambda r: f"{r.entry} · {r.market} · stop {r.stop} · near {r.near_low}", axis=1))
    b = spy["Close"].reindex(curves[next(iter(curves))].index).ffill()
    b = b / b.iat[0] * START_EQ
    byrs = (b.index[-1] - b.index[0]).days / 365.25
    sy = b.groupby(b.index.year).last()
    sy = sy / np.r_[START_EQ, sy.values[:-1]] - 1
    lines += [f"<b>Buy & hold SPY</b> (for reference: a short strategy should make money when this falls): "
              f"{((b.iat[-1] / START_EQ) ** (1 / byrs) - 1) * 100:+.1f}%/yr, max drop {(b / b.cummax() - 1).min() * 100:+.1f}%",
              "by year: " + "  ".join(f"{y}: {v * 100:+.0f}%" for y, v in sy.items()), ""]

    last = len(full) - 1                                  # today's weak list, like a screener
    today = [(syms[c], F["r126"][last, c], F["C"][last, c]) for c in np.flatnonzero(weak[last])]
    today.sort(key=lambda x: x[1])
    trig = {e: set(np.flatnonzero(weak[last] & triggers(F, e))) for e in ENTRIES}
    lines.append(f"<b>Weak list on {full[last]:%Y-%m-%d}</b> ({len(today)} stocks, weakest first; * = trigger today):")
    lines.append("  " + ", ".join(f"{s} {r * 100:+.0f}%" + ("*" if any(syms.index(s) in t for t in trig.values()) else "")
                                  for s, r, _ in today[:30]) if today else "  none")
    lines += ["", "<i>Today's index members only: stocks that fell out of the index (often the biggest losers) "
              "are missing, so the weak lists of past years are smaller than they really were. Daily candles: "
              "stops judged on daily highs. No historical borrow availability: hard-to-borrow names may not have "
              "been shortable. Past results don't guarantee future ones.</i>"]
    text = "\n".join(lines)
    plain = text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
    print("\n" + plain)
    with open(os.path.join(out_dir, "summary.txt"), "w") as f:
        f.write(plain + "\n")
    pd.concat(all_tr).to_csv(os.path.join(out_dir, "trades.csv"), index=False)
    pd.DataFrame(rows).to_csv(os.path.join(out_dir, "results.csv"), index=False)
    pd.DataFrame({**curves, "Buy & hold SPY": b}).round(0).to_csv(os.path.join(out_dir, "equity.csv"), index_label="date")

    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(12, 7.5), dpi=110, sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    fig.patch.set_facecolor("black")
    palette = ["#42a5f5", "#ffd54f", "#66bb6a", "#ef5350", "#ab47bc", "#26c6da", "#ffa726", "#ec407a"]
    allc = {**{k: curves[k] for k in best}, "Buy & hold SPY": b}     # chart: the 6 best + SPY
    for a_ in (ax, ax2):
        a_.set_facecolor("black"); a_.grid(color="#222"); a_.tick_params(colors="#bbbbbb")
        for s in a_.spines.values():
            s.set_color("#444")
    for n, (name, s) in enumerate(allc.items()):
        spy_line = name.startswith("Buy")
        col = "#bbbbbb" if spy_line else palette[n % len(palette)]
        ax.plot(s.index, s.values, color=col, lw=1.6, ls="--" if spy_line else "-", label=name)
        ax2.plot(s.index, (s / s.cummax() - 1) * 100, color=col, lw=1.0, ls="--" if spy_line else "-")
    ax.set_title("Short strategy (weak-stocks screen): best 6 versions vs SPY", color="white")
    ax.set_ylabel("Account value ($)", color="#bbbbbb"); ax2.set_ylabel("Drawdown (%)", color="#bbbbbb")
    leg = ax.legend(facecolor="black", edgecolor="#444", fontsize=8)
    for x in leg.get_texts():
        x.set_color("#ddd")
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "chart.png"), facecolor="black")
    print(f"Done in {time.time() - t0:.0f}s, saved in {out_dir}")
    return rows


def main():
    end = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20)
    start = end - dt.timedelta(days=round(365.25 * YEARS))
    if os.environ.get("START"):
        start = dt.datetime.fromisoformat(os.environ["START"]).replace(tzinfo=dt.timezone.utc)
    members = sorted(set(sum((index_members(u) for u in UNIVERSES), [])))
    print(f"{len(members)} stocks, {start:%Y-%m-%d} → {end:%Y-%m-%d}", flush=True)
    daily = load_prices(members + ["SPY"], start, end)
    spy = daily.pop("SPY")
    print(f"Prices for {len(daily)} stocks", flush=True)
    tag = f"from{os.environ['START']}" if os.environ.get("START") else f"{YEARS:g}y"
    out_dir = os.path.join("reports", "backtest", f"short-screen-{'-'.join(UNIVERSES)}-{tag}"
                           + ("" if SOURCE == "alpaca" else f"-{SOURCE}"))
    run(daily, spy, start, out_dir)


if __name__ == "__main__":
    main()
