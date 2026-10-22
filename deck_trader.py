"""
Deck pattern trader: paper-trades the breakouts of the Fidelity-deck patterns (deck_patterns.py, the
same detection and the same charts as the pattern scan) in the PATTERN Alpaca paper account, so their
effectiveness can be measured on real prices. Started 21 Oct 2026; replaces pattern_trader.py's entries
(that trader now only manages the positions it already had).

    signal  a pattern the pattern scan sends as a chart (ALERTS in pattern_scan.py: every multi-bar,
            short-term and activated candlestick pattern; not doji / NR4 / inside bar) breaks out on the
            DAILY chart at yesterday's close. Up breakouts are bought (shorts: SHORTS, off since 21 Oct).
    entry   market order at today's open (the first stock scan after 9:30 New York)
    stop    the deck's protective stop: just outside the breakout candle (slide 14); for gaps the pivot,
            for candlestick / pipe / NR patterns the other side of the pattern
    exit    EXIT = "trail" (since 22 Oct 2026, backtest version 6): no take profit; sold in the last 20
            minutes before the close when the price is below the 20-day low (lowest low of the last 20
            completed days), or at the next morning run if that check was missed
            EXIT = "target" (21 Oct only): the deck's measured move as take profit (NO_TARGET_R x the
            risk for patterns without a deck target)
    orders  "trail": market entry + stop order (OTO, good till cancelled); "target": bracket order
    filter  (since 22 Oct 2026) only if the breakout day's close is above the stock's 50-day average,
            and not if the price at the open is more than 2% above the breakout close (MAX_GAP);
            Double bottom, Cup and handle, Pipe bottom and Hanging man aren't traded (SKIP_PATTERNS)
    size    (since 22 Oct 2026, 2x leverage) max 20% of equity per stock - with the deck's tight stops this cap
            sizes ~98% of trades (the 2%-risk rule rarely binds; actual risk is ~0.5% of equity), all
            positions at most 200% of equity (Alpaca's overnight margin limit), so ~10 positions at a time;
            max 25 positions, max 8 new trades a day
    regime  (since 22 Oct 2026) leverage only while SPY is above its 200-day MA (REGIME_MA): below it new
            trades are sized at 1x, and at the afternoon check every deck position is trimmed until the
            whole account is at most 100% invested (backtest 8y: same return, max drop -33% vs -45%)
Every order is tagged pat-deck_<pattern>_<up|down>-..., so the daily paper report shows the result of
each pattern and direction separately.

Needs ALPACA_PATTERN_KEY_ID / ALPACA_PATTERN_SECRET_KEY. Paper-trading address only (trader.Alpaca).
By hand: Actions -> Pattern trader -> Run workflow (dry run = show what it would do).
"""
import datetime as dt
import json
import math
import os
import re
import time
import uuid

import pandas as pd

import deck_patterns as dp
import pattern_scan
import pattern_trader
import scanner
import trader
from fmt import px

ENABLED = True
# 22 Oct 2026 (user): 2x leverage + 50-day MA filter. LEVERAGE multiplies the three limits below, like the
# backtest (8y, S&P 100: +33.0%/yr, worst drop -39.5%, vs 1x no filter +23.0%/yr, -25.5%; SPY +15.4%, -33.8%)
LEVERAGE = 2.0               # 1.0 = no borrowing
RISK_PER_TRADE = 0.01        # x LEVERAGE: % of equity at risk per trade
MAX_POSITION_PCT = 0.10      # x LEVERAGE: max share of equity in one stock
MAX_GROSS = 1.0              # x LEVERAGE: all positions together at most this x equity
MA_FILTER = 50               # only buy if the breakout day's close is above its 50-day average (0 = off)
MAX_GAP = 0.02               # 22 Oct 2026: skip if the price at the open is more than 2% above the breakout close
                             # (backtest 8y, 2x + MA filter: +38.5%/yr vs +33.1% without; None = off)
