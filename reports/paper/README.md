# Paper trading report

Updated 2026-10-26 20:27 New York time by `paper_report.py` (rebuilt from Alpaca every weekday after the close).
Files: `<account>_trades.csv` (closed trades), `<account>_open.csv` (open positions), `<account>_equity.csv` (account value per day vs SPY).

## Breakout bot (stocks + crypto)

| | |
|---|---|
| Account value | $100,836.27 (+0.0% today) |
| Since 12 Oct 2026 | +0.8% (SPY -1.6%) |
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
| Account value | $98,449.16 (-1.2% today) |
| Since 15 Oct 2026 | -1.6% (SPY -0.8%) |
| Worst drop from a high | -3.0% |
| Closed trades | 5, win 20%, avg -1.01R, profit factor 0.72, $-444.98 |
| Open positions | 13 ($+24.57 not yet realised, 162% invested) |

**Open positions**

| Symbol | Opened by | Since | Entry | Now | Stop | P/L | R |
|---|---|---|---|---|---|---|---|
| ADBE | deck Harami up | 2026-10-23 | 270.06 | 257.40 | 254.87 | $-12.66 (-4.7%) | -0.83 |
| AMT | deck Bull flag up | 2026-10-26 | 189.93 | 188.28 | 184.27 | $-81.05 (-0.9%) | -0.29 |
| C | ABCD | 2026-10-16 | 124.16 | 121.25 | nan | $-343.29 (-2.3%) |  |
| COST | deck Bull pennant up | 2026-10-23 | 1019.61 | 1005.06 | 998.04 | $-276.40 (-1.4%) | -0.67 |
| GOOGL | deck Symmetrical triangle down | 2026-10-21 | 333.63 | 318.56 | nan | $+437.07 (+4.5%) |  |
| ISRG | deck Rising wedge up | 2026-10-23 | 445.54 | 449.00 | 424.87 | $+155.70 (+0.8%) | +0.17 |
| LLY | Triple bottom | 2026-10-16 | 1265.00 | 1282.89 | 1106.68 | $+107.34 (+1.4%) | +0.11 |
| MDT | deck Bull flag up | 2026-10-23 | 96.15 | 94.79 | 92.63 | $-285.93 (-1.4%) | -0.39 |
| MMM | deck Descending triangle up | 2026-10-21 | 170.69 | 172.79 | nan | $+121.80 (+1.2%) |  |
| NEE | deck Symmetrical triangle up | 2026-10-23 | 82.25 | 82.10 | 78.32 | $-37.06 (-0.2%) | -0.04 |
| UBER | deck Symmetrical triangle down | 2026-10-21 | 68.80 | 66.15 | nan | $+384.25 (+3.9%) |  |
| WFC | Falling wedge | 2026-10-19 | 82.93 | 82.68 | 77.09 | $-40.25 (-0.3%) | -0.04 |
| WMT | deck Bull flag up | 2026-10-23 | 121.94 | 120.13 | 117.35 | $-104.95 (-1.5%) | -0.39 |

**Closed trades by what opened them**

| Opened by | Trades | Win | Avg R | P/L |
|---|---|---|---|---|
| deck Falling wedge up | 1 | 0% | -1.01 | $-293.88 |
| deck Gap down down | 1 | 100% |  | $+1,135.72 |
| deck Inverse head shoulders up | 1 | 0% | -1.03 | $-175.42 |
| deck Symmetrical triangle up | 2 | 0% | -1.00 | $-1,111.40 |

**Last closed trades**

| Symbol | Opened by | In | Out | Entry | Exit | P/L | R |
|---|---|---|---|---|---|---|---|
| INTU | deck Symmetrical triangle up | 2026-10-23 | 2026-10-26 | 353.83 | 334.65 | $-1,093.24 (-5.4%) | -1.01 |
| MS | deck Falling wedge up | 2026-10-22 | 2026-10-23 | 185.09 | 179.75 | $-293.88 (-2.9%) | -1.01 |
| COF | deck Inverse head shoulders up | 2026-10-22 | 2026-10-22 | 207.71 | 204.13 | $-175.42 (-1.7%) | -1.03 |
| LRCX | deck Gap down down | 2026-10-21 | 2026-10-22 | 266.29 | 235.60 | $+1,135.72 (+11.5%) |  |
| CMCSA | deck Symmetrical triangle up | 2026-10-21 | 2026-10-21 | 21.96 | 21.92 | $-18.16 (-0.2%) | -1.00 |
