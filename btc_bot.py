"""
Bitcoin bot (replaces the top-10 crypto breakout bot, 19 Oct 2026): trades ONLY Bitcoin with the best
Bitcoin strategy of the backtests, the user's rule `strategies/btc50_uprise_ma_f1.py`:

    buy   when the daily close crosses above the 50-day average while the 50-day average is rising
          (higher than 10 days ago)
    sell  when a bounce fails below the highest high since the buy (lower high: a high above the 3 days
          before it, then 1 lower day), when a close falls below the 50-day average, or when a close
          falls below the starting stop (the last swing low before the buy, 1-3 ATR below)
    no buying back until the next fresh cross

Exactly as in the backtest: every decision uses COMPLETED daily candles (UTC days, 00:00-24:00 UTC)
and is carried out at the next open, i.e. by this bot's daily run shortly after 00:00 UTC
(Actions -> Crypto scan, on a timer; it can also be started by hand). There is no stop order in
Alpaca: the stops are judged on daily closes, like in the backtest.

Money: Alpaca paper account #1 (shared with the stock breakout bot). A buy puts the whole crypto
budget (crypto_trader.CRYPTO_BUDGET = 50% of the account equity, minus any other crypto held) into
Bitcoin, like the backtest's "whole account" (SIZE = 1.0) but limited to the crypto half.

Run locally: python btc_bot.py   (prints instead of sending when no Telegram secrets are set;
DRY_RUN=1 = show what it would do, place no orders)
"""
import datetime as dt
import math
import os
import time
import uuid

import pandas as pd

import crypto
import crypto_trader
import scanner
import trader
from fmt import px
from strategies import _structure

STRATEGY = "btc50_uprise_ma_f1"         # the strategy file whose settings (KW) the bot uses
SYMBOL = "BTC/USD"
COIN = "BTC"
HISTORY_DAYS = 480                      # daily candles needed: 200-day average + swing points
CATCH_UP_DAYS = 2                       # still buy if the cross was on the last or the previous close (missed run)
ORDER_TAG = "crypto-btcrule"            # client_order_id prefix (the paper report names the trades by it)


def rule_settings():
    import importlib
    return importlib.import_module(f"strategies.{STRATEGY}").KW


def daily_candles(now):
    """Completed daily BTC candles (UTC days); today's unfinished candle is left out."""
    start = now - dt.timedelta(days=HISTORY_DAYS)
    try:
        h = crypto.bars([SYMBOL], "1Hour", start, now)[SYMBOL]
    except Exception as e:
        print(f"Alpaca prices failed ({e}); using Yahoo", flush=True)
        h = crypto.yahoo([SYMBOL], "730d", "1h")[SYMBOL]
    d = crypto.daily_from(h[["Open", "High", "Low", "Close", "Volume"]])
    return d[d.index < pd.Timestamp(now.date())]


def target(rule):
    """Replays the rule's signals: should the bot hold Bitcoin after the last completed close?
    Returns (in_position, day of the buy signal or None, day of the last sell signal or None)."""
    pos, buy_day, sell_day = False, None, None
    for day, r in rule.iterrows():
        if not pos and r["entry"]:
            pos, buy_day = True, day
        elif pos and r["exit"]:
            pos, sell_day = False, day
    return pos, buy_day, sell_day


def btc_position(api):
    try:
        p = api.get("/positions/BTCUSD")
        return p, float(p.get("qty") or 0)
    except Exception:
        return None, 0.0


