"""
Paper trading with Alpaca. Turns STRONG (⭐) signals into orders in your PAPER account:
    entry  = market order (whole shares)
    stop   = entry -/+ STOP_ATR x daily ATR
    exit   = EXIT_MODE "trail" (default, the best version in the backtest): no fixed target; at the
             last check of the day (~15:45 New York) the bot sells any position whose price is below
             its 20-day low, so winners can run as long as the trend lasts
             EXIT_MODE "target": take profit at REWARD_RISK x the stop distance (2:1)
The stop (and target) are sent together with the entry, so Alpaca closes the trade at the stop by
itself even when the bot isn't running. Orders stay active until filled (good-till-cancelled).

Position size: each trade risks RISK_PER_TRADE of the account equity between entry and stop,
capped at MAX_POSITION_PCT of equity per stock.

Safety: this file ONLY talks to Alpaca's paper-trading address. It cannot place real-money trades.
"""
import datetime as dt
import html
import re
import math
import os
import time
import uuid

import pandas as pd
import requests

import pine

TRADING_ENABLED = True       # False = alerts only, no paper trades
SEND_PINE_SCRIPT = True      # also send a TradingView Pine script that draws the trade
ALLOW_SHORTS = False         # sell short on STRONG SHORT signals (the backtest: shorts lost money)
RISK_PER_TRADE = 0.01        # risk 1% of equity per trade
STOP_ATR = 2.0               # stop-loss distance = 2 x daily ATR
EXIT_MODE = "trail"          # "trail" = sell when the price falls below the 20-day low; "target" = 2:1 target
TRAIL_DAYS = 20              # trailing exit: the lowest low of the last 20 days
EXIT_CHECK_MINUTES = 20      # trailing exits are checked by the scan in the last 20 minutes before the close
REWARD_RISK = 2.0            # target = 2 x the stop distance (EXIT_MODE "target" only)
MAX_POSITION_PCT = 0.20      # never put more than 20% of equity in one stock
MAX_OPEN_POSITIONS = 10      # don't open new trades beyond this
MAX_INVESTED = 1.0           # total in positions at most 1.0 x equity = no borrowing (2.0 = up to 2x leverage)
MAX_NEW_PER_RUN = 3          # at most 3 new trades per check

PAPER_URL = "https://paper-api.alpaca.markets/v2"     # paper trading only
DATA_URL = "https://data.alpaca.markets/v2"


class Alpaca:
    def __init__(self):
        key, secret = os.environ.get("ALPACA_KEY_ID"), os.environ.get("ALPACA_SECRET_KEY")
        self.ok = bool(key and secret)
        self.h = {"APCA-API-KEY-ID": key or "", "APCA-API-SECRET-KEY": secret or ""}

    def get(self, path, base=PAPER_URL, **params):
        r = requests.get(f"{base}{path}", headers=self.h, params=params, timeout=20)
        r.raise_for_status()
        return r.json()

    def delete(self, path):
        r = requests.delete(f"{PAPER_URL}{path}", headers=self.h, timeout=20)
        if not r.ok and r.status_code != 404:
            raise RuntimeError(f"{r.status_code} {r.text[:300]}")
        return r.json() if r.content else {}

    def post(self, path, body):
        r = requests.post(f"{PAPER_URL}{path}", headers=self.h, json=body, timeout=20)
        if not r.ok:
            raise RuntimeError(f"{r.status_code} {r.text[:300]}")
        return r.json()

    def live_price(self, symbol):
        """Real-time last trade from the free IEX feed (the scan's prices are 15+ min old)."""
        js = self.get(f"/stocks/{symbol.replace('-', '.')}/trades/latest", base=DATA_URL, feed="iex")
        return float(js["trade"]["p"])


def _r2(x):
    return round(x + 1e-9, 2)


