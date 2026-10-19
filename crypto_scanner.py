"""
Crypto scanner: the S&P 100 bot's strategy (rules.py: 200-day MA, Bollinger Bands, Donchian channel,
major support/resistance, Fibonacci, chart patterns) on the 10 biggest tradable cryptocurrencies
(crypto.py), with the same Telegram alerts and charts, and paper trades (crypto_trader.py).

Runs only when you start it: Actions -> Crypto scan -> Run workflow. Crypto trades 24/7, so every run
analyzes the candle that has just closed, at any hour and on weekends. A "day" is a UTC day.
Candles (SCAN_MODE): "15m" (like the stock bot), "1h" or "4h" for the intraday chart; the daily
chart is always checked too.

Run locally:
    pip install -r requirements.txt
    python crypto_scanner.py      # prints alerts if no Telegram secrets are set
"""
import datetime as dt
import json
import os
import time

import pandas as pd

import crypto
import crypto_trader
import patterns
import rules
import scanner
import trader
from fmt import px

# Top-10 breakout paper trading was replaced by the Bitcoin bot (btc_bot.py) on 19 Oct 2026; this scanner
# now only sends alerts (the workflow runs it for "patterns only"). True = trade the old way again.
OLD_TOP10_TRADING = False
STATE_FILE = os.environ.get("STATE_FILE", "crypto_state.json")   # sent alerts and the paper-trade book
MODES = {
    "15m": {"minutes": 15, "name": "15-min", "days": 12},
    "1h":  {"minutes": 60, "name": "hourly"},
    "4h":  {"minutes": 240, "name": "4-hour"},
}
HISTORY_DAYS = 420                   # hourly history: daily candles for the 200-day MA and 1-year levels


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state, now):
    cutoff = (now - dt.timedelta(days=5)).isoformat()
    state = {k: v for k, v in state.items() if not isinstance(v, str) or v >= cutoff}
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def held_coins():
    """Crypto already in the paper account: scanned too, so their trend exits can be checked."""
    api = trader.Alpaca()
    if not api.ok:
        return []
    try:
        return [crypto.to_alpaca(crypto.coin_of(p["symbol"])) for p in api.get("/positions")
                if p.get("asset_class") == "crypto"]
    except Exception:
        return []


def get_prices(coins, mode, now):
    """Daily candles (today so far included), intraday candles and hourly candles per coin.
    Alpaca first; Yahoo Finance for anything Alpaca can't deliver."""
    t0 = time.time()
    hourly = {}
    try:
        hourly = crypto.bars(coins, "1Hour", now - dt.timedelta(days=HISTORY_DAYS), now)
    except Exception as e:
        print(f"Alpaca hourly prices failed ({e})", flush=True)
    missing = [c for c in coins if len(hourly.get(c, ())) < 24 * 200]
    if missing:
        print(f"Yahoo backup for: {' '.join(missing)}", flush=True)
        hourly.update(crypto.yahoo(missing, "730d", "1h"))
    print(f"Hourly prices: {time.time() - t0:.0f}s", flush=True)

    m = MODES[mode]
    if mode == "15m":
        intra = {}
        try:
            intra = crypto.bars(coins, "15Min", now - dt.timedelta(days=m["days"]), now)
        except Exception as e:
            print(f"Alpaca 15-min prices failed ({e})", flush=True)
        miss = [c for c in coins if len(intra.get(c, ())) < 300]
        if miss:
            intra.update(crypto.yahoo(miss, "14d", "15m"))
    elif mode == "1h":
        intra = hourly
    else:
        intra = {c: crypto.resample(h, "4h") for c, h in hourly.items()}
    daily = {c: crypto.daily_from(h) for c, h in hourly.items()}
    return daily, intra, hourly


def with_live(d, price):
    """Today's daily candle finished with the live price."""
    if not price or not len(d):
        return d
    d = d.copy()
    i = d.index[-1]
    d.at[i, "Close"] = price
    d.at[i, "High"] = max(d.at[i, "High"], price)
    d.at[i, "Low"] = min(d.at[i, "Low"], price)
    return d


def trade_chart(item, trade, m):
    daily = trade["tf"] == "daily"
    frame = item["sd"] if daily else item["sh"]
    return scanner.draw_chart(item["t"], frame, f"{item['t']} {'daily' if daily else m['name']}: paper trade LONG",
                              show_sma=daily, n_show=90 if daily else 100, trade=trade)


