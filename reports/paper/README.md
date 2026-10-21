# Paper trading report

Updated 2026-10-21 17:47 New York time by `paper_report.py` (rebuilt from Alpaca every weekday after the close).
Files: `<account>_trades.csv` (closed trades), `<account>_open.csv` (open positions), `<account>_equity.csv` (account value per day vs SPY).

## Breakout bot (stocks + crypto)

| | |
|---|---|
| Account value | $99,103.06 (+0.3% today) |
| Since 12 Oct 2026 | -0.9% (SPY -0.6%) |
| Worst drop from a high | -2.5% |
| Closed trades | 19, win 74%, avg +0.21R, profit factor 0.79, $-616.40 |
| Open positions | 10 ($-273.98 not yet realised, 100% invested) |

**Open positions**

| Symbol | Opened by | Since | Entry | Now | Stop | P/L | R |
|---|---|---|---|---|---|---|---|
| ADBE | breakout (daily) | 2026-10-16 | 254.73 | 255.58 | 238.99 | $+51.85 (+0.3%) | +0.05 |
| COP | breakout (15-min) | 2026-10-16 | 143.67 | 145.52 | 137.58 | $+258.76 (+1.3%) | +0.30 |
| CRM | breakout (daily) | 2026-10-16 | 250.13 | 243.06 | 234.48 | $-431.27 (-2.8%) | -0.45 |
| DIS | breakout (15-min) | 2026-10-16 | 112.10 | 111.61 | 107.99 | $-1.47 (-0.4%) | -0.12 |
| LIN | breakout (15-min) | 2026-10-16 | 499.74 | 498.80 | 484.05 | $-0.94 (-0.2%) | -0.06 |
| NOW | breakout (daily) | 2026-10-15 | 148.66 | 154.10 | 136.82 | $+462.40 (+3.7%) | +0.46 |
| RTX | breakout (daily) | 2026-10-20 | 195.52 | 191.34 | 187.03 | $-58.52 (-2.1%) | -0.49 |
| SBUX | breakout (daily) | 2026-10-20 | 100.00 | 99.05 | 95.16 | $-0.95 (-0.9%) | -0.20 |
| WFC | breakout (15-min) | 2026-10-16 | 84.43 | 82.42 | 80.28 | $-399.99 (-2.4%) | -0.48 |
| XOM | breakout (daily) | 2026-10-20 | 179.82 | 178.01 | 173.19 | $-153.85 (-1.0%) | -0.27 |

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
| Account value | $100,886.52 (+1.1% today) |
| Since 15 Oct 2026 | +0.9% (SPY +0.2%) |
| Worst drop from a high | -0.6% |
| Closed trades | 1, win 0%, avg -1.00R, $-18.16 |
| Open positions | 7 ($+904.70 not yet realised, 74% invested) |

**Open positions**

| Symbol | Opened by | Since | Entry | Now | Stop | P/L | R |
|---|---|---|---|---|---|---|---|
| C | ABCD | 2026-10-16 | 124.16 | 124.00 | nan | $-18.74 (-0.1%) |  |
| GOOGL | deck Symmetrical triangle down | 2026-10-21 | 333.63 | 335.03 | nan | $-40.56 (-0.4%) |  |
| LLY | Triple bottom | 2026-10-16 | 1265.00 | 1271.00 | 1106.68 | $+36.00 (+0.5%) | +0.04 |
| LRCX | deck Gap down down | 2026-10-21 | 266.29 | 246.92 | nan | $+716.64 (+7.3%) |  |
| MMM | deck Descending triangle up | 2026-10-21 | 170.69 | 172.95 | nan | $+131.07 (+1.3%) |  |
| UBER | deck Symmetrical triangle down | 2026-10-21 | 68.80 | 67.68 | nan | $+162.40 (+1.6%) |  |
| WFC | Falling wedge | 2026-10-19 | 82.93 | 82.42 | 77.09 | $-82.11 (-0.6%) | -0.09 |

**Closed trades by what opened them**

| Opened by | Trades | Win | Avg R | P/L |
|---|---|---|---|---|
| deck Symmetrical triangle up | 1 | 0% | -1.00 | $-18.16 |

**Last closed trades**

| Symbol | Opened by | In | Out | Entry | Exit | P/L | R |
|---|---|---|---|---|---|---|---|
| CMCSA | deck Symmetrical triangle up | 2026-10-21 | 2026-10-21 | 21.96 | 21.92 | $-18.16 (-0.2%) | -1.00 |
