"""
Pictures of the deck pattern trader's paper trades (deck_trader.py, pattern Alpaca account).

For every order tagged pat-deck_... opened on DATE (New York date, default today) it finds the pattern
again exactly as the trader saw it (daily candles up to the day before the entry), and draws the
pattern chart with today's candle, the fill, the order's stop and take profit, the current price and
the open P&L. Saves PNGs + a README table to reports/paper/deck-trades/<date>/ (committed by
.github/workflows/deck-trades.yml). Never trades.
"""
import datetime as dt
import os

import pandas as pd

import deck_patterns as dp
import deck_trader
import pattern_scan
import pattern_trader
import scanner
from fmt import px

ET = scanner.ET


def parse_tag(cid):
    """pat-deck_Symmetrical_triangle_up-1a2b3c -> ("Symmetrical_triangle_up", "LONG")."""
    tag = cid[len("pat-deck_"):].rsplit("-", 1)[0]
    return tag, ("LONG" if tag.endswith("_up") else "SHORT")


def main():
    a = pattern_trader.api()
    if not a.ok:
        raise SystemExit("No ALPACA_PATTERN_KEY_ID / ALPACA_PATTERN_SECRET_KEY")
    os.environ.setdefault("ALPACA_KEY_ID", os.environ.get("ALPACA_PATTERN_KEY_ID", ""))
    os.environ.setdefault("ALPACA_SECRET_KEY", os.environ.get("ALPACA_PATTERN_SECRET_KEY", ""))
    day = os.environ.get("DATE", "").strip()
    day = dt.date.fromisoformat(day) if day else dt.datetime.now(ET).date()
    after = dt.datetime.combine(day, dt.time(0, 0), ET).astimezone(dt.timezone.utc)
    orders = a.get("/orders", status="all", limit=500, nested="true", direction="asc",
                   after=after.strftime("%Y-%m-%dT%H:%M:%SZ"))
    orders = [o for o in orders if (o.get("client_order_id") or "").startswith("pat-deck_")
              and pd.Timestamp(o["submitted_at"]).tz_convert(ET).date() == day]
    positions = {p["symbol"]: p for p in a.get("/positions")}
    print(f"{len(orders)} deck-trader orders on {day}", flush=True)

    out = os.path.join("reports", "paper", "deck-trades", day.isoformat())
    os.makedirs(out, exist_ok=True)
    for f in os.listdir(out):
        os.remove(os.path.join(out, f))
    now = dt.datetime.now(ET)
    rows, md = [], []
    for k, o in enumerate(orders, 1):
        sym, t = o["symbol"], o["symbol"].replace(".", "-")
        tag, side = parse_tag(o["client_order_id"])
        legs = o.get("legs") or []
        stop = next((float(x["stop_price"]) for x in legs if x.get("stop_price")), None)
        target = next((float(x["limit_price"]) for x in legs if x.get("type") == "limit"), None)
        filled = o["status"] == "filled" and o.get("filled_avg_price")
        entry = float(o["filled_avg_price"]) if filled else None
        qty = int(float(o.get("filled_qty") or 0)) or int(float(o["qty"]))
        exit_leg = next((x for x in legs if x["status"] == "filled"), None)
        sign = 1 if side == "LONG" else -1
        if exit_leg:
            now_px = float(exit_leg["filled_avg_price"])
            status = "closed: " + ("take profit hit" if exit_leg["type"] == "limit" else "stop hit")
        elif sym in positions:
            now_px = float(positions[sym].get("current_price") or entry or 0)
            status = "open"
        else:
            now_px, status = entry, ("not filled: " + o["status"]) if not filled else "closed"
        pnl = sign * (now_px - entry) * qty if entry and now_px else 0.0
        pnl_pct = sign * (now_px / entry - 1) * 100 if entry and now_px else 0.0
        row = {"symbol": sym, "pattern": tag.replace("_", " "), "side": side, "qty": qty,
               "entry": entry, "stop": stop, "target": target, "now": now_px, "pnl": round(pnl, 2),
               "pnl_pct": round(pnl_pct, 2), "status": status,
               "time": pd.Timestamp(o.get("filled_at") or o["submitted_at"]).tz_convert(ET).strftime("%H:%M NY")}
        rows.append(row)

        # the pattern again, as the trader saw it (candles up to the day before the entry) ...
        entry_time = pd.Timestamp(o["submitted_at"]).tz_convert(ET).to_pydatetime()
        hist = pattern_trader.daily_prices([t], entry_time).get(t)
        img = None
        if hist is not None:
            df = dp.add_atr(hist)
            pat = next((p for p in dp.detect(df) if p.get("brk") and p["stage"] == "breakout"
                        and p["brk"]["bar"] == len(df) - 1 and deck_trader.slug(p) == tag), None)
            if pat:
                # ... drawn with the candles since then (today's candle so far included)
                end = now - dt.timedelta(minutes=scanner.DATA_DELAY_MINUTES)
                new = scanner.alpaca_bars([t], "1Day", entry_time - dt.timedelta(days=3), end).get(t)
                full = hist
                if new is not None and len(new):
                    new = new.copy()
                    new.index = new.index.tz_convert(ET).tz_localize(None).normalize()
                    new = new[new.index > hist.index[-1]][["Open", "High", "Low", "Close", "Volume"]]
                    full = pd.concat([hist, new])
                trade = {**row, "bar": len(df), "entry": entry or float(full["Open"].iat[-1]),
                         "when": row["time"]}
                if exit_leg and exit_leg.get("filled_at"):    # the candle the trade was closed on
                    xd = pd.Timestamp(exit_leg["filled_at"]).tz_convert(ET).tz_localize(None).normalize()
                    trade["exit_bar"] = min(int(full.index.searchsorted(xd)), len(full) - 1)
                if trade["bar"] >= len(full):
                    trade["bar"] = len(full) - 1
                img = pattern_scan.draw(t, dp.add_atr(full), pat, trade=trade)
            else:
                print(f"{sym}: pattern {tag} not found again", flush=True)
        fn = f"{k:02d}-{t}-{tag}.png"
        if img:
            with open(os.path.join(out, fn), "wb") as f:
                f.write(img)
            md.append(f"### {k}. {sym} — {row['pattern']} ({status})\n\n![]({fn})\n")

    tab = pd.DataFrame(rows)
    if len(tab):
        tab.to_csv(os.path.join(out, "trades.csv"), index=False)
    tot = tab["pnl"].sum() if len(tab) else 0.0
    head = [f"# Deck pattern trader: trades opened on {day}", "",
            f"Pattern Alpaca paper account, orders tagged `pat-deck_…`. Prices as of {now:%d %b %H:%M} New York "
            f"(Alpaca data, about 16 minutes delayed for the chart candles).", "",
            f"**{len(tab)} trades · open P&L {'+' if tot >= 0 else '−'}${abs(tot):,.2f}**", ""]
    if len(tab):
        head += ["| # | Stock | Pattern | Side | Qty | Entry | Stop | Take profit | Now | P&L | Status |",
                 "|---|---|---|---|---|---|---|---|---|---|---|"]
        f = lambda v: px(v) if v is not None and v == v else "–"
        for k, r in enumerate(rows, 1):
            head.append(f"| {k} | {r['symbol']} | {r['pattern']} | {r['side']} | {r['qty']} | {f(r['entry'])} | "
                        f"{f(r['stop'])} | {f(r['target'])} | {f(r['now'])} | "
                        f"{'+' if r['pnl'] >= 0 else '−'}${abs(r['pnl']):,.2f} ({r['pnl_pct']:+.2f}%) | {r['status']} |")
    with open(os.path.join(out, "README.md"), "w") as fh:
        fh.write("\n".join(head) + "\n\n" + "\n".join(md))
    print("\n".join(head))


if __name__ == "__main__":
    main()
