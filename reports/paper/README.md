# Paper trading report

Updated 2026-10-22 19:59 New York time by `paper_report.py` (rebuilt from Alpaca every weekday after the close).
Files: `<account>_trades.csv` (closed trades), `<account>_open.csv` (open positions), `<account>_equity.csv` (account value per day vs SPY).

## Breakout bot (stocks + crypto)

| | |
|---|---|
| Account value | $100,918.23 (+2.0% today) |
| Since 12 Oct 2026 | +0.9% (SPY -1.8%) |
| Worst drop from a high | -2.5% |
| Closed trades | 19, win 74%, avg +0.21R, profit factor 0.79, $-616.40 |
| Open positions | 10 ($+1,541.19 not yet realised, 100% invested) |

**Open positions**

| Symbol | Opened by | Since | Entry | Now | Stop | P/L | R |
|---|---|---|---|---|---|---|---|
| ADBE | breakout (daily) | 2026-10-16 | 254.73 | 269.48 | 238.99 | $+899.75 (+5.8%) | +0.94 |
| COP | breakout (15-min) | 2026-10-16 | 143.67 | 145.63 | 137.58 | $+274.40 (+1.4%) | +0.32 |
| CRM | breakout (daily) | 2026-10-16 | 250.13 | 250.50 | 234.48 | $+22.57 (+0.1%) | +0.02 |
| DIS | breakout (15-min) | 2026-10-16 | 112.10 | 112.88 | 107.99 | $+2.34 (+0.7%) | +0.19 |
| LIN | breakout (15-min) | 2026-10-16 | 499.74 | 509.00 | 484.05 | $+9.26 (+1.9%) | +0.59 |
| NOW | breakout (daily) | 2026-10-15 | 148.66 | 156.50 | 136.82 | $+666.40 (+5.3%) | +0.66 |
| RTX | breakout (daily) | 2026-10-20 | 195.52 | 195.39 | 187.03 | $-1.82 (-0.1%) | -0.02 |
| SBUX | breakout (daily) | 2026-10-20 | 100.00 | 102.00 | 95.16 | $+2.00 (+2.0%) | +0.41 |
| WFC | breakout (15-min) | 2026-10-16 | 84.43 | 83.24 | 80.28 | $-236.81 (-1.4%) | -0.29 |
| XOM | breakout (daily) | 2026-10-20 | 179.82 | 178.68 | 173.19 | $-96.90 (-0.6%) | -0.17 |

**Closed trades by what opened them**

| Opened by | Trades | Win | Avg R | P/L |
|---|---|---|---|---|
| breakout (15-min) | 4 | 50% | -0.35 | $-1,921.03 |
| breakout (daily) | 2 | 100% | +0.31 | $+531.90 |
| unknown | 13 | 77% | +0.42 | $+772.73 |

**Last closed trades**

| Symbol | Opened by | In | Out | Entry | Exit | P/L | R |
|---|---|---|---|---|---|---|---|
| GM | breakout (15-min) | 2026-10-16 | 2026-10-20 | 85.67 | 77.32 | $-1,953.60 (-9.8%) | -1.96 |
| AMGN | unknown | 2026-10-15 | 2026-10-15 | 439.86 | 440.00 | $+6.44 (+0.0%) | +0.01 |
| CRM | breakout (15-min) | 2026-10-15 | 2026-10-15 | 245.35 | 243.80 | $-1.55 (-0.6%) | -0.09 |
| TMO | breakout (15-min) | 2026-10-15 | 2026-10-15 | 684.68 | 696.87 | $+12.19 (+1.8%) | +0.34 |
| LLY | breakout (15-min) | 2026-10-15 | 2026-10-15 | 1248.29 | 1270.22 | $+21.93 (+1.8%) | +0.31 |
| GILD | unknown | 2026-10-15 | 2026-10-15 | 159.72 | 158.57 | $-136.99 (-0.7%) | -0.14 |
| DIS | unknown | 2026-10-15 | 2026-10-15 | 110.87 | 111.44 | $+103.08 (+0.5%) | +0.13 |
| V | breakout (daily) | 2026-10-15 | 2026-10-15 | 397.90 | 402.19 | $+214.38 (+1.1%) | +0.31 |
| DHR | breakout (daily) | 2026-10-15 | 2026-10-15 | 228.71 | 232.49 | $+317.52 (+1.6%) | +0.31 |
| BRK.B | unknown | 2026-10-14 | 2026-10-15 | 525.21 | 532.02 | $+258.74 (+1.3%) | +0.60 |

## Pattern trader (selected chart patterns)

| | |
|---|---|
| Account value | $101,587.27 (+1.1% today) |
| Since 15 Oct 2026 | +1.6% (SPY -1.0%) |
| Worst drop from a high | -0.6% |
| Closed trades | 3, win 33%, avg -1.02R, profit factor 5.87, $+942.14 |
| Open positions | 7 ($+645.98 not yet realised, 74% invested) |

**Open positions**

| Symbol | Opened by | Since | Entry | Now | Stop | P/L | R |
|---|---|---|---|---|---|---|---|
| C | ABCD | 2026-10-16 | 124.16 | 122.60 | nan | $-183.94 (-1.3%) |  |
| GOOGL | deck Symmetrical triangle down | 2026-10-21 | 333.63 | 324.20 | nan | $+273.51 (+2.8%) |  |
| LLY | Triple bottom | 2026-10-16 | 1265.00 | 1309.00 | 1106.68 | $+264.00 (+3.5%) | +0.28 |
| MMM | deck Descending triangle up | 2026-10-21 | 170.69 | 175.56 | nan | $+282.46 (+2.9%) |  |
| MS | deck Falling wedge up | 2026-10-22 | 185.09 | 184.50 | 179.83 | $-32.71 (-0.3%) | -0.11 |
| UBER | deck Symmetrical triangle down | 2026-10-21 | 68.80 | 68.85 | nan | $-7.25 (-0.1%) |  |
| WFC | Falling wedge | 2026-10-19 | 82.93 | 83.24 | 77.09 | $+49.91 (+0.4%) | +0.05 |

**Closed trades by what opened them**

| Opened by | Trades | Win | Avg R | P/L |
|---|---|---|---|---|
| deck Gap down down | 1 | 100% |  | $+1,135.72 |
| deck Inverse head shoulders up | 1 | 0% | -1.03 | $-175.42 |
| deck Symmetrical triangle up | 1 | 0% | -1.00 | $-18.16 |

**Last closed trades**

| Symbol | Opened by | In | Out | Entry | Exit | P/L | R |
|---|---|---|---|---|---|---|---|
| COF | deck Inverse head shoulders up | 2026-10-22 | 2026-10-22 | 207.71 | 204.13 | $-175.42 (-1.7%) | -1.03 |
| LRCX | deck Gap down down | 2026-10-21 | 2026-10-22 | 266.29 | 235.60 | $+1,135.72 (+11.5%) |  |
| CMCSA | deck Symmetrical triangle up | 2026-10-21 | 2026-10-21 | 21.96 | 21.92 | $-18.16 (-0.2%) | -1.00 |