def plan_trade(direction, price, atr, equity, buying_power):
    """Shares, stop and target for one trade. Returns None if the trade is too small."""
    stop_dist = STOP_ATR * atr
    if stop_dist <= 0 or price <= 0:
        return None
    qty = math.floor(equity * RISK_PER_TRADE / stop_dist)
    qty = min(qty, math.floor(equity * MAX_POSITION_PCT / price), math.floor(buying_power * 0.95 / price))
    if qty < 1:
        return None
    sign = 1 if direction == "LONG" else -1
    stop = _r2(price - sign * stop_dist)
    target = _r2(price + sign * REWARD_RISK * stop_dist) if EXIT_MODE == "target" else None
    return {"qty": qty, "stop": stop, "target": target, "risk": qty * stop_dist,
            "risk_pct": qty * stop_dist / equity}


def trade_timeframe(item):
    """Chart the trade on the timeframe of its strong signal: 'daily' or the intraday chart."""
    strong = [e for e in item.get("events", []) if e.get("strong")]
    return "daily" if any(e.get("tf") == "daily" for e in strong) else "intraday"


def reasons(item, direction):
    """Plain-language reasons for the trade, strongest first (also written on the chart)."""
    ev = sorted(item.get("events", []), key=lambda e: not e.get("strong"))
    out = [e["text"] for e in ev if e.get("text") and e.get("dir") in (direction, "WATCH")]
    # pattern lines carry the pattern's own entry/stop/target; keep only the breakout level so
    # they can't be confused with the trade's levels
    out = [re.sub(r": entry ([\d.]+), stop [\d.]+, target [\d.]+", r" through \1", r) for r in out]
    ctx = item.get("ctx", {})
    if ctx.get("sma200"):
        side = "above" if ctx["price"] > ctx["sma200"] else "below"
        pct = (ctx["price"] / ctx["sma200"] - 1) * 100
        out.append(f"Trend: price {side} the 200-day MA {ctx['sma200']:.2f} ({pct:+.1f}%)")
    return out[:6]


def tradingview_link(sym, tf):
    interval = "D" if tf == "daily" else "15"
    return f"https://www.tradingview.com/chart/?symbol={sym}&interval={interval}"


def twenty_day_low(daily, today):
    """Lowest low of the last TRAIL_DAYS completed days (today's candle left out)."""
    if daily is None or not len(daily):
        return None
    idx = daily.index.tz_convert("America/New_York") if daily.index.tz is not None else daily.index
    past = daily[pd.Index(idx.date) < today]
    return float(past["Low"].tail(TRAIL_DAYS).min()) if len(past) >= TRAIL_DAYS else None


def exit_line(plan, price, sign):
    if plan.get("target") is not None:
        reward = abs(plan["target"] - price) * plan["qty"]
        return f"🎯 Take profit {plan['target']:.2f} ({sign * (plan['target'] / price - 1) * 100:+.1f}%, +${reward:,.0f})"
    trail = f" (now {plan['trail']:.2f})" if plan.get("trail") else ""
    return f"🎯 No fixed target: sells when the price is below the {TRAIL_DAYS}-day low{trail} at the 15:45 check"


def trade_message(head, sym, direction, price, plan, why, tf):
    sign = 1 if direction == "LONG" else -1
    lines = [f"{head}: <b>{'BUY' if direction == 'LONG' else 'SELL SHORT'} {plan['qty']} {sym}</b> @ ~{price:.2f}",
             exit_line(plan, price, sign),
             f"🛑 Stop loss {plan['stop']:.2f} ({sign * (plan['stop'] / price - 1) * 100:+.1f}%, −${plan['risk']:,.0f} "
             f"= {plan.get('risk_pct', RISK_PER_TRADE):.1%} of equity)",
             "<b>Why:</b>"] + [f"• {r}" for r in why]
    lines.append(f'<a href="{tradingview_link(sym, tf)}">Open {sym} on TradingView ({"daily" if tf == "daily" else "15-min"})</a>')
    return "\n".join(lines)


def _strong_direction(item):
    dirs = {e["dir"] for e in item["events"] if e.get("strong")}
    return dirs.pop() if len(dirs) == 1 else None      # skip if signals disagree