def main():
    t0 = time.time()
    now = dt.datetime.now(dt.timezone.utc)
    mode = os.environ.get("SCAN_MODE", "15m")
    dry_run = os.environ.get("DRY_RUN") == "1"
    patterns_only = os.environ.get("STRATEGY") == "patterns"     # chart patterns only, no strategy, no trades
    m = MODES[mode]
    state = load_state()
    log = scanner.log

    coins = crypto.top_coins()
    extra = [c for c in held_coins() if c not in coins]
    log(f"{now:%Y-%m-%d %H:%M} UTC: {m['name']} crypto {'PATTERN ' if patterns_only else ''}scan of {len(coins)} coins"
        + (f" (+{len(extra)} held: {' '.join(extra)})" if extra else "") + (" (dry run)" if dry_run else ""), t0)
    daily, intra, hourly = get_prices(coins + extra, mode, now)
    live = crypto.latest_prices(coins + extra)
    for c, p in live.items():
        if c in daily:
            daily[c] = with_live(daily[c], p)
    log(f"Prices ready for {len(daily)} coins, {len(live)} live prices", t0)

    found, watch, checked = [], [], 0
    for t in coins:
        if t not in daily or t not in intra:
            print(f"{t}: no prices, skipped", flush=True)
            continue
        try:
            h = crypto.completed(intra[t], m["minutes"], now)
            if len(daily[t]) < 60 or len(h) < 60:
                print(f"{t}: not enough history ({len(daily[t])} days), skipped", flush=True)
                continue
            checked += 1
            dd = scanner.add_indicators(daily[t].copy())
            events, ctx, sd, sh = rules.evaluate(dd, scanner.add_indicators(h.copy()), m["name"])
            if live.get(t):
                ctx["price"] = live[t]
            if patterns_only:
                events = []                                  # strategy signals off
            day_key, bar_key = str(dd.index[-1].date()), f"{mode}|{h.index[-1].isoformat()}"
            for e in events:
                e["key"] = f"{t}|{e['code']}|{e['tf']}|{day_key if e['tf'] == 'daily' else bar_key}"
            events = [e for e in events if e["key"] not in state]
            keys = [e["key"] for e in events]
            pat_hits, forming_any = [], []

            if scanner.PATTERNS_ENABLED or patterns_only:      # chart patterns on the daily and hourly charts
                price, prev = float(h["Close"].iat[-1]), float(h["Close"].iat[-2])
                hv = scanner.add_indicators(h.copy()).iloc[-1]
                vol_now = float(hv.Volume / hv.VolAvg20) if hv.VolAvg20 else None
                hr = scanner.add_indicators(crypto.completed(hourly[t], 60, now).copy())
                p_ev, p_keys, pat_hits, forming_any, p_watch = scanner.chart_patterns(
                    t, (("daily", dd), ("hourly", hr)), price, prev, vol_now, state, ctx["price"], recent=patterns_only,
                    market="crypto")
                events += p_ev
                keys += p_keys
                watch += p_watch

            if events:
                found.append({"t": t, "events": events, "ctx": ctx, "sd": sd, "sh": sh, "pats": pat_hits,
                              "forming": forming_any, "keys": keys,
                              "score": (sum(e["strong"] for e in events), len(events))})
        except Exception as e:                       # one bad coin never stops the scan
            print(f"{t}: {e}", flush=True)
    log(f"Checked {checked} coins, {len(found)} with new signals, {len(watch)} new patterns forming", t0)

    # alerts: strongest first, all with a chart (at most 10 coins)
    found.sort(key=lambda x: x["score"], reverse=True)
    sent = 0
    for item in found:
        try:
            interval = {"15m": "15", "1h": "60", "4h": "240"}[mode]
            text = scanner.format_alert(item["t"], item["events"], item["ctx"], mode,
                                        link=crypto.tradingview(item["t"], interval))
            if scanner.send_telegram(text, scanner.alert_chart(item, m)):
                for k in item["keys"]:
                    state[k] = now.isoformat()
                sent += 1
        except Exception as e:
            print(f"{item['t']}: alert failed: {e}", flush=True)

    scanner.send_watchlist(watch, state, now, title="👀 Crypto patterns forming")

    if not patterns_only and OLD_TOP10_TRADING:
        for line in crypto_trader.run(found, state, scanner.send_telegram, dry_run=dry_run,
                                      chart_fn=lambda it, tr: trade_chart(it, tr, m), daily=daily, prices=live,
                                      tf_name=m["name"]):
            print(line, flush=True)

    save_state(state, now)
    names = " ".join(crypto.coin_of(c) for c in coins)
    if patterns_only:
        scanner.send_telegram(f"✅ Crypto pattern scan done: {checked} coins checked ({names}), {sent} with a pattern "
                              f"breakout (last {scanner.RECENT_BARS['daily']} days / {scanner.RECENT_BARS['hourly']} "
                              f"hourly candles), {len(watch)} patterns forming. Patterns: {patterns.selection_text('crypto')}. "
                              f"No trades are placed by pattern scans.")
    else:
        scanner.send_telegram(f"✅ Crypto scan done ({m['name']} candles{', dry run' if dry_run else ''}): "
                              f"{checked} coins checked ({names}), {sent} with new signals.")
    log(f"Done: {sent} alerts sent", t0)


if __name__ == "__main__":
    main()
