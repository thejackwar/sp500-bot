# S&P 100 Trade Alert Bot

Scans the S&P 100 (list refreshed from Wikipedia every run, built-in backup copy; GOOGL only, not GOOG) **every 15 minutes during US market hours** (started by a cron-job.org timer, section 5), from 9:30 to 16:00 New York time (**15:30 → 22:00 Milan**), on real-time 15-minute candles. The opening check catches opening gaps; every later check analyzes the 15-minute candle that has just closed. GitHub usually starts scheduled runs a minute or two late (more at the top of the hour, when its servers are busiest).
It runs the strategy in `rules.py` (200-day moving average, Bollinger Bands, Donchian channel) and sends a Telegram
message with a chart whenever a signal appears. ⭐ Strong LONG signals are also traded automatically in your **Alpaca paper account** (no real money).

| File | What it does |
|---|---|
| `scanner.py` | Downloads prices, computes indicators, levels and trend lines, sends alerts |
| `rules.py` | **Your strategy** (200-day MA, Bollinger, Donchian). Settings at the top |
| `levels.py` | Major support/resistance and Fibonacci retracements |
| `trader.py` | Paper trading with Alpaca (switch: `TRADING_ENABLED`) |
| `pine.py` | Writes the TradingView Pine Script for each trade |
| `patterns.py` | Chart-pattern recognition (switch: `PATTERNS_ENABLED` in `scanner.py`) |
| `.github/workflows/scan.yml` | The schedule that runs it on GitHub for free |
| `backtest.py` + `.github/workflows/backtest.yml` | Tests the strategy on past years (run by hand) |

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
**Actions** tab → **S&P 500 scan** → **Run workflow** → keep 15m → tick *Test mode* → **Run**.
It takes about 3–5 minutes. The log shows `Alpaca: 100 tickers OK` when the keys work. Any current setups arrive on Telegram. After that it runs automatically every weekday.

## 5. Exact timing with cron-job.org (free)
GitHub's own schedule often starts runs 5–30 minutes late, or skips them. So the scan is started by an outside timer instead, which presses GitHub's "Run workflow" button every 15 minutes.
1. **GitHub token:** your profile picture → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**. Name `cron-job`, expiration 1 year, **Repository access: Only select repositories → sp500-bot**, **Permissions → Actions: Read and write**. Generate and copy the token (shown once).
2. **cron-job.org:** sign up (free) → **Create cronjob**:
   - URL: `https://api.github.com/repos/thejackwar/sp500-bot/actions/workflows/scan.yml/dispatches`
   - Schedule: **Custom**: days of week Mon–Fri, hours 9–16, minutes 0, 15, 30, 45; **time zone America/New_York**
   - **Advanced:** request method **POST**; headers `Authorization: Bearer <your token>`, `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`; request body `{"ref":"main","inputs":{"mode":"15m","source":"timer"}}`
3. **Test run** on cron-job.org: it should answer **204**, and a new run appears in GitHub Actions within seconds.
Runs started by the timer still check New York time and skip (in ~10 seconds) outside 9:30–16:12, so 9:00/9:15 and holidays cost almost nothing. Only one scan runs at a time. The token expires after a year: make a new one and paste it into the cron-job.org header. To go back to GitHub's schedule, see the comment at the top of `scan.yml`.

## The strategy (`rules.py`)
**Daily chart** (checked during the day with the latest price; each alert once per day):
- 🟢/🔴 Price crosses above/below the **200-day moving average**
- 🟢/🔴 Price breaks above/below the **Bollinger Bands** (20 days, 2 std dev)
- ⚪ **Bollinger squeeze:** bands at their narrowest in ~6 months, a big move often follows
- 🟢/🔴 **Donchian breakout:** new 20-day high/low
- 🟢/🔴 **Major support/resistance break:** price clears (by ¼ ATR) a level touched by 3+ significant swings in the last year
- 🟢/🔴 **Fibonacci retracement:** price pulls back to the 38.2%, 50% or 61.8% level of the latest major swing (a move of 6+ ATR) for the first time. Pullback in a rise = watch for a bounce (long); bounce in a drop = watch for a rejection (short)

**⭐ STRONG** when it all lines up: price on the right side of the 200-day MA + Donchian breakout in that direction + Bollinger confirmation (price beyond the band, or a squeeze in the last 10 candles).

**Intraday chart** (15-minute candles all day): slower settings (Donchian 55, Bollinger 2.5 std) and **only ⭐ strong setups** are sent. Short candles break 20-candle highs/lows constantly (about 240 alerts a day in testing). Expect roughly **30 alerts a day** in total (major S/R and Fibonacci add about 5).

Up to 10 stocks per check get a chart alert (strongest first); any others are listed in one summary message. Every alert shows the 200-day MA (and distance from it), the daily Bollinger and Donchian levels, the nearest major support and resistance, the current Fibonacci levels, and a TradingView link.

