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
| `strategies/` | Other strategies to compare in the backtest (one file each, `_template.py` to start) |
| `pattern_trader.py` + `.github/workflows/pattern-trade.yml` | Paper trades your selected chart patterns in a second Alpaca paper account (section *Pattern paper trader*) |
| `paper_report.py` + `.github/workflows/paper-report.yml` | Daily report of both paper accounts, saved in `reports/paper/` (section *Paper report*) |
| `btc_bot.py` + `.github/workflows/crypto-scan.yml` | **Bitcoin bot**: trades only Bitcoin with the 50-day rule, once a day after 00:00 UTC (section *Crypto*) |
| `crypto_scanner.py` | Chart-pattern alerts on the top 10 cryptocurrencies (*Crypto scan* → patterns only); its top-10 breakout trading was replaced by the Bitcoin bot |
| `crypto.py` | Picks the top 10 coins and downloads their prices |
| `crypto_trader.py` | Crypto paper-trading helpers (Alpaca orders, crypto budget); used by the Bitcoin bot |

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
- **10 start dates per version:** with max 10 positions and 3 new trades per check, many signals are skipped, so which trades get taken depends on where the test starts: moving the start by one day can change a 5-year result by 20+ percentage points. So every version is run from 10 start dates a week apart (signals that tie are taken in random order), and the report shows the middle result, the range, the worst drawdown and in how many of the 10 runs it beat SPY over the same period. The by-year table is the middle of the 10 runs; the chart and `trades.csv` show the first run. *Start dates per version* in the Run workflow form: 10 (default), 5 or 1.
- **Assumptions:** $100,000 start; borrowed money (more than 100% invested) pays 6.5% a year interest (`MARGIN_RATE`); fills at the next 15-minute candle's open with 0.02% slippage; a stop or target the price gaps through fills at the opening price; if one candle touches both stop and target, the stop counts (cautious).
- **Limits:** it uses today's S&P 100 members (stocks that left the index aren't tested, which flatters the results); no short-borrow fees; major S/R, Fibonacci and patterns don't open trades so they aren't tested.

