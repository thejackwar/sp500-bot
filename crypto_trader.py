"""
Crypto paper trading with Alpaca: the stock bot's rules (trader.py), adapted to crypto.
    entry  = market order, fractional amount (e.g. 0.0123 BTC)
    stop   = entry - STOP_ATR x daily ATR, sent as a separate stop-limit sell order that stays active
             (good-till-cancelled), so Alpaca sells at the stop by itself even when the bot isn't running.
             Crypto on Alpaca has no bracket orders, so the stop is placed right after the entry fills.
    exit   = EXIT_MODE "trail" (like the stock bot): no fixed target; sells when the price is below
             the 20-day low. The bot only runs when you start it, so this is checked at every run.
             EXIT_MODE "target": a take-profit is not supported here (one sell order per coin on
             Alpaca), so "target" is only available in the backtest.
Longs only: Alpaca can't sell crypto short, and crypto can't be bought with borrowed money.

Money: each trade risks RISK_PER_TRADE of the account equity between entry and stop, at most
MAX_POSITION_PCT of equity per coin, and all crypto together at most CRYPTO_BUDGET of the account,
so the stock bot keeps the rest. Crypto positions don't take stock-position slots (trader.py ignores them).

The bot treats every crypto position in the paper account as its own (trend exit and stop check).

Safety: uses Alpaca's paper-trading address only (trader.Alpaca). It cannot touch real money.
"""
import datetime as dt
import math
import re
import time
import uuid

import pandas as pd

import crypto
import trader
from fmt import px

TRADING_ENABLED = True       # False = alerts only, no paper trades
ALLOW_SHORTS = False         # Alpaca can't short crypto (the backtest can test it anyway)
RISK_PER_TRADE = 0.01        # risk 1% of equity per trade
STOP_ATR = 2.0               # stop-loss distance = 2 x daily ATR
EXIT_MODE = "trail"          # sell when the price falls below the 20-day low
TRAIL_DAYS = 20              # trailing exit: the lowest low of the last 20 days (UTC days)
REWARD_RISK = 2.0            # 2:1 target (backtest only, see above)
MAX_POSITION_PCT = 0.20      # never more than 20% of equity in one coin
MAX_OPEN_POSITIONS = 5       # crypto positions at the same time
MAX_INVESTED = 1.0           # backtest: total in positions at most 1.0 x equity (crypto can't use margin)
CRYPTO_BUDGET = 0.50         # live: all crypto positions together at most 50% of the paper account
MAX_NEW_PER_RUN = 3          # at most 3 new trades per run
STOP_LIMIT_SLACK = 0.01      # the stop sells with a limit 1% below the stop price (fills in fast drops)
FEE = 0.0025                 # Alpaca's crypto fee for small accounts: 0.25% per buy or sell (backtest)
MIN_ORDER_USD = 10           # skip trades smaller than this


def round_price(x, inc=None):
    """A price rounded to Alpaca's price step for the coin (or to 6 significant digits)."""
    if inc:
        return round(round(x / inc) * inc, 10)
    return float(f"{x:.6g}")


def floor_qty(q, step=None):
    step = step or 1e-8
    return math.floor(q / step + 1e-9) * step


def fmt_qty(q):
    return f"{q:,.2f}" if q >= 100 else f"{q:.6g}"


def plan_trade(direction, price, atr, equity, buying_power, step=None, min_qty=0.0, price_inc=None):
    """Amount, stop and target for one trade. Returns None if the trade is too small."""
    stop_dist = STOP_ATR * atr
    if stop_dist <= 0 or price <= 0:
        return None
    qty = min(equity * RISK_PER_TRADE / stop_dist, equity * MAX_POSITION_PCT / price,
              max(0.0, buying_power) * 0.95 / price)
    qty = floor_qty(qty, step)
    if qty <= 0 or qty < min_qty or qty * price < MIN_ORDER_USD:
        return None
    sign = 1 if direction == "LONG" else -1
    stop = round_price(price - sign * stop_dist, price_inc)
    target = round_price(price + sign * REWARD_RISK * stop_dist, price_inc) if EXIT_MODE == "target" else None
    return {"qty": qty, "stop": stop, "target": target, "risk": qty * stop_dist,
            "risk_pct": qty * stop_dist / equity}


def twenty_day_low(daily, today):
    """Lowest low of the last TRAIL_DAYS completed UTC days (today's candle left out)."""
    if daily is None or not len(daily):
        return None
    past = daily[pd.Index(daily.index.date) < today]
    return float(past["Low"].tail(TRAIL_DAYS).min()) if len(past) >= TRAIL_DAYS else None


