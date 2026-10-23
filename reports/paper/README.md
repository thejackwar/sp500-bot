# Paper trading report

Updated 2026-10-23 07:40 New York time by `paper_report.py` (rebuilt from Alpaca every weekday after the close).
Files: `<account>_trades.csv` (closed trades), `<account>_open.csv` (open positions), `<account>_equity.csv` (account value per day vs SPY).

## Breakout bot (stocks + crypto)

| | |
|---|---|
| Account value | $100,614.14 (-0.3% today) |
| Since 12 Oct 2026 | +0.6% (SPY -1.8%) |
| Worst drop from a high | -2.5% |
| Closed trades | 19, win 74%, avg +0.21R, profit factor 0.79, $-616.40 |
| Open positions | 10 ($+1,237.10 not yet realised, 100% invested) |

**Open positions**

| Symbol | Opened by | Since | Entry | Now | Stop | P/L | R |
|---|---|---|---|---|---|---|---|
| ADBE | breakout (daily) | 2026-10-16 | 254.73 | 267.11 | 238.99 | $+755.18 (+4.9%) | +0.79 |
| COP | breakout (15-min) | 2026-10-16 | 143.67 | 146.65 | 137.58 | $+417.20 (+2.1%) | +0.49 |
| CRM | breakout (daily) | 2026-10-16 | 250.13 | 247.65 | 234.48 | $-151.28 (-1.0%) | -0.16 |
| DIS | breakout (15-min) | 2026-10-16 | 112.10 | 112.35 | 107.99 | $+0.75 (+0.2%) | +0.06 |
| LIN | breakout (15-min) | 2026-10-16 | 499.74 | 498.60 | 484.05 | $-1.14 (-0.2%) | -0.07 |
| NOW | breakout (daily) | 2026-10-15 | 148.66 | 155.40 | 136.82 | $+572.90 (+4.5%) | +0.57 |
| RTX | breakout (daily) | 2026-10-20 | 195.52 | 194.20 | 187.03 | $-18.48 (-0.7%) | -0.16 |
| SBUX | breakout (daily) | 2026-10-20 | 100.00 | 101.50 | 95.16 | $+1.50 (+1.5%) | +0.31 |
| WFC | breakout (15-min) | 2026-10-16 | 84.43 | 83.01 | 80.28 | $-282.58 (-1.7%) | -0.34 |
| XOM | breakout (daily) | 2026-10-20 | 179.82 | 179.15 | 173.19 | $-56.95 (-0.4%) | -0.10 |

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
| Account value | $101,405.20 (-0.1% today) |
| Since 15 Oct 2026 | +1.4% (SPY -1.0%) |
| Worst drop from a high | -0.6% |
| Closed trades | 3, win 33%, avg -1.02R, profit factor 5.87, $+942.14 |
| Open positions | 7 ($+464.13 not yet realised, 74% invested) |

**Open positions**

| Symbol | Opened by | Since | Entry | Now | Stop | P/L | R |
|---|---|---|---|---|---|---|---|
| C | ABCD | 2026-10-16 | 124.16 | 122.18 | nan | $-233.50 (-1.6%) |  |
| GOOGL | deck Symmetrical triangle down | 2026-10-21 | 333.63 | 323.05 | nan | $+306.86 (+3.2%) |  |
| LLY | Triple bottom | 2026-10-16 | 1265.00 | 1301.50 | 1106.68 | $+219.00 (+2.9%) | +0.23 |
| MMM | deck Descending triangle up | 2026-10-21 | 170.69 | 174.84 | nan | $+240.70 (+2.4%) |  |
| MS | deck Falling wedge up | 2026-10-22 | 185.09 | 183.37 | 179.83 | $-94.86 (-0.9%) | -0.33 |
| UBER | deck Symmetrical triangle down | 2026-10-21 | 68.80 | 68.71 | nan | $+13.05 (+0.1%) |  |
| WFC | Falling wedge | 2026-10-19 | 82.93 | 83.01 | 77.09 | $+12.88 (+0.1%) | +0.01 |

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
