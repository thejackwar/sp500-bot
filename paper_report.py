"""
Daily paper-trading report for both Alpaca paper accounts:
    Breakout bot    (ALPACA_KEY_ID / ALPACA_SECRET_KEY): trader.py stocks + crypto_trader.py crypto
    Pattern trader  (ALPACA_PATTERN_KEY_ID / ALPACA_PATTERN_SECRET_KEY): pattern_trader.py

Everything is rebuilt from Alpaca's own records every run (account value history, filled orders,
open positions), so a missed day loses nothing. Writes:
    reports/paper/README.md            summary of both accounts (read this first)
    reports/paper/<account>_trades.csv every closed trade: entry, exit, profit, R, what opened it
    reports/paper/<account>_open.csv   open positions with their stop and current profit
    reports/paper/<account>_equity.csv account value per day, and SPY on the same scale
and sends a short Telegram summary. The workflow (paper-report.yml) commits the files to the repo.

R = profit divided by the money risked at the entry (entry to the first stop). "What opened it" comes
from the order's client_order_id: bot-daily / bot-intraday (breakout bot), crypto-... (crypto bot),
pat-<pattern> (pattern trader); trades placed before this tagging existed show "unknown".
"""
import datetime as dt
import os
import re

import numpy as np
import pandas as pd
import requests

import scanner
from fmt import px

OUT = os.path.join("reports", "paper")
PAPER = "https://paper-api.alpaca.markets/v2"
DATA = "https://data.alpaca.markets/v2"
ACCOUNTS = [   # (file name, title, key id variable, secret variable)
    ("breakout", "Breakout bot (stocks + crypto)", "ALPACA_KEY_ID", "ALPACA_SECRET_KEY"),
    ("patterns", "Pattern trader (selected chart patterns)", "ALPACA_PATTERN_KEY_ID", "ALPACA_PATTERN_SECRET_KEY"),
]


class Api:
    def __init__(self, key, secret):
        self.h = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}

    def get(self, path, base=PAPER, **params):
        r = requests.get(f"{base}{path}", headers=self.h, params=params, timeout=30)
        r.raise_for_status()
        return r.json()


def sym_key(s):
    return s.replace("/", "")                          # BTC/USD (orders) = BTCUSD (positions)


def source(client_id):
    """What opened the trade, from the order's client_order_id."""
    c = client_id or ""
    if c.startswith("pat-"):
        return c[4:].rsplit("-", 1)[0].replace("_", " ")
    for prefix, name in (("bot-daily", "breakout (daily)"), ("bot-intraday", "breakout (15-min)"),
                         ("crypto-daily", "crypto breakout (daily)"), ("crypto-", "crypto breakout (intraday)")):
        if c.startswith(prefix):
            return name
    return "unknown"


# ---------------------------------------------------------------- Alpaca records
def all_orders(api, since):
    """Every order since `since` (oldest first), bracket/oto legs included as separate orders."""
    out, after = [], since
    while True:
        page = api.get("/orders", status="all", limit=500, direction="asc", nested="true", after=after)
        if not page:
            break
        for o in page:
            out.append(o)
            out += o.get("legs") or []
        if len(page) < 500:
            break
        after = page[-1]["submitted_at"]
    seen, uniq = set(), []
    for o in out:
        if o["id"] not in seen:
            seen.add(o["id"])
            uniq.append(o)
    return uniq


def equity_history(api, start):
    try:
        js = api.get("/account/portfolio/history", timeframe="1D", start=start, intraday_reporting="market_hours")
    except Exception:
        js = api.get("/account/portfolio/history", timeframe="1D", period="1A")
    ts = pd.to_datetime(js.get("timestamp") or [], unit="s", utc=True).tz_convert(scanner.ET).normalize().tz_localize(None)
    eq = pd.Series(js.get("equity") or [], index=ts, dtype=float).dropna()
    eq = eq[eq > 0]
    return eq[~eq.index.duplicated(keep="last")]