def _assets(api):
    """Alpaca's crypto assets by coin: min order size, amount step, price step."""
    out = {}
    try:
        for a in api.get("/assets", asset_class="crypto", status="active"):
            if a["symbol"].endswith("/USD"):
                out[a["symbol"][:-4]] = a
    except Exception as e:
        print(f"Crypto paper trading: couldn't read Alpaca's coin details ({e})", flush=True)
    return out


def _f(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _wait_fill(api, order_id, seconds=20):
    o = None
    for _ in range(seconds):
        o = api.get(f"/orders/{order_id}")
        if o["status"] in ("filled", "canceled", "expired", "rejected"):
            break
        time.sleep(1)
    return o


def place_stop(api, coin, qty, stop, inc=None):
    """Good-till-cancelled stop-limit sell for the whole position."""
    body = {"symbol": crypto.to_alpaca(coin), "qty": f"{qty:.10g}", "side": "sell", "type": "stop_limit",
            "time_in_force": "gtc", "stop_price": f"{round_price(stop, inc):.10g}",
            "limit_price": f"{round_price(stop * (1 - STOP_LIMIT_SLACK), inc):.10g}"}
    return api.post("/orders", body)


def _position_qty(api, coin, fallback):
    try:
        p = api.get(f"/positions/{coin}USD")
        return _f(p.get("qty_available"), None) or _f(p.get("qty"), fallback)
    except Exception:
        return fallback


def check_book(api, book, positions, open_orders, assets, send):
    """Trades placed earlier: put a stop on entries that filled since, report stops that were hit."""
    held = {crypto.coin_of(p["symbol"]): p for p in positions}
    for coin, t in list(book.items()):
        if t.get("status") != "open":
            continue
        inc = _f((assets.get(coin) or {}).get("price_increment"))
        try:
            if not t.get("stop_id"):                       # entry hadn't filled when it was placed
                o = api.get(f"/orders/{t['order_id']}")
                if o["status"] in ("canceled", "expired", "rejected"):
                    t["status"] = "closed"
                    send(f"📕 Crypto paper trade {coin}: entry order {o['status']}, no position opened")
                elif o["status"] == "filled" and coin in held:
                    qty = _position_qty(api, coin, _f(o.get("filled_qty"), t["qty"]))
                    t["stop_id"] = place_stop(api, coin, qty, t["stop"], inc)["id"]
                    t["entry"] = _f(o.get("filled_avg_price"), t["entry"])
                    send(f"🛡 {coin}: entry filled @ {px(t['entry'])}, stop loss placed at {px(t['stop'])}")
                continue
            if coin in held:
                s = api.get(f"/orders/{t['stop_id']}")
                if s["status"] in ("canceled", "expired", "rejected") and coin not in open_orders:
                    qty = _position_qty(api, coin, t["qty"])
                    t["stop_id"] = place_stop(api, coin, qty, t["stop"], inc)["id"]
                    send(f"🛡 {coin}: the stop order was {s['status']}, placed it again at {px(t['stop'])}")
                continue
            s = api.get(f"/orders/{t['stop_id']}")          # position gone: stop hit or sold by hand
            t["status"] = "closed"
            if s["status"] == "filled":
                exit_px = float(s["filled_avg_price"])
                pnl = (exit_px - t["entry"]) * t["qty"]
                send(f"🛑 Stop hit: {coin} {fmt_qty(t['qty'])} @ {px(t['entry'])} → {px(exit_px)}  "
                     f"<b>{'+' if pnl >= 0 else '−'}${abs(pnl):,.2f}</b>")
            else:
                send(f"📕 Crypto paper trade {coin} closed (no longer in the account)")
        except Exception as e:
            print(f"{coin}: couldn't check the trade ({e})", flush=True)
    cutoff = (dt.datetime.utcnow() - dt.timedelta(days=10)).isoformat()
    for coin in [c for c, t in book.items() if t["status"] == "closed" and t["opened"] < cutoff]:
        del book[coin]


def trend_exits(api, book, positions, daily, prices, send, dry_run=False):
    """Sell crypto positions whose price is below their 20-day low; warn about positions without a stop."""
    log = []
    today = dt.datetime.utcnow().date()
    try:
        orders = api.get("/orders", status="open", limit=500)
    except Exception as e:
        return [f"Crypto trend exits: couldn't read orders ({e})"]
    for p in positions:
        coin = crypto.coin_of(p["symbol"])
        sym = crypto.to_alpaca(coin)
        qty = _f(p.get("qty"), 0.0)
        if qty <= 0:
            continue
        mine = [o for o in orders if crypto.coin_of(o["symbol"]) == coin]
        low20 = twenty_day_low(daily.get(sym), today)
        price = prices.get(sym) or _f(p.get("current_price"), 0.0)
        if low20 is None:
            log.append(f"{coin}: no daily prices for the trend exit check")
            continue
        if price >= low20:
            log.append(f"{coin}: trend intact ({px(price)} vs {TRAIL_DAYS}-day low {px(low20)})")
            if not any(o["side"] == "sell" for o in mine) and not dry_run:
                send(f"⚠️ {coin}: position has no stop-loss order in Alpaca. Check it in the paper account.")
            continue
        entry = _f(p.get("avg_entry_price"), price)
        pnl = (price - entry) * qty
        text = (f"{TRAIL_DAYS}-day low {px(low20)} broken: {coin} {fmt_qty(qty)} @ {px(entry)} → ~{px(price)}  "
                f"<b>{'+' if pnl >= 0 else '−'}${abs(pnl):,.2f}</b>")
        if dry_run:
            send(f"🧪 Would sell (trend exit): {text}")
            log.append(f"{coin}: trend exit dry run")
            continue
        try:
            for o in mine:
                api.delete(f"/orders/{o['id']}")           # the stop order holds the coins: cancel it first
            for _ in range(10):
                if not [o for o in api.get("/orders", status="open", limit=500)
                        if crypto.coin_of(o["symbol"]) == coin]:
                    break
                time.sleep(1)
            api.delete(f"/positions/{coin}USD")
            if coin in book:
                book[coin]["status"] = "closed"
            send(f"📉 Trend exit, sold: {text}")
            log.append(f"{coin}: trend exit, sold {fmt_qty(qty)} @ ~{px(price)}")
        except Exception as e:
            send(f"⚠️ Trend exit for {coin} failed: {e}")
            log.append(f"{coin}: trend exit failed: {e}")
    return log


def reasons(item):
    ev = sorted(item.get("events", []), key=lambda e: not e.get("strong"))
    out = [e["text"] for e in ev if e.get("text") and e.get("dir") in ("LONG", "WATCH")]
    ctx = item.get("ctx", {})
    if ctx.get("sma200"):
        side = "above" if ctx["price"] > ctx["sma200"] else "below"
        out.append(f"Trend: price {side} the 200-day MA {px(ctx['sma200'])} "
                   f"({(ctx['price'] / ctx['sma200'] - 1) * 100:+.1f}%)")
    # pattern lines carry the pattern's own entry/stop/target: keep only the breakout level
    out = [re.sub(r": entry ([\d.]+), stop [\d.]+, target [\d.]+", r" through \1", r) for r in out]
    return out[:6]


def run(found, state, send, dry_run=False, chart_fn=None, daily=None, prices=None, tf_name="15-min"):
    """found: the crypto scanner's coins with signals. state: persisted dict (book under '_crypto_trades').
    Returns a list of log lines."""
    log = []
    if not TRADING_ENABLED:
        return log
    api = trader.Alpaca()
    if not api.ok:
        return ["Crypto paper trading: no Alpaca keys"]
    book = state.setdefault("_crypto_trades", {})
    daily, prices = daily or {}, prices or {}
    try:
        acct = api.get("/account")
        positions = [p for p in api.get("/positions") if p.get("asset_class") == "crypto"]
        orders = [o for o in api.get("/orders", status="open", limit=500) if o.get("asset_class") == "crypto"]
    except Exception as e:
        return [f"Crypto paper trading: couldn't reach Alpaca ({e})"]
    assets = _assets(api)
    equity = float(acct["equity"])
    cash = _f(acct.get("non_marginable_buying_power"), _f(acct.get("cash"), 0.0))
    invested = sum(abs(_f(p.get("market_value"), 0.0)) for p in positions)
    log.append(f"Paper account: equity ${equity:,.0f}, cash for crypto ${cash:,.0f}, crypto positions ${invested:,.0f}")
    check_book(api, book, positions, {crypto.coin_of(o["symbol"]) for o in orders}, assets, send)
    log += trend_exits(api, book, positions, daily, prices, send, dry_run)

    candidates = [it for it in found if {e["dir"] for e in it["events"] if e.get("strong")} == {"LONG"}]
    if not candidates:
        return log
    try:                                                   # positions may have changed (trend exits)
        positions = [p for p in api.get("/positions") if p.get("asset_class") == "crypto"]
        orders = [o for o in api.get("/orders", status="open", limit=500) if o.get("asset_class") == "crypto"]
    except Exception as e:
        return log + [f"Crypto paper trading: couldn't read positions/orders ({e})"]
    held = {crypto.coin_of(p["symbol"]) for p in positions}
    pending = {crypto.coin_of(o["symbol"]) for o in orders if o["side"] == "buy"}
    invested = sum(abs(_f(p.get("market_value"), 0.0)) for p in positions)
    slots = max(0, MAX_OPEN_POSITIONS - len(held | pending))
    room = max(0.0, min(CRYPTO_BUDGET * equity - invested, cash))
    today = dt.datetime.utcnow().date().isoformat()

    placed = 0
    for it in candidates:
        coin = crypto.coin_of(it["t"])
        if coin in held or coin in pending:
            log.append(f"{coin}: already have a position/order, skipped")
            continue
        if book.get(coin, {}).get("opened", "")[:10] == today:
            log.append(f"{coin}: already traded today, skipped")
            continue
        if placed >= MAX_NEW_PER_RUN or slots <= 0:
            log.append(f"{coin}: position limit reached, skipped")
            continue
        a = assets.get(coin, {})
        if assets and not a.get("tradable", False):
            log.append(f"{coin}: not tradable on Alpaca, skipped")
            continue
        try:
            price = prices.get(it["t"]) or float(it["ctx"]["price"])
            atr = float(it["sd"]["ATR"].iat[-1])
            inc = _f(a.get("price_increment"))
            plan = plan_trade("LONG", price, atr, equity, room, step=_f(a.get("min_trade_increment")),
                              min_qty=_f(a.get("min_order_size"), 0.0), price_inc=inc)
            if not plan:
                log.append(f"{coin}: trade too small (or the crypto budget is used up), skipped")
                continue
            plan["trail"] = twenty_day_low(it["sd"], pd.Timestamp(it["sd"].index[-1]).date())
            why = reasons(it)
            desc = (f"BUY {fmt_qty(plan['qty'])} {coin} @ ~{px(price)}, stop {px(plan['stop'])}, trend exit "
                    f"(risk ${plan['risk']:,.0f} = {plan['risk_pct']:.1%} of equity)")
            trade = {"sym": coin, "dir": "LONG", "entry": price, "tf": "daily" if any(
                e.get("strong") and e["tf"] == "daily" for e in it["events"]) else "intraday",
                     "why": why, **plan, "qty": fmt_qty(plan["qty"]),
                     "trail_note": f"{TRAIL_DAYS}-day low, checked when the bot runs"}

            def notify(head):
                lines = [f"{head}: <b>BUY {fmt_qty(plan['qty'])} {coin}</b> @ ~{px(price)} "
                         f"(${plan['qty'] * price:,.0f})",
                         f"🎯 No fixed target: sells when the price is below the {TRAIL_DAYS}-day low"
                         + (f" (now {px(plan['trail'])})" if plan.get("trail") else "") + ", checked at every run",
                         f"🛑 Stop loss {px(plan['stop'])} ({(plan['stop'] / price - 1) * 100:+.1f}%, "
                         f"−${plan['risk']:,.0f} = {plan['risk_pct']:.1%} of equity)",
                         "<b>Why:</b>"] + [f"• {r}" for r in why]
                interval = "D" if trade["tf"] == "daily" else {"15-min": "15", "hourly": "60", "4-hour": "240"}.get(tf_name, "60")
                lines.append(f'<a href="{crypto.tradingview(coin, interval)}">Open {coin} on TradingView</a>')
                image = None
                if chart_fn:
                    try:
                        image = chart_fn(it, trade)
                    except Exception as e:
                        print(f"{coin}: trade chart failed: {e}", flush=True)
                send("\n".join(lines), image) if image else send("\n".join(lines))

            if dry_run:
                notify("🧪 NOT placed (dry run), it would be")
                log.append(f"{coin}: dry run: {desc}")
                continue
            order = api.post("/orders", {"symbol": crypto.to_alpaca(coin), "qty": f"{plan['qty']:.10g}",
                                         "side": "buy", "type": "market", "time_in_force": "gtc",
                                         "client_order_id": f"crypto-{trade['tf']}-{uuid.uuid4().hex[:12]}"})
            book[coin] = {"order_id": order["id"], "stop_id": None, "qty": plan["qty"], "entry": price,
                          "stop": plan["stop"], "status": "open", "opened": dt.datetime.utcnow().isoformat()}
            o = _wait_fill(api, order["id"])
            if o and o["status"] == "filled":
                book[coin]["entry"] = price = _f(o.get("filled_avg_price"), price)
                qty = _position_qty(api, coin, _f(o.get("filled_qty"), plan["qty"]))   # after Alpaca's fee
                book[coin]["qty"] = qty
                book[coin]["stop_id"] = place_stop(api, coin, qty, plan["stop"], inc)["id"]
                trade["entry"] = price
                notify("🤖 Crypto paper trade placed")
            else:
                notify("🤖 Crypto paper order sent (not filled yet, the stop is placed at the next run once it fills)")
            placed += 1
            slots -= 1
            room = max(0.0, room - plan["qty"] * price)
            log.append(f"{coin}: placed {desc}")
        except Exception as e:
            send(f"⚠️ Crypto paper trade for {coin} failed: {e}")
            log.append(f"{coin}: order failed: {e}")
    return log
