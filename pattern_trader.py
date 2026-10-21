"""
Pattern paper trader: trades your selected chart patterns (patterns.SELECTED["stocks"]) on the S&P 100
in a SEPARATE Alpaca paper account, so its results never mix with the breakout bot's.

The rules are the backtest's version 7 ("selected patterns, mixed exits"):
    signal  a selected UP pattern breaks out on the DAILY chart at yesterday's close
            (same detection as the alerts: patterns.py, incl. the 1.3x volume check)
    entry   market order at today's open (the first scan of the day, ~9:30-10:30 New York)
    stop    the pattern's own stop, sent with the entry (good-till-cancelled, Alpaca handles it)
    exit    per pattern (EXIT_BY_PATTERN): ABCD sells at the pattern's target (bracket order);
            the others have no target and are sold when the price is below the 20-day low in the
            last 20 minutes before the close (like the backtest), or at the next open if that check
            was missed and yesterday's close was below it
    size    1% of equity at risk between entry and stop, max 20% of equity per stock,
            max 10 positions, max 3 new trades a day, never more than 100% invested (no borrowing)

It runs inside the S&P 500 scan: once in the morning (first scan after 9:30 New York: entries) and once
in the last 20 minutes before the close (trend exits), or by hand:
Actions -> Pattern trader -> Run workflow (with "dry run" to see what it would do).

Needs its own paper account keys as repository secrets: ALPACA_PATTERN_KEY_ID, ALPACA_PATTERN_SECRET_KEY.
Without them it does nothing. Safety: paper-trading address only (trader.Alpaca), no real money.
"""
import datetime as dt
import json
import os
import re
import time
import uuid

import pandas as pd

import patterns
import rules
import scanner
import trader
from fmt import px

ENABLED = True               # False = never trade (the scan step then does nothing)
# 21 Oct 2026: replaced by deck_trader.py (Fidelity-deck patterns) in the same account. This trader no
# longer opens trades; it only manages the positions it already opened (stops are in Alpaca, trend exits here).
NEW_ENTRIES = False
MARKET = "stocks"            # which selection in patterns.SELECTED to trade
EXIT_BY_PATTERN = {"ABCD": "target"}   # pattern name -> "target"; every other pattern -> "trail"
TRAIL_DAYS = 20              # trailing exit: sell when yesterday's close is below the 20-day low
RISK_PER_TRADE = 0.01        # 1% of equity at risk per trade
MAX_POSITION_PCT = 0.20      # max 20% of equity in one stock
MAX_OPEN_POSITIONS = 10
MAX_NEW_PER_DAY = 3
MAX_INVESTED = 1.0           # no borrowing
WINDOW = ((9, 30), (10, 30))  # New York time in which the morning run (entries) happens
EXIT_CHECK_MINUTES = 20      # trend exits are checked in the last 20 minutes before the close
STATE_FILE = os.environ.get("PATTERN_STATE_FILE", "pattern_trader_state.json")
HISTORY_DAYS = 420           # daily candles downloaded (patterns look back ~1 year)
ET = scanner.ET


def api():
    a = trader.Alpaca()
    key, secret = os.environ.get("ALPACA_PATTERN_KEY_ID"), os.environ.get("ALPACA_PATTERN_SECRET_KEY")
    a.ok = bool(key and secret)
    a.h = {"APCA-API-KEY-ID": key or "", "APCA-API-SECRET-KEY": secret or ""}
    return a


def exit_mode(name):
    return EXIT_BY_PATTERN.get(name, "trail")


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    cutoff = (dt.date.today() - dt.timedelta(days=400)).isoformat()
    state["seen"] = {k: v for k, v in state.get("seen", {}).items() if v >= cutoff}
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def daily_prices(tickers, now):
    """Completed daily candles (today left out), Alpaca's complete feed; Yahoo for anything missing."""
    end = now - dt.timedelta(minutes=scanner.DATA_DELAY_MINUTES)
    os.environ.setdefault("ALPACA_KEY_ID", os.environ.get("ALPACA_PATTERN_KEY_ID", ""))
    os.environ.setdefault("ALPACA_SECRET_KEY", os.environ.get("ALPACA_PATTERN_SECRET_KEY", ""))
    daily = scanner.alpaca_bars(tickers, "1Day", end - dt.timedelta(days=HISTORY_DAYS), end)
    missing = [t for t in tickers if len(daily.get(t, ())) < 250]
    if missing:
        daily.update(scanner.download(missing, "2y", "1d"))
    today = now.astimezone(ET).date()
    out = {}
    for t, d in daily.items():
        d = d.copy()
        idx = d.index.tz_convert(ET).tz_localize(None) if d.index.tz is not None else d.index
        d.index = pd.DatetimeIndex(idx).normalize()
        d = d[d.index.date < today]
        if len(d) >= 250:
            out[t] = d[["Open", "High", "Low", "Close", "Volume"]]
    return out