# 22 Oct 2026 (user): these patterns aren't traded - they lost money in both halves of the 8-year S&P 100
# backtest (2x + MA filter: +34.4%/yr, max drop -37.4% without them vs +33.1%, -39.5% with them)
SKIP_PATTERNS = {"Double bottom", "Cup and handle", "Pipe bottom", "Hanging man"}
REGIME_MA = 200             # 22 Oct 2026: LEVERAGE only while SPY is above its 200-day MA, else 1x (0 = off)
MAX_OPEN_POSITIONS = 25      # whole account (old pattern trader's positions count too)
MAX_NEW_PER_DAY = 8
EXIT = "trail"               # "trail" = backtest version 6 (+20.8%/yr, 8y); "target" = version 2 (+13.4%/yr)
NO_TARGET_R = 2.0            # EXIT "target": take profit for patterns without a deck target, 2 x the risk
EXIT_CHECK_MINUTES = 20      # EXIT "trail": the 20-day-low check runs in the last 20 minutes before the close
SHORTS = False               # 21 Oct 2026: off. Backtest 8y: every down pattern lost money (shorts −17%/yr)
WINDOW = ((9, 30), (10, 30))  # New York time of the morning run (entries)
STATE_FILE = os.environ.get("DECK_TRADER_STATE_FILE", "deck_trader_state.json")
ET = scanner.ET
FAM_RANK = {"multi": 0, "short": 1, "candle": 2}


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    cutoff = (dt.date.today() - dt.timedelta(days=60)).isoformat()
    state["seen"] = {k: v for k, v in state.get("seen", {}).items() if v >= cutoff}
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def find_signals(daily):
    """Charted deck patterns whose breakout candle is the last completed daily candle."""
    out = []
    for t, d in daily.items():
        try:
            df = dp.add_atr(d)
            for p in dp.detect(df):
                brk = p.get("brk")
                if p["stage"] != "breakout" or not brk or brk["bar"] != len(df) - 1:
                    continue
                if pattern_scan.mode_of(p) != "chart" or (brk["dir"] == "SHORT" and not SHORTS):
                    continue
                if p["name"].split(" (")[0] in SKIP_PATTERNS:
                    print(f"{t}: {p['name']} breakout skipped, pattern not traded (SKIP_PATTERNS)", flush=True)
                    continue
                if MA_FILTER and brk["dir"] == "LONG":
                    ma = float(df["Close"].tail(MA_FILTER).mean())
                    if len(df) < MA_FILTER or not float(df["Close"].iat[-1]) > ma:
                        print(f"{t}: {p['name']} breakout skipped, close below the {MA_FILTER}-day MA {ma:.2f}",
                              flush=True)
                        continue
                out.append({"t": t, "df": df, "p": p, "close": float(df["Close"].iat[-1])})
        except Exception as e:
            print(f"{t}: {e!r}", flush=True)
    out.sort(key=lambda f: (FAM_RANK[f["p"]["family"]], f["t"]))
    return out


def regime(a, now, live=False):
    """Is SPY above its REGIME_MA-day average? live=False: yesterday's close (morning, like the backtest's
    signal day); live=True: the current price (afternoon check, near the close). Returns (on, price, ma)."""
    if not REGIME_MA:
        return True, None, None
    try:
        c = pattern_trader.daily_prices(["SPY"], now)["SPY"]["Close"]
        if live:
            price = float(a.live_price("SPY"))
            ma = (float(c.tail(REGIME_MA - 1).sum()) + price) / REGIME_MA
        else:
            price, ma = float(c.iat[-1]), float(c.tail(REGIME_MA).mean())
        return price > ma, price, ma
    except Exception as e:                              # no data: be careful, no leverage
        print(f"Regime check failed ({e!r}): 1x", flush=True)
        return False, None, None