def round_trips(orders):
    """Filled orders -> closed trades and the opening details of still-open positions.
    Per symbol, a trade starts when the position leaves 0 and ends when it is back to (about) 0."""
    fills = [o for o in orders if o.get("filled_at") and float(o.get("filled_qty") or 0) > 0]
    fills.sort(key=lambda o: o["filled_at"])
    stops = [o for o in orders if o.get("side") == "sell" and o.get("type") in ("stop", "stop_limit")
             and o.get("stop_price")]
    trades, open_ = [], {}
    for o in fills:
        s = sym_key(o["symbol"])
        q = float(o["filled_qty"]) * (1 if o["side"] == "buy" else -1)
        p = float(o["filled_avg_price"])
        t = open_.get(s)
        if t is None:
            stop = next((float(x["stop_price"]) for x in stops if sym_key(x["symbol"]) == s
                         and x["submitted_at"] >= o["submitted_at"]), None)
            t = open_[s] = {"symbol": s, "dir": "LONG" if q > 0 else "SHORT", "opened": o["filled_at"], "qty": 0.0,
                            "max_qty": 0.0, "cost": 0.0, "proceeds": 0.0, "bought": 0.0, "sold": 0.0,
                            "stop": stop, "source": source(o.get("client_order_id")), "first_px": p}
        t["qty"] += q
        t["max_qty"] = max(t["max_qty"], abs(t["qty"]))
        if q > 0:
            t["cost"] += q * p
            t["bought"] += q
        else:
            t["proceeds"] += -q * p
            t["sold"] += -q
        if abs(t["qty"]) <= max(1e-9, 0.01 * t["max_qty"]):       # closed (crypto fees leave dust)
            sign = 1 if t["dir"] == "LONG" else -1
            entry = (t["cost"] / t["bought"]) if sign > 0 else (t["proceeds"] / t["sold"])
            exit_ = (t["proceeds"] / t["sold"]) if sign > 0 else (t["cost"] / t["bought"])
            qty = t["bought"] if sign > 0 else t["sold"]
            pnl = t["proceeds"] - t["cost"]
            risk = abs(entry - t["stop"]) * qty if t["stop"] else None
            trades.append({"symbol": s, "direction": t["dir"], "opened_by": t["source"],
                           "entry_date": t["opened"][:10], "exit_date": o["filled_at"][:10],
                           "entry": round(entry, 6), "exit": round(exit_, 6), "qty": round(qty, 8),
                           "stop": t["stop"], "pnl": round(pnl, 2), "pct": round(sign * (exit_ / entry - 1) * 100, 2),
                           "R": round(pnl / risk, 2) if risk else None,
                           "days": (pd.Timestamp(o["filled_at"]) - pd.Timestamp(t["opened"])).days})
            del open_[s]
    return pd.DataFrame(trades, columns=["symbol", "direction", "opened_by", "entry_date", "exit_date", "entry",
                                         "exit", "qty", "stop", "pnl", "pct", "R", "days"]), open_


def spy_curve(index, start_value):
    """SPY's closes on the same days, scaled to the account's starting value."""
    try:
        bars = scanner.alpaca_bars(["SPY"], "1Day", pd.Timestamp(index[0]).tz_localize(scanner.ET) - pd.Timedelta(days=5),
                                   dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20)).get("SPY")
        c = bars["Close"]
        c.index = c.index.tz_localize(None).normalize()
        c = c.reindex(index, method="ffill").dropna()
        return c / c.iat[0] * start_value
    except Exception as e:
        print(f"No SPY prices ({e})", flush=True)
        return None


