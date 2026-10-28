"""
Rule test: "If Friday's high does NOT pass Thursday's high, Monday will visit Friday's low."

Regular hours only (9:30-16:00 ET), 5-minute bars from the Friday->Monday backtest's saved prices.
  condition   Friday high < Thursday high (Friday = last trading day of the week, Thursday = the
              trading day before it)
  claim       Monday's low <= Friday's low (Monday = first trading day of the next week)
  compared to Mondays where Friday DID pass Thursday's high, and to the same pattern on every
              other day (day D-1 high < day D-2 high -> does day D visit day D-1's low?)
  trade       short at Monday's open, target = Friday's low, stop = Friday's high + $0.02
              (if Monday opens at/above it: stop = open + 0.25 ATR; never closer than 0.1 ATR),
              else out at 16:00; same costs
              as friday_monday_backtest.py. Mondays opening at/below Friday's low: no trade.

    SYMBOLS="SPY" python friday_low_test.py          (empty SYMBOLS = the S&P 100)
"""
import os

import numpy as np
import pandas as pd

import friday_monday_backtest as fm

OUT = os.environ.get("OUT_DIR", "backtest-results")


def load():
    if fm.FAKE:
        end = pd.Timestamp.now(tz=fm.ET)
        return fm.fake_data(pd.Timestamp(fm.START, tz=fm.ET) - pd.Timedelta(days=40), end, n=10)
    data = {}
    for s in fm.SYMBOLS:
        try:
            data[s] = pd.read_pickle(os.path.join(fm.CACHE_DIR, f"{s}_5min.pkl.gz"))
        except Exception:
            pass
    missing = [s for s in fm.SYMBOLS if s not in data]
    if missing:                                   # not saved yet: download (needs Alpaca keys)
        end = pd.Timestamp.now(tz=fm.ET) - pd.Timedelta(minutes=20)
        data.update(fm.fetch(missing, pd.Timestamp(fm.START, tz=fm.ET) - pd.Timedelta(days=40), end, fm._headers()))
    return data


def first_hit(mon, level):
    m, o, h, l, c = mon
    i = np.flatnonzero(l <= level)
    return int(i[0]) if len(i) else None


def trade(mon, fri_low, fri_high, atr):
    m, o, h, l, c = mon
    entry = o[0]
    if entry <= fri_low:
        return None
    stop = fri_high + fm.STOP_BUFFER
    if entry >= stop:
        stop = entry + fm.A_STOP_ATR * atr
    stop = max(stop, entry + fm.MIN_RISK_ATR * atr)      # same minimum stop as the main backtest
    risk = stop - entry
    s = np.flatnonzero(h >= stop)
    t = np.flatnonzero(l <= fri_low - fm.TICK)
    s = s[0] if len(s) else 10 ** 9
    t = t[0] if len(t) else 10 ** 9
    if s <= t and s < len(h):
        px, why = max(stop, o[s]), "stop"
    elif t < len(h):
        px, why = min(fri_low, o[t]) if t > 0 else fri_low, "target"
    else:
        px, why = c[-1], "close"
    gross = entry - px
    net = gross - fm.costs(entry, px, "mkt", why)
    return dict(R=net / risk, R_gross=gross / risk, reason=why, risk_atr=risk / atr,
                target_atr=(entry - fri_low) / atr)