## Paper trading (`trader.py`)
Every **⭐ STRONG LONG** signal becomes an order in your Alpaca **paper** account. These rules are the best version found by the backtest (longs only + trailing exit):
- **Entry:** market order, whole shares, priced from Alpaca's real-time IEX feed at the moment of the order.
- **Stop-loss:** 2 × daily ATR from the entry, sent with the entry and active until hit (good-till-cancelled), so Alpaca closes the trade at the stop even when the bot isn't running.
- **Exit (trailing):** no fixed target. The scan in the last 20 minutes before the close (normally 15:45 New York) sells any position whose price is below its **20-day low** (📉 message with the profit or loss). Winners can run as long as the trend lasts. To go back to a fixed 2:1 target, set `EXIT_MODE = "target"` in `trader.py`.
- **Shorts:** off (`ALLOW_SHORTS = False`): in the backtest they lost money in every version.
- **Size:** each trade risks **1% of equity** between entry and stop, capped at 20% of equity per stock. **No leverage:** the total in positions never goes above 100% of the account (`MAX_INVESTED = 1.0`); when the money is used up, the next trade is smaller or skipped. `MAX_INVESTED = 2.0` allows up to 2× with borrowed money (pays interest on a real account).
- **Limits:** max 10 open positions, max 3 new trades per check, one trade per stock per day, no new trade if you already hold the stock.
- **Telegram:** 🤖 when a trade is placed, with a **trade chart** in TradingView style: candles, a green take-profit box and a red stop-loss box from the entry (prices, % and R:R), an arrow on the entry candle, and the **reasons for the trade** written on the chart. The message repeats the reasons and has a TradingView link that opens the stock on the same timeframe (TradingView links can't carry drawings, so the levels are on the image). 🛑 or 📉 follow with the profit or loss when the stop or the trend exit closes the trade. The trade chart shows the stop box, a yellow dashed line at the 20-day low (the trend exit) and "no fixed target".
- **TradingView (Pine Script):** after each trade a 📋 message carries a ready-made **Pine Script** with the trade built in. On TradingView: open the stock on the timeframe named in the message → **Pine Editor** → paste → **Add to chart**. It draws the 200-day MA, Bollinger Bands and Donchian channel, green take-profit and red stop-loss boxes from the entry candle, an entry label with R:R, and a table with the reasons. (Pine can't download data from outside TradingView, so each trade gets its own script.) Turn off with `SEND_PINE_SCRIPT = False` in `trader.py`.
- **Test mode and market closed:** no orders; you get a 🧪 message showing what it *would* have traded.
- It only uses Alpaca's paper-trading address, so it cannot touch real money. Settings are at the top of `trader.py`; set `TRADING_ENABLED = False` to go back to alerts only.

## Backtest (`backtest.py`)
Replays the past years check by check (every 15 minutes, like the live bot) with the same rules and settings as `rules.py` and `trader.py`: ⭐ STRONG signals on the daily and 15-minute charts, entry at the next price, stop 2×ATR, target 2:1, 1% risk, max 10 positions, max 3 new per check, one trade per stock per day.
- **Run it:** Actions → **Backtest** → **Run workflow** → pick the number of years (default 5) → **Run**. The first run downloads all the 15-minute history from Alpaca (about 10–15 minutes) and saves it; later runs reuse the saved prices and only download the days since, so they take about 2–3 minutes. GitHub deletes saved prices that go unused for 7 days; the next run then simply downloads everything again. A stock is also downloaded again in full after a split or dividend, because Alpaca adjusts its old prices.
- **Versions compared in one run** (edit `VARIANTS` at the top of `backtest.py` to test others): (1) longs + shorts (the old rules), (2) longs only with the 2:1 target, (3) longs only with a trailing exit: no 2:1 target, the trade is closed when, at the last check of the day, the price is below the 20-day low, with the 2×ATR starting stop kept, (4) longs only with no leverage: total invested never above 100% of the account, like holding SPY, (5) trailing exit with no leverage (the bot now). Other options available in `simulate()`: SPY 200-day trend filter, daily signals only. Each summary also shows "invested", the average money in the market as a % of the account.
- **Results on Telegram:** a summary per version (return, yearly return, max drawdown, profit factor, trades, win rate, average R, worst losing streak, holding time) against buying and holding SPY; a table of returns by year and the long/short split; the equity-curve chart; and `trades.csv` with every trade of every version (column `version`). The same files are attached to the run as **backtest-results**.
- **Assumptions:** $100,000 start; borrowed money (more than 100% invested) pays 6.5% a year interest (`MARGIN_RATE`); fills at the next 15-minute candle's open with 0.02% slippage; a stop or target the price gaps through fills at the opening price; if one candle touches both stop and target, the stop counts (cautious).
- **Limits:** it uses today's S&P 100 members (stocks that left the index aren't tested, which flatters the results); no short-borrow fees; major S/R, Fibonacci and patterns don't open trades so they aren't tested.

## Charts
Every alert comes with a **candlestick chart on a black background** (green = up candle, red = down candle):
- **Strategy alert:** Bollinger Bands (shaded), Donchian channel, 200-day MA (daily), squeeze markers.
- **Major S/R break:** the broken level as a thick cyan line. **Fibonacci:** the swing (dashed) and the 0 / 38.2 / 50 / 61.8 / 100% levels in orange.
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
- **Prices:** Alpaca's official API on the free plan, two feeds:
  - **15-minute candles and live prices:** the **real-time IEX feed**, so each check sees the candle that has just closed. IEX is one exchange: its prices match the full market closely for S&P 100 stocks, but its volume is only IEX's share. Volume is only ever compared with IEX's own recent average, so the comparison stays fair.
  - **Daily history:** the complete all-exchange feed (15+ minutes delayed on the free plan, which doesn't matter for past days). Today's daily candle is built from the live IEX snapshot.
- **Backup:** if Alpaca fails for some tickers or the whole run, those tickers come from Yahoo Finance automatically. Without Alpaca keys, the bot uses Yahoo for everything.
- A private repo gets 2,000 free minutes a month. This bot uses roughly 1,300–1,500 (27 scans per weekday). Runs outside the trading window stop after a few seconds without downloading anything.
- GitHub pauses schedules after 60 days with no commits. Any small edit restarts them.
- Signals are possible setups, not advice. Use the paper account's results to judge the strategy before ever trading real money.
