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
    target  the deck's measured move; patterns the deck gives no target for (gaps, candlesticks)
            take profit at NO_TARGET_R x the risk
    orders  bracket order (stop + target, good till cancelled): Alpaca closes the trade, nothing to watch
    size    1% of equity at risk, max 10% of equity per stock, long + short value at most 100% of
            equity (no leverage), max 25 positions in the account, max 8 new trades a day
Every order is tagged pat-deck_<pattern>_<up|down>-..., so the daily paper report shows the result of
each pattern and direction separately.

Needs ALPACA_PATTERN_KEY_ID / ALPACA_PATTERN_SECRET_KEY. Paper-trading address only (trader.Alpaca).
By hand: Actions -> Pattern trader -> Run workflow (dry run = show what it would do).
"""
import datetime as dt
import json
import os
import re
import uuid

import deck_patterns as dp
import pattern_scan
import pattern_trader
import scanner
import trader
from fmt import px

ENABLED = True
RISK_PER_TRADE = 0.01        # 1% of equity at risk per trade
MAX_POSITION_PCT = 0.10      # max 10% of equity in one stock
MAX_GROSS = 1.0              # longs + shorts together at most 1 x equity (no leverage)
MAX_OPEN_POSITIONS = 25      # whole account (old pattern trader's positions count too)
MAX_NEW_PER_DAY = 8
NO_TARGET_R = 2.0            # take profit for patterns without a deck target: 2 x the risk
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
                out.append({"t": t, "df": df, "p": p, "close": float(df["Close"].iat[-1])})
        except Exception as e:
            print(f"{t}: {e!r}", flush=True)
    out.sort(key=lambda f: (FAM_RANK[f["p"]["family"]], f["t"]))
    return out


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
    # every run: report trades that hit their stop or target since the last check
    trader.check_closed(a, book, lambda m, *x: send("🧩 " + m, *x))
    for t in book.values():                            # remember which entries filled
        if t["status"] == "open" and not t.get("filled"):
            try:
                t["filled"] = a.get(f"/orders/{t['order_id']}")["status"] == "filled"
            except Exception:
                pass
    in_window = WINDOW[0] <= (now.hour, now.minute) <= WINDOW[1]
    if not force and (state.get("last_run") == today or not in_window):
        save_state(state)
        return [f"Deck trader: no entries now (last run {state.get('last_run')}, window {in_window})"]
    if not clock["is_open"] and not (force or dry_run):
        save_state(state)
        return ["Deck trader: market closed today"]
    live = clock["is_open"] and not dry_run

    daily = pattern_trader.daily_prices(scanner.get_tickers(), now)
    found = find_signals(daily)
    positions = a.get("/positions")
    held = {p["symbol"] for p in positions}
    pending = {o["symbol"] for o in a.get("/orders", status="open", limit=500)}   # entries not filled yet
    equity = float(acct["equity"])
    gross = sum(abs(float(p.get("market_value") or 0)) for p in positions)
    room = max(0.0, min(MAX_GROSS * equity - gross, float(acct.get("buying_power") or acct["cash"])))
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
        stop = brk["stop"]
        dist = sign * (price - stop)
        if dist <= 0:
            lines.append(f"• {sym}: {what}, skipped (opened beyond the stop)")
            continue
        deck_target = brk.get("target")
        target = deck_target if deck_target is not None else price + sign * NO_TARGET_R * dist
        if sign * (target - price) <= 0.1 * dist:
            lines.append(f"• {sym}: {what}, skipped (opened at or beyond the target)")
            continue
        qty = min(int(equity * RISK_PER_TRADE / dist), int(equity * MAX_POSITION_PCT / price), int(room * 0.95 / price))
        if qty < 1:
            lines.append(f"• {sym}: {what}, skipped (too small or no room left)")
            continue
        stop, target = trader._r2(stop), trader._r2(target)
        risk = qty * dist
        rr = abs(target - price) / dist
        tgt_txt = ("the deck's measured move" if deck_target is not None else f"no deck target: {NO_TARGET_R:g} x the risk")
        msg = (f"🧩 <b>{('BUY' if long else 'SELL SHORT') if live else ('Would buy' if long else 'Would short')} "
               f"{qty} {sym}</b> @ ~{px(price)}: {p['name']} breakout {'↑' if long else '↓'} at yesterday's close\n"
               f"🎯 Target {px(target)} ({(target / price - 1) * 100:+.1f}%, {tgt_txt})\n"
               f"🛑 Stop {px(stop)} ({(stop / price - 1) * 100:+.1f}%, −${risk:,.0f} = {risk / equity:.1%} of equity) · R:R {rr:.1f}\n"
               f'<a href="https://www.tradingview.com/chart/?symbol={sym}&interval=D">Open {sym} on TradingView</a>')
        if live:
            body = {"symbol": sym, "qty": str(qty), "side": "buy" if long else "sell", "type": "market",
                    "time_in_force": "gtc", "order_class": "bracket",
                    "take_profit": {"limit_price": f"{target:.2f}"}, "stop_loss": {"stop_price": f"{stop:.2f}"},
                    "client_order_id": f"pat-deck_{slug(p)}-{uuid.uuid4().hex[:10]}"}
            try:
                order = a.post("/orders", body)
            except Exception as e:
                send(f"⚠️ Deck trade for {sym} ({what}) failed: {e}")
                lines.append(f"• {sym}: {what}, order failed")
                continue
            book[sym] = {"order_id": order["id"], "side": brk["dir"], "qty": qty, "entry": price, "stop": stop,
                         "target": target, "pattern": p["name"], "status": "open", "filled": False,
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
    send(f"🧩 <b>Deck pattern trader</b>{' (dry run)' if not live else ''}: {len(found)} breakouts at yesterday's "
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