def trim_to_1x(a, book, send, dry_run):
    """Regime off: sell part of every deck position (stop orders re-placed for the rest) so the whole
    account is at most MAX_GROSS x equity."""
    acct, positions = a.get("/account"), a.get("/positions")
    equity = float(acct["equity"])
    gross = sum(abs(float(p.get("market_value") or 0)) for p in positions)
    cap = MAX_GROSS * equity
    mine = [p for p in positions if book.get(p["symbol"], {}).get("status") == "open" and float(p["qty"]) > 0]
    mine_value = sum(float(p["market_value"]) for p in mine)
    if gross <= cap * 1.01 or mine_value <= 0:
        return [f"Regime off: {gross / equity:.0%} invested, nothing to trim"]
    frac = min(1.0, (gross - cap) / mine_value)
    lines = []
    for p in mine:
        sym, qty = p["symbol"], int(float(p["qty"]))
        q = min(qty, math.ceil(qty * frac))
        if q <= 0:
            continue
        t = book[sym]
        if dry_run:
            lines.append(f"• {sym}: would sell {q} of {qty}")
            continue
        try:
            for o in a.get("/orders", status="open", symbols=sym, limit=50):
                a.delete(f"/orders/{o['id']}")         # the stop order holds the shares
            for _ in range(10):
                if not a.get("/orders", status="open", symbols=sym, limit=50):
                    break
                time.sleep(1)
            a.post("/orders", {"symbol": sym, "qty": str(q), "side": "sell", "type": "market", "time_in_force": "day",
                               "client_order_id": f"pat-deck_delever-{uuid.uuid4().hex[:10]}"})
            left = qty - q
            if left > 0:
                so = a.post("/orders", {"symbol": sym, "qty": str(left), "side": "sell", "type": "stop",
                                        "stop_price": f"{t['stop']:.2f}", "time_in_force": "gtc",
                                        "client_order_id": f"pat-deck_stop-{uuid.uuid4().hex[:10]}"})
                t["qty"], t["stop_order_id"] = left, so["id"]      # check_closed reads this stop's fill
            else:
                t["status"] = "closed"
            lines.append(f"• {sym}: sold {q} of {qty}")
        except Exception as e:
            send(f"⚠️ Deck trader: trimming {sym} failed: {e}")
            lines.append(f"• {sym}: trim failed")
    send(f"🧩⚖️ <b>{'Would de-lever' if dry_run else 'De-levered'} to 1x</b>: SPY is below its {REGIME_MA}-day MA. "
         f"Account {gross / equity:.0%} invested → ~{MAX_GROSS:.0%}.\n" + "\n".join(lines))
    return lines


def slug(p):
    return re.sub(r"[^A-Za-z0-9]+", "_", p["name"].split(" (")[0]).strip("_") + \
        ("_up" if p["brk"]["dir"] == "LONG" else "_down")


