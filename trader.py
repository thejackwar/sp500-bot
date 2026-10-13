"""
Paper trading with Alpaca. Turns STRONG (⭐) signals into bracket orders in your PAPER account:
    entry  = market order (whole shares)
    stop   = entry -/+ STOP_ATR x daily ATR
    target = entry +/- REWARD_RISK x the stop distance (2:1 by default)
The stop and target are sent together with the entry ("bracket"), so Alpaca closes the trade by
itself even when the bot isn't running. Orders stay active until filled (good-till-cancelled).

Position size: each trade risks RISK_PER_TRADE of the account equity between entry and stop,
capped at MAX_POSITION_PCT of equity per stock.

Safety: this file ONLY talks to Alpaca's paper-trading address. It cannot place real-money trades.
"""
import datetime as dt
import math
import os

import requests

TRADING_ENABLED = True       # False = alerts only, no paper trades
ALLOW_SHORTS = True          # also sell short on STRONG SHORT signals
RISK_PER_TRADE = 0.01        # risk 1% of equity per trade
STOP_ATR = 2.0               # stop-loss distance = 2 x daily ATR
REWARD_RISK = 2.0            # target = 2 x the stop distance
MAX_POSITION_PCT = 0.20      # never put more than 20% of equity in one stock
MAX_OPEN_POSITIONS = 10      # don't open new trades beyond this
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
    target = _r2(price + sign * REWARD_RISK * stop_dist)
    return {"qty": qty, "stop": stop, "target": target, "risk": qty * stop_dist}


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


def run(found, state, send, test_mode=False):
    """found: the scanner's list of stocks with signals. state: persisted dict (book under '_trades').
    send: function that sends a Telegram message. Returns a list of log lines."""
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

    candidates = [it for it in found if _strong_direction(it)]
    candidates = [it for it in candidates if ALLOW_SHORTS or _strong_direction(it) == "LONG"]
    if not candidates:
        return log
    try:
        held = {p["symbol"] for p in api.get("/positions")}
        pending = {o["symbol"] for o in api.get("/orders", status="open", limit=500)}
    except Exception as e:
        return log + [f"Paper trading: couldn't read positions/orders ({e})"]
    slots = max(0, MAX_OPEN_POSITIONS - len(held | pending))
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
            plan = plan_trade(direction, price, atr, equity, bp)
            if not plan:
                log.append(f"{sym}: position would be under 1 share, skipped")
                continue
            side = "buy" if direction == "LONG" else "sell"
            desc = (f"{'BUY' if side == 'buy' else 'SELL SHORT'} {plan['qty']} {sym} @ ~{price:.2f}, "
                    f"stop {plan['stop']:.2f}, target {plan['target']:.2f} "
                    f"(risk ${plan['risk']:,.0f} = {RISK_PER_TRADE:.0%} of equity)")
            if test_mode or not clock["is_open"]:
                why = "test mode" if test_mode else "market closed"
                send(f"🧪 <b>Paper trade NOT placed ({why})</b>, it would be:\n{desc}")
                log.append(f"{sym}: dry run ({why}): {desc}")
                continue
            order = api.post("/orders", {
                "symbol": sym, "qty": str(plan["qty"]), "side": side, "type": "market",
                "time_in_force": "gtc", "order_class": "bracket",
                "take_profit": {"limit_price": f"{plan['target']:.2f}"},
                "stop_loss": {"stop_price": f"{plan['stop']:.2f}"},
            })
            book[sym] = {"order_id": order["id"], "side": direction, "qty": plan["qty"], "entry": price,
                         "stop": plan["stop"], "target": plan["target"], "status": "open",
                         "filled": False, "opened": dt.datetime.utcnow().isoformat()}
            placed += 1
            slots -= 1
            send(f"🤖 <b>Paper trade placed</b>: {desc}")
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