def main():
    data = load()
    rows = []
    for sym, df in data.items():
        days, daily = fm.split_days(df)
        dates = sorted(days)
        for k in range(2, len(dates)):
            d2, d1, d0 = dates[k - 2], dates[k - 1], dates[k]
            if d0 < fm.START or (d0 - d1).days > 4:
                continue
            atr = daily.at[d1, "atr"]
            if not atr > 0:
                continue
            mon = days[d0]
            low1, high1, high2 = daily.at[d1, "Low"], daily.at[d1, "High"], daily.at[d2, "High"]
            monday = d0.isocalendar()[:2] != d1.isocalendar()[:2]
            i = first_hit(mon, low1)
            r = dict(sym=sym, date=d0, monday=monday, cond=high1 < high2,
                     hit=i is not None, gap_below=mon[1][0] <= low1,
                     hit_time=fm._hhmm(mon[0][i]) if i is not None else "",
                     dist_atr=(mon[1][0] - low1) / atr)
            if monday:
                tr = trade(mon, low1, high1, atr)
                if tr:
                    r.update(tr)
            rows.append(r)
    t = pd.DataFrame(rows)
    t["oos"] = t["date"] >= fm.OOS_START
    t["year"] = pd.to_datetime(t["date"]).dt.year

    out = []
    w = out.append
    w("RULE: Friday high < Thursday high  ->  Monday visits Friday's low   " + " ".join(sorted(data))[:80])
    w(f"Mondays {t[t.monday]['date'].min()} to {t[t.monday]['date'].max()}; regular hours only; out-of-sample from {fm.OOS_START}.")
    w("")
    w("=== 1. HOW OFTEN DOES THE NEXT DAY TRADE AT OR BELOW THE PREVIOUS DAY'S LOW? ===")
    w(f"{'':<46}{'days':>8}{'hit':>7}{'IS hit':>8}{'OOS hit':>9}{'opens below':>13}{'hit, not opening below':>24}")

    def line(label, x):
        nb = x[~x["gap_below"]]
        w(f"{label:<46}{len(x):>8,}{x['hit'].mean():>7.0%}{x[~x.oos]['hit'].mean():>8.0%}"
          f"{x[x.oos]['hit'].mean():>9.0%}{x['gap_below'].mean():>13.0%}{nb['hit'].mean():>24.0%}")

    mon, oth = t[t.monday], t[~t.monday]
    line("Monday, Fri high < Thu high  (YOUR RULE)", mon[mon.cond])
    line("Monday, Fri high >= Thu high", mon[~mon.cond])
    line("Monday, all", mon)
    line("Other days, prev high < the high before it", oth[oth.cond])
    line("Other days, prev high >= the high before it", oth[~oth.cond])
    w("")
    w("A rule is only useful if YOUR RULE's hit rate is clearly above the other rows.")
    w("")

    w("=== 2. SAME, BY HOW FAR MONDAY OPENS ABOVE FRIDAY'S LOW (in ATRs) ===")
    w("(a low that is closer is hit more often anyway; this compares like with like)")
    bins = [-np.inf, 0, 0.25, 0.5, 0.75, 1.0, 1.5, np.inf]
    labels = ["opens below", "0-0.25", "0.25-0.5", "0.5-0.75", "0.75-1", "1-1.5", ">1.5"]
    mon = mon.assign(bucket=pd.cut(mon["dist_atr"], bins, labels=labels))
    w(f"{'distance':<14}{'rule n':>8}{'rule hit':>10}{'other n':>9}{'other hit':>11}")
    for b in labels:
        a, o = mon[(mon.bucket == b) & mon.cond], mon[(mon.bucket == b) & ~mon.cond]
        w(f"{b:<14}{len(a):>8}{_p(a['hit'].mean()):>10}{len(o):>9}{_p(o['hit'].mean()):>11}")
    w("")

    w("=== 3. YEAR BY YEAR (hit rate, Mondays) ===")
    w(f"{'year':<8}{'rule n':>8}{'rule hit':>10}{'other hit':>11}")
    for y, g in mon.groupby("year"):
        w(f"{y:<8}{int(g.cond.sum()):>8}{_p(g[g.cond]['hit'].mean()):>10}{_p(g[~g.cond]['hit'].mean()):>11}")
    w("")

    w("=== 4. AS A TRADE: short Monday open, target Friday's low, stop Friday's high, out at 16:00 ===")
    tr = mon[mon["R"].notna()] if "R" in mon else mon.iloc[:0]
    w(f"{'':<34}{'trades':>8}{'win':>6}{'gross R':>9}{'net R':>8}{'PF':>6}{'t':>6}{'target':>8}{'stop':>6}{'16:00':>7}")
    for label, x in [("Rule, in-sample", tr[tr.cond & ~tr.oos]), ("Rule, out-of-sample", tr[tr.cond & tr.oos]),
                     ("Fri high >= Thu high, in-sample", tr[~tr.cond & ~tr.oos]),
                     ("Fri high >= Thu high, out-of-sample", tr[~tr.cond & tr.oos])]:
        if not len(x):
            continue
        R = x["R"].to_numpy(float)
        pf = R[R > 0].sum() / -R[R < 0].sum() if (R < 0).any() else np.nan
        wk = x.groupby("date")["R"].mean()
        ts = wk.mean() / (wk.std(ddof=1) / np.sqrt(len(wk))) if len(wk) > 2 else np.nan
        rc = x["reason"].value_counts(normalize=True)
        w(f"{label:<34}{len(x):>8,}{(R > 0).mean():>6.0%}{x['R_gross'].mean():>+9.3f}{R.mean():>+8.3f}{pf:>6.2f}{ts:>+6.1f}"
          f"{rc.get('target', 0):>8.0%}{rc.get('stop', 0):>6.0%}{rc.get('close', 0):>7.0%}")
    if len(tr):
        w(f"Median target distance {tr['target_atr'].median():.2f} ATR vs stop distance {tr['risk_atr'].median():.2f} ATR "
          "(so a 'hit' usually earns less than 1R).")

    os.makedirs(OUT, exist_ok=True)
    text = "\n".join(out)
    open(os.path.join(OUT, "summary.txt"), "w").write(text + "\n")
    t.to_csv(os.path.join(OUT, "days.csv.gz"), index=False)
    print(text)


def _p(x):
    return "-" if x != x else f"{x:.0%}"


if __name__ == "__main__":
    main()