def run(now=None, force=False, dry_run=False, send=None):
    send = send or scanner.send_telegram
    now = (now or dt.datetime.now(ET)).astimezone(ET)
    if not ENABLED:
        return ["Deck trader: switched off"]
    a = pattern_trader.api()
    if not a.ok:
        return ["Deck trader: no ALPACA_PATTERN_KEY_ID / ALPACA_PATTERN_SECRET_KEY set, nothing to do"]
    state = load_state()
    book = state.setdefault("_trades", {})
    today = now.date().isoformat()
    try:
        clock = a.get("/clock")
        acct = a.get("/account")
    except Exception as e:
        return [f"Deck trader: couldn't reach Alpaca ({e})"]
    for t in book.values():                            # 21 Oct trades were bracket orders (target exit)
        t.setdefault("exit", "target")
    # every run: report trades that hit their stop or target since the last check
    trader.check_closed(a, book, lambda m, *x: send("🧩 " + m, *x))
    for t in book.values():                            # remember which entries filled
        if t["status"] == "open" and not t.get("filled"):
            try:
                t["filled"] = a.get(f"/orders/{t['order_id']}")["status"] == "filled"
            except Exception:
                pass
    in_window = WINDOW[0] <= (now.hour, now.minute) <= WINDOW[1]
    if not force and not (state.get("last_run") != today and in_window):
        # afternoon: the 20-day-low exits, once, in the last EXIT_CHECK_MINUTES minutes before the close
        try:
            close_in = (pd.Timestamp(clock["next_close"]) - pd.Timestamp(clock["timestamp"])).total_seconds() / 60
        except Exception:
            close_in = 999
        if clock["is_open"] and close_in <= EXIT_CHECK_MINUTES and state.get("last_exit_run") != today:
            trail = [s for s, t in book.items() if t.get("exit") == "trail" and t["status"] == "open"]
            lines = []
            if trail:
                daily = pattern_trader.daily_prices([s.replace(".", "-") for s in trail], now)
                lines = pattern_trader.trend_exits(a, book, a.get("/positions"), daily, send, dry_run, today=True)
            on, spy, ma = regime(a, now, live=True)
            if not on and LEVERAGE > 1:
                lines += trim_to_1x(a, book, send, dry_run)
            state["last_exit_run"] = today
            save_state(state)
            return ["Deck trader: 20-day-low check"] + lines
        save_state(state)
        return [f"Deck trader: no entries now (last run {state.get('last_run')}, window {in_window})"]
    if not clock["is_open"] and not (force or dry_run):
        save_state(state)
        return ["Deck trader: market closed today"]
    live = clock["is_open"] and not dry_run

    daily = pattern_trader.daily_prices(scanner.get_tickers(), now)
    # a missed afternoon check: sell trail positions whose close yesterday was below the 20-day low
    pattern_trader.trend_exits(a, book, a.get("/positions"), daily, send, not live)
    found = find_signals(daily)
    positions = a.get("/positions")
    held = {p["symbol"] for p in positions}
    pending = {o["symbol"] for o in a.get("/orders", status="open", limit=500)}   # entries not filled yet
    equity = float(acct["equity"])
    gross = sum(abs(float(p.get("market_value") or 0)) for p in positions)
    bp = float(acct.get("regt_buying_power") or acct.get("buying_power") or acct["cash"])   # overnight (2x) limit
    on, spy, ma = regime(a, now)
    lev = LEVERAGE if on else min(LEVERAGE, 1.0)
    room = max(0.0, min(MAX_GROSS * lev * equity - gross, bp))
    slots = max(0, MAX_OPEN_POSITIONS - len(held | pending))
    placed, lines = 0, []
    for f in found:
        sym, p = f["t"].replace("-", "."), f["p"]
        brk = p["brk"]
        long = brk["dir"] == "LONG"
        key = f"{f['t']}|{p['name']}|{brk['dir']}|{today}"
        if key in state.setdefault("seen", {}):
            continue
        if live:                                       # a dry run must not hide it from the real run
            state["seen"][key] = today
        what = f"{p['name']} {'↑' if long else '↓'}"
        if sym in held or sym in pending:
            lines.append(f"• {sym}: {what}, already in the account")
            continue
        if placed >= MAX_NEW_PER_DAY or slots <= 0:
            lines.append(f"• {sym}: {what}, skipped (position limit)")
            continue
        if not long:
            try:
                asset = a.get(f"/assets/{sym}")
                if not (asset.get("shortable") and asset.get("easy_to_borrow")):
                    lines.append(f"• {sym}: {what}, skipped (Alpaca can't short it)")
                    continue
            except Exception:
                pass
        try:
            price = a.live_price(sym)
        except Exception:
            price = f["close"]
        sign = 1 if long else -1
        if MAX_GAP is not None and long and price > f["close"] * (1 + MAX_GAP):
            lines.append(f"• {sym}: {what}, skipped (opened {(price / f['close'] - 1) * 100:+.1f}% above the "
                         f"breakout close {px(f['close'])}, limit +{MAX_GAP:.0%})")
            continue
        stop = brk["stop"]
        dist = sign * (price - stop)
        if dist <= 0:
            lines.append(f"• {sym}: {what}, skipped (opened beyond the stop)")
            continue
        deck_target = brk.get("target")
        target = deck_target if deck_target is not None else price + sign * NO_TARGET_R * dist
        if EXIT == "target" and sign * (target - price) <= 0.1 * dist:
            lines.append(f"• {sym}: {what}, skipped (opened at or beyond the target)")
            continue
        qty = min(int(equity * RISK_PER_TRADE * lev / dist), int(equity * MAX_POSITION_PCT * lev / price),
                  int(room * 0.95 / price))
        if qty < 1:
            lines.append(f"• {sym}: {what}, skipped (too small or no room left)")
            continue
        stop, target = trader._r2(stop), trader._r2(target)
        risk = qty * dist
        rr = abs(target - price) / dist
        tgt_txt = ("the deck's measured move" if deck_target is not None else f"no deck target: {NO_TARGET_R:g} x the risk")
        if EXIT == "trail":
            low20 = pattern_trader.trail_level(daily[f["t"]], today=True) if f["t"] in daily else None
            exit_txt = (f"🎯 No target: sells when the price is below the 20-day low (now {px(low20 or 0)}) "
                        f"in the last 20 min before the close")
        else:
            exit_txt = f"🎯 Target {px(target)} ({(target / price - 1) * 100:+.1f}%, {tgt_txt}) · R:R {rr:.1f}"
        msg = (f"🧩 <b>{('BUY' if long else 'SELL SHORT') if live else ('Would buy' if long else 'Would short')} "
               f"{qty} {sym}</b> @ ~{px(price)}: {p['name']} breakout {'↑' if long else '↓'} at yesterday's close\n"
               f"{exit_txt}\n"
               f"🛑 Stop {px(stop)} ({(stop / price - 1) * 100:+.1f}%, −${risk:,.0f} = {risk / equity:.1%} of equity)\n"
               f'<a href="https://www.tradingview.com/chart/?symbol={sym}&interval=D">Open {sym} on TradingView</a>')
        if live:
            body = {"symbol": sym, "qty": str(qty), "side": "buy" if long else "sell", "type": "market",
                    "time_in_force": "gtc", "stop_loss": {"stop_price": f"{stop:.2f}"},
                    "client_order_id": f"pat-deck_{slug(p)}-{uuid.uuid4().hex[:10]}"}
            if EXIT == "target":
                body.update(order_class="bracket", take_profit={"limit_price": f"{target:.2f}"})
            else:
                body["order_class"] = "oto"                    # stop only: the 20-day-low check sells
            try:
                order = a.post("/orders", body)
            except Exception as e:
                send(f"⚠️ Deck trade for {sym} ({what}) failed: {e}")
                lines.append(f"• {sym}: {what}, order failed")
                continue
            book[sym] = {"order_id": order["id"], "side": brk["dir"], "qty": qty, "entry": price, "stop": stop,
                         "target": target if EXIT == "target" else None, "exit": EXIT,
                         "pattern": p["name"], "status": "open", "filled": False,
                         "opened": dt.datetime.utcnow().isoformat()}
        try:
            chart = pattern_scan.draw(f["t"], f["df"], p)
        except Exception:
            chart = None
        send(("" if live else "🧪 ") + msg, chart)
        placed += 1
        slots -= 1
        room -= qty * price
        lines.append(f"• {sym}: {what}, {'traded' if live else 'would trade'} {qty}")
    if live:
        state["last_run"] = today
    save_state(state)
    n_pos = len(a.get("/positions"))
    reg = ("" if not REGIME_MA else f", SPY {'above' if on else 'BELOW'} its {REGIME_MA}-day MA"
           + (f" ({px(spy)} vs {px(ma)})" if spy else "") + ("" if on else ": 1x"))
    send(f"🧩 <b>Deck pattern trader</b>{' (dry run)' if not live else ''} ({LEVERAGE:g}x{reg}"
         f"{f', {MA_FILTER}-day MA filter' if MA_FILTER else ''}"
         f"{f', no entry >{MAX_GAP:.0%} above the breakout' if MAX_GAP is not None else ''}): "
         f"{len(found)} breakouts at yesterday's "
         f"close, {placed} {'trades' if live else 'would-be trades'}. Account ${equity:,.0f}, {n_pos} positions."
         + ("\n" + "\n".join(lines) if lines else ""))
    return lines


def main():
    force = os.environ.get("PATTERN_FORCE") == "1"
    dry = os.environ.get("DRY_RUN") == "1"
    for line in run(force=force, dry_run=dry):
        print(line, flush=True)


if __name__ == "__main__":
    main()