# ---------------------------------------------------------------- one account
def account_report(name, title, api):
    acct = api.get("/account")
    start = acct.get("created_at", "2026-01-01")[:10]
    eq = equity_history(api, start)
    orders = all_orders(api, start + "T00:00:00Z")
    trades, opening = round_trips(orders)
    positions = api.get("/positions")
    equity, last = float(acct["equity"]), float(acct.get("last_equity") or acct["equity"])
    today = pd.Timestamp.now(tz=scanner.ET).normalize().tz_localize(None)
    if len(eq) == 0 or eq.index[-1] < today:
        eq.loc[today] = equity
    eq = eq.sort_index()
    changed = np.flatnonzero(eq.diff().fillna(0).values != 0)
    if len(changed):                                   # start the day before the first change (skip idle months)
        eq = eq.iloc[max(0, changed[0] - 1):]
    start_value = float(eq.iat[0])
    spy = spy_curve(eq.index, start_value)

    opens = []
    for p in positions:
        s = p["symbol"]
        t = opening.get(sym_key(s), {})
        qty, entry, now = float(p["qty"]), float(p["avg_entry_price"]), float(p.get("current_price") or 0)
        stop = t.get("stop")
        risk = abs(entry - stop) * abs(qty) if stop else None
        upnl = float(p.get("unrealized_pl") or 0)
        opens.append({"symbol": s, "opened_by": t.get("source", "unknown"), "since": (t.get("opened") or "")[:10],
                      "qty": qty, "entry": entry, "now": now, "stop": stop, "pnl": round(upnl, 2),
                      "pct": round(float(p.get("unrealized_plpc") or 0) * 100, 2),
                      "R": round(upnl / risk, 2) if risk else None, "value": round(float(p.get("market_value") or 0), 2)})
    opens = pd.DataFrame(opens, columns=["symbol", "opened_by", "since", "qty", "entry", "now", "stop", "pnl", "pct",
                                         "R", "value"])

    # files
    os.makedirs(OUT, exist_ok=True)
    trades.to_csv(os.path.join(OUT, f"{name}_trades.csv"), index=False)
    opens.to_csv(os.path.join(OUT, f"{name}_open.csv"), index=False)
    e = pd.DataFrame({"equity": eq.round(2)})
    if spy is not None:
        e["spy_same_money"] = spy.round(2)
    e.index.name = "date"
    e.to_csv(os.path.join(OUT, f"{name}_equity.csv"))

    # figures
    ret = equity / start_value - 1
    day = equity / last - 1 if last else 0.0
    dd = float((eq / eq.cummax() - 1).min()) if len(eq) else 0.0
    spy_ret = float(spy.iat[-1] / spy.iat[0] - 1) if spy is not None and len(spy) > 1 else None
    done = trades
    wins = done[done["pnl"] > 0]
    gross_loss = -done.loc[done["pnl"] <= 0, "pnl"].sum()
    rs = done["R"].dropna()
    st = {"title": title, "equity": equity, "day": day, "ret": ret, "spy": spy_ret, "dd": dd, "since": eq.index[0],
          "trades": len(done), "win": len(wins) / len(done) if len(done) else None,
          "avgR": float(rs.mean()) if len(rs) else None, "pnl": float(done["pnl"].sum()) if len(done) else 0.0,
          "pf": float(wins["pnl"].sum() / gross_loss) if gross_loss > 0 else None,
          "open": opens, "upnl": float(opens["pnl"].sum()) if len(opens) else 0.0,
          "invested": float(opens["value"].abs().sum() / equity) if len(opens) and equity else 0.0}
    # per source
    if len(done):
        g = done.groupby("opened_by")
        st["by_source"] = pd.DataFrame({"trades": g.size(), "win": g["pnl"].apply(lambda x: (x > 0).mean()),
                                        "avgR": g["R"].mean(), "pnl": g["pnl"].sum()})
    st["recent"] = done.tail(10)
    return st


def pct(x):
    return "n/a" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x * 100:+.1f}%"


