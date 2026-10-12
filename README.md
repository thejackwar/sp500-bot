# S&P 100 Trade Alert Bot

Scans the S&P 100 (list refreshed from Wikipedia every run, built-in backup copy; GOOGL only, not GOOG) during US market hours: **every 15 minutes on 15-minute candles for the first 3 hours after the open** (about 16:00–18:45 Milan time), then **every hour on hourly candles** until the close (about 18:50–22:50 Milan time).
It runs the strategy in `rules.py` (200-day moving average, Bollinger Bands, Donchian channel) and sends a Telegram
message with a chart whenever a signal appears. It **never places trades**. It only alerts you.

| File | What it does |
|---|---|
| `scanner.py` | Downloads prices, computes indicators, levels and trend lines, sends alerts |
| `rules.py` | **Your strategy** (200-day MA, Bollinger, Donchian). Settings at the top |
| `patterns.py` | Chart-pattern recognition (switch: `PATTERNS_ENABLED` in `scanner.py`) |
| `.github/workflows/scan.yml` | The schedule that runs it on GitHub for free |

## 1. Create your Telegram bot (5 min)
1. In Telegram, open **@BotFather** and send `/newbot`. Pick a name. It replies with a **token** like `123456:ABC-xyz`.
2. Open your new bot and send it any message (e.g. "hi").
3. In a browser, open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`.
   Find `"chat":{"id":123456789` and copy that number. That's your **chat ID**.

## 2. Get your Alpaca data keys (5 min)
1. Sign up for free at **alpaca.markets**. A paper-trading account is enough, with no money and no ID checks for trading.
2. In the dashboard (Paper account), find **API Keys** and click **Generate New Keys**.
3. Copy the **Key ID** and the **Secret Key**. The secret is shown only once, so save it somewhere safe.

## 3. Put the bot on GitHub (5 min)
1. Sign up at github.com (free). Click **New repository**, name it `sp500-bot`, choose **Private**, and create it.
2. Click **uploading an existing file**. Drag in everything from the unzipped folder, **including the `.github` folder**, then **Commit**.
   (If your file browser hides `.github`: in the repo click **Add file → Create new file**, type the name
   `.github/workflows/scan.yml`, and paste in that file's contents.)
3. Go to **Settings → Secrets and variables → Actions → New repository secret** and add four:
   - `TELEGRAM_TOKEN` = your bot token
   - `TELEGRAM_CHAT_ID` = your chat ID
   - `ALPACA_KEY_ID` = your Alpaca Key ID
   - `ALPACA_SECRET_KEY` = your Alpaca Secret Key

## 4. Test it
**Actions** tab → **S&P 500 scan** → **Run workflow** → pick 15m or 1h → tick *Test mode* → **Run**.
It takes about 3–5 minutes. The log shows `Alpaca: 100 tickers OK` when the keys work. Any current setups arrive on Telegram. After that it runs automatically every weekday.

## The strategy (`rules.py`)
**Daily chart** (checked during the day with the latest price; each alert once per day):
- 🟢/🔴 Price crosses above/below the **200-day moving average**
- 🟢/🔴 Price breaks above/below the **Bollinger Bands** (20 days, 2 std dev)
- ⚪ **Bollinger squeeze:** bands at their narrowest in ~6 months, a big move often follows
- 🟢/🔴 **Donchian breakout:** new 20-day high/low

**⭐ STRONG** when it all lines up: price on the right side of the 200-day MA + Donchian breakout in that direction + Bollinger confirmation (price beyond the band, or a squeeze in the last 10 candles).

**Intraday chart** (15-minute candles 9:30–12:30 New York, hourly after): slower settings (Donchian 55, Bollinger 2.5 std) and **only ⭐ strong setups** are sent. Short candles break 20-candle highs/lows constantly (about 240 alerts a day in testing). Expect roughly **25–30 alerts a day** in total.

Up to 10 stocks per check get a chart alert (strongest first); any others are listed in one summary message. Every alert shows the 200-day MA (and distance from it), the daily Bollinger and Donchian levels, and a TradingView link.

## Charts
Every alert comes with a **candlestick chart on a black background** (green = up candle, red = down candle):
- **Strategy alert:** Bollinger Bands (shaded), Donchian channel, 200-day MA (daily), squeeze markers.
- **Pattern breakout:** the chart switches to the pattern's timeframe (daily or hourly) and draws the pattern in yellow: lines, labelled swing points (LS/Head/RS, 1-2-3, X-A-B-C-D…), plus entry, stop and target.
- If a stock gets a strategy alert while a pattern is still **forming** on that chart, the forming pattern is drawn too.


## Chart patterns (`patterns.py`)
Recognised automatically on the **daily and hourly** charts:
- **Reversal:** double top/bottom, triple top/bottom, head & shoulders (and inverse), rising/falling wedge, rounding bottom, bump & run
- **Continuation:** ascending/descending/symmetrical triangle, rectangle, bull/bear flag, bull/bear pennant
- **Harmonic:** ABCD, Gartley, Butterfly

How it works: swing highs/lows are found with a zigzag that ignores moves smaller than 2× ATR, and each pattern is checked against those swings.
- **Forming:** listed in one 👀 watchlist message per check (each pattern only once).
- **Breakout:** a chart alert with the pattern drawn, plus entry, stop and target (target = the pattern's height, the usual "measured move"). Breakouts need the candle's volume at least 1.3× its 20-candle average and a clear close beyond the line. Harmonics alert when price reaches the completion zone and turns.
- Diamond patterns are not detected (too rare and loosely shaped to recognise reliably).


## Notes
- **Prices:** Alpaca's official API on the free plan. Data covers all US exchanges (accurate prices and volume) but must be 15+ minutes old, so each check runs about 17–20 minutes after its candle closes. A setup reaches you about 20 minutes after its candle closes. Good for swing trades, not for scalping.
- **Backup:** if Alpaca fails for some tickers or the whole run, those tickers come from Yahoo Finance automatically. Without Alpaca keys, the bot uses Yahoo for everything.
- A private repo gets 2,000 free minutes a month. This bot uses roughly 800–1,100. Runs outside the trading window stop after a few seconds without downloading anything.
- GitHub pauses schedules after 60 days with no commits. Any small edit restarts them.
- Signals are possible setups, not advice. Backtest your rules before trading real money.
