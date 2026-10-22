"""
Monte Carlo tests of the live deck pattern trader (version 6: deck stop, sell on a close below the 20-day low,
close above the 50-day MA, 2x leverage) on the S&P 100 - same engine as deck_universe_backtest.py.

Four tests:
  1. Drawdown range   - block bootstrap of the strategy's DAILY returns (20-day blocks, so bad weeks stay
                         together and the overlap between open trades is kept) -> range of yearly return and
                         max drop over the same number of years, chance of a drop worse than -30% / -50%.
  2. Edge range       - bootstrap of the closed trades (with replacement) -> range of avg R, win rate and
                         profit factor; chance the real average R is <= 0.
  3. Random entries   - same days, same number of buys per day, same 50-day-MA filter, same stop distance
                         (in ATR, drawn from the real trades), same exits, sizing and leverage, but the stock is
                         picked at random instead of by a pattern. If the real result doesn't beat ~95% of
                         random runs, the patterns aren't adding much beyond the exit rule and the trend filter.
  4. Parameter jitter - random 20-day-low length (15-25) and MA length (40-60): a robust strategy degrades
                         smoothly instead of collapsing.

    python deck_montecarlo.py                 all four (needs ALPACA keys; run it in GitHub Actions)
    OFFLINE=1 python deck_montecarlo.py       tests 1-2 only, from the saved trades/equity of the live setup
Knobs: LEVERAGE=2 MA=50 YEARS=8 N_BOOT=10000 N_RANDOM=200 N_JITTER=40 BLOCK=20
Results: reports/backtest/montecarlo-deck-v6-<lev>x-ma<MA>-sp100-<years>y/
"""
import datetime as dt
import glob
import os
import time
from concurrent.futures import ProcessPoolExecutor

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import deck_universe_backtest as dub

LEV = float(os.environ.get("LEVERAGE", "2"))
MA = int(os.environ.get("MA", "50"))
YEARS = float(os.environ.get("YEARS", "8"))
N_BOOT = int(os.environ.get("N_BOOT", "10000"))
N_RANDOM = int(os.environ.get("N_RANDOM", "200"))
N_JITTER = int(os.environ.get("N_JITTER", "40"))
BLOCK = int(os.environ.get("BLOCK", "20"))
OFFLINE = os.environ.get("OFFLINE", "") not in ("", "0")
START_EQ = dub.START_EQ
rng = np.random.default_rng(42)


def pct(a, q):
    return float(np.percentile(a, q))


def cagr_dd(eq):
    eq = np.asarray(eq, float)
    yrs = len(eq) / 252
    cagr = (max(eq[-1], 1e-9) / eq[0]) ** (1 / yrs) - 1 if eq[-1] > 0 else -1.0
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    return cagr, dd


# ---------------------------------------------------------------- tests 1-2 (need only the results)
def block_bootstrap(eq):
    """Test 1: rebuild equity curves from 20-day blocks of real daily returns."""
    r = eq.pct_change().dropna().values
    n = len(r)
    k = int(np.ceil(n / BLOCK))
    cagrs, dds = np.empty(N_BOOT), np.empty(N_BOOT)
    for i in range(N_BOOT):
        starts = rng.integers(0, n - BLOCK, k)
        path = np.concatenate([r[s:s + BLOCK] for s in starts])[:n]
        curve = START_EQ * np.cumprod(1 + path)
        cagrs[i], dds[i] = cagr_dd(np.r_[START_EQ, curve])
    return cagrs, dds


def trade_bootstrap(tr):
    """Test 2: resample the closed trades with replacement."""
    R = tr["R"].values
    pnl = tr["pnl"].values
    n = len(R)
    out = np.empty((N_BOOT, 3))
    for i in range(N_BOOT):
        idx = rng.integers(0, n, n)
        p = pnl[idx]
        loss = -p[p < 0].sum()
        out[i] = R[idx].mean(), (p > 0).mean(), p[p > 0].sum() / loss if loss else np.nan
    return out


