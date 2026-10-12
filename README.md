# S&P 500 Trade Alert Bot

Scans all ~500 S&P 500 stocks during US market hours: **every 15 minutes on 15-minute candles for the first 3 hours after the open** (about 16:00–18:45 Milan time), then **every hour on hourly candles** until the close (about 18:50–22:50 Milan time).
It computes indicators, support/resistance levels and trend lines, then sends a Telegram
message with a chart whenever a setup appears. It **never places trades**. It only alerts you.

| File | What it does |
|---|---|
| `scanner.py` | Downloads prices, computes indicators, levels and trend lines, sends alerts |
| `rules.py` | **Your trading rules.** The only file you normally edit |
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
It takes about 3–5 minutes. The log's first lines show `Alpaca: 50X tickers OK` when the keys work. Any current setups arrive on Telegram. After that it runs automatically every weekday.

## How it works
- **Daily chart (trend):** decides whether longs or shorts are allowed (placeholder rule: price above EMA50 above EMA200 = long).
- **Entry chart:** 15-minute candles from 9:30 to 12:30 New York time, hourly candles after that. Each check looks at the candle that just closed.
- **Support/resistance:** swing highs and lows that cluster within half an ATR, with at least 2 touches.
- **Trend lines:** a rising line from the lowest swing low to the latest one, and a falling line from the highest swing high to the latest one. A line is discarded if price broke through it too often.
- **No duplicates:** each signal on each bar is sent only once.

Indicators already computed and available in `rules.py`: EMA 20/50/200, RSI(14), MACD(12,26,9), ATR(14), 20-bar average volume.

## Notes
- **Prices:** Alpaca's official API on the free plan. Data covers all US exchanges (accurate prices and volume) but must be 15+ minutes old, so each check runs about 17–20 minutes after its candle closes. A setup reaches you about 20 minutes after its candle closes. Good for swing trades, not for scalping.
- **Backup:** if Alpaca fails for some tickers or the whole run, those tickers come from Yahoo Finance automatically. Without Alpaca keys, the bot uses Yahoo for everything.
- A private repo gets 2,000 free minutes a month. This bot uses roughly 1,100–1,300. Runs outside the trading window stop after a few seconds without downloading anything.
- GitHub pauses schedules after 60 days with no commits. Any small edit restarts them.
- Signals are possible setups, not advice. Backtest your rules before trading real money.
