"""
Pine Script (v6) for a paper trade, ready to paste into TradingView's Pine Editor.

Pine can't download data from outside TradingView, so the bot writes a small script per trade
with the entry, stop, target and reasons built in. On TradingView it draws:
  - the strategy indicators: daily 200-day MA, Bollinger Bands, Donchian channel
  - a green take-profit box and a red stop-loss box starting at the entry candle (like the
    long/short position tool), with prices and %
  - an entry arrow and label, and a table with the reasons for the trade
"""
import datetime as dt
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def _s(text):
    """Text as a Pine string literal."""
    return '"' + str(text).replace("\\", "\\\\").replace('"', "'").replace("\n", " ") + '"'


def entry_candle(now, tf):
    """Start time (New York) of the candle the trade was entered on."""
    now = now.astimezone(ET)
    if tf == "daily":
        return now.replace(hour=9, minute=30, second=0, microsecond=0)
    return now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)


def trade_script(sym, trade, now=None, settings=None):
    """trade: dict with dir, entry, stop, target, qty, tf ('daily' / 'intraday'), why (list)."""
    now = now or dt.datetime.now(ET)
    tf = trade["tf"]
    settings = settings or ({"bb_std": 2.0, "donchian": 20} if tf == "daily" else {"bb_std": 2.5, "donchian": 55})
    t0 = entry_candle(now, tf)
    long = trade["dir"] == "LONG"
    e, tp, sl = trade["entry"], trade["target"], trade["stop"]
    sign = 1 if long else -1
    tp_pct, sl_pct = sign * (tp / e - 1) * 100, sign * (sl / e - 1) * 100
    rr = abs(tp - e) / abs(e - sl)
    side = "BUY" if long else "SELL SHORT"
    why = [w if len(w) <= 90 else w[:89] + "…" for w in trade.get("why", [])][:6]
    tf_name = "1D" if tf == "daily" else "15m"
    rows = "\n".join(f"    table.cell(t, 0, {i + 1}, {_s('• ' + w)}, text_color = color.white, "
                     f"text_halign = text.align_left, text_size = size.small)" for i, w in enumerate(why))
    return f'''//@version=6
indicator({_s(f"Bot trade: {side} {sym}")}, overlay = true, max_boxes_count = 10, max_labels_count = 10, max_lines_count = 10)
// Paper trade from the S&P 100 bot, placed {now.astimezone(ET):%Y-%m-%d %H:%M} New York. Best viewed on the {tf_name} chart of {sym}.

// Strategy indicators
sma200  = request.security(syminfo.tickerid, "D", ta.sma(close, 200))
basis   = ta.sma(close, 20)
dev     = {settings["bb_std"]} * ta.stdev(close, 20)
dcHigh  = ta.highest(high, {settings["donchian"]})[1]
dcLow   = ta.lowest(low, {settings["donchian"]})[1]
plot(sma200, "200-day MA", color = color.purple, linewidth = 2)
pUp = plot(basis + dev, "Bollinger upper", color = color.new(color.blue, 40))
pLo = plot(basis - dev, "Bollinger lower", color = color.new(color.blue, 40))
fill(pUp, pLo, color = color.new(color.blue, 92))
plot(dcHigh, "Donchian high", color = color.new(color.green, 20), style = plot.style_stepline)
plot(dcLow, "Donchian low", color = color.new(color.red, 20), style = plot.style_stepline)

// The trade
entryTime = timestamp("America/New_York", {t0.year}, {t0.month}, {t0.day}, {t0.hour}, {t0.minute})
rightTime = entryTime + 30 * timeframe.in_seconds() * 1000
entry  = {e:.2f}
stop   = {sl:.2f}
target = {tp:.2f}

if barstate.isfirst
    box.new(entryTime, target, rightTime, entry, xloc = xloc.bar_time, border_color = color.teal,
         bgcolor = color.new(color.teal, 75), text = {_s(f"Take profit {tp:.2f} ({tp_pct:+.1f}%)")},
         text_color = color.white, text_size = size.small, text_halign = text.align_left,
         text_valign = {"text.align_top" if long else "text.align_bottom"})
    box.new(entryTime, entry, rightTime, stop, xloc = xloc.bar_time, border_color = color.red,
         bgcolor = color.new(color.red, 75), text = {_s(f"Stop loss {sl:.2f} ({sl_pct:+.1f}%)")},
         text_color = color.white, text_size = size.small, text_halign = text.align_left,
         text_valign = {"text.align_bottom" if long else "text.align_top"})
    line.new(entryTime, entry, rightTime, entry, xloc = xloc.bar_time, color = color.white, width = 2)
    label.new(entryTime, entry, {_s(f"{side} {trade['qty']} @ {e:.2f}  R:R {rr:.1f}")}, xloc = xloc.bar_time,
         style = {"label.style_label_up" if long else "label.style_label_down"},
         color = {"color.teal" if long else "color.red"}, textcolor = color.white, size = size.normal)

var table t = table.new(position.top_right, 1, {len(why) + 1}, bgcolor = color.new(#131722, 10),
     border_color = color.gray, border_width = 1)
if barstate.islast
    table.cell(t, 0, 0, {_s(f"Why this trade ({side} {sym})")}, text_color = color.yellow,
         text_halign = text.align_left, text_size = size.small)
{rows}
'''
