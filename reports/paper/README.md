# Paper trading report

Updated 2026-10-23 19:52 New York time by `paper_report.py` (rebuilt from Alpaca every weekday after the close).
Files: `<account>_trades.csv` (closed trades), `<account>_open.csv` (open positions), `<account>_equity.csv` (account value per day vs SPY).

## Breakout bot (stocks + crypto)

| | |
|---|---|
| Account value | $100,838.36 (-0.1% today) |
| Since 12 Oct 2026 | +0.8% (SPY -2.3%) |
| Worst drop from a high | -2.5% |
| Closed trades | 29, win 66%, avg +0.21R, profit factor 1.23, $+844.92 |
| Open positions | 0 ($+0.00 not yet realised, 0% invested) |

**Closed trades by what opened them**

| Opened by | Trades | Win | Avg R | P/L |
|---|---|---|---|---|
| breakout (15-min) | 8 | 50% | -0.11 | $-1,875.06 |
| breakout (daily) | 8 | 62% | +0.25 | $+1,947.25 |
| unknown | 13 | 77% | +0.42 | $+772.73 |

**Last closed trades**

| Symbol | Opened by | In | Out | Entry | Exit | P/L | R |
|---|---|---|---|---|---|---|---|
| ADBE | breakout (daily) | 2026-10-16 | 2026-10-23 | 254.73 | 268.79 | $+857.57 (+5.5%) | +0.89 |
| WFC | breakout (15-min) | 2026-10-16 | 2026-10-23 | 84.43 | 81.78 | $-526.63 (-3.1%) | -0.64 |
| COP | breakout (15-min) | 2026-10-16 | 2026-10-23 | 143.67 | 147.70 | $+563.98 (+2.8%) | +0.66 |
| CRM | breakout (daily) | 2026-10-16 | 2026-10-23 | 250.13 | 248.30 | $-111.41 (-0.7%) | -0.12 |
| XOM | breakout (daily) | 2026-10-20 | 2026-10-23 | 179.82 | 179.34 | $-40.94 (-0.3%) | -0.07 |
| RTX | breakout (daily) | 2026-10-20 | 2026-10-23 | 195.52 | 192.15 | $-47.18 (-1.7%) | -0.40 |
| LIN | breakout (15-min) | 2026-10-16 | 2026-10-23 | 499.74 | 510.94 | $+11.20 (+2.2%) | +0.71 |
| NOW | breakout (daily) | 2026-10-15 | 2026-10-23 | 148.66 | 157.55 | $+755.65 (+6.0%) | +0.75 |
| SBUX | breakout (daily) | 2026-10-20 | 2026-10-23 | 100.00 | 101.66 | $+1.66 (+1.7%) | +0.34 |
| DIS | breakout (15-min) | 2026-10-16 | 2026-10-23 | 112.10 | 111.24 | $-2.58 (-0.8%) | -0.21 |

## Pattern trader (selected chart patterns)

| | |
|---|---|
| Account value | $99,456.79 (-2.0% today) |
| Since 15 Oct 2026 | -0.5% (SPY -1.5%) |
| Worst drop from a high | -2.0% |
| Closed trades | 4, win 25%, avg -1.01R, profit factor 2.33, $+648.26 |
| Open positions | 13 ($-1,190.40 not yet realised, 191% invested) |

**Open positions**

| Symbol | Opened by | Since | Entry | Now | Stop | P/L | R |
|---|---|---|---|---|---|---|---|
| ADBE | deck Harami up | 2026-10-23 | 270.06 | 263.03 | 254.87 | $-527.15 (-2.6%) | -0.46 |
| C | ABCD | 2026-10-16 | 124.16 | 120.59 | nan | $-421.12 (-2.9%) |  |
| COST | deck Bull pennant up | 2026-10-23 | 1019.61 | 1002.01 | 998.04 | $-334.35 (-1.7%) | -0.82 |
| GOOGL | deck Symmetrical triangle down | 2026-10-21 | 333.63 | 321.15 | nan | $+361.96 (+3.7%) |  |
| INTU | deck Symmetrical triangle up | 2026-10-23 | 353.83 | 343.69 | 334.88 | $-578.20 (-2.9%) | -0.54 |
| ISRG | deck Rising wedge up | 2026-10-23 | 445.54 | 452.00 | 424.87 | $+290.70 (+1.4%) | +0.31 |
| LLY | Triple bottom | 2026-10-16 | 1265.00 | 1279.00 | 1106.68 | $+84.00 (+1.1%) | +0.09 |
| MDT | deck Bull flag up | 2026-10-23 | 96.15 | 95.41 | 92.63 | $-155.73 (-0.8%) | -0.21 |
| MMM | deck Descending triangle up | 2026-10-21 | 170.69 | 173.46 | nan | $+160.66 (+1.6%) |  |
| NEE | deck Symmetrical triangle up | 2026-10-23 | 82.25 | 81.65 | 78.32 | $-147.70 (-0.7%) | -0.15 |
| UBER | deck Symmetrical triangle down | 2026-10-21 | 68.80 | 66.98 | nan | $+263.90 (+2.6%) |  |
| WFC | Falling wedge | 2026-10-19 | 82.93 | 82.46 | 77.09 | $-75.67 (-0.6%) | -0.08 |
| WMT | deck Bull flag up | 2026-10-23 | 121.94 | 120.01 | 117.35 | $-111.70 (-1.6%) | -0.42 |

**Closed trades by what opened them**

| Opened by | Trades | Win | Avg R | P/L |
|---|---|---|---|---|
| deck Falling wedge up | 1 | 0% | -1.01 | $-293.88 |
| deck Gap down down | 1 | 100% |  | $+1,135.72 |
| deck Inverse head shoulders up | 1 | 0% | -1.03 | $-175.42 |
| deck Symmetrical triangle up | 1 | 0% | -1.00 | $-18.16 |

**Last closed trades**

| Symbol | Opened by | In | Out | Entry | Exit | P/L | R |
|---|---|---|---|---|---|---|---|
| MS | deck Falling wedge up | 2026-10-22 | 2026-10-23 | 185.09 | 179.75 | $-293.88 (-2.9%) | -1.01 |
| COF | deck Inverse head shoulders up | 2026-10-22 | 2026-10-22 | 207.71 | 204.13 | $-175.42 (-1.7%) | -1.03 |
| LRCX | deck Gap down down | 2026-10-21 | 2026-10-22 | 266.29 | 235.60 | $+1,135.72 (+11.5%) |  |
| CMCSA | deck Symmetrical triangle up | 2026-10-21 | 2026-10-21 | 21.96 | 21.92 | $-18.16 (-0.2%) | -1.00 |