## Testing new strategies (`strategies/` folder)
The live bot stays as it is; new ideas are tested in the backtest first. Each file in `strategies/` is one strategy, compared side by side with the bot and with buying and holding SPY, using the same money rules (1% risk per trade, max 20% per stock, max 10 positions, never more than 100% invested).
- **Run:** Actions → **Backtest** → Run workflow → *What to compare*: **strategies** (or **bot versions** for the bot's own variants).
- **Included examples:** `rsi2_pullback.py` (buy short dips in up-trends, sell on the bounce), `golden_cross.py` (50/200-day average cross), `momentum_52w.py` (buy new 1-year highs, sell below the 50-day low).
- **Add your own:** copy `_template.py` to a new name (e.g. `my_idea.py`), change `entries()` (when to buy, judged on the daily close; it buys at the next day's open) and `EXIT` (stop in ATRs, target, trailing N-day low, max days) and optionally `exits()` and `rank()`. Indicator helpers are in `strategies/tools.py` (sma, ema, rsi, atr, adx, highest, lowest, bollinger, crossed_above/below). Files starting with `_` are ignored.
- **Buying during the day:** a strategy can define `entry_level()` instead of `entries()`: one trigger price per day, bought on the first 15-min close above it (at the next candle's open). A strategy can also define `setups()` (each signal with its own stop and target, `EXIT["to_target"] = True` sells at the target) and `SIZE` (a fixed share of the account per trade, e.g. `1.0` = all in, instead of 1% risk).
- **Bitcoin strategies kept (19 Oct 2026):** only the ones that ended with more money than buying and holding Bitcoin (Jan 2021 – Oct 2026, every one of the 10 start dates). All trade **Bitcoin only** with the whole account, decide on daily closes and buy/sell at the next 00:00 UTC open. Run with *Market* = crypto, *coins* = **BTC**, *only* = `btc50_uprise btc_trend_50_200`.
  - **Your rule** (`btc50_uprise_*.py`, rules in `strategies/_structure.py`): buy only when the daily close crosses above the 50-day average while the 50-day average is rising (higher than 10 days ago); sell when a bounce fails below the highest high since entry (a **lower high**: a high above the 3 days before it, then 1-3 lower days), at the starting stop (under the last swing low, 1-3 ATR), or when price comes back to the 50-day; no buying back until the next cross.
    - `btc50_uprise_ma_f1.py`: close below the 50-day, lower high after 1 day (+23.7%/yr, worst drop −16.9%).
    - `btc50_uprise_touch_f1.py`: low touches the 50-day again, lower high after 1 day (+23.7%/yr, −18.1%).
    - `btc50_uprise_touch_f2.py` / `btc50_uprise_touch.py`: the same with the lower high after 2 / 3 days.
    - `btc50_uprise_ma.py`: close below the 50-day, lower high after 3 days.
    - `btc50_uprise_maonly.py`: close below the 50-day only (no lower-high exit).
    - `btc50_uprise_f1.py`: lower high after 1 day only (no 50-day exit).
  - `btc_trend_50_200.py`: hold Bitcoin while its daily close is above both its 50- and 200-day averages, else cash (+21.3%/yr, −26.6%).
  - **Shorts (backtest only, added 19 Oct 2026; Alpaca can't short crypto):** `btc50_short_ma_f1.py` is the exact mirror of `btc50_uprise_ma_f1.py` (the rule run on the chart turned upside down, `side="short"` in `_structure.py`): sell short when the close crosses below a falling 50-day; cover on a higher low after 1 day, a close back above the 50-day, or the starting stop above the last swing high. `btc50_both_ma_f1.py` trades both directions with the whole account, one position at a time. A strategy file sets `SIDE = "short"` or `"both"` (with `short_entries()` / `short_exits()`) for the backtest to take shorts. No borrow or funding fees are charged on shorts. `btc50_short_2h/1h/30m/15m.py` run the same short rule on 2-hour / 1-hour / 30-minute / 15-minute candles (counted in candles: 50-candle average, 10 candles for "falling"); a strategy file sets `TF` (e.g. `"2h"`) for the backtest to build those candles from the 15-min prices, decide at each candle's close and trade at the next 15-min open (several trades a day allowed).
  - Removed on 19 Oct: every crypto / Bitcoin strategy that ended below buy & hold (crypto breakout, MA cross, momentum, rotation, Turtle, squeeze, ADX, Chandelier, early entries near support, short-term Bitcoin, the other 50-day / lower-high and trend-holding variants, take profits). Their results stay in `reports/backtest/`.
- **Be careful with the results:** the more ideas you try, the more likely one looks good by luck. Check a winner on years it wasn't tuned on before changing the live bot.

## Crypto: the Bitcoin bot
Since 19 Oct 2026 the crypto bot trades **only Bitcoin** with the best Bitcoin strategy of the backtests, `strategies/btc50_uprise_ma_f1.py` (+23.7%/yr from Jan 2021 vs +17.3% for holding Bitcoin, worst drop −16.9% vs −76.7%; see *Testing new strategies*). `btc_bot.py`:
- **Buys** when Bitcoin's daily close crosses above its 50-day average while the 50-day average is rising (higher than 10 days ago).
- **Sells** when a bounce fails below the highest high since the buy (a lower high: a high above the 3 days before it, then one lower day), when a close falls below the 50-day average, or when a close falls below the starting stop (the last swing low before the buy, 1-3 ATR below). No buying back until the next fresh cross; no take profit.
- **When:** every decision uses the completed daily candle (UTC day) and is carried out by the next run, **once a day at ~00:07 UTC** (02:07 Milan in summer) on a GitHub timer — exactly like the backtest (decide on the close, trade at the next open). If a run is missed, it still buys the next day; a cross older than that is skipped (it waits for the next one). There is no stop order in Alpaca: all exits are judged on daily closes.
- **Money:** Alpaca paper account #1 (shared with the stock bot). A buy puts the whole crypto budget into Bitcoin: 50% of the account equity (`CRYPTO_BUDGET` in `crypto_trader.py`), minus any other crypto held. Orders are tagged `crypto-btcrule-…` ("Bitcoin 50-day rule" in the paper report).
- **Telegram:** one message a day in the crypto chat: buy / sell with the reason, or the status (holding: entry, highest high, stop; in cash: the 50-day average and what the next buy needs).
- **Run by hand / dry run:** Actions → **Crypto scan** → *strategy* **bot**, tick **Dry run** to see what it would do without placing orders.
- **Patterns only** (same workflow, *strategy* **patterns only**): chart-pattern alerts on the top 10 coins, no trades (`crypto_scanner.py`).
- **Separate Telegram chat (optional):** add a repository secret `TELEGRAM_CHAT_ID_CRYPTO` with the chat ID of another group (the same bot must be a member). Crypto scans and crypto backtests then go there; without it they go to your usual chat.
- **Which coins:** the ranking is read from CoinGecko every run (CoinPaprika, then a built-in list as backups). **Stablecoins** (USDT, USDC...) and gold tokens are skipped because their price doesn't move, and coins **Alpaca doesn't trade** (e.g. BNB, TRX) are skipped because they can't be paper traded; the next coins in the ranking take their place. Coins too new for the 200-day MA still get the other alerts.
- **Prices:** Alpaca's crypto data (real-time, 24/7, free), Yahoo Finance as backup. Crypto never closes, so a "day" is a **UTC day** (02:00 → 02:00 Milan in summer, 01:00 → 01:00 in winter), the daily candle most crypto charts use. Every run analyzes the candle that has just closed, at any hour, weekends included.
- **Old top-10 breakout trading (replaced by the Bitcoin bot on 19 Oct 2026; `crypto_scanner.py` no longer trades)** — ⭐ STRONG LONG signals only, same rules as the stocks: 1% risk per trade, stop 2× daily ATR, trailing exit below the 20-day low. Differences:
  - **Fractional amounts** (e.g. 0.0123 BTC). Alpaca's crypto fee is **0.25%** per buy or sell for small accounts, taken from the coin you receive; the stop order uses the amount actually in the account, so it always matches.
  - **Stop:** Alpaca has no bracket orders for crypto, so the stop is a separate **stop-limit** sell (limit 1% below the stop) placed right after the entry fills. It stays active between runs. If the entry hasn't filled yet, the next run places the stop.
  - **Trend exit:** checked at **every run** (the bot doesn't run on a timer), not at a fixed time.
  - **Longs only:** Alpaca can't short crypto or lend money for it.
  - **Limits:** max 5 coins, max 3 new per run, max 20% of equity per coin, and all crypto together at most **50% of the paper account** (`CRYPTO_BUDGET`) so the stock bot keeps the rest. Crypto positions don't take any of the stock bot's 10 position slots.
  - Every crypto position in the paper account is treated as the bot's own: it gets the trend-exit check and a ⚠️ warning if it has no stop order.
  - No Pine Script for crypto trades yet.
- **Backtest:** Actions → **Backtest** → Run workflow → *Market*: **crypto**. Same versions and `strategies/` files as for stocks, on the current top 10 coins, with a fee per buy and sell set by *Fee* (**0.05% futures** by default since 19 Oct 2026, like Binance's taker fee; **0.25% Alpaca** = what the live bot pays; results with 0.05% go to folders ending in `-fee005`; funding payments on futures are not included), 0.05% slippage, no borrowing, and **buy & hold BTC** as the benchmark. Start dates are a week (7 days) apart. Alpaca's crypto history is shorter than its stock history; if it doesn't reach back as far as the years you pick, the test starts where the prices start (the report shows the dates). The first run downloads the 15-minute history (a few minutes); later runs reuse the saved copy. *Coins*: **top 10** (default), **BTC** or **BTC ETH** only — a fixed coin has no coin-picking bias; results go to e.g. `reports/backtest/crypto-btc-strategies-8y/`.
- **Crypto strategies** (in `strategies/`, tested only when *Market* is **crypto**; `MARKETS = ("crypto",)` at the top of a file limits it to crypto): the Bitcoin strategies listed in *Testing new strategies* above. The report also shows the live bot (**Bot now**) and **Bot now + BTC filter** (the live bot's signals, taken only while BTC is above its 50-day average). Any strategy can use the market filter with `tools.market_up(d)` (SPY for stocks, BTC for crypto).
- **Strategy files on crypto:** they get daily candles too, but crypto has 365 trading days a year, so "252 days" in a strategy means about 8 months for crypto instead of a year.
- **Running it on a timer later:** the workflow file has the lines to put back (every 4 hours ≈ 180 runs a month). Running every 15 minutes, 24/7, needs about 2,900 runs a month, more than the 2,000 free minutes of a private repo.

## Charts
Every alert comes with a **candlestick chart on a black background** (green = up candle, red = down candle):
- **Strategy alert:** Bollinger Bands (shaded), Donchian channel, 200-day MA (daily), squeeze markers.
- **Major S/R break:** the broken level as a thick cyan line. **Fibonacci:** the swing (dashed) and the 0 / 38.2 / 50 / 61.8 / 100% levels in orange.
- **Pattern breakout:** the chart switches to the pattern's timeframe (daily or hourly) and draws the pattern in yellow: lines, labelled swing points (LS/Head/RS, 1-2-3, X-A-B-C-D…), plus entry, stop and target.
- If a stock gets a strategy alert while a pattern is still **forming** on that chart, the forming pattern is drawn too.


## Chart patterns (`patterns.py`)
Recognised automatically on the **daily and hourly** charts:
- **Reversal:** double top/bottom, triple top/bottom, head & shoulders (and inverse), rising/falling wedge, rounding bottom, bump & run, diamond (top/bottom)
- **Continuation:** ascending/descending/symmetrical triangle, rectangle, bull/bear flag, bull/bear pennant
- **Harmonic:** ABCD, Gartley, Butterfly

How it works: swing highs/lows are found with a zigzag that ignores moves smaller than 2× ATR, and each pattern is checked against those swings.
- **Forming:** listed in one 👀 watchlist message per check (each pattern only once).
- **Breakout:** a chart alert with the pattern drawn, plus entry, stop and target (target = the pattern's height, the usual "measured move"). Breakouts need the candle's volume at least 1.3× its 20-candle average and a clear close beyond the line. Harmonics alert when price reaches the completion zone and turns.
- **Diamond:** swings first spread out (higher highs, lower lows) then narrow again. After a rise it's a top (breaks down), after a fall a bottom (breaks up); if the trend before it isn't clear, both breakouts are watched. Rare, so expect few.

### Patterns-only scan (stocks and crypto)
Just the chart patterns above, without the bot's strategy alerts, and **no trades**.
- **Run it:** Actions → **S&P 500 scan** (stocks) or **Crypto scan** (crypto) → Run workflow → *What to scan for*: **patterns only** → Run. Works at any time, also when the market is closed.
- **Formed (breakout):** a chart alert for every pattern that broke out on the latest candle **or recently** (last 3 daily candles / last 7 hourly candles; the message says "2 days ago" etc.), with the pattern drawn, entry, stop and target.
- **Forming:** one 👀 watchlist message with **every** pattern forming right now, on the daily and hourly charts; 🆕 marks the ones that weren't there at the previous pattern scan.
- **Only your selected patterns** are reported (see *Your pattern selection* below).
- A ✅ message at the end counts what was found and lists the selection. Stock pattern alerts go to the stock chat, crypto ones to the crypto chat.
- Pattern scans keep their own memory, so they never hide or repeat alerts of the normal bot (and the other way round). The timer (cron-job.org) keeps running the normal bot.

### Backtesting the patterns
Actions → **Backtest** → Run workflow → *Market*: **stocks** or **crypto**, *What to compare*: **patterns** → Run.
- Replays the **daily** chart day by day: a pattern that breaks out on a day's close (same rules as the live alerts, incl. the 1.3× volume check) is traded at the next day's open, with **the pattern's own stop and target** (the measured move), sold after 60 trading days if neither is hit. Same money rules as the bot (1% risk per trade, max 20% per position, no borrowing).
- **Versions:** (1) up patterns only, (2) up + down patterns (shorts; test only for crypto), (3) up patterns only while SPY/BTC is in an up-trend, (4) up patterns with a trailing exit (20-day low) instead of the target. Compared with buying and holding SPY/BTC.
- **Version 7** = the pattern paper trader's exact rules: your selected **up** patterns, ABCD sold at its target, the others with the trailing exit, no 60-day limit (stocks only).
- **Versions 5 and 6** trade only your selected patterns, with the pattern's target (5) or the trailing exit (6). They appear when the selection for that market isn't empty.
- **Pattern scorecard** (🧩 message + `patterns.csv`): every breakout traded on its own, without the position limits, per pattern and direction (↑/↓): number of trades, win rate, **avgR** with the pattern's stop and target, how often the target was reached, and **trail** = average R with the pattern's stop and a trailing exit (sold on a close below the 20-day low, no target). R = profit in units of the risk; above 0 = made money on average. Under ~20 trades a pattern's figures are mostly luck.
- **Split check** (✂️ message): picks the patterns that did well on the **first half** of the period only (15+ trades, average R above 0), then shows how they did on the **second half**, which they weren't picked on, next to all patterns. If they still beat "all patterns" in the second half, the pick is probably real; if not, it was luck. Below it, a table of every pattern's results in each half.

### Pattern paper trader (`pattern_trader.py`)
Trades your selected stock patterns in a **separate Alpaca paper account**, so its results never mix with the breakout bot's (each has its own $100,000).
- **Setup (once):** in Alpaca, open a second paper account and generate its API keys. In GitHub add two repository secrets: `ALPACA_PATTERN_KEY_ID` and `ALPACA_PATTERN_SECRET_KEY`. Without them the pattern trader does nothing. Optional: `TELEGRAM_CHAT_ID_PATTERNS` = the chat ID of a separate Telegram group (the same bot must be a member). Then everything about patterns goes there: the pattern trader's trades and morning summary, its part of the daily 📒 paper report, and the stock **patterns only** scans. Without it they go to the stock chat, marked 🧩.
- **When:** inside the normal S&P 500 scan (the timer), so it needs no extra setup: the **first scan after 9:30 New York** places the day's trades, and a scan in the **last 20 minutes before the close** checks the trend exits. Each happens once a day.
- **Signal:** a selected **up** pattern (`SELECTED["stocks"]` in `patterns.py`) broke out on the daily chart at **yesterday's close**, with the same rules as the alerts (incl. 1.3× volume). Bought at today's open with a market order. Each pattern is traded once.
- **Stop:** the pattern's own stop, sent with the entry (Alpaca keeps it active, good-till-cancelled).
- **Exit per pattern** (`EXIT_BY_PATTERN` at the top of `pattern_trader.py`): **ABCD** sells at the pattern's target (bracket order). The others have **no target** and are sold when the price is below the **20-day low** in the last 20 minutes before the close (if that check was missed, at the next open when yesterday's close was below it).
- **Money:** 1% of equity at risk per trade, max 20% of equity per stock, max 10 positions, max 3 new trades a day, no borrowing. Longs only.
- **Messages (🧩):** each trade with its pattern chart, stop and exit rule; stops, targets and trend exits with the profit or loss; one summary per morning.
- **Try it by hand:** Actions → **Pattern trader** → Run workflow. *Dry run* (on by default) shows what it would buy right now without placing orders. With dry run off it places today's trades if the morning run was missed.
- Backtest version 7 uses exactly these rules.

### Paper report (`paper_report.py`)
Every weekday after the US close (~23:20 Milan in summer time, ~22:20 in winter) the **Paper report** workflow rebuilds a report of **both** paper accounts from Alpaca's own records and commits it to the repo:
- `reports/paper/README.md`: account value (today and since the start, vs SPY), worst drop, open positions with their stop and profit, closed trades by what opened them (breakout daily / 15-min, crypto, or the chart pattern), last 10 trades.
- `reports/paper/<account>_trades.csv` (every closed trade with profit, % and R), `<account>_open.csv`, `<account>_equity.csv` (value per day next to SPY with the same money). `<account>` = `breakout` or `patterns`.
- A 📒 summary on Telegram (stock chat).
- Run it any time: Actions → **Paper report** → Run workflow. Everything is rebuilt from Alpaca each time, so a missed day loses nothing.
- "What opened it" comes from a tag on each order (`bot-…`, `crypto-…`, `pat-<pattern>-…`); trades placed before the tag existed show "unknown". R = profit ÷ the money at risk between entry and the first stop.
- The daily commit also keeps the repo active, so GitHub never pauses its schedules.

### Your pattern selection
The list `SELECTED` at the top of `patterns.py` decides which patterns the **patterns-only scans** alert on and which ones backtest versions 5-6 trade, separately for stocks and crypto:
```python
SELECTED = {
    "stocks": ["ABCD up", "Falling wedge up", "Inverse head & shoulders up", "Triple bottom up", "Gartley up"],
    "crypto": [],          # empty = every pattern
}
```
Write each pattern's name as in the scorecard plus `up` or `down` (`↑`/`↓` work too); a name without a direction means both. The stock list holds the up patterns that made money (with the exit the pattern trader uses for them) in all four halves of the 5-year and 8-year S&P 100 split checks; change it as new results come in. The normal bot's alerts always show every pattern.
- **Hourly and 15-minute patterns:** *Pattern candles* = **1h** or **15m** (default **daily**). The same patterns are found on hourly (or 15-min) candles, built from the 15-min prices; a breakout at a candle's close (incl. the 1.3× volume check on that candle) is traded at the open of the next 15-min candle (for stocks, the next morning if it was the day's last candle). Target trades are sold after 10 trading days if neither stop nor target is hit; the trailing versions use the same 20-day low as the daily ones. The scorecard and split check are replayed on 15-min candles. Results go to `reports/backtest/<market>-patterns-1h-<years>y` (or `-15m-`).
  - **Time:** hourly ≈ 25-30 min for 8 years of the S&P 100; 15-min is ~4× slower, so it tests **at most 5 years** (3 years ≈ 35 min is a good choice).
  - **First intraday run:** the saved 15-min prices didn't keep volume, so the first hourly/15-min pattern test downloads them again with volume (one time, ~45-60 min for 8 years). Later runs reuse them.
- Forming patterns can't be traded, so they aren't backtested.


## Notes
- **Prices:** Alpaca's official API on the free plan, two feeds:
  - **15-minute candles and live prices:** the **real-time IEX feed**, so each check sees the candle that has just closed. IEX is one exchange: its prices match the full market closely for S&P 100 stocks, but its volume is only IEX's share. Volume is only ever compared with IEX's own recent average, so the comparison stays fair.
  - **Daily history:** the complete all-exchange feed (15+ minutes delayed on the free plan, which doesn't matter for past days). Today's daily candle is built from the live IEX snapshot.
- **Backup:** if Alpaca fails for some tickers or the whole run, those tickers come from Yahoo Finance automatically. Without Alpaca keys, the bot uses Yahoo for everything.
- A private repo gets 2,000 free minutes a month. This bot uses roughly 1,300–1,500 (27 scans per weekday). Runs outside the trading window stop after a few seconds without downloading anything.
- GitHub pauses schedules after 60 days with no commits. Any small edit restarts them.
- Signals are possible setups, not advice. Use the paper account's results to judge the strategy before ever trading real money.