def main():
    now = dt.datetime.now(dt.timezone.utc)
    dry_run = os.environ.get("DRY_RUN") == "1"
    send = scanner.send_telegram
    kw = rule_settings()
    d = daily_candles(now)
    if len(d) < 220:
        send(f"⚠️ Bitcoin bot: only {len(d)} days of prices, not enough for the rule. Nothing done.")
        return
    rule = _structure.run(d[["Open", "High", "Low", "Close"]], **kw)
    last = d.index[-1]
    close = float(d["Close"].iat[-1])
    sma50 = d["Close"].rolling(50).mean()
    rising = bool(sma50.iat[-1] > sma50.iat[-11])
    want, buy_day, sell_day = target(rule)
    tag = "🧪 DRY RUN — " if dry_run else ""
    head = f"₿ <b>Bitcoin bot</b> ({STRATEGY}), close {last:%d %b %Y}: {px(close)}"
    ma_line = f"50-day average {px(sma50.iat[-1])} ({'rising' if rising else 'falling'})"

    api = trader.Alpaca()
    if not api.ok:
        send(f"{head}\n⚠️ No Alpaca keys: can't trade. Rule says: {'HOLD' if want else 'CASH'}.")
        return
    acct = api.get("/account")
    equity = float(acct["equity"])
    cash = float(acct.get("non_marginable_buying_power") or acct.get("cash") or 0)
    positions = [p for p in api.get("/positions") if p.get("asset_class") == "crypto"]
    others = [p for p in positions if crypto.coin_of(p["symbol"]) != COIN]
    if others:
        send("⚠️ Bitcoin bot: other crypto in the paper account (not managed by this bot): "
             + ", ".join(f"{crypto.coin_of(p['symbol'])} ${float(p['market_value']):,.0f}" for p in others))
    pos, qty = btc_position(api)
    open_orders = [o for o in api.get("/orders", status="open", limit=500)
                   if crypto.coin_of(o["symbol"]) == COIN]

    # ---------------------------------------------------------------- sell
    if qty > 0 and not want:
        why = rule.at[sell_day, "reason"] if sell_day is not None else "the rule is not in a trade"
        entry = float(pos.get("avg_entry_price") or close)
        pnl = (close - entry) * qty
        text = (f"{head}\n📉 {tag}<b>SELL {crypto_trader.fmt_qty(qty)} BTC</b> (~{px(close)}): {why} "
                f"on the close of {sell_day:%d %b}" if sell_day is not None else
                f"{head}\n📉 {tag}<b>SELL {crypto_trader.fmt_qty(qty)} BTC</b> (~{px(close)}): {why}")
        text += f"\nBought @ {px(entry)} → about <b>{'+' if pnl >= 0 else '−'}${abs(pnl):,.0f}</b> ({(close / entry - 1) * 100:+.1f}%)"
        if not dry_run:
            for o in open_orders:
                api.delete(f"/orders/{o['id']}")
            time.sleep(2)
            api.delete("/positions/BTCUSD")
        send(text + f"\n{ma_line}")
        return

    # ---------------------------------------------------------------- buy
    if qty <= 0 and want:
        age = (d.index > buy_day).sum() if buy_day is not None else 99   # closes since the buy signal
        if open_orders:
            send(f"{head}\n⏳ A Bitcoin order is still open in Alpaca; not buying again.")
            return
        if age >= CATCH_UP_DAYS:
            send(f"{head}\nThe rule has been in a trade since the cross on {buy_day:%d %b}, but the bot only buys "
                 f"right after a cross (missed or started late): waiting for the next one.\n{ma_line}")
            return
        assets = crypto_trader._assets(api)
        a = assets.get(COIN, {})
        other_value = sum(abs(float(p.get("market_value") or 0)) for p in others)
        budget = max(0.0, min(crypto_trader.CRYPTO_BUDGET * equity - other_value, cash)) * 0.99   # room for the fee
        step = float(a.get("min_trade_increment") or 1e-8)
        buy_qty = math.floor(budget / close / step) * step
        if buy_qty * close < crypto_trader.MIN_ORDER_USD:
            send(f"{head}\n⚠️ Buy signal, but no room in the crypto budget (${budget:,.0f}).")
            return
        lines = [f"{head}",
                 f"📈 {tag}<b>BUY {crypto_trader.fmt_qty(buy_qty)} BTC</b> (~${buy_qty * close:,.0f}, "
                 f"{buy_qty * close / equity:.0%} of the account): the close crossed above the rising 50-day average",
                 ma_line,
                 "Sells when: a bounce fails below the highest high since the buy (lower high), a close below the "
                 "50-day average, or a close below the starting stop (the last swing low). Checked every day after 00:00 UTC.",
                 f'<a href="{crypto.tradingview(SYMBOL, "D")}">Open BTC on TradingView</a>']
        if not dry_run:
            order = api.post("/orders", {"symbol": SYMBOL, "qty": f"{buy_qty:.10g}", "side": "buy", "type": "market",
                                         "time_in_force": "gtc", "client_order_id": f"{ORDER_TAG}-{uuid.uuid4().hex[:12]}"})
            o = crypto_trader._wait_fill(api, order["id"])
            if o and o.get("status") == "filled":
                lines[1] += f" — filled @ {px(float(o['filled_avg_price']))}"
            else:
                lines[1] += " — order sent, not filled yet"
        send("\n".join(lines))
        return

    # ---------------------------------------------------------------- no change
    if qty > 0:
        entry = float(pos.get("avg_entry_price") or close)
        stop = rule.at[last, "stop"]
        peak = rule.at[last, "peak"]
        send(f"{head}\n✋ Holding {crypto_trader.fmt_qty(qty)} BTC (bought @ {px(entry)}, {(close / entry - 1) * 100:+.1f}%). "
             f"Highest high since the buy {px(peak) if pd.notna(peak) else '-'}; starting stop "
             f"{px(stop) if pd.notna(stop) else '-'}.\n{ma_line}: sells on a close below it or on a lower high.")
    else:
        if want:      # rule in a trade the bot didn't take (started late / missed day): handled above
            return
        if close > sma50.iat[-1]:
            nxt = "a fresh cross: a close below the 50-day first, then a close back above it while it is rising"
        else:
            nxt = "a close above it while it is rising" if rising else "the 50-day average to turn up and a close above it"
        send(f"{head}\n💤 In cash. {ma_line}. Next buy needs {nxt}"
             + (f" (it last sold on {sell_day:%d %b})." if sell_day is not None else "."))


if __name__ == "__main__":
    main()