def report_1_2(eq, tr, lines):
    real_cagr, real_dd = cagr_dd(eq.values)
    cg, dd = block_bootstrap(eq)
    lines += [f"<b>1. Drawdown range</b> ({N_BOOT:,} reshuffled histories, {BLOCK}-day blocks of real daily returns)",
              f"real: {real_cagr:+.1%}/yr, max drop {real_dd:+.1%}",
              f"yearly return: 5% {pct(cg, 5):+.1%} · median {pct(cg, 50):+.1%} · 95% {pct(cg, 95):+.1%}; "
              f"losing money over the period: {(cg < 0).mean():.1%}",
              f"max drop: median {pct(dd, 50):+.1%} · 1 in 20 worse than {pct(dd, 5):+.1%} · "
              f"1 in 100 worse than {pct(dd, 1):+.1%}",
              f"chance of a drop worse than -30%: {(dd < -0.30).mean():.0%} · -50%: {(dd < -0.50).mean():.0%} · "
              f"-70%: {(dd < -0.70).mean():.0%}", ""]
    bs = trade_bootstrap(tr)
    lines += [f"<b>2. Edge range</b> ({N_BOOT:,} resamples of the {len(tr)} closed trades)",
              f"avg R: real {tr.R.mean():+.2f} · 5% {pct(bs[:, 0], 5):+.2f} · 95% {pct(bs[:, 0], 95):+.2f}; "
              f"chance avg R <= 0: {(bs[:, 0] <= 0).mean():.2%}",
              f"win rate: 5% {pct(bs[:, 1], 5):.0%} - 95% {pct(bs[:, 1], 95):.0%} · profit factor: "
              f"5% {np.nanpercentile(bs[:, 2], 5):.2f} - 95% {np.nanpercentile(bs[:, 2], 95):.2f}",
              f"how much rides on the biggest winners: without the top 1% of trades avg R is "
              f"{tr.R.sort_values().iloc[:-max(1, len(tr) // 100)].mean():+.2f}, without the top 5% "
              f"{tr.R.sort_values().iloc[:-max(1, len(tr) // 20)].mean():+.2f}", ""]
    return {"cg": cg, "dd": dd, "bs": bs, "real_cagr": real_cagr, "real_dd": real_dd}


# ---------------------------------------------------------------- tests 3-4 (need prices)
def load():
    end = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20)
    start = end - dt.timedelta(days=round(365.25 * YEARS))
    syms = dub.scanner.get_tickers()
    raw = dub.scanner.alpaca_bars(sorted(set(syms) | {"SPY"}), "1Day", start - dt.timedelta(days=450), end,
                                  chunk=50, workers=6)
    daily = {}
    for s, d in raw.items():
        d = d.copy()
        d.index = d.index.tz_convert(dub.scanner.ET).tz_localize(None).normalize()
        daily[s] = d[["Open", "High", "Low", "Close", "Volume"]]
    spy = daily.pop("SPY")
    t0 = time.time()
    found = {}
    jobs = [(s, daily[s]) for s in sorted(daily) if len(daily[s]) > 300]
    with ProcessPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        for s, out in pool.map(dub._signals, jobs, chunksize=4):
            found[s] = out
    print(f"Breakouts found in {time.time() - t0:.0f}s", flush=True)
    syms = [s for s in syms if s in daily and s in found]
    days = spy.index[spy.index >= pd.Timestamp(start.date()) - pd.Timedelta(days=40)]
    first = int(np.searchsorted(days.values, np.datetime64(pd.Timestamp(start.date()))))
    return daily, found, syms, days, first, spy


class Market:
    def __init__(self, daily, found, syms, days, first):
        self.daily, self.found, self.syms, self.days, self.first = daily, found, syms, days, first
        mat = lambda f: np.column_stack([f(daily[s]).reindex(days).values for s in syms])
        self.O, self.H, self.L, self.C = (mat(lambda d, k=k: d[k]) for k in ("Open", "High", "Low", "Close"))
        self.ATR = mat(lambda d: dub.dp.add_atr(d)["ATR"])
        self.pos_of = {d: k for k, d in enumerate(days)}
        self._ma, self._low = {}, {}

    def ma(self, n):
        if n not in self._ma:
            self._ma[n] = np.column_stack([self.daily[s]["Close"].rolling(n).mean().reindex(self.days).values
                                           for s in self.syms])
        return self._ma[n]

    def low(self, n):
        if n not in self._low:
            self._low[n] = np.column_stack([self.daily[s]["Low"].rolling(n).min().shift(1).reindex(self.days).values
                                            for s in self.syms])
        return self._low[n]

    def signals(self, ma_n):
        """The real pattern breakouts, filtered by close > ma_n-day MA."""
        M, sigs = self.ma(ma_n), {}
        for col, s in enumerate(self.syms):
            for date, stop, fam, name, bclose, batr in self.found[s]:
                k = self.pos_of.get(pd.Timestamp(date))
                if k is None or k < self.first or not (self.C[k, col] > M[k, col]):
                    continue
                sigs.setdefault(k, []).append((col, stop, fam, name, bclose, batr))
        return sigs

    def random_signals(self, real, seed):
        """Same number of buys per day, random stocks above their MA, real stop distances in ATR."""
        r = np.random.default_rng(seed)
        M = self.ma(MA)
        dist = np.array([(b - st) / a for v in real.values() for (_, st, _, _, b, a) in v if a > 0 and b > st])
        out = {}
        for k, v in real.items():
            ok = np.flatnonzero((self.C[k] > M[k]) & np.isfinite(self.ATR[k]) & (self.ATR[k] > 0))
            if not len(ok):
                continue
            for col in r.choice(ok, min(len(v), len(ok)), replace=False):
                c, a = self.C[k, col], self.ATR[k, col]
                out.setdefault(k, []).append((int(col), c - r.choice(dist) * a, int(r.integers(0, 3)),
                                              "random", c, a))
        return out

    def run(self, sigs, trail, d0, seed):
        tr, eq, info = dub.simulate(self.days, self.O, self.H, self.L, self.C, self.low(trail), sigs, self.syms,
                                    d0, seed=seed, lev=LEV)
        return dub.summarize(eq, tr), tr, eq