def check_closed(api, book, send):
    """Tell the user about trades closed since the last run (stop or target hit)."""
    try:
        open_syms = {p["symbol"] for p in api.get("/positions")}
    except Exception as e:
        print(f"Paper trading: couldn't read positions ({e})", flush=True)
        return
    for sym, t in list(book.items()):
        if t.get("status") != "open" or sym in open_syms:
            continue
        if not t.get("filled"):                         # entry never filled yet: still pending?
            try:
                o = api.get(f"/orders/{t['order_id']}")
            except Exception:
                continue
            if o["status"] in ("new", "accepted", "pending_new", "partially_filled", "held"):
                continue
            if o["status"] in ("canceled", "expired", "rejected"):
                t["status"] = "closed"
                send(f"📕 Paper trade {sym}: entry order {o['status']}, no position opened")
                continue
        exit_px, how = None, "closed"
        try:
            o = api.get(f"/orders/{t['order_id']}", nested="true")
            for leg in o.get("legs") or []:
                if leg["status"] == "filled":
                    exit_px = float(leg["filled_avg_price"])
                    how = "🎯 Target hit" if leg["type"] == "limit" else "🛑 Stop hit"
            if o.get("order_class") == "oto" and exit_px is None and o.get("legs"):
                leg = o["legs"][0]
                if leg.get("filled_avg_price"):
                    exit_px, how = float(leg["filled_avg_price"]), "🛑 Stop hit"
            if exit_px is None and t.get("stop_order_id"):       # stop re-placed after a partial sale
                so = api.get(f"/orders/{t['stop_order_id']}")
                if so.get("filled_avg_price"):
                    exit_px, how = float(so["filled_avg_price"]), "🛑 Stop hit"
            entry = float(o["filled_avg_price"]) if o.get("filled_avg_price") else t["entry"]
        except Exception:
            entry = t["entry"]
        t["status"] = "closed"
        if exit_px is None:
            send(f"📕 Paper trade {sym} closed (no longer in the account)")
            continue
        sign = 1 if t["side"] == "LONG" else -1
        pnl = (exit_px - entry) * t["qty"] * sign
        send(f"{how}: {sym} {t['side']} {t['qty']} @ {entry:.2f} → {exit_px:.2f}  "
             f"<b>{'+' if pnl >= 0 else '−'}${abs(pnl):,.2f}</b>")
    # keep the book small: forget trades closed more than 10 days ago
    cutoff = (dt.datetime.utcnow() - dt.timedelta(days=10)).isoformat()
    for sym in [s for s, t in book.items() if t["status"] == "closed" and t["opened"] < cutoff]:
        del book[sym]


def trend_exits(api, book, daily, send, test_mode=False):
    """Trailing exit: sell long positions whose price is below their 20-day low.
    Run by the last scan of the day. daily: {ticker: daily candles} from the scanner."""
    log = []
    today = pd.Timestamp.now(tz="America/New_York").date()
    try:
        positions = api.get("/positions")
    except Exception as e:
        return [f"Trend exits: couldn't read positions ({e})"]
    for p in positions:
        if p.get("asset_class") == "crypto":
            continue                                    # crypto positions belong to crypto_trader.py
        sym, qty = p["symbol"], int(float(p["qty"]))
        if qty <= 0:
            continue                                    # shorts keep their own stop/target
        low20 = twenty_day_low((daily or {}).get(sym.replace(".", "-")), today)
        if low20 is None:
            log.append(f"{sym}: no daily prices for the trend exit check")
            continue
        try:
            price = api.live_price(sym)
        except Exception:
            price = float(p.get("current_price") or 0)
        if not price or price >= low20:
            log.append(f"{sym}: trend intact ({price:.2f} vs {TRAIL_DAYS}-day low {low20:.2f})")
            continue
        entry = float(p["avg_entry_price"])
        pnl = (price - entry) * qty
        text = (f"{TRAIL_DAYS}-day low {low20:.2f} broken: {sym} {qty} @ {entry:.2f} → ~{price:.2f}  "
                f"<b>{'+' if pnl >= 0 else '−'}${abs(pnl):,.2f}</b>")
        if test_mode:
            send(f"🧪 Would sell (trend exit): {text}")
            log.append(f"{sym}: trend exit dry run")
            continue
        try:
            for o in api.get("/orders", status="open", symbols=sym, limit=50):
                api.delete(f"/orders/{o['id']}")         # the stop order holds the shares: cancel it first
            for _ in range(10):
                if not api.get("/orders", status="open", symbols=sym, limit=50):
                    break
                time.sleep(1)
            api.delete(f"/positions/{sym}")
            if sym in book:
                book[sym]["status"] = "closed"
            send(f"📉 Trend exit, sold: {text}")
            log.append(f"{sym}: trend exit, sold {qty} @ ~{price:.2f}")
        except Exception as e:
            send(f"⚠️ Trend exit for {sym} failed: {e}")
            log.append(f"{sym}: trend exit failed: {e}")
    return log