def find_breakouts(daily, state):
    """Selected UP patterns that broke out at the last completed daily close (each pattern once)."""
    found = []
    for t, d in daily.items():
        try:
            dd = scanner.add_indicators(d.copy())
            r = dd.iloc[-1]
            vol = float(r.Volume / r.VolAvg20) if r.VolAvg20 else None
            pats, _ = patterns.find_patterns(dd, "daily", float(dd["Close"].iat[-1]), float(dd["Close"].iat[-2]), vol)
            for pt in pats:
                if pt["stage"] != "breakout" or pt["dir"] != "LONG" or pt["id"] in state.get("seen", {}):
                    continue
                if not patterns.is_selected(pt["name"], "LONG", MARKET):
                    continue
                frame = rules.add_strategy_indicators(dd, rules.SQUEEZE_LOOKBACK["daily"], **rules.SETTINGS["daily"])
                found.append({"t": t, "p": pt, "frame": frame.tail(patterns.SETTINGS["daily"]["lookback"]),
                              "close": float(dd["Close"].iat[-1])})
        except Exception as e:
            print(f"{t}: {e}", flush=True)
    return found


def trail_level(d, today=False):
    """today=False: lowest low of the TRAIL_DAYS days before the last completed day (judges yesterday's close).
    today=True: lowest low of the last TRAIL_DAYS completed days (judges today's price)."""
    if len(d) <= TRAIL_DAYS:
        return None
    return float(d["Low"].iloc[-TRAIL_DAYS:].min() if today else d["Low"].iloc[-TRAIL_DAYS - 1:-1].min())


def trend_exits(a, book, positions, daily, send, dry_run, today=False):
    """Trailing-exit positions: sell when the price is below the 20-day low.
    today=False (morning): yesterday's close vs the 20-day low before it (a missed afternoon check).
    today=True (last 20 minutes): today's live price vs the low of the last 20 days."""
    log = []
    for p in positions:
        sym = p["symbol"]
        t = book.get(sym)
        if not t or t.get("status") != "open":
            continue                                   # not this trader's position (deck_trader shares the account)
        if t.get("exit") == "target":
            continue                                   # bracket order: stop and target handle it
        qty = int(float(p["qty"]))
        d = daily.get(sym.replace(".", "-"))
        if qty <= 0 or d is None:
            continue
        low = trail_level(d, today)
        try:
            live = a.live_price(sym)
        except Exception:
            live = float(p.get("current_price") or d["Close"].iat[-1])
        close = live if today else float(d["Close"].iat[-1])
        if low is None or close >= low:
            log.append(f"{sym}: trend intact ({px(close)} vs {TRAIL_DAYS}-day low {px(low or 0)})")
            continue
        entry = float(p["avg_entry_price"])
        price = live
        pnl = (price - entry) * qty
        when = "is" if today else "closed yesterday"
        text = (f"{sym} {when} {px(close)}, below the {TRAIL_DAYS}-day low {px(low)}: {qty} @ {px(entry)} → ~{px(price)}  "
                f"<b>{'+' if pnl >= 0 else '−'}${abs(pnl):,.2f}</b>")
        if dry_run:
            send(f"🧪🧩 Would sell (trend exit): {text}")
            continue
        try:
            for o in a.get("/orders", status="open", symbols=sym, limit=50):
                a.delete(f"/orders/{o['id']}")         # the stop order holds the shares
            for _ in range(10):
                if not a.get("/orders", status="open", symbols=sym, limit=50):
                    break
                time.sleep(1)
            a.delete(f"/positions/{sym}")
            if t:
                t["status"] = "closed"
            send(f"🧩📉 Pattern trade, trend exit: {text}")
            log.append(f"{sym}: trend exit")
        except Exception as e:
            send(f"⚠️ Pattern trader: trend exit for {sym} failed: {e}")
    return log