def main():
    t00 = time.time()
    tag = f"{LEV:g}x-ma{MA}-sp100-{YEARS:g}y"
    out_dir = os.path.join("reports", "backtest", f"montecarlo-deck-v6-{tag}")
    os.makedirs(out_dir, exist_ok=True)
    lines = [f"🎲 Monte Carlo tests of the deck pattern trader version 6 (S&P 100, close above the {MA}-day MA, "
             f"{LEV:g}x leverage, sell on a close below the 20-day low)", ""]
    extra = {}

    if OFFLINE:                       # newest saved universe backtest of this setup
        want = f"SP100 {LEV:g}x leverage · close above its {MA}-day MA"
        for d in sorted(glob.glob("reports/backtest/universe-deck-v6-lev-*-sp100-8y"), key=os.path.getmtime,
                        reverse=True):
            eqs = pd.read_csv(os.path.join(d, "equity.csv"), index_col="date", parse_dates=True)
            if want in eqs.columns:
                break
        else:
            raise SystemExit(f"No saved backtest with '{want}'")
        trs = pd.read_csv(os.path.join(d, "trades.csv"))
        tr = trs[(trs["filter"] == f"ma{MA}") & (trs["leverage"] == LEV) & (trs.result != "still open")]
        eq = eqs[want].dropna()
        lines.append(f"From the saved middle run in {d} ({eq.index[0]:%d %b %Y} → {eq.index[-1]:%d %b %Y}).\n")
        res = report_1_2(eq, tr, lines)
        lines.append("Tests 3-4 (random entries, parameter jitter) need prices: run the Monte Carlo workflow.")
    else:
        daily, found, syms, days, first, spy = load()
        mk = Market(daily, found, syms, days, first)
        real = mk.signals(MA)
        print(f"{sum(len(v) for v in real.values())} filtered breakouts", flush=True)
        starts = [first + r * dub.STEP for r in range(dub.RUNS)]
        base = [mk.run(real, dub.TRAIL, d0, seed=r) for r, d0 in enumerate(starts)]
        cg = [b[0]["cagr"] for b in base]
        mid = int(np.argsort(cg)[len(cg) // 2])
        st, tr, eq = base[mid]
        tr = tr[tr.result != "still open"]
        lines.append(f"Real strategy, middle of {dub.RUNS} start dates: {st['cagr']:+.1%}/yr, max drop "
                     f"{st['dd']:+.1%}, {st['trades']} trades, avg {st['avgR']:+.2f}R.\n")
        res = report_1_2(eq, tr, lines)

        # 3. random entries
        t0 = time.time()
        rnd = []
        for i in range(N_RANDOM):
            s_, _, _ = mk.run(mk.random_signals(real, 1000 + i), dub.TRAIL, starts[i % len(starts)], seed=i)
            rnd.append(s_)
            if (i + 1) % 25 == 0:
                print(f"Random entries {i + 1}/{N_RANDOM} ({time.time() - t0:.0f}s)", flush=True)
        rc = np.array([x["cagr"] for x in rnd])
        rr = np.array([x["avgR"] for x in rnd])
        rd = np.array([x["dd"] for x in rnd])
        real_med = float(np.median(cg))
        p_val = (np.sum(rc >= real_med) + 1) / (len(rc) + 1)
        lines += [f"<b>3. Random entries</b> ({N_RANDOM} runs: same days, buys per day, MA filter, stop distance, "
                  f"exits, sizing)",
                  f"real (median of {dub.RUNS} starts) {real_med:+.1%}/yr vs random: median {np.median(rc):+.1%} · "
                  f"95% {pct(rc, 95):+.1%} · best {rc.max():+.1%}",
                  f"real beats {np.mean(rc < real_med):.0%} of random runs (p = {p_val:.3f}) · avg R real "
                  f"{st['avgR']:+.2f} vs random median {np.median(rr):+.2f} · max drop random median "
                  f"{np.median(rd):+.1%}",
                  ("→ the patterns add a real edge on top of the exit rule and trend filter" if p_val < 0.05 else
                   "→ NOT clearly better than random stocks with the same rules: most of the result comes from the "
                   "exit rule, the trend filter and leverage, not the patterns"), ""]
        extra["random"] = (rc, real_med)

        # 4. parameter jitter
        t0 = time.time()
        jit = []
        for i in range(N_JITTER):
            trail, ma_n = int(rng.integers(15, 26)), int(rng.integers(40, 61))
            s_, _, _ = mk.run(mk.signals(ma_n), trail, starts[i % len(starts)], seed=i)
            jit.append({"trail": trail, "ma": ma_n, **s_})
        jt = pd.DataFrame(jit)
        spy_c = spy["Close"].reindex(days).ffill().iloc[first:]
        spy_cagr = (spy_c.iat[-1] / spy_c.iat[0]) ** (365.25 / (spy_c.index[-1] - spy_c.index[0]).days) - 1
        lines += [f"<b>4. Parameter jitter</b> ({N_JITTER} runs, trail 15-25 days, MA 40-60 days, {time.time() - t0:.0f}s)",
                  f"yearly return: worst {jt.cagr.min():+.1%} · 5% {pct(jt.cagr, 5):+.1%} · median "
                  f"{jt.cagr.median():+.1%} · best {jt.cagr.max():+.1%}; beat SPY ({spy_cagr:+.1%}/yr) in "
                  f"{(jt.cagr > spy_cagr).mean():.0%}",
                  f"max drop: median {jt.dd.median():+.1%} · worst {jt.dd.min():+.1%}",
                  "worst settings: " + ", ".join(f"trail {int(r.trail)}/MA {int(r.ma)} {r.cagr:+.1%}"
                                                 for _, r in jt.nsmallest(3, "cagr").iterrows()), ""]
        jt.to_csv(os.path.join(out_dir, "jitter.csv"), index=False)
        pd.DataFrame(rnd).to_csv(os.path.join(out_dir, "random_entries.csv"), index=False)
        extra["jitter"] = jt

    lines.append("<i>Bootstraps assume the future looks like a reshuffled past; today's index members only "
                 "(survivorship flatters results). Past results don't guarantee future ones.</i>")
    text = "\n".join(lines)
    plain = text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
    print("\n" + plain)
    with open(os.path.join(out_dir, "summary.txt"), "w") as f:
        f.write(plain + "\n")

    # charts
    panels = [("Yearly return, reshuffled histories (%)", res["cg"] * 100, res["real_cagr"] * 100),
              ("Max drop, reshuffled histories (%)", res["dd"] * 100, res["real_dd"] * 100),
              ("Avg R, resampled trades", res["bs"][:, 0], tr.R.mean())]
    if "random" in extra:
        panels.append(("Yearly return, random entries (%)", extra["random"][0] * 100, extra["random"][1] * 100))
    if "jitter" in extra:
        panels.append(("Yearly return, jittered settings (%)", extra["jitter"].cagr.values * 100,
                       res["real_cagr"] * 100))
    fig, axs = plt.subplots(1, len(panels), figsize=(4.2 * len(panels), 3.6), dpi=110)
    fig.patch.set_facecolor("black")
    for ax, (title, data, real_v) in zip(np.atleast_1d(axs), panels):
        ax.set_facecolor("black"); ax.tick_params(colors="#bbbbbb")
        for s in ax.spines.values():
            s.set_color("#444")
        ax.hist(data[np.isfinite(data)], bins=50, color="#42a5f5", alpha=0.85)
        ax.axvline(real_v, color="#ffd54f", lw=2, label="real")
        ax.set_title(title, color="white", fontsize=10)
    leg = np.atleast_1d(axs)[0].legend(facecolor="black", edgecolor="#444")
    for x in leg.get_texts():
        x.set_color("#ddd")
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "chart.png"), facecolor="black")
    print(f"Done in {time.time() - t00:.0f}s, saved in {out_dir}")


if __name__ == "__main__":
    main()
