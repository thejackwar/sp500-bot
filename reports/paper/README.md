# Paper trading report

Updated 2026-11-24 00:20 New York time by `paper_report.py` (rebuilt from Alpaca every weekday after the close).
Files: `<account>_trades.csv` (closed trades), `<account>_open.csv` (open positions), `<account>_equity.csv` (account value per day vs SPY).

## Breakout bot (stocks + crypto)

| | |
|---|---|
| Account value | $100,836.27 (+0.0% today) |
| Since 12 Oct 2026 | +0.8% (SPY -7.2%) |
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
| Account value | $95,018.99 (+1.6% today) |
| Since 15 Oct 2026 | -5.0% (SPY -6.4%) |
| Worst drop from a high | -7.7% |
| Closed trades | 48, win 40%, avg +0.09R, profit factor 0.67, $-4,768.86 |
| Open positions | 8 ($+155.64 not yet realised, 97% invested) |

**Open positions**

| Symbol | Opened by | Since | Entry | Now | Stop | P/L | R |
|---|---|---|---|---|---|---|---|
| AAPL | deck Gap up up | 2026-11-12 | 357.07 | 350.81 | 338.04 | $-37.54 (-1.8%) | -0.33 |
| BRK.B | deck Gap up up | 2026-11-11 | 578.64 | 574.00 | 559.61 | $-101.98 (-0.8%) | -0.24 |
| CMCSA | deck Harami up | 2026-11-05 | 25.06 | 24.73 | 23.10 | $-182.12 (-1.3%) | -0.17 |
| COST | deck Gap up up | 2026-11-05 | 1067.22 | 1071.50 | 1014.34 | $+51.35 (+0.4%) | +0.08 |
| LLY | deck Gap up up | 2026-11-10 | 1412.69 | 1370.60 | 1333.22 | $-336.70 (-3.0%) | -0.53 |
| MDLZ | deck Gap up up | 2026-11-10 | 66.35 | 66.09 | 64.69 | $-51.21 (-0.4%) | -0.16 |
| TSLA | deck Harami up | 2026-11-10 | 374.80 | 386.88 | 338.15 | $+422.80 (+3.2%) | +0.33 |
| WMT | deck Gap up up | 2026-11-10 | 126.04 | 129.80 | 123.03 | $+391.04 (+3.0%) | +1.25 |

**Closed trades by what opened them**

| Opened by | Trades | Win | Avg R | P/L |
|---|---|---|---|---|
| ABCD | 1 | 100% |  | $+273.08 |
| Falling wedge | 1 | 100% | +0.37 | $+346.21 |
| Triple bottom | 1 | 100% | +0.72 | $+679.68 |
| deck Ascending triangle up | 2 | 0% | -1.04 | $-1,639.82 |
| deck Bull flag up | 4 | 25% | -0.26 | $-26.31 |
| deck Bull pennant up | 3 | 33% | -0.32 | $-608.11 |
| deck Bullish engulfing up | 2 | 50% | -0.02 | $-43.92 |
| deck Descending triangle up | 1 | 100% |  | $+301.44 |
| deck Falling wedge up | 1 | 0% | -1.01 | $-293.88 |
| deck Gap down down | 1 | 100% |  | $+1,135.72 |
| deck Gap up up | 7 | 29% | -0.68 | $-2,662.23 |
| deck Harami up | 5 | 40% | -0.45 | $-2,369.58 |
| deck Inverse head shoulders up | 1 | 0% | -1.03 | $-175.42 |
| deck Piercing line up | 1 | 0% | -0.27 | $-17.29 |
| deck Rectangle up | 1 | 100% | +1.65 | $+1,395.05 |
| deck Rising wedge up | 2 | 0% | -0.51 | $-22.78 |
| deck Symmetrical triangle down | 2 | 100% |  | $+1,899.28 |
| deck Symmetrical triangle up | 7 | 29% | +2.41 | $-1,045.44 |
| deck Triple bottom up | 5 | 40% | -0.29 | $-1,894.54 |

**Last closed trades**

| Symbol | Opened by | In | Out | Entry | Exit | P/L | R |
|---|---|---|---|---|---|---|---|
| MO | deck Gap up up | 2026-11-12 | 2026-11-19 | 78.34 | 74.60 | $-14.94 (-4.8%) | -1.06 |
| MCD | deck Gap up up | 2026-11-10 | 2026-11-19 | 282.55 | 268.90 | $-928.49 (-4.8%) | -1.02 |
| KO | deck Rising wedge up | 2026-11-16 | 2026-11-18 | 100.36 | 97.59 | $-11.08 (-2.8%) | -1.01 |
| LOW | deck Gap up up | 2026-11-10 | 2026-11-17 | 231.10 | 220.29 | $-907.60 (-4.7%) | -1.00 |
| TMO | deck Ascending triangle up | 2026-11-10 | 2026-11-17 | 716.03 | 686.37 | $-800.85 (-4.1%) | -1.05 |
| BMY | deck Ascending triangle up | 2026-11-05 | 2026-11-06 | 64.46 | 61.83 | $-838.97 (-4.1%) | -1.03 |
| MO | deck Gap up up | 2026-11-05 | 2026-11-06 | 78.09 | 75.11 | $-783.07 (-3.8%) | -1.26 |
| MDLZ | deck Gap up up | 2026-11-05 | 2026-11-06 | 67.96 | 64.90 | $-927.18 (-4.5%) | -1.04 |
| GM | deck Triple bottom up | 2026-11-05 | 2026-11-05 | 93.19 | 88.71 | $-985.60 (-4.8%) | -1.00 |
| MSFT | deck Symmetrical triangle up | 2026-11-05 | 2026-11-05 | 553.75 | 539.18 | $-538.94 (-2.6%) | -1.01 |
