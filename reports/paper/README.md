# Paper trading report

Updated 2026-10-28 20:59 New York time by `paper_report.py` (rebuilt from Alpaca every weekday after the close).
Files: `<account>_trades.csv` (closed trades), `<account>_open.csv` (open positions), `<account>_equity.csv` (account value per day vs SPY).

## Breakout bot (stocks + crypto)

| | |
|---|---|
| Account value | $100,836.27 (+0.0% today) |
| Since 12 Oct 2026 | +0.8% (SPY -0.7%) |
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
| Account value | $99,533.37 (+0.8% today) |
| Since 15 Oct 2026 | -0.5% (SPY +0.1%) |
| Worst drop from a high | -2.9% |
| Closed trades | 8, win 12%, avg -1.02R, profit factor 0.33, $-2,307.99 |
| Open positions | 16 ($+1,844.05 not yet realised, 197% invested) |

**Open positions**

| Symbol | Opened by | Since | Entry | Now | Stop | P/L | R |
|---|---|---|---|---|---|---|---|
| C | ABCD | 2026-10-16 | 124.16 | 125.43 | nan | $+150.00 (+1.0%) |  |
| CSCO | deck Piercing line up | 2026-10-27 | 111.68 | 111.00 | 103.58 | $-5.46 (-0.6%) | -0.08 |
| EMR | deck Bullish engulfing up | 2026-10-28 | 168.04 | 167.71 | 154.52 | $-38.76 (-0.2%) | -0.02 |
| FDX | deck Harami up | 2026-10-28 | 307.21 | 298.14 | 295.73 | $-226.75 (-3.0%) | -0.79 |
| GOOGL | deck Symmetrical triangle down | 2026-10-21 | 333.63 | 295.71 | nan | $+1,099.72 (+11.4%) |  |
| ISRG | deck Rising wedge up | 2026-10-23 | 445.54 | 445.99 | 424.87 | $+20.25 (+0.1%) | +0.02 |
| LLY | Triple bottom | 2026-10-16 | 1265.00 | 1309.00 | 1106.68 | $+264.00 (+3.5%) | +0.28 |
| MDT | deck Bull flag up | 2026-10-23 | 96.15 | 93.57 | 92.63 | $-541.54 (-2.7%) | -0.73 |
| MMM | deck Descending triangle up | 2026-10-21 | 170.69 | 172.80 | nan | $+122.38 (+1.2%) |  |
| NEE | deck Symmetrical triangle up | 2026-10-23 | 82.25 | 81.55 | 78.32 | $-172.15 (-0.8%) | -0.18 |
| TSLA | deck Triple bottom up | 2026-10-27 | 397.80 | 401.51 | 381.04 | $+181.79 (+0.9%) | +0.22 |
| TXN | deck Symmetrical triangle up | 2026-10-27 | 301.88 | 306.50 | 282.49 | $+254.00 (+1.5%) | +0.24 |
| UBER | deck Symmetrical triangle down | 2026-10-21 | 68.80 | 65.30 | nan | $+507.50 (+5.1%) |  |
| UNP | deck Harami up | 2026-10-28 | 299.40 | 297.80 | 278.59 | $-3.20 (-0.5%) | -0.08 |
| WFC | Falling wedge | 2026-10-19 | 82.93 | 84.80 | 77.09 | $+300.47 (+2.2%) | +0.32 |
| WMT | deck Bull flag up | 2026-10-23 | 121.94 | 120.76 | 117.35 | $-68.20 (-1.0%) | -0.26 |

**Closed trades by what opened them**

| Opened by | Trades | Win | Avg R | P/L |
|---|---|---|---|---|
| deck Bull flag up | 1 | 0% | -1.07 | $-297.99 |
| deck Bull pennant up | 1 | 0% | -1.01 | $-414.27 |
| deck Falling wedge up | 1 | 0% | -1.01 | $-293.88 |
| deck Gap down down | 1 | 100% |  | $+1,135.72 |
| deck Harami up | 1 | 0% | -1.01 | $-1,150.75 |
| deck Inverse head shoulders up | 1 | 0% | -1.03 | $-175.42 |
| deck Symmetrical triangle up | 2 | 0% | -1.00 | $-1,111.40 |

**Last closed trades**

| Symbol | Opened by | In | Out | Entry | Exit | P/L | R |
|---|---|---|---|---|---|---|---|
| ADBE | deck Harami up | 2026-10-23 | 2026-10-27 | 270.06 | 254.72 | $-1,150.75 (-5.7%) | -1.01 |
| COST | deck Bull pennant up | 2026-10-23 | 2026-10-27 | 1019.61 | 997.80 | $-414.27 (-2.1%) | -1.01 |
| AMT | deck Bull flag up | 2026-10-26 | 2026-10-27 | 189.93 | 183.85 | $-297.99 (-3.2%) | -1.07 |
| INTU | deck Symmetrical triangle up | 2026-10-23 | 2026-10-26 | 353.83 | 334.65 | $-1,093.24 (-5.4%) | -1.01 |
| MS | deck Falling wedge up | 2026-10-22 | 2026-10-23 | 185.09 | 179.75 | $-293.88 (-2.9%) | -1.01 |
| COF | deck Inverse head shoulders up | 2026-10-22 | 2026-10-22 | 207.71 | 204.13 | $-175.42 (-1.7%) | -1.03 |
| LRCX | deck Gap down down | 2026-10-21 | 2026-10-22 | 266.29 | 235.60 | $+1,135.72 (+11.5%) |  |
| CMCSA | deck Symmetrical triangle up | 2026-10-21 | 2026-10-21 | 21.96 | 21.92 | $-18.16 (-0.2%) | -1.00 |