def run(found, state, send, test_mode=False, chart_fn=None, daily=None):
    """found: the scanner's list of stocks with signals. state: persisted dict (book under '_trades').
    send(text, image=None): sends a Telegram message. chart_fn(item, trade) -> PNG of the trade.
    Returns a list of log lines."""
    log = []
    if not TRADING_ENABLED:
        return log
    api = Alpaca()
    if not api.ok:
        return ["Paper trading: no Alpaca keys"]
    book = state.setdefault("_trades", {})
    try:
        acct = api.get("/account")
        clock = api.get("/clock")
    except Exception as e:
        return [f"Paper trading: couldn't reach Alpaca ({e})"]
    equity, bp = float(acct["equity"]), float(acct["buying_power"])
    log.append(f"Paper account: equity ${equity:,.0f}, buying power ${bp:,.0f}, market {'open' if clock['is_open'] else 'closed'}")
    check_closed(api, book, send)
    if EXIT_MODE == "trail":
        try:
            close_in = (pd.Timestamp(clock["next_close"]) - pd.Timestamp(clock["timestamp"])).total_seconds() / 60
        except Exception:
            close_in = 999
        if test_mode or (clock["is_open"] and close_in <= EXIT_CHECK_MINUTES):
            log += trend_exits(api, book, daily, send, test_mode)

    candidates = [it for it in found if _strong_direction(it)]
    candidates = [it for it in candidates if ALLOW_SHORTS or _strong_direction(it) == "LONG"]
    if not candidates:
        return log
    try:
        positions = api.get("/positions")
        # crypto positions and their stop orders (crypto_trader.py) don't use up stock slots,
        # but their money counts in "invested", so the two bots never borrow together
        held = {p["symbol"] for p in positions if p.get("asset_class") != "crypto"}
        invested = sum(abs(float(p.get("market_value") or 0)) for p in positions)
        pending = {o["symbol"] for o in api.get("/orders", status="open", limit=500)
                   if o.get("asset_class") != "crypto"}
    except Exception as e:
        return log + [f"Paper trading: couldn't read positions/orders ({e})"]
    slots = max(0, MAX_OPEN_POSITIONS - len(held | pending))
    room = max(0.0, MAX_INVESTED * equity - invested)      # money left before borrowing
    today = dt.datetime.utcnow().date().isoformat()

    placed = 0
    for it in candidates:
        sym = it["t"].replace("-", ".")
        direction = _strong_direction(it)
        if sym in held or sym in pending:
            log.append(f"{sym}: already have a position/order, skipped")
            continue
        if book.get(sym, {}).get("opened", "")[:10] == today:
            log.append(f"{sym}: already traded today, skipped")
            continue
        if placed >= MAX_NEW_PER_RUN or slots <= 0:
            log.append(f"{sym}: position limit reached, skipped")
            continue
        try:
            if direction == "SHORT":
                asset = api.get(f"/assets/{sym}")
                if not (asset.get("shortable") and asset.get("easy_to_borrow")):
                    log.append(f"{sym}: not easy to borrow, short skipped")
                    continue
            try:
                price = api.live_price(sym)
            except Exception:
                price = float(it["ctx"]["price"])
            atr = float(it["sd"]["ATR"].iat[-1])
            plan = plan_trade(direction, price, atr, equity, min(bp, room))
            if plan and EXIT_MODE == "trail":
                plan["trail"] = twenty_day_low(it["sd"], pd.Timestamp(it["sd"].index[-1]).date())
            if not plan:
                log.append(f"{sym}: position would be under 1 share (or no money left without borrowing), skipped")
                continue
            side = "buy" if direction == "LONG" else "sell"
            desc = (f"{'BUY' if side == 'buy' else 'SELL SHORT'} {plan['qty']} {sym} @ ~{price:.2f}, "
                    f"stop {plan['stop']:.2f}, " + (f"target {plan['target']:.2f} " if plan["target"] else "trend exit ") +
                    f"(risk ${plan['risk']:,.0f} = {plan['risk_pct']:.1%} of equity)")
            try:                                     # explaining the trade must never block it
                tf, why = trade_timeframe(it), reasons(it, direction)
            except Exception:
                tf, why = "daily", []
            trade = {"sym": sym, "dir": direction, "entry": price, "tf": tf, "why": why, **plan}

            def notify(head):
                try:
                    text = trade_message(head, sym, direction, price, plan, why, tf)
                except Exception:
                    text = f"{head}: {desc}"
                image = None
                if chart_fn:
                    try:
                        image = chart_fn(it, trade)
                    except Exception as e:
                        print(f"{sym}: trade chart failed: {e}", flush=True)
                send(text, image) if image else send(text)
                if SEND_PINE_SCRIPT:
                    try:
                        tf_name = "daily (1D)" if tf == "daily" else "15-minute"
                        head = (f"📋 <b>Draw it on TradingView</b>: open {sym} on the {tf_name} chart → "
                                f"Pine Editor → paste this → <b>Add to chart</b>\n")
                        t2 = dict(trade)
                        while True:                  # Telegram messages max 4096 characters
                            code = html.escape(pine.trade_script(sym, t2), quote=False)
                            msg = f'{head}<pre><code class="language-pine">{code}</code></pre>'
                            if len(msg) <= 4000 or not t2["why"]:
                                break
                            t2["why"] = t2["why"][:-1]
                        send(msg)
                    except Exception as e:
                        print(f"{sym}: Pine script failed: {e}", flush=True)

            if test_mode or not clock["is_open"]:
                reason = "test mode" if test_mode else "market closed"
                notify(f"🧪 NOT placed ({reason}), it would be")
                log.append(f"{sym}: dry run ({reason}): {desc}")
                continue
            body = {"symbol": sym, "qty": str(plan["qty"]), "side": side, "type": "market",
                    "time_in_force": "gtc", "stop_loss": {"stop_price": f"{plan['stop']:.2f}"},
                    # the source of the trade, for the daily paper report (paper_report.py)
                    "client_order_id": f"bot-{tf}-{uuid.uuid4().hex[:12]}"}
            if plan["target"] is not None:             # bracket: stop + take profit
                body.update(order_class="bracket", take_profit={"limit_price": f"{plan['target']:.2f}"})
            else:                                      # stop only; the trend exit sells the rest
                body["order_class"] = "oto"
            order = api.post("/orders", body)
            book[sym] = {"order_id": order["id"], "side": direction, "qty": plan["qty"], "entry": price,
                         "stop": plan["stop"], "target": plan["target"], "status": "open",
                         "filled": False, "opened": dt.datetime.utcnow().isoformat()}
            placed += 1
            slots -= 1
            room = max(0.0, room - plan["qty"] * price)
            notify("🤖 Paper trade placed")
            log.append(f"{sym}: placed {desc}")
        except Exception as e:
            send(f"⚠️ Paper trade for {sym} failed: {e}")
            log.append(f"{sym}: order failed: {e}")
    for t in book.values():                              # remember which entries have filled
        if t["status"] == "open" and not t.get("filled"):
            try:
                t["filled"] = api.get(f"/orders/{t['order_id']}")["status"] == "filled"
            except Exception:
                pass
    return log
