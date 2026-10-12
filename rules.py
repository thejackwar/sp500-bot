"""
YOUR TRADING RULES: this is the only file you need to change.

The three rules below are PLACEHOLDERS so the bot works end to end.
They will be replaced with Giacomo's own indicators and conditions.

What each function receives (da = daily analysis, ha = hourly analysis):
    x["df"]          price table with Open/High/Low/Close/Volume plus
                     EMA20, EMA50, EMA200, RSI, MACD, MACD_signal, ATR, VolAvg20
    x["support"]     list of (price, touches) below current price, nearest first
    x["resistance"]  list of (price, touches) above current price, nearest first
    x["up_line"]     rising trend line or None  -> ["now"] = its value on the last bar
    x["down_line"]   falling trend line or None -> ["now"] = its value on the last bar
"""


def trend_filter(da):
    """Daily chart decides the direction we're allowed to trade."""
    last = da["df"].iloc[-1]
    return {
        "long": last.Close > last.EMA50 > last.EMA200,
        "short": last.Close < last.EMA50 < last.EMA200,
    }


def entry_signals(da, ha, trend):
    """Hourly chart decides the entry. Return a list of (direction, description)."""
    h = ha["df"]
    last, prev = h.iloc[-1], h.iloc[-2]
    atr = last.ATR
    levels_below = da["support"][:3] + ha["support"][:3]
    levels_above = da["resistance"][:3] + ha["resistance"][:3]
    out = []

    if trend["long"]:
        # 1) Bounce off support: previous bar dipped to a level, this bar closed higher, RSI not hot
        for lvl, touches in levels_below:
            if abs(prev.Low - lvl) <= 0.5 * atr and last.Close > prev.Close and last.RSI < 50:
                out.append(("LONG", f"Bounce off support {lvl:.2f} ({touches} touches)"))
                break
        # 2) Breakout above resistance on strong volume
        #    (a level just broken now sits *below* price, so it's in the support list)
        for lvl, touches in levels_below:
            if prev.Close < lvl < last.Close and last.Volume > 1.5 * last.VolAvg20:
                out.append(("LONG", f"Breakout above {lvl:.2f} on {last.Volume / last.VolAvg20:.1f}x volume"))
                break
        # 3) Touch of the rising trend line on the daily chart
        ln = da["up_line"]
        if ln and abs(last.Low - ln["now"]) <= 0.3 * da["df"]["ATR"].iat[-1] and last.Close > ln["now"]:
            out.append(("LONG", f"Holding the daily uptrend line ({ln['now']:.2f})"))

    if trend["short"]:
        # 4) Breakdown: a support just broken now sits *above* price
        for lvl, touches in levels_above:
            if prev.Close > lvl > last.Close and last.Volume > 1.5 * last.VolAvg20:
                out.append(("SHORT", f"Breakdown below support {lvl:.2f} on strong volume"))
                break

    return out