def run(now=None, force=False, dry_run=False, send=None):
    send = send or scanner.send_telegram
    now = now or dt.datetime.now(ET)
    log = []
    if not ENABLED:
        return ["Pattern trader: switched off (ENABLED = False)"]
    a = api()
    if not a.ok:
        return ["Pattern trader: no ALPACA_PATTERN_KEY_ID / ALPACA_PATTERN_SECRET_KEY set, nothing to do"]
    state = load_state()
    today = now.astimezone(ET).date().isoformat()
    in_window = WINDOW[0] <= (now.astimezone(ET).hour, now.astimezone(ET).minute) <= WINDOW[1]
    late = now.astimezone(ET).hour >= 15                # possibly the last minutes before the close
    if not force and (state.get("last_run") == today or not in_window) and \
            not (late and state.get("last_exit_run") != today):
        return [f"Pattern trader: not now (last run {state.get('last_run')}, window {in_window})"]
    try:
        clock = a.get("/clock")
        acct = a.get("/account")
    except Exception as e:
        return [f"Pattern trader: couldn't reach Alpaca ({e})"]
    if not force and not (state.get("last_run") != today and in_window):
        # afternoon: trend exits only, in the last EXIT_CHECK_MINUTES minutes before the close
        try:
            close_in = (pd.Timestamp(clock["next_close"]) - pd.Timestamp(clock["timestamp"])).total_seconds() / 60
        except Exception:
            close_in = 999
        if not clock["is_open"] or close_in > EXIT_CHECK_MINUTES:
            return [f"Pattern trader: not now ({close_in:.0f} min to the close)"]
        book = state.setdefault("_trades", {})
        daily = daily_prices([p["symbol"].replace(".", "-") for p in a.get("/positions")], now)
        trader.check_closed(a, book, lambda m, *x: send("🧩 " + m, *x))
        lines = trend_exits(a, book, a.get("/positions"), daily, send, dry_run, today=True)
        state["last_exit_run"] = today
        save_state(state)
        return lines
    if not clock["is_open"] and not (force or dry_run):
        return ["Pattern trader: market closed today"]
    live = clock["is_open"] and not dry_run

    tickers = scanner.get_tickers()
    daily = daily_prices(tickers, now)
    book = state.setdefault("_trades", {})
    trader.check_closed(a, book, lambda m, *x: send("🧩 " + m, *x))
    positions = a.get("/positions")
    log += trend_exits(a, book, positions, daily, send, not live)

    found = find_breakouts(daily, state) if NEW_ENTRIES else []
    log.append(f"{len(found)} selected pattern breakouts at the last close")
    positions = a.get("/positions")
    held = {p["symbol"] for p in positions}
    pending = {o["symbol"] for o in a.get("/orders", status="open", limit=500) if o["side"] == "buy"}
    equity = float(acct["equity"])
    invested = sum(abs(float(p.get("market_value") or 0)) for p in positions)
    room = max(0.0, min(MAX_INVESTED * equity - invested, float(acct.get("non_marginable_buying_power") or acct["cash"])))
    slots = max(0, MAX_OPEN_POSITIONS - len(held | pending))
    placed, lines = 0, []
    found.sort(key=lambda f: f["p"]["name"] != "ABCD")            # ABCD (best record) first
    for f in found:
        sym, pt = f["t"].replace("-", "."), f["p"]
        mode = exit_mode(pt["name"])
        if live:                                       # a dry run must not hide it from the real run
            state.setdefault("seen", {})[pt["id"]] = today
        if sym in held or sym in pending:
            lines.append(f"• {sym}: {pt['name']}, already held")
            continue
        if placed >= MAX_NEW_PER_DAY or slots <= 0:
            lines.append(f"• {sym}: {pt['name']}, skipped (position limit)")
            continue
        try:
            price = a.live_price(sym)
        except Exception:
            price = f["close"]
        stop, target = pt["stop"], pt["target"]
        dist = price - stop
        if dist <= 0 or (mode == "target" and price >= target):
            lines.append(f"• {sym}: {pt['name']}, skipped (opened {'below the stop' if dist <= 0 else 'above the target'})")
            continue
        plan = trader.plan_trade("LONG", price, dist / trader.STOP_ATR, equity, room)
        if plan:                                       # same sizing as the bot, with this account's limits
            qty = min(plan["qty"], int(equity * RISK_PER_TRADE / dist), int(equity * MAX_POSITION_PCT / price))
        if not plan or qty < 1:
            lines.append(f"• {sym}: {pt['name']}, skipped (too small or no money left)")
            continue
        stop = trader._r2(stop)
        tgt = trader._r2(target) if mode == "target" else None
        risk = qty * (price - stop)
        exit_txt = (f"🎯 Target {px(tgt)} ({(tgt / price - 1) * 100:+.1f}%, the pattern's measured move)" if tgt else
                    f"🎯 No target: sells when the close is below the {TRAIL_DAYS}-day low (now {px(trail_level(daily[f['t']]) or 0)})")
        msg = (f"🧩 <b>{'BUY' if live else 'Would buy'} {qty} {sym}</b> @ ~{px(price)}: {pt['name']} breakout (daily, "
               f"yesterday's close)\n{exit_txt}\n🛑 Stop {px(stop)} ({(stop / price - 1) * 100:+.1f}%, −${risk:,.0f} = "
               f"{risk / equity:.1%} of equity)\n"
               f'<a href="https://www.tradingview.com/chart/?symbol={sym}&interval=D">Open {sym} on TradingView</a>')
        if live:
            slug = re.sub(r"[^A-Za-z0-9]+", "_", pt["name"]).strip("_")
            body = {"symbol": sym, "qty": str(qty), "side": "buy", "type": "market", "time_in_force": "gtc",
                    "stop_loss": {"stop_price": f"{stop:.2f}"},
                    "client_order_id": f"pat-{slug}-{uuid.uuid4().hex[:12]}"}   # for the daily report
            if tgt:
                body.update(order_class="bracket", take_profit={"limit_price": f"{tgt:.2f}"})
            else:
                body["order_class"] = "oto"
            try:
                order = a.post("/orders", body)
            except Exception as e:
                send(f"⚠️ Pattern trade for {sym} failed: {e}")
                continue
            book[sym] = {"order_id": order["id"], "side": "LONG", "qty": qty, "entry": price, "stop": stop,
                         "target": tgt, "exit": mode, "pattern": pt["name"], "status": "open", "filled": False,
                         "opened": dt.datetime.utcnow().isoformat()}
        try:
            chart = scanner.draw_chart(f["t"], f["frame"], f"{f['t']} daily: {pt['name']} (breakout)",
                                       show_sma=True, pattern=pt)
        except Exception:
            chart = None
        send(("" if live else "🧪 ") + msg, chart) if chart else send(("" if live else "🧪 ") + msg)
        placed += 1
        slots -= 1
        room -= qty * price
        lines.append(f"• {sym}: {pt['name']}, {'bought' if live else 'would buy'} {qty}")
    for t in book.values():                            # remember which entries filled
        if t["status"] == "open" and not t.get("filled"):
            try:
                t["filled"] = a.get(f"/orders/{t['order_id']}")["status"] == "filled"
            except Exception:
                pass
    if live:
        state["last_run"] = today
    save_state(state)
    n_pos = len(a.get("/positions"))
    if not NEW_ENTRIES:
        return log + ["Pattern trader: entries off (replaced by deck_trader.py), exits only"]
    send(f"🧩 <b>Pattern trader</b>{' (dry run)' if not live else ''}: {len(found)} selected breakouts at "
         f"yesterday's close ({patterns.selection_text(MARKET)}), {placed} {'trades' if live else 'would-be trades'}. "
         f"Account ${equity:,.0f}, {n_pos} positions." + ("\n" + "\n".join(lines) if lines else ""))
    return log + lines


def main():
    force = os.environ.get("PATTERN_FORCE") == "1"
    dry = os.environ.get("DRY_RUN") == "1"
    for line in run(force=force, dry_run=dry):
        print(line, flush=True)


if __name__ == "__main__":
    main()