def markdown(reports, now):
    lines = [f"# Paper trading report", "",
             f"Updated {now:%Y-%m-%d %H:%M} New York time by `paper_report.py` (rebuilt from Alpaca every weekday after the close).",
             "Files: `<account>_trades.csv` (closed trades), `<account>_open.csv` (open positions), "
             "`<account>_equity.csv` (account value per day vs SPY).", ""]
    for name, st in reports.items():
        lines += [f"## {st['title']}", ""]
        if "error" in st:
            lines += [f"Not available: {st['error']}", ""]
            continue
        lines += ["| | |", "|---|---|",
                  f"| Account value | ${st['equity']:,.2f} ({pct(st['day'])} today) |",
                  f"| Since {st['since']:%d %b %Y} | {pct(st['ret'])} (SPY {pct(st['spy'])}) |",
                  f"| Worst drop from a high | {pct(st['dd'])} |",
                  f"| Closed trades | {st['trades']}"
                  + (f", win {st['win']:.0%}, avg {st['avgR']:+.2f}R" if st['trades'] and st['avgR'] is not None else "")
                  + (f", profit factor {st['pf']:.2f}" if st['pf'] else "") + f", ${st['pnl']:+,.2f} |",
                  f"| Open positions | {len(st['open'])} (${st['upnl']:+,.2f} not yet realised, {st['invested']:.0%} invested) |", ""]
        if len(st["open"]):
            lines += ["**Open positions**", "", "| Symbol | Opened by | Since | Entry | Now | Stop | P/L | R |",
                      "|---|---|---|---|---|---|---|---|"]
            for r in st["open"].itertuples():
                lines.append(f"| {r.symbol} | {r.opened_by} | {r.since} | {px(r.entry)} | {px(r.now)} | "
                             f"{px(r.stop) if r.stop else '-'} | ${r.pnl:+,.2f} ({r.pct:+.1f}%) | "
                             f"{'' if r.R is None or pd.isna(r.R) else f'{r.R:+.2f}'} |")
            lines.append("")
        if "by_source" in st:
            lines += ["**Closed trades by what opened them**", "", "| Opened by | Trades | Win | Avg R | P/L |",
                      "|---|---|---|---|---|"]
            for src, r in st["by_source"].iterrows():
                lines.append(f"| {src} | {int(r.trades)} | {r.win:.0%} | "
                             f"{'' if pd.isna(r.avgR) else f'{r.avgR:+.2f}'} | ${r.pnl:+,.2f} |")
            lines.append("")
        if len(st["recent"]):
            lines += ["**Last closed trades**", "", "| Symbol | Opened by | In | Out | Entry | Exit | P/L | R |",
                      "|---|---|---|---|---|---|---|---|"]
            for r in st["recent"].iloc[::-1].itertuples():
                lines.append(f"| {r.symbol} | {r.opened_by} | {r.entry_date} | {r.exit_date} | {px(r.entry)} | "
                             f"{px(r.exit)} | ${r.pnl:+,.2f} ({r.pct:+.1f}%) | "
                             f"{'' if r.R is None or pd.isna(r.R) else f'{r.R:+.2f}'} |")
            lines.append("")
    return "\n".join(lines)


def telegram_text(reports, now):
    lines = [f"📒 <b>Paper trading, {now:%a %d %b}</b>"]
    for name, st in reports.items():
        if "error" in st:
            continue
        lines.append(f"\n<b>{st['title']}</b>\n${st['equity']:,.0f} ({pct(st['day'])} today), "
                     f"{pct(st['ret'])} since {st['since']:%d %b} (SPY {pct(st['spy'])})\n"
                     f"{len(st['open'])} open (${st['upnl']:+,.0f}), {st['trades']} closed"
                     + (f", win {st['win']:.0%}, avg {st['avgR']:+.2f}R" if st['trades'] and st['avgR'] is not None else ""))
    return "\n".join(lines)


def main():
    now = pd.Timestamp.now(tz=scanner.ET)
    reports = {}
    for name, title, k, s in ACCOUNTS:
        key, secret = os.environ.get(k), os.environ.get(s)
        if not key or not secret:
            print(f"{title}: no keys ({k}), skipped", flush=True)
            continue
        try:
            reports[name] = account_report(name, title, Api(key, secret))
            print(f"{title}: ${reports[name]['equity']:,.2f}, {reports[name]['trades']} closed trades", flush=True)
        except Exception as e:
            print(f"{title}: report failed ({e})", flush=True)
            reports[name] = {"title": title, "error": str(e)}
    if not reports:
        print("No accounts to report on.")
        return
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "README.md"), "w") as f:
        f.write(markdown(reports, now))
    if os.environ.get("SEND_TELEGRAM", "1") == "1":
        scanner.send_telegram(telegram_text(reports, now))


if __name__ == "__main__":
    main()
